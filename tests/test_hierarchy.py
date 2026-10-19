"""Work hierarchy logic, without Picard."""

import json

import pytest
from conftest import WORKS, load_fixture, load_work

from work_hierarchy.hierarchy import (
    JSON_TAG,
    MAX_DEPTH,
    WORK_LEVEL_PARENT,
    Performance,
    WorkInfo,
    WorkLink,
    _numeral_value,
    apply_tags,
    display_name,
    performances,
    resolve,
    standard_tags,
    title_tags,
    to_json,
)

ALL_WORKS = {path.stem: WorkInfo.from_json(load_work(path.stem)) for path in WORKS.glob("*.json")}


def recording(release: str, disc: int, number: int) -> dict:
    medium = load_fixture(release)["media"][disc - 1]
    return medium["tracks"][number - 1]["recording"]


def work(work_id, title, parents=(), parts=(), type=None):
    return WorkInfo(work_id, title, type, tuple(parents), tuple(parts))


def link(work_id, title, position=None, attributes=()):
    return WorkLink(work_id, title, None, position, tuple(attributes))


def performed(*works, attributes=()):
    return [Performance(w, tuple(attributes)) for w in works]


class FakeMetadata(dict):
    def __init__(self):
        super().__init__()
        self.deleted = set()

    def __setitem__(self, name, values):
        self.deleted.discard(name)
        super().__setitem__(name, values)

    def delete(self, name):
        self.pop(name, None)
        self.deleted.add(name)


# Titles


@pytest.mark.parametrize(
    ("title", "parent", "position", "expected"),
    [
        # Exact parent prefix and a matching numeral.
        (
            "Symphony no. 5 in C minor, op. 67: I. Allegro con brio",
            "Symphony no. 5 in C minor, op. 67",
            1,
            ("Allegro con brio", 1),
        ),
        # Shortened parent prefix.
        (
            "Die Zauberflöte: Akt I. No. 1 Introduktion",
            "Die Zauberflöte, K. 620: Akt I",
            1,
            ("No. 1 Introduktion", None),
        ),
        ("Die Zauberflöte: Ouvertüre", "Die Zauberflöte, K. 620", 1, ("Ouvertüre", None)),
        ("Symphony No. 5: III. Scherzo", "Symphony no. 5 in C minor, op. 67", 3, ("Scherzo", 3)),
        # Case and accents are ignored when matching the prefix.
        ("Messe Solennelle: Kyrie", "Messe solennelle", 1, ("Kyrie", None)),
        ("Tosca: Act I. Recondita armonia", "Tosca", None, ("Act I. Recondita armonia", None)),
        # A numeral that disagrees with the position is kept.
        ("Suite: IV. Passepied", "Suite", 5, ("IV. Passepied", None)),
        # Arabic numerals, and numerals used when the position is unknown.
        ("Mass: 2. Gloria", "Mass", 2, ("Gloria", 2)),
        ("Mass: II. Gloria", "Mass", None, ("Gloria", 2)),
        # No prefix in common.
        ("Gute Nacht", "Winterreise, D. 911", 1, ("Gute Nacht", None)),
        # A word in common but no separator after it.
        ("Gloria in excelsis Deo", "Gloria, RV 589", 1, ("Gloria in excelsis Deo", None)),
        # Nothing would be left.
        ("Akt I", "Akt I", 1, ("Akt I", None)),
        # Not a Roman numeral.
        ("Op. 10: IIII. Etude", "Op. 10", 4, ("IIII. Etude", None)),
    ],
)
def test_display_name(title, parent, position, expected):
    assert display_name(title, parent, position) == expected


def test_display_name_of_top_level_work_is_its_title():
    assert display_name("Die Zauberflöte, K. 620", None, None) == ("Die Zauberflöte, K. 620", None)


