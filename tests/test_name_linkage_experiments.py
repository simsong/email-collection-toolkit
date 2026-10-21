# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Validate candidate freezing and evaluation without private archive data.
# Synthetic identities declare independent labels, including difficult negatives.
# Missing blocked pairs count against recall and transitive false merges count.
# Optional native engine checks exercise their real public scoring interfaces.
# A miniature verified archive checks read-only profile preparation end to end.
# No source mailbox, network model, or mocked matcher is used by these tests.
"""Requirements: reproducible, source-safe and honestly labeled matcher experiments."""
from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest

from scripts.name_matcher.engines import fit_dedupe, fit_splink, score_dedupe, score_splink, splink_on_new_profiles
from scripts.name_matcher.experiments import prepare_profiles, validate_evidence
from scripts.name_matcher.linkage import Pair, Profile, candidates, fit_mixture, name_key, quality, synthetic_profiles
from scripts.name_matcher.build_observations import build_database
from tests.test_name_matcher_research import _one_message_archive
from mailarchiver.name_entities import classify_name, word_rules
from scripts.name_matcher.evaluate import evaluate
from scripts.name_matcher.experiments import save_pairs


@pytest.mark.parametrize('name,address,kind',[
    ('The SANS Institute','sans@example.test','organization'),
    ('Fidelity Investments','fidelity@example.test','organization'),
    ('Digital Research Labs','contact@example.test','organization'),
    ('Jane Laws','jane@example.test','unknown'),
    ('John Sears','john@example.test','person'),
    ('Mary Blue','mary@example.test','unknown'),
    ('Blue, Mary','mary@example.test','unknown'),
    ('Glass, John','john@example.test','unknown'),
    ('Zoë Martin','zoe@example.test','unknown'),
    ('王小明','wang@example.test','unknown'),
    ('X Æ A-12','x@example.test','unknown'),
    ('Person Name','reply+001853ac123456@reply.github.com','service'),
])
def test_sbook_codewords_and_ambiguous_person_names_stay_distinct(name: str,address: str,kind: str) -> None:
    """Conservative entity filter reuses real SBook sources, retaining unresolved and foreign names."""
    assert classify_name(name,address).kind==kind
    assert len(word_rules('parse_company.fp','P_COMPANY'))>300


@pytest.mark.skipif(importlib.util.find_spec('splink') is None,reason='requires isolated name-research environment')
def test_real_splink_name_frequency_favors_rare_exact_names() -> None:
    """Equal agreement on a rare full name carries more evidence than a common full name."""
    trained=fit_splink(synthetic_profiles(301,24),name_frequencies=True)
    profiles=[Profile(id=i,address=f'a{i}@test',name='rare name' if i<3 else 'common name',local='same',domain='same.test')
        for i in range(1,15)]
    pairs=[Pair(left=1,right=2,blocks=['test'],features=[0.0]*9),Pair(left=3,right=4,blocks=['test'],features=[0.0]*9)]
    rare,common=score_splink(splink_on_new_profiles(trained,profiles),pairs)
    assert rare>common


def test_owner_evaluation_validates_pair_mapping_and_separates_non_person_labels(tmp_path: Path) -> None:
    """Selected-sample metrics use owner link decisions, never machine entity predictions or guessed notes."""
    profiles=[Profile(id=i,address=f'a{i}@test') for i in range(1,5)]
    pairs=[Pair(left=1,right=2,blocks=['test'],features=[0.0]*9),Pair(left=3,right=4,blocks=['test'],features=[0.0]*9)]
    database=tmp_path/'experiment.sqlite3'
    save_pairs(database,pairs)
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE profiles(id INTEGER PRIMARY KEY,value TEXT)')
        connection.executemany('INSERT INTO profiles VALUES(?,?)',[(p.id,p.model_dump_json()) for p in profiles])
        connection.executemany('INSERT INTO scores VALUES(?,?,?)',[('test',0,1.0),('test',1,1.0)])
    labels=tmp_path/'labels.json'
    labels.write_text('[{"candidate_id":"1:2","left":"a1@test","right":"a2@test","label":"match","notes":"not a person"},'
        '{"candidate_id":"3:4","left":"a3@test","right":"a4@test","label":"not-person","notes":"same organization"}]')
    result=evaluate(database,labels)
    assert result.explicit_non_person==2
    assert result.all_identity_results[0].judged==1 and result.all_identity_results[0].true_positive==1
    assert result.owner_person_subset_results[0].judged==0
    labels.write_text(labels.read_text().replace('a4@test','wrong@test'))
    with pytest.raises(ValueError,match='address mapping'):
        evaluate(database,labels)


