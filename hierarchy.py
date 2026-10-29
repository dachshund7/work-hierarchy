"""Work hierarchy resolution and tagging.

This module has no dependency on Picard so it can be tested on its own. It
works on the JSON documents returned by the MusicBrainz web service:

* the recording node of a release lookup made with ``work-rels`` and
  ``work-level-rels`` (what Picard requests when "Use track relationships" is
  enabled), which contains the works the recording is a performance of, plus
  each work's own work relationships (so its immediate parent is known) and,
  as ``artist-rels`` is requested too, its composers;
* work lookups made with ``inc=work-rels+artist-rels``, used to climb from a
  parent to its own parent, and so on up to the top-level work.

MusicBrainz does not record where in a recording each work starts, so nothing
here deals with timing: a track is described by the ordered list of works it
contains, and nothing more.

Composers are named by MusicBrainz's main name and sort name unless a
function is given to name them (see ArtistNamer), which the plugin uses to
follow Picard's "Translate artist names" options.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import Protocol

    class MetadataLike(Protocol):
        """The subset of picard.metadata.Metadata used for writing tags."""

        def getall(self, name: str) -> list[str]: ...

        def __setitem__(self, name: str, values: Any) -> None: ...

        def delete(self, name: str) -> None: ...


FORMAT_VERSION = 1

#: Tag holding the machine-readable description of the track's works.
JSON_TAG = "work_hierarchy"

MOVEMENT_TAGS = ("movement", "movementnumber", "movementtotal", "showmovement")

#: Tag holding when the recording was made, e.g. "1937-11-08/1938-03-08".
RECORDING_DATE_TAG = "recordingdate"

#: The relationships of a recording to artists whose dates are when it was
#: made: those of the players, singers and conductors, and the recording
#: engineer's. Others can be later, like a mix, or about the release, like
#: the phonographic copyright.
_SESSION_ARTIST_TYPES = frozenset({
    "chorus master", "concertmaster", "conductor", "instrument", "performer",
    "performing orchestra", "recording", "vocal",
})  # fmt: skip

#: Attribute of the "parts" relationship that marks a grouping of separate
#: works (e.g. "6 Partitas, BWV 825-830") rather than a work and its parts.
COLLECTION_ATTRIBUTE = "part of collection"

#: Attribute of the "performance" relationship of a recording that contains
#: only part of the work, such as one of the tracks a movement is split into.
PARTIAL_ATTRIBUTE = "partial"

#: Guard against runaway climbs through bad data.
MAX_DEPTH = 12

_WORD_RE = re.compile(r"\w+")
_SEPARATORS = ":.,;/|-–—"
_LEADING_SEPARATORS_RE = re.compile(rf"^[\s{re.escape(_SEPARATORS)}]+")
#: What can come between a parent's last word and the separator: anything
#: but words and separators, such as the closing quotes, brackets and "!"
#: in "Symphony no. 9, op. 125 “Choral”: I. Allegro", 'Kantate, BWV 214
#: "Tönet, ihr Pauken! Erschallet, Trompeten!": I. Coro' or "Kantate, BWV
#: 4 „Christ lag in Todesbanden“: I. Sinfonia", in any quoting style.
_BEFORE_SEPARATOR_RE = re.compile(rf"^[^\w{re.escape(_SEPARATORS)}]*")
#: A movement title in MusicBrainz style, "<Work>: <Roman numeral>. <Movement>".
_TITLE_MOVEMENT_RE = re.compile(r"^(?P<work>.+):\s+(?P<num>[IVXLCDM]+)\.\s+(?P<movement>\S.*)$")
_NUMERAL_RE = re.compile(r"^(?P<num>[IVXLCDM]+|\d+)\.\s+(?=\S)")
_ROMAN_RE = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

#: The catalogues of composers' works, as written before the numbers, as in
#: "BWV 846", "op. 2 no. 4", "K. 331" or "TWV 40:18".
_CATALOGUES = frozenset({
    "anh", "b", "bb", "buxwv", "bwv", "cw", "d", "fp", "fwv", "gwv", "h", "hob", "hwv", "jw", "k",
    "kv", "l", "lwv", "m", "mwv", "op", "rv", "s", "sv", "swv", "sz", "trv", "twv", "w", "wab",
    "wd", "woo", "wq", "z", "zwv",
})  # fmt: skip
#: Catalogues that number their groups in Roman numerals, as in "Hob. XVI:52".
_ROMAN_CATALOGUES = frozenset({"hob", "jw"})
#: How a number in a set is introduced, as in "op. 2 no. 4".
_NUMBER_IN_SET = frozenset({"no", "nr", "nº", "n°"})
_NAME_SEPARATORS_RE = re.compile(r"[\s,;()\[\]\"“”„]+")


@dataclass(frozen=True)
class WorkLink:
    """One side of a "parts" relationship: a work's parent or one of its parts.

    Also the work an arrangement arranges.
    """

    id: str
    title: str
    type: str | None
    #: The part's position in its parent (the relationship's ordering key).
    position: int | None
    attributes: tuple[str, ...]
    #: The comment that tells works with the same title apart, as in
    #: "for two pianos, Busoni".
    disambiguation: str = ""

    @property
    def is_collection(self) -> bool:
        return COLLECTION_ATTRIBUTE in self.attributes


#: Hyphens and dashes, U+2010 to U+2015, as well as "-", and spaces.
_HYPHENS_RE = re.compile(r"[-\u2010-\u2015\s]+")


def is_catch_all(disambiguation: str) -> bool:
    """Whether a work's disambiguation marks it as the "catch-all for
    arrangements" work that MusicBrainz keeps for the arrangements of a work
    that have no work of their own.

    Editors spell it "catch-all", "catch all" and with other hyphens.
    """
    text = _HYPHENS_RE.sub(" ", disambiguation.casefold())
    return "catch all" in text and "arrangement" in text


#: Names an artist node of the web service: its name and sort name, as the
#: tags should have them.
ArtistNamer = Callable[[Mapping[str, Any]], "tuple[str, str]"]


def main_names(artist: Mapping[str, Any]) -> tuple[str, str]:
    """An artist's main name and sort name on MusicBrainz."""
    return artist.get("name") or "", artist.get("sort-name") or ""


