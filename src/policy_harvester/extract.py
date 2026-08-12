from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup
from markdownify import markdownify

from .models import Attachment, DocumentContent


ATTACHMENT_SUFFIXES = {
    ".7z",
    ".doc",
    ".docx",
    ".gz",
    ".pdf",
    ".rar",
    ".tar",
    ".xls",
    ".xlsx",
    ".zip",
}


def _attachment_name(link, absolute_url: str) -> str:
    download = str(link.get("download") or "").strip()
    label = " ".join(link.get_text(" ", strip=True).split())
    for candidate in (download, label):
        if PurePosixPath(candidate.lower()).suffix in ATTACHMENT_SUFFIXES:
            return candidate
    return unquote(PurePosixPath(urlsplit(absolute_url).path).name) or label or "attachment"


def _is_attachment(link, absolute_url: str) -> bool:
    if link.has_attr("download"):
        return True
    suffix = PurePosixPath(urlsplit(absolute_url).path.lower()).suffix
    if suffix in ATTACHMENT_SUFFIXES:
        return True
    text = link.get_text(" ", strip=True)
    return bool(re.search(r"(?:附件|下载)", text) and suffix)


def extract_document(url: str, html: str) -> DocumentContent:
    """Extract a useful generic document when a site has no custom parser."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    title = " ".join(heading.get_text(" ", strip=True).split()) if heading else ""
    if not title and soup.title:
        title = " ".join(soup.title.get_text(" ", strip=True).split())

    container = (
        soup.find("article")
        or soup.find("main")
        or soup.select_one(".article, .content, .TRS_Editor, #content")
        or soup.body
        or soup
    )
    for element in container.select("script, style, noscript, nav, footer"):
        element.decompose()

    attachments: list[Attachment] = []
    seen: set[str] = set()
    for link in container.find_all("a", href=True):
        attachment_url = urljoin(url, str(link["href"]).strip())
        if attachment_url in seen or not _is_attachment(link, attachment_url):
            continue
        seen.add(attachment_url)
        attachments.append(Attachment(_attachment_name(link, attachment_url), attachment_url))

    text = markdownify(str(container), heading_style="ATX").strip()
    return DocumentContent(title=title, markdown=text + ("\n" if text else ""), attachments=tuple(attachments))
