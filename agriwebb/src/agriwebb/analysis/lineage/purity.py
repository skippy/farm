"""Lineage-derived breed purity.

Why this exists
---------------
AgriWebb's per-animal breed is a pick from a fixed global list (no custom
breeds, no "Finnish Landrace X" entry), so on-farm the "Finish Landrace" label
(AgriWebb's spelling) is also used for predominantly-Finn crosses.  The label
therefore cannot answer "is this animal purebred?".

This module answers it from *recorded parentage* instead:

* Walk each animal's sires/dams recursively until reaching a **founder** --
  an animal with no recorded parents, or an off-cache parent that exists only
  as a name/VID string in ``parentAnimalIdentity`` (``parentAnimalId`` is
  ``null`` for those).
* Founders' breed status is asserted by the shepherd in ``founders.json``
  (sibling of this file).  Each entry is ``purebred: true`` (100 % one breed),
  ``purebred: false`` (optionally with a ``composition`` of fractions), or
  ``null`` (not yet classified).
* Composition = average of the two parents' compositions.  Any unclassified
  founder or missing parent contributes ``"unknown"``.

Verdicts
--------
``purebred`` (tri-state) is the single source of truth; ``status`` is derived:

* ``True`` / ``"verified"``   -- every founder resolved and purebred of one breed
* ``False`` / ``"mixed"``     -- at least one founder is a known cross or two
  breeds meet anywhere in the tree (conclusive even if other founders are
  unclassified)
* ``None`` / ``"unverified"`` -- otherwise (unclassified founders / parentage gaps)

External founders are matched by normalised name first, then VID, against the
registry ``id`` and ``aliases`` (case/punctuation-insensitive -- "Blue Man",
"Blueman" and "BLUEMAN" all match).  Cached animals can be pinned by
``animalId``; a registry entry always wins over recorded parents.

Assumption: one sire and one dam per animal.  If more than one parent is
recorded for a role (e.g. an unresolved multi-ram joining), their compositions
are averaged as a best estimate *and* a gap is recorded, so the verdict can
never be ``verified`` on ambiguous parentage.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from agriwebb.analysis.lambing.loader import get_breed, get_name

DEFAULT_REGISTRY_PATH = Path(__file__).with_name("founders.json")

UNKNOWN = "unknown"

Role = Literal["sire", "dam"]

# AgriWebb label -> canonical breed name used in compositions.
_CANONICAL_BREEDS = {
    "finish landrace": "Finnish Landrace",
    "finnish landrace": "Finnish Landrace",
    "finnsheep": "Finnish Landrace",
    "finn": "Finnish Landrace",
    "north country cheviot": "North Country Cheviot",
    "ncc": "North Country Cheviot",
    "bluefaced leicester": "Bluefaced Leicester",
    "bfl": "Bluefaced Leicester",
}

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def normalize_key(value: str | None) -> str:
    """Uppercase and strip everything but letters/digits: ``'Blue Man'`` -> ``'BLUEMAN'``."""
    if not value:
        return ""
    return _NON_ALNUM.sub("", value.upper())


def canonical_breed(label: str | None) -> str:
    """Map an AgriWebb breed label (incl. the 'Finish Landrace' typo) to a canonical name."""
    if not label:
        return "?"
    return _CANONICAL_BREEDS.get(label.strip().lower(), label.strip())


# ---------------------------------------------------------------------------
# Founder registry
# ---------------------------------------------------------------------------


class RegistryError(ValueError):
    """Raised for an invalid or self-contradictory founders.json."""


@dataclass
class Founder:
    """A shepherd-asserted breed status for a founder animal.

    ``id`` is the human label (and primary match key for external founders).
    ``animal_id`` pins a cached animal.  ``purebred`` is ``None`` until classified.
    Invariants (checked at construction, i.e. at registry load):
    ``purebred=True`` requires ``breed``; ``composition`` fractions are >= 0.
    """

    id: str
    animal_id: str | None = None
    aliases: list[str] = field(default_factory=list)
    breed: str | None = None
    purebred: bool | None = None
    composition: dict[str, float] | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not normalize_key(self.id):
            raise RegistryError("founder entry is missing a usable 'id'")
        if self.purebred is True and not self.breed:
            raise RegistryError(f"founder {self.id!r}: purebred=true requires 'breed'")
        if self.composition and any(v < 0 for v in self.composition.values()):
            raise RegistryError(f"founder {self.id!r}: composition fractions must be >= 0")

    @property
    def resolved(self) -> bool:
        return self.purebred is not None

    def match_keys(self) -> set[str]:
        return {normalize_key(self.id), *(normalize_key(a) for a in self.aliases)} - {""}

    def get_composition(self) -> dict[str, float]:
        """Breed fractions this founder contributes (``{"unknown": 1.0}`` when unclassified)."""
        if self.purebred is True and self.breed:
            return {canonical_breed(self.breed): 1.0}
        if self.purebred is False and self.composition:
            total = sum(self.composition.values())
            if total > 0:
                return {canonical_breed(k): v / total for k, v in self.composition.items() if v > 0}
        return {UNKNOWN: 1.0}


class FounderRegistry:
    """Collection of :class:`Founder` entries with normalised lookup.

    Indices are built once at construction; the founder list is copied and
    must not be mutated afterwards.  Alias-key and ``animalId`` collisions
    raise :class:`RegistryError` so a hand-edit typo fails loudly at load.
    """

    def __init__(self, founders: list[Founder]):
        self.founders = list(founders)
        self._by_key: dict[str, Founder] = {}
        self._by_animal_id: dict[str, Founder] = {}
        for f in self.founders:
            for k in f.match_keys():
                other = self._by_key.get(k)
                if other is not None and other is not f:
                    raise RegistryError(f"alias {k!r} is claimed by both {other.id!r} and {f.id!r}")
                self._by_key[k] = f
            if f.animal_id:
                other = self._by_animal_id.get(f.animal_id)
                if other is not None:
                    raise RegistryError(f"animalId {f.animal_id} is pinned by both {other.id!r} and {f.id!r}")
                self._by_animal_id[f.animal_id] = f

    # -- lookup -------------------------------------------------------------

    def match_label(self, label: str | None) -> Founder | None:
        return self._by_key.get(normalize_key(label)) if label else None

    def match_animal_id(self, animal_id: str | None) -> Founder | None:
        return self._by_animal_id.get(animal_id) if animal_id else None

    def match_identity(self, name: str | None, vid: str | None) -> Founder | None:
        """Match an off-cache parent by name first, then VID."""
        return self.match_label(name) or self.match_label(vid)

    # -- persistence --------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> FounderRegistry:
        """Load founders.json (missing file -> empty registry; malformed -> RegistryError)."""
        path = path or DEFAULT_REGISTRY_PATH
        if not path.exists():
            return cls([])
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise RegistryError(f"{path}: invalid JSON ({e})") from e
        entries = raw.get("founders", []) if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            raise RegistryError(f"{path}: expected a list of founders")
        founders = []
        for i, e in enumerate(entries):
            if not isinstance(e, dict) or "id" not in e:
                raise RegistryError(f"{path}: founder #{i} is missing 'id'")
            founders.append(
                Founder(
                    id=e["id"],
                    animal_id=e.get("animalId"),
                    aliases=list(e.get("aliases") or []),
                    breed=e.get("breed"),
                    purebred=e.get("purebred"),
                    composition=e.get("composition"),
                    notes=e.get("notes"),
                )
            )
        return cls(founders)

    def save(self, path: Path) -> None:
        """Write the registry as hand-editable JSON.

        ``path`` is required on purpose: the packaged default lives inside the
        installed package and is edited by hand, never overwritten by code.
        """
        payload = {
            "_comment": (
                "Shepherd-asserted breed status for founder animals (no recorded parents, or "
                "off-cache parents referenced only by name/VID). purebred: true | false | null (unclassified). "
                "For known crosses you may give 'composition' fractions. 'aliases' are alternate spellings; "
                "matching is case/punctuation-insensitive. Edit by hand; see agriwebb/docs/breed-purity.md."
            ),
            "founders": [
                {
                    "id": f.id,
                    "animalId": f.animal_id,
                    "aliases": f.aliases,
                    "breed": f.breed,
                    "purebred": f.purebred,
                    "composition": f.composition,
                    "notes": f.notes,
                }
                for f in self.founders
            ],
        }
        path.write_text(json.dumps(payload, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Parentage helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParentRef:
    role: Role
    animal_id: str | None
    name: str | None
    vid: str | None

    @property
    def label(self) -> str:
        return self.name or self.vid or "?"


def parent_refs(animal: dict) -> list[ParentRef]:
    out: list[ParentRef] = []
    parentage = animal.get("parentage") or {}
    for role, key in (("sire", "sires"), ("dam", "dams")):
        for p in parentage.get(key) or []:
            ident = p.get("parentAnimalIdentity") or {}
            out.append(ParentRef(role, p.get("parentAnimalId"), ident.get("name"), ident.get("vid")))
    return out


# ---------------------------------------------------------------------------
# Purity computation
# ---------------------------------------------------------------------------


@dataclass
class PurityResult:
    animal_id: str
    name: str
    breed_label: str
    composition: dict[str, float]
    purebred: bool | None
    purebred_breed: str | None
    founders: list[str]  # resolved founder ids used
    unresolved: list[str]  # founder labels lacking a registry verdict
    gaps: list[str]  # missing/ambiguous parents, cycles

    @property
    def status(self) -> str:
        """verified | mixed | unverified -- derived from ``purebred``."""
        if self.purebred is True:
            return "verified"
        if self.purebred is False:
            return "mixed"
        return "unverified"

    @property
    def rounded_composition(self) -> dict[str, float]:
        return {k: round(v, 4) for k, v in self.composition.items()}

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status
        d["composition"] = self.rounded_composition
        return d


@dataclass
class _Node:
    comp: dict[str, float]
    founders: set[str]
    unresolved: set[str]
    gaps: list[str]
    known_cross: bool  # a founder asserted purebred=False somewhere below


def _mix(parts: list[dict[str, float]]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    w = 1.0 / len(parts)
    for p in parts:
        for k, v in p.items():
            out[k] += v * w
    return dict(out)


def _combine(nodes: list[_Node]) -> _Node:
    """Average compositions and union the bookkeeping of several nodes."""
    return _Node(
        comp=_mix([n.comp for n in nodes]),
        founders=set().union(*(n.founders for n in nodes)),
        unresolved=set().union(*(n.unresolved for n in nodes)),
        gaps=[g for n in nodes for g in n.gaps],
        known_cross=any(n.known_cross for n in nodes),
    )


def _unknown_node(*, unresolved: str | None = None, gap: str | None = None) -> _Node:
    return _Node({UNKNOWN: 1.0}, set(), {unresolved} if unresolved else set(), [gap] if gap else [], False)


def _founder_node(f: Founder) -> _Node:
    if f.resolved:
        return _Node(f.get_composition(), {f.id}, set(), [], known_cross=f.purebred is False)
    return _unknown_node(unresolved=f.id)


def compute_purity(
    animal_id: str,
    by_id: dict[str, dict],
    registry: FounderRegistry,
    max_depth: int = 20,
) -> PurityResult:
    """Derive breed composition and purebred verdict for one cached animal.

    ``max_depth`` bounds straight-line ancestry depth (a gap is recorded when
    hit); cycles are detected via the recursion path and never memoised.
    """
    if animal_id not in by_id:
        raise KeyError(animal_id)

    memo: dict[str, _Node] = {}

    def walk(aid: str, stack: tuple[str, ...]) -> _Node:
        if aid in memo:
            return memo[aid]
        animal = by_id[aid]
        name = get_name(animal)

        pinned = registry.match_animal_id(aid)
        if pinned is not None:
            memo[aid] = _founder_node(pinned)
            return memo[aid]

        if aid in stack:
            return _unknown_node(gap=f"{name}: parentage cycle")
        if len(stack) >= max_depth:
            return _unknown_node(gap=f"{name}: max depth reached")

        refs = parent_refs(animal)
        if not refs:
            # Implicit founder: cached animal with no recorded parents, not in registry
            memo[aid] = _unknown_node(unresolved=name)
            return memo[aid]

        sides: list[_Node] = []
        for role in ("sire", "dam"):
            role_refs = [r for r in refs if r.role == role]
            if not role_refs:
                sides.append(_unknown_node(gap=f"{name}: {role} not recorded"))
                continue
            role_nodes: list[_Node] = []
            for ref in role_refs:
                if ref.animal_id and ref.animal_id in by_id:
                    role_nodes.append(walk(ref.animal_id, (*stack, aid)))
                else:
                    f = registry.match_identity(ref.name, ref.vid)
                    role_nodes.append(_founder_node(f) if f is not None else _unknown_node(unresolved=ref.label))
            side = _combine(role_nodes)
            if len(role_refs) > 1:
                side.gaps.append(f"{name}: {len(role_refs)} {role}s recorded (ambiguous)")
            sides.append(side)

        memo[aid] = _combine(sides)
        return memo[aid]

    node = walk(animal_id, ())
    comp = {k: v for k, v in node.comp.items() if v > 1e-12}
    breeds = [b for b in comp if b != UNKNOWN]

    if node.known_cross or len(breeds) > 1:
        purebred, pb = False, None
    elif node.unresolved or node.gaps or UNKNOWN in comp or not breeds:
        purebred, pb = None, None
    else:
        purebred, pb = True, breeds[0]

    animal = by_id[animal_id]
    return PurityResult(
        animal_id=animal_id,
        name=get_name(animal),
        breed_label=get_breed(animal),
        composition=dict(sorted(comp.items(), key=lambda kv: (-kv[1], kv[0]))),
        purebred=purebred,
        purebred_breed=pb,
        founders=sorted(node.founders),
        unresolved=sorted(node.unresolved),
        gaps=list(dict.fromkeys(node.gaps)),
    )


# ---------------------------------------------------------------------------
# Herd queries
# ---------------------------------------------------------------------------


def find_purebred(
    animals: list[dict],
    by_id: dict[str, dict],
    registry: FounderRegistry,
    breed: str,
    year: int | None = None,
    on_farm_only: bool = True,
) -> dict[str, list[PurityResult]]:
    """Classify candidate animals for *breed* as purebred / unverified / mixed.

    Candidates = animals (optionally born in *year*, optionally on-farm) whose
    lineage carries **any** fraction of *breed* or is labelled with it -- the
    label alone is never trusted for the verdict.  Verified purebreds of a
    *different* breed are excluded.  Returns results grouped by status and
    sorted by name.
    """
    target = canonical_breed(breed)
    out: dict[str, list[PurityResult]] = {"purebred": [], "unverified": [], "mixed": []}
    for a in animals:
        chars = a.get("characteristics") or {}
        if year is not None and chars.get("birthYear") != year:
            continue
        if on_farm_only and not (a.get("state") or {}).get("onFarm"):
            continue
        r = compute_purity(a["animalId"], by_id, registry)
        involved = target in r.composition or canonical_breed(r.breed_label) == target
        if not involved:
            continue
        if r.purebred is True:
            if r.purebred_breed == target:
                out["purebred"].append(r)
            continue
        out["mixed" if r.purebred is False else "unverified"].append(r)
    for v in out.values():
        v.sort(key=lambda r: r.name.lower())
    return out


# ---------------------------------------------------------------------------
# Founder discovery (what the shepherd needs to classify)
# ---------------------------------------------------------------------------


@dataclass
class FounderCandidate:
    key: str  # registry id, normalised label (external) or animalId (cached)
    in_cache: bool
    animal_id: str | None
    role: Literal["sire", "dam", "self"]
    labels: set[str] = field(default_factory=set)  # raw spellings seen
    vids: set[str] = field(default_factory=set)
    breed_hint: str | None = None  # cached animal's label
    birth_year: int | None = None
    children: int = 0
    descendants: int = 0
    resolved: bool = False
    registry_id: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["labels"] = sorted(self.labels)
        d["vids"] = sorted(self.vids)
        return d


def discover_founders(
    animals: list[dict],
    by_id: dict[str, dict],
    registry: FounderRegistry,
) -> list[FounderCandidate]:
    """Enumerate every founder in the herd with descendant counts and registry status.

    External founders are grouped by registry match when one exists, otherwise
    by normalised name (falling back to VID).  A parent reference with neither
    name nor VID cannot be grouped and gets its own per-animal entry.
    """
    cands: dict[str, FounderCandidate] = {}
    children: dict[str, set[str]] = defaultdict(set)

    for a in animals:
        refs = parent_refs(a)
        if not refs:
            f = registry.match_animal_id(a["animalId"])
            cands[a["animalId"]] = FounderCandidate(
                key=a["animalId"],
                in_cache=True,
                animal_id=a["animalId"],
                role="self",
                labels={get_name(a)},
                breed_hint=get_breed(a),
                birth_year=(a.get("characteristics") or {}).get("birthYear"),
                resolved=bool(f and f.resolved),
                registry_id=f.id if f else None,
            )
        for ref in refs:
            if ref.animal_id and ref.animal_id in by_id:
                children[ref.animal_id].add(a["animalId"])
                continue
            f = registry.match_identity(ref.name, ref.vid)
            if f is not None:
                key = f.id
            else:
                key = normalize_key(ref.name) or normalize_key(ref.vid)
                if not key:
                    key = f"unidentified:{a['animalId']}:{ref.role}"
            c = cands.get(key)
            if c is None:
                c = cands[key] = FounderCandidate(
                    key=key,
                    in_cache=False,
                    animal_id=None,
                    role=ref.role,
                    resolved=bool(f and f.resolved),
                    registry_id=f.id if f else None,
                )
            if ref.name:
                c.labels.add(ref.name)
            elif not ref.vid:
                c.labels.add(f"unidentified {ref.role} of {get_name(a)}")
            if ref.vid:
                c.vids.add(ref.vid)
            children[key].add(a["animalId"])

    def descendants(start: str) -> set[str]:
        seen: set[str] = set()
        stack = [start]
        while stack:
            x = stack.pop()
            for ch in children.get(x, ()):
                if ch not in seen:
                    seen.add(ch)
                    stack.append(ch)
        return seen

    for c in cands.values():
        c.children = len(children.get(c.key, ()))
        c.descendants = len(descendants(c.key))

    return sorted(cands.values(), key=lambda c: (c.resolved, -c.descendants, c.key))
