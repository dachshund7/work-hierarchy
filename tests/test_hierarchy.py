"""Work hierarchy logic, without Picard."""

import dataclasses
import json

import pytest
from support import WORKS, load_fixture, load_work

from hierarchy import (
    JSON_TAG,
    MAX_DEPTH,
    RECORDING_DATE_TAG,
    Artist,
    Performance,
    StandardTags,
    WorkInfo,
    WorkLink,
    _catalogue_numbers,
    _numeral_value,
    apply_composer_tags,
    apply_recording_date,
    apply_tags,
    display_name,
    inherited_composers,
    is_catch_all,
    performances,
    recording_date,
    resolve,
    standard_tags,
    title_tags,
    to_json,
)

ALL_WORKS = {path.stem: WorkInfo.from_json(load_work(path.stem)) for path in WORKS.glob("*.json")}


def recording(release: str, disc: int, number: int) -> dict:
    medium = load_fixture(release)["media"][disc - 1]
    return medium["tracks"][number - 1]["recording"]


def work(work_id, title, parents=(), parts=(), type=None, composers=()):
    return WorkInfo(work_id, title, type, tuple(parents), tuple(parts), tuple(composers))


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

    def getall(self, name):
        return list(self.get(name, []))

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
        (
            "Symphony no. 9 in D minor, op. 125 “Choral”: "
            "I. Allegro ma non troppo, un poco maestoso",
            "Symphony no. 9 in D minor, op. 125 “Choral”",
            1,
            ("Allegro ma non troppo, un poco maestoso", 1),
        ),
        (
            "Choral Fantasy in C minor, op. 80 “Choral Fantasy”: I. Adagio",
            "Choral Fantasy in C minor, op. 80 “Choral Fantasy”",
            1,
            ("Adagio", 1),
        ),
        (
            "Symphony no. 3 (Eroica): II. Marcia funebre",
            "Symphony no. 3 (Eroica)",
            2,
            ("Marcia funebre", 2),
        ),
        (
            'Kantate, BWV 214 "Tönet, ihr Pauken! Erschallet, Trompeten!": I. Coro '
            '"Tönet, ihr Pauken! Erschallet, Trompeten!"',
            'Kantate, BWV 214 "Tönet, ihr Pauken! Erschallet, Trompeten!"',
            1,
            ('Coro "Tönet, ihr Pauken! Erschallet, Trompeten!"', 1),
        ),
        (
            'Kantate, BWV 89 "Was soll ich aus dir machen, Ephraim?": II. Recitativo (Alt)',
            'Kantate, BWV 89 "Was soll ich aus dir machen, Ephraim?"',
            2,
            ("Recitativo (Alt)", 2),
        ),
        (
            "Kantate, BWV 4 „Christ lag in Todesbanden“: I. Sinfonia",
            "Kantate, BWV 4 „Christ lag in Todesbanden“",
            1,
            ("Sinfonia", 1),
        ),
        (
            "Symphonie Nr. 9 »Aus der Neuen Welt«: I. Adagio",
            "Symphonie Nr. 9 »Aus der Neuen Welt«",
            1,
            ("Adagio", 1),
        ),
        ("Concerto 「Spring」: I. Allegro", "Concerto 「Spring」", 1, ("Allegro", 1)),
        # An opening bracket after the parent's title isn't the end of it.
        ("Adelaide (Excerpts)", "Adelaide", None, ("Adelaide (Excerpts)", None)),
        # A quote that isn't closing the parent's title is kept.
        ("Tosca: “Vissi d'arte”", "Tosca", None, ("“Vissi d'arte”", None)),
        ("Mass: II. Gloria", "Mass", None, ("Gloria", 2)),
        # No prefix in common.
        ("Gute Nacht", "Winterreise, D. 911", 1, ("Gute Nacht", None)),
        # A word in common but no separator after it.
        ("Gloria in excelsis Deo", "Gloria, RV 589", 1, ("Gloria in excelsis Deo", None)),
        # Nothing would be left.
        ("Akt I", "Akt I", 1, ("Akt I", None)),
        # Not a Roman numeral.
        ("Op. 10: IIII. Etude", "Op. 10", 4, ("IIII. Etude", None)),
        # A letter, or a catalogue such as Schubert's, rather than 100 or 500.
        ("Mass in B minor: C. Credo", "Mass in B minor", None, ("C. Credo", None)),
        ("Impromptus: D. 899", "Impromptus", None, ("D. 899", None)),
        # A period or comma that is the parent's own, followed by its next
        # word, isn't the end of the prefix, even when the title goes on
        # with words the parent doesn't have.
        (
            "Piano Sonata no. 14 “Quasi una fantasia”: I. Adagio sostenuto",
            "Piano Sonata no. 14 in C-sharp minor, op. 27 no. 2 “Moonlight”",
            1,
            ("Piano Sonata no. 14 “Quasi una fantasia”: I. Adagio sostenuto", None),
        ),
        (
            "Symphony no. 3 (Eroica): II. Marcia funebre",
            "Symphony no. 3",
            2,
            ("Symphony no. 3 (Eroica): II. Marcia funebre", None),
        ),
        (
            "Concerto in D, RV 93 (arr. guitar): I. Allegro",
            "Concerto in D, RV 93",
            1,
            ("Concerto in D, RV 93 (arr. guitar): I. Allegro", None),
        ),
        ("Op. 10 No. 3: Etude", "Op. 10", 3, ("Op. 10 No. 3: Etude", None)),
        # A period after the parent's last word still is.
        ("Die Zauberflöte. Ouvertüre", "Die Zauberflöte, K. 620", 1, ("Ouvertüre", None)),
    ],
)
def test_display_name(title, parent, position, expected):
    assert display_name(title, parent, position)[:2] == expected