@dataclass(frozen=True)
class Artist:
    id: str
    name: str
    sort_name: str = field(default="", compare=False)


@dataclass(frozen=True)
class WorkInfo:
    """A work, its "parts" relationships and its composers."""

    id: str
    title: str
    type: str | None
    parents: tuple[WorkLink, ...]
    parts: tuple[WorkLink, ...]
    composers: tuple[Artist, ...] = ()
    disambiguation: str = ""
    #: The works this one is linked to as an arrangement of; see
    #: arrangement_of.
    arranges: tuple[WorkLink, ...] = ()

    @classmethod
    def from_json(cls, node: Mapping[str, Any], name_artist: ArtistNamer = main_names) -> WorkInfo:
        """A work node of the web service, its composers named by ``name_artist``."""
        parents = []
        parts = []
        arranges = []
        composers = []
        for rel in node.get("relations") or ():
            if rel.get("target-type") == "artist" and rel.get("type") == "composer":
                artist = rel.get("artist") or {}
                composer = Artist(artist.get("id") or "", *name_artist(artist))
                if composer.id and composer not in composers:
                    composers.append(composer)
                continue
            if rel.get("target-type") != "work" or rel.get("type") not in ("parts", "arrangement"):
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
                disambiguation=target.get("disambiguation") or "",
            )
            backward = rel.get("direction") == "backward"
            if rel.get("type") == "arrangement":
                # Forward, the works that arrange this one, which don't
                # matter here
                if backward:
                    arranges.append(link)
            elif backward:
                parents.append(link)
            else:
                parts.append(link)
        return cls(
            id=node["id"],
            title=node.get("title") or "",
            type=node.get("type"),
            parents=tuple(parents),
            parts=tuple(parts),
            composers=tuple(composers),
            disambiguation=node.get("disambiguation") or "",
            arranges=tuple(arranges),
        )

    @property
    def arrangement_of(self) -> WorkLink | None:
        """The work this one arranges, if it's an arrangement.

        An arrangement is linked backward to the work it arranges. Some
        originals are linked backward to their own catch-all for arrangements
        too, the wrong way round, so a catch-all is never taken for the work
        arranged.
        """
        return next((link for link in self.arranges if not is_catch_all(link.disambiguation)), None)

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
    #: None when the work was not looked up, like part_total.
    composers: tuple[Artist, ...] | None
    #: Part number to display: the position, or a number parsed from the title.
    number: int | None
    #: The numeral removed from the start of the name, as the title writes
    #: it ("III" for "III. Sequentia"), or None.
    numeral: str | None
    #: None when the work was not looked up, like part_total.
    disambiguation: str | None = None
    #: The work this one arranges (see WorkInfo.arrangement_of); also None
    #: when the work was not looked up.
    arrangement_of: WorkLink | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "name": self.name,
            "type": self.type,
            "position": self.position,
            "number": self.number,
            "numeral": self.numeral,
            "part_total": self.part_total,
            "part_attributes": list(self.part_attributes),
            "collections": [
                {"id": link.id, "title": link.title, "position": link.position}
                for link in self.collections
            ],
            "composers": None
            if self.composers is None
            else [{"id": artist.id, "name": artist.name} for artist in self.composers],
            "disambiguation": self.disambiguation,
            "arrangement_of": None
            if self.arrangement_of is None
            else {"id": self.arrangement_of.id, "title": self.arrangement_of.title},
        }


