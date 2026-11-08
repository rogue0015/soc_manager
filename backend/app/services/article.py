"""Reads a blog post as an article: its title, summary, tags and the full text as Markdown.

Used for platforms that publish whole articles (dev.to). Most posts only exist
as the generated HTML page (the blog keeps its Markdown sources offline), so the
text is taken from the page's <div class="post-body"> and turned back into
Markdown. The blog's builder only emits a small set of tags (paragraphs, headings,
lists, quotes, links, emphasis, code, images, rules), so a small converter on
the standard library covers it; unknown tags keep their text. Links and images
become absolute, because the article lives on another site.
See docs/decisions/0007-devto-articles.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import requests

from ..db import Database
from ..platforms import Article
from .blog import parse_post
from .errors import InvalidInput

TIMEOUT = 15
VOID = {"area", "br", "hr", "img", "input", "link", "meta", "source", "wbr"}
INLINE = {"a", "abbr", "b", "br", "cite", "code", "del", "em", "i", "img", "kbd", "mark", "s", "small",
          "span", "strike", "strong", "sub", "sup", "time", "u"}
SKIP = {"script", "style", "noscript", "template", "button", "form", "iframe"}
# Characters that mean something in Markdown when they appear in plain text.
SPECIAL = re.compile(r"([\\`*_\[\]<])")
BLOCK_START = re.compile(r"^(#{1,6}\s|>|[-+*]\s|\d+[.)]\s)")


class ArticleError(Exception):
    """The blog post couldn't be read as an article. The message is shown to the user."""


# ---- HTML -> a small tree ----------------------------------------------------

@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)   # _Node | str


