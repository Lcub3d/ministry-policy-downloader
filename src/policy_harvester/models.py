from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Attachment:
    name: str
    url: str


@dataclass(frozen=True, slots=True)
class Policy:
    site: str
    section: str
    title: str
    url: str
    date: str = ""
    doc_no: str = ""
    kind: str = "page"


@dataclass(frozen=True, slots=True)
class DocumentContent:
    title: str
    markdown: str
    attachments: tuple[Attachment, ...] = ()
