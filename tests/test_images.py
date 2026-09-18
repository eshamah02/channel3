"""Image harvesting and normalisation.

Several assertions here encode results measured against the live CDNs during
development (see PLAN.md task 5): where a rule's correctness depended on whether
a real URL still resolved, the observed status code and pixel dimensions are
recorded in the comment so the choice is reviewable rather than asserted.
"""

from pathlib import Path

import pytest

from extraction import images
from extraction.layers import jsonld
from extraction.candidates import IMAGE_URLS, VARIANTS, CandidateBundle, Tier
from extraction.dom import canonical_url, parse
from extraction.pipeline import build_bundle
from models import Variant, VariantAttribute

FIXTURES = Path(__file__).parent / "fixtures"

BASE = "https://shop.test/products/gallery-widget"


def load(name: str):
    return parse((FIXTURES / name).read_text())


def gallery() -> list[str]:
    html = (FIXTURES / "images_gallery.html").read_text()
    bundle, soup = build_bundle(html)
    return images.harvest(soup, bundle, BASE)


# --- normalize: scheme and resolution --------------------------------------


def test_protocol_relative_becomes_https():
    """// is absolute apart from the scheme; https because mixed content is blocked."""
    assert (
        images.normalize("//cdn.test/a.jpg", BASE) == "https://cdn.test/a.jpg"
    )


def test_relative_resolves_against_the_canonical_url():
    assert (
        images.normalize("/products/a.jpg", BASE)
        == "https://shop.test/products/a.jpg"
    )


def test_document_relative_path_resolves_against_the_base_directory():
    assert (
        images.normalize("thumb/a.jpg", BASE)
        == "https://shop.test/products/thumb/a.jpg"
    )


def test_relative_without_a_base_is_unusable():
    """A page with no canonical URL and no og:url leaves nothing to resolve against."""
    assert images.normalize("/products/a.jpg", None) is None


@pytest.mark.parametrize(
    "url",
    [
        "data:image/gif;base64,R0lGODlhAQABAAAAACw=",
        "blob:https://shop.test/9f8c",
        "javascript:void(0)",
        "about:blank",
        "",
        "   ",
    ],
)
def test_unusable_urls_are_rejected(url):
    assert images.normalize(url, BASE) is None


def test_non_http_scheme_is_rejected():
    assert images.normalize("ftp://cdn.test/a.jpg", BASE) is None


# --- normalize: rendition parameters ---------------------------------------


def test_dimension_and_quality_params_are_stripped_format_is_kept():
    """Measured: article 1200x1200 -> 2890x2890 (5.8x pixel area) after stripping."""
    assert (
        images.normalize("https://cdn.test/a.jpg?w=200&q=80&fmt=auto", BASE)
        == "https://cdn.test/a.jpg?fmt=auto"
    )


def test_scene7_wid_hei_are_stripped():
    """Adobe Scene7 spells them wid/hei rather than width/height.

    Measured on a live Scene7 host: the page requested 950x1095 and the master is
    867x1000, so the request was upscaling. The unparameterised URL is the true
    original even though it is fewer pixels and fewer bytes.
    """
    assert (
        images.normalize("https://cdn.test/is/image/x?hei=1095&wid=950", BASE)
        == "https://cdn.test/is/image/x"
    )


def test_rendering_hints_are_stripped():
    """resMode and defaultImage change how it renders, not which image it is."""
    assert (
        images.normalize(
            "https://cdn.test/is/image/x?resMode=sharp2&defaultImage=fallback", BASE
        )
        == "https://cdn.test/is/image/x"
    )


def test_identifying_params_survive():
    """A cache buster or asset id is part of the address, not a size request."""
    result = images.normalize("https://cdn.test/a.jpg?_cb=123&id=77&w=50", BASE)
    assert "_cb=123" in result and "id=77" in result and "w=50" not in result


def test_one_by_one_requests_are_rejected_as_beacons():
    assert images.normalize("https://cdn.test/t.gif?w=1&h=1", BASE) is None


# --- normalize: path transforms -------------------------------------------


