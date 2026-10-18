"""Work hierarchy resolution and tagging.

This module has no dependency on Picard so it can be tested on its own. It
works on the JSON documents returned by the MusicBrainz web service:

* the recording node of a release lookup made with ``work-rels`` and
  ``work-level-rels`` (what Picard requests when "Use track relationships" is
  enabled), which contains the works the recording is a performance of, plus
  each work's own work relationships (so its immediate parent is known);
* work lookups made with ``inc=work-rels``, used to climb from a parent to its
  own parent, and so on up to the top-level work.

MusicBrainz does not record where in a recording each work starts, so nothing
here deals with timing: a track is described by the ordered list of works it
contains, and nothing more.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

if TYPE_CHECKING:
    from typing import Protocol

    class MetadataLike(Protocol):
        """The subset of picard.metadata.Metadata used for writing tags."""

        def __setitem__(self, name: str, values: Any) -> None: ...

        def delete(self, name: str) -> None: ...


FORMAT_VERSION = 1

#: Tag holding the machine-readable description of the track's works.
JSON_TAG = "work_hierarchy"

MOVEMENT_TAGS = ("movement", "movementnumber", "movementtotal", "showmovement")

WORK_LEVEL_TOP = "top"
WORK_LEVEL_PARENT = "parent"

#: Attribute of the "parts" relationship that marks a grouping of separate
#: works (e.g. "6 Partitas, BWV 825-830") rather than a work and its parts.
COLLECTION_ATTRIBUTE = "part of collection"

#: Guard against runaway climbs through bad data.
MAX_DEPTH = 12

_WORD_RE = re.compile(r"\w+")
_SEPARATORS = ":.,;/|-–—"
_LEADING_SEPARATORS_RE = re.compile(r"^[\s:.,;/|\-–—]+")
_NUMERAL_RE = re.compile(r"^(?P<num>[IVXLCDM]+|\d+)\.\s+(?=\S)")
_ROMAN_RE = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


@dataclass(frozen=True)
class WorkLink:
    """One side of a "parts" relationship: a work's parent or one of its parts."""

    id: str
    title: str
    type: str | None
    #: The part's position in its parent (the relationship's ordering key).
    position: int | None
    attributes: tuple[str, ...]

    @property
    def is_collection(self) -> bool:
        return COLLECTION_ATTRIBUTE in self.attributes


@dataclass(frozen=True)
class WorkInfo:
    """A work and its "parts" relationships."""

    id: str
    title: str
    type: str | None
    parents: tuple[WorkLink, ...]
    parts: tuple[WorkLink, ...]

    @classmethod
    def from_json(cls, node: Mapping[str, Any]) -> WorkInfo:
        parents = []
        parts = []
        for rel in node.get("relations") or ():
            if rel.get("target-type") != "work" or rel.get("type") != "parts":
                continue
            target = rel.get("work") or {}
            if not target.get("id"):
                continue
            link = WorkLink(
                id=target["id"],
                title=target.get("title") or "",
                type=target.get("type"),
                position=_int_or_none(rel.get("ordering-key")),
                attributes=tuple(rel.get("attributes") or ()),
            )
            if rel.get("direction") == "backward":
                parents.append(link)
            else:
                parts.append(link)
        return cls(
            id=node["id"],
            title=node.get("title") or "",
            type=node.get("type"),
            parents=tuple(parents),
            parts=tuple(parts),
        )

    @property
    def part_total(self) -> int:
        """The highest part position, or the number of parts if none have one.

        Positions are not always contiguous, so counting the numbered parts
        could give a total lower than some part's position. Unnumbered parts
        are ignored when there are numbered ones: MusicBrainz often lists
        appendix items or alternative versions that way.
        """
        positions = [part.position for part in self.parts if part.position is not None]
        return max(positions) if positions else len(self.parts)

    @property
    def collections(self) -> tuple[WorkLink, ...]:
        return tuple(parent for parent in self.parents if parent.is_collection)

    def parent(self) -> WorkLink | None:
        """The parent work in the hierarchy, or None for a top-level work.

        Collections are not followed. If a work has several parents, the one
        whose title the work's own title extends wins (MusicBrainz style is
        "Parent: Part"), then one that gives the work a position.
        """
        candidates = [link for link in self.parents if not link.is_collection]
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        ranked = sorted(
            enumerate(candidates),
            key=lambda item: (
                -_parent_prefix_end(self.title, item[1].title),
                item[1].position is None,
                item[0],
            ),
        )
        return ranked[0][1]


