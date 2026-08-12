from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol
from urllib.request import Request, urlopen

from .extract import extract_document
from .models import Attachment, DocumentContent, Policy
from .storage import Storage, canonical_url


class SiteAdapter(Protocol):
    site: str

    def iter_policies(
        self, fetch_text: Callable[[str | Request], str], since: str | None = None
    ) -> Iterable[Policy]: ...

    def parse_document(self, url: str, html: str) -> DocumentContent: ...


@dataclass(frozen=True, slots=True)
class RunStats:
    indexed: int = 0
    documents_downloaded: int = 0
    attachments_downloaded: int = 0
    documents_skipped: int = 0
    attachments_skipped: int = 0
    dry_run: bool = False


class PipelineError(RuntimeError):
    def __init__(self, stage: str, url: str, cause: BaseException) -> None:
        super().__init__(f"{stage} failed for {url}: {cause}")
        self.stage = stage
        self.url = url


class DownloadValidationError(ValueError):
    pass


def _decode_html(data: bytes, content_type: str = "") -> str:
    match = re.search(r"charset\s*=\s*['\"]?([\w-]+)", content_type, re.IGNORECASE)
    if not match:
        meta = re.search(br"charset\s*=\s*['\"]?([\w-]+)", data[:4096], re.IGNORECASE)
        match = re.match(r"(.+)", meta.group(1).decode("ascii", "ignore")) if meta else None
    candidates = [match.group(1) if match else "utf-8", "utf-8", "gb18030"]
    for encoding in dict.fromkeys(candidates):
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            pass
    return data.decode("utf-8", errors="replace")