def test_display_name_of_top_level_work_is_its_title():
    assert display_name("Die Zauberflöte, K. 620", None, None) == (
        "Die Zauberflöte, K. 620",
        None,
        None,
    )


def test_display_name_gives_the_numeral_removed():
    title = "Requiem, op. 89: I. oddíl: III. Sequentia"
    assert display_name(title, "Requiem, op. 89: I. oddíl", 3) == ("Sequentia", 3, "III")
    assert display_name("Mass: 2. Gloria", "Mass", 2) == ("Gloria", 2, "2")
    # Kept in the name, so not removed.
    assert display_name("Suite: IV. Passepied", "Suite", 5) == ("IV. Passepied", None, None)


@pytest.mark.parametrize(
    ("numeral", "value"),
    [("I", 1), ("IV", 4), ("IX", 9), ("XIV", 14), ("XL", 40), ("MCMXC", 1990), ("12", 12)],
)
def test_numeral_value(numeral, value):
    assert _numeral_value(numeral) == value


@pytest.mark.parametrize("numeral", ["IIII", "IC", "VX", "MMMM", "C", "D", "L", "M"])
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


def test_works_performed_whole_are_looked_up_for_their_aliases():
    schubert = Artist("schubert", "Franz Schubert")
    song = work("s", "Erlkönig, D. 328", composers=[schubert])
    resolution = resolve(performed(song), {})
    assert (resolution.unknown, resolution.whole) == (frozenset(), {"s"})
    assert resolve(performed(song), {}, failed=["s"]).whole == frozenset()

    # Once looked up, it has its aliases, and keeps its composers as named
    looked_up = WorkInfo.from_json({
        "id": "s",
        "title": "Erlkönig, D. 328",
        "aliases": [
            {"name": "The Erl-King"},
            {"name": "Erlkönig, D. 328"},
            {"name": "The Erl-King", "locale": "en"},
            {"name": "Le Roi des aulnes"},
        ],
        "relations": [
            {
                "type": "composer",
                "target-type": "artist",
                "artist": {"id": "schubert", "name": "Schubert, Franz"},
            }
        ],
    })
    resolution = resolve(performed(song), {"s": looked_up})
    assert resolution.whole == frozenset()
    [node] = json.loads(to_json(resolution.segments))["segments"][0]["path"]
    assert node["aliases"] == ["The Erl-King", "Le Roi des aulnes"]
    assert node["composers"] == [{"id": "schubert", "name": "Franz Schubert"}]


