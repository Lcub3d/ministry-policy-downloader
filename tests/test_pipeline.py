import tempfile
import unittest
from email.message import Message
from unittest.mock import patch
from pathlib import Path
from urllib.request import Request

from policy_harvester.models import Attachment, DocumentContent, Policy
from policy_harvester.pipeline import DownloadValidationError, Pipeline, PipelineError
from policy_harvester.storage import Storage


class FakeAdapter:
    site = "ndrc"

    def __init__(self, fail_index=False):
        self.fail_index = fail_index

    def iter_policies(self, fetch_text, since=None):
        fetch_text("https://example.gov.cn/index")
        yield Policy("ndrc", "政策", "测试政策", "https://example.gov.cn/policy", since or "")
        if self.fail_index:
            raise RuntimeError("second page failed")

    def parse_document(self, url, html):
        return DocumentContent(
            "测试政策",
            "# 测试政策\n\n这是用于验证归档流程的完整正文。\n",
            (Attachment("附件：清单.xlsx", "https://example.gov.cn/list.xlsx"),),
        )


class DuplicateAdapter(FakeAdapter):
    def iter_policies(self, fetch_text, since=None):
        yield Policy("ndrc", "政策", "政策甲", "https://EXAMPLE.gov.cn/a#top")
        yield Policy("ndrc", "政策", "政策乙", "https://example.gov.cn/a")


class InvalidAttachmentAdapter(FakeAdapter):
    def parse_document(self, url, html):
        return DocumentContent(
            "测试政策",
            "正文\n",
            (Attachment("清单.xlsx", "not-an-absolute-url"),),
        )


