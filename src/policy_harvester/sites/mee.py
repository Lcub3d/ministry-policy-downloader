from ._base import DEFAULT_BODY_SELECTORS, Section, SiteAdapter


ADAPTER = SiteAdapter(
    site="mee",
    base_url="https://www.mee.gov.cn",
    allowed_hosts=("mee.gov.cn",),
    article_pattern=r"/t\d{6,8}_\d+\.s?html?$",
    items_per_page=20,
    doc_no_patterns=(
        r"([环国][^（〈(]{0,12}[〈〔（(]\s*20\d{2}\s*[〉〕）)]\s*第?\s*\d+\s*号)",
        r"((?:生态环境)?部令\s*第?\s*\d+\s*号)",
        r"((?:生态环境部)?公告\s*20\d{2}\s*年\s*第\s*\d+\s*号)",
    ),
    body_selectors=("#UCAP-CONTENT", ".TRS_Editor", ".Custom_UnionStyle") + DEFAULT_BODY_SELECTORS,
    sections=(
        Section("zyygwj", "中央有关文件", ("/zcwj/zyygwj/index.shtml",)),
        Section("gwywj", "国务院有关文件", ("/zcwj/gwywj/index.shtml",)),
        Section("bwj_ling", "部令", ("/zcwj/bwj/ling/index.shtml",)),
        Section("bwj_gg", "部公告", ("/zcwj/bwj/gg/index.shtml",)),
        Section("bwj_wj", "部文件", ("/zcwj/bwj/wj/index.shtml",)),
        Section("bwj_han", "部函", ("/zcwj/bwj/han/index.shtml",)),
        Section("bgtwj_wj", "办公厅文件", ("/zcwj/bgtwj/wj/index.shtml",)),
        Section("bgtwj_han", "办公厅函", ("/zcwj/bgtwj/han/index.shtml",)),
        Section("qt", "其他政策文件", ("/zcwj/qt/index.shtml",)),
        Section("zcjd", "政策解读", ("/zcwj/zcjd/index.shtml",)),
        Section("gzk", "规章库", ("/gzk/index.shtml", "/gzk/index.html")),
        Section("fgbz", "法规标准", ("/ywgz/fgbz/index.shtml",)),
        Section("gb_zghjzk", "中国生态环境状况公报", ("/hjzl/sthjzk/zghjzkgb/index.shtml",)),
        Section("gb_tjnb", "生态环境统计年报", ("/hjzl/sthjzk/sthjtjnb/index.shtml",)),
        Section("gb_zsbg", "中国噪声污染防治报告", ("/hjzl/sthjzk/hjzywr/index.shtml",)),
        Section("gb_gtfw", "大中城市固体废物污染防治年报", ("/hjzl/sthjzk/gtfwwrfz/index.shtml",)),
        Section("gb_jagb", "中国海洋生态环境状况公报", ("/hjzl/sthjzk/jagb/index.shtml",)),
        Section("gb_ydy", "中国移动源环境管理年报", ("/hjzl/sthjzk/ydyhjgl/index.shtml",)),
    ),
)

