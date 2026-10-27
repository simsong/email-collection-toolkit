# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Compare address identities without changing any source or accepted identity state.
# Typed profiles retain bounded header, signature, time and correspondent evidence.
# Deterministic blocks freeze candidate pairs before engines are compared.
# Rules and an unsupervised Fellegi-Sunter mixture expose distinct assumptions.
# Synthetic entity labels are used only for explicitly synthetic evaluation.
# Scores are experimental ranking values until independently labeled calibration.
"""Reusable, local linkage experiments and labeled cluster metrics."""
from __future__ import annotations

import itertools
import math
import random
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from pydantic import BaseModel, Field

SEED = 20261021
FEATURES = ("full_name", "name_similarity", "local", "initial_surname", "domain", "phone", "signature_link", "repeated_signature", "neighbors")
ROLE = re.compile(r"(?:^|[._+-])(?:info|admin|support|sales|news|newsletter|noreply|no-reply|help|office|president|webmaster|list|service|contact|notification|alerts?)(?:$|[._+-])")
NONPERSON = {"mailing", "list", "team", "customer", "service", "support", "newsletter", "department", "university", "notifications", "office", "admin", "unknown", "undisclosed"}


def name_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    if "," in value:
        last, _, first = value.partition(",")
        value = first + " " + last
    return " ".join(re.findall(r"[^\W\d_]+", value))


def human_name(value: str) -> bool:
    tokens = value.split()
    return 2 <= len(tokens) <= 5 and len(tokens[-1]) >= 3 and not (set(tokens) & NONPERSON) and "@" not in value


def local_key(value: str) -> str:
    return "".join(character for character in value.casefold().split("+", 1)[0] if character.isalpha())


class Profile(BaseModel):
    id: int
    address: str
    name: str = ""
    observed_name: str = ""  # Dominant original observation, including organization names.
    names: list[str] = Field(default_factory=list)
    local: str = ""
    domain: str = ""
    first: str = ""
    last: str = ""
    observations: int = 0
    sent: int = 0
    kind: str = "unknown"
    phones: list[str] = Field(default_factory=list)
    signature_links: dict[int, int] = Field(default_factory=dict)
    repeated_links: dict[int, int] = Field(default_factory=dict)
    neighbors: list[int] = Field(default_factory=list)
    group: str = ""  # Populated only for a synthetic corpus or explicit independent labels.


class Pair(BaseModel):
    left: int
    right: int
    blocks: list[str]
    features: list[float]
    veto: bool = False


class BlockingSummary(BaseModel):
    pairs: int
    skipped_blocks: int
    skipped_memberships: int
    block_limit: int
    feature_names: tuple[str, ...] = FEATURES


def pair_features(left: Profile, right: Profile) -> list[float]:
    names_left, names_right = set(left.names), set(right.names)
    exact = bool(names_left & names_right)
    similarity = SequenceMatcher(None, left.name, right.name, autojunk=False).ratio() if left.name and right.name else 0.0
    def initials(name: str) -> tuple[str, str]:
        return (name.split()[0][0], name.split()[-1]) if human_name(name) else ("", "")
    initial_match = any(initials(a) == initials(b) for a in names_left for b in names_right)
    a_neighbors, b_neighbors = set(left.neighbors), set(right.neighbors)
    overlap = len(a_neighbors & b_neighbors) / max(1, len(a_neighbors | b_neighbors))
    return [float(exact), similarity, float(bool(left.local) and left.local == right.local),
        float(initial_match), float(left.domain == right.domain), float(bool(set(left.phones) & set(right.phones))),
        float(right.id in left.signature_links or left.id in right.signature_links),
        float(right.id in left.repeated_links or left.id in right.repeated_links), overlap]


def candidates(profiles: list[Profile], block_limit: int = 25) -> tuple[list[Pair], BlockingSummary]:
    """Bound common blocks; weak initials are proposals, never an automatic merge."""
    blocks: dict[tuple[str, str], set[int]] = defaultdict(set)
    by_id = {profile.id: profile for profile in profiles}
    reasons: dict[tuple[int, int], set[str]] = defaultdict(set)
    for profile in profiles:
        for name in profile.names:
            if human_name(name):
                blocks["name", name].add(profile.id)
                tokens = name.split()
                blocks["initial-surname", tokens[0][0] + ":" + tokens[-1]].add(profile.id)
        if len(profile.local) >= 6:
            blocks["local", profile.local].add(profile.id)
        for phone in profile.phones:
            blocks["phone", phone].add(profile.id)
        for target in set(profile.signature_links) | set(profile.repeated_links):
            if target in by_id and target != profile.id:
                reasons[min(profile.id, target), max(profile.id, target)].add("signature")
    skipped = memberships = 0
    for (kind, _key), members in sorted(blocks.items()):
        if len(members) > block_limit:
            skipped += 1
            memberships += len(members)
            continue
        for pair in itertools.combinations(sorted(members), 2):
            reasons[pair].add(kind)
    pairs = [Pair(left=a, right=b, blocks=sorted(why), features=pair_features(by_id[a], by_id[b]),
        veto=by_id[a].kind in ("role", "automated", "mailing-list") or by_id[b].kind in ("role", "automated", "mailing-list"))
        for (a, b), why in sorted(reasons.items())]
    return pairs, BlockingSummary(pairs=len(pairs), skipped_blocks=skipped, skipped_memberships=memberships, block_limit=block_limit)


