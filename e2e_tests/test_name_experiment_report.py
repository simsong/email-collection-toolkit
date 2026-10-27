# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Exercise the private research review report with entirely synthetic identities.
# Fifty proposals are generated from fixed candidate and score fixtures.
# Browser storage is deliberately reordered to detect labels attached by position.
# Downloaded decisions must retain the displayed stable address-pair identities.
# Nothing is sent to a service and no real archive content is rendered.
# This protects the owner-labeling path used for later accuracy measurement.
"""Research requirement: stable local owner-review labels and usable report export."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from playwright.sync_api import Page, expect

from scripts.name_matcher.experiments import RunSummary, Timing, render_report, save_pairs
from scripts.name_matcher.linkage import candidates, synthetic_profiles


def test_owner_labels_follow_pair_identity_after_cached_rows_are_reordered(tmp_path: Path,page: Page) -> None:
    profiles=synthetic_profiles(501,40)
    pairs,blocking=candidates(profiles)
    fingerprint=save_pairs(tmp_path/'experiments.sqlite3',pairs)
    with sqlite3.connect(tmp_path/'experiments.sqlite3') as database:
        database.executemany('INSERT INTO scores VALUES(?,?,?)',(('fixture engine',index,1.0) for index,_pair in enumerate(pairs)))
    timing=Timing(wall_seconds=0,cpu_seconds=0)
    summary=RunSummary(created_at='2026-10-21T12:00:00+00:00',archive='synthetic-fixture-only',messages=100,extraction_errors=0,
        extraction_seconds=0,profiles=len(profiles),profiles_with_names=len(profiles),frozen_candidates=len(pairs),
        candidate_sha256=fingerprint,preparation=timing,blocking=timing,block_summary=blocking)
    render_report(summary,profiles,tmp_path)
    page.goto((tmp_path/'report.html').as_uri())
    expect(page.locator('[data-review]')).to_have_count(50)
    expect(page.locator('[data-not-person]')).to_have_count(50)
    page.locator('#show-non-person').check()
    # The prior report stored this exact seed shape and used an exclusive non-person label.
    legacy_seed=json.loads((tmp_path/'review-50.json').read_text())
    legacy_hash=hashlib.sha256((summary.archive+summary.created_at+json.dumps(legacy_seed,sort_keys=True)).encode()).hexdigest()
    assert page.evaluate('key')==f'name-review-{fingerprint[:16]}-{legacy_hash[:16]}'
    page.evaluate("""() => {
        const legacy=initial.map(row=>({...row}));
        legacy[0].label='match';legacy[0].notes='Independently checked first displayed pair';
        legacy[1].label='not-person';legacy[1].notes='B is an organization, not an individual';
        legacy[2].notes='Existing unlabeled comment: not a person';
        localStorage.setItem(key,JSON.stringify(legacy));
    }""")
    previous_key=page.evaluate('key')
    render_report(summary,profiles,tmp_path)
    page.reload()
    assert page.evaluate('key')==previous_key
    expect(page.locator('[data-review="0"]')).to_have_value('match')
    expect(page.locator('[data-note="0"]')).to_have_value('Independently checked first displayed pair')
    expect(page.locator('[data-note="2"]')).to_have_value('Existing unlabeled comment: not a person')
    expect(page.locator('[data-review="1"]')).to_have_value('')
    expect(page.locator('[data-not-person="1"]')).to_be_checked()
    # Pair ten can match while being a non-person identity; both judgments persist.
    page.locator('[data-review="9"]').select_option('match')
    page.locator('[data-not-person="9"]').check()
    page.locator('[data-note="9"]').fill('Both are the same organization')
    selected=page.evaluate('rows[0].candidate_id')
    page.evaluate('localStorage.setItem(key,JSON.stringify([...rows].reverse()))')
    page.reload()
    expect(page.locator('[data-review="0"]')).to_have_value('match')
    assert page.evaluate('rows[0].candidate_id')==selected
    expect(page.locator('[data-note="0"]')).to_have_value('Independently checked first displayed pair')
    expect(page.locator('[data-review="1"]')).to_have_value('')
    expect(page.locator('[data-not-person="1"]')).to_be_checked()
    expect(page.locator('[data-note="1"]')).to_have_value('B is an organization, not an individual')
    expect(page.locator('[data-review="2"]')).to_have_value('')
    expect(page.locator('[data-note="2"]')).to_have_value('Existing unlabeled comment: not a person')
    expect(page.locator('[data-review="9"]')).to_have_value('match')
    expect(page.locator('[data-not-person="9"]')).to_be_checked()
    expect(page.locator('[data-note="9"]')).to_have_value('Both are the same organization')
    expect(page.locator('#progress')).to_have_text('2 / 50 identity decisions · 2 non-person flags')
    page.locator('#show-non-person').uncheck()
    expect(page.locator('[data-review-row="9"]')).to_be_hidden()
    page.locator('#show-non-person').check()
    expect(page.locator('[data-review="9"]')).to_have_value('match')
    expect(page.locator('[data-not-person="9"]')).to_be_checked()
    with page.expect_download() as download:
        page.locator('#export').click()
    path=download.value.path()
    assert path is not None
    labels=json.loads(path.read_text())
    assert labels[0]['candidate_id']==selected and labels[0]['label']=='match'
    assert labels[0]['not_person'] is False
    assert labels[1]['label']=='' and labels[1]['not_person'] is True
    assert labels[1]['notes']=='B is an organization, not an individual'
    assert labels[2]['label']=='' and labels[2]['notes']=='Existing unlabeled comment: not a person'
    assert labels[9]['label']=='match' and labels[9]['not_person'] is True
    assert labels[9]['notes']=='Both are the same organization'
    assert sum(bool(label['label']) for label in labels)==2
    # A report regenerated under another source mapping cannot inherit those labels.
    summary.archive='another-synthetic-fixture'
    render_report(summary,profiles,tmp_path)
    page.reload()
    expect(page.locator('[data-review="0"]')).to_have_value('')
    expect(page.locator('[data-not-person="9"]')).not_to_be_checked()
