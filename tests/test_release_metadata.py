import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".md", ".py", ".toml", ".yml", ".yaml", ".txt"}


class ReleaseMetadataTests(unittest.TestCase):
    def test_required_community_files_exist(self):
        for name in (
            "README.md",
            "LICENSE",
            "NOTICE",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "LAUNCH_PLAN.md",
            "MANIFEST.in",
            "pyproject.toml",
            ".gitignore",
        ):
            with self.subTest(name=name):
                path = ROOT / name
                self.assertTrue(path.is_file(), name)
                self.assertTrue(path.read_text(encoding="utf-8").strip(), name)

    def test_public_text_has_no_private_absolute_paths(self):
        private_vault = "LL" + "Liu"
        forbidden = re.compile(
            r"(?:[CDG]:\\(?:Users|PycharmProjects|Obsidian|资料|政策)\\|"
            + re.escape(private_vault)
            + r")",
            re.IGNORECASE,
        )
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
                continue
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")))

    def test_runtime_and_backup_files_are_ignored(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in (".venv/", "__pycache__/", "data/", "logs/", "*.xlsx", "*.bak", "*.lock"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, text)

    def test_cli_help_has_no_mojibake(self):
        from policy_harvester.cli import build_parser

        help_text = build_parser().format_help()
        for marker in ("æ—¥æœŸ", "å¢žé‡", "æ¥æº", "�"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, help_text)

    def test_web_assets_are_declared_in_package_metadata(self):
        import tomllib

        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["license-files"], ["LICENSE", "NOTICE"])
        self.assertEqual(
            metadata["project"]["urls"]["Repository"],
            "https://github.com/Lcub3d/ministry-policy-downloader",
        )
        patterns = metadata["tool"]["setuptools"]["package-data"]["policy_harvester"]
        self.assertEqual(patterns, ["web/*.html", "web/*.css", "web/*.js"])

    def test_readme_states_coverage_and_transport_boundaries(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for statement in (
            "已配置栏目完整",
            "官网所有搜索入口",
            "直链文件",
            "原始附件归档",
            "http://",
            "强制改写为 HTTPS",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, text)

    def test_launch_plan_rejects_inauthentic_growth(self):
        text = (ROOT / "LAUNCH_PLAN.md").read_text(encoding="utf-8")
        for statement in ("200 GitHub Stars", "不是结果承诺", "不买星", "不刷星", "不使用机器人", "不参与互星群"):
            with self.subTest(statement=statement):
                self.assertIn(statement, text)

    def test_sdist_manifest_declares_public_documents(self):
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
        declared = {line.removeprefix("include ") for line in manifest if line.startswith("include ")}
        required = {
            "README.md",
            "LICENSE",
            "NOTICE",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "LAUNCH_PLAN.md",
        }
        self.assertTrue(required <= declared, required - declared)


if __name__ == "__main__":
    unittest.main()