def test_movements_are_not_looked_up_for_aliases():
    movement = work("m1", "Sonata: I. Allegro", parents=[link("sonata", "Sonata", 1)])
    sonata = work("sonata", "Sonata", parts=[link("m1", "Sonata: I. Allegro", 1)])
    # Not while the paths aren't complete, as the work isn't known yet
    resolution = resolve(performed(movement), {})
    assert (resolution.unknown, resolution.whole) == ({"sonata"}, frozenset())
    # The work is the sonata, an ancestor, looked up already
    assert resolve(performed(movement), {"sonata": sonata}).whole == frozenset()


def test_works_have_no_aliases_unless_asked_for():
    assert WorkInfo.from_json({"id": "x", "title": "X"}).aliases is None
    assert WorkInfo.from_json({"id": "x", "title": "X", "aliases": []}).aliases == ()


def test_work_and_its_part_both_linked():
    whole = work("w", "Suite", parts=[link("p", "Suite: I. Prelude", 1)])
    part = work("p", "Suite: I. Prelude", parents=[link("w", "Suite", 1)])

    resolution = resolve(performed(whole, part), {"w": whole})

    assert [s.performed.id for s in resolution.segments] == ["p"]


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


def test_standard_tags_number_the_levels_above_the_movement():
    requiem = work("r", "Requiem, op. 89")
    part = work("1", "Requiem, op. 89: I. oddíl", parents=[link("r", requiem.title, 1)])
    sequence = work("3", f"{part.title}: III. Sequentia", parents=[link("1", part.title, 3)])
    quid = work("q", f"{sequence.title}: Quid sum miser", parents=[link("3", sequence.title, 3)])
    works = {w.id: w for w in (requiem, part, sequence)}

    tags = standard_tags(resolve(performed(quid), works).segments)

    assert tags.work == ("Requiem, op. 89",)
    # The movement's own number is the movement number.
    assert (tags.movement, tags.movementnumber) == ("I. oddíl: III. Sequentia: Quid sum miser", 3)


# A part with a catalogue number of its own is the work

WTC = work("wtc", "The Well-Tempered Clavier")
BOOK_I = work("book1", "The Well-Tempered Clavier, Book I", parents=[link("wtc", WTC.title, 1)])
BWV_846 = work(
    "bwv846",
    f"{BOOK_I.title}: Prelude and Fugue no. 1 in C major, BWV 846",
    parents=[link("book1", BOOK_I.title, 1)],
    parts=[link("p846", "…: Prelude", 1), link("f846", "…: Fugue", 2)],
)
PRELUDE_846 = work("p846", f"{BWV_846.title}: Prelude", parents=[link("bwv846", BWV_846.title, 1)])
FUGUE_846 = work("f846", f"{BWV_846.title}: Fugue", parents=[link("bwv846", BWV_846.title, 2)])
BWV_847 = work(
    "bwv847",
    f"{BOOK_I.title}: Prelude and Fugue no. 2 in C minor, BWV 847",
    parents=[link("book1", BOOK_I.title, 2)],
)
PRELUDE_847 = work("p847", f"{BWV_847.title}: Prelude", parents=[link("bwv847", BWV_847.title, 1)])
WTC_WORKS = {w.id: w for w in (WTC, BOOK_I, BWV_846, BWV_847)}


def wtc_tags(*works):
    return standard_tags(resolve(performed(*works), WTC_WORKS).segments)


def test_a_part_with_its_own_catalogue_number_is_the_work():
    resolution = resolve(performed(PRELUDE_846), WTC_WORKS)
    assert [s.work_index for s in resolution.segments] == [2]
    tags = standard_tags(resolution.segments)
    assert tags.work == (BWV_846.title,)
    assert (tags.movement, tags.movementnumber, tags.movementtotal) == ("Prelude", 1, 2)
    # Its movements together
    assert wtc_tags(FUGUE_846, PRELUDE_846).movement == "Prelude / Fugue"


def test_a_whole_part_with_its_own_catalogue_number_is_not_a_movement():
    assert wtc_tags(BWV_846) == StandardTags((BWV_846.title,), None, None, None)
    # Unless it has no parts, like a movement with a number of its own
    without_parts = dataclasses.replace(BWV_846, parts=())
    tags = wtc_tags(without_parts)
    assert tags.work == (WTC.title,)
    assert tags.movement == "Book I: Prelude and Fugue no. 1 in C major, BWV 846"


