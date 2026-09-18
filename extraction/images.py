"""Image harvesting and full-resolution normalisation.

Images are produced deterministically and the model never emits them, because long
lists are what it truncates and mangles. Declared sources are read first, then the
DOM, and each is resolved to an absolute URL for the largest available rendition.

The parameters and path shapes below are published CDN conventions, not rules keyed
off a hostname; a CDN using its own names is left alone.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from extraction.layers import meta
from extraction.dom import canonical_url
from extraction.candidates import IMAGE_URLS, VARIANTS, CandidateBundle

logger = logging.getLogger(__name__)

# Schemes that cannot be fetched as a product image.
_REJECTED_SCHEMES = frozenset({"data", "blob", "javascript", "about", "mailto"})

# Query parameters that request a *rendition* rather than identify the image.
# Removing them asks the CDN for its original.
_DIMENSION_PARAMS = frozenset(
    {
        # generic
        "w", "h", "width", "height", "maxwidth", "maxheight", "max_width",
        "max_height", "mw", "mh", "size", "sz", "sw", "sh", "dpr", "scale",
        "resize", "fit", "crop", "zoom", "max", "min",
        # Adobe Scene7
        "wid", "hei", "qlt", "scl",
        # Imgix / Shopify / Contentful family
        "q", "quality", "compress", "lossless",
        # Next.js image endpoint
        "imwidth", "imageheight", "imagewidth",
    }
)

# Change how the image renders, not which image it is. Stripping them collapses
# what would otherwise be several gallery entries for one photograph.
_AUXILIARY_PARAMS = frozenset(
    {
        "resmode", "defaultimage", "bgc", "bgcolor", "iccembed", "printres",
        "op_usm", "op_sharpen", "cache", "sharpen", "interpolation",
    }
)

# Preserved because some CDNs error without them, but excluded from the dedupe key:
# one photograph offered as both webp and jpeg is one image.
_PRESERVED_PARAMS = frozenset({"fmt", "format", "auto", "output", "ext", "fm"})

# Comma-separated short-prefixed directives such as "w_640,c_limit", matched on the
# shape of the segment rather than on any vendor's hostname.
_TRANSFORM_SEGMENT = re.compile(r"^[a-z]{1,3}_[^/]+(?:,[a-z]{1,3}_[^/]+)*$")

# Extensions that are never product photography. Vector and icon formats are
# used for interface furniture; a gallery image is a raster photograph.
_REJECTED_EXTENSIONS = frozenset({".svg", ".ico", ".cur"})

# Interface furniture. These are unambiguous anywhere in the filename...
_FILENAME_SUBSTRINGS = (
    "sprite", "spinner", "loading", "placeholder", "favicon", "blank", "pixel",
    "spacer", "transparent", "tracking", "beacon", "watermark",
)

# ...whereas these could appear inside a legitimate product name, so they only
# count as a whole word in the filename ("ace_logo.png" yes, "flagship" no).
_FILENAME_TOKENS = frozenset(
    {"logo", "logos", "icon", "icons", "badge", "badges", "flag", "flags", "avatar"}
)

_FILENAME_TOKEN_SPLIT = re.compile(r"[-_./]+")

# Checked as whole path segments, so /icons/ is caught while "iconic-chair" is not.
# Catches furniture whose filename gives nothing away, such as a share button.
_CHROME_PATH_SEGMENTS = frozenset(
    {
        "icon", "icons", "logo", "logos", "sprite", "sprites", "badge", "badges",
        "flag", "flags", "ui", "chrome", "buttons", "svg", "fonts", "emoji",
    }
)

# Hosts that serve measurement rather than media, and path prefixes that serve
# data rather than files.
_NON_MEDIA_HOST_HINTS = ("analytics", "tracking", "telemetry", "metrics", "beacon")
_NON_MEDIA_PATH_HINTS = ("/api/", "/track/", "/collect", "/event")

# Elements whose images are page furniture.
_CHROME_ANCESTORS = frozenset({"nav", "header", "footer", "aside"})
_CHROME_ROLES = frozenset({"navigation", "banner", "contentinfo", "search", "dialog"})

# Below this, a declared width or height means an icon, not a gallery image.
_MIN_DECLARED_PIXELS = 100

# A gallery, not a crawl of every asset: pages routinely carry a hundred img
# elements, nearly all of them interface furniture.
_MAX_IMAGES = 24

# Lazy-loading attributes, in the two spellings that dominate.
_LAZY_ATTRS = ("data-src", "data-original")
_LAZY_SRCSET_ATTRS = ("data-srcset", "data-lazy-srcset")

_SRCSET_DESCRIPTOR = re.compile(r"^[\d.]+[wx],?$")


def harvest(
    soup: BeautifulSoup,
    bundle: CandidateBundle | None = None,
    base_url: str | None = None,
) -> list[str]:
    """Every product photo on the page, at full resolution, best source first.

    Takes the images the site declared, adds anything else the markup references,
    filters out interface graphics like logos and icons, and rewrites each URL to ask
    for the original rather than a thumbnail. The declared ones come from the bundle
    rather than being re-parsed, so JSON-LD and microdata images have one owner.
    """
    if base_url is None:
        base_url = canonical_url(soup)

    declared = _declared(bundle) if bundle is not None else []
    scraped = _from_dom(soup)

    ordered = declared + _restrict_to_declared_hosts(scraped, declared, base_url)

    out: list[str] = []
    seen: set[str] = set()
    for raw in ordered:
        normalised = normalize(raw, base_url)
        if normalised is None:
            continue
        key = _dedupe_key(normalised)
        if key in seen:
            continue
        seen.add(key)
        out.append(normalised)
        if len(out) >= _MAX_IMAGES:
            break
    return out


def _restrict_to_declared_hosts(
    scraped: list[str], declared: list[str], base_url: str | None
) -> list[str]:
    """Drop scraped images not served from the same host as the declared ones.

    Menu promotions, review photos and ad creative are real images on real CDNs, so
    filename and size checks cannot spot them, but the host serving the official
    gallery can. If the page declared nothing, everything is kept.
    """
    hosts = {
        urlparse(url).netloc.lower()
        for url in (normalize(entry, base_url) for entry in declared)
        if url
    }
    if not hosts:
        return scraped

    # The page's own origin is always allowed: a site may serve its gallery itself
    # while declaring og:image on a CDN, or the reverse.
    if base_url:
        own = urlparse(base_url).netloc.lower()
        if own:
            hosts.add(own)

    kept: list[str] = []
    dropped = 0
    for entry in scraped:
        normalised = normalize(entry, base_url)
        if normalised and urlparse(normalised).netloc.lower() in hosts:
            kept.append(entry)
        else:
            dropped += 1

    if dropped:
        logger.debug("images: dropped %d off-host images, kept %d", dropped, len(kept))
    return kept


def _declared(bundle: CandidateBundle) -> list[str]:
    """Image URLs the site published itself, from JSON-LD, microdata and og:image.

    Images attached to variants count too, since a product group often carries no
    photos of its own and hangs them off each variant instead.
    """
    entries: list[tuple[str, int, str]] = []
    sequence = 0

    for candidate in bundle.for_field(IMAGE_URLS):
        values = candidate.value if isinstance(candidate.value, list) else [candidate.value]
        for value in values:
            if isinstance(value, str):
                entries.append((candidate.tier.value, sequence, value))
                sequence += 1

    for candidate in bundle.for_field(VARIANTS):
        for value in getattr(candidate.value, "image_urls", []) or []:
            if isinstance(value, str):
                entries.append((candidate.tier.value, sequence, value))
                sequence += 1

    entries.sort(key=lambda entry: (entry[0], entry[1]))
    return [url for _, _, url in entries]


def _from_dom(soup: BeautifulSoup) -> list[str]:
    """Images found by walking the HTML itself, for pages that declared none.

    Skips anything inside navigation, headers and footers, since those are the site's
    furniture rather than the product.
    """
    found: list[str] = []

    # A preload hint is the page telling the browser which image matters most.
    for link in soup.find_all("link"):
        rel = link.get("rel") or []
        rels = {rel.lower()} if isinstance(rel, str) else {item.lower() for item in rel}
        if "preload" not in rels or (link.get("as") or "").lower() != "image":
            continue
        # link[rel=preload] carries its own responsive list attribute.
        widest = _widest_srcset(link.get("imagesrcset"))
        found.append(widest or link.get("href") or "")

    for element in soup.find_all("img"):
        if not isinstance(element, Tag) or _is_chrome(element) or _is_icon(element):
            continue

        # A <picture> offers art-directed alternatives; the widest still wins.
        from_responsive = False
        parent = element.find_parent("picture")
        if parent is not None:
            for source in parent.find_all("source"):
                widest = _widest_srcset(source.get("srcset"))
                if widest:
                    found.append(widest)
                    from_responsive = True

        for attr in ("srcset", *_LAZY_SRCSET_ATTRS):
            widest = _widest_srcset(element.get(attr))
            if widest:
                found.append(widest)
                from_responsive = True

        # src is the fallback for clients that cannot read a responsive list, so
        # taking both would re-add the default-size rendition under another filename.
        if from_responsive:
            continue

        for attr in ("src", *_LAZY_ATTRS):
            value = element.get(attr)
            if isinstance(value, str) and value.strip():
                found.append(value)
                break

    return [url for url in found if url]


def _is_chrome(element: Tag) -> bool:
    """True if this image sits in navigation, a header, a footer or a sidebar."""
    for parent in element.parents:
        if parent.name in _CHROME_ANCESTORS:
            return True
        role = parent.get("role")
        if isinstance(role, str) and role.lower() in _CHROME_ROLES:
            return True
    return False


def _is_icon(element: Tag) -> bool:
    """True if the markup's own width/height attributes say this is icon-sized."""
    for attr in ("width", "height"):
        raw = element.get(attr)
        if not isinstance(raw, str):
            continue
        digits = re.match(r"^\s*(\d+)", raw)
        if digits and int(digits.group(1)) < _MIN_DECLARED_PIXELS:
            return True
    return False


