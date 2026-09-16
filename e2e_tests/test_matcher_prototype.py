# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirements: synthetic matcher window in doc/requirements.md.

Exercise the actual browser UI and its session-only model, with no archive,
API substitutions, or authoritative-matcher claims.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

GUI = Path(__file__).resolve().parents[1] / "gui"
WIDTH = "width"
HEIGHT = "height"


@pytest.fixture(autouse=True)
def open_prototype(page: Page) -> Iterator[None]:
    """Each test starts from the same standalone synthetic document."""
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({WIDTH: 1120, HEIGHT: 750})
    page.goto((GUI / "matcher.html").as_uri())
    expect(page.locator("tr.group")).to_have_count(8)
    yield
    assert not errors, errors


def test_disclosure_and_independent_address_filters(page: Page) -> None:
    """Mailbox/domain filters AND on one address; full-email search is absent."""
    expect(page.locator("tr.address")).to_have_count(3)
    page.get_by_role("button", name="Collapse Sam L. Green", exact=True).click()
    expect(page.locator("tr.address")).to_have_count(0)
    page.get_by_role("button", name="Expand Sam L. Green", exact=True).click()
    expect(page.locator("tr.address").first).to_contain_text("2008-04-12")
    expect(page.locator("tr.address").first).to_contain_text("2026-08-30")
    expect(page.locator("tr.address").first).to_contain_text("1,842")
    expect(page.locator("#email-filter")).to_have_count(0)
    page.locator("#domain-filter").fill("SLG")
    expect(page.locator("tr.address")).to_have_count(1)
    expect(page.locator("tr.address")).to_contain_text("priya@slg-research.test")
    page.get_by_role("button", name="Clear", exact=True).click()
    page.locator("#mailbox-filter").fill("SLG")
    expect(page.locator("tr.address")).to_have_count(2)
    page.locator("#domain-filter").fill("HARBOR")
    expect(page.locator("tr.address")).to_have_count(1)
    expect(page.locator("tr.address")).to_contain_text("slg@harbor-lab.test")
    page.get_by_role("button", name="Clear", exact=True).click()
    page.locator("#mailbox-filter").fill("slg")
    page.locator("#domain-filter").fill("northstar")
    expect(page.locator("tr.group")).to_have_count(0)
    expect(page.get_by_text("No matching addresses", exact=True)).to_be_visible()


def test_drag_group_preserves_target_and_all_child_statistics(page: Page) -> None:
    """Dropping a canonical row moves every child, even children hidden by a filter."""
    page.locator("#domain-filter").fill("northstar")
    page.locator('tr.group[data-group-id="sam"]').drag_to(page.locator('tr.group[data-group-id="alex"]'))
    expect(page.locator('tr.group[data-group-id="sam"]')).to_have_count(0)
    expect(page.locator('tr.group[data-group-id="alex"] .member-count')).to_have_text("2/5")
    expect(page.locator("#status")).to_contain_text("Moved 3 addresses to Alex Morgan")
    page.get_by_role("button", name="Clear", exact=True).click()
    expect(page.locator('tr.address[data-group-id="alex"]')).to_have_count(5)
    moved = page.locator('tr[data-address-id="sam-slg"]')
    expect(moved).to_contain_text("1996-02-18")
    expect(moved).to_contain_text("2014-11-06")
    expect(moved).to_contain_text("763")
    expect(page.locator("#summary")).to_have_text("7 canonical entries · 12 of 12 addresses")
    page.get_by_role("button", name="Undo", exact=True).click()
    expect(page.locator("tr.group")).to_have_count(8)
    expect(page.locator('tr.address[data-group-id="sam"]')).to_have_count(3)


