# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Run optional research engines against the same externally frozen pair list.
# Dedupe learns solely from independent synthetic training identities for this run.
# Splink learns unsupervised parameters and scores registered blocked pairs.
# A collective pass projects correspondent neighborhoods through seeded clusters.
# All third-party records are constructed at named-key API boundaries.
# No engine persists accepted groups or accesses a source mailbox.
"""Local Dedupe, Splink and collective-graph experiment adapters."""
from __future__ import annotations

import importlib
import itertools
import random
from typing import Any

from .linkage import SEED, Pair, Profile, components

ID = "unique_id"
NAME = "name"
LOCAL = "local"
DOMAIN = "domain"
PHONE = "phone"
INITIAL = "initial_surname"
MATCH = "match"
DISTINCT = "distinct"
LEFT_KEY = "join_key_l"
RIGHT_KEY = "join_key_r"
MATCH_KEY = "match_key"
LEFT_ID = "unique_id_l"
RIGHT_ID = "unique_id_r"
PROBABILITY = "match_probability"
PAIRS = "pairs"
SCORE = "score"


def external_record(profile: Profile) -> dict[str, Any]:
    tokens = profile.name.split()
    return {ID: profile.id, NAME: profile.name or None, LOCAL: profile.local or None,
        DOMAIN: profile.domain or None, PHONE: profile.phones[0] if profile.phones else None,
        INITIAL: tokens[0][0] + tokens[-1] if len(tokens) >= 2 else None}


def fit_dedupe(profiles: list[Profile]) -> Any:
    """Use known synthetic entities, never name-derived pseudo-labels from the archive."""
    random.seed(SEED)
    importlib.import_module("numpy").random.seed(SEED)
    dedupe = importlib.import_module("dedupe")
    variables = [dedupe.variables.String(NAME, has_missing=True), dedupe.variables.String(LOCAL, has_missing=True),
        dedupe.variables.Exact(DOMAIN, has_missing=True), dedupe.variables.Exact(PHONE, has_missing=True)]
    model = dedupe.Dedupe(variables, num_cores=0, in_memory=True)
    records = {profile.id: external_record(profile) for profile in profiles}
    groups: dict[str, list[Profile]] = {}
    for profile in profiles:
        groups.setdefault(profile.group, []).append(profile)
    positives = [(records[a.id], records[b.id]) for members in groups.values() for a, b in itertools.combinations(members, 2)]
    rng = random.Random(SEED)
    negatives = []
    # Include same-name different people and role-account negatives, then random negatives.
    for a, b in itertools.combinations(profiles, 2):
        if a.group != b.group and a.name and a.name == b.name:
            negatives.append((records[a.id], records[b.id]))
    while len(negatives) < max(500, len(positives)):
        a, b = rng.sample(profiles, 2)
        if a.group != b.group:
            negatives.append((records[a.id], records[b.id]))
    model.prepare_training(records, sample_size=min(1500, len(records) * 2))
    model.mark_pairs({MATCH: positives, DISTINCT: negatives})
    model.train(index_predicates=False)
    return model


def score_dedupe(model: Any, profiles: list[Profile], pairs: list[Pair]) -> list[float]:
    records = {profile.id: external_record(profile) for profile in profiles}
    scores = model.score(iter(((pair.left, records[pair.left]), (pair.right, records[pair.right])) for pair in pairs))
    by_pair = {tuple(int(item) for item in row[PAIRS]): float(row[SCORE]) for row in scores}
    return [0.0 if pair.veto else by_pair.get((pair.left, pair.right), 0.0) for pair in pairs]


def fit_splink(profiles: list[Profile], name_frequencies: bool = False) -> Any:
    splink = importlib.import_module("splink")
    comparisons = importlib.import_module("splink.comparison_library")
    records = [external_record(profile) for profile in profiles]
    settings = splink.SettingsCreator(link_type="dedupe_only", unique_id_column_name=ID,
        probability_two_random_records_match=min(0.01, 2/max(2, len(profiles))),
        comparisons=[comparisons.JaroWinklerAtThresholds(NAME, [0.95, 0.85]).configure(term_frequency_adjustments=name_frequencies),
            comparisons.JaroWinklerAtThresholds(LOCAL, [0.95, 0.85]),
            comparisons.ExactMatch(DOMAIN), comparisons.ExactMatch(PHONE)],
        blocking_rules_to_generate_predictions=[], retain_matching_columns=False,
        retain_intermediate_calculation_columns=False, max_iterations=25, em_convergence=0.001)
    backend = splink.DuckDBAPI()
    linker = splink.Linker(backend.register(records, table_name="profiles"), settings)
    linker.training.estimate_u_using_random_sampling(max_pairs=100000, seed=SEED)
    linker.training.estimate_parameters_using_expectation_maximisation(
        f"l.{INITIAL}=r.{INITIAL} AND l.{INITIAL} IS NOT NULL", max_pairs=200000)
    return linker


def score_splink(linker: Any, pairs: list[Pair]) -> list[float]:
    # Splink 5's public API accepts frozen blocked pair IDs directly.
    table = linker._db_api.register(  # pylint: disable=protected-access
        [{LEFT_KEY: pair.left, RIGHT_KEY: pair.right, MATCH_KEY: "0"} for pair in pairs], table_name="frozen_candidates")
    linker.table_management.register_blocked_pairs_for_predict(table)
    prediction = linker.inference.predict()
    rows = prediction.as_record_list()
    by_pair = {(int(row[LEFT_ID]), int(row[RIGHT_ID])): float(row[PROBABILITY]) for row in rows}
    if len(by_pair) != len(pairs):
        raise ValueError(f"Splink returned {len(by_pair)} of {len(pairs)} frozen pairs")
    return [0.0 if pair.veto else by_pair[pair.left, pair.right] for pair in pairs]


def splink_on_new_profiles(trained: Any, profiles: list[Profile]) -> Any:
    splink = importlib.import_module("splink")
    settings = trained.misc.save_model_to_json()
    backend = splink.DuckDBAPI()
    return splink.Linker(backend.register([external_record(profile) for profile in profiles], table_name="test_profiles"), settings)


def collective_scores(profiles: list[Profile], pairs: list[Pair], seeds: list[float]) -> list[float]:
    """Propagate name-compatible links through equivalence-projected correspondent sets."""
    scores = list(seeds)
    for _ in range(2):
        clusters = components([profile.id for profile in profiles], [(p.left, p.right) for p, s in zip(pairs, scores) if s >= 0.99])
        neighborhoods = {profile.id: {clusters.get(item, item) for item in profile.neighbors} for profile in profiles}
        changed = False
        for index, pair in enumerate(pairs):
            if pair.veto or scores[index] >= 0.99 or not (pair.features[0] or pair.features[3]):
                continue
            a, b = neighborhoods[pair.left], neighborhoods[pair.right]
            common = len(a & b)
            if common >= 2 and common / max(1, len(a | b)) >= 0.25:
                scores[index] = 0.99
                changed = True
        if not changed:
            break
    return scores
