"""Parsing entry point and the semantic serialiser.

The tree is parsed once and shared by every layer, so no layer may mutate it.
"""

from __future__ import annotations

import copy
import logging
import re

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

logger = logging.getLogger(__name__)

# lxml over html.parser for speed on large documents, and because it recovers
# from the unclosed tags and stray markup that real pages ship with.
_PARSER = "lxml"


def parse(html: str) -> BeautifulSoup:
    """Parse HTML into a searchable tree. Every layer reads this same tree."""
    return BeautifulSoup(html, _PARSER)



def canonical_url(soup: BeautifulSoup) -> str | None:
    """The page's own URL, taken from <link rel=canonical> or og:url. None if neither.

    Used for two things: the product id is a hash of it, and relative image URLs are
    resolved against it. Some pages publish neither, hence the None.
    """
    link = soup.find("link", rel=lambda value: bool(value) and "canonical" in value)
    if link is not None:
        href = link.get("href")
        if isinstance(href, str) and href.strip():
            return href.strip()

    og_url = soup.find("meta", property="og:url") or soup.find("meta", attrs={"name": "og:url"})
    if og_url is not None:
        content = og_url.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

    return None


# --- semantic serialisation -------------------------------------------------

# Attributes that can carry product meaning. Everything else is presentation.
_KEEP_ATTRS = frozenset(
    {
        "itemprop",
        "itemtype",
        "itemscope",
        "content",
        "src",
        "href",
        # srcset is deliberately absent: it is the same image at a dozen widths,
        # and image harvesting reads it from the live tree instead.
        "alt",
        "title",
        "datetime",
        "value",
        "label",
        "aria-label",
        "aria-labelledby",
        # Not in the presentation category either: role and name are what make a
        # variant picker legible as a picker, and type identifies which scripts
        # hold structured data.
        "role",
        "name",
        "type",
        "lang",
        "rel",
        # Without property, every OpenGraph tag becomes an anonymous <meta content>.
        "property",
        # Selection state, which is what makes a rendered picker legible.
        "aria-pressed",
        "aria-checked",
        "aria-hidden",
    }
)

# Elements that contribute bytes and no product information.
_DROP_TAGS = frozenset({"style", "noscript", "template", "svg", "canvas", "map"})

# Unwrapped rather than decomposed: lxml does not treat the HTML5 <source> as
# void, so a <picture>'s <img> parses as its child and would be removed with it.
_UNWRAP_TAGS = frozenset({"source", "picture", "track", "wbr"})

# <link> is dropped apart from the page's own address: preload hints, stylesheets
# and alternates are numerous and say nothing about the product.
_KEEP_LINK_RELS = frozenset({"canonical"})

# Application-state blobs are dropped despite holding product data: embedded.py
# has already mined them, and a hydration payload can run to hundreds of kilobytes.
_KEEP_SCRIPT_TYPES = frozenset({"application/ld+json"})

# Wrappers safe to unwrap when they carry nothing of their own.
_WRAPPER_TAGS = frozenset({"div", "span", "section", "font"})

_WHITESPACE = re.compile(r"\s+")

# Long enough for any real URL, short enough that a base64 payload or an inlined
# stylesheet cannot dominate the output.
_MAX_ATTR_CHARS = 300


