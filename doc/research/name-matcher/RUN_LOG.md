<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Experimental Run Log

This tracked log contains aggregate reproducibility evidence only. Private
databases, message text, signature text, addresses, names, and request payloads
remain in ignored local storage.

## 2026-10-21 — full-archive local comparison

### Follow-up: SBook filtering, name frequency and owner review

Pinned SBook5 source commit `d88ee99496e546ec75228b87298a6b1e19de0ff4`
supplies original company/address Flex rules, company expressions and first names,
with its MIT notice. Upstream Git blob IDs were verified byte-for-byte. The
adaptation uses whole-word lexical rules only and retains person/company
conflicts as unknown. Classifications filter views reversibly and do not become
owner truth or modify the original fifty pairs.

A separate private full comparison reused the verified extraction and retained
exactly the same 197,479 candidates and candidate SHA-256. The new native Splink
full-name-frequency variant fit in 0.543 s and scored in 0.413 s, proposing 7,813
direct edges at 0.99, versus 1,883 for the unadjusted baseline. An independent
synthetic real-engine regression confirms that otherwise-equal rare exact names
receive more evidence than common names. Source preservation checks passed.

The owner's 50-case export contained 33 match, five non-match, six legacy
not-person and six blank link labels. Fourteen rows had an explicit non-person
label or written annotation. The export predates independent checkbox fields;
notes are retained, and same-organization notes are not converted into labels.

At 0.99 on the **38 adjudicated, selected identity pairs**, the signature rules
found 24/33 positives with 0/5 false links; Fellegi–Sunter found 31/33 with 2/5
false links; unadjusted Splink found 5/33 with 0/5 false links; frequency-adjusted
Splink found 10/33 with 0/5 false links. These are descriptive review-set results,
including some non-person identities, not population accuracy or confirmed
person-only metrics. An additional report excludes explicit owner non-person
annotations without treating machine classifications as ground truth.

### Original seven-strategy run

Input was the user-designated largest archive, opened read-only. Four extraction
workers verified all 1,200,791 messages across 81 MBOX generations (39,722,019,429
bytes), with zero extraction errors. The catalog contained 219,074 addresses;
104,005 profiles had usable human names. Header observations numbered 3,054,732;
526,168 detected signature blocks yielded 2,983,782 candidate facts. These are
detector outputs, not verified identity labels. Source size/time checks and the
catalog SHA-256 were unchanged after the run.

Command shapes: `make name-matcher-observations ARCHIVE=... OUTPUT=... ARGS='--workers 4'`,
then `make name-matcher-experiments ARCHIVE=... EVIDENCE=... OUTPUT=...`.
Private evidence, scores, logs and the 50-case HTML review report remain under
the ignored `.tmp/name-experiments-20261021/` directory, outside the archive.

| Shared stage | Wall seconds |
| --- | ---: |
| Verified full extraction | 577.34 |
| Profile preparation | 30.954 |
| Frozen candidate generation | 4.894 |

There were 197,479 frozen pairs; blocks larger than 25 were excluded (272 blocks,
31,763 memberships). Candidate SHA-256:
`b32bcf94b70da3df557892fda9ef7d8eb44e676de142e45b662c639e8fc9ec93`.
All native engines completed without error. Times below exclude shared stages;
direct edges use the exploratory cutoff 0.99, not a verified precision target.

| Strategy | Fit wall s | Score wall s | Proposed direct edges | Largest component |
| --- | ---: | ---: | ---: | ---: |
| No links | 0 | 0.003 | 0 | 1 |
| Name/local rules | 0 | 0.017 | 12,156 | 78 |
| Add heuristic signatures | 0 | 0.020 | 13,116 | 186 |
| Add repeated detected signatures | 0 | 0.022 | 13,143 | 211 |
| Collective correspondent graph | 0 | 0.686 | 14,825 | 217 |
| Fellegi–Sunter EM | 0.147 | 0.313 | 33,457 | 132 |
| Dedupe | 7.528 | 1.918 | 0 | 1 |
| Splink/DuckDB | 0.946 | 0.439 | 1,883 | 23 |