def test_movements_of_two_parts_with_their_own_numbers_are_in_the_work_above():
    tags = wtc_tags(FUGUE_846, PRELUDE_847)
    assert tags.work == (WTC.title,)
    assert tags.movement == (
        "Book I: Prelude and Fugue no. 1 in C major, BWV 846: Fugue"
        " / Prelude and Fugue no. 2 in C minor, BWV 847: Prelude"
    )


def chain(*levels):
    """The performed work, last of the levels, and the works above it.

    Each level is a work's ID, title and position in the one before.
    """
    works = []
    for work_id, title, position in levels:
        parents = [link(works[-1].id, works[-1].title, position)] if works else []
        works.append(work(work_id, title, parents=parents))
    return works[-1], {w.id: w for w in works[:-1]}


def test_parts_of_sets_with_their_own_numbers_are_works():
    giga, works = chain(
        ("op2", "12 sonate da camera, op. 2", None),
        ("op2no4", "Sonata da camera a trè in E minor, op. 2 no. 4", 4),
        ("g", "Sonata da camera a trè in E minor, op. 2 no. 4: Giga", 3),
    )
    tags = standard_tags(resolve(performed(giga), works).segments)
    assert (tags.work, tags.movement) == (
        ("Sonata da camera a trè in E minor, op. 2 no. 4",),
        "Giga",
    )

    suite = "Water Music Suite no. 1 in F major, HWV 348"
    andante, works = chain(
        ("hwv348-350", "Water Music, HWV 348–350", None),
        ("hwv348", suite, 1),
        ("aaa", f"{suite}: III. Allegro - Andante - Allegro", 3),
        ("an", f"{suite}: III. Allegro - Andante - Allegro: Andante", 2),
    )
    tags = standard_tags(resolve(performed(andante), works).segments)
    assert tags.work == (suite,)
    assert tags.movement == "III. Allegro - Andante - Allegro: Andante"


def test_numbers_that_movements_repeat_do_not_make_them_works():
    variation, works = chain(
        ("k359", "12 Variations, K. 374a/359", None),
        ("v1", "Variations in G major, K359: Variation I", 2),
    )
    assert standard_tags(resolve(performed(variation), works).segments).work == (
        "12 Variations, K. 374a/359",
    )
    allemande, works = chain(
        ("bwv813", "Französische Suite Nr. 2 c-Moll, BWV 813", None),
        ("a", "Französische Suite Nr. 2 c-Moll, BWV 813: I. Allemande", 1),
    )
    assert standard_tags(resolve(performed(allemande), works).segments).work == (
        "Französische Suite Nr. 2 c-Moll, BWV 813",
    )
    # Nor do acts, which have no catalogue numbers
    tags = standard_tags(zauberfloete_segments(1, 4))
    assert tags.work == ("Die Zauberflöte, K. 620",)


@pytest.mark.parametrize(
    ("name", "numbers"),
    [
        ("Prelude and Fugue no. 1 in C major, BWV 846", ["bwv 846"]),
        ("Sonata da camera a trè in E minor, op. 2 no. 4", ["op 2 no 4"]),
        ("12 sonate da camera, op. 2", ["op 2"]),
        ("Fantasia in A major, TWV 40:18", ["twv 40:18"]),
        ("Sonata in E-flat major, Hob. XVI:52", ["hob xvi:52"]),
        ("Piano Sonata no. 11 in A major, K. 331", ["k 331"]),
        ("4 Impromptus, op. 90, D. 899", ["op 90", "d 899"]),
        # The name of the movement after the colon isn't a number in the set
        ("Impromptu in C minor, op. 90, D. 899: No. 1. Allegro", ["op 90", "d 899"]),
        ('Cantata, BuxWV 75a "Membra Jesu Nostri: Ad Pedes"', ["buxwv 75a"]),
        ("Sonata, op.2", ["op 2"]),
        ("Bachianas Brasileiras nº 5, W389", ["w 389"]),
        ("Suite (BWV 1007)", ["bwv 1007"]),
        ("Book I", []),
        ("Act II", []),
        ("Atto IV", []),
        ("No. 4 in E minor", []),
        ("Allegro - Andante - Allegro da capo", []),
        ("Suite in D major", []),
        ("Opus Dei", []),
        ("Sonata, op. posth.", []),
    ],
)
def test_catalogue_numbers(name, numbers):
    assert _catalogue_numbers(name) == numbers