@pytest.mark.parametrize(
    ("numeral", "value"),
    [("I", 1), ("IV", 4), ("IX", 9), ("XIV", 14), ("XL", 40), ("MCMXC", 1990), ("12", 12)],
)
def test_numeral_value(numeral, value):
    assert _numeral_value(numeral) == value


@pytest.mark.parametrize("numeral", ["IIII", "IC", "VX", "MMMM"])
def test_invalid_roman_numerals(numeral):
    assert _numeral_value(numeral) is None


# Parents


def test_collections_are_not_parents():
    partita = ALL_WORKS["ddd67114-e7d5-4b79-bffc-38d6e9ea4ca9"]
    assert partita.parent() is None
    assert [c.title for c in partita.collections] == ["6 Partitas, BWV 825-830"]


def test_parent_whose_title_the_work_extends_is_preferred():
    child = work(
        "c",
        "Winterreise, D. 911: No. 1. Gute Nacht",
        parents=[
            link("x", "Schubert: Favourite Songs", 3),
            link("w", "Winterreise, D. 911", 1),
        ],
    )
    assert child.parent().id == "w"


def test_parent_with_position_is_preferred_when_titles_do_not_help():
    child = work("c", "Ave Maria", parents=[link("x", "Collection A"), link("y", "Songs", 2)])
    assert child.parent().id == "y"


def test_part_total_is_the_highest_position():
    # Act I's positions have gaps, and it has unnumbered appendix parts.
    act = ALL_WORKS["5adc213f-700a-4435-9e95-831ed720f348"]
    positions = [part.position for part in act.parts if part.position is not None]
    assert len(positions) < max(positions)
    assert len(act.parts) > len(positions)
    assert act.part_total == max(positions)


def test_part_total_without_positions_counts_parts():
    assert work("w", "W", parts=[link("a", "A"), link("b", "B")]).part_total == 2


# Resolving


def test_track_with_two_movements_in_reverse_web_service_order():
    # Track 6 lists Bourree II before Bourree I.
    performed = performances(recording("bach_keyboard_suites", 1, 6))
    assert [p.work.title.split(": ")[-1] for p in performed] == [
        "IX. Bourree II",
        "VIII. Bourree I",
    ]

    resolution = resolve(performed, ALL_WORKS)

    assert resolution.unknown == frozenset()
    assert [[node.name for node in s.path] for s in resolution.segments] == [
        ["Ouvertüre nach französischer Art h-Moll, BWV 831", "Bourree I"],
        ["Ouvertüre nach französischer Art h-Moll, BWV 831", "Bourree II"],
    ]


def test_unknown_ancestors_are_reported():
    performed = performances(recording("zauberfloete", 1, 2))

    resolution = resolve(performed, {})

    assert resolution.unknown == {"5adc213f-700a-4435-9e95-831ed720f348"}
    # The act is still included from what the number's relationship says.
    [segment] = resolution.segments
    assert [node.title for node in segment.path] == [
        "Die Zauberflöte, K. 620: Akt I",
        "Die Zauberflöte: Akt I. No. 1 Introduktion „Zu Hilfe! Zu Hilfe!” (Tamino, Damen)",
    ]
    assert segment.path[0].part_total is None


def test_climb_continues_once_parent_is_known():
    performed = performances(recording("zauberfloete", 1, 2))
    act = "5adc213f-700a-4435-9e95-831ed720f348"

    resolution = resolve(performed, {act: ALL_WORKS[act]})

    assert resolution.unknown == {"e208c5f5-5d37-3dfc-ac0b-999f207c9e46"}
    assert len(resolution.segments[0].path) == 3


def test_failed_ancestors_are_not_reported_again():
    performed = performances(recording("zauberfloete", 1, 2))
    resolution = resolve(performed, {}, failed=["5adc213f-700a-4435-9e95-831ed720f348"])
    assert resolution.unknown == frozenset()


