# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Run reproducible local identity-linkage comparisons on a read-only archive.
# Verified extraction is a separate prerequisite; profiles are bounded derivatives.
# All engines consume one frozen candidate list and persist scores separately.
# Synthetic entities supply explicit training/test labels, never private gold.
# Private HTML and JSON reports expose runtime, limitations and review examples.
# The report never applies proposed links to the archive or contacts databases.
"""Compare local identity matchers and prepare fifty owner-review examples."""
from __future__ import annotations

import argparse
import hashlib
import html
import importlib.metadata
import json
import logging
import os
import random
import re
import resource
import sqlite3
import time

import yaml
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from mailarchiver.name_entities import classify_name

from .engines import collective_scores, fit_dedupe, fit_splink, score_dedupe, score_splink, splink_on_new_profiles
from .linkage import FEATURES, ROLE, SEED, BlockingSummary, Pair, Profile, Quality, candidates, components, fit_mixture, human_name, local_key, name_key, quality, rule_scores, synthetic_profiles

THRESHOLDS = (0.5, 0.9, 0.99, 0.999)
DEFAULT_THRESHOLD = 0.99


class Timing(BaseModel):
    wall_seconds: float
    cpu_seconds: float


class EngineRun(BaseModel):
    name: str
    training: str
    fit: Timing
    score: Timing
    accepted_edges: int
    linked_addresses: int
    nontrivial_clusters: int
    largest_cluster: int
    test_quality: list[Quality] = Field(default_factory=list)
    thresholds: list[tuple[float, int]] = Field(default_factory=list)
    error: str | None = None


class RunSummary(BaseModel):
    created_at: str
    archive: str
    evidence: str = ""
    messages: int
    extraction_errors: int
    extraction_seconds: float
    profiles: int
    profiles_with_names: int
    frozen_candidates: int
    candidate_sha256: str
    seed: int = SEED
    preparation: Timing
    blocking: Timing
    block_summary: BlockingSummary
    engines: list[EngineRun] = Field(default_factory=list)
    versions: dict[str, str] = Field(default_factory=dict)
    process_peak_rss_bytes: int = 0
    source_unchanged: bool = False


def read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=60)


class EvidenceScope(BaseModel):
    messages: int
    errors: int
    extraction_seconds: float