class _TreeBuilder(HTMLParser):
    """Forgiving: an end tag closes everything opened after its start tag; stray end tags are ignored."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _parse(html: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def _find(node: _Node, tag: str, css_class: str) -> _Node | None:
    if node.tag == tag and css_class in node.attrs.get("class", "").split():
        return node
    for child in node.children:
        if isinstance(child, _Node) and (found := _find(child, tag, css_class)):
            return found
    return None


def _text(node: _Node | str) -> str:
    if isinstance(node, str):
        return node
    return "".join(_text(c) for c in node.children)


# ---- tree -> Markdown ------------------------------------------------------------

def _escape(text: str) -> str:
    return SPECIAL.sub(r"\\\1", re.sub(r"\s+", " ", text))


def _escape_block_start(text: str) -> str:
    """Plain text that happens to start like a heading, quote or list item must stay plain text."""
    if not BLOCK_START.match(text):
        return text
    if text[0].isdigit():
        return re.sub(r"^(\d+)([.)])", r"\1\\\2", text)
    return "\\" + text


def _wrap(inner: str, marker: str) -> str:
    """Emphasis markers must touch the text, so spaces move outside them."""
    core = inner.strip()
    if not core:
        return inner
    lead = inner[: len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()):]
    return f"{lead}{marker}{core}{marker}{trail}"


def _code_span(text: str) -> str:
    fence = "``" if "`" in text else "`"
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _url(base: str, href: str) -> str:
    url = urljoin(base, href.strip())
    return f"<{url}>" if re.search(r"[\s()<>]", url) else url


def _inline(nodes: list[Any], base: str) -> str:
    out = []
    for n in nodes:
        if isinstance(n, str):
            out.append(_escape(n))
            continue
        tag, inner = n.tag, (lambda: _inline(n.children, base))
        if tag in SKIP:
            continue
        if tag in ("strong", "b"):
            out.append(_wrap(inner(), "**"))
        elif tag in ("em", "i", "cite"):
            out.append(_wrap(inner(), "*"))
        elif tag in ("del", "s", "strike"):
            out.append(_wrap(inner(), "~~"))
        elif tag == "code":
            out.append(_code_span(_text(n)))
        elif tag == "br":
            out.append("  \n")
        elif tag == "img":
            src = n.attrs.get("src", "")
            if src:
                out.append(f"![{_escape(n.attrs.get('alt', ''))}]({_url(base, src)})")
        elif tag == "a":
            text, href = inner().strip(), n.attrs.get("href", "").strip()
            if not href or href.startswith(("#", "javascript:")):
                out.append(text)
            else:
                out.append(f"[{text or _escape(urljoin(base, href))}]({_url(base, href)})")
        else:
            out.append(inner())
    return "".join(out)


def _list(node: _Node, base: str) -> str:
    ordered = node.tag == "ol"
    try:
        start = int(node.attrs.get("start") or 1)
    except ValueError:
        start = 1
    items = [c for c in node.children if isinstance(c, _Node) and c.tag == "li"]
    lines = []
    for i, li in enumerate(items):
        marker = f"{start + i}." if ordered else "-"
        pad = " " * (len(marker) + 1)
        body = "\n".join(_blocks(li.children, base)) or ""
        first, *rest = body.split("\n")
        lines.append(f"{marker} {first}".rstrip())
        lines.extend(pad + line if line else "" for line in rest)
    return "\n".join(lines)


def _blocks(nodes: list[Any], base: str) -> list[str]:
    blocks: list[str] = []
    pending: list[Any] = []   # inline content between block elements

    def flush() -> None:
        text = _inline(pending, base).strip()
        pending.clear()
        if text:
            blocks.append(_escape_block_start(text))

    for n in nodes:
        if isinstance(n, str) or n.tag in INLINE:
            pending.append(n)
            continue
        flush()
        tag = n.tag
        if tag in SKIP:
            continue
        if tag == "p":
            text = _inline(n.children, base).strip()
            if text:
                blocks.append(_escape_block_start(text))
        elif re.fullmatch(r"h[1-6]", tag):
            text = _inline(n.children, base).strip()
            if text:
                blocks.append(f"{'#' * int(tag[1])} {text}")
        elif tag == "hr":
            blocks.append("---")
        elif tag == "pre":
            code = _text(n).strip("\n")
            lang = next((c.attrs.get("class", "") for c in n.children if isinstance(c, _Node) and c.tag == "code"), "")
            lang = re.sub(r"^(language|lang)-", "", lang.split()[0]) if lang else ""
            fence = "````" if "```" in code else "```"
            blocks.append(f"{fence}{lang}\n{code}\n{fence}")
        elif tag == "blockquote":
            inner = "\n\n".join(_blocks(n.children, base))
            blocks.append("\n".join(f"> {line}" if line else ">" for line in inner.split("\n")))
        elif tag in ("ul", "ol"):
            blocks.append(_list(n, base))
        else:   # div, section, figure, table, ...: keep what's inside
            blocks.extend(_blocks(n.children, base))
    flush()
    return blocks


def html_to_markdown(html: str, base_url: str) -> str:
    """Markdown for an HTML fragment; relative links and images are resolved against `base_url`."""
    return "\n\n".join(_blocks(_parse(html).children, base_url)).strip() + "\n"


def post_body_markdown(page_html: str, page_url: str) -> str | None:
    """The article text of a blog post page as Markdown, or None if the page has no post body."""
    root = _parse(page_html)
    body = _find(root, "div", "post-body") or _find(root, "article", "post")
    if body is None:
        return None
    markdown = "\n\n".join(_blocks(body.children, page_url)).strip()
    return markdown + "\n" if markdown else None


# ---- reading a blog post ------------------------------------------------------------

class ArticleReader:
    def __init__(self, db: Database, fetch: Callable[..., requests.Response] = requests.get):
        self.db = db
        self.fetch = fetch

    def read(self, url: str) -> Article:
        """Fetch the published post and return it as an Article. Raises ArticleError."""
        try:
            resp = self.fetch(url, timeout=TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ArticleError(f"Couldn't read the blog post {url} ({type(exc).__name__}). "
                               "Check that it's online, then try again.") from exc
        meta = parse_post(resp.text, url)
        markdown = post_body_markdown(resp.text, meta["url"])
        if not markdown:
            raise ArticleError(f"Couldn't find the article text on {url}.")
        return Article(title=meta["title"], markdown=markdown, canonical_url=meta["url"],
                       description=meta["description"], tags=tuple(meta["tags"]))

    def preview(self, url: str) -> dict[str, Any]:
        """The article a platform would get for a post in the blog library (for the composer's preview)."""
        with self.db.connect() as conn:
            known = conn.execute("SELECT 1 FROM blog_posts WHERE url = ?", (url,)).fetchone()
        if not known:
            raise InvalidInput("That post isn't in the blog library. Press Sync, then try again.")
        try:
            return asdict(self.read(url))
        except ArticleError as exc:
            raise InvalidInput(str(exc)) from exc