@dataclass(frozen=True)
class Performance:
    """A recording-to-work "performance" relationship."""

    work: WorkInfo
    #: Relationship attributes such as "partial", "live" or "medley".
    attributes: tuple[str, ...]


@dataclass(frozen=True)
class Node:
    """One level of a work's path from its top-level work."""

    id: str
    title: str
    #: The title with the parent's title and a matching part number removed.
    name: str
    type: str | None
    #: Position in the parent work; None for top-level works or when unknown.
    position: int | None
    #: See WorkInfo.part_total; None when the work was not looked up.
    part_total: int | None
    #: Attributes of the relationship to the parent, e.g. "act" or "movement".
    part_attributes: tuple[str, ...]
    collections: tuple[WorkLink, ...]
    #: Part number to display: the position, or a number parsed from the title.
    number: int | None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "name": self.name,
            "type": self.type,
            "position": self.position,
            "part_total": self.part_total,
            "part_attributes": list(self.part_attributes),
            "collections": [
                {"id": link.id, "title": link.title, "position": link.position}
                for link in self.collections
            ],
        }


@dataclass(frozen=True)
class Segment:
    """One work performed in a track, with its path from the top-level work."""

    #: Top-level work first, the work the recording performs last.
    path: tuple[Node, ...]
    attributes: tuple[str, ...]

    @property
    def work(self) -> Node:
        return self.path[-1]

    def to_json(self) -> dict[str, Any]:
        return {
            "path": [node.to_json() for node in self.path],
            "attributes": list(self.attributes),
        }


@dataclass(frozen=True)
class Resolution:
    """The works in a track, in performance order."""

    segments: tuple[Segment, ...]
    #: IDs of ancestor works that must be looked up to complete the paths.
    unknown: frozenset[str]


@dataclass(frozen=True)
class StandardTags:
    work: tuple[str, ...]
    movement: str | None
    movementnumber: int | None
    movementtotal: int | None


def performances(recording: Mapping[str, Any]) -> list[Performance]:
    """The works a recording node is a performance of, in web service order."""
    result = []
    for rel in recording.get("relations") or ():
        if rel.get("target-type") != "work" or rel.get("type") != "performance":
            continue
        work = rel.get("work") or {}
        if work.get("id"):
            result.append(Performance(WorkInfo.from_json(work), tuple(rel.get("attributes") or ())))
    return result


def resolve(
    performed: Sequence[Performance],
    works: Mapping[str, WorkInfo],
    failed: Iterable[str] = (),
) -> Resolution:
    """Build each performed work's path to its top-level work.

    ``works`` holds ancestor works that have been looked up. A path stops at
    the first ancestor that is missing from ``works``; that ancestor is still
    included, using what the child's relationship says about it, and its ID
    is reported in ``Resolution.unknown`` unless it is listed in ``failed``.
    """
    skip = frozenset(failed)
    unknown = set()
    segments = []
    seen = set()
    for performance in performed:
        if performance.work.id in seen:
            continue
        seen.add(performance.work.id)
        path, missing = _climb(performance.work, works)
        if missing is not None and missing not in skip:
            unknown.add(missing)
        segments.append(Segment(path, performance.attributes))

    # A recording linked to both a work and one of its parts (an editing
    # mistake that does occur) is a performance of the part.
    ancestors = {node.id for segment in segments for node in segment.path[:-1]}
    segments = [segment for segment in segments if segment.work.id not in ancestors]

    # The recording-work relationships are unordered, so order by position in
    # the parent works, keeping separate top-level works in web service order.
    root_order: dict[str, int] = {}
    for segment in segments:
        root_order.setdefault(segment.path[0].id, len(root_order))
    segments.sort(
        key=lambda segment: (
            root_order[segment.path[0].id],
            [_position_key(node) for node in segment.path[1:]],
        )
    )
    return Resolution(tuple(segments), frozenset(unknown))


