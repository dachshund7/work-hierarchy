"""The plugin running inside Picard's real album and track loading code."""

import json

from conftest import load_fixture, start_album_load

BWV_831 = "13c7fae5-83ee-4a34-be2f-efbf7a0e6639"
ZAUBERFLOETE = "e208c5f5-5d37-3dfc-ac0b-999f207c9e46"
AKT_I = "5adc213f-700a-4435-9e95-831ed720f348"
AKT_II = "a2e62eb9-6b1d-4a98-aa60-7e09dcb2ae48"


def track(album, disc, number):
    return next(
        t
        for t in album.tracks
        if t.metadata["discnumber"] == str(disc) and t.metadata["tracknumber"] == str(number)
    )


def tags(t, *names):
    return {name: t.metadata.getall(name) for name in names}


def test_album_waits_for_work_lookups(picard_env):
    tagger, _ = picard_env
    album = start_album_load(load_fixture("bach_keyboard_suites"))

    assert not album.loaded
    assert album._requests > 0

    tagger.mb_api.serve()

    assert album.loaded
    assert album._requests == 0
    assert album.errors == []


def test_one_lookup_per_parent_work(picard_env):
    tagger, _ = picard_env
    start_album_load(load_fixture("bach_keyboard_suites"))
    tagger.mb_api.serve()

    # Twenty tracks, three suites; their collections are not looked up.
    assert sorted(tagger.mb_api.requested) == sorted([
        BWV_831,
        "ddd67114-e7d5-4b79-bffc-38d6e9ea4ca9",
        "eb3a7cf5-a796-4d88-acd3-beac6f43b657",
    ])
    assert all(call["inc"] == ["work-rels"] for call in tagger.mb_api.calls)


def test_track_with_two_movements(picard_env):
    tagger, _ = picard_env
    album = start_album_load(load_fixture("bach_keyboard_suites"))
    tagger.mb_api.serve()

    t = track(album, 1, 4)
    assert tags(t, "work", "movement", "movementnumber", "movementtotal", "showmovement") == {
        "work": ["Ouvertüre nach französischer Art h-Moll, BWV 831"],
        "movement": ["Passepied I / Passepied II"],
        "movementnumber": ["5"],
        "movementtotal": ["11"],
        "showmovement": ["1"],
    }
    document = json.loads(t.metadata["work_hierarchy"])
    assert [segment["path"][-1]["name"] for segment in document["segments"]] == [
        "Passepied I",
        "Passepied II",
    ]
    # Picard's own work tags are still available to scripts.
    assert t.metadata.getall("~recording_work") == [
        "Ouvertüre nach französischer Art h-Moll, BWV 831: V. Passepied I",
        "Ouvertüre nach französischer Art h-Moll, BWV 831: VI. Passepied II",
    ]
    assert len(t.metadata.getall("musicbrainz_workid")) == 2


def test_original_metadata_has_the_final_tags(picard_env):
    tagger, _ = picard_env
    album = start_album_load(load_fixture("bach_keyboard_suites"))
    tagger.mb_api.serve()

    t = track(album, 1, 4)
    assert t.orig_metadata["movement"] == "Passepied I / Passepied II"
    assert t.orig_metadata["work_hierarchy"] == t.metadata["work_hierarchy"]


def test_opera_three_levels(picard_env):
    tagger, _ = picard_env
    album = start_album_load(load_fixture("zauberfloete"))
    tagger.mb_api.serve()

    assert album.loaded
    assert sorted(tagger.mb_api.requested) == sorted([ZAUBERFLOETE, AKT_I, AKT_II])

    t = track(album, 1, 2)
    assert t.metadata["work"] == "Die Zauberflöte, K. 620"
    assert t.metadata["movement"] == (
        "Akt I: No. 1 Introduktion „Zu Hilfe! Zu Hilfe!” (Tamino, Damen)"
    )
    path = json.loads(t.metadata["work_hierarchy"])["segments"][0]["path"]
    assert [node["name"] for node in path] == [
        "Die Zauberflöte, K. 620",
        "Akt I",
        "No. 1 Introduktion „Zu Hilfe! Zu Hilfe!” (Tamino, Damen)",
    ]
    assert path[0]["type"] == "Opera"
    assert path[1]["part_attributes"] == ["act"]

    t = track(album, 2, 20)
    assert t.metadata["movement"] == (
        "Akt II: „Die Strahlen der Sonne vertreiben die Nacht” (Sarastro)"
        " / „Heil sei euch Geweihten” (Chor)"
    )


def test_partial_performance_is_recorded(picard_env):
    tagger, _ = picard_env
    album = start_album_load(load_fixture("zauberfloete"))
    tagger.mb_api.serve()

    segments = json.loads(track(album, 1, 11).metadata["work_hierarchy"])["segments"]
    assert [segment["attributes"] for segment in segments] == [["partial"]]