# Parts of works named in recordings' titles

FIREWORKS = work("hwv351", "Music for the Royal Fireworks, HWV 351")
OUVERTURE = work(
    "i", f"{FIREWORKS.title}: I. Ouverture", parents=[link("hwv351", FIREWORKS.title, 1)]
)


def fireworks_movement(title, attributes=("partial",)):
    metadata = FakeMetadata()
    segments = resolve(performed(OUVERTURE, attributes=attributes), {"hwv351": FIREWORKS}).segments
    apply_tags(metadata, segments, title)
    return metadata["movement"], json.loads(metadata[JSON_TAG])["segments"][0]["excerpt"]


@pytest.mark.parametrize(
    ("title", "excerpt"),
    [
        ("Music for the Royal Fireworks, HWV 351: Ia. Overture – Adagio", "Ia. Overture – Adagio"),
        ("Music for the Royal Fireworks: Adagio", "Adagio"),
        # A number like the movement's is left out, as from its name.
        (
            "Music for the Royal Fireworks, HWV 351: I. Ouverture (Beginning)",
            "Ouverture (Beginning)",
        ),
        # Titled in another language
        ("Feuerwerksmusik: Ouvertüre (Teil 1)", "Ouvertüre (Teil 1)"),
        ("Feuerwerksmusik - Ouvertüre (Teil 1)", "Ouvertüre (Teil 1)"),
    ],
)
def test_part_of_a_movement_is_named_after_the_recording(title, excerpt):
    assert fireworks_movement(title) == (excerpt, excerpt)


@pytest.mark.parametrize(
    "title",
    [
        "Music for the Royal Fireworks, HWV 351: I. Ouverture",
        "Ouverture",
        "Music for the Royal Fireworks, HWV 351",
        "Music for the Royal Fireworks: ",
    ],
)
def test_titles_naming_no_part_of_the_movement(title):
    assert fireworks_movement(title) == ("Ouverture", None)


def test_whole_movements_are_named_after_the_work():
    title = "Music for the Royal Fireworks, HWV 351: Ia. Overture – Adagio"
    assert fireworks_movement(title, attributes=("live",)) == ("Ouverture", None)


def test_part_of_a_number_keeps_its_act():
    lakme = work("lakme", "Lakmé")
    act = work("act1", "Lakmé: Acte I", parents=[link("lakme", "Lakmé", 1)])
    air = work(
        "air",
        "Lakmé: Acte I. Air « Prendre le dessin d’un bijou »",
        parents=[link("act1", act.title, 8)],
    )
    segments = resolve(performed(air, attributes=["partial"]), {"lakme": lakme, "act1": act})
    metadata = FakeMetadata()

    apply_tags(
        metadata, segments.segments, "Lakmé : Acte I. Air « Prendre le dessin d’un bijou » (Gerald)"
    )

    assert metadata["movement"] == "Acte I: Air « Prendre le dessin d’un bijou » (Gerald)"
    assert metadata["movementnumber"] == "8"


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
    assert (segment["work_index"], segment["attributes"], segment["excerpt"]) == (0, [], None)
    opera, act, number = segment["path"]
    assert opera == {
        "id": "e208c5f5-5d37-3dfc-ac0b-999f207c9e46",
        "title": "Die Zauberflöte, K. 620",
        "name": "Die Zauberflöte, K. 620",
        "type": "Opera",
        "position": None,
        "number": None,
        "numeral": None,
        "part_total": 3,
        "part_attributes": [],
        "collections": [],
        "composers": [
            {"id": "b972f589-fb0e-474e-b64a-803b0364fa75", "name": "Wolfgang Amadeus Mozart"}
        ],
        "disambiguation": "",
        "arrangement_of": None,
        # The fixtures are saved without aliases
        "aliases": None,
    }
    assert (act["name"], act["position"], act["part_total"]) == ("Akt I", 2, 31)
    assert act["part_attributes"] == ["act"]
    assert (number["position"], number["part_total"]) == (1, 0)
    # "No. 1" is part of the name, not a numeral removed from it.
    assert (number["number"], number["numeral"]) == (1, None)