def validate_evidence(archive: Path, evidence: Path) -> EvidenceScope:
    """Reject unrelated, bounded, incomplete or stale observations before aggregation."""
    with read_only(evidence) as source:
        runs=source.execute("SELECT archive_catalog,started_at,completed_at,configuration_yaml FROM extraction_runs").fetchall()
        if len(runs)!=1:
            raise ValueError("Evidence must contain exactly one completed extraction run")
        recorded,start,end,configuration=runs[0]
        if Path(recorded).resolve()!=(archive/'archive.sqlite3').resolve():
            raise ValueError("Evidence belongs to a different archive")
        if not end:
            raise ValueError("Evidence extraction is incomplete")
        config=yaml.safe_load(configuration)
        limit_key="limit"
        if not isinstance(config,dict) or config.get(limit_key) is not None:
            raise ValueError("Bounded evidence is not a full-archive extraction")
        errors=source.execute("SELECT count(*) FROM extraction_errors").fetchone()[0]
        if errors:
            raise ValueError(f"Evidence has {errors} extraction errors; full coverage is required")
        source.execute("ATTACH DATABASE ? AS catalog",((archive/'archive.sqlite3').resolve().as_uri()+"?mode=ro",))
        messages=source.execute("SELECT count(*) FROM messages").fetchone()[0]
        expected=source.execute("SELECT count(*) FROM catalog.messages").fetchone()[0]
        missing=source.execute("SELECT count(*) FROM catalog.messages c LEFT JOIN messages e ON e.catalog_message_pk=c.message_pk AND e.message_sha256=c.sha256 WHERE e.catalog_message_pk IS NULL").fetchone()[0]
        if messages!=expected or missing:
            raise ValueError(f"Evidence is stale or partial: {messages} extracted, {expected} catalogued, {missing} unmatched hashes")
        return EvidenceScope(messages=messages,errors=errors,extraction_seconds=(datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds())


def timed(function: Callable[[], Any]) -> tuple[Any, Timing]:
    wall, cpu = time.perf_counter(), time.process_time()
    result = function()
    return result, Timing(wall_seconds=time.perf_counter()-wall, cpu_seconds=time.process_time()-cpu)


def prepare_profiles(archive: Path, evidence: Path, destination: Path) -> list[Profile]:
    """Aggregate evidence on disk; never retain canonical messages or bodies in memory."""
    output = sqlite3.connect(destination, uri=True)
    output.execute("PRAGMA temp_store=FILE")
    output.execute("ATTACH DATABASE ? AS evidence", (evidence.resolve().as_uri()+"?mode=ro",))
    output.execute("ATTACH DATABASE ? AS catalog", ((archive/'archive.sqlite3').resolve().as_uri()+"?mode=ro",))
    profiles = []
    for ident, address in output.execute("SELECT address_pk,address FROM catalog.email_addresses ORDER BY address_pk"):
        local, _, domain = address.rpartition("@")
        kind = "automated" if any(term in local for term in ("noreply", "no-reply", "bounce", "mailer-daemon")) else "role" if ROLE.search(local) else "mailing-list" if any(term in local for term in ("listserv", "-list", "lists.")) else "unknown"
        profiles.append(Profile(id=ident, address=address, local=local_key(local), domain=domain, kind=kind))
    by_address = {profile.address.casefold(): profile for profile in profiles}
    by_id = {profile.id: profile for profile in profiles}
    output.execute("CREATE TABLE names AS SELECT address,normalized_name,count(*) n FROM evidence.header_observations GROUP BY address,normalized_name")
    for address, observed, count in output.execute("SELECT address,normalized_name,n FROM names ORDER BY address,n DESC,normalized_name"):
        profile = by_address.get(address)
        if profile is None:
            continue
        profile.observations += count
        if observed and not profile.observed_name:
            profile.observed_name = observed
        normalized = name_key(observed)
        if human_name(normalized) and normalized not in profile.names and len(profile.names) < 5:
            profile.names.append(normalized)
            if not profile.name:
                profile.name = normalized
    for address, first, last, sent in output.execute("""SELECT h.address,min(m.observed_at),max(m.observed_at),
        count(DISTINCT CASE WHEN h.role='from' THEN h.catalog_message_pk END)
        FROM evidence.header_observations h JOIN evidence.messages m USING(catalog_message_pk) GROUP BY h.address"""):
        if profile := by_address.get(address):
            profile.first, profile.last, profile.sent = first, last, sent
    output.execute("""CREATE TABLE signature_links AS SELECT h.address sender,f.normalized_value target,
        count(DISTINCT h.catalog_message_pk) n FROM evidence.header_observations h
        JOIN evidence.signature_facts f USING(catalog_message_pk) WHERE h.role='from' AND f.kind='email'
        AND h.address<>f.normalized_value GROUP BY h.address,f.normalized_value""")
    for sender, target, count in output.execute("SELECT sender,target,n FROM signature_links"):
        a, b = by_address.get(sender), by_address.get(target)
        if a and b and a.id != b.id:
            a.signature_links[b.id] = count
    output.execute("""CREATE TABLE repeated_signature_links AS SELECT sender,target,max(n) n FROM (
        SELECT h.address sender,f.normalized_value target,b.text_sha256,count(DISTINCT h.catalog_message_pk) n
        FROM evidence.header_observations h JOIN evidence.signature_facts f USING(catalog_message_pk)
        JOIN evidence.signature_blocks b USING(catalog_message_pk,block_ordinal)
        WHERE h.role='from' AND f.kind='email' AND h.address<>f.normalized_value
        GROUP BY h.address,f.normalized_value,b.text_sha256 HAVING count(DISTINCT h.catalog_message_pk)>=3)
        GROUP BY sender,target""")
    for sender, target, count in output.execute("SELECT sender,target,n FROM repeated_signature_links"):
        a, b = by_address.get(sender), by_address.get(target)
        if a and b and a.id != b.id:
            a.repeated_links[b.id] = count
    output.execute("""CREATE TABLE phones AS SELECT h.address,f.normalized_value phone,count(DISTINCT h.catalog_message_pk) n
        FROM evidence.header_observations h JOIN evidence.signature_facts f USING(catalog_message_pk)
        WHERE h.role='from' AND f.kind='phone' GROUP BY h.address,f.normalized_value""")
    for address, phone in output.execute("""SELECT address,phone FROM phones WHERE phone IN
        (SELECT phone FROM phones GROUP BY phone HAVING count(*)<=5) AND n>=2 ORDER BY address,n DESC,phone"""):
        if (profile := by_address.get(address)) and len(profile.phones) < 3:
            profile.phones.append(phone)
    output.execute("""CREATE TABLE edges AS SELECT m.sender_address_pk a,r.address_pk b,count(*) n
        FROM catalog.messages m JOIN catalog.recipients r USING(message_pk)
        WHERE m.sender_address_pk<>r.address_pk GROUP BY m.sender_address_pk,r.address_pk""")
    for ident, neighbor, _count in output.execute("""SELECT a,b,sum(n) total FROM
        (SELECT a,b,n FROM edges UNION ALL SELECT b,a,n FROM edges) GROUP BY a,b ORDER BY a,total DESC,b"""):
        if ident in by_id and len(by_id[ident].neighbors) < 100:
            by_id[ident].neighbors.append(neighbor)
    output.execute("CREATE TABLE profiles(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
    output.executemany("INSERT INTO profiles VALUES(?,?)", ((profile.id, profile.model_dump_json()) for profile in profiles))
    output.commit()
    output.close()
    return profiles


def load_profiles(database: Path) -> list[Profile]:
    with read_only(database) as connection:
        profiles = [Profile.model_validate_json(row[0]) for row in connection.execute("SELECT value FROM profiles ORDER BY id")]
        by_address = {profile.address:profile for profile in profiles}
        for address,name in connection.execute("SELECT address,normalized_name FROM names WHERE normalized_name<>'' ORDER BY address,n DESC,normalized_name"):
            if (profile:=by_address.get(address)) and not profile.observed_name:
                profile.observed_name=name
        return profiles


def save_pairs(database: Path, pairs: list[Pair]) -> str:
    digest = hashlib.sha256()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE candidates(id INTEGER PRIMARY KEY,left_id INTEGER,right_id INTEGER,value TEXT NOT NULL)")
        for index, pair in enumerate(pairs):
            record = pair.model_dump_json()
            digest.update(record.encode())
            digest.update(b"\n")
            connection.execute("INSERT INTO candidates VALUES(?,?,?,?)", (index, pair.left, pair.right, record))
        connection.execute("CREATE TABLE scores(engine TEXT,pair_id INTEGER,score REAL,PRIMARY KEY(engine,pair_id))")
    return digest.hexdigest()


def register_result(summary: RunSummary, engine: str, training: str, fit: Timing, score_time: Timing,
                    profiles: list[Profile], pairs: list[Pair], scores: list[float], test_profiles: list[Profile],
                    test_pairs: list[Pair], test_scores: list[float], database: Path) -> None:
    if len(scores) != len(pairs) or any(not 0 <= score <= 1 for score in scores):
        raise ValueError("engine scores are incomplete or invalid")
    edges = [(pair.left, pair.right) for pair, score in zip(pairs, scores) if score >= DEFAULT_THRESHOLD]
    clusters = components([profile.id for profile in profiles], edges)
    sizes = Counter(clusters.values())
    with sqlite3.connect(database) as connection:
        connection.executemany("INSERT INTO scores VALUES(?,?,?)", ((engine, index, score) for index, score in enumerate(scores)))
    result = EngineRun(name=engine, training=training, fit=fit, score=score_time, accepted_edges=len(edges),
        linked_addresses=sum(size for size in sizes.values() if size > 1), nontrivial_clusters=sum(size>1 for size in sizes.values()),
        largest_cluster=max(sizes.values(), default=0), test_quality=[quality(test_profiles, test_pairs, test_scores, threshold) for threshold in THRESHOLDS],
        thresholds=[(threshold, sum(value>=threshold for value in scores)) for threshold in THRESHOLDS])
    summary.engines.append(result)
    print(engine, "fit", round(fit.wall_seconds,3), "score", round(score_time.wall_seconds,3), "accepted", len(edges), flush=True)


def run_engines(summary: RunSummary, profiles: list[Profile], pairs: list[Pair], output: Path) -> None:
    database = output/'experiments.sqlite3'
    training_profiles = synthetic_profiles(SEED + 1)
    testing_profiles = synthetic_profiles(SEED + 2)
    training_pairs, _ = candidates(training_profiles)
    testing_pairs, _ = candidates(testing_profiles)
    (output/'synthetic-training.json').write_text(json.dumps([profile.model_dump() for profile in training_profiles]))
    (output/'synthetic-test.json').write_text(json.dumps([profile.model_dump() for profile in testing_profiles]))
    zero = Timing(wall_seconds=0,cpu_seconds=0)
    def record(name: str, note: str, fit: Timing, times: Timing, scores: list[float], test: list[float]) -> None:
        register_result(summary,name,note,fit,times,profiles,pairs,scores,testing_profiles,testing_pairs,test,database)
        (output/'summary.json').write_text(summary.model_dump_json(indent=2))
    baseline, baseline_time = timed(lambda: [0.0 for _pair in pairs])
    record("no links baseline","Singletons; no matching",zero,baseline_time,baseline,[0.0 for _pair in testing_pairs])
    for name, options in (("name-local rules", {}), ("plus heuristic signatures", {"signatures":True}),
                          ("plus repeated detected signatures", {"signatures":True,"repeated":True})):
        scores, elapsed = timed(lambda options=options: rule_scores(pairs,**options))
        record(name,"No fitted parameters; fixed rules",zero,elapsed,scores,rule_scores(testing_pairs,**options))
    seeds = rule_scores(pairs,signatures=True,repeated=True)
    scores, elapsed = timed(lambda: collective_scores(profiles,pairs,seeds))
    record("collective correspondent graph","Two passes; rule seeds; no learned probabilities",zero,elapsed,scores,
        collective_scores(testing_profiles,testing_pairs,rule_scores(testing_pairs,signatures=True,repeated=True)))
    mixture, fit = timed(lambda: fit_mixture(pairs))
    (output/'fellegi-sunter-model.json').write_text(mixture.model_dump_json(indent=2))
    scores, elapsed = timed(lambda: [0.0 if pair.veto else mixture.score(pair.features) for pair in pairs])
    synthetic_mixture = fit_mixture(training_pairs)
    record("Fellegi-Sunter EM","Unsupervised blocked-pair mixture; synthetic model fit only on training entities",fit,elapsed,scores,
        [0.0 if pair.veto else synthetic_mixture.score(pair.features) for pair in testing_pairs])
    for engine in ("Dedupe", "Splink DuckDB", "Splink name frequency"):
        try:
            if engine == "Dedupe":
                model, fit = timed(lambda: fit_dedupe(training_profiles))
                scores, elapsed = timed(lambda: score_dedupe(model,profiles,pairs))
                testing_scores = score_dedupe(model,testing_profiles,testing_pairs)
                note = "Supervised on synthetic training entities only; no owner labels"
            else:
                frequencies = engine == "Splink name frequency"
                model, fit = timed(lambda frequencies=frequencies: fit_splink(profiles,name_frequencies=frequencies))
                scores, elapsed = timed(lambda: score_splink(model,pairs))
                model.misc.save_model_to_json(str(output/('splink-frequency-model.json' if frequencies else 'splink-model.json')),overwrite=True)
                synthetic_model = fit_splink(training_profiles,name_frequencies=frequencies)
                testing_scores = score_splink(splink_on_new_profiles(synthetic_model,testing_profiles),testing_pairs)
                note = "Unsupervised random-u and EM; prior 2/address count; synthetic fit on training entities only"
                if frequencies:
                    note += "; full-name term-frequency adjustment: rare exact names carry more evidence"
            record(engine,note,fit,elapsed,scores,testing_scores)
        except Exception as error:  # Preserve an engine failure without substituting another implementation.
            logging.exception("Engine %s failed",engine)
            summary.engines.append(EngineRun(name=engine,training="Run failed",fit=zero,score=zero,accepted_edges=0,
                linked_addresses=0,nontrivial_clusters=0,largest_cluster=0,error=f"{type(error).__name__}: {error}"))
            (output/'summary.json').write_text(summary.model_dump_json(indent=2))


def sample_pairs(database: Path, profiles: list[Profile], count: int = 50) -> list[tuple[Pair,dict[str,float],str]]:
    """Stratified, deterministic proposals for review, not a precision-estimation sample."""
    with read_only(database) as connection:
        engines = [row[0] for row in connection.execute("SELECT DISTINCT engine FROM scores WHERE engine<>'no links baseline' ORDER BY engine")]
        buckets: dict[str,list[tuple[Pair,dict[str,float],str]]] = {key:[] for key in ("consensus","signature","cross-domain","disagreement","uncertain")}
        by_id = {profile.id:profile for profile in profiles}
        cursor = connection.execute("SELECT c.id,c.value,s.engine,s.score FROM candidates c JOIN scores s ON s.pair_id=c.id ORDER BY c.id,s.engine")
        last_id = None
        current: Pair | None = None
        scores: dict[str,float] = {}
        def consider() -> None:
            if current is None or not scores:
                return
            accepted = sum(score>=DEFAULT_THRESHOLD for score in scores.values())
            if not accepted:
                return
            category = "consensus" if accepted==len(engines) else "signature" if current.features[6] else "cross-domain" if by_id[current.left].domain!=by_id[current.right].domain and current.features[0] else "disagreement" if accepted<len(engines)//2 else "uncertain"
            buckets[category].append((current,dict(scores),category))
        for ident, value, engine, score in cursor:
            if ident != last_id:
                consider()
                current = Pair.model_validate_json(value)
                scores = {}
                last_id = ident
            scores[engine] = score
        consider()
    rng = random.Random(SEED)
    for bucket in buckets.values():
        rng.shuffle(bucket)
    chosen = []
    appearances: Counter[int] = Counter()
    while len(chosen)<count and any(buckets.values()):
        for bucket in buckets.values():
            while bucket:
                item = bucket.pop()
                if appearances[item[0].left]>=2 or appearances[item[0].right]>=2:
                    continue
                chosen.append(item)
                appearances.update((item[0].left,item[0].right))
                break
            if len(chosen)==count:
                break
    if len(chosen)<count:
        raise ValueError(f"Only {len(chosen)} representative proposed links available")
    return chosen


def verify_source(output: Path, archive: Path) -> bool:
    before = json.loads((output/'source-before.json').read_text())
    unchanged = True
    for item in before['files']:
        status = (archive/item['path']).stat()
        unchanged &= status.st_size==item['size'] and status.st_mtime_ns==item['mtime_ns']
    with (archive/'archive.sqlite3').open('rb') as stream:
        unchanged &= hashlib.file_digest(stream,'sha256').hexdigest()==before['catalog_sha256']
    (output/'source-after.json').write_text(json.dumps({'unchanged':unchanged,'checked_at':datetime.now(UTC).isoformat()}))
    return unchanged


def render_report(summary: RunSummary, profiles: list[Profile], output: Path) -> None:
    by_id={profile.id:profile for profile in profiles}
    name_frequencies=Counter(name for profile in profiles for name in set(profile.names))
    selected=sample_pairs(output/'experiments.sqlite3',profiles)
    def escaped(value: object) -> str:
        return html.escape(str(value),quote=True)
    engine_rows=[]
    synthetic_rows=[]
    for engine in summary.engines:
        if engine.error:
            engine_rows.append(f'<tr><td>{escaped(engine.name)}</td><td colspan="6">FAILED: {escaped(engine.error)}</td></tr>')
            continue
        engine_rows.append(f'<tr><td>{escaped(engine.name)}</td><td>{engine.fit.wall_seconds:.2f}</td><td>{engine.score.wall_seconds:.2f}</td><td>{engine.fit.cpu_seconds+engine.score.cpu_seconds:.2f}</td><td>{engine.accepted_edges:,}</td><td>{engine.linked_addresses:,}</td><td>{engine.largest_cluster:,}</td></tr>')
        for metric in engine.test_quality:
            def percent(value: float | None) -> str:
                return '—' if value is None else f'{100*value:.1f}%'
            synthetic_rows.append(f'<tr><td>{escaped(engine.name)}</td><td>{metric.threshold:g}</td><td>{percent(metric.precision)}</td><td>{percent(metric.recall)}</td><td>{percent(metric.f1)}</td><td>{percent(metric.b3_precision)}</td><td>{percent(metric.b3_recall)}</td><td>{metric.overmerged_clusters}</td><td>{metric.largest_error_component}</td><td>{percent(metric.blocking_recall)}</td><td>{metric.brier:.3f}</td></tr>')
    review=[]
    rows=[]
    for ordinal,(pair,scores,stratum) in enumerate(selected,1):
        a,b=by_id[pair.left],by_id[pair.right]
        a_type=classify_name(a.observed_name or a.name,a.address)
        b_type=classify_name(b.observed_name or b.name,b.address)
        automatic_non_person=str(a_type.non_person or b_type.non_person).lower()
        evidence=[f'{name}={value:.2f}' for name,value in zip(FEATURES,pair.features) if value>0]
        review.append({'candidate_id':f'{pair.left}:{pair.right}','left':a.address,'right':b.address,'label':'','notes':''})
        shared_phones=sorted(set(a.phones)&set(b.phones))
        if shared_phones:
            evidence.append("Shared signature phone: "+", ".join(shared_phones))
        if pair.features[6]:
            evidence.append(f"Signature alternate-email occurrences: {a.signature_links.get(b.id,0)} A→B; {b.signature_links.get(a.id,0)} B→A")
        evidence.append("Name variants A: "+"; ".join(a.names))
        evidence.append("Name variants B: "+"; ".join(b.names))
        for name in sorted(set(a.names)&set(b.names)):
            evidence.append(f'Name frequency: {name} appears at {name_frequencies[name]:,} distinct addresses (rarity evidence, not proof)')
        evidence.append(f'Entity A: {a_type.kind}; '+"; ".join(a_type.reasons))
        evidence.append(f'Entity B: {b_type.kind}; '+"; ".join(b_type.reasons))
        score_text='<br>'.join(f'{escaped(name)}: {score:.4f}' for name,score in scores.items())
        rows.append(f'<tr data-review-row="{ordinal-1}" data-auto-non-person="{automatic_non_person}"><td>{ordinal}<br>{escaped(stratum)}</td><td><b>{escaped(a.observed_name or a.name or "No observed name")}</b><br>{escaped(a.address)}<br>{escaped(a.first[:10])} – {escaped(a.last[:10])}<br>{a.sent:,} sent · {escaped(a.kind)}</td><td><b>{escaped(b.observed_name or b.name or "No observed name")}</b><br>{escaped(b.address)}<br>{escaped(b.first[:10])} – {escaped(b.last[:10])}<br>{b.sent:,} sent · {escaped(b.kind)}</td><td>{"<br>".join(escaped(item) for item in evidence)}<br>Blocks: {escaped(", ".join(pair.blocks))}</td><td class="scores">{score_text}</td><td><select data-review="{ordinal-1}"><option value="">Unlabeled</option><option value="match">Same identity</option><option value="non-match">Different identities</option><option value="insufficient">Insufficient evidence</option></select><label class="entity-kind"><input type="checkbox" data-not-person="{ordinal-1}" aria-label="Not a person in pair {ordinal}"> Not a person</label><textarea data-note="{ordinal-1}" placeholder="Evidence / correction"></textarea></td></tr>')
    (output/'review-50.json').write_text(json.dumps(review,indent=2))
    log = output/'extraction.log'
    extraction=log.read_text() if log.exists() else 'Extraction time from extraction_runs; no external time log supplied.'
    source_status='Verified unchanged' if summary.source_unchanged else 'CHECK FAILED OR NOT COMPLETE'
    payload=json.dumps(review).replace('<','\\u003c')
    # Keep legacy seed fields stable: independent browser annotations must not change storage identity.
    review_fingerprint=hashlib.sha256((summary.archive+summary.created_at+json.dumps(review,sort_keys=True)).encode()).hexdigest()
    content=f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>Local name matching experiments — 21 October 2026</title><style>
body{{font:15px system-ui,sans-serif;color:#172536;background:#f4f7fb;margin:0}}main{{max-width:1500px;margin:auto;padding:32px}}h1{{font-size:36px}}h2{{margin-top:40px}}.notice{{background:#fff2cd;padding:18px;border-left:5px solid #b77b00}}.cards{{display:flex;gap:16px;flex-wrap:wrap}}.card{{background:white;padding:20px;border-radius:10px;min-width:170px}}.card b{{display:block;font-size:30px}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:10px;border-bottom:1px solid #d8e0eb;text-align:left;vertical-align:top}}th{{background:#e3ecf7;position:sticky;top:0}}td{{overflow-wrap:anywhere}}.scores{{font-size:11px;min-width:150px}}select,textarea,button{{font:inherit;padding:8px;max-width:100%;box-sizing:border-box}}.entity-kind{{display:block;margin-top:8px;min-width:180px}}textarea{{width:180px;height:70px;display:block;margin-top:6px}}button{{background:#175fc1;color:white;border:0;border-radius:6px;cursor:pointer}}.scroll{{overflow:auto}}code,pre{{font-size:12px}}pre{{white-space:pre-wrap;background:white;padding:12px}}a{{color:#175fc1}}.muted{{color:#596a7f}}</style>
<main><p>EMAIL COLLECTION TOOLKIT · PRIVATE LOCAL RESEARCH</p><h1>Name matching at full archive scale</h1><p>{escaped(summary.archive)} · generated {escaped(summary.created_at)}</p>
<div class="notice"><b>Real-archive accuracy is unmeasured.</b> The owner confirmed there is no independently verified pair set. No matcher score is an observed accuracy or a calibrated real-person probability. The 50 rows below are proposals for owner review, not 50 confirmed matches. No groups were applied.</div>
<div class="cards"><div class="card"><b>{summary.messages:,}</b>verified extracted messages</div><div class="card"><b>{summary.profiles:,}</b>addresses</div><div class="card"><b>{summary.frozen_candidates:,}</b>frozen candidate pairs</div><div class="card"><b>{summary.profiles_with_names:,}</b>addresses with usable human names</div></div>
<h2>Measured time and proposed links</h2><p>Full verified extraction: <b>{summary.extraction_seconds:.2f}s</b>; {summary.extraction_errors:,} extraction errors. Engine seconds measured locally with <code>perf_counter</code> and <code>process_time</code>. Fit and score are separate; neither includes extraction, profile preparation ({summary.preparation.wall_seconds:.2f}s), or common blocking ({summary.blocking.wall_seconds:.2f}s). Dedupe fit uses synthetic data. Results below use the predeclared exploratory cutoff 0.99; that cutoff has not met a real-world precision target. CPU includes native-library threads in this process.</p><div class="scroll"><table><tr><th>Strategy</th><th>Fit wall s</th><th>Score wall s</th><th>Fit+score CPU s</th><th>Accepted direct edges</th><th>Linked addresses</th><th>Largest component</th></tr>{''.join(engine_rows)}</table></div>
<h2>What ran, and what these strategies mean</h2><ul><li>Precision rules: exact observed multi-token name plus matching normalized local part. Lists/role/automated addresses are vetoed by explicit lexical heuristics.</li><li>Heuristic-signature ablation: adds a matching name plus explicit signature email or a low-frequency shared phone.</li><li>Repeated-signature ablation: adds compatible initial/surname plus an alternate email occurring in the same detected signature block in at least three distinct messages. This is repetition of detected blocks; it is not the planned general repeated-tail or supervised detector.</li><li>Collective graph: two passes propagate name-compatible links through correspondent neighborhoods projected through seeded components. Only the 100 strongest correspondents per address are retained.</li><li>Fellegi–Sunter: unsupervised binary-feature mixture over the frozen candidates. Assumptions include conditional feature independence; correlated names/local parts can inflate scores.</li><li>Dedupe: native learned linkage using dominant name, local part, domain and phone. Training labels are generated synthetic identities, including same-name and role-account negatives. This transfer is unvalidated.</li><li>Splink/DuckDB: native name/local-part similarity and exact domain/phone comparisons, random-pair u estimation and blocked EM. Its frozen prediction pairs match the shared candidate set. Training has a 200,000-pair cap and explicit prior; missing comparison levels are reported in the run log.</li></ul>
<p>Not run: a separately trained logistic model, full repeated-tail/supervised signature detection, temporal-feature ablation or handoff/shared-account change-point model, active owner-label selection study, local LLM reranking (no local model provisioned), cloud LLM batches (no disclosure authorization), or independent external-corpus evaluation. These are not represented by substitute measurements.</p>
<h2>Synthetic accuracy — explicitly not archive accuracy</h2><p>Generated train and test entities have disjoint name vocabularies and seeds. Test includes missing names, initials, exact same-name different people, and role accounts. Ground truth is declared by the generator. Pair P/R/F1 below is computed over the transitive predicted clusters; ungenerated true pairs count against recall. B³ scores count all test addresses, including singletons. Brier is evaluated on frozen candidate pairs only. Thresholds were fixed before this run. This synthetic stress test cannot validate private-archive precision, recall or calibration.</p><div class="scroll"><table><tr><th>Strategy</th><th>Cutoff</th><th>Pair P</th><th>Pair R</th><th>Pair F1</th><th>B³ P</th><th>B³ R</th><th>Overmerged clusters</th><th>Largest error component</th><th>Blocking recall</th><th>Brier</th></tr>{''.join(synthetic_rows)}</table></div>
<h2>50 representative proposed matches</h2><p>Seed {SEED}; deterministic stratification across consensus, signature support, cross-domain names, disagreement and uncertain proposals. Each address appears at most twice. This is an illustrative, selected review set; its accuracy cannot be extrapolated to the entire archive. Labels are saved in this browser locally and can be exported for evaluation. Judge whether the addresses represent the same identity, including people or organizations. Independently check “Not a person” when A, B or both are an organization, list, service or role account; identify which in the notes. You can combine “Same identity” with “Not a person”. The non-person flag is not a different-identity judgment. “Insufficient evidence” must not be counted as a pair negative.</p><p>Recognized service/organization identities are hidden by default using an offline adaptation of SBook5 codewords and gazetteers; uncertain names stay visible. Your non-person flags also filter rows. These display filters do not change the frozen scores, pair numbers or saved labels.</p><label><input id="show-non-person" type="checkbox"> Show non-person identities</label> <span id="visible-count"></span><br><button id="export">Download my labels</button> <span id="progress"></span><div class="scroll"><table><tr><th># / stratum</th><th>Identity A</th><th>Identity B</th><th>Evidence</th><th>Engine scores</th><th>Your decision</th></tr>{''.join(rows)}</table></div>
<h2>Reproducibility and preservation</h2><p>Archive source: <b>{source_status}</b>. Full extraction verifies each message's SHA-256 against canonical MBOX bytes. The existing catalog contributes dates and header graph edges. Evidence and output are local derivatives with owner-only permissions; outputs remain outside the archive. Block cap {summary.block_summary.block_limit}; {summary.block_summary.skipped_blocks:,} common blocks skipped ({summary.block_summary.skipped_memberships:,} memberships). Candidate SHA-256 <code>{summary.candidate_sha256}</code>. This development archive has been inspected before and is not a final untouched test corpus.</p><p>Process high-water RSS: {summary.process_peak_rss_bytes/1024**3:.2f} GiB (whole run; not independent per-engine peaks). Versions: {escaped(json.dumps(summary.versions))}.</p><details><summary>Extraction timing and diagnostics</summary><pre>{escaped(extraction)}</pre></details>
<p>Engine documentation: <a href="https://docs.dedupe.io/en/latest/API-documentation.html">Dedupe</a>; <a href="https://moj-analytical-services.github.io/splink/api_docs/training.html">Splink training</a>. Their documentation does not establish this archive's accuracy. Entity codewords: <a href="https://github.com/simsong/SBook5/blob/d88ee99496e546ec75228b87298a6b1e19de0ff4/libsbook/parse_company.fp">SBook5</a>; this is a conservative whole-word adaptation, not its full scanner.</p></main>
<script>const initial={payload}; const key='name-review-{summary.candidate_sha256[:16]}-{review_fingerprint[:16]}';let stored;try{{stored=JSON.parse(localStorage.getItem(key))}}catch{{stored=[]}}
const previous=new Map((Array.isArray(stored)?stored:[]).filter(row=>row&&typeof row==='object').map(row=>[row.candidate_id,row]));const rows=initial.map(row=>{{const old=previous.get(row.candidate_id);return old&&old.left===row.left&&old.right===row.right?{{...row,label:['match','non-match','insufficient'].includes(old.label)?old.label:'',not_person:old.not_person===true||old.label==='not-person',notes:typeof old.notes==='string'?old.notes:''}}:{{...row,not_person:false}}}});
const showNonPerson=document.getElementById('show-non-person');showNonPerson.checked=localStorage.getItem(key+'-show-non-person')==='true';
function filterRows(){{let visible=0;document.querySelectorAll('[data-review-row]').forEach(e=>{{const i=Number(e.dataset.reviewRow);e.hidden=!showNonPerson.checked&&(e.dataset.autoNonPerson==='true'||rows[i].not_person);if(!e.hidden)visible++}});document.getElementById('visible-count').textContent=visible+' / 50 pairs visible'}}
showNonPerson.addEventListener('change',()=>{{localStorage.setItem(key+'-show-non-person',String(showNonPerson.checked));filterRows()}});
function save(){{filterRows();localStorage.setItem(key,JSON.stringify(rows));document.getElementById('progress').textContent=rows.filter(r=>r.label).length+' / 50 identity decisions · '+rows.filter(r=>r.not_person).length+' non-person flags'}}
document.querySelectorAll('[data-review]').forEach(e=>{{const i=Number(e.dataset.review);e.value=rows[i].label;e.addEventListener('change',()=>{{rows[i].label=e.value;save()}})}});document.querySelectorAll('[data-not-person]').forEach(e=>{{const i=Number(e.dataset.notPerson);e.checked=rows[i].not_person;e.addEventListener('change',()=>{{rows[i].not_person=e.checked;save()}})}});document.querySelectorAll('[data-note]').forEach(e=>{{const i=Number(e.dataset.note);e.value=rows[i].notes;e.addEventListener('input',()=>{{rows[i].notes=e.value;save()}})}});document.getElementById('export').onclick=()=>{{const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([JSON.stringify(rows,null,2)],{{type:'application/json'}}));link.download='name-matcher-owner-labels.json';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000)}};save();</script></html>'''
    (output/'report.html').write_text(content)


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--report-only',action='store_true')
    args=parser.parse_args()
    archive,evidence,output=args.archive.resolve(),args.evidence.resolve(),args.output.resolve()
    if output.is_relative_to(archive) or evidence.is_relative_to(archive):
        raise ValueError('Experiment outputs must be outside the archive')
    output.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.umask(0o077)
    database=output/'experiments.sqlite3'
    if args.report_only:
        summary=RunSummary.model_validate_json((output/'summary.json').read_text())
        render_report(summary,load_profiles(database),output)
        return
    if database.exists():
        raise FileExistsError(f'Refusing to overwrite {database}; use a new experiment output')
    before = output/'source-before.json'
    if not before.exists():
        paths=[archive/'archive.sqlite3',archive/'search.sqlite3',*sorted((archive/'data').rglob('*.mbox'))]
        with (archive/'archive.sqlite3').open('rb') as stream:
            catalog_hash=hashlib.file_digest(stream,'sha256').hexdigest()
        before.write_text(json.dumps({'archive':str(archive),'catalog_sha256':catalog_hash,'files':[
            {'path':str(path.relative_to(archive)),'size':path.stat().st_size,'mtime_ns':path.stat().st_mtime_ns} for path in paths]}))
    scope=validate_evidence(archive,evidence)
    message_count,error_count,extraction_seconds=scope.messages,scope.errors,scope.extraction_seconds
    log=output/'extraction.log'
    if log.exists() and (match:=re.search(r'^\s*(\d+\.\d+)\s+real\s',log.read_text(),re.MULTILINE)):
        extraction_seconds=float(match.group(1))
    manifest={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(Path(__file__).parent.glob('*.py'))}
    (output/'code-manifest.json').write_text(json.dumps(manifest,indent=2))
    profiles,preparation=timed(lambda:prepare_profiles(archive,evidence,database))
    (pairs,blocking_summary),blocking=timed(lambda:candidates(profiles))
    fingerprint=save_pairs(database,pairs)
    summary=RunSummary(created_at=datetime.now(UTC).isoformat(),archive=str(archive),evidence=str(evidence),messages=message_count,extraction_errors=error_count,extraction_seconds=extraction_seconds,profiles=len(profiles),
        profiles_with_names=sum(bool(profile.names) for profile in profiles),frozen_candidates=len(pairs),
        candidate_sha256=fingerprint,preparation=preparation,blocking=blocking,block_summary=blocking_summary,
        versions={name:importlib.metadata.version(name) for name in ('dedupe','splink','duckdb','numpy','scikit-learn')})
    run_engines(summary,profiles,pairs,output)
    summary.process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    summary.source_unchanged=verify_source(output,archive)
    (output/'summary.json').write_text(summary.model_dump_json(indent=2))
    render_report(summary,profiles,output)
    print('Report:',output/'report.html',flush=True)


if __name__=='__main__':
    main()