class DirectFileAdapter(FakeAdapter):
    site = "mof"

    def iter_policies(self, fetch_text, since=None):
        yield Policy(
            "mof",
            "财政法规目录",
            "彩票机构会计制度",
            "https://example.gov.cn/files/accounting.pdf",
            kind="file",
        )

    def parse_document(self, url, html):
        raise AssertionError("direct files must not be parsed as HTML")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.storage = Storage(self.root)
        self.text_calls = []
        self.byte_calls = []

    def tearDown(self):
        self.storage.close()
        self.temporary.cleanup()

    def fetch_text(self, url):
        self.text_calls.append(url)
        return "<html><article>正文</article></html>"

    def fetch_bytes(self, url):
        self.byte_calls.append(url)
        return b"attachment"

    def pipeline(self, adapter=None):
        return Pipeline(
            self.storage,
            adapter or FakeAdapter(),
            fetch_text=self.fetch_text,
            fetch_bytes=self.fetch_bytes,
            delay=0,
        )

    def test_dry_run_has_no_durable_writes(self):
        stats = self.pipeline().update("2026-08-01", dry_run=True)
        self.assertEqual(stats.indexed, 1)
        self.assertTrue(stats.dry_run)
        self.assertEqual(self.storage.list_policies(), [])
        self.assertFalse((self.root / "manifest.csv").exists())
        self.assertEqual(self.text_calls, ["https://example.gov.cn/index"])

    def test_update_is_sequential_and_repeatable(self):
        pipeline = self.pipeline()
        first = pipeline.update("2026-08-01")
        second = pipeline.update("2026-08-01")

        self.assertEqual(first.documents_downloaded, 1)
        self.assertEqual(first.attachments_downloaded, 1)
        self.assertEqual(second.documents_downloaded, 0)
        self.assertEqual(second.attachments_downloaded, 0)
        self.assertEqual(second.documents_skipped, 1)
        self.assertEqual(second.attachments_skipped, 1)
        self.assertEqual(self.byte_calls, ["https://example.gov.cn/list.xlsx"])
        self.assertEqual(
            self.text_calls,
            [
                "https://example.gov.cn/index",
                "https://example.gov.cn/policy",
                "https://example.gov.cn/index",
            ],
        )
        self.assertTrue((self.root / "manifest.csv").is_file())
        self.assertTrue((self.root / "manifest.jsonl").is_file())

    def test_partial_index_failure_writes_nothing(self):
        with self.assertRaisesRegex(PipelineError, "index failed"):
            self.pipeline(FakeAdapter(fail_index=True)).update()
        self.assertEqual(self.storage.list_policies(), [])
        self.assertFalse((self.root / "manifest.csv").exists())

    def test_canonical_duplicate_index_writes_nothing(self):
        with self.assertRaisesRegex(PipelineError, "duplicate policy URL"):
            self.pipeline(DuplicateAdapter()).update()
        self.assertEqual(self.storage.list_policies(), [])

    def test_document_is_not_completed_before_attachments_are_registered(self):
        with self.assertRaisesRegex(PipelineError, "document failed"):
            self.pipeline(InvalidAttachmentAdapter()).update()
        policy = self.storage.list_policies()[0]
        self.assertEqual(policy["content_status"], "failed")
        self.assertIsNone(policy["html_path"])
        self.assertFalse((self.root / "manifest.csv").exists())

    def test_attachment_failure_is_recorded_and_stops_export(self):
        def fail(_url):
            raise OSError("offline")

        pipeline = Pipeline(
            self.storage,
            FakeAdapter(),
            fetch_text=self.fetch_text,
            fetch_bytes=fail,
            delay=0,
        )
        with self.assertRaisesRegex(PipelineError, "attachment failed"):
            pipeline.update()

        policy = self.storage.list_policies()[0]
        attachment = self.storage.list_attachments(str(policy["id"]))[0]
        self.assertEqual(policy["content_status"], "complete")
        self.assertEqual(attachment["status"], "failed")
        self.assertIn("offline", str(attachment["last_error"]))
        self.assertFalse((self.root / "manifest.csv").exists())

    def test_default_attachment_fetch_rejects_html_error_page(self):
        class Response:
            headers = Message()

            def __enter__(self):
                self.headers["Content-Type"] = "text/html; charset=utf-8"
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return b"<!doctype html><title>blocked</title>"

        pipeline = self.pipeline()
        with patch("policy_harvester.pipeline.urlopen", return_value=Response()):
            with self.assertRaisesRegex(DownloadValidationError, "returned HTML"):
                pipeline._default_fetch_bytes("https://example.gov.cn/file.pdf")

    def test_default_text_fetch_preserves_post_request(self):
        class Response:
            headers = Message()

            def __enter__(self):
                self.headers["Content-Type"] = "text/html; charset=utf-8"
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return ("<html><body>" + "有效检索结果" * 20 + "</body></html>").encode()

        request = Request(
            "https://search.example.gov.cn/query",
            data=b"channelid=1",
            headers={"User-Agent": "custom-agent", "X-Test": "yes"},
        )
        pipeline = self.pipeline()
        with patch("policy_harvester.pipeline.urlopen", return_value=Response()) as opened:
            pipeline._default_fetch_text(request)

        sent = opened.call_args.args[0]
        self.assertIs(sent, request)
        self.assertEqual(sent.get_method(), "POST")
        self.assertEqual(sent.data, b"channelid=1")
        self.assertEqual(sent.get_header("User-agent"), "custom-agent")
        self.assertEqual(sent.get_header("X-test"), "yes")

    def test_direct_file_download_is_repeatable_without_html_request(self):
        pipeline = self.pipeline(DirectFileAdapter())

        first = pipeline.update()
        second = pipeline.update()

        policy = self.storage.list_policies()[0]
        self.assertEqual(policy["content_status"], "not_applicable")
        self.assertEqual(self.text_calls, [])
        self.assertEqual(self.byte_calls, ["https://example.gov.cn/files/accounting.pdf"])
        self.assertEqual(first.attachments_downloaded, 1)
        self.assertEqual(second.attachments_downloaded, 0)
        self.assertEqual(second.attachments_skipped, 1)
        attachment = self.storage.list_attachments(str(policy["id"]))[0]
        self.assertEqual(attachment["name"], "彩票机构会计制度.pdf")
        self.assertTrue(str(attachment["path"]).endswith("/彩票机构会计制度.pdf"))
        self.assertEqual(self.storage.audit()["missing_content_files"], 0)

    def test_too_short_document_is_not_marked_complete(self):
        class ShortDocumentAdapter(FakeAdapter):
            def parse_document(self, url, html):
                return DocumentContent("测试", "短")

        with self.assertRaisesRegex(PipelineError, "empty or too short"):
            self.pipeline(ShortDocumentAdapter()).update()
        self.assertEqual(self.storage.list_policies()[0]["content_status"], "failed")


if __name__ == "__main__":
    unittest.main()