The complete comparison command took 63.95 wall seconds, including synthetic
evaluation and report generation. Its process high-water RSS was 1.50 GiB;
independent per-engine memory peaks and extraction memory were not measured.
Versions: Dedupe 3.0.3, Splink 5.0.0, DuckDB 1.5.6, NumPy 2.5.4,
scikit-learn 1.9.1; BTrees is pinned below 6.5 for Dedupe compatibility.

**Real-archive precision and recall are unmeasured.** The owner confirmed no
independent labels exist. The report offers 50 selected proposals with engine
scores, observed names/dates and signature/phone evidence for owner review.
This selected set cannot estimate population accuracy, and no groups were applied.

Synthetic evaluation used disjoint train/test name vocabularies and declared
identity labels, including same-name different people and role-account negatives.
Pair metrics include transitive cluster errors and missed true pairs. At 0.99:

| Strategy | Synthetic pair precision | Synthetic pair recall |
| --- | ---: | ---: |
| Name/local rules | 40.0% | 12.5% |
| Add heuristic signatures | 66.7% | 41.7% |
| Add repeated detected signatures | 25.5% | 87.5% |
| Collective correspondent graph | 25.5% | 87.5% |
| Fellegi–Sunter EM | 100.0% | 100.0% |
| Dedupe | undefined (no links) | 0.0% |
| Splink/DuckDB | 100.0% | 12.5% |

These generator-dependent results are not archive accuracy. In particular, the
perfect synthetic EM result does not establish real calibration. Dedupe's
synthetic-trained scores produced 1,018 archive proposals at cutoff 0.5 and none
at 0.9 or above. Splink's synthetic precision/recall was 92.3%/100.0% at 0.9.
The report includes every predeclared threshold (0.5, 0.9, 0.99, 0.999), B³,
overmerge/component diagnostics and candidate-only Brier scores.

The comparison covers runnable baseline strategies. General repeated-tail or
supervised signature detection, a separate logistic model, temporal ablations,
handoff/shared-account inference, active labeling, local/cloud LLMs and external
corpus evaluation remain unrun and are identified as such in the report.

## 2026-09-03 — extraction smoke test

- Branch: `research-name-matching`
- Base after final synchronization: `origin/main` at `499ae6f`
- Input: Simson development archive, read-only
- Selection: first 2,000 catalogued messages allocated in filename order across
  13 MBOX generations; this is a path/integrity smoke test, not a random or
  representative evaluation sample
- Workers: 4 process workers, independent SQLite shards
- Extractor: `mailarchiver-name-observations` 0.1.0, heuristic signature v1
- Command shape: `make name-matcher-observations ARCHIVE=... OUTPUT=... ARGS='--limit 2000 --workers 4'`

| Measure | Value |
|---|---:|
| Messages | 2,000 |
| Extraction errors | 0 |
| Header observations | 4,840 |
| Transport/list markers | 261 |
| Distinct addresses | 1,331 |
| Distinct nonempty normalized names | 464 |
| Signature candidates | 428 |
| Signature facts | 2,712 |

Signature candidate methods were 333 contact blocks, 78 delimiter blocks, and
17 signoffs. Facts comprised 360 email occurrences, 280 phone occurrences, 124
name candidates, one URL occurrence, and 1,947 retained text lines. These are
unadjudicated detector outputs, not precision or recall results.

The combined database and YAML summary were both mode `0600`; SQLite foreign-key
validation returned no violations. Each canonical message was accepted only
after SHA-256 verification through the existing MBOX location reader.

Post-sync focused validation: `make test-name-resolution` reported 6 passed;
`make pylint` rated the tree 10.00/10; `git diff --check` reported no errors.
Before the final main fast-forward, the full unit suite reported 234 passed and
one skipped (the optional local 86 MB encoding fixture was absent). This full
suite result is recorded as pre-sync and is not substituted for post-sync
focused validation.
