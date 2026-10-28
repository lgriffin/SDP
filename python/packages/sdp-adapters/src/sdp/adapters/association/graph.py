"""Assemble an AssociationGraph from per-option posteriors. Holds no probability logic."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sdp.kernel.domain.association import (
    Association,
    AssociationGraph,
    AssociationPolicy,
    AssociationRecord,
    ObjectHypothesis,
)
from sdp.kernel.science.association import (
    CandidateOption,
    RecordOptions,
    candidate_options,
    field_density_per_arcsec2,
    is_anchor,
)


@dataclass(frozen=True)
class Scored:
    """One option's numbers from a method: prior, likelihood, Bayes factor (if any), posterior."""

    option: CandidateOption | None
    """``None`` is the no-counterpart option: the record is its own object."""
    prior: float
    likelihood: float
    bayes_factor: float | None
    posterior: float


Scorer = Callable[[RecordOptions, float, AssociationPolicy], Sequence[Scored]]


def hypothesis_for(anchor_or_record_id: str) -> str:
    return f"obj:{anchor_or_record_id}"


def build_graph(
    records: Sequence[AssociationRecord],
    policy: AssociationPolicy,
    *,
    method: str,
    method_version: str,
    score: Scorer,
) -> AssociationGraph:
    density = field_density_per_arcsec2(records, policy)
    rows: list[tuple[str, str, Scored, dict[str, Any]]] = []
    for ro in candidate_options(records, policy):
        rid = ro.record.record_id
        if is_anchor(ro.record, policy):
            rows.append((rid, hypothesis_for(rid), Scored(None, 1.0, 1.0, None, 1.0), {"role": "anchor"}))
            continue
        for s in score(ro, density, policy):
            if s.posterior < policy.min_posterior:
                continue
            if s.option is None:
                rows.append((rid, hypothesis_for(rid), s, {"role": "no-counterpart"}))
            elif s.option.is_blend:
                share = s.posterior / len(s.option.members)
                for m in s.option.members:
                    split = Scored(s.option, s.prior, s.likelihood, s.bayes_factor, share)
                    rows.append(
                        (
                            rid,
                            hypothesis_for(m),
                            split,
                            {"role": "blend", "blend_of": list(s.option.members), "option_posterior": s.posterior},
                        )
                    )
            else:
                rows.append((rid, hypothesis_for(s.option.members[0]), s, {"role": "counterpart"}))
    members: dict[str, set[str]] = {}
    for rid, hid, _, _ in rows:
        members.setdefault(hid, set()).add(rid)
    by_record: dict[str, list[str]] = {}
    for rid, hid, _, _ in rows:
        by_record.setdefault(rid, []).append(hid)
    associations = tuple(
        Association(
            record_id=rid,
            hypothesis_id=hid,
            method=method,
            method_version=method_version,
            prior=min(1.0, max(0.0, s.prior)),
            likelihood=s.likelihood,
            posterior=min(1.0, max(0.0, s.posterior)),
            bayes_factor=s.bayes_factor,
            competing=tuple(sorted(h for h in by_record[rid] if h != hid)),
            evidence={**ev, "density_per_arcsec2": density, "common_epoch_jyear": _epoch(records, rid)},
        )
        for rid, hid, s, ev in rows
    )
    return AssociationGraph(
        policy=policy,
        method=method,
        method_version=method_version,
        records=tuple(sorted(records, key=lambda r: r.record_id)),
        hypotheses=tuple(ObjectHypothesis(h, tuple(sorted(m))) for h, m in sorted(members.items())),
        associations=tuple(sorted(associations, key=lambda a: (a.record_id, -a.posterior, a.hypothesis_id))),
        source_density_per_arcsec2=density,
    )


def _epoch(records: Sequence[AssociationRecord], record_id: str) -> float:
    return next(r.epoch_jyear for r in records if r.record_id == record_id)
