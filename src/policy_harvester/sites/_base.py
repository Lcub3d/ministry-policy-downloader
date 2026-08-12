from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Callable, Iterator
from urllib.error import HTTPError
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request

from bs4 import BeautifulSoup
from markdownify import markdownify

from ..models import Attachment, DocumentContent, Policy


ARTICLE_DATE = re.compile(r"/t(20\d{2})(\d{2})(\d{2})_")
VISIBLE_DATE = re.compile(r"(20\d{2})\s*[-/.年月]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?")
ATTACHMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt",
    ".zip", ".rar", ".7z", ".ppt", ".pptx", ".wps", ".et", ".dwg",
}
NOISE_SELECTORS = (
    "nav", "header", "footer", "script", "style", "form", "noscript", "iframe",
    ".footer", ".foot", ".header", ".nav", ".crumb", ".breadcrumb", ".share",
    ".bdsharebuttonbox", ".print", ".tools", ".sidebar", ".yqlj",
    "#footer", "#foot", "#header", "#nav", "#barrierfree_container",
)
DEFAULT_BODY_SELECTORS = (
    "#UCAP-CONTENT", ".TRS_Editor", "#zoom", ".article-content", ".editor-content",
    ".pages_content", ".con_text", "#mainText", ".gk_content", ".article_content",
    "article", "main",
)


@dataclass(frozen=True, slots=True)
class Section:
    key: str
    name: str
    indexes: tuple[str, ...]
    selector: str | None = None
    url_prefixes: tuple[str, ...] = ()
    title_pattern: str | None = None