def rule_scores(pairs: list[Pair], signatures: bool = False, repeated: bool = False, graph: bool = False) -> list[float]:
    scores = []
    for pair in pairs:
        name, _similarity, local, initials, _domain, phone, signature, repeat, neighbors = pair.features
        accepted = bool(name and local or signatures and name and (signature or phone) or repeated and initials and repeat)
        if graph and name and neighbors >= 0.3:
            accepted = True
        scores.append(float(accepted and not pair.veto))
    return scores


class MixtureModel(BaseModel):
    prior: float
    match_rates: list[float]
    nonmatch_rates: list[float]
    iterations: int
    converged: bool

    def score(self, features: list[float]) -> float:
        bits = binary_features(features)
        log_odds = math.log(self.prior / (1 - self.prior))
        for bit, matched, other in zip(bits, self.match_rates, self.nonmatch_rates):
            log_odds += math.log((matched if bit else 1 - matched) / (other if bit else 1 - other))
        return 1 / (1 + math.exp(-max(-700, min(700, log_odds))))


def binary_features(features: list[float]) -> tuple[int, ...]:
    return tuple(int(value >= (0.90 if index == 1 else 0.2 if index == 8 else 0.5)) for index, value in enumerate(features))


def fit_mixture(pairs: list[Pair], maximum_iterations: int = 100) -> MixtureModel:
    """Fit an unsupervised blocked-pair mixture, with no archive-derived gold labels."""
    counts = Counter(binary_features(pair.features) for pair in pairs)
    if not counts:
        raise ValueError("mixture training requires candidate pairs")
    model = MixtureModel(prior=0.1, match_rates=[0.8] * len(FEATURES), nonmatch_rates=[0.1] * len(FEATURES), iterations=0, converged=False)
    total = sum(counts.values())
    for iteration in range(maximum_iterations):
        responsibilities = [(pattern, count, model.score(list(pattern))) for pattern, count in counts.items()]
        match_total = sum(count * score for _, count, score in responsibilities)
        other_total = total - match_total
        def clip(value: float) -> float:
            return min(0.999, max(0.001, value))
        matched = [clip((1 + sum(count * score * pattern[index] for pattern, count, score in responsibilities)) / (2 + match_total)) for index in range(len(FEATURES))]
        other = [clip((1 + sum(count * (1 - score) * pattern[index] for pattern, count, score in responsibilities)) / (2 + other_total)) for index in range(len(FEATURES))]
        prior = clip((match_total + 1) / (total + 2))
        delta = max(abs(prior - model.prior), *(abs(a - b) for a, b in zip(matched + other, model.match_rates + model.nonmatch_rates)))
        model = MixtureModel(prior=prior, match_rates=matched, nonmatch_rates=other, iterations=iteration + 1, converged=delta < 1e-6)
        if model.converged:
            break
    return model


class Quality(BaseModel):
    threshold: float
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float | None
    recall: float | None
    f1: float | None
    b3_precision: float
    b3_recall: float
    b3_f1: float
    overmerged_clusters: int
    undermerged_entities: int
    largest_error_component: int
    blocking_recall: float
    brier: float
    ece: float


def components(ids: list[int], edges: list[tuple[int, int]]) -> dict[int, int]:
    parents = {item: item for item in ids}
    def find(item: int) -> int:
        while parents[item] != item:
            parents[item] = parents[parents[item]]
            item = parents[item]
        return item
    for left, right in edges:
        a, b = find(left), find(right)
        parents[max(a, b)] = min(a, b)
    return {item: find(item) for item in ids}


