"""Common parsing types + dispatch by extension."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PageSegment:
    """A logical page/section of a document. `page` is 1-based (for citations)."""
    page: int
    text: str
    label: str | None = None


class ParseError(Exception):
    pass


def parse_document(path: Path, content_type: str = "") -> list[PageSegment]:
    ext = path.suffix.lower()
    if ext == ".pdf" or "pdf" in content_type:
        from app.parsers import pdf_parser
        return pdf_parser.parse(path)
    if ext in {".docx", ".doc"} or "word" in content_type:
        from app.parsers import docx_parser
        return docx_parser.parse(path)
    if ext in {".txt", ".md"}:
        from app.parsers import text_parser
        return text_parser.parse(path)
    if ext in {".xlsx", ".xls"} or "sheet" in content_type or "excel" in content_type:
        from app.parsers import xlsx_parser
        return xlsx_parser.parse(path)
    raise ParseError(f"Неподдерживаемый тип файла: {ext or content_type}")