class Pipeline:
    """Run one adapter sequentially and stop on the first durable failure."""

    def __init__(
        self,
        storage: Storage | None,
        adapter: SiteAdapter,
        *,
        fetch_text: Callable[[str | Request], str] | None = None,
        fetch_bytes: Callable[[str], bytes] | None = None,
        delay: float = 1.0,
        timeout: float = 30.0,
    ) -> None:
        if delay < 0 or timeout <= 0:
            raise ValueError("delay must be non-negative and timeout must be positive")
        self.storage = storage
        self.adapter = adapter
        self.delay = delay
        self.timeout = timeout
        self._last_request = 0.0
        self._fetch_text = fetch_text or self._default_fetch_text
        self._fetch_bytes = fetch_bytes or self._default_fetch_bytes

    def _wait(self) -> None:
        remaining = self.delay - (time.monotonic() - self._last_request)
        if remaining > 0:
            time.sleep(remaining)
        self._last_request = time.monotonic()

    def _text(self, url: str | Request) -> str:
        self._wait()
        return self._fetch_text(url)

    def _bytes(self, url: str) -> bytes:
        self._wait()
        return self._fetch_bytes(url)

    def _default_fetch_text(self, url: str | Request) -> str:
        request = url if isinstance(url, Request) else Request(url)
        if not request.has_header("User-agent"):
            request.add_header("User-Agent", "PolicyHarvester/0.1 (+local archive)")
        with urlopen(request, timeout=self.timeout) as response:
            data = response.read()
            text = _decode_html(data, response.headers.get("Content-Type", ""))
        compact = re.sub(r"\s+", " ", text).strip().casefold()
        if len(compact) < 80:
            raise DownloadValidationError("HTML response is unexpectedly short")
        if any(
            marker in compact
            for marker in (
                "请输入验证码",
                "访问过于频繁",
                "安全验证",
                "access denied",
                "captcha",
            )
        ):
            raise DownloadValidationError("HTML response is a challenge or access-denied page")
        return text

    def _default_fetch_bytes(self, url: str) -> bytes:
        request = Request(url, headers={"User-Agent": "PolicyHarvester/0.1 (+local archive)"})
        with urlopen(request, timeout=self.timeout) as response:
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            data = response.read()
        if not data:
            raise DownloadValidationError("empty attachment response")
        prefix = data[:512].lstrip().lower()
        if content_type in {"text/html", "application/xhtml+xml"} or prefix.startswith(
            (b"<!doctype html", b"<html")
        ):
            raise DownloadValidationError("attachment URL returned HTML instead of a file")
        return data

    def preview(self, since: str | None = None) -> list[Policy]:
        policies = list(self.adapter.iter_policies(self._text, since=since))
        self._validate_policies(policies)
        return policies

    def update(self, since: str | None = None, *, dry_run: bool = False) -> RunStats:
        # Materialize before writes: a partial/failed index cannot become a successful batch.
        try:
            policies = self.preview(since)
        except Exception as exc:
            raise PipelineError("index", self.adapter.site, exc) from exc
        if dry_run:
            return RunStats(indexed=len(policies), dry_run=True)
        if self.storage is None:
            raise RuntimeError("storage is required for archive updates")

        identifiers = [self.storage.upsert_policy(policy) for policy in policies]
        documents_downloaded = attachments_downloaded = documents_skipped = 0
        attachments_skipped = 0
        for policy, identifier in zip(policies, identifiers):
            stored = self.storage.get_policy(identifier)
            if stored is None:
                raise RuntimeError(f"policy disappeared from storage: {identifier}")
            if policy.kind == "file":
                self.storage.register_attachment(
                    identifier,
                    Attachment(policy.title, policy.url),
                )
                documents_skipped += 1
            elif stored["content_status"] != "complete":
                try:
                    raw_html = self._text(policy.url)
                    parser = getattr(self.adapter, "parse_document", extract_document)
                    document = parser(policy.url, raw_html)
                    if len(re.sub(r"\s+", "", document.markdown)) < 8:
                        raise DownloadValidationError("document body is empty or too short")
                    for attachment in document.attachments:
                        self.storage.register_attachment(identifier, attachment)
                    self.storage.save_document(identifier, raw_html, document.markdown)
                    documents_downloaded += 1
                except Exception as exc:
                    self.storage.mark_policy_failed(identifier, str(exc))
                    raise PipelineError("document", policy.url, exc) from exc
            else:
                documents_skipped += 1

            attachments = self.storage.list_attachments(identifier)
            pending_ids = {
                str(item["id"]) for item in attachments if item["status"] != "complete"
            }
            attachments_skipped += len(attachments) - len(pending_ids)
            for attachment in attachments:
                if str(attachment["id"]) not in pending_ids:
                    continue
                attachment_id = str(attachment["id"])
                attachment_url = str(attachment["url"])
                try:
                    self.storage.save_attachment(attachment_id, self._bytes(attachment_url))
                    attachments_downloaded += 1
                except Exception as exc:
                    self.storage.mark_attachment_failed(attachment_id, str(exc))
                    raise PipelineError("attachment", attachment_url, exc) from exc

        # Manifests are replaced only after the complete batch succeeds.
        self.storage.export_csv()
        self.storage.export_jsonl()
        return RunStats(
            indexed=len(policies),
            documents_downloaded=documents_downloaded,
            attachments_downloaded=attachments_downloaded,
            documents_skipped=documents_skipped,
            attachments_skipped=attachments_skipped,
        )

    def audit(self) -> dict[str, int]:
        if self.storage is None:
            raise RuntimeError("storage is required for archive audits")
        return self.storage.audit()

    def _validate_policies(self, policies: list[Policy]) -> None:
        seen: set[str] = set()
        for policy in policies:
            if policy.site != self.adapter.site:
                raise ValueError(
                    f"adapter {self.adapter.site!r} returned policy for site {policy.site!r}"
                )
            if not policy.title.strip():
                raise ValueError(f"blank policy title: {policy.url}")
            url = canonical_url(policy.url)
            if policy.kind not in {"page", "file"}:
                raise ValueError(f"unsupported policy kind: {policy.kind}")
            if url in seen:
                raise ValueError(f"duplicate policy URL in index: {policy.url}")
            seen.add(url)

