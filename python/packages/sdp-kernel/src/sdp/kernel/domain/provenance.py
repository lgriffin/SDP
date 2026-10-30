"""A PROV-aligned evidence graph builder (ADR-009). Pure: it produces PROV-JSON documents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

PREFIXES = {"sdp": "https://github.com/lgriffin/SDP/ns#", "prov": "http://www.w3.org/ns/prov#"}
RELATIONS = ("used", "wasGeneratedBy", "wasAssociatedWith", "wasDerivedFrom", "wasAttributedTo")
_RELATION_KEYS = {
    "used": ("prov:activity", "prov:entity"),
    "wasGeneratedBy": ("prov:entity", "prov:activity"),
    "wasAssociatedWith": ("prov:activity", "prov:agent"),
    "wasDerivedFrom": ("prov:generatedEntity", "prov:usedEntity"),
    "wasAttributedTo": ("prov:entity", "prov:agent"),
}


def qualified(identifier: str) -> str:
    """Prefix SDP identifiers (``sha256:…``, ``experiment/…``) with ``sdp:``; keep declared QNames."""
    prefix, sep, _ = identifier.partition(":")
    return identifier if sep and prefix in PREFIXES else f"sdp:{identifier}"


@dataclass
class ProvGraph:
    """Entities, activities, agents and the five relations SDP records."""

    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    activities: dict[str, dict[str, Any]] = field(default_factory=dict)
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    relations: dict[str, list[tuple[str, str]]] = field(default_factory=lambda: {r: [] for r in RELATIONS})
    #: each relation's pairs as a set (with the list length it was built from), so adding one is not a scan
    #: of the list; rebuilt when the list changed behind its back (loaders append to ``relations`` directly)
    _seen: dict[str, tuple[int, set[tuple[str, str]]]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def entity(self, identifier: str, **attributes: Any) -> str:
        key = qualified(identifier)
        self.entities.setdefault(key, {}).update({f"sdp:{k}" if ":" not in k else k: v for k, v in attributes.items()})
        return key

    def activity(self, identifier: str, **attributes: Any) -> str:
        key = qualified(identifier)
        self.activities.setdefault(key, {}).update(
            {f"sdp:{k}" if ":" not in k else k: v for k, v in attributes.items()}
        )
        return key

    def agent(self, identifier: str, **attributes: Any) -> str:
        key = qualified(identifier)
        self.agents.setdefault(key, {}).update({f"sdp:{k}" if ":" not in k else k: v for k, v in attributes.items()})
        return key

    def relate(self, relation: str, subject: str, obj: str) -> None:
        if relation not in _RELATION_KEYS:
            raise ValueError(f"unknown PROV relation {relation}")
        self._add(relation, (qualified(subject), qualified(obj)))

    def _add(self, relation: str, pair: tuple[str, str]) -> None:
        pairs = self.relations[relation]
        built_from, seen = self._seen.get(relation, (-1, set()))
        if built_from != len(pairs):
            seen = set(pairs)
        if pair not in seen:
            pairs.append(pair)
            seen.add(pair)
        self._seen[relation] = (len(pairs), seen)

    def merge(self, other: ProvGraph) -> None:
        for key, attrs in other.entities.items():
            self.entities.setdefault(key, {}).update(attrs)
        for key, attrs in other.activities.items():
            self.activities.setdefault(key, {}).update(attrs)
        for key, attrs in other.agents.items():
            self.agents.setdefault(key, {}).update(attrs)
        for relation, pairs in other.relations.items():
            for pair in pairs:
                self._add(relation, pair)

    def ancestors(self, identifier: str) -> list[str]:
        """Every entity and activity upstream of ``identifier``, nearest first."""
        start = qualified(identifier)
        edges: dict[str, list[str]] = {}
        for entity, activity in self.relations["wasGeneratedBy"]:
            edges.setdefault(entity, []).append(activity)
        for activity, entity in self.relations["used"]:
            edges.setdefault(activity, []).append(entity)
        for generated, used in self.relations["wasDerivedFrom"]:
            edges.setdefault(generated, []).append(used)
        seen: list[str] = []
        frontier = [start]
        while frontier:
            nxt: list[str] = []
            for node in frontier:
                for parent in edges.get(node, []):
                    if parent not in seen and parent != start:
                        seen.append(parent)
                        nxt.append(parent)
            frontier = nxt
        return seen

    def subgraph(self, activity_id: str) -> ProvGraph:
        """The activity, everything it used, generated or was associated with, and the derivation
        ancestry of those entities: what a manifest needs to explain itself."""
        root = qualified(activity_id)
        out = ProvGraph()
        if root in self.activities:
            out.activities[root] = dict(self.activities[root])
        touched: set[str] = {root}
        for relation, pairs in self.relations.items():
            for a, b in pairs:
                if root in (a, b):
                    out.relations[relation].append((a, b))
                    touched.update((a, b))
        # entities this activity generated or used: follow their derivations and generators upstream
        frontier = [n for n in touched if n in self.entities]
        seen = set(frontier)
        while frontier:
            nxt: list[str] = []
            for node in frontier:
                for relation in ("wasDerivedFrom", "wasGeneratedBy", "wasAttributedTo"):
                    for a, b in self.relations[relation]:
                        if a == node:
                            if (a, b) not in out.relations[relation]:
                                out.relations[relation].append((a, b))
                            touched.add(b)
                            if b not in seen and b in self.entities:
                                seen.add(b)
                                nxt.append(b)
            frontier = nxt
        for node in touched:
            if node in self.entities:
                out.entities[node] = dict(self.entities[node])
            if node in self.activities:
                out.activities[node] = dict(self.activities[node])
            if node in self.agents:
                out.agents[node] = dict(self.agents[node])
        return out

    def to_prov_json(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "prefix": dict(PREFIXES),
            "entity": {k: dict(v) for k, v in sorted(self.entities.items())},
            "activity": {k: dict(v) for k, v in sorted(self.activities.items())},
            "agent": {k: dict(v) for k, v in sorted(self.agents.items())},
        }
        for relation in RELATIONS:
            first, second = _RELATION_KEYS[relation]
            doc[relation] = {
                f"_:{relation[0:3]}{i}": {first: a, second: b} for i, (a, b) in enumerate(self.relations[relation])
            }
        return doc

    @classmethod
    def from_prov_json(cls, doc: Mapping[str, Any]) -> ProvGraph:
        graph = cls()
        graph.entities = {k: dict(v) for k, v in doc.get("entity", {}).items()}
        graph.activities = {k: dict(v) for k, v in doc.get("activity", {}).items()}
        graph.agents = {k: dict(v) for k, v in doc.get("agent", {}).items()}
        for relation in RELATIONS:
            first, second = _RELATION_KEYS[relation]
            graph.relations[relation] = [(r[first], r[second]) for r in doc.get(relation, {}).values()]
        return graph