def _widest_srcset(srcset: str | None) -> str | None:
    """Pick the biggest image from a srcset, which lists one image at several widths.

    Follows the spec's grammar instead of just splitting on commas, because a CDN URL
    with resize instructions in it contains commas of its own.
    https://html.spec.whatwg.org/multipage/images.html#srcset-attributes
    """
    if not isinstance(srcset, str) or not srcset.strip():
        return None

    entries: list[tuple[str, str | None]] = []
    pending: str | None = None

    for token in srcset.split():
        if pending is not None and _SRCSET_DESCRIPTOR.match(token):
            entries.append((pending, token.rstrip(",")))
            pending = None
            continue
        if pending is not None:
            entries.append((pending, None))
        pending = token.rstrip(",")
        if token.endswith(","):
            entries.append((pending, None))
            pending = None
    if pending is not None:
        entries.append((pending, None))

    if not entries:
        return None

    # The spec forbids mixing w and x descriptors in one attribute, so whichever
    # is present is the one to compare on.
    for suffix in ("w", "x"):
        scored = [
            (float(descriptor[:-1]), url)
            for url, descriptor in entries
            if descriptor and descriptor.endswith(suffix)
        ]
        if scored:
            return max(scored, key=lambda item: item[0])[1]

    return entries[0][0]