def quality(profiles: list[Profile], pairs: list[Pair], scores: list[float], threshold: float) -> Quality:
    """Evaluate all true synthetic pairs, so missed blocks count as false negatives."""
    if not profiles or any(not profile.group for profile in profiles) or len(scores) != len(pairs):
        raise ValueError("quality requires complete independent labels and aligned scores")
    gold = {profile.id: profile.group for profile in profiles}
    true_sizes = Counter(gold.values())
    true_pairs = sum(size * (size - 1) // 2 for size in true_sizes.values())
    accepted = [(pair.left, pair.right) for pair, score in zip(pairs, scores) if score >= threshold]
    predicted = components(list(gold), accepted)
    cluster_sizes = Counter(predicted.values())
    cells = Counter((predicted[item], gold[item]) for item in gold)
    tp = sum(size * (size - 1) // 2 for size in cells.values())
    predicted_pairs = sum(size * (size - 1) // 2 for size in cluster_sizes.values())
    fp, fn = predicted_pairs - tp, true_pairs - tp
    precision = tp / predicted_pairs if predicted_pairs else None
    recall = tp / true_pairs if true_pairs else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    b3p = sum(cells[predicted[item], gold[item]] / cluster_sizes[predicted[item]] for item in gold) / len(gold)
    b3r = sum(cells[predicted[item], gold[item]] / true_sizes[gold[item]] for item in gold) / len(gold)
    over = {cluster for cluster in cluster_sizes if sum(1 for cell in cells if cell[0] == cluster) > 1}
    under = sum(sum(1 for cell in cells if cell[1] == group) > 1 for group in true_sizes)
    blocked_true = sum(gold[pair.left] == gold[pair.right] for pair in pairs)
    bins: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for pair, score in zip(pairs, scores):
        bins[min(9, int(score * 10))].append((score, int(gold[pair.left] == gold[pair.right])))
    ece = sum(abs(sum(score - truth for score, truth in rows)) for rows in bins.values()) / max(1, len(pairs))
    brier = sum((score - int(gold[pair.left] == gold[pair.right])) ** 2 for pair, score in zip(pairs, scores)) / max(1, len(pairs))
    return Quality(threshold=threshold, true_positive=tp, false_positive=fp, false_negative=fn, precision=precision,
        recall=recall, f1=f1, b3_precision=b3p, b3_recall=b3r, b3_f1=2*b3p*b3r/(b3p+b3r),
        overmerged_clusters=len(over), undermerged_entities=under, largest_error_component=max((cluster_sizes[item] for item in over), default=0),
        blocking_recall=blocked_true / true_pairs if true_pairs else 0.0, brier=brier, ece=ece)


def synthetic_profiles(seed: int, entities: int = 120) -> list[Profile]:
    """Generate disjoint entity vocabularies and hard negatives, never archive-derived labels."""
    rng = random.Random(seed)
    tag = "".join(chr(97 + int(digit)) for digit in str(seed))
    profiles = []
    for entity in range(entities):
        first = f"Avery{tag}{chr(97+entity%26)}{chr(97+entity//26)}"
        last = f"Morgan{tag}{chr(97+(entity//3)%26)}{chr(97+(entity//78)%26)}"
        full = name_key(first + " " + last)
        for alias in range(4):
            ident = len(profiles)
            local = (first + last if alias % 2 == 0 else first[0] + last).lower()
            observed = [full] if alias != 3 else [name_key(first[0]+" "+last)]
            if alias == 2 and entity % 4 == 0:
                observed = []
            profiles.append(Profile(id=ident, address=f"{local}{entity}@site{alias}.test", name=observed[0] if observed else "",
                names=observed, local=local, domain=f"site{alias}.test", first="2000-01-01", last="2025-01-01", sent=20,
                observations=40, kind="person", phones=[f"+1555{seed%10000:04d}{entity:04d}"],
                signature_links={ident-alias: 5} if alias else {}, repeated_links={ident-alias: 3} if alias else {},
                neighbors=[100000+entity*10+offset for offset in range(4)], group=f"{seed}:{entity}"))
        if entity % 8 == 0:
            # Different person with the exact same name, distinct contacts and correspondents.
            profiles.append(Profile(id=len(profiles), address=f"{first}{last}.other@other.test".lower(), name=full,
                names=[full], local=local_key(first+last), domain="other.test", sent=10, observations=10,
                kind="person", neighbors=[200000+entity], group=f"{seed}:same-name-negative:{entity}"))
        if entity % 10 == 0:
            profiles.append(Profile(id=len(profiles), address=f"support{entity}@company.test", name=full,
                names=[full], local="support", domain="company.test", kind="role", group=f"{seed}:role:{entity}"))
    rng.shuffle(profiles)
    return profiles
