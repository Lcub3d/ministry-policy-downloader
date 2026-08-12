import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from policy_harvester.cli import build_parser, main
from policy_harvester.models import Policy
from policy_harvester.webapp import JobManager, WEB_ROOT, _is_local_request


class CliTests(unittest.TestCase):
    def test_help_contract_and_date_validation(self):
        help_text = build_parser().format_help()
        for command in ("update", "preview", "audit", "serve"):
            self.assertIn(command, help_text)
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                ["update", "--source", "ndrc", "--since", "2026-02-30"]
            )

    def test_audit_exit_codes(self):
        with tempfile.TemporaryDirectory() as temporary:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["audit", "--output", temporary]), 0)
            database = Path(temporary) / "policy_archive.sqlite3"
            self.assertTrue(database.is_file())

    def test_serve_rejects_non_loopback(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                main(["serve", "--host", "0.0.0.0", "--no-browser"]),
                1,
            )

    def test_preview_and_update_dry_run_do_not_create_output_directory(self):
        class Adapter:
            site = "ndrc"

            def iter_policies(self, _fetch_text, since=None):
                yield Policy("ndrc", "政策", "测试政策", "https://example.gov.cn/policy", since or "")

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "must-not-exist"
            with patch("policy_harvester.cli.get_adapter", return_value=Adapter()):
                for command in ("preview", "update"):
                    argv = [command, "--source", "ndrc", "--output", str(output)]
                    if command == "update":
                        argv.append("--dry-run")
                    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(main(argv), 0)
                    self.assertFalse(output.exists())


class WebAppTests(unittest.TestCase):
    def test_request_origin_must_match_loopback_host_and_server_port(self):
        self.assertTrue(_is_local_request("127.0.0.1:8765", None, 8765))
        self.assertTrue(
            _is_local_request("[::1]:8765", "http://[::1]:8765", 8765)
        )
        self.assertFalse(
            _is_local_request("attacker.example:8765", "http://attacker.example:8765", 8765)
        )
        self.assertFalse(
            _is_local_request("localhost:8765", "http://127.0.0.1:8765", 8765)
        )
        self.assertFalse(
            _is_local_request("localhost:9999", "http://localhost:9999", 8765)
        )

    def test_static_assets_exist_and_use_relative_links(self):
        html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="app.css"', html)
        self.assertIn('src="app.js"', html)
        self.assertTrue((WEB_ROOT / "app.css").is_file())
        self.assertTrue((WEB_ROOT / "app.js").is_file())

    def test_manager_validates_input_before_starting_thread(self):
        manager = JobManager()
        with self.assertRaisesRegex(ValueError, "至少选择"):
            manager.create({"sources": [], "since": "2026-08-01", "output": "data"})
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            manager.create({"sources": ["ndrc"], "since": "bad", "output": "data"})
        with self.assertRaisesRegex(ValueError, "不支持"):
            manager.create({"sources": ["other"], "since": "2026-08-01", "output": "data"})

    def test_scan_job_reports_results_without_durable_writes(self):
        manager = JobManager()
        fake_policy = type("P", (), {"url": "https://example.test/item"})()

        class FakePipeline:
            def __init__(self, *_args, **_kwargs):
                pass

            def preview(self, *_args):
                return [fake_policy]

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "must-not-exist"
            with patch("policy_harvester.webapp.threading.Thread") as thread:
                job = manager.create(
                    {
                        "sources": ["ndrc"],
                        "since": "2026-08-01",
                        "output": str(output),
                        "mode": "scan",
                        "delay": 1,
                    }
                )
                thread.return_value.start.assert_called_once()
            with patch("policy_harvester.webapp.Pipeline.preview", FakePipeline.preview):
                manager._run(job)
            snapshot = job.snapshot()
            self.assertEqual(snapshot["status"], "success")
            self.assertEqual(snapshot["results"]["ndrc"]["discovered"], 1)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