def test_cloudinary_style_transform_segment_is_removed():
    """Measured: 400x400 -> 3144x3144, a 61.8x pixel-area gain, still HTTP 200."""
    url = (
        "https://cdn.test/a/images/t_default/"
        "u_9ddf04c7,c_scale,fl_relative,w_1.0,h_1.0,fl_layer_apply/"
        "abc-123/NAME.png"
    )
    assert images.normalize(url, BASE) == "https://cdn.test/a/images/abc-123/NAME.png"


def test_dimension_shaped_path_segment_is_preserved():
    """Deliberate departure from the original plan.

    The plan called for stripping segments matching NNNxNNN as thumbnail
    transforms. Measured against a live CDN, that segment was the directory
    holding the native-resolution original: removing it returned HTTP 404 while
    keeping it returned a 2890x2890 image. A working smaller image would beat a
    broken larger one; here keeping it is also the larger image.
    """
    url = "https://cdn.test/products/SKU1/2890x1500/image1.jpg"
    assert images.normalize(url, BASE) == url


def test_a_directive_shaped_filename_is_not_stripped():
    """The last segment is the file; it stays however transform-like it looks."""
    url = "https://cdn.test/images/w_640,c_limit"
    assert images.normalize(url, BASE) == url


# --- normalize: furniture --------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.test/ui/chevron.svg",
        "https://cdn.test/favicon.ico",
        "https://cdn.test/brand/ace_logo.png",
        "https://cdn.test/ui/icon.png",
        "https://cdn.test/ui/sprite-sheet.png",
        "https://cdn.test/ui/loading-large.gif",
        "https://cdn.test/_mzblank.gif",
        "https://cdn.test/img/placeholder.jpg",
        "https://analytics.test/api/view/product?id=1",
        "https://cdn.test/api/collect?e=view",
    ],
)
def test_interface_furniture_and_beacons_are_rejected(url):
    assert images.normalize(url, BASE) is None


def test_a_product_name_containing_a_risky_word_survives():
    """"flag" only counts as a whole word: flagship is a product, not a flag icon."""
    assert images.normalize("https://cdn.test/products/flagship-tee.jpg", BASE) is not None


# --- srcset ---------------------------------------------------------------


def test_widest_w_descriptor_wins():
    assert (
        images._widest_srcset("https://c.test/a-320.jpg 320w, https://c.test/a-2000.jpg 2000w")
        == "https://c.test/a-2000.jpg"
    )


def test_widest_x_descriptor_wins():
    assert (
        images._widest_srcset("https://c.test/a.jpg 1x, https://c.test/a3.jpg 3x")
        == "https://c.test/a3.jpg"
    )


def test_srcset_with_commas_inside_urls_is_parsed_correctly():
    """Transformation-bearing CDN URLs contain commas; splitting on commas corrupts them."""
    srcset = (
        "https://c.test/a/w_320,c_limit/x.jpg 320w, "
        "https://c.test/a/w_1600,c_limit/x.jpg 1600w"
    )
    assert images._widest_srcset(srcset) == "https://c.test/a/w_1600,c_limit/x.jpg"


def test_srcset_without_descriptors_takes_the_first():
    assert images._widest_srcset("https://c.test/only.jpg") == "https://c.test/only.jpg"


def test_empty_srcset_yields_nothing():
    assert images._widest_srcset(None) is None
    assert images._widest_srcset("   ") is None


# --- harvest --------------------------------------------------------------


def test_declared_images_come_first():
    """The canonical gallery leads, because that is what a PDP shows first."""
    urls = gallery()
    assert urls[0] == "https://cdn.test/products/hero.jpg"
    assert urls[1] == "https://cdn.test/products/second.jpg"


def test_harvest_collects_from_every_dom_source():
    urls = gallery()
    joined = " ".join(urls)
    assert "preload-1600.jpg" in joined  # link[rel=preload][imagesrcset], widest
    assert "art-2000.jpg" in joined  # picture > source[srcset], widest
    assert "retina-3x.jpg" in joined  # img[srcset], widest x
    assert "lazy.jpg" in joined  # img[data-src]
    assert "shop.test/products/relative.jpg" in joined  # resolved against canonical


