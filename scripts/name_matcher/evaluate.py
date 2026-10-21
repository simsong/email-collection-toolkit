# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
# Evaluate exported owner decisions against an existing frozen comparison.
# Validate stable candidate IDs and addresses before joining any saved score.
# Keep identity-link decisions separate from non-person annotations and notes.
# Report selected-sample metrics without extrapolating to archive-wide accuracy.
# Read the labels and experiment database only; write private derived reports.
# Neither this evaluator nor its HTML applies groups or trains a matcher.
"""Owner-adjudicated, selected-sample linkage diagnostics."""
from __future__ import annotations

import argparse
import html
import json
import os
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, TypeAdapter

from .linkage import Profile
from .experiments import read_only


class Decision(BaseModel):
    candidate_id: str
    left: str
    right: str
    label: Literal['','match','non-match','not-person','insufficient'] = ''
    not_person: bool = False
    notes: str = ''

    @property
    def explicit_non_person(self) -> bool:
        # Preserve the distinction between a checkbox, an old label and a written annotation.
        return self.not_person or self.label=='not-person' or 'not a person' in self.notes.casefold()


class Assessment(BaseModel):
    engine: str
    threshold: float
    judged: int
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int
    precision: float | None
    recall: float | None


class OwnerEvaluation(BaseModel):
    rows: int
    label_counts: dict[str,int]
    explicit_non_person: int
    all_identity_results: list[Assessment]
    owner_person_subset_results: list[Assessment]


def evaluate(database: Path, labels: Path) -> OwnerEvaluation:
    decisions=TypeAdapter(list[Decision]).validate_json(labels.read_text())
    if not decisions or len({row.candidate_id for row in decisions})!=len(decisions):
        raise ValueError('Owner decisions must be nonempty and have unique candidate IDs')
    scores: dict[str,list[tuple[Decision,float]]] = {}
    with read_only(database) as connection:
        for row in decisions:
            parts=row.candidate_id.split(':')
            if len(parts)!=2 or not all(part.isdecimal() for part in parts):
                raise ValueError('Malformed candidate ID')
            left,right=map(int,parts)
            candidate=connection.execute('SELECT id FROM candidates WHERE left_id=? AND right_id=?',(left,right)).fetchone()
            profiles=[connection.execute('SELECT value FROM profiles WHERE id=?',(ident,)).fetchone() for ident in (left,right)]
            if not candidate or any(profile is None for profile in profiles):
                raise ValueError('Owner candidate is absent from frozen experiment')
            addresses=[Profile.model_validate_json(profile[0]).address for profile in profiles if profile is not None]
            if addresses!=[row.left,row.right]:
                raise ValueError('Owner candidate address mapping does not match experiment')
            for engine,score in connection.execute('SELECT engine,score FROM scores WHERE pair_id=?',(candidate[0],)):
                scores.setdefault(engine,[]).append((row,score))
    def assess(engine: str, values: list[tuple[Decision,float]], threshold: float, people_only: bool) -> Assessment:
        counts=Counter((row.label=='match',score>=threshold) for row,score in values
            if row.label in ('match','non-match') and not (people_only and row.explicit_non_person))
        tp,fp,fn,tn=(counts[True,True],counts[False,True],counts[True,False],counts[False,False])
        return Assessment(engine=engine,threshold=threshold,judged=tp+fp+fn+tn,true_positive=tp,false_positive=fp,
            false_negative=fn,true_negative=tn,precision=tp/(tp+fp) if tp+fp else None,recall=tp/(tp+fn) if tp+fn else None)
    for values in scores.values():
        if len(values)!=len(decisions):
            raise ValueError('Incomplete saved scores for owner review set')
    return OwnerEvaluation(rows=len(decisions),label_counts=dict(Counter(row.label for row in decisions)),
        explicit_non_person=sum(row.explicit_non_person for row in decisions),
        all_identity_results=[assess(engine,values,threshold,False) for engine,values in sorted(scores.items()) for threshold in (0.5,0.9,0.99)],
        owner_person_subset_results=[assess(engine,values,threshold,True) for engine,values in sorted(scores.items()) for threshold in (0.5,0.9,0.99)])


def write_report(result: OwnerEvaluation, output: Path) -> None:
    def table(values: list[Assessment]) -> str:
        rows=[]
        for row in values:
            precision='—' if row.precision is None else f'{row.precision:.1%}'
            recall='—' if row.recall is None else f'{row.recall:.1%}'
            rows.append(f'<tr><td>{html.escape(row.engine)}</td><td>{row.threshold:g}</td><td>{row.judged}</td><td>{precision}</td><td>{recall}</td><td>{row.false_positive}</td><td>{row.false_negative}</td></tr>')
        return '<table><tr><th>Engine</th><th>Cutoff</th><th>Judged pairs</th><th>Precision</th><th>Recall</th><th>False links</th><th>Missed links</th></tr>'+''.join(rows)+'</table>'
    output.with_suffix('.json').write_text(result.model_dump_json(indent=2))
    output.write_text('<!doctype html><meta charset="utf-8"><title>Owner review evaluation</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #ccc;text-align:left}</style>'
        '<h1>Your 50-case review: selected-sample diagnostics</h1><p>These pairs were selected as illustrative matcher proposals. Results are descriptive for this review set; they do not establish archive-wide precision, recall or entity-classification accuracy. Unknown and insufficient decisions are excluded. No labels were used to fit these models.</p>'
        f'<p>Rows: {result.rows}; labels: {html.escape(json.dumps(result.label_counts))}; explicitly non-person: {result.explicit_non_person}.</p>'
        '<h2>All adjudicated identity links, including services/organizations</h2>'+table(result.all_identity_results)+
        '<h2>Excluding owner-annotated non-person cases</h2><p>Excludes checkbox flags, legacy not-person choices and notes explicitly saying “not a person”. Remaining rows are not independently confirmed to be people; automated predictions were not used as ground truth. A same-organization comment alone is not converted into a same-identity label.</p>'+table(result.owner_person_subset_results))


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--labels',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    database,labels,output=args.database.resolve(),args.labels.resolve(),args.output.resolve()
    destinations=(output,output.with_suffix('.json'))
    if any(path in (database,labels) or path.exists() for path in destinations):
        raise ValueError('Report outputs must be new files distinct from inputs')
    if any((parent/'archive.sqlite3').exists() for parent in output.parents):
        raise ValueError('Owner reports must be outside the canonical archive')
    os.umask(0o077)
    output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    write_report(evaluate(database,labels),output)


if __name__=='__main__':
    main()