def test_work_and_its_part_both_linked():
    whole = work("w", "Suite", parts=[link("p", "Suite: I. Prelude", 1)])
    part = work("p", "Suite: I. Prelude", parents=[link("w", "Suite", 1)])

    resolution = resolve(performed(whole, part), {"w": whole})

    assert [s.work.id for s in resolution.segments] == ["p"]


def test_duplicate_performances_are_merged():
    song = work("s", "Song")
    resolution = resolve(performed(song, song), {})
    assert len(resolution.segments) == 1


def test_separate_top_level_works_keep_their_order():
    b = work("b2", "B: II. Two", parents=[link("b", "B", 2)])
    a = work("a1", "A: I. One", parents=[link("a", "A", 1)])
    resolution = resolve(performed(b, a), {})
    assert [s.path[0].id for s in resolution.segments] == ["b", "a"]


def test_cycles_stop_the_climb():
    a = work("a", "A", parents=[link("b", "B", 1)])
    b = work("b", "B", parents=[link("a", "A", 1)])
    resolution = resolve(performed(a), {"a": a, "b": b})
    assert [node.id for node in resolution.segments[0].path] == ["b", "a"]
    assert resolution.unknown == frozenset()


def test_depth_is_limited():
    works = {
        str(i): work(str(i), f"W{i}", parents=[link(str(i + 1), f"W{i + 1}", 1)])
        for i in range(MAX_DEPTH * 2)
    }
    resolution = resolve(performed(works["0"]), works)
    assert len(resolution.segments[0].path) == MAX_DEPTH


def test_performance_attributes():
    resolution = resolve(performances(recording("zauberfloete", 1, 11)), ALL_WORKS)
    assert [s.attributes for s in resolution.segments] == [("partial",)]


def test_every_fixture_track_resolves_completely():
    for release in ("bach_keyboard_suites", "zauberfloete"):
        for medium in load_fixture(release)["media"]:
            for t in medium["tracks"]:
                resolution = resolve(performances(t["recording"]), ALL_WORKS)
                assert resolution.unknown == frozenset()
                tags = standard_tags(resolution.segments)
                assert tags.work, t["title"]
                assert tags.movement, t["title"]
                assert tags.movementnumber is not None
                assert tags.movementtotal is not None
                assert tags.movementnumber <= tags.movementtotal, t["title"]


# Standard tags


def zauberfloete_segments(disc, number):
    return resolve(performances(recording("zauberfloete", disc, number)), ALL_WORKS).segments


def test_standard_tags_top_level():
    tags = standard_tags(zauberfloete_segments(1, 4))
    assert tags.work == ("Die Zauberflöte, K. 620",)
    assert tags.movement == "Akt I: No. 3 Arie „Dies Bildnis ist bezaubernd schön“ (Tamino)"
    assert (tags.movementnumber, tags.movementtotal) == (6, 31)


def test_standard_tags_parent_level():
    tags = standard_tags(zauberfloete_segments(1, 4), WORK_LEVEL_PARENT)
    assert tags.work == ("Die Zauberflöte, K. 620: Akt I",)
    assert tags.movement == "No. 3 Arie „Dies Bildnis ist bezaubernd schön“ (Tamino)"
    assert (tags.movementnumber, tags.movementtotal) == (6, 31)


def test_standard_tags_shared_levels_named_once():
    tags = standard_tags(zauberfloete_segments(2, 20))
    assert tags.movement == (
        "Akt II: „Die Strahlen der Sonne vertreiben die Nacht” (Sarastro)"
        " / „Heil sei euch Geweihten” (Chor)"
    )
    assert tags.movementnumber == 35


def test_standard_tags_movements_from_different_levels():
    opera = ALL_WORKS["e208c5f5-5d37-3dfc-ac0b-999f207c9e46"]
    overture = performances(recording("zauberfloete", 1, 1))
    number = performances(recording("zauberfloete", 1, 2))
    tags = standard_tags(resolve(overture + number, ALL_WORKS).segments)
    assert tags.work == (opera.title,)
    assert tags.movement == (
        "Ouvertüre / Akt I: No. 1 Introduktion „Zu Hilfe! Zu Hilfe!” (Tamino, Damen)"
    )


