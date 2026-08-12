from ._base import DEFAULT_BODY_SELECTORS, Section, SiteAdapter


ADAPTER = SiteAdapter(
    site="ndrc",
    base_url="https://www.ndrc.gov.cn",
    allowed_hosts=("ndrc.gov.cn",),
    article_pattern=r"/t\d{6,8}_\d+(?:_\w+)?\.s?html?$",
    host_aliases=(("m.ndrc.gov.cn", "www.ndrc.gov.cn"),),
    doc_no_patterns=(
        r"(发改[\u4e00-\u9fff]{1,8}〔\s*20\d{2}\s*〕\s*\d+\s*号)",
        r"((?:国家)?发展和?改革委员会令\s*第?\s*\d+\s*号)",
        r"(第\s*\d+\s*号令)",
    ),
    body_selectors=("#UCAP-CONTENT", ".TRS_Editor", ".editorContent-main") + DEFAULT_BODY_SELECTORS,
    sections=(
        Section("tz", "通知", ("/xxgk/zcfb/tz/index.html", "/xxgk/zcfb/tz/"), url_prefixes=("/xxgk/zcfb/tz/",)),
        Section("gg", "公告", ("/xxgk/zcfb/gg/index.html", "/xxgk/zcfb/gg/"), url_prefixes=("/xxgk/zcfb/gg/",)),
        Section("fzggwl", "发展改革委令", ("/xxgk/zcfb/fzggwl/index.html",), url_prefixes=("/xxgk/zcfb/fzggwl/",)),
        Section("ghxwj", "规范性文件", ("/xxgk/zcfb/ghxwj/index.html",), url_prefixes=("/xxgk/zcfb/ghxwj/",)),
        Section("ghwb", "规划文本", ("/xxgk/zcfb/ghwb/index.html",), url_prefixes=("/xxgk/zcfb/ghwb/",)),
        Section("jd", "政策解读", ("/xxgk/jd/jd/index.html",), url_prefixes=("/xxgk/jd/jd/",)),
        Section("zctj", "政策图解", ("/xxgk/jd/zctj/index.html",), url_prefixes=("/xxgk/jd/zctj/",)),
    ),
)