def test_parent_work_level_setting(picard_env):
    tagger, settings = picard_env
    settings["work_hierarchy_work_level"] = "parent"
    album = start_album_load(load_fixture("zauberfloete"))
    tagger.mb_api.serve()

    t = track(album, 1, 2)
    assert t.metadata["work"] == "Die Zauberflöte, K. 620: Akt I"
    assert t.metadata["movement"] == "No. 1 Introduktion „Zu Hilfe! Zu Hilfe!” (Tamino, Damen)"
    # The JSON is the same whichever level the work tag uses.
    assert len(json.loads(t.metadata["work_hierarchy"])["segments"][0]["path"]) == 3


def test_tagger_scripts_see_the_new_tags(picard_env):
    tagger, settings = picard_env
    settings["enable_tagger_scripts"] = True
    settings["list_of_scripts"] = [(0, "test", True, "$set(comment,%work% | %movement%)")]
    album = start_album_load(load_fixture("bach_keyboard_suites"))
    tagger.mb_api.serve()

    assert track(album, 1, 4).metadata["comment"] == (
        "Ouvertüre nach französischer Art h-Moll, BWV 831 | Passepied I / Passepied II"
    )


def test_failed_lookup_still_finishes_the_album(picard_env):
    tagger, _ = picard_env
    album = start_album_load(load_fixture("zauberfloete"))
    tagger.mb_api.serve(fail=frozenset([AKT_I]))

    assert album.loaded
    assert album._requests == 0
    # Without Act I's own data its numbers stop at the act, which is known
    # from the numbers' relationships.
    t = track(album, 1, 2)
    assert t.metadata["work"] == "Die Zauberflöte, K. 620: Akt I"
    assert t.metadata["movementnumber"] == "1"
    assert t.metadata["movementtotal"] == ""
    # Act II is unaffected.
    assert track(album, 2, 1).metadata["work"] == "Die Zauberflöte, K. 620"


def test_cached_works_are_not_looked_up_again(picard_env):
    tagger, _ = picard_env
    start_album_load(load_fixture("bach_keyboard_suites"))
    tagger.mb_api.serve()
    tagger.mb_api.requested.clear()

    album = start_album_load(load_fixture("bach_keyboard_suites"))

    assert tagger.mb_api.requested == []
    assert album.loaded
    assert track(album, 1, 4).metadata["movement"] == "Passepied I / Passepied II"


def test_without_track_relationships_tags_are_left_alone(picard_env):
    tagger, settings = picard_env
    settings["track_ars"] = False
    release = load_fixture("bach_keyboard_suites")
    for medium in release["media"]:
        for t in medium["tracks"]:
            del t["recording"]["relations"]
    album = start_album_load(release)

    assert album.loaded
    assert tagger.mb_api.requested == []
    t = track(album, 1, 4)
    assert "work_hierarchy" not in t.metadata
    assert "movement" not in t.metadata.deleted_tags


def test_recording_without_works_clears_stale_tags(picard_env):
    tagger, _ = picard_env
    release = load_fixture("bach_keyboard_suites")
    release["media"][0]["tracks"][3]["recording"]["relations"] = []
    album = start_album_load(release)
    tagger.mb_api.serve()

    t = track(album, 1, 4)
    assert "work_hierarchy" in t.metadata.deleted_tags
    assert "movement" in t.metadata.deleted_tags


def test_standalone_recording(picard_env):
    from picard.album import NatAlbum
    from picard.track import NonAlbumTrack

    tagger, _ = picard_env
    tagger.nats = NatAlbum()
    recording = load_fixture("zauberfloete")["media"][0]["tracks"][1]["recording"]
    t = NonAlbumTrack(recording["id"])
    tagger.nats.tracks.append(t)

    t._parse_recording(recording)
    # Picard considers the track loaded straight away, with the act known
    # from the recording's own data.
    assert t.loaded
    assert t.metadata["work"] == "Die Zauberflöte, K. 620: Akt I"

    tagger.mb_api.serve()

    assert t.metadata["work"] == "Die Zauberflöte, K. 620"
    assert t.metadata["movement"].startswith("Akt I: No. 1 Introduktion")
    assert t.orig_metadata["movement"] == t.metadata["movement"]


def test_error_while_tagging_still_finishes_the_album(picard_env, monkeypatch):
    import work_hierarchy

    tagger, _ = picard_env
    album = start_album_load(load_fixture("zauberfloete"))

    def broken_apply_tags(*args, **kwargs):
        raise RuntimeError("broken")

    monkeypatch.setattr(work_hierarchy, "apply_tags", broken_apply_tags)
    tagger.mb_api.serve()

    assert album.loaded
    assert album._requests == 0
