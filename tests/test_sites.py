import unittest
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from policy_harvester.sites import ADAPTERS, get_adapter
from policy_harvester.sites._base import _page_count
from policy_harvester.sites.mof import SEARCH_PARAMS, SEARCH_URL


FIXTURES = Path(__file__).with_name("fixtures")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class SiteAdapterTests(unittest.TestCase):
    CASES = {
        "ndrc": ("tz", "ndrc_index.html", "ndrc_document.html", "通知"),
        "mee": ("bwj_ling", "mee_index.html", "mee_document.html", "部令"),
        "mnr": ("tzgg", "mnr_index.html", "mnr_document.html", "通知公告"),
        "mof": ("zcfb", "mof_index.html", "mof_document.html", "政策发布"),
    }

    def test_registry_contains_four_sources(self):
        self.assertEqual(set(ADAPTERS), {"ndrc", "mee", "mnr", "mof"})
        self.assertIs(get_adapter("NDRC"), ADAPTERS["ndrc"])
        with self.assertRaisesRegex(ValueError, "不支持的来源"):
            get_adapter("unknown")

    def test_each_site_parses_offline_index(self):
        for site, (section_key, index_file, _, section_name) in self.CASES.items():
            with self.subTest(site=site):
                adapter = ADAPTERS[site]
                section = next(item for item in adapter.sections if item.key == section_key)
                page_url = adapter.page_url(section.indexes[0], 1)
                items = adapter.parse_index(fixture(index_file), page_url, section)
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0].site, site)
                self.assertEqual(items[0].section, section_name)
                self.assertEqual(items[0].date, f"2026-08-{ {'ndrc': 12, 'mee': 11, 'mnr': 10, 'mof': 9}[site]:02d}")
                self.assertTrue(items[0].url.startswith(("http://", "https://")))

    def test_each_site_extracts_body_and_attachment(self):
        for site, (_, _, document_file, _) in self.CASES.items():
            with self.subTest(site=site):
                adapter = ADAPTERS[site]
                document_url = adapter.normalize_url("/sample/202608/t20260801_1.html")
                document = adapter.parse_document(document_url, fixture(document_file))
                self.assertTrue(document.title)
                self.assertGreater(len(document.markdown), 10)
                self.assertEqual(len(document.attachments), 1)
                self.assertFalse(document.attachments[0].name.startswith("附件"))
                self.assertTrue(document.attachments[0].url.startswith(("http://", "https://")))

    def test_pagination_and_since_are_offline_and_bounded(self):
        adapter = ADAPTERS["ndrc"]
        section = next(item for item in adapter.sections if item.key == "tz")
        first_url = adapter.page_url(section.indexes[0], 1)
        second_url = adapter.page_url(section.indexes[0], 2)
        second_html = fixture("ndrc_index.html").replace(
            "t20260812_1400010", "t20260701_1400009"
        ).replace("2026年8月12日", "2026年7月1日")
        responses = {first_url: fixture("ndrc_index.html"), second_url: second_html}

        one_section = adapter.__class__(
            site=adapter.site,
            base_url=adapter.base_url,
            allowed_hosts=adapter.allowed_hosts,
            article_pattern=adapter.article_pattern,
            sections=(section,),
            doc_no_patterns=adapter.doc_no_patterns,
            body_selectors=adapter.body_selectors,
            host_aliases=adapter.host_aliases,
            items_per_page=adapter.items_per_page,
        )
        policies = list(one_section.iter_policies(responses.__getitem__, since="2026-08-01"))
        self.assertEqual(len(policies), 1)
        self.assertEqual(policies[0].date, "2026-08-12")
        self.assertEqual(set(responses), {first_url, second_url})

    def test_finance_urls_preserve_the_official_scheme(self):
        adapter = ADAPTERS["mof"]
        self.assertEqual(
            adapter.normalize_url("http://www.mof.gov.cn/a.htm"),
            "http://www.mof.gov.cn/a.htm",
        )

    def test_mof_regulation_search_uses_post_then_paged_get(self):
        adapter = ADAPTERS["mof"]
        section = next(item for item in adapter.sections if item.key == "fgml")
        adapter = replace(adapter, sections=(section,))
        calls = []

        def fetch(request):
            calls.append(request)
            if isinstance(request, Request):
                self.assertEqual(request.full_url, SEARCH_URL)
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(
                    parse_qs(request.data.decode("ascii"))["channelid"],
                    [SEARCH_PARAMS["channelid"]],
                )
                return "<html><body>找到相关结果约 101 条</body></html>"
            page = int(parse_qs(urlsplit(request).query)["page"][0])
            return (
                "<html><body><a href='https://www.mof.gov.cn/fgk/"
                f"20260{page}/t20260{page}01_400000{page}.htm'>"
                f"财政法规目录测试文件第{page}页</a><time>2026-0{page}-01</time></body></html>"
            )

        policies = list(adapter.iter_policies(fetch))

        self.assertEqual(len(policies), 2)
        self.assertIsInstance(calls[0], Request)
        self.assertEqual(
            [int(parse_qs(urlsplit(url).query)["page"][0]) for url in calls[1:]],
            [1, 2],
        )
        self.assertTrue(all(policy.section == "财政法规目录" for policy in policies))

    def test_mnr_report_title_pattern_accepts_old_generic_path(self):
        adapter = ADAPTERS["mnr"]
        section = next(item for item in adapter.sections if item.key == "gkbg")
        html = """
        <a href="/2025/t20250120_2800001.html">自然资源部2024年政府信息公开工作报告</a>
        <a href="/2025/t20250120_2800002.html">普通工作动态</a>
        <a href="/xxgkbg/2025/t20250120_2800003.html">自然资源部公开报告</a>
        """

        policies = adapter.parse_index(html, "https://gk.mnr.gov.cn/", section)

        self.assertEqual(
            [policy.url for policy in policies],
            [
                "https://gk.mnr.gov.cn/2025/t20250120_2800001.html",
                "https://gk.mnr.gov.cn/xxgkbg/2025/t20250120_2800003.html",
            ],
        )

    def test_record_count_pager_is_converted_to_page_count(self):
        self.assertEqual(_page_count("<script>createPageHTML(41, 0, 20)</script>", None), 3)

    def test_document_title_drops_site_suffix_only(self):
        adapter = ADAPTERS["ndrc"]
        html = (
            "<html><title>关于印发规划的通知】-国家发展和改革委员会</title>"
            "<body><main>正文内容</main></body></html>"
        )
        document = adapter.parse_document("https://www.ndrc.gov.cn/t20260801_1.html", html)
        self.assertEqual(document.title, "关于印发规划的通知")

    def test_invalid_since_is_rejected_before_fetch(self):
        called = False

        def fetch(_):
            nonlocal called
            called = True
            return ""

        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            list(ADAPTERS["ndrc"].iter_policies(fetch, since="2026-02-30"))
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