def standard_tags(segments: Sequence[Segment], work_level: str = WORK_LEVEL_TOP) -> StandardTags:
    """Values for Picard's standard work and movement tags.

    With ``WORK_LEVEL_TOP`` the work tag is the top-level work and the
    movement names all the levels below it ("Akt I: No. 1 Introduktion"). With
    ``WORK_LEVEL_PARENT`` the work tag is the performed work's parent and the
    movement is the performed work only. Several works in one track are
    combined ("Passepied I / Passepied II"); the movement number is the first
    one's.
    """
    work_titles: list[str] = []
    groups: dict[str, list[tuple[Node, ...]]] = {}
    first_movement: Segment | None = None
    for segment in segments:
        path = segment.path
        split = len(path) - 2 if work_level == WORK_LEVEL_PARENT and len(path) > 1 else 0
        work = path[split]
        if work.title not in work_titles:
            work_titles.append(work.title)
        below = path[split + 1 :]
        if below:
            groups.setdefault(work.id, []).append(below)
            if first_movement is None:
                first_movement = segment

    if first_movement is None:
        return StandardTags(tuple(work_titles), None, None, None)
    movement = " / ".join(_movement_text(paths) for paths in groups.values())
    return StandardTags(
        work=tuple(work_titles),
        movement=movement,
        movementnumber=first_movement.work.number,
        movementtotal=first_movement.path[-2].part_total,
    )


def to_json(segments: Sequence[Segment]) -> str:
    document = {
        "version": FORMAT_VERSION,
        "segments": [segment.to_json() for segment in segments],
    }
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def apply_tags(
    metadata: MetadataLike,
    segments: Sequence[Segment],
    work_level: str = WORK_LEVEL_TOP,
) -> None:
    """Write the work, movement and work_hierarchy tags.

    Tags that do not apply are deleted so stale values are removed from files.
    """
    if not segments:
        metadata.delete(JSON_TAG)
        for name in MOVEMENT_TAGS:
            metadata.delete(name)
        return

    tags = standard_tags(segments, work_level)
    metadata["work"] = list(tags.work)
    values = {
        "movement": tags.movement,
        "movementnumber": tags.movementnumber,
        "movementtotal": tags.movementtotal,
        "showmovement": 1 if tags.movement else None,
    }
    for name, value in values.items():
        if value is None:
            metadata.delete(name)
        else:
            metadata[name] = str(value)
    metadata[JSON_TAG] = to_json(segments)


def display_name(
    title: str, parent_title: str | None, position: int | None
) -> tuple[str, int | None]:
    """A part's name without its parent's title, and the number taken from it.

    "Symphony no. 5 in C minor, op. 67: I. Allegro con brio" under "Symphony
    no. 5 in C minor, op. 67" at position 1 gives ("Allegro con brio", 1). The
    parent prefix does not need to match exactly; parts are often titled with
    a shortened form of their parent's title, as in "Die Zauberflöte: Akt I.
    No. 1 Introduktion" under "Die Zauberflöte, K. 620: Akt I". A leading
    number is only removed when it agrees with the position, so information
    is not lost when MusicBrainz's numbering and ordering disagree.
    """
    if parent_title is None:
        return title, None
    name = title[_parent_prefix_end(title, parent_title) :]
    number = None
    match = _NUMERAL_RE.match(name)
    if match:
        value = _numeral_value(match.group("num"))
        if value is not None and (position is None or value == position):
            name = name[match.end() :]
            number = value
    return name.strip() or title, number