def normalize(url: str, base: str | None = None) -> str | None:
    """Turn one image URL into a full-size absolute URL, or None if it isn't usable.

    Strips the parts of a URL that request a smaller version, so the CDN returns its
    original. Relative URLs are completed using the page's own address.
    """
    if not isinstance(url, str):
        return None
    candidate = url.strip()
    if not candidate:
        return None

    scheme = candidate.split(":", 1)[0].lower() if ":" in candidate else ""
    if scheme in _REJECTED_SCHEMES:
        return None

    # A protocol-relative URL is absolute apart from the scheme. https rather
    # than http because a mixed-content image is blocked by every modern browser.
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    elif not scheme:
        if not base:
            # Nothing to resolve against; a relative path alone is not fetchable.
            return None
        candidate = urljoin(base, candidate)

    parts = urlparse(candidate)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None

    if not _is_media(parts.netloc, parts.path):
        return None

    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    if _is_tracking_pixel(query_pairs):
        return None

    kept = [
        (key, value)
        for key, value in query_pairs
        if key.lower() in _PRESERVED_PARAMS
        or key.lower() not in _DIMENSION_PARAMS | _AUXILIARY_PARAMS
    ]

    path = _strip_transform_segments(parts.path)

    return urlunparse(
        (parts.scheme, parts.netloc, path, parts.params, urlencode(kept), "")
    )


def _is_media(host: str, path: str) -> bool:
    """True if this URL looks like a real photo rather than an icon or a tracker.

    Checks the file extension, the filename, the path, and the host. Words like "logo"
    only count as whole words, so "ace_logo.png" is rejected but "flagship" is not.
    """
    lowered_host = host.lower()
    if any(hint in lowered_host for hint in _NON_MEDIA_HOST_HINTS):
        return False

    lowered_path = path.lower()
    if any(hint in lowered_path for hint in _NON_MEDIA_PATH_HINTS):
        return False

    segments = [segment for segment in lowered_path.split("/") if segment]
    if set(segments[:-1]) & _CHROME_PATH_SEGMENTS:
        return False

    filename = lowered_path.rsplit("/", 1)[-1]
    if any(filename.endswith(extension) for extension in _REJECTED_EXTENSIONS):
        return False
    if any(fragment in filename for fragment in _FILENAME_SUBSTRINGS):
        return False
    if set(_FILENAME_TOKEN_SPLIT.split(filename)) & _FILENAME_TOKENS:
        return False

    return True


def _dedupe_key(url: str) -> str:
    """A key for spotting duplicates, ignoring the requested file format.

    One photo offered as both webp and jpeg is one photo, not two gallery entries.
    """
    parts = urlparse(url)
    pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _PRESERVED_PARAMS
    ]
    return urlunparse(
        (parts.scheme, parts.netloc, parts.path, parts.params, urlencode(pairs), "")
    )


def _is_tracking_pixel(query_pairs: list[tuple[str, str]]) -> bool:
    """True if the URL asks for a 1x1 image, which is an analytics beacon."""
    dimensions = []
    for key, value in query_pairs:
        if key.lower() in {"w", "h", "width", "height", "wid", "hei"}:
            try:
                dimensions.append(float(value))
            except ValueError:
                continue
    return bool(dimensions) and all(value <= 2 for value in dimensions)


def _strip_transform_segments(path: str) -> str:
    """Remove resize instructions built into a URL path, like "/w_640,c_limit/".

    Only that directive shape is removed. A plain "800x600" segment is left alone,
    because it is just as often the folder holding the original, and removing it turns
    a working URL into a 404.
    """
    segments = path.split("/")
    kept = [
        segment
        for segment in segments
        if not (segment and _TRANSFORM_SEGMENT.match(segment))
    ]
    # Never strip the filename itself, however directive-shaped it looks.
    if kept and segments and kept[-1] != segments[-1]:
        kept.append(segments[-1])
    return "/".join(kept) or "/"