def test_json_gives_the_numerals_removed():
    segments = resolve(performances(recording("bach_keyboard_suites", 1, 4)), ALL_WORKS).segments
    movements = [json.loads(to_json(segments))["segments"][i]["path"][-1] for i in (0, 1)]
    assert [(m["name"], m["number"], m["numeral"]) for m in movements] == [
        ("Passepied I", 5, "V"),
        ("Passepied II", 6, "VI"),
    ]


def test_json_number_without_position_is_read_from_the_title():
    mass = work("m", "Mass", parts=[link("g", "Mass: II. Gloria")])
    gloria = work("g", "Mass: II. Gloria", parents=[link("m", "Mass")])
    [segment] = resolve(performed(gloria), {"m": mass}).segments
    node = segment.to_json()["path"][-1]
    assert (node["position"], node["number"], node["numeral"]) == (None, 2, "II")


PARTITA = "20a652f5-4b08-42fe-a036-dcd933885460"
PARTITA_TITLE = "Violin Partita no. 2 in D minor, BWV 1004"


def arrangement_rel(work_id, title, direction, disambiguation=""):
    return {
        "type": "arrangement",
        "direction": direction,
        "target-type": "work",
        "attributes": [],
        "work": {"id": work_id, "title": title, "disambiguation": disambiguation},
    }


def test_catch_alls_for_arrangements_are_spelled_many_ways():
    for text in (
        "catch-all for arrangements",
        "catch all for arrangements",
        "catch\u2010all for arrangements",
        "Catch-All for Arrangements",
    ):
        assert is_catch_all(text), text
    for text in ("", "for two pianos, Busoni", "catch-all for recordings"):
        assert not is_catch_all(text), text


def test_an_arrangement_names_the_work_it_arranges():
    # MusicBrainz's catch-all for the arrangements of a partita, which a
    # recording of its Chaconne on the lute is linked to
    catch_all = WorkInfo.from_json({
        "id": "42419947-4c5a-4623-a4fd-a9cd61611884",
        "title": PARTITA_TITLE,
        "disambiguation": "catch-all for arrangements",
        "relations": [
            arrangement_rel(PARTITA, PARTITA_TITLE, "backward"),
            {
                "type": "parts",
                "direction": "forward",
                "target-type": "work",
                "ordering-key": 5,
                "attributes": [],
                "work": {
                    "id": "c9f2776f-69e4-4f55-9a19-223a0c05ba2a",
                    "title": f"{PARTITA_TITLE}: V. Chaconne",
                },
            },
        ],
    })
    arranged = catch_all.arrangement_of
    assert arranged is not None
    assert arranged.id == PARTITA
    assert catch_all.disambiguation == "catch-all for arrangements"

    chaconne = work(
        "c9f2776f-69e4-4f55-9a19-223a0c05ba2a",
        f"{PARTITA_TITLE}: V. Chaconne",
        parents=[link(catch_all.id, PARTITA_TITLE, 5)],
    )
    segments = resolve(performed(chaconne), {catch_all.id: catch_all}).segments
    partita, part = json.loads(to_json(segments))["segments"][0]["path"]
    assert partita["arrangement_of"] == {"id": PARTITA, "title": PARTITA_TITLE}
    assert partita["disambiguation"] == "catch-all for arrangements"
    assert (part["arrangement_of"], part["disambiguation"]) == (None, "")

    # An arrangement of its own, with what it is in its disambiguation
    busoni = WorkInfo.from_json({
        "id": "b",
        "title": "Chaconne",
        "disambiguation": "for piano, Busoni",
        "relations": [arrangement_rel(PARTITA, PARTITA_TITLE, "backward")],
    })
    arranged = busoni.arrangement_of
    assert arranged is not None
    assert arranged.title == PARTITA_TITLE

    # The original, which others arrange, isn't one
    original = WorkInfo.from_json({
        "id": PARTITA,
        "title": PARTITA_TITLE,
        "relations": [arrangement_rel(catch_all.id, PARTITA_TITLE, "forward")],
    })
    assert original.arrangement_of is None


