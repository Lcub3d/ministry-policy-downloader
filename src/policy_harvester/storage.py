from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit

from .models import Attachment, Policy


POLICIES_TABLE = """
CREATE TABLE {table_name} (
    id TEXT PRIMARY KEY,
    site TEXT NOT NULL,
    section TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    date TEXT NOT NULL DEFAULT '',
    doc_no TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'page' CHECK (kind IN ('page', 'file')),
    content_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (content_status IN ('pending', 'complete', 'failed', 'not_applicable')),
    html_path TEXT,
    markdown_path TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

SCHEMA = f"""
{POLICIES_TABLE.format(table_name="IF NOT EXISTS policies")};
CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'complete', 'failed')),
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (policy_id, url),
    UNIQUE (policy_id, path)
);
CREATE INDEX IF NOT EXISTS idx_policies_site_status
    ON policies(site, content_status);
CREATE INDEX IF NOT EXISTS idx_attachments_policy_status
    ON attachments(policy_id, status);
PRAGMA user_version = 2;
"""

_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class ArchiveLockedError(RuntimeError):
    pass


class ArchiveLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: io.TextIOWrapper | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                if not self.handle.read(1):
                    self.handle.write("0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.handle.seek(0)
            self.handle.truncate()
            self.handle.write(f"pid={os.getpid()} started={time.time():.0f}\n")
            self.handle.flush()
        except (OSError, BlockingIOError) as exc:
            self.handle.close()
            self.handle = None
            raise ArchiveLockedError(
                f"archive is already open by another process: {self.path.parent}"
            ) from exc

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"expected an absolute HTTP(S) URL: {url!r}")
    netloc = parts.netloc.lower()
    if (scheme == "http" and netloc.endswith(":80")) or (
        scheme == "https" and netloc.endswith(":443")
    ):
        netloc = netloc.rsplit(":", 1)[0]
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def policy_id(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()


def safe_filename(name: str, url: str = "") -> str:
    value = html.unescape(unquote(name)).strip()
    value = re.sub(r"^附件\s*[:：]\s*", "", value)
    if not value and url:
        value = unquote(PurePosixPath(urlsplit(url).path).name)
    value = _INVALID_FILENAME.sub("_", value)
    value = re.sub(r"\s+", " ", value).strip(" .") or "attachment"
    stem, suffix = os.path.splitext(value)
    if not suffix and url:
        url_suffix = PurePosixPath(unquote(urlsplit(url).path)).suffix
        if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", url_suffix):
            suffix = url_suffix
    if stem.upper() in _RESERVED_NAMES:
        stem = f"_{stem}"
    if len(value) > 180:
        stem = stem[: max(1, 180 - len(suffix))].rstrip(" .")
    return stem + suffix


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


class Storage:
    """SQLite state plus atomically written archive files under one directory."""

    def __init__(self, output_dir: str | os.PathLike[str]) -> None:
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.lock = ArchiveLock(self.output_dir / ".policy-harvester.lock")
        self.lock.acquire()
        self.database_path = self.output_dir / "policy_archive.sqlite3"
        self._closed = False
        try:
            self.connection = sqlite3.connect(self.database_path)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self._migrate_legacy_database()
            self.connection.executescript(SCHEMA)
        except Exception:
            if hasattr(self, "connection"):
                self.connection.close()
            self._closed = True
            self.lock.release()
            raise

    def _migrate_legacy_database(self) -> None:
        table = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'policies'"
        ).fetchone()
        if table is None:
            return
        columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(policies)")
        }
        definition = str(table["sql"] or "").casefold()
        if "kind" in columns and "not_applicable" in definition:
            return

        backup = self.output_dir / "policy_archive.pre-v2.sqlite3"
        counter = 1
        while backup.exists():
            backup = self.output_dir / f"policy_archive.pre-v2-{counter}.sqlite3"
            counter += 1
        destination = sqlite3.connect(backup)
        try:
            self.connection.backup(destination)
        finally:
            destination.close()

        kind = (
            "CASE WHEN kind IN ('page', 'file') THEN kind ELSE 'page' END"
            if "kind" in columns
            else "'page'"
        )
        status = (
            f"CASE WHEN {kind} = 'file' THEN 'not_applicable' "
            "WHEN content_status IN ('pending', 'complete', 'failed') "
            "THEN content_status ELSE 'pending' END"
        )
        self.connection.execute("PRAGMA foreign_keys = OFF")
        try:
            with self.connection:
                self.connection.execute(POLICIES_TABLE.format(table_name="policies_v2"))
                self.connection.execute(
                    f"""
                    INSERT INTO policies_v2 (
                        id, site, section, title, url, date, doc_no, kind,
                        content_status, html_path, markdown_path, last_error,
                        created_at, updated_at
                    )
                    SELECT
                        id, site, section, title, url, date, doc_no, {kind},
                        {status}, html_path, markdown_path, last_error,
                        created_at, updated_at
                    FROM policies
                    """
                )
                self.connection.execute("DROP TABLE policies")
                self.connection.execute("ALTER TABLE policies_v2 RENAME TO policies")
        finally:
            self.connection.execute("PRAGMA foreign_keys = ON")
        violations = self.connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError("legacy database migration broke foreign keys")

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.connection.close()
        finally:
            self._closed = True
            self.lock.release()

    def upsert_policy(self, policy: Policy) -> str:
        if not policy.site.strip() or not policy.title.strip():
            raise ValueError("policy site and title must not be blank")
        if policy.kind not in {"page", "file"}:
            raise ValueError("policy kind must be page or file")
        url = canonical_url(policy.url)
        identifier = policy_id(url)
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO policies (id, site, section, title, url, date, doc_no, kind, content_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    site = excluded.site,
                    section = excluded.section,
                    title = excluded.title,
                    date = excluded.date,
                    doc_no = excluded.doc_no,
                    kind = excluded.kind,
                    content_status = CASE
                        WHEN excluded.kind = 'file' THEN 'not_applicable'
                        WHEN policies.kind = 'file' THEN 'pending'
                        ELSE policies.content_status
                    END,
                    html_path = CASE
                        WHEN excluded.kind = 'file' THEN NULL ELSE policies.html_path
                    END,
                    markdown_path = CASE
                        WHEN excluded.kind = 'file' THEN NULL ELSE policies.markdown_path
                    END,
                    last_error = CASE
                        WHEN excluded.kind != policies.kind THEN NULL ELSE policies.last_error
                    END,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    identifier,
                    policy.site.strip(),
                    policy.section.strip(),
                    policy.title.strip(),
                    url,
                    policy.date.strip(),
                    policy.doc_no.strip(),
                    policy.kind,
                    "not_applicable" if policy.kind == "file" else "pending",
                ),
            )
        return identifier

    def get_policy(self, identifier: str) -> dict[str, object] | None:
        row = self.connection.execute("SELECT * FROM policies WHERE id = ?", (identifier,)).fetchone()
        return dict(row) if row else None

    def get_policy_by_url(self, url: str) -> dict[str, object] | None:
        return self.get_policy(policy_id(url))

    def list_policies(self, site: str | None = None) -> list[dict[str, object]]:
        if site is None:
            rows = self.connection.execute("SELECT * FROM policies ORDER BY created_at, id").fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM policies WHERE site = ? ORDER BY created_at, id", (site,)
            ).fetchall()
        return [dict(row) for row in rows]

    def save_document(self, identifier: str, raw_html: str, markdown: str) -> tuple[str, str]:
        row = self.connection.execute("SELECT site FROM policies WHERE id = ?", (identifier,)).fetchone()
        if row is None:
            raise KeyError(f"unknown policy: {identifier}")
        folder = Path("archive") / safe_filename(str(row["site"])) / identifier
        html_path = folder / "page.html"
        markdown_path = folder / "content.md"
        _atomic_write(self.output_dir / html_path, raw_html.encode("utf-8"))
        _atomic_write(self.output_dir / markdown_path, markdown.encode("utf-8"))
        with self.connection:
            self.connection.execute(
                """
                UPDATE policies
                SET content_status = 'complete', html_path = ?, markdown_path = ?,
                    last_error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (html_path.as_posix(), markdown_path.as_posix(), identifier),
            )
        return html_path.as_posix(), markdown_path.as_posix()

    def mark_policy_failed(self, identifier: str, error: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE policies SET content_status = 'failed', last_error = ?,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (error, identifier),
            )

    def register_attachment(self, identifier: str, attachment: Attachment) -> str:
        url = canonical_url(attachment.url)
        attachment_id = hashlib.sha256(f"{identifier}\n{url}".encode("utf-8")).hexdigest()
        existing = self.connection.execute(
            "SELECT id FROM attachments WHERE policy_id = ? AND url = ?", (identifier, url)
        ).fetchone()
        if existing:
            return str(existing["id"])

        policy = self.connection.execute(
            "SELECT site FROM policies WHERE id = ?", (identifier,)
        ).fetchone()
        if policy is None:
            raise KeyError(f"unknown policy: {identifier}")
        filename = safe_filename(attachment.name, url)
        claimed = {
            str(row["name"]).casefold()
            for row in self.connection.execute(
                "SELECT name FROM attachments WHERE policy_id = ?", (identifier,)
            )
        }
        if filename.casefold() in claimed:
            stem, suffix = os.path.splitext(filename)
            short_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
            filename = f"{stem}_{short_hash}{suffix}"
        relative_path = (
            Path("archive")
            / safe_filename(str(policy["site"]))
            / identifier
            / "attachments"
            / filename
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO attachments (id, policy_id, name, url, path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (attachment_id, identifier, filename, url, relative_path.as_posix()),
            )
        return attachment_id

    def get_attachment(self, attachment_id: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_attachments(self, identifier: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            "SELECT * FROM attachments WHERE policy_id = ? ORDER BY created_at, id", (identifier,)
        ).fetchall()
        return [dict(row) for row in rows]

    def save_attachment(self, attachment_id: str, data: bytes) -> str:
        row = self.connection.execute(
            "SELECT path FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown attachment: {attachment_id}")
        relative_path = str(row["path"])
        _atomic_write(self.output_dir / Path(relative_path), data)
        with self.connection:
            self.connection.execute(
                """
                UPDATE attachments SET status = 'complete', last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (attachment_id,),
            )
        return relative_path

    def mark_attachment_failed(self, attachment_id: str, error: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE attachments SET status = 'failed', last_error = ?,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (error, attachment_id),
            )

    def pending_attachments(self, identifier: str) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT * FROM attachments
            WHERE policy_id = ? AND status != 'complete'
            ORDER BY created_at, id
            """,
            (identifier,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _manifest_rows(self) -> Iterator[dict[str, object]]:
        for policy in self.list_policies():
            attachments = self.list_attachments(str(policy["id"]))
            yield {
                "id": policy["id"],
                "site": policy["site"],
                "section": policy["section"],
                "title": policy["title"],
                "url": policy["url"],
                "date": policy["date"],
                "doc_no": policy["doc_no"],
                "kind": policy["kind"],
                "status": policy["content_status"],
                "html_path": policy["html_path"] or "",
                "markdown_path": policy["markdown_path"] or "",
                "error": policy["last_error"] or "",
                "attachments": [
                    {
                        "name": item["name"],
                        "url": item["url"],
                        "path": item["path"],
                        "status": item["status"],
                        "error": item["last_error"] or "",
                    }
                    for item in attachments
                ],
            }

    def export_csv(self, path: str | os.PathLike[str] | None = None) -> Path:
        destination = Path(path) if path else self.output_dir / "manifest.csv"
        fields = [
            "id",
            "site",
            "section",
            "title",
            "url",
            "date",
            "doc_no",
            "kind",
            "status",
            "html_path",
            "markdown_path",
            "error",
            "attachments",
        ]
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in self._manifest_rows():
            row["attachments"] = json.dumps(row["attachments"], ensure_ascii=False)
            writer.writerow(row)
        _atomic_write(destination, stream.getvalue().encode("utf-8-sig"))
        return destination

    def export_jsonl(self, path: str | os.PathLike[str] | None = None) -> Path:
        destination = Path(path) if path else self.output_dir / "manifest.jsonl"
        content = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in self._manifest_rows()
        )
        _atomic_write(destination, content.encode("utf-8"))
        return destination

    def audit(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for table, prefix, column, statuses in (
            (
                "policies",
                "policies",
                "content_status",
                ("pending", "complete", "failed", "not_applicable"),
            ),
            ("attachments", "attachments", "status", ("pending", "complete", "failed")),
        ):
            rows = self.connection.execute(
                f"SELECT {column} AS status, COUNT(*) AS count FROM {table} GROUP BY {column}"
            ).fetchall()
            counts = {str(row["status"]): int(row["count"]) for row in rows}
            result[f"{prefix}_total"] = sum(counts.values())
            for status in statuses:
                result[f"{prefix}_{status}"] = counts.get(status, 0)

        result["missing_content_files"] = sum(
            1
            for row in self.connection.execute(
                "SELECT html_path, markdown_path FROM policies WHERE content_status = 'complete'"
            )
            if not row["html_path"]
            or not row["markdown_path"]
            or not (self.output_dir / str(row["html_path"])).is_file()
            or not (self.output_dir / str(row["markdown_path"])).is_file()
        )
        result["missing_attachment_files"] = sum(
            1
            for row in self.connection.execute(
                "SELECT path FROM attachments WHERE status = 'complete'"
            )
            if not (self.output_dir / str(row["path"])).is_file()
        )
        return result