def test_harvest_prefers_the_widest_and_drops_the_narrow_sibling():
    urls = gallery()
    assert not any("art-320" in url for url in urls)
    assert not any("preload-400" in url for url in urls)
    assert not any("retina-1x" in url for url in urls)


def test_size_only_variants_collapse():
    """hero.jpg?w=800&q=70 and hero.jpg?w=200&fmt=auto are one photograph."""
    urls = gallery()
    assert sum(1 for url in urls if "hero.jpg" in url) == 1


def test_format_variants_collapse():
    """The same image offered as webp and jpeg is one gallery entry."""
    soup = parse(
        '<img src="https://cdn.test/p/a.jpg?fm=webp">'
        '<img src="https://cdn.test/p/a.jpg">'
    )
    assert len(images.harvest(soup, None, BASE)) == 1


def test_harvest_rejects_furniture_from_the_dom():
    urls = gallery()
    joined = " ".join(urls)
    for rejected in (
        "megamenu-banner",  # inside <nav>
        "newsletter",  # inside <footer>
        "chevron.svg",
        "ace_logo",
        "thumb.jpg",  # width/height declare an icon
        "t.gif",
        "data:",
    ):
        assert rejected not in joined, rejected


def test_off_host_images_are_dropped_when_the_page_declares_its_own():
    """Review snapshots and ad creative are real images, but not this product.

    Nothing about the filename or the size distinguishes them; the host that
    serves the declared gallery is the signal.
    """
    urls = gallery()
    assert not any("reviews.test" in url for url in urls)


def test_all_dom_images_are_kept_when_nothing_is_declared():
    """With no anchor there is nothing to compare against, so keep what was found."""
    soup = parse(
        '<main><img src="https://a.test/p/one.jpg"><img src="https://b.test/p/two.jpg"></main>'
    )
    urls = images.harvest(soup, None, BASE)
    assert len(urls) == 2


def test_variant_images_are_collected():
    """A ProductGroup often has no image of its own and hangs it off each variant."""
    bundle = CandidateBundle()
    bundle.add(
        VARIANTS,
        Variant(
            option_values=[VariantAttribute(name="Size", value="M")],
            image_urls=["https://cdn.test/p/variant.jpg"],
        ),
        "JSON-LD hasVariant",
        Tier.A,
    )
    soup = parse("<html><body></body></html>")
    assert images.harvest(soup, bundle, BASE) == ["https://cdn.test/p/variant.jpg"]


def test_tier_a_variant_images_outrank_tier_b_meta_images():
    soup = parse(
        '<html><head><meta property="og:image" content="https://cdn.test/p/og.jpg">'
        "</head><body></body></html>"
    )
    bundle = CandidateBundle()
    bundle.add(IMAGE_URLS, "https://cdn.test/p/og.jpg", "meta og:image", Tier.B)
    bundle.add(
        VARIANTS,
        Variant(
            option_values=[VariantAttribute(name="Size", value="M")],
            image_urls=["https://cdn.test/p/variant.jpg"],
        ),
        "JSON-LD hasVariant",
        Tier.A,
    )
    assert images.harvest(soup, bundle, BASE)[0] == "https://cdn.test/p/variant.jpg"


def test_harvest_is_capped():
    body = "".join(
        f'<img src="https://cdn.test/p/{index}.jpg">' for index in range(60)
    )
    soup = parse(f"<main>{body}</main>")
    assert len(images.harvest(soup, None, BASE)) == images._MAX_IMAGES


def test_harvest_falls_back_to_the_canonical_url_for_a_base():
    soup = load("images_gallery.html")
    bundle = CandidateBundle()
    jsonld.extract(soup, bundle)
    urls = images.harvest(soup, bundle)  # no base passed
    assert any("shop.test/products/relative.jpg" in url for url in urls)


def test_harvest_never_raises_on_a_page_with_no_images():
    soup = parse("<html><body><p>No images here.</p></body></html>")
    assert images.harvest(soup, None, BASE) == []


def test_harvest_output_is_order_stable():
    assert gallery() == gallery()
