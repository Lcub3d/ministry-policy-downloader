import csv
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from policy_harvester.models import Attachment, Policy
from policy_harvester.storage import ArchiveLockedError, Storage, policy_id, safe_filename


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.storage = Storage(self.root)

    def tearDown(self):
        self.storage.close()
        self.temporary.cleanup()

    def test_url_identity_is_stable_and_upsert_is_idempotent(self):
        original = Policy("ndrc", "政策", "旧标题", "HTTPS://EXAMPLE.GOV.CN:443/a#top")
        changed = Policy("ndrc", "政策", "新标题", "https://example.gov.cn/a")

        first = self.storage.upsert_policy(original)
        second = self.storage.upsert_policy(changed)

        self.assertEqual(first, second)
        self.assertEqual(first, policy_id("https://example.gov.cn/a"))
        self.assertEqual(len(self.storage.list_policies()), 1)
        self.assertEqual(self.storage.get_policy(first)["title"], "新标题")

    def test_close_is_repeatable(self):
        self.storage.close()
        self.storage.close()

    def test_second_writer_is_rejected(self):
        with self.assertRaisesRegex(ArchiveLockedError, "already open"):
            Storage(self.root)

    def test_archives_files_and_hashes_only_real_name_collisions(self):
        identifier = self.storage.upsert_policy(
            Policy("mof", "制度", "彩票机构会计制度", "https://example.gov.cn/p/1")
        )
        first_url = "https://example.gov.cn/files/accounting.pdf"
        second_url = "https://cdn.example.gov.cn/files/accounting.pdf"
        first = self.storage.register_attachment(
            identifier, Attachment("附件：彩票机构会计制度.pdf", first_url)
        )
        repeated = self.storage.register_attachment(
            identifier, Attachment("附件：彩票机构会计制度.pdf", first_url)
        )
        second = self.storage.register_attachment(
            identifier, Attachment("附件：彩票机构会计制度.pdf", second_url)
        )

        self.assertEqual(first, repeated)
        first_row = self.storage.get_attachment(first)
        second_row = self.storage.get_attachment(second)
        self.assertEqual(first_row["name"], "彩票机构会计制度.pdf")
        expected_hash = hashlib.sha1(second_url.encode("utf-8")).hexdigest()[:8]
        self.assertEqual(second_row["name"], f"彩票机构会计制度_{expected_hash}.pdf")

        html_path, markdown_path = self.storage.save_document(identifier, "<p>正文</p>", "正文\n")
        attachment_path = self.storage.save_attachment(first, b"%PDF-local-test")
        self.assertEqual((self.root / html_path).read_text(encoding="utf-8"), "<p>正文</p>")
        self.assertEqual((self.root / markdown_path).read_text(encoding="utf-8"), "正文\n")
        self.assertEqual((self.root / attachment_path).read_bytes(), b"%PDF-local-test")
        hidden = [path.name for path in self.root.rglob("*") if path.name.startswith(".")]
        self.assertEqual(hidden, [".policy-harvester.lock"])

    def test_exports_csv_jsonl_and_audits_missing_files(self):
        identifier = self.storage.upsert_policy(
            Policy("mee", "公告", "排污许可公告", "https://example.gov.cn/p/2", "2026-08-01", "环办〔2026〕1号")
        )
        attachment_id = self.storage.register_attachment(
            identifier, Attachment("清单.xlsx", "https://example.gov.cn/a/list.xlsx")
        )
        self.storage.save_document(identifier, "<p>正文</p>", "正文\n")
        self.storage.save_attachment(attachment_id, b"xlsx-test")

        csv_path = self.storage.export_csv()
        jsonl_path = self.storage.export_jsonl()

        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            csv_row = next(csv.DictReader(handle))
        json_row = json.loads(jsonl_path.read_text(encoding="utf-8").strip())
        self.assertEqual(csv_row["title"], "排污许可公告")
        self.assertEqual(json.loads(csv_row["attachments"])[0]["status"], "complete")
        self.assertEqual(json_row["doc_no"], "环办〔2026〕1号")
        self.assertEqual(csv_row["kind"], "page")
        self.assertEqual(json_row["attachments"][0]["name"], "清单.xlsx")
        self.assertEqual(self.storage.audit()["missing_content_files"], 0)

        (self.root / str(self.storage.get_policy(identifier)["html_path"])).unlink()
        self.assertEqual(self.storage.audit()["missing_content_files"], 1)

    def test_direct_file_is_not_a_missing_document(self):
        identifier = self.storage.upsert_policy(
            Policy(
                "mof",
                "会计制度",
                "彩票机构会计制度.pdf",
                "https://example.gov.cn/files/lottery.pdf",
                kind="file",
            )
        )
        self.storage.register_attachment(
            identifier,
            Attachment("彩票机构会计制度.pdf", "https://example.gov.cn/files/lottery.pdf"),
        )
        report = self.storage.audit()
        self.assertEqual(report["policies_total"], 1)
        self.assertEqual(report["policies_not_applicable"], 1)
        self.assertEqual(report["policies_pending"], 0)
        self.assertEqual(report["missing_content_files"], 0)

    def test_rejects_unsafe_or_incomplete_identity_inputs(self):
        with self.assertRaises(ValueError):
            self.storage.upsert_policy(Policy("ndrc", "政策", "", "https://example.gov.cn/a"))
        with self.assertRaises(ValueError):
            policy_id("not-a-url")
        self.assertEqual(safe_filename("附件：CON.pdf"), "_CON.pdf")
        self.assertEqual(
            safe_filename("彩票机构会计制度", "https://example.gov.cn/files/lottery.pdf"),
            "彩票机构会计制度.pdf",
        )
        self.assertNotIn("/", safe_filename("../../evil.pdf"))