def test_an_original_linked_to_its_catch_all_is_not_an_arrangement():
    # The Marsch der Priester is linked as an arrangement of its own
    # catch-all for arrangements, the wrong way round
    [performance] = performances(recording("zauberfloete", 2, 1))
    march = performance.work
    assert [is_catch_all(link.disambiguation) for link in march.arranges] == [True]
    assert march.arrangement_of is None

    segments = resolve([performance], ALL_WORKS).segments
    path = json.loads(to_json(segments))["segments"][0]["path"]
    assert path[-1]["arrangement_of"] is None


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


def test_composers_are_named_by_the_function_given():
    node = {
        "id": "w",
        "title": "Trio",
        "relations": [
            {
                "target-type": "artist",
                "type": "composer",
                "artist": {"id": "a", "name": "Пётр Ильич Чайковский", "sort-name": "Tchaikovsky"},
            },
        ],
    }
    assert WorkInfo.from_json(node).composers[0].name == "Пётр Ильич Чайковский"
    named = WorkInfo.from_json(node, lambda artist: ("P. I. Tchaikovsky", "Tchaikovsky, P. I."))
    assert (named.composers[0].name, named.composers[0].sort_name) == (
        "P. I. Tchaikovsky",
        "Tchaikovsky, P. I.",
    )
    recording = {
        "relations": [{"target-type": "work", "type": "performance", "work": node}],
    }
    [performance] = performances(recording, lambda artist: ("Named", "Named"))
    assert performance.work.composers[0].name == "Named"


# Composers inherited from parent works

MOZART = Artist("b972f589-fb0e-474e-b64a-803b0364fa75", "Wolfgang Amadeus Mozart")
SCHIKANEDER = Artist("s", "Emanuel Schikaneder", "Schikaneder, Emanuel")


def without_composers(node: dict) -> dict:
    for rel in node["relations"]:
        if rel.get("type") == "performance":
            work = rel["work"]
            work["relations"] = [r for r in work["relations"] if r.get("type") != "composer"]
    return node


def test_work_without_composers_inherits_its_parents():
    number = recording("zauberfloete", 1, 2)
    segments = resolve(performances(without_composers(number)), ALL_WORKS).segments

    [mozart] = inherited_composers(segments)
    assert mozart == MOZART
    # The sort name comes from the act's lookup.
    assert mozart.sort_name == "Mozart, Wolfgang Amadeus"


def test_works_with_composers_inherit_nothing():
    segments = resolve(performances(recording("zauberfloete", 1, 2)), ALL_WORKS).segments
    assert inherited_composers(segments) == ()


def test_nearest_ancestor_with_composers_wins():
    top = work("t", "Opera", composers=[MOZART])
    act = work("a", "Opera: Act I", parents=[link("t", "Opera")], composers=[SCHIKANEDER])
    scene = work("s1", "Opera: Act I: Scene 1", parents=[link("a", "Opera: Act I")])
    number = work("n", "Opera: Act I: Scene 1: No. 1", parents=[link("s1", "Scene 1", 1)])
    works = {w.id: w for w in (top, act, scene)}

    segments = resolve(performed(number), works).segments

    assert inherited_composers(segments) == (SCHIKANEDER,)


def test_no_composers_are_inherited_from_works_not_looked_up():
    number = without_composers(recording("zauberfloete", 1, 2))
    segments = resolve(performances(number), {}).segments
    assert inherited_composers(segments) == ()


def test_apply_composer_tags_adds_to_picards():
    linked = work("l", "Song", composers=[SCHIKANEDER])
    unlinked = work("u", "Opera: No. 1", parents=[link("t", "Opera", 1)])
    works = {"t": work("t", "Opera", composers=[MOZART, SCHIKANEDER])}
    segments = resolve(performed(linked, unlinked), works).segments
    metadata = FakeMetadata()
    # As Picard tags it, from the linked work's composer.
    metadata["composer"] = ["Emanuel Schikaneder"]
    metadata["composersort"] = ["Schikaneder, Emanuel"]
    metadata["musicbrainz_composerid"] = ["s"]

    apply_composer_tags(metadata, segments)

    expected = {
        "composer": ["Emanuel Schikaneder", "Wolfgang Amadeus Mozart"],
        # Without a sort name, the name.
        "composersort": ["Schikaneder, Emanuel", "Wolfgang Amadeus Mozart"],
        "musicbrainz_composerid": ["s", MOZART.id],
    }
    assert metadata == expected
    # Applying again, as each level of ancestors arrives, adds nothing.
    apply_composer_tags(metadata, segments)
    assert metadata == expected