def test_standard_tags_for_a_work_without_parent():
    tags = standard_tags(resolve(performed(work("s", "Erlkönig, D. 328")), {}).segments)
    assert tags.work == ("Erlkönig, D. 328",)
    assert tags.movement is None
    assert tags.movementnumber is None


def test_standard_tags_for_a_medley_of_songs():
    songs = performed(work("a", "Song A"), work("b", "Song B"))
    tags = standard_tags(resolve(songs, {}).segments)
    assert tags.work == ("Song A", "Song B")
    assert tags.movement is None


# Writing tags


def test_apply_tags():
    metadata = FakeMetadata()
    segments = resolve(performances(recording("bach_keyboard_suites", 1, 4)), ALL_WORKS).segments

    apply_tags(metadata, segments)

    assert metadata["work"] == ["Ouvertüre nach französischer Art h-Moll, BWV 831"]
    assert metadata["movement"] == "Passepied I / Passepied II"
    assert metadata["movementnumber"] == "5"
    assert metadata["movementtotal"] == "11"
    assert metadata["showmovement"] == "1"
    assert json.loads(metadata[JSON_TAG]) == json.loads(to_json(segments))
    assert metadata.deleted == set()


def test_apply_tags_without_movement_removes_movement_tags():
    metadata = FakeMetadata()
    apply_tags(metadata, resolve(performed(work("s", "Erlkönig")), {}).segments)
    assert metadata["work"] == ["Erlkönig"]
    assert metadata.deleted == {"movement", "movementnumber", "movementtotal", "showmovement"}


def test_apply_tags_without_works_removes_tags_but_keeps_work():
    metadata = FakeMetadata()
    apply_tags(metadata, ())
    assert metadata.deleted == {
        JSON_TAG,
        "movement",
        "movementnumber",
        "movementtotal",
        "showmovement",
    }


def test_json_format():
    segments = resolve(performances(recording("zauberfloete", 1, 2)), ALL_WORKS).segments

    document = json.loads(to_json(segments))

    assert document["version"] == 1
    [segment] = document["segments"]
    assert segment["attributes"] == []
    opera, act, number = segment["path"]
    assert opera == {
        "id": "e208c5f5-5d37-3dfc-ac0b-999f207c9e46",
        "title": "Die Zauberflöte, K. 620",
        "name": "Die Zauberflöte, K. 620",
        "type": "Opera",
        "position": None,
        "part_total": 3,
        "part_attributes": [],
        "collections": [],
        "composers": [
            {"id": "b972f589-fb0e-474e-b64a-803b0364fa75", "name": "Wolfgang Amadeus Mozart"}
        ],
    }
    assert (act["name"], act["position"], act["part_total"]) == ("Akt I", 2, 31)
    assert act["part_attributes"] == ["act"]
    assert (number["position"], number["part_total"]) == (1, 0)


def test_json_includes_collections():
    segments = resolve(performances(recording("bach_keyboard_suites", 1, 9)), ALL_WORKS).segments
    partita = json.loads(to_json(segments))["segments"][0]["path"][0]
    assert partita["collections"] == [
        {
            "id": "3c1d42c1-0cdc-402e-8a0b-771b79c60742",
            "title": "6 Partitas, BWV 825-830",
            "position": 1,
        }
    ]


def test_json_includes_composers():
    segments = resolve(performances(recording("bach_keyboard_suites", 1, 4)), ALL_WORKS).segments
    bach = [{"id": "24f1766e-9635-4d58-a4d4-9413f9f98a4c", "name": "Johann Sebastian Bach"}]
    for segment in json.loads(to_json(segments))["segments"]:
        # The performed movements come from the release, the suite from its lookup
        assert [node["composers"] for node in segment["path"]] == [bach, bach]


