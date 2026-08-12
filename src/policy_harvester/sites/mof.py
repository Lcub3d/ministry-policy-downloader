from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from urllib.parse import urlencode
from urllib.request import Request

from ..models import Policy
from ._base import DEFAULT_BODY_SELECTORS, Section, SiteAdapter


SEARCH_URL = "https://search.mof.gov.cn/was5/web/search"
SEARCH_PER_PAGE = 100
SEARCH_PARAMS = {
    "channelid": "201408",
    "was_custom_expr": " themecat=2180",
    "perpage": str(SEARCH_PER_PAGE),
    "outlinepage": "10",
    "searchscope": "title",
    "sStartTime": "",
    "sEndTime": "",
    "orderby": "",
    "ticai": "",
    "jigou": "",
    "filenumwords": "",
    "zhuti": "2180",
}


class MofAdapter(SiteAdapter):
    def iter_policies(
        self,
        fetch_text: Callable[[str | Request], str],
        since: str | None = None,
    ) -> Iterator[Policy]:
        self._validate_since(since)
        yielded: set[str] = set()
        for section in self.sections:
            items = (
                self._iter_search(fetch_text, section, since)
                if section.key == "fgml"
                else self._iter_section(fetch_text, section, since)
            )
            for item in items:
                if item.url not in yielded:
                    yielded.add(item.url)
                    yield item

    def _iter_search(
        self,
        fetch_text: Callable[[str | Request], str],
        section: Section,
        since: str | None,
    ) -> Iterator[Policy]:
        form = urlencode(SEARCH_PARAMS).encode("ascii")
        summary = fetch_text(Request(SEARCH_URL, data=form))
        match = re.search(r"找到相关结果约\s*([\d,]+)\s*条", summary)
        if not match:
            raise RuntimeError(f"{self.site}/{section.key} 未解析到检索总数")
        total = int(match.group(1).replace(",", ""))
        pages = (total + SEARCH_PER_PAGE - 1) // SEARCH_PER_PAGE
        if not pages:
            raise RuntimeError(f"{self.site}/{section.key} 检索结果为空")
        for page in range(1, pages + 1):
            url = f"{SEARCH_URL}?{urlencode({**SEARCH_PARAMS, 'page': page})}"
            policies = self.parse_index(fetch_text(url), url, section)
            if not policies:
                raise RuntimeError(f"{self.site}/{section.key} 第 {page} 页未解析到政策")
            for policy in policies:
                if not since or not policy.date or policy.date >= since:
                    yield policy


ADAPTER = MofAdapter(
    site="mof",
    base_url="https://www.mof.gov.cn",
    allowed_hosts=("mof.gov.cn",),
    article_pattern=r"/t\d{6,8}_\d+\.s?html?$",
    doc_no_patterns=(
        r"([财国][^（〈(]{0,14}[〈〔（(]\s*20\d{2}\s*[〉〕）)]\s*第?\s*\d+\s*号)",
        r"(财政部令\s*第?\s*\d+\s*号)",
        r"(财政部公告\s*20\d{2}\s*年\s*第\s*\d+\s*号)",
    ),
    body_selectors=("#UCAP-CONTENT", ".TRS_Editor", ".article_content", "#zoom") + DEFAULT_BODY_SELECTORS,
    sections=(
        Section("zcfb", "政策发布", ("/zhengwuxinxi/zhengcefabu/index.htm",)),
        Section("tzgg", "通知通告", ("/gkml/bulinggonggao/tongzhitonggao/index.htm",)),
        Section("czbl", "财政部令", ("/gkml/bulinggonggao/czbl/index.htm",)),
        Section("czbgg", "财政部公告", ("/gkml/bulinggonggao/czbgg/index.htm",)),
        Section("fgml", "财政法规目录", ()),
        Section("czwg", "财政文告", ("/gkml/caizhengwengao/index.htm",)),
        Section("czsj", "财政数据", ("/gkml/caizhengshuju/index.htm",)),
    ),
)