def test_drag_child_move_to_hidden_group_and_separate(page: Page) -> None:
    """A child move changes only membership; picker supports a filtered-out destination."""
    page.locator('tr[data-address-id="sam-slg"]').drag_to(page.locator('tr.group[data-group-id="alex"]'))
    expect(page.locator('tr.address[data-group-id="sam"]')).to_have_count(2)
    expect(page.locator('tr.address[data-group-id="alex"]')).to_have_count(3)
    page.locator("#mailbox-filter").fill("slg")
    page.locator("#domain-filter").fill("cedar")
    page.get_by_role("button", name="slg@cedar.test", exact=True).click()
    page.get_by_label("Move to", exact=True).select_option("priya")
    page.get_by_role("button", name="Move", exact=True).click()
    expect(page.locator('tr.group[data-group-id="priya"]')).to_be_visible()
    expect(page.locator('tr.group[data-group-id="priya"] .member-count')).to_have_text("1/3")
    page.get_by_role("button", name="slg@cedar.test", exact=True).click()
    page.get_by_role("button", name="Make separate", exact=True).click()
    expect(page.locator("tr.group .row-label")).to_have_text("slg@cedar.test")
    page.get_by_role("button", name="Undo", exact=True).click()
    expect(page.locator('tr.group[data-group-id="priya"]')).to_be_visible()
    page.get_by_role("button", name="Reset demo", exact=True).click()
    expect(page.locator("tr.group")).to_have_count(8)
    expect(page.locator('tr.address[data-group-id="sam"]')).to_have_count(3)
    page.get_by_role("button", name="Undo", exact=True).click()
    page.get_by_role("button", name="Expand Priya Shah", exact=True).click()
    expect(page.locator('tr[data-address-id="sam-slg"]')).to_have_attribute("data-group-id", "priya")


def test_noop_moves_and_matcher_batch_undo(page: Page) -> None:
    """Self drops do nothing; predefined matches are one reversible batch and idempotent."""
    page.locator('tr[data-address-id="sam-slg"]').drag_to(page.locator('tr.group[data-group-id="sam"]'))
    expect(page.get_by_role("button", name="Undo", exact=True)).to_be_disabled()
    page.get_by_role("button", name="Run authoritative matcher", exact=True).click()
    expect(page.locator("tr.group")).to_have_count(6)
    expect(page.locator('tr.address[data-group-id="sam"]')).to_have_count(4)
    expect(page.locator('tr.address[data-group-id="alex"]')).to_have_count(3)
    expect(page.locator("#status")).to_contain_text("Applied 2 predefined demo matches")
    page.get_by_role("button", name="Run authoritative matcher", exact=True).click()
    expect(page.locator("#status")).to_contain_text("No remaining demo matches")
    page.get_by_role("button", name="Undo", exact=True).click()
    expect(page.locator("tr.group")).to_have_count(8)
    expect(page.get_by_role("button", name="Undo", exact=True)).to_be_disabled()
    page.reload()
    expect(page.locator("tr.group")).to_have_count(8)


def test_minimum_window_and_keyboard_controls(page: Page, tmp_path: Path) -> None:
    """The minimum window keeps matrix, actions and status usable, including keyboard moves."""
    preview = GUI.parent / ".tmp" / "matcher-preview"
    preview.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(preview / "matcher.png"))
    page.set_viewport_size({WIDTH: 780, HEIGHT: 600})
    toggle = page.get_by_role("button", name="Collapse Sam L. Green", exact=True)
    toggle.focus()
    page.keyboard.press("Enter")
    expect(page.locator("tr.address")).to_have_count(0)
    page.keyboard.press("Enter")
    source = page.get_by_role("button", name="slg@cedar.test", exact=True)
    source.focus()
    page.keyboard.press("Enter")
    page.get_by_label("Move to", exact=True).select_option("alex")
    page.get_by_role("button", name="Move", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.locator('tr[data-address-id="sam-slg"]')).to_have_attribute("data-group-id", "alex")
    assert page.locator("body").evaluate("el => el.scrollWidth <= innerWidth")
    assert page.locator("#matcher").evaluate("el => el.scrollHeight <= innerHeight")
    page.screenshot(path=str(tmp_path / "matcher-minimum.png"))
    page.screenshot(path=str(preview / "matcher-minimum.png"))