def test_apply_composer_tags_keeps_other_values():
    unlinked = work("u", "Opera: No. 1", parents=[link("t", "Opera", 1)])
    works = {"t": work("t", "Opera", composers=[MOZART])}
    segments = resolve(performed(unlinked), works).segments
    metadata = FakeMetadata()
    # Set by something else, without an ID.
    metadata["composer"] = ["W. A. Mozart"]

    apply_composer_tags(metadata, segments)

    assert metadata["composer"] == ["W. A. Mozart", "Wolfgang Amadeus Mozart"]
    assert metadata["musicbrainz_composerid"] == [MOZART.id]


def test_inherited_composers_are_unique_by_id():
    renamed = Artist(MOZART.id, "Mozart")
    first = work("a", "Opera A: No. 1", parents=[link("ta", "Opera A", 1)])
    second = work("b", "Opera B: No. 1", parents=[link("tb", "Opera B", 1)])
    works = {
        "ta": work("ta", "Opera A", composers=[MOZART]),
        "tb": work("tb", "Opera B", composers=[renamed]),
    }
    segments = resolve(performed(first, second), works).segments

    assert [artist.id for artist in inherited_composers(segments)] == [MOZART.id]


def test_apply_tags_adds_inherited_composers():
    number = without_composers(recording("zauberfloete", 1, 2))
    metadata = FakeMetadata()
    apply_tags(metadata, resolve(performances(number), ALL_WORKS).segments)
    assert metadata["composer"] == [MOZART.name]
    assert metadata["musicbrainz_composerid"] == [MOZART.id]


def test_apply_composer_tags_leaves_tags_alone_when_nothing_is_inherited():
    segments = resolve(performances(recording("zauberfloete", 1, 2)), ALL_WORKS).segments
    metadata = FakeMetadata()
    apply_composer_tags(metadata, segments)
    assert metadata == {}
    assert metadata.deleted == set()


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


def test_recording_date_from_the_performance_of_the_work():
    # Beecham's Zauberflöte, recorded from November 1937 to March 1938
    assert recording_date(recording("zauberfloete", 1, 4)) == "1937-11-08/1938-03-08"
    # MusicBrainz has no dates for these
    assert recording_date(recording("bach_keyboard_suites", 1, 4)) is None


def dated(target, kind, begin=None, end=None):
    return {"target-type": target, "type": kind, "begin": begin, "end": end}


def test_recording_date_from_the_sessions_of_the_artists():
    recording = {
        "relations": [
            dated("artist", "instrument", "2011-12-05", "2011-12-07"),
            dated("artist", "conductor", "2011-12"),
            # Not when the music was recorded
            dated("artist", "phonographic copyright", "2013"),
            dated("artist", "mix", "2012-03-01"),
            dated("place", "recorded at", "2010"),
        ]
    }
    # A partial date sorts before the days in it
    assert recording_date(recording) == "2011-12/2011-12-07"


def test_recording_date_of_a_single_day():
    assert recording_date({"relations": [dated("work", "performance", "1999-04-07")]}) == (
        "1999-04-07"
    )
    # A relationship with only an end ended then
    assert recording_date({"relations": [dated("artist", "vocal", end="1992")]}) == "1992"
    assert recording_date({"relations": [dated("work", "performance")]}) is None
    assert recording_date({}) is None


def test_recording_date_tag_is_written_or_deleted():
    metadata = FakeMetadata()
    apply_recording_date(metadata, {"relations": [dated("work", "performance", "1961")]})
    assert metadata[RECORDING_DATE_TAG] == "1961"
    apply_recording_date(metadata, {"relations": []})
    assert RECORDING_DATE_TAG in metadata.deleted
