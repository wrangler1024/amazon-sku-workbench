"""Preserve email layout while removing active HTML from the isolated viewer."""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlsplit


_DROP_CONTENT = frozenset({"script", "iframe", "object", "applet"})
_DROP_VOID = frozenset({"embed"})
_DROP_TAG = frozenset({"base", "form"})
_URL_ATTRS = frozenset({"href", "src", "action", "formaction", "background", "xlink:href"})
_UPGRADE_IMAGE_HOSTS = frozenset({"g-ecx.images-amazon.com"})


class _EmailHtmlFilter(HTMLParser):
    def __init__(self, allow_remote: bool):
        super().__init__(convert_charrefs=False)
        self.allow_remote = allow_remote
        self.parts: list[str] = []
        self.skipped: Optional[str] = None
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if self.skipped:
            if tag == self.skipped:
                self.skip_depth += 1
            return
        if tag in _DROP_VOID:
            return
        if tag in _DROP_CONTENT:
            self.skipped = tag
            self.skip_depth = 1
            return
        if tag in _DROP_TAG:
            return
        values = dict(attrs)
        if tag == "meta" and (values.get("http-equiv") or "").strip().lower() == "refresh":
            return
        if tag == "link":
            rel = (values.get("rel") or "").lower().split()
            if not self.allow_remote or "stylesheet" not in rel:
                return
        self.parts.append(self._tag(tag, attrs))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if self.skipped or tag in _DROP_CONTENT or tag in _DROP_VOID or tag in _DROP_TAG:
            return
        values = dict(attrs)
        if tag == "meta" and (values.get("http-equiv") or "").strip().lower() == "refresh":
            return
        if tag == "link":
            rel = (values.get("rel") or "").lower().split()
            if not self.allow_remote or "stylesheet" not in rel:
                return
        self.parts.append(self._tag(tag, attrs, closed=True))

    def handle_endtag(self, tag: str) -> None:
        if self.skipped:
            if tag == self.skipped:
                self.skip_depth -= 1
                if self.skip_depth == 0:
                    self.skipped = None
            return
        if tag not in _DROP_TAG and tag not in _DROP_CONTENT and tag not in _DROP_VOID:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.skipped:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if not self.skipped:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self.skipped:
            self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        if not self.skipped:
            self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        if not self.skipped:
            self.parts.append(f"<!{decl}>")

    @staticmethod
    def _tag(tag: str, attrs: list[tuple[str, Optional[str]]], *, closed: bool = False) -> str:
        clean = [f"<{tag}"]
        for name, value in attrs:
            if name.startswith("on") or name in {"srcdoc", "formaction"}:
                continue
            if value is None:
                clean.append(f" {name}")
                continue
            if name in _URL_ATTRS and value.strip().lower().startswith(("javascript:", "vbscript:")):
                continue
            if tag == "img" and name == "src" and value.lower().startswith("http://"):
                if (urlsplit(value).hostname or "").lower() in _UPGRADE_IMAGE_HOSTS:
                    value = "https://" + value[7:]
            clean.append(f' {name}="{escape(value, quote=True)}"')
        clean.append(" />" if closed else ">")
        return "".join(clean)


def prepare_email_html(source: str, *, allow_remote: bool = False) -> str:
    """Keep tables and inline styles; remove executable and navigating elements."""
    parser = _EmailHtmlFilter(allow_remote)
    parser.feed(source)
    parser.close()
    return "".join(parser.parts)