def test_inclusive_date_bounds_recompute_statistics(page: Page) -> None:
    """Bound actual dated observations, not merely overlap between first/last use."""
    expect(page.locator(".date-placeholder").first).to_be_visible()
    page.locator("#mailbox-filter").fill("slg")
    page.locator("#domain-filter").fill("cedar")
    address = page.locator('tr[data-address-id="sam-slg"]')
    expect(address.locator("td").nth(3)).to_have_text("763")
    page.get_by_label("Start date", exact=True).fill("2014-11-06")
    expect(page.locator(".date-placeholder").first).to_be_hidden()
    expect(address.locator("td").nth(1)).to_have_text("2014-11-06")
    expect(address.locator("td").nth(3)).to_have_text("1")
    page.get_by_label("End date", exact=True).fill("2014-11-06")
    expect(address.locator("td").nth(3)).to_have_text("1")
    page.get_by_label("End date", exact=True).fill("2014-11-05")
    expect(page.get_by_role("alert")).to_be_visible()
    expect(page.locator("tr.address")).to_have_count(0)
    page.get_by_label("Start date", exact=True).fill("")
    page.get_by_label("End date", exact=True).fill("1996-02-18")
    expect(address.locator("td").nth(2)).to_have_text("1996-02-18")
    expect(address.locator("td").nth(3)).to_have_text("1")
    page.get_by_role("button", name="slg@cedar.test", exact=True).click()
    page.get_by_label("Move to", exact=True).select_option("alex")
    page.get_by_role("button", name="Move", exact=True).click()
    page.get_by_label("End date", exact=True).fill("1996-02-17")
    expect(page.locator("tr.address")).to_have_count(0)
    page.get_by_label("End date", exact=True).fill("")
    expect(address.locator("td").nth(3)).to_have_text("763")
    expect(address).to_have_attribute("data-group-id", "alex")


def test_headers_sort_hierarchy_and_deduplicate_group_counts(page: Page) -> None:
    """Numeric/date/text order toggles without detaching children or double-counting shared mail."""
    page.get_by_role("button", name="Clear", exact=True).click()
    sam = page.locator('tr.group[data-group-id="sam"]')
    expect(sam.locator("td").nth(3)).to_have_text("3,030")
    page.get_by_role("button", name="Messages", exact=True).click()
    expect(page.locator("tr.group").first).to_have_attribute("data-group-id", "sam")
    assert page.locator('tr.address[data-group-id="sam"] td.number').all_text_contents() == ["1,842", "763", "426"]
    page.get_by_role("button", name="Messages", exact=True).click()
    expect(page.locator("tr.group").first).to_have_attribute("data-group-id", "alex-old")
    assert page.locator('tr.address[data-group-id="sam"] td.number').all_text_contents() == ["426", "763", "1,842"]
    expect(page.locator('th[aria-sort="ascending"]')).to_contain_text("Messages")
    page.get_by_role("button", name="First use", exact=True).click()
    expect(page.locator("tr.group").first).to_have_attribute("data-group-id", "sam")
    page.get_by_role("button", name="Last use", exact=True).click()
    expect(page.locator("tr.group").first).to_have_attribute("data-group-id", "alex-old")
    page.get_by_role("button", name="Canonical name / email address", exact=True).click()
    expect(page.locator("tr.group .row-label").first).to_have_text("A. Morgan")
    page.get_by_role("button", name="Canonical name / email address", exact=True).click()
    expect(page.locator("tr.group .row-label").first).to_have_text("Sam L. Green")
    page.locator("#start-date").fill("2026-08-30")
    page.get_by_role("button", name="Messages", exact=True).click()
    expect(page.locator("tr.group")).to_have_count(1)
    expect(page.locator('th[aria-sort="descending"]')).to_contain_text("Messages")


def test_institution_subclass_uses_same_controls_with_independent_groups(page: Page) -> None:
    """Institutions disclose their addresses and merge aliases without changing name groups."""
    page.goto((GUI / "matcher.html").as_uri() + "?kind=institution")
    expect(page.get_by_role("heading", name="Institution matcher", exact=True)).to_be_visible()
    expect(page.locator("tr.group")).to_have_count(6)
    expect(page.locator('tr.address[data-group-id="northstar"]')).to_have_count(3)
    page.locator("#domain-filter").fill("harbor")
    expect(page.locator("tr.group")).to_have_count(2)
    page.get_by_role("button", name="Run institution matcher", exact=True).click()
    expect(page.locator("tr.group .row-label")).to_have_text("Harbor Laboratory")
    expect(page.locator('tr.address[data-group-id="harbor"]')).to_have_count(3)
    page.get_by_role("button", name="Undo", exact=True).click()
    expect(page.locator("tr.group")).to_have_count(2)
    page.get_by_role("button", name="Clear", exact=True).click()
    preview = GUI.parent / ".tmp" / "matcher-preview"
    preview.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(preview / "institution.png"))
    page.goto((GUI / "matcher.html").as_uri())
    expect(page.locator("tr.group")).to_have_count(8)
    expect(page.get_by_role("button", name="Undo", exact=True)).to_be_disabled()