def semantic_html(soup: BeautifulSoup, max_chars: int | None = None) -> str:
    """Rewrite the page as HTML with everything decorative removed.

    Drops styling, scripts, and presentation attributes, keeping structure and text.
    Gets a page to roughly 8% of its original size, which is what makes it cheap to
    send to a model. Works on a copy, because the shared tree must not be changed.
    """
    original_size = len(str(soup))
    working = copy.copy(soup)

    for comment in working.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    for element in working.find_all(list(_DROP_TAGS)):
        element.decompose()

    for element in working.find_all(list(_UNWRAP_TAGS)):
        element.unwrap()

    for script in working.find_all("script"):
        script_type = (script.get("type") or "").strip().lower()
        if script_type not in _KEEP_SCRIPT_TYPES:
            script.decompose()

    for link in working.find_all("link"):
        rel = link.get("rel") or []
        rels = {rel} if isinstance(rel, str) else set(rel)
        if not rels & _KEEP_LINK_RELS:
            link.decompose()

    for element in working.find_all(True):
        _strip_attrs(element)

    for string in list(working.find_all(string=True)):
        if _in_structured_script(string):
            # JSON-LD and application/json payloads are content, not prose;
            # reflowing their whitespace risks corrupting them.
            continue
        collapsed = _WHITESPACE.sub(" ", str(string))
        if collapsed != str(string):
            string.replace_with(NavigableString(collapsed))

    # Each pass can expose more work for the other, so repeat until settled.
    for _ in range(3):
        changed = _unwrap_bare_wrappers(working)
        changed = _drop_empty_elements(working) or changed
        if not changed:
            break

    out = str(working)
    out = _WHITESPACE.sub(" ", out).strip()

    if original_size:
        logger.debug(
            "semantic_html: %d -> %d chars (%.1f%% of original)",
            original_size,
            len(out),
            100 * len(out) / original_size,
        )

    if max_chars is not None and len(out) > max_chars:
        return out[:max_chars]
    return out


def _strip_attrs(element: Tag) -> None:
    """Delete every attribute on one tag except those that can carry meaning."""
    kept = {}
    for name, value in element.attrs.items():
        lowered = name.lower()
        # data-* survives: it is where client-rendered pages park their state,
        # including, on some pages, the entire product record.
        if lowered in _KEEP_ATTRS or lowered.startswith("data-"):
            kept[name] = _shrink_value(value)
    element.attrs = kept


def _shrink_value(value):
    """Cap one attribute's value so a huge one cannot dominate the output.

    An inline base64 image becomes "data:[elided]", and anything else is cut at 300
    characters. The attribute still shows up; only its bulk is gone.
    """
    if not isinstance(value, str):
        return value

    stripped = value.lstrip()
    if stripped.startswith("data:"):
        return "data:[elided]"

    if len(value) > _MAX_ATTR_CHARS:
        return value[:_MAX_ATTR_CHARS] + "\u2026"
    return value


def _in_structured_script(string: NavigableString) -> bool:
    """True if this text is inside a JSON-LD script, where whitespace matters."""
    parent = string.parent
    if parent is None or parent.name != "script":
        return False
    return (parent.get("type") or "").strip().lower() in _KEEP_SCRIPT_TYPES


def _has_kept_attrs(element: Tag) -> bool:
    """True if any attribute survived the strip, so the tag still says something."""
    return bool(element.attrs)


def _unwrap_bare_wrappers(root: BeautifulSoup) -> bool:
    """Remove <div>/<span> wrappers that hold one child and no useful attributes.

    Returns True if anything changed, so the caller knows to look again.
    """
    changed = False
    for element in root.find_all(list(_WRAPPER_TAGS)):
        if _has_kept_attrs(element):
            continue
        children = [child for child in element.children if not _is_blank(child)]
        if len(children) == 1 and isinstance(children[0], Tag):
            element.unwrap()
            changed = True
    return changed


def _drop_empty_elements(root: BeautifulSoup) -> bool:
    """Delete tags left completely empty. Returns True if anything changed."""
    changed = False
    # Reversed document order approximates bottom-up, so a parent is considered
    # after the children that might have just been removed from it.
    for element in reversed(root.find_all(True)):
        if element.name in {"br", "hr", "img", "source", "meta", "link", "input"}:
            continue
        if _has_kept_attrs(element):
            continue
        if element.get_text(strip=True):
            continue
        if element.find(True) is not None:
            continue
        element.decompose()
        changed = True
    return changed


def _is_blank(node) -> bool:
    """True if this node is just whitespace between tags."""
    return isinstance(node, NavigableString) and not str(node).strip()
