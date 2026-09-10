"""Exercise the actual website header and stylesheet at narrow viewport widths."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from playwright.sync_api import Page, expect

ROOT = Path(__file__).parents[1]
WIDTH = "width"
HEIGHT = "height"
X = "x"


@pytest.mark.parametrize("width", [390, 600, 800, 1280])
def test_navigation_remains_visible_and_within_viewport(page: Page, width: int) -> None:
    """Requirement: adding or reordering navigation cannot hide mobile access to setup."""
    template = ROOT / "website/themes/envelope-rainbow/templates/base.html"
    header = BeautifulSoup(template.read_text(encoding="utf-8"), "html.parser").find("header")
    assert header is not None
    page.set_viewport_size({WIDTH: width, HEIGHT: 900})
    page.set_content(str(header))
    page.add_style_tag(content=(ROOT / "website/static/styles.css").read_text(encoding="utf-8"))
    if width <= 650:
        bounds = page.locator("header").bounding_box()
        assert bounds is not None
        assert bounds[X] == pytest.approx(15, abs=0.5)
        assert bounds[WIDTH] == pytest.approx(width - 30, abs=0.5)
    for _ in range(2):
        links = page.locator("nav a")
        expect(links).to_have_count(8)
        for link in links.all():
            expect(link).to_be_visible()
            bounds = link.bounding_box()
            assert bounds is not None
            assert 0 <= bounds[X] and bounds[X] + bounds[WIDTH] <= width
        expect(page.get_by_role("link", name="Gmail setup")).to_be_visible()
        # Exercise the regression after a real DOM reordering, not an index-string check.
        page.locator("nav").evaluate("(nav) => nav.prepend(nav.lastElementChild)")


def test_documentation_code_scrolls_within_mobile_page(page: Page) -> None:
    """Requirement: long configuration examples must not widen the mobile page."""
    page.set_viewport_size({WIDTH: 390, HEIGHT: 900})
    page.set_content(
        '<main class="page shell"><div class="prose"><pre><code>'
        'credential_ref: keyring://mail-archiver/personal-imap-account'
        '</code></pre></div></main>'
    )
    page.add_style_tag(content=(ROOT / "website/static/styles.css").read_text(encoding="utf-8"))
    assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
    assert page.locator("pre").evaluate("(block) => block.scrollWidth > block.clientWidth")