@dataclass(frozen=True, slots=True)
class SiteAdapter:
    site: str
    base_url: str
    allowed_hosts: tuple[str, ...]
    article_pattern: str
    sections: tuple[Section, ...]
    doc_no_patterns: tuple[str, ...]
    body_selectors: tuple[str, ...] = DEFAULT_BODY_SELECTORS
    host_aliases: tuple[tuple[str, str], ...] = ()
    force_https: bool = False
    items_per_page: int | None = None
    max_pages: int = 500

    def normalize_url(self, url: str, base_url: str | None = None) -> str:
        absolute = urljoin(base_url or self.base_url, url.strip())
        parts = urlsplit(absolute)
        host = parts.hostname.casefold() if parts.hostname else ""
        aliases = dict(self.host_aliases)
        if host in aliases:
            host = aliases[host]
            port = f":{parts.port}" if parts.port else ""
            parts = parts._replace(netloc=host + port)
        if self.force_https and self._host_allowed(host):
            parts = parts._replace(scheme="https")
        return urlunsplit(parts._replace(fragment=""))

    def page_url(self, index: str, page: int) -> str:
        first = self.normalize_url(index)
        if page <= 1:
            return first
        parts = urlsplit(first)
        if parts.path.endswith("/"):
            path = f"{parts.path}index_{page - 1}.html"
        else:
            match = re.match(r"(.*/index)(\.s?html?|\.htm)$", parts.path, re.IGNORECASE)
            if not match:
                raise ValueError(f"无法从列表页推导分页地址：{first}")
            path = f"{match.group(1)}_{page - 1}{match.group(2)}"
        return urlunsplit(parts._replace(path=path))

    def parse_index(self, html: str, page_url: str, section: Section) -> list[Policy]:
        soup = BeautifulSoup(html, "html.parser")
        root = soup.select_one(section.selector) if section.selector else soup
        if root is None:
            return []
        policies: list[Policy] = []
        seen: set[str] = set()
        for anchor in root.find_all("a", href=True):
            url = self.normalize_url(anchor["href"], page_url)
            parts = urlsplit(url)
            if not self._host_allowed(parts.hostname or ""):
                continue
            is_page = bool(re.search(self.article_pattern, parts.path, re.IGNORECASE))
            is_file = _is_attachment(url)
            if not is_page and not is_file:
                continue
            title = _clean_title(anchor.get("title") or anchor.get_text(" ", strip=True))
            if len(title) < 5 or url in seen:
                continue
            if (section.url_prefixes or section.title_pattern) and not (
                any(parts.path.startswith(prefix) for prefix in section.url_prefixes)
                or bool(section.title_pattern and re.search(section.title_pattern, title))
            ):
                continue
            seen.add(url)
            date = _date_near(anchor) or _date_from_url(url)
            policies.append(Policy(
                site=self.site,
                section=section.name,
                title=title,
                url=url,
                date=date,
                doc_no=self._doc_no(title),
                kind="file" if is_file else "page",
            ))
        return policies

    def iter_policies(
        self,
        fetch_text: Callable[[str | Request], str],
        since: str | None = None,
    ) -> Iterator[Policy]:
        self._validate_since(since)
        yielded: set[str] = set()
        for section in self.sections:
            for item in self._iter_section(fetch_text, section, since):
                if item.url not in yielded:
                    yielded.add(item.url)
                    yield item

    def _iter_section(
        self,
        fetch_text: Callable[[str | Request], str],
        section: Section,
        since: str | None,
    ) -> Iterator[Policy]:
        first_url, first_html, first_items = self._load_first_page(fetch_text, section)
        total_pages = _page_count(first_html, self.items_per_page)
        page = 1
        seen_in_section: set[str] = set()
        while True:
            if page == 1:
                page_url, html, items = first_url, first_html, first_items
            else:
                if total_pages is not None and page > total_pages:
                    break
                page_url = self.page_url(first_url, page)
                try:
                    html = fetch_text(page_url)
                except HTTPError as exc:
                    if total_pages is None and exc.code in {404, 410}:
                        break
                    raise RuntimeError(f"{self.site}/{section.key} 第 {page} 页加载失败") from exc
                items = self.parse_index(html, page_url, section)

            page_urls = {item.url for item in items}
            if not items:
                if total_pages is not None:
                    raise RuntimeError(f"{self.site}/{section.key} 第 {page} 页未解析到政策")
                break
            if page_urls <= seen_in_section:
                if total_pages is not None and page <= total_pages:
                    raise RuntimeError(f"{self.site}/{section.key} 第 {page} 页与前页重复")
                break
            seen_in_section.update(page_urls)

            for item in items:
                if not since or not item.date or item.date >= since:
                    yield item

            dated = [item.date for item in items if item.date]
            if since and len(dated) == len(items) and max(dated) < since:
                break
            if total_pages is not None and page >= total_pages:
                break
            if total_pages is None and page >= self.max_pages:
                raise RuntimeError(f"{self.site}/{section.key} 分页超过安全上限 {self.max_pages}")
            page += 1

    def parse_document(self, url: str, html: str) -> DocumentContent:
        url = self.normalize_url(url)
        soup = BeautifulSoup(html, "html.parser")
        heading = soup.find("h1")
        title = _clean_title(heading.get_text(" ", strip=True) if heading else "")
        if not title and soup.title:
            title = _clean_document_title(soup.title.get_text(" ", strip=True))

        candidates = [node for selector in self.body_selectors for node in soup.select(selector)]
        body = max(candidates, key=lambda node: len(node.get_text(" ", strip=True)), default=soup.body or soup)
        clean = BeautifulSoup(str(body), "html.parser")
        for selector in NOISE_SELECTORS:
            for node in clean.select(selector):
                node.decompose()
        for node in clean.find_all(class_=re.compile(r"footer|foot|nav|crumb|breadcrumb|share|tools", re.I)):
            node.decompose()
        for node in clean.find_all(id=re.compile(r"footer|foot|nav|crumb|breadcrumb|share|tools", re.I)):
            node.decompose()
        for image in clean.find_all("img", src=True):
            image["src"] = self.normalize_url(image["src"], url)
            image["alt"] = image.get("alt") or "政策图片"
        for link in clean.find_all("a", href=True):
            link["href"] = self.normalize_url(link["href"], url)

        attachments: list[Attachment] = []
        seen: set[str] = set()
        for link in soup.find_all("a", href=True):
            attachment_url = self.normalize_url(link["href"], url)
            if attachment_url in seen or not _is_attachment(attachment_url):
                continue
            seen.add(attachment_url)
            attachments.append(Attachment(
                name=_attachment_name(link.get_text(" ", strip=True), attachment_url),
                url=attachment_url,
            ))

        body_markdown = markdownify(str(clean), heading_style="ATX", strip=["script", "style", "svg"])
        return DocumentContent(
            title=title,
            markdown=_compact_markdown(body_markdown),
            attachments=tuple(attachments),
        )

    def _load_first_page(
        self,
        fetch_text: Callable[[str | Request], str],
        section: Section,
    ) -> tuple[str, str, list[Policy]]:
        failures: list[Exception] = []
        for index in section.indexes:
            url = self.page_url(index, 1)
            try:
                html = fetch_text(url)
            except Exception as exc:
                failures.append(exc)
                continue
            items = self.parse_index(html, url, section)
            if items:
                return url, html, items
        cause = failures[-1] if failures else None
        raise RuntimeError(f"{self.site}/{section.key} 首页未解析到政策") from cause

    @staticmethod
    def _validate_since(since: str | None) -> None:
        if since:
            try:
                datetime.strptime(since, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError("since 必须为 YYYY-MM-DD") from exc

    def _doc_no(self, title: str) -> str:
        for pattern in self.doc_no_patterns:
            match = re.search(pattern, title)
            if match:
                return re.sub(r"\s+", "", match.group(1))
        return ""

    def _host_allowed(self, host: str) -> bool:
        host = host.casefold().rstrip(".")
        return any(host == allowed or host.endswith(f".{allowed}") for allowed in self.allowed_hosts)


def _clean_title(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    value = re.sub(r"\s*[（(]\s*20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?\s*[）)]\s*$", "", value)
    return re.sub(r"^[【\[]\s*|\s*[】\]]$", "", value).strip()


def _clean_document_title(value: str) -> str:
    value = _clean_title(value)
    value = re.sub(
        r"[】\]]?\s*[-—–|_]\s*(?:国家发展和改革委员会|中华人民共和国生态环境部|"
        r"中华人民共和国自然资源部|中华人民共和国财政部|生态环境部|自然资源部|财政部)\s*$",
        "",
        value,
    )
    return value.rstrip("】]").strip()


def _date_near(anchor) -> str:
    node = anchor
    for _ in range(5):
        node = node.parent
        if node is None:
            break
        match = VISIBLE_DATE.search(node.get_text(" ", strip=True))
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return ""


def _date_from_url(url: str) -> str:
    match = ARTICLE_DATE.search(url)
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else ""


def _page_count(html: str, items_per_page: int | None) -> int | None:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    create_page = re.search(
        r"createPageHTML\s*\(\s*(\d+)\s*,\s*\d+\s*,\s*(\d+)",
        html,
        re.IGNORECASE,
    )
    if create_page:
        total, per_page = int(create_page.group(1)), max(1, int(create_page.group(2)))
        return max(1, (total + per_page - 1) // per_page)
    for pattern in (
        r"共\s*(\d+)\s*页",
        r"(?:totalPages?|pageCount)\s*[:=]\s*['\"]?(\d+)",
    ):
        match = re.search(pattern, text if pattern.startswith("共") else html, re.IGNORECASE)
        if match:
            return max(1, int(match.group(1)))
    match = re.search(r"共\s*(\d+)\s*条", text)
    if match and items_per_page:
        return max(1, (int(match.group(1)) + items_per_page - 1) // items_per_page)
    indexes = [int(value) for value in re.findall(r"index_(\d+)\.s?html?", html, re.IGNORECASE)]
    return max(indexes) + 1 if indexes else None


def _is_attachment(url: str) -> bool:
    lower = url.casefold()
    path = urlsplit(lower).path
    return (
        PurePosixPath(path).suffix in ATTACHMENT_EXTENSIONS
        or "document/download" in lower
        or "downloadattach" in lower
        or "/cms_files/" in lower
        or "/file/" in lower
    )


def _attachment_name(text: str, url: str) -> str:
    query = parse_qs(urlsplit(url).query)
    encoded = next((query[key][0] for key in ("fileName", "filename", "n") if query.get(key)), "")
    fallback = unquote(encoded) if encoded else unquote(PurePosixPath(urlsplit(url).path).name)
    fallback = fallback or "附件"
    name = re.sub(r"^附件\s*[一二三四五六七八九十\d]*\s*[:：、.．-]?\s*", "", text.strip())
    if not name or name in {"下载", "点击下载", "附件", "查看附件"}:
        name = fallback
    suffix = PurePosixPath(fallback).suffix
    if suffix.casefold() in ATTACHMENT_EXTENSIONS and not PurePosixPath(name).suffix:
        name += suffix
    return re.sub(r"\s+", " ", name).strip()


def _compact_markdown(value: str) -> str:
    lines: list[str] = []
    blank = False
    for line in value.splitlines():
        line = line.rstrip()
        if line.strip():
            lines.append(line)
            blank = False
        elif not blank:
            lines.append("")
            blank = True
    return "\n".join(lines).strip()