class LegacyMigrationTests(unittest.TestCase):
    def test_v1_database_is_backed_up_and_migrated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "policy_archive.sqlite3"
            identifier = policy_id("https://example.gov.cn/legacy")
            attachment_id = "a" * 64
            connection = sqlite3.connect(database)
            try:
                connection.executescript(
                    """
                    CREATE TABLE policies (
                        id TEXT PRIMARY KEY, site TEXT NOT NULL, section TEXT NOT NULL,
                        title TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
                        date TEXT NOT NULL DEFAULT '', doc_no TEXT NOT NULL DEFAULT '',
                        content_status TEXT NOT NULL DEFAULT 'pending'
                            CHECK (content_status IN ('pending', 'complete', 'failed')),
                        html_path TEXT, markdown_path TEXT, last_error TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE attachments (
                        id TEXT PRIMARY KEY,
                        policy_id TEXT NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
                        name TEXT NOT NULL, url TEXT NOT NULL, path TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'complete', 'failed')),
                        last_error TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (policy_id, url), UNIQUE (policy_id, path)
                    );
                    """
                )
                connection.execute(
                    "INSERT INTO policies (id, site, section, title, url) VALUES (?, ?, ?, ?, ?)",
                    (identifier, "ndrc", "政策", "旧记录", "https://example.gov.cn/legacy"),
                )
                connection.execute(
                    "INSERT INTO attachments (id, policy_id, name, url, path) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        attachment_id,
                        identifier,
                        "旧附件.pdf",
                        "https://example.gov.cn/legacy.pdf",
                        "archive/legacy.pdf",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            with Storage(root) as storage:
                self.assertEqual(storage.get_policy(identifier)["kind"], "page")
                self.assertEqual(storage.get_attachment(attachment_id)["policy_id"], identifier)
                self.assertEqual(storage.connection.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(storage.connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                direct = storage.upsert_policy(
                    Policy(
                        "mof",
                        "制度",
                        "直链文件.pdf",
                        "https://example.gov.cn/direct.pdf",
                        kind="file",
                    )
                )
                self.assertEqual(storage.get_policy(direct)["content_status"], "not_applicable")

            self.assertTrue((root / "policy_archive.pre-v2.sqlite3").is_file())


if __name__ == "__main__":
    unittest.main()
