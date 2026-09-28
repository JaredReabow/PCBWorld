"""Deterministic duplicate-UUID normalization for imported KiCad boards.

Boards exported by other tools (EasyEDA Pro in this workspace's case) reuse
UUIDs: the same identifier can be carried by several footprints, pads or
graphics.  Everything downstream that treats a UUID as an identity then has to
fail closed, because a reused identifier does not name one physical item.  This
module repairs that on a *disposable copy* of the board: it rewrites only the
identifier tokens of the duplicate occurrences and nothing else.

The contract, in order of importance:

* **Nothing but identity metadata changes.**  The module never re-serializes the
  board.  It parses the file to locate the exact source span of each ``uuid``
  atom and splices new text into the original bytes, so geometry, nets, rules,
  footprints, layers, stackup, zone outlines, properties, fill polygons, labels
  and locked copper keep their exact bytes and their original order.
* **Already-unique identifiers are preserved.**  An identifier carried by
  exactly one item is that item's identity and is left alone.
* **Duplicate occurrences get a deterministic new identifier** derived from the
  occurrence's own parsed structure (its subtree with every identity token
  stripped).  The same physical object therefore maps to the same new UUID in
  any file that holds it, independent of the order objects appear in.
* **Two indistinguishable occurrences are refused, never guessed.**  If two
  occurrences of one identifier have identical structure, no rule can tell them
  apart, so the board is refused and the ambiguity is reported.
* **Referenced identifiers are refused, never half-rewritten.**  A reference
  (a group member list, a rule condition, a project selector) to a duplicated
  identifier cannot say *which* occurrence it means.  Such a board is refused
  rather than rewritten speculatively; sidecar files are never modified.
* **Idempotent.**  A normalized board has no duplicates, so normalizing it again
  returns the input bytes unchanged.

The module is stdlib-only and reads/writes no state outside the files it is
asked to handle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

#: Namespace for deterministic renumbering.  Changing it changes every minted
#: UUID, so it is part of the normalization contract (like a hash prefix).
NORMALIZE_NAMESPACE = uuid.UUID("5e1f9c3a-6d2b-4d7e-9f10-8c4b7a2d1e33")

#: Node tags that carry an item's own identity.  KiCad 9 writes ``uuid``; older
#: writers used ``tstamp``.  Both are treated the same way.
IDENTITY_TAGS = ("uuid", "tstamp")

#: Selector patterns a sidecar (.kicad_pro / .kicad_dru) can use to name an item.
SIDECAR_UUID_PATTERNS = (
    re.compile(r"""(?P<quote>['"])(?P<uuid>[0-9a-fA-F-]{36})(?P=quote)"""),
    re.compile(r"""Uuid\s*==\s*['"](?P<uuid>[0-9a-fA-F-]{36})['"]"""),
)


class SexprError(ValueError):
    """The input is not a well-formed s-expression."""


# ===========================================================================
# Tokenizer + parser (source spans kept, so the file can be spliced in place)
# ===========================================================================

_WHITESPACE = " \t\r\n\f\v"
_DELIMITER = " \t\r\n\f\v()\""
_COMMENT_START = ";#"


@dataclass(frozen=True)
class Token:
    """One lexical token with its exact source span."""

    kind: str          # "open" | "close" | "atom" | "comment"
    text: str          # decoded value (quotes stripped, escapes resolved)
    raw: str           # the exact source text
    start: int
    end: int
    quoted: bool = False


def _unquote(raw: str) -> str:
    """Decode a quoted KiCad string body (backslash escapes only)."""
    body = raw[1:-1]
    if "\\" not in body:
        return body
    out: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            nxt = body[index + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def tokenize(text: str) -> list[Token]:
    """Split ``text`` into tokens, keeping each token's exact source span.

    Comments (``;`` or ``#`` at a token boundary) are returned as tokens so a
    caller can see they exist, but the parser drops them from the tree: they are
    not part of the board's structure, and because the file is spliced rather
    than re-serialized they survive byte for byte regardless.
    """
    tokens: list[Token] = []
    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        if char in _WHITESPACE:
            index += 1
            continue

        if char in _COMMENT_START:
            stop = text.find("\n", index)
            stop = length if stop < 0 else stop
            tokens.append(Token("comment", text[index:stop], text[index:stop],
                                index, stop))
            index = stop
            continue

        if char == "(":
            tokens.append(Token("open", "(", "(", index, index + 1))
            index += 1
            continue

        if char == ")":
            tokens.append(Token("close", ")", ")", index, index + 1))
            index += 1
            continue

        if char == '"':
            stop = index + 1
            while stop < length:
                if text[stop] == "\\":
                    stop += 2
                    continue
                if text[stop] == '"':
                    stop += 1
                    break
                stop += 1
            else:
                raise SexprError(f"unterminated string at offset {index}")
            raw = text[index:stop]
            tokens.append(Token("atom", _unquote(raw), raw, index, stop,
                                quoted=True))
            index = stop
            continue

        stop = index
        while stop < length and text[stop] not in _DELIMITER:
            stop += 1
        if stop == index:
            raise SexprError(f"unexpected character {char!r} at offset {index}")
        raw = text[index:stop]
        tokens.append(Token("atom", raw, raw, index, stop))
        index = stop

    return tokens


@dataclass
class Atom:
    """A leaf value, keeping the exact source text it was written with."""

    text: str
    raw: str
    start: int
    end: int
    quoted: bool = False


@dataclass
class SList:
    """A parenthesized list; ``head`` is its tag (the first atom, if any)."""

    items: list[Any]
    start: int
    end: int

    @property
    def head(self) -> str | None:
        for item in self.items:
            return item.text if isinstance(item, Atom) else None
        return None


def parse(text: str) -> SList:
    """Parse one complete top-level s-expression form."""
    tokens = [token for token in tokenize(text) if token.kind != "comment"]
    if not tokens:
        raise SexprError("no s-expression found")

    position = 0

    def parse_list() -> SList:
        nonlocal position
        token = tokens[position]
        if token.kind != "open":
            raise SexprError(f"expected '(' at offset {token.start}")
        position += 1
        items: list[Any] = []
        while True:
            if position >= len(tokens):
                raise SexprError("unexpected end of input inside a list")
            current = tokens[position]
            if current.kind == "close":
                position += 1
                return SList(items, token.start, current.end)
            if current.kind == "open":
                items.append(parse_list())
                continue
            items.append(Atom(current.text, current.raw, current.start,
                              current.end, current.quoted))
            position += 1

    root = parse_list()
    if position != len(tokens):
        trailing = tokens[position]
        raise SexprError(f"trailing tokens after the top-level form "
                         f"(offset {trailing.start})")
    return root


def iter_lists(node: SList) -> Iterator[SList]:
    """Depth-first traversal of every list in ``node`` (``node`` included)."""
    yield node
    for item in node.items:
        if isinstance(item, SList):
            yield from iter_lists(item)


def iter_atoms(node: SList) -> Iterator[Atom]:
    """Every atom in ``node``, in document order."""
    for item in node.items:
        if isinstance(item, Atom):
            yield item
        else:
            yield from iter_atoms(item)


def semantic_form(value: Any) -> Any:
    """Structure + non-identity atoms, with identity lists dropped.

    This is the comparison form: two boards are semantically equivalent when
    their :func:`semantic_form` trees are equal, which ignores exactly the
    identity metadata normalization is allowed to change.  It is byte-exact for
    every other token, so it can prove that nothing else moved.
    """
    return _semantic_forms(value, {}, fold_angles=False)


def signature_form(value: Any) -> Any:
    """Like :func:`semantic_form`, but rotation angles are folded mod 360.

    Used only to decide *which physical object an identity token belongs to*,
    never to prove nothing else changed: two writers can spell one rotation
    differently (``-180`` vs ``180``), and that must not split one object into
    two identities.
    """
    return _semantic_forms(value, {}, fold_angles=True)


def _semantic_forms(value: Any, memo: dict[int, Any], *, fold_angles: bool) -> Any:
    """Memoized tree form (the tree is large; forms are reused)."""
    if isinstance(value, Atom):
        return value.text
    cache_key = (id(value), fold_angles)
    cached = memo.get(cache_key)
    if cached is not None:
        return cached
    head = value.head
    children = []
    for index, item in enumerate(value.items):
        if isinstance(item, SList) and item.head in IDENTITY_TAGS:
            continue
        if isinstance(item, Atom):
            children.append(_signature_atom(head, index, item)
                            if fold_angles else item.text)
            continue
        children.append(_semantic_forms(item, memo, fold_angles=fold_angles))
    form = tuple(children)
    memo[cache_key] = form
    return form


def _signature_atom(head: str | None, index: int, atom: Atom) -> str:
    """One atom as the *identity* comparison sees it.

    Rotation angles are only defined modulo a full turn, and two writers can
    spell the same rotation differently (this workspace's import writes ``-180``
    where KiCad writes ``180``).  Angles are therefore folded into ``[0, 360)``
    for identity purposes.  Coordinates and every other value stay byte-exact:
    the comparison form used for verification does not go through this function.
    """
    if head == "at" and index == 3:
        try:
            angle = float(atom.text)
        except ValueError:
            return atom.text
        folded = angle % 360.0
        text = f"{folded:g}"
        return "0" if text == "-0" else text
    if atom.text and atom.text.lstrip("-").replace(".", "", 1).isdigit():
        try:
            if float(atom.text) == 0.0:
                return "0"          # ``-0`` and ``0`` are one coordinate
        except ValueError:
            return atom.text
    return atom.text


def semantic_digest(value: SList | str) -> str:
    """SHA-256 over the semantic form of ``value`` (a tree or source text)."""
    root = parse(value) if isinstance(value, str) else value
    material = json.dumps(semantic_form(root), separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# ===========================================================================
# Occurrences
# ===========================================================================

@dataclass(frozen=True)
class Occurrence:
    """One item's identity token: the UUID, where it sits, which item owns it."""

    uuid: str
    atom: Atom
    item: SList
    index: int          # position of the identity list inside ``item``
    path: tuple[int, ...]  # the owner item's path from the document root
    signature: str      # structural identity of ``item`` (identity-stripped)


def _walk(node: SList, path: tuple[int, ...] = ()) -> Iterator[tuple[SList, tuple[int, ...]]]:
    for index, item in enumerate(node.items):
        if isinstance(item, SList):
            child_path = path + (index,)
            yield item, child_path
            yield from _walk(item, child_path)


def collect_occurrences(root: SList) -> list[Occurrence]:
    """Every ``(uuid "...")`` / ``(tstamp "...")`` token and its owner item."""
    occurrences: list[Occurrence] = []
    memo: dict[int, Any] = {}
    for node, path in _walk(root):
        if node.head not in IDENTITY_TAGS:
            continue
        if len(node.items) != 2 or not isinstance(node.items[1], Atom):
            continue
        # The owner is the list that contains this identity list.
        owner_path = path[:-1]
        owner = _list_at_path(root, owner_path)
        if owner is None:
            continue
        atom = node.items[1]
        occurrences.append(Occurrence(
            uuid=atom.text,
            atom=atom,
            item=owner,
            index=path[-1],
            path=owner_path,
            signature=chain_signature(root, owner_path, memo),
        ))
    return occurrences


def _list_at_path(root: SList, path: tuple[int, ...]) -> SList | None:
    node = root
    for index in path:
        if not 0 <= index < len(node.items):
            return None
        child = node.items[index]
        if not isinstance(child, SList):
            return None
        node = child
    return node


def struct_signature(item: SList) -> str:
    """Deterministic structural identity of one item (identity tokens dropped).

    Two occurrences are distinguishable exactly when this differs: it is the
    item's whole parsed subtree with every identity token removed, so it names
    the physical object rather than the file position.
    """
    material = json.dumps(semantic_form(item), separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def chain_signature(root: SList, path: tuple[int, ...],
                    memo: dict[int, Any] | None = None) -> str:
    """Structural identity of one item *in its hierarchy*.

    Footprint children are written in footprint-local coordinates (every
    instance of a library footprint carries the same pad/graphic geometry), so
    the item's own subtree is not enough: the chain of enclosing items is what
    makes two instances of one footprint distinct objects.  The chain starts at
    the outermost item and ends at the owner itself, each contributing its
    identity-stripped form.
    """
    memo = {} if memo is None else memo
    chain: list[Any] = []
    node = root
    for index in path:
        node = node.items[index]
        chain.append(_semantic_forms(node, memo, fold_angles=True))
    material = json.dumps(chain, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# ===========================================================================
# Duplicate analysis
# ===========================================================================

def duplicate_groups(
    occurrences: Sequence[Occurrence],
) -> dict[str, list[Occurrence]]:
    """UUID -> occurrences, for identifiers carried by more than one item."""
    groups: dict[str, list[Occurrence]] = {}
    for occurrence in occurrences:
        groups.setdefault(occurrence.uuid, []).append(occurrence)
    return {key: group for key, group in groups.items() if len(group) > 1}


def _identity_atom_indexes(root: SList) -> set[int]:
    """Source offsets of every identity atom (not just their owning lists)."""
    offsets: set[int] = set()
    for node in iter_lists(root):
        if node.head in IDENTITY_TAGS and len(node.items) == 2 \
                and isinstance(node.items[1], Atom):
            offsets.add(node.items[1].start)
    return offsets


def referenced_identifiers(root: SList, duplicated: Iterable[str]) -> list[tuple[str, int]]:
    """``(uuid, offset)`` for every non-identity atom that names a duplicate.

    A reference to a duplicated identifier cannot say which occurrence it means,
    so it can never be rewritten unambiguously.
    """
    wanted = set(duplicated)
    identity_offsets = _identity_atom_indexes(root)
    found: list[tuple[str, int]] = []
    for atom in iter_atoms(root):
        if atom.start in identity_offsets:
            continue
        if atom.text in wanted:
            found.append((atom.text, atom.start))
    return found


@dataclass
class Analysis:
    """What a board's identifiers look like, without changing anything."""

    node_count: int = 0
    identity_count: int = 0
    distinct_identifiers: int = 0
    duplicate_identifiers: int = 0
    duplicate_occurrences: int = 0
    duplicates: dict[str, int] = field(default_factory=dict)     # uuid -> count
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    parse_error: str | None = None

    @property
    def clean(self) -> bool:
        return (self.parse_error is None and not self.duplicates
                and not self.ambiguous and not self.references)

    def to_evidence(self) -> dict[str, Any]:
        return {
            "node_count": self.node_count,
            "identity_count": self.identity_count,
            "distinct_identifiers": self.distinct_identifiers,
            "duplicate_identifiers": self.duplicate_identifiers,
            "duplicate_occurrences": self.duplicate_occurrences,
            "duplicates": dict(sorted(self.duplicates.items())),
            "ambiguous": list(self.ambiguous),
            "references": list(self.references),
            "parse_error": self.parse_error,
        }


def analyze(text: str) -> Analysis:
    """Measure duplicate identifiers, indistinguishability and references."""
    try:
        root = parse(text)
    except SexprError as error:
        return Analysis(parse_error=f"{type(error).__name__}: {error}")

    occurrences = collect_occurrences(root)
    groups = duplicate_groups(occurrences)

    result = Analysis(
        node_count=sum(1 for _node in iter_lists(root)),
        identity_count=len(occurrences),
        distinct_identifiers=len({occurrence.uuid for occurrence in occurrences}),
        duplicate_identifiers=len(groups),
        duplicate_occurrences=sum(len(group) for group in groups.values()),
        duplicates={key: len(group) for key, group in groups.items()},
    )

    # Only occurrences that need a new identifier matter here: two such
    # occurrences with the same structure cannot be told apart by any rule, so
    # they are refused rather than split by file order.
    renumbered = [occurrence for group in groups.values() for occurrence in group]
    by_signature: dict[str, list[Occurrence]] = {}
    for occurrence in renumbered:
        by_signature.setdefault(occurrence.signature, []).append(occurrence)
    for signature, same in by_signature.items():
        if len(same) > 1:
            result.ambiguous.append({
                "signature": signature,
                "uuids": sorted({occurrence.uuid for occurrence in same}),
                "occurrences": len(same),
                "offsets": sorted(occurrence.atom.start for occurrence in same),
            })

    result.references = [
        {"uuid": key, "offset": offset}
        for key, offset in referenced_identifiers(root, groups)
    ]
    return result


# ===========================================================================
# Normalization
# ===========================================================================

def mint_uuid(signature: str) -> str:
    """Deterministic UUID for one physical object.

    Derived from the object's own parsed structure, so the same physical object
    is renumbered identically in every file that holds it - including a file
    that lists its objects in a different order.
    """
    return str(uuid.uuid5(NORMALIZE_NAMESPACE, signature))


@dataclass
class NormalizationResult:
    """The normalized bytes plus the proof metadata that goes with them."""

    ok: bool = False
    text: str = ""
    source_sha256: str = ""
    normalized_sha256: str = ""
    semantic_digest: str = ""
    mapping: list[dict[str, Any]] = field(default_factory=list)
    minted: dict[str, str] = field(default_factory=dict)   # signature -> new uuid
    analysis: Analysis = field(default_factory=Analysis)
    problems: list[str] = field(default_factory=list)

    def evidence(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "source_sha256": self.source_sha256,
            "normalized_sha256": self.normalized_sha256,
            "semantic_digest": self.semantic_digest,
            "identity_policy": IDENTITY_POLICY,
            "mapping": self.mapping,
            "analysis": self.analysis.to_evidence(),
            "problems": list(self.problems),
        }


#: The normalization policy label that travels with the evidence, so a report
#: can never be replayed as if it came from a different rule.
IDENTITY_POLICY = "deterministic-duplicate-uuid-v1"


def _splice(text: str, edits: Sequence[tuple[int, int, str]]) -> str:
    """Replace ``[start, end)`` spans with new text, right to left."""
    parts: list[str] = []
    cursor = 0
    for start, end, replacement in sorted(edits):
        if start < cursor:
            raise ValueError("overlapping edits")
        parts.append(text[cursor:start])
        parts.append(replacement)
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def normalize_text(text: str) -> NormalizationResult:
    """Renumber every duplicate identifier, refusing anything ambiguous.

    Returns the result whether or not it succeeded; the caller decides what to
    do with a refusal.  ``result.text`` is only meaningful when ``ok`` is true.
    """
    result = NormalizationResult(
        source_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    analysis = analyze(text)
    result.analysis = analysis

    if analysis.parse_error:
        result.problems.append(f"board is not parseable: {analysis.parse_error}")
        return result

    root = parse(text)
    occurrences = collect_occurrences(root)
    groups = duplicate_groups(occurrences)
    renumbered = [occurrence for group in groups.values() for occurrence in group]

    if analysis.ambiguous:
        for entry in analysis.ambiguous:
            result.problems.append(
                f"{entry['occurrences']} duplicate occurrences "
                f"(identifiers {entry['uuids']}) are structurally identical at "
                f"offsets {entry['offsets']}; no rule can tell them apart"
            )
    if analysis.references:
        for entry in analysis.references[:10]:
            result.problems.append(
                f"identifier {entry['uuid']} is duplicated and referenced at "
                f"offset {entry['offset']}; the reference cannot name one "
                "occurrence, so it is not rewritten"
            )
        if len(analysis.references) > 10:
            result.problems.append(
                f"... and {len(analysis.references) - 10} more references to "
                "duplicated identifiers"
            )
    if result.problems:
        return result

    edits: list[tuple[int, int, str]] = []
    for occurrence in sorted(renumbered, key=lambda occ: occ.atom.start):
        fresh = mint_uuid(occurrence.signature)
        result.minted[occurrence.signature] = fresh
        edits.append((occurrence.atom.start, occurrence.atom.end, f'"{fresh}"'))
        result.mapping.append({
            "original_uuid": occurrence.uuid,
            "new_uuid": fresh,
            "item_kind": occurrence.item.head or "",
            "offset": occurrence.atom.start,
            "occurrence_signature": occurrence.signature,
        })

    normalized = _splice(text, edits) if edits else text
    result.text = normalized
    result.normalized_sha256 = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    result.semantic_digest = semantic_digest(root)

    # Post-checks, before the result may report ok. ``ok`` is only set once the
    # output has been read back and proven: the caller writes a file on ``ok``,
    # so a splice that produced anything other than a collision-free,
    # identity-only edit must never reach that point.
    rewritten_offsets = {int(entry["offset"]) for entry in result.mapping}
    kept_identifiers = {occurrence.uuid for occurrence in occurrences
                        if occurrence.atom.start not in rewritten_offsets}
    minted = [str(entry["new_uuid"]) for entry in result.mapping]

    collisions = sorted(set(minted) & kept_identifiers)
    if collisions:
        result.problems.append(
            "a minted identifier collides with an identifier the board already "
            f"keeps ({collisions[:5]}); refusing rather than trusting the "
            "UUIDv5 input space"
        )
    if len(set(minted)) != len(minted):
        result.problems.append(
            "two occurrences were assigned the same new identifier"
        )

    after = analyze(normalized)
    if after.parse_error:
        result.problems.append(
            f"the normalized board does not parse: {after.parse_error}"
        )
    else:
        if after.duplicate_identifiers:
            result.problems.append(
                "the normalized board still carries "
                f"{after.duplicate_identifiers} duplicated identifier(s)"
            )
        if after.ambiguous:
            result.problems.append(
                "the normalized board still has structurally indistinguishable "
                "occurrences"
            )
        if after.references:
            result.problems.append(
                "the normalized board references a duplicated identifier"
            )
    result.problems.extend(verify_equivalence(text, normalized, result.mapping))

    result.ok = not result.problems
    return result


def verify_equivalence(source: str, normalized: str,
                       mapping: Sequence[dict[str, Any]]) -> list[str]:
    """Prove only identity metadata changed between ``source`` and ``normalized``.

    Three independent checks, because each can fail on its own:

    1. the semantic forms (structure + every non-identity atom) are equal, so no
       geometry, net, rule, layer, property or ordering changed;
    2. the two files carry the same number of identity tokens, in the same
       document order, so no item was added, dropped or moved;
    3. each token in document order is exactly what the mapping says it should
       be: the mapped identifier when it was rewritten, the original otherwise.

    Tokens are compared by position, not by byte offset, because a replacement
    identifier has a different length than the one it replaced.
    """
    problems: list[str] = []
    try:
        source_root = parse(source)
        target_root = parse(normalized)
    except SexprError as error:
        return [f"cannot parse for verification: {type(error).__name__}: {error}"]

    if semantic_form(source_root) != semantic_form(target_root):
        problems.append(
            "semantic forms differ: a non-identity token or the structure "
            "changed during normalization"
        )

    source_tokens = [(occurrence.uuid, occurrence.atom.start)
                     for occurrence in collect_occurrences(source_root)]
    target_tokens = [occurrence.uuid
                     for occurrence in collect_occurrences(target_root)]
    if len(source_tokens) != len(target_tokens):
        problems.append(
            "the number of identity tokens changed during normalization "
            f"({len(source_tokens)} -> {len(target_tokens)})"
        )
        return problems

    rewritten = {int(entry["offset"]): str(entry["new_uuid"])
                 for entry in mapping}
    for index, ((original, offset), actual) in enumerate(
            zip(source_tokens, target_tokens)):
        expected = rewritten.get(offset, original)
        if actual != expected:
            problems.append(
                f"identity token #{index} (source offset {offset}) is "
                f"{actual!r}, expected {expected!r}"
            )
    unapplied = set(rewritten) - {offset for _uuid, offset in source_tokens}
    for offset in sorted(unapplied):
        problems.append(f"the mapping rewrites offset {offset}, which carries "
                        "no identity token")
    return problems


# ===========================================================================
# Sidecars (project / rules) — read-only unless a rewrite is provable
# ===========================================================================

def sidecar_identifier_references(text: str) -> dict[str, int]:
    """UUIDs a sidecar file names by selector (project settings, DRC rules)."""
    found: dict[str, int] = {}
    for pattern in SIDECAR_UUID_PATTERNS:
        for match in pattern.finditer(text):
            key = match.group("uuid").lower()
            found[key] = found.get(key, 0) + 1
    return found


def sidecar_problems(paths: Iterable[str | Path],
                     duplicated: Iterable[str]) -> list[str]:
    """Refuse to leave a sidecar pointing at an identifier that moved.

    Sidecars are never rewritten.  A sidecar naming a duplicated identifier
    would be made stale by normalization, and there is no unambiguous way to
    re-point it, so the board is refused instead.
    """
    wanted = {key.lower() for key in duplicated}
    problems: list[str] = []
    for path in paths:
        text_path = Path(path)
        if not text_path.is_file():
            continue
        try:
            text = text_path.read_text(encoding="utf-8")
        except OSError as error:
            problems.append(f"{text_path}: cannot read ({error})")
            continue
        references = sidecar_identifier_references(text)
        hits = sorted(key for key in references if key in wanted)
        if hits:
            problems.append(
                f"{text_path.name} names duplicated identifier(s) "
                f"{hits[:5]}{' ...' if len(hits) > 5 else ''}; a sidecar is "
                "never rewritten, so the board is refused"
            )
    return problems


# ===========================================================================
# File-level entry points
# ===========================================================================

def normalize_board(
    board_path: str | Path,
    output_path: str | Path | None = None,
    *,
    sidecars: Sequence[str | Path] = (),
    dry_run: bool = False,
) -> NormalizationResult:
    """Normalize one board file (writing ``output_path`` unless ``dry_run``)."""
    board_path = Path(board_path)
    text = board_path.read_text(encoding="utf-8")
    result = normalize_text(text)

    if result.ok and sidecars:
        problems = sidecar_problems(sidecars, result.analysis.duplicates)
        if problems:
            result.ok = False
            result.problems.extend(problems)

    if result.ok and not dry_run and output_path is not None:
        target = Path(output_path)
        target.write_text(result.text, encoding="utf-8")
    return result


def _cmd_analyze(args: argparse.Namespace) -> int:
    text = Path(args.board).read_text(encoding="utf-8")
    analysis = analyze(text)
    print(json.dumps({
        "board": args.board,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        **analysis.to_evidence(),
    }, indent=2, sort_keys=True))
    return 0 if analysis.parse_error is None else 1


def _cmd_normalize(args: argparse.Namespace) -> int:
    result = normalize_board(
        args.board, args.output, sidecars=args.sidecar or (), dry_run=args.dry_run,
    )
    if args.report:
        Path(args.report).write_text(
            json.dumps(result.evidence(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if not result.ok:
        for problem in result.problems:
            print(f"refused: {problem}", file=sys.stderr)
        return 2

    if not args.dry_run and args.output:
        problems = verify_equivalence(
            Path(args.board).read_text(encoding="utf-8"),
            Path(args.output).read_text(encoding="utf-8"),
            result.mapping,
        )
        if problems:
            for problem in problems:
                print(f"verification failed: {problem}", file=sys.stderr)
            return 3
        # Idempotence is a property of the output, so it is checked on the output.
        again = normalize_text(Path(args.output).read_text(encoding="utf-8"))
        if not again.ok or again.text != Path(args.output).read_text(encoding="utf-8"):
            print("verification failed: normalization is not idempotent",
                  file=sys.stderr)
            return 3

    print(json.dumps({
        "ok": True,
        "source_sha256": result.source_sha256,
        "normalized_sha256": result.normalized_sha256,
        "semantic_digest": result.semantic_digest,
        "rewritten": len(result.mapping),
        "duplicate_identifiers": result.analysis.duplicate_identifiers,
    }, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kicad_metadata",
        description="Deterministic duplicate-UUID normalization for KiCad boards.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze_cmd = sub.add_parser("analyze", help="report identifiers, no writes")
    analyze_cmd.add_argument("board")
    analyze_cmd.set_defaults(func=_cmd_analyze)

    normalize_cmd = sub.add_parser(
        "normalize", help="write a normalized copy (never the input)")
    normalize_cmd.add_argument("board")
    normalize_cmd.add_argument("--output", required=False, default=None)
    normalize_cmd.add_argument("--report", default=None,
                               help="write the mapping/evidence JSON here")
    normalize_cmd.add_argument("--sidecar", action="append", default=[],
                               help=".kicad_pro / .kicad_dru to leave unchanged "
                                    "and refuse if they name a duplicate")
    normalize_cmd.add_argument("--dry-run", action="store_true")
    normalize_cmd.set_defaults(func=_cmd_normalize)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "normalize" and not args.dry_run and not args.output:
        parser.error("normalize needs --output (or --dry-run)")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
