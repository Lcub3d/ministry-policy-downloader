from ._base import DEFAULT_BODY_SELECTORS, Section, SiteAdapter


ADAPTER = SiteAdapter(
    site="mnr",
    base_url="https://www.mnr.gov.cn",
    allowed_hosts=("mnr.gov.cn",),
    article_pattern=r"/t\d{6,8}_\d+\.s?html?$",
    host_aliases=(("m.mnr.gov.cn", "www.mnr.gov.cn"),),
    doc_no_patterns=(
        r"([自国][^（〈(]{0,14}[〈〔（(]\s*20\d{2}\s*[〉〕）)]\s*第?\s*\d+\s*号)",
        r"(自然资源部令\s*第?\s*\d+\s*号)",
        r"(自然资源部公告\s*20\d{2}\s*年\s*第\s*\d+\s*号)",
    ),
    body_selectors=("#UCAP-CONTENT", ".TRS_Editor", ".gk_content", "#comp_1542") + DEFAULT_BODY_SELECTORS,
    sections=(
        Section("tzgg", "通知公告", ("/gk/tzgg/index.html",), selector="ul.ky_open_list"),
        Section("zcjd", "政策解读", ("/gk/zcjd/index.html",), selector="ul.ky_open_list"),
        Section("ghjh", "规划计划", ("/gk/ghjh/",), selector="ul.ky_open_list"),
        Section("yjzj", "意见征集", ("/gk/yjzj/",), selector="ul.ky_open_list"),
        Section("gz", "部门规章", ("https://gk.mnr.gov.cn/zc/gz/",), url_prefixes=("/zc/gz/",)),
        Section("zxgfxwj", "现行有效规范性文件", ("https://gk.mnr.gov.cn/zc/zxgfxwj/",), url_prefixes=("/zc/zxgfxwj/",)),
        Section(
            "gkbg",
            "公开报告",
            ("https://gk.mnr.gov.cn/",),
            url_prefixes=("/xxgkbg/",),
            title_pattern=r"自然资源部\d{4}年政府信息公开工作报告",
        ),
    ),
)