def test_composers_are_unknown_for_works_not_looked_up():
    segments = resolve(performances(recording("zauberfloete", 1, 2)), {}).segments
    # The path stops at the act, which is known only from the number's
    # relationship to it
    act, number = json.loads(to_json(segments))["segments"][0]["path"]
    assert act["composers"] is None
    assert number["composers"] == [
        {"id": "b972f589-fb0e-474e-b64a-803b0364fa75", "name": "Wolfgang Amadeus Mozart"}
    ]


def test_composers_are_read_once_each():
    node = {
        "id": "w",
        "title": "Trio",
        "relations": [
            {"target-type": "artist", "type": "composer", "artist": {"id": "a", "name": "A"}},
            {"target-type": "artist", "type": "composer", "artist": {"id": "a", "name": "A"}},
            {"target-type": "artist", "type": "lyricist", "artist": {"id": "b", "name": "B"}},
        ],
    }
    assert [c.id for c in WorkInfo.from_json(node).composers] == ["a"]


# Work and movement from titles


def test_unlinked_movement_is_read_from_its_title():
    prelude = work("p", "Suite for Cello Solo no. 1 in G major, BWV 1007: I. Prélude")
    metadata = FakeMetadata()
    segments = resolve(performed(prelude), {}).segments

    apply_tags(metadata, segments)

    assert metadata["work"] == ["Suite for Cello Solo no. 1 in G major, BWV 1007"]
    assert metadata["movement"] == "Prélude"
    assert metadata["movementnumber"] == "1"
    assert metadata["showmovement"] == "1"
    assert "movementtotal" in metadata.deleted
    # The JSON still says only what MusicBrainz links
    [segment] = json.loads(metadata[JSON_TAG])["segments"]
    assert [node["title"] for node in segment["path"]] == [prelude.title]


def test_recording_title_is_read_without_works():
    tags = title_tags((), "Introit: Gaudeamus omnes: II. Gloria")
    assert tags is not None
    assert (tags.work, tags.movement, tags.movementnumber, tags.movementtotal) == (
        ("Introit: Gaudeamus omnes",),
        "Gloria",
        2,
        None,
    )


def test_unlinked_movements_of_the_same_work_are_joined():
    segments = resolve(
        performed(work("a", "Suite: V. Passepied I"), work("b", "Suite: VI. Passepied II")), {}
    ).segments
    tags = title_tags(segments, None)
    assert tags is not None
    assert (tags.work, tags.movement, tags.movementnumber) == (
        ("Suite",),
        "Passepied I / Passepied II",
        5,
    )
    # Not if they are movements of different works
    segments = resolve(performed(work("a", "Suite: V. Air"), work("b", "Partita: I. Air")), {})
    assert title_tags(segments.segments, None) is None


@pytest.mark.parametrize(
    "title",
    [
        "Erlkönig, D. 328",
        "Slavonic Dances, op. 72: No. 1 in B major",
        "Mass in B minor: Kyrie",
        "Suite: IIII. Gigue",
    ],
)
def test_titles_that_are_not_movements(title):
    assert title_tags(resolve(performed(work("w", title)), {}).segments, None) is None
    assert title_tags((), title) is None


def test_titles_are_not_read_when_musicbrainz_links_the_parent():
    suite = work("s", "Suite", parts=[link("p", "Suite: I. Prélude", 1)])
    prelude = work("p", "Suite: I. Prélude", parents=[link("s", "Suite")])
    segments = resolve(performed(prelude), {"s": suite}).segments
    assert title_tags(segments, None) is None


def test_titles_are_not_read_for_works_in_a_collection():
    waltz = work(
        "w",
        "Waltzes, op. 54: I. Moderato",
        parents=[link("c", "Waltzes", 1, ["part of collection"])],
    )
    assert title_tags(resolve(performed(waltz), {}).segments, None) is None