@dataclass(frozen=True)
class Segment:
    """One work performed in a track, with its path from the top-level work."""

    #: Top-level work first, the work the recording performs last.
    path: tuple[Node, ...]
    attributes: tuple[str, ...]
    #: Which node of the path is the work, rather than a part of one (see
    #: _work_index).
    work_index: int = 0
    #: For a recording of only part of the performed work, the name its title
    #: gives that part, when that isn't the performed work's own name (see
    #: with_excerpts).
    excerpt: str | None = None

    @property
    def performed(self) -> Node:
        return self.path[-1]

    def to_json(self) -> dict[str, Any]:
        return {
            "path": [node.to_json() for node in self.path],
            "work_index": self.work_index,
            "attributes": list(self.attributes),
            "excerpt": self.excerpt,
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


def performances(
    recording: Mapping[str, Any], name_artist: ArtistNamer = main_names
) -> list[Performance]:
    """The works a recording node is a performance of, in web service order."""
    result = []
    for rel in recording.get("relations") or ():
        if rel.get("target-type") != "work" or rel.get("type") != "performance":
            continue
        work = rel.get("work") or {}
        if work.get("id"):
            work_info = WorkInfo.from_json(work, name_artist)
            result.append(Performance(work_info, tuple(rel.get("attributes") or ())))
    return result


def recording_date(recording: Mapping[str, Any]) -> str | None:
    """When a recording node was made, from the dates of its relationships.

    The dates are those MusicBrainz gives the performance of its works and
    the sessions of its artists (see _SESSION_ARTIST_TYPES), which Picard
    loads with "Use track relationships". The result runs from the earliest
    to the latest of them, as an ISO 8601 interval, "1937-11-08/1938-03-08",
    or is a single date when they're all the same. Dates can be partial,
    such as "2011-12" or "1992". None when no relationship has a date.
    """
    dates = []
    for rel in recording.get("relations") or ():
        target, kind = rel.get("target-type"), rel.get("type")
        if (target, kind) != ("work", "performance") and not (
            target == "artist" and kind in _SESSION_ARTIST_TYPES
        ):
            continue
        dates.extend(date for date in (rel.get("begin"), rel.get("end")) if date)
    if not dates:
        return None
    # ISO dates sort in time order, a partial one before the days it covers
    first, last = min(dates), max(dates)
    return first if first == last else f"{first}/{last}"


def apply_recording_date(metadata: MetadataLike, recording: Mapping[str, Any]) -> None:
    """Write the recordingdate tag, or delete it when MusicBrainz has no date."""
    date = recording_date(recording)
    if date is None:
        metadata.delete(RECORDING_DATE_TAG)
    else:
        metadata[RECORDING_DATE_TAG] = date


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
    segments = [segment for segment in segments if segment.performed.id not in ancestors]

    # The recording-work relationships are unordered, so order by position in
    # the parent works, keeping separate top-level works in web service order.
    by_root: dict[str, list[Segment]] = {}
    for segment in segments:
        by_root.setdefault(segment.path[0].id, []).append(segment)
    ordered: list[Segment] = []
    for group in by_root.values():
        group.sort(key=lambda segment: [_position_key(node) for node in segment.path[1:]])
        work_index = _work_index([segment.path for segment in group])
        ordered.extend(replace(segment, work_index=work_index) for segment in group)
    return Resolution(tuple(ordered), frozenset(unknown))


def standard_tags(segments: Sequence[Segment]) -> StandardTags:
    """Values for Picard's standard work and movement tags.

    The work tag is the work, usually the top-level one (see _work_index).
    The movement names the levels below it, numbered as their titles number
    them ("Akt I: No. 1 Introduktion", "I. oddíl: III. Sequentia: Quid sum
    miser"), the performed work last, by its excerpt if it has one. Several
    works in one track are combined ("Passepied I / Passepied II"). The
    movement number is the first one's, and left out of the movement tag.
    """
    work_titles: list[str] = []
    movements: dict[str, list[tuple[tuple[Node, ...], str]]] = {}
    first_movement: Segment | None = None
    for segment in segments:
        work = segment.path[segment.work_index]
        if work.title not in work_titles:
            work_titles.append(work.title)
        if segment.work_index < len(segment.path) - 1:
            levels = segment.path[segment.work_index + 1 : -1]
            name = segment.excerpt or segment.performed.name
            movements.setdefault(work.id, []).append((levels, name))
            if first_movement is None:
                first_movement = segment

    if first_movement is None:
        return StandardTags(tuple(work_titles), None, None, None)
    return StandardTags(
        work=tuple(work_titles),
        movement=" / ".join(_movement_text(group) for group in movements.values()),
        movementnumber=first_movement.performed.number,
        movementtotal=first_movement.path[-2].part_total,
    )


def title_tags(segments: Sequence[Segment], recording_title: str | None) -> StandardTags | None:
    """Work and movement tags read from titles, when MusicBrainz doesn't link them.

    Many movements are linked to a work of their own, titled "Suite no. 1 in
    G major, BWV 1007: I. Prélude", that isn't a part of the suite, and
    many recordings aren't linked to works at all. If the performed works
    have no parent and aren't in a collection, or if there are none, their
    titles, or the recording's, are read as "<Work>: <Roman numeral>.
    <Movement>", the way the Work & Movement plugin does. Returns None if the
    titles don't all name movements of the same work.
    """
    if segments:
        if any(len(segment.path) > 1 or segment.performed.collections for segment in segments):
            return None
        titles = [segment.performed.title for segment in segments]
    elif recording_title:
        titles = [recording_title]
    else:
        return None

    movements: list[str] = []
    works = set()
    number = None
    for title in titles:
        match = _TITLE_MOVEMENT_RE.match(title)
        value = _numeral_value(match.group("num")) if match else None
        if match is None or value is None:
            return None
        works.add(match.group("work"))
        if number is None:
            number = value
        if match.group("movement") not in movements:
            movements.append(match.group("movement"))
    if len(works) != 1:
        return None
    return StandardTags(
        work=(works.pop(),),
        movement=" / ".join(movements),
        movementnumber=number,
        movementtotal=None,
    )


def to_json(segments: Sequence[Segment]) -> str:
    document = {
        "version": FORMAT_VERSION,
        "segments": [segment.to_json() for segment in segments],
    }
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def inherited_composers(segments: Sequence[Segment]) -> tuple[Artist, ...]:
    """Composers of ancestor works, for performed works that have none.

    MusicBrainz often links a composer to a work but not to its parts. Each
    performed work without composers takes those of its nearest ancestor that
    has some.
    """
    result: dict[str, Artist] = {}
    for segment in segments:
        if segment.performed.composers:
            continue
        for node in reversed(segment.path[:-1]):
            if node.composers:
                for artist in node.composers:
                    result.setdefault(artist.id, artist)
                break
    return tuple(result.values())


def apply_composer_tags(metadata: MetadataLike, segments: Sequence[Segment]) -> None:
    """Add inherited composers (see inherited_composers) to the composer tags.

    Composers already in the tags, by ID, are not added again, so a track
    with linked and unlinked works keeps Picard's names for the linked ones,
    and tags written again, as for a standalone recording once its lookups
    have finished, get nothing twice. Other values are left as they are.
    """
    present = set(metadata.getall("musicbrainz_composerid"))
    extra = [artist for artist in inherited_composers(segments) if artist.id not in present]
    if not extra:
        return
    metadata["composer"] = [*metadata.getall("composer"), *(artist.name for artist in extra)]
    metadata["composersort"] = [
        *metadata.getall("composersort"),
        *(artist.sort_name or artist.name for artist in extra),
    ]
    metadata["musicbrainz_composerid"] = [
        *metadata.getall("musicbrainz_composerid"),
        *(artist.id for artist in extra),
    ]


def apply_tags(
    metadata: MetadataLike, segments: Sequence[Segment], recording_title: str | None = None
) -> None:
    """Write the work, movement and work_hierarchy tags, and inherited composers.

    Tags that do not apply are deleted so stale values are removed from files.
    The work_hierarchy tag only describes the works MusicBrainz links, so a
    work and movement read from titles instead (see title_tags) only go in
    the standard tags. Composers are added as apply_composer_tags describes.
    """
    segments = with_excerpts(segments, recording_title)
    from_titles = title_tags(segments, recording_title)
    if segments:
        _write_standard_tags(metadata, from_titles or standard_tags(segments))
        metadata[JSON_TAG] = to_json(segments)
    else:
        metadata.delete(JSON_TAG)
        if from_titles is None:
            for name in MOVEMENT_TAGS:
                metadata.delete(name)
        else:
            _write_standard_tags(metadata, from_titles)
    apply_composer_tags(metadata, segments)


def _write_standard_tags(metadata: MetadataLike, tags: StandardTags) -> None:
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


def display_name(
    title: str, parent_title: str | None, position: int | None
) -> tuple[str, int | None, str | None]:
    """A part's name without its parent's title, and the number taken from it.

    "Symphony no. 5 in C minor, op. 67: I. Allegro con brio" under "Symphony
    no. 5 in C minor, op. 67" at position 1 gives ("Allegro con brio", 1,
    "I"): the name, the number and the numeral it was written as. The
    parent prefix does not need to match exactly; parts are often titled with
    a shortened form of their parent's title, as in "Die Zauberflöte: Akt I.
    No. 1 Introduktion" under "Die Zauberflöte, K. 620: Akt I". A leading
    number is only removed when it agrees with the position, so information
    is not lost when MusicBrainz's numbering and ordering disagree.
    """
    if parent_title is None:
        return title, None, None
    name = title[_parent_prefix_end(title, parent_title) :]
    number = numeral = None
    match = _NUMERAL_RE.match(name)
    if match:
        value = _numeral_value(match.group("num"))
        if value is not None and (position is None or value == position):
            name = name[match.end() :]
            number, numeral = value, match.group("num")
    return name.strip() or title, number, numeral


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
        name, number, numeral = display_name(
            level.title, parent.title if parent else None, position
        )
        node = Node(
            id=level.id,
            title=level.title,
            name=name,
            type=level.type,
            position=position,
            part_total=info.part_total if info else None,
            part_attributes=parent_link.attributes if parent_link else (),
            collections=info.collections if info else (),
            composers=info.composers if info else None,
            number=position if position is not None else number,
            numeral=numeral,
            disambiguation=info.disambiguation if info else None,
            arrangement_of=info.arrangement_of if info else None,
        )
        nodes.append(node)
        parent = node
    return tuple(nodes), missing


def _movement_text(movements: Sequence[tuple[tuple[Node, ...], str]]) -> str:
    """Join movements below a work, naming the levels they share only once.

    Each movement is the levels between the work and the performed work, and
    the performed work's name.
    """
    shortest = min(len(levels) for levels, _ in movements)
    common = 0
    while common < shortest and len({levels[common].id for levels, _ in movements}) == 1:
        common += 1
    rests: list[str] = []
    for levels, name in movements:
        rest = ": ".join([*map(_level_name, levels[common:]), name])
        if rest not in rests:
            rests.append(rest)
    shared = movements[0][0][:common]
    return ": ".join([*map(_level_name, shared), " / ".join(rests)])


def _level_name(node: Node) -> str:
    """A level's name with the number its title gives it, as in "III. Sequentia"."""
    return f"{node.numeral}. {node.name}" if node.numeral else node.name


def with_excerpts(segments: Sequence[Segment], recording_title: str | None) -> list[Segment]:
    """The segments, with the excerpts the recording's title names.

    MusicBrainz links each recording of part of a work, such as the tracks a
    movement is split into, to the whole work, which names them all the
    same ("Ouverture"). The recording's title names the part: "Music for the
    Royal Fireworks, HWV 351: Ia. Overture – Adagio" is read as the title of
    a part of the performed work's parent would be (see display_name), giving
    "Ia. Overture – Adagio". A title in another language, not starting with
    the parent's, is read as "<Work>: <Part>" or "<Work> - <Part>".
    """
    result = []
    for segment in segments:
        excerpt = None
        if recording_title and PARTIAL_ATTRIBUTE in segment.attributes and len(segment.path) > 1:
            excerpt = _excerpt(recording_title, segment.path[-2], segment.performed)
        result.append(replace(segment, excerpt=excerpt) if excerpt else segment)
    return result


def _excerpt(title: str, parent: Node, performed: Node) -> str | None:
    if not _parent_prefix_end(title, parent.title):
        _, separator, rest = title.partition(": ")
        if not separator:
            rest = title.partition(" - ")[2]
        title = rest.strip()
        if not title:
            return None
    name = display_name(title, parent.title, performed.position)[0]
    return None if name == performed.name else name


def _work_index(paths: Sequence[tuple[Node, ...]]) -> int:
    """Which level of the paths, all from one top-level work, is the work.

    The top-level work, unless a part of it has a catalogue number of its
    own, one the works above it don't have, as each Prelude and Fugue of The
    Well-Tempered Clavier (BWV 846 and on) does but its books, or the acts of
    an opera, don't: the work is then the deepest such part, if every path
    goes through it. The names of movements often repeat their work's
    number, or give it another one ("K359: Variation I" in "12 Variations, K.
    374a/359"), so the performed work only counts if it has parts, like a
    whole Prelude and Fugue in one track.
    """
    first = paths[0]
    for index in range(len(first) - 1, 0, -1):
        node = first[index]
        if _has_own_catalogue_number(node, first[:index]) and all(
            index < len(path)
            and path[index].id == node.id
            and (index < len(path) - 1 or bool(path[index].part_total))
            for path in paths
        ):
            return index
    return 0


def _has_own_catalogue_number(node: Node, above: Sequence[Node]) -> bool:
    theirs = {number for work in above for number in _catalogue_numbers(work.name)}
    return any(number not in theirs for number in _catalogue_numbers(node.name))


def _catalogue_numbers(name: str) -> list[str]:
    """The catalogue numbers in a work's name, lowercase and without dots.

    "bwv 846" in "Prelude and Fugue no. 1 in C major, BWV 846", or "op 2 no
    4" in "Sonata da camera a trè in E minor, op. 2 no. 4"; none in "Book I"
    or "Act II".
    """
    words = [word for word in _NAME_SEPARATORS_RE.split(name) if word]

    def catalogue(word: str) -> str | None:
        word = word.removesuffix(".").lower()
        return word if word in _CATALOGUES else None

    def is_number(catalogue: str, number: str) -> bool:
        return _starts_with_digit(number) or (
            catalogue in _ROMAN_CATALOGUES and number[:1] in ("I", "V", "X")
        )

    numbers = []
    for i, word in enumerate(words):
        following = words[i + 1] if i + 1 < len(words) else ""
        found = catalogue(word)
        if found and is_number(found, following):
            number, after = following, i + 2
        else:
            # Written as one word, as in "op.2" or "W389".
            split = next((j for j, char in enumerate(word) if not char.isalpha()), len(word))
            found, number = catalogue(word[:split]), word[split:].removeprefix(".")
            if not found or not is_number(found, number):
                continue
            after = i + 1
        result = f"{found} {number.rstrip('.:').lower()}"
        # A number in a set, as in "op. 2 no. 4", unless the name of a part
        # follows a colon, as in "op. 90, D. 899: No. 1".
        if not number.endswith(":") and after + 1 < len(words):
            no, set_number = words[after : after + 2]
            if no.rstrip(".").lower() in _NUMBER_IN_SET and _starts_with_digit(set_number):
                result += f" no {set_number.rstrip('.:').lower()}"
        numbers.append(result)
    return numbers


def _starts_with_digit(text: str) -> bool:
    return "0" <= text[:1] <= "9"


def _parent_prefix_end(child: str, parent: str) -> int:
    """Where the child's own title starts after a prefix taken from the parent's.

    The prefix must start with the parent's first word, consist of words
    found in order in the parent's title (case and accent insensitive; parent
    words may be skipped, as in "Die Zauberflöte" for "Die Zauberflöte, K.
    620") and be followed by a separator, possibly after closing quotes,
    brackets or other punctuation. A comma or period followed by the
    parent's next word is the parent's own, as in "no. 14" or "K. 620", so
    the child is still quoting it there. Returns 0 when there is no prefix.
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
        rest = _BEFORE_SEPARATOR_RE.sub("", child[match.end() :])
        separator = rest[:1]
        if not separator or separator not in _SEPARATORS:
            continue
        remainder = _LEADING_SEPARATORS_RE.sub("", rest)
        next_word = _WORD_RE.search(remainder)
        if next_word is None:
            continue
        if (
            separator in ".,"
            and index < len(parent_words)
            and _fold(next_word.group()) == parent_words[index]
        ):
            continue
        end = len(child) - len(remainder)
    return end


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _numeral_value(numeral: str) -> int | None:
    if numeral.isdigit():
        return int(numeral)
    # A single C, D, L or M is a letter, as in "C. Credo", or the catalogue
    # in Schubert's "D. 899" or Scarlatti's "L. 413", not 100, 500, 50 or 1000.
    if numeral in ("C", "D", "L", "M") or not _ROMAN_RE.match(numeral):
        return None
    total = 0
    for char, following in zip(numeral, numeral[1:] + " ", strict=False):
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