def _climb(work: WorkInfo, works: Mapping[str, WorkInfo]) -> tuple[tuple[Node, ...], str | None]:
    """The path from ``work``'s top-level work down to ``work``.

    Returns the path and the ID of the ancestor that stopped the climb because
    it has not been looked up, if any.
    """
    # Each level is a work (or just a link, if not looked up) together with
    # the link from it up to the next level.
    levels: list[tuple[WorkInfo | WorkLink, WorkLink | None]] = []
    current: WorkInfo | WorkLink = work
    seen = {work.id}
    missing = None
    while True:
        if not isinstance(current, WorkInfo):
            levels.append((current, None))
            missing = current.id
            break
        link = current.parent()
        if link is None or link.id in seen or len(levels) + 1 >= MAX_DEPTH:
            levels.append((current, None))
            break
        levels.append((current, link))
        seen.add(link.id)
        current = works.get(link.id) or link

    levels.reverse()
    nodes = []
    parent: Node | None = None
    for level, parent_link in levels:
        info = level if isinstance(level, WorkInfo) else None
        # The relationship to the level above was recorded on this level.
        position = parent_link.position if parent_link else None
        name, numeral = display_name(level.title, parent.title if parent else None, position)
        node = Node(
            id=level.id,
            title=level.title,
            name=name,
            type=level.type,
            position=position,
            part_total=info.part_total if info else None,
            part_attributes=parent_link.attributes if parent_link else (),
            collections=info.collections if info else (),
            number=position if position is not None else numeral,
        )
        nodes.append(node)
        parent = node
    return tuple(nodes), missing


def _movement_text(paths: Sequence[tuple[Node, ...]]) -> str:
    """Join paths below a work, naming levels they share only once."""
    shortest = min(len(path) for path in paths)
    common = 0
    while common < shortest - 1 and len({path[common].id for path in paths}) == 1:
        common += 1
    rests: list[str] = []
    for path in paths:
        rest = ": ".join(node.name for node in path[common:])
        if rest not in rests:
            rests.append(rest)
    return ": ".join([node.name for node in paths[0][:common]] + [" / ".join(rests)])


def _parent_prefix_end(child: str, parent: str) -> int:
    """Where the child's own title starts after a prefix taken from the parent's.

    The prefix must start with the parent's first word, consist of words
    found in order in the parent's title (case and accent insensitive; parent
    words may be skipped, as in "Die Zauberflöte" for "Die Zauberflöte, K.
    620") and be followed by a separator. Returns 0 when there is no prefix.
    """
    parent_words = [_fold(word) for word in _WORD_RE.findall(parent)]
    if not parent_words:
        return 0
    end = 0
    index = 0
    for count, match in enumerate(_WORD_RE.finditer(child)):
        word = _fold(match.group())
        if count == 0:
            if word != parent_words[0]:
                return 0
            index = 1
        else:
            while index < len(parent_words) and parent_words[index] != word:
                index += 1
            if index == len(parent_words):
                break
            index += 1
        rest = child[match.end() :]
        following = rest.lstrip()[:1]
        if following and following in _SEPARATORS:
            remainder = _LEADING_SEPARATORS_RE.sub("", rest)
            if _WORD_RE.search(remainder):
                end = len(child) - len(remainder)
    return end


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _numeral_value(numeral: str) -> int | None:
    if numeral.isdigit():
        return int(numeral)
    if not _ROMAN_RE.match(numeral):
        return None
    total = 0
    for char, following in zip(numeral, numeral[1:] + " "):
        value = _ROMAN_VALUES[char]
        total += -value if value < _ROMAN_VALUES.get(following, 0) else value
    return total


def _position_key(node: Node) -> tuple[int, int]:
    return (0, node.position) if node.position is not None else (1, 0)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