def test_name_normalization_and_disjoint_synthetic_entities() -> None:
    assert name_key("Morgan, Avery L.") == "avery l morgan"
    train, test = synthetic_profiles(11,12), synthetic_profiles(12,12)
    assert not ({profile.name for profile in train if profile.name} & {profile.name for profile in test if profile.name})
    pairs, stats = candidates(train)
    assert pairs == candidates(list(reversed(train)))[0]
    assert stats.pairs == len(pairs) and all(pair.left < pair.right for pair in pairs)
    model = fit_mixture(pairs)
    assert all(0 <= model.score(pair.features) <= 1 for pair in pairs)


def test_cluster_metrics_count_transitive_overmerges_and_missed_blocks() -> None:
    profiles=[Profile(id=1,address='a@test',group='A'),Profile(id=2,address='b@test',group='A'),Profile(id=3,address='c@test',group='B')]
    pairs=[Pair(left=1,right=2,blocks=['test'],features=[0.0]*9),Pair(left=2,right=3,blocks=['test'],features=[0.0]*9)]
    metric=quality(profiles,pairs,[1.0,1.0],0.99)
    assert metric.precision == pytest.approx(1/3) and metric.recall == 1
    assert metric.overmerged_clusters==1 and metric.largest_error_component==3
    missed=quality(profiles,pairs[1:],[0.0],0.99)
    assert missed.recall==0 and missed.false_negative==1 and missed.blocking_recall==0


def test_common_name_blocks_are_bounded_instead_of_forming_all_pairs() -> None:
    profiles=[Profile(id=i,address=f'{i}@test',name='avery morgan',names=['avery morgan']) for i in range(40)]
    pairs,stats=candidates(profiles,block_limit=25)
    assert not pairs and stats.skipped_blocks==2


def test_profile_preparation_preserves_original_database_and_mbox(tmp_path: Path) -> None:
    archive=tmp_path/'archive'
    raw=b'From: Sender Name <sender@example.test>\nTo: Reader <reader@example.test>\n\nHi\n'
    _one_message_archive(archive,raw)
    evidence=tmp_path/'evidence.sqlite3'
    build_database(archive,evidence,workers=1,limit=None)
    sources=[archive/'archive.sqlite3',*list((archive/'data').rglob('*.mbox'))]
    before=[hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]
    profiles=prepare_profiles(archive,evidence,tmp_path/'profiles.sqlite3')
    assert profiles[0].name=='sender name' and profiles[0].sent==1
    assert before==[hashlib.sha256(path.read_bytes()).hexdigest() for path in sources]
    assert not list(archive.glob('*-journal'))


@pytest.mark.skipif(importlib.util.find_spec('dedupe') is None,reason='requires isolated name-research environment')
def test_real_dedupe_scores_the_frozen_pairs() -> None:
    train,test=synthetic_profiles(101,16),synthetic_profiles(102,12)
    pairs,_=candidates(test)
    model=fit_dedupe(train)
    scores=score_dedupe(model,test,pairs)
    assert len(scores)==len(pairs) and all(0<=score<=1 for score in scores)
    assert any(score>0.5 for score in scores)


@pytest.mark.skipif(importlib.util.find_spec('splink') is None,reason='requires isolated name-research environment')
def test_real_splink_scores_exact_frozen_pairs_on_new_entities() -> None:
    train,test=synthetic_profiles(201,16),synthetic_profiles(202,12)
    pairs,_=candidates(test)
    model=fit_splink(train)
    scores=score_splink(splink_on_new_profiles(model,test),pairs)
    assert len(scores)==len(pairs) and all(0<=score<=1 for score in scores)
    assert any(score>0.5 for score in scores)


def test_evidence_scope_rejects_foreign_bounded_and_stale_extractions(tmp_path: Path) -> None:
    """Full-scale reports may not combine partial or unrelated evidence with a full catalog."""
    archive=tmp_path/'archive'
    raw=b'From: Sender Name <sender@example.test>\n\nEvidence provenance fixture\n'
    _one_message_archive(archive,raw)
    evidence=tmp_path/'evidence.sqlite3'
    build_database(archive,evidence,workers=1,limit=None)
    assert validate_evidence(archive,evidence).messages==1
    foreign=tmp_path/'foreign'
    _one_message_archive(foreign,raw)
    with pytest.raises(ValueError,match='different archive'):
        validate_evidence(foreign,evidence)
    bounded=tmp_path/'bounded.sqlite3'
    build_database(archive,bounded,workers=1,limit=1)
    with pytest.raises(ValueError,match='Bounded evidence'):
        validate_evidence(archive,bounded)
    with sqlite3.connect(archive/'archive.sqlite3') as catalog:
        catalog.execute("UPDATE messages SET sha256=?",('0'*64,))
    with pytest.raises(ValueError,match='stale or partial'):
        validate_evidence(archive,evidence)
