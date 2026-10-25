"""The plugin running inside Picard's real album and track loading code."""

import gc
import json
import logging
import weakref
from unittest.mock import MagicMock

from picard.album import AlbumStatus, NatAlbum
from picard.metadata import Metadata, track_metadata_processors
from picard.track import NonAlbumTrack
from PyQt6 import QtNetwork
from support import load_fixture, load_work, start_album_load

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


def plugin_errors(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("main.plugin.work_hierarchy_") and record.levelno >= logging.ERROR
    ]


def enable_script(settings, script):
    settings["enable_tagger_scripts"] = True
    settings["list_of_scripts"] = [(0, "test", True, script)]


def load_standalone(picard_env, recording):
    """A standalone recording with one file, loaded the way Picard does."""
    picard_env.tagger.nats = NatAlbum()
    t = NonAlbumTrack(recording["id"])
    picard_env.tagger.nats.tracks.append(t)
    file = MagicMock()
    file.metadata = Metadata()
    t.files.append(file)
    t._parse_recording(recording)
    return t, file


def test_album_waits_for_work_lookups(picard_env):
    web = picard_env.web
    album = start_album_load(load_fixture("bach_keyboard_suites"))

    assert not album.loaded
    assert album.has_critical_tasks()

    web.serve()

    assert album.loaded
    assert not album.has_critical_tasks()
    assert album.errors == []


def test_one_lookup_per_parent_work(picard_env):
    web = picard_env.web
    start_album_load(load_fixture("bach_keyboard_suites"))
    web.serve()

    # Twenty tracks, three suites; their collections are not looked up.
    assert sorted(web.requested) == sorted([
        BWV_831,
        "ddd67114-e7d5-4b79-bffc-38d6e9ea4ca9",
        "eb3a7cf5-a796-4d88-acd3-beac6f43b657",
    ])
    assert all(
        call["unencoded_queryargs"] == {"inc": "artist-rels+work-rels"} for call in web.calls
    )


def test_track_with_two_movements(picard_env):
    web = picard_env.web
    album = start_album_load(load_fixture("bach_keyboard_suites"))
    web.serve()

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
    # Picard 3's musicbrainz_composerid holds the performed works' composers,
    # the same IDs as the JSON's.
    assert t.metadata.getall("musicbrainz_composerid") == [
        composer["id"] for composer in document["segments"][0]["path"][-1]["composers"]
    ]


def test_original_metadata_has_the_final_tags(picard_env):
    web = picard_env.web
    album = start_album_load(load_fixture("bach_keyboard_suites"))
    web.serve()

    t = track(album, 1, 4)
    assert t.orig_metadata["movement"] == "Passepied I / Passepied II"
    assert t.orig_metadata["work_hierarchy"] == t.metadata["work_hierarchy"]


def test_opera_three_levels(picard_env):
    web = picard_env.web
    album = start_album_load(load_fixture("zauberfloete"))
    web.serve()

    assert album.loaded
    assert sorted(web.requested) == sorted([ZAUBERFLOETE, AKT_I, AKT_II])

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


def test_pregap_and_data_tracks_are_looked_up(picard_env):
    release = load_fixture("zauberfloete")
    first, second = release["media"]
    # Only these two tracks are left, so only they need the acts and opera.
    first["pregap"] = first["tracks"][1]
    second["data-tracks"] = [second["tracks"][0]]
    for medium in release["media"]:
        medium["tracks"], medium["track-count"] = [], 0
    album = start_album_load(release)
    picard_env.web.serve()

    assert album.loaded
    assert [t.metadata["work"] for t in album.tracks] == ["Die Zauberflöte, K. 620"] * 2
    assert sorted(picard_env.web.requested) == sorted([ZAUBERFLOETE, AKT_I, AKT_II])


def test_part_of_a_number_is_named_after_its_recording(picard_env):
    web = picard_env.web
    album = start_album_load(load_fixture("zauberfloete"))
    web.serve()

    # The recording, "Die Zauberflöte: Akt I. „O ew’ge Nacht“ (Tamino,
    # Priester)", is of only part of a long number.
    t = track(album, 1, 11)
    [segment] = json.loads(t.metadata["work_hierarchy"])["segments"]
    assert segment["attributes"] == ["partial"]
    assert segment["excerpt"] == "„O ew’ge Nacht“ (Tamino, Priester)"
    assert t.metadata["movement"] == "Akt I: „O ew’ge Nacht“ (Tamino, Priester)"
    assert segment["path"][-1]["name"].startswith("„Zurück!” … „Zurück?”")


MOZART_ID = "b972f589-fb0e-474e-b64a-803b0364fa75"


def zauberfloete_with_unlinked_composer():
    """The release, with the composer of track 1/2's number removed."""
    release = load_fixture("zauberfloete")
    recording = release["media"][0]["tracks"][1]["recording"]
    for rel in recording["relations"]:
        if rel.get("type") == "performance":
            work = rel["work"]
            work["relations"] = [r for r in work["relations"] if r.get("type") != "composer"]
    return release


def test_composer_is_inherited_from_the_parent_work(picard_env):
    web = picard_env.web
    album = start_album_load(zauberfloete_with_unlinked_composer())
    web.serve()

    assert tags(track(album, 1, 2), "composer", "composersort", "musicbrainz_composerid") == {
        "composer": ["Wolfgang Amadeus Mozart"],
        "composersort": ["Mozart, Wolfgang Amadeus"],
        "musicbrainz_composerid": [MOZART_ID],
    }
    # Tracks with their own composer are as Picard tagged them.
    assert track(album, 1, 3).metadata.getall("musicbrainz_composerid") == [MOZART_ID]


def test_later_processors_see_the_complete_tags(picard_env):
    seen = {}

    def later_processor(track, metadata, track_node, release_node=None):
        if (metadata["discnumber"], metadata["tracknumber"]) == ("1", "2"):
            seen.update({name: metadata.getall(name) for name in ("work", "movement", "composer")})
        metadata.add("composer", "Arranger")
        metadata.add("musicbrainz_composerid", "arranger-id")

    # Lower priority: runs after the plugin's processor.
    track_metadata_processors.register(later_processor.__module__, later_processor, -10)
    try:
        album = start_album_load(zauberfloete_with_unlinked_composer())
        picard_env.web.serve()
    finally:
        track_metadata_processors.functions.unregister(
            later_processor.__module__, lambda function: function is later_processor
        )

    assert seen == {
        "work": ["Die Zauberflöte, K. 620"],
        "movement": ["Akt I: No. 1 Introduktion „Zu Hilfe! Zu Hilfe!” (Tamino, Damen)"],
        "composer": ["Wolfgang Amadeus Mozart"],
    }
    # What they change stays.
    assert tags(track(album, 1, 2), "composer", "musicbrainz_composerid") == {
        "composer": ["Wolfgang Amadeus Mozart", "Arranger"],
        "musicbrainz_composerid": [MOZART_ID, "arranger-id"],
    }


def test_tagger_scripts_see_the_new_tags(picard_env):
    web = picard_env.web
    enable_script(picard_env.settings, "$set(comment,%work% | %movement%)")
    album = start_album_load(load_fixture("bach_keyboard_suites"))
    web.serve()

    assert track(album, 1, 4).metadata["comment"] == (
        "Ouvertüre nach französischer Art h-Moll, BWV 831 | Passepied I / Passepied II"
    )


def test_stopped_lookups_do_not_block_later_albums(picard_env, caplog):
    web = picard_env.web
    first = start_album_load(load_fixture("zauberfloete"))
    # What Picard's Stop does: queued requests are dropped without calling
    # their handlers, loading albums are aborted, then the web service's
    # pending requests are reported changed.
    web.queue.clear()
    first.abort_loading("Stopped by user")
    web.pending_requests_changed.emit()
    web.requested.clear()

    second = start_album_load(load_fixture("zauberfloete"))
    web.serve()

    assert sorted(web.requested) == sorted([ZAUBERFLOETE, AKT_I, AKT_II])
    assert second.loaded
    assert track(second, 1, 2).metadata["work"] == "Die Zauberflöte, K. 620"
    assert plugin_errors(caplog) == []


def zauberfloete_by_acts():
    """The release without the numbers that are parts of the opera itself.

    The rest are parts of an act, so the opera is only looked up once an
    act has arrived.
    """
    release = load_fixture("zauberfloete")
    for medium in release["media"]:
        medium["tracks"] = [
            track
            for track in medium["tracks"]
            if not any(
                rel.get("type") == "parts"
                and rel.get("direction") == "backward"
                and rel["work"]["id"] == ZAUBERFLOETE
                for performance in track["recording"]["relations"]
                if performance.get("type") == "performance"
                for rel in performance["work"].get("relations", [])
            )
        ]
    return release


def test_removed_album_is_not_finished(picard_env, caplog):
    web = picard_env.web
    album = start_album_load(zauberfloete_by_acts())
    assert sorted(web.requested) == sorted([AKT_I, AKT_II])
    # What Picard does when an album is removed.
    album.cancel_tasks()
    web.serve()

    assert not album.loaded
    # The acts' lookups were already queued; the opera isn't looked up.
    assert sorted(web.requested) == sorted([AKT_I, AKT_II])
    assert plugin_errors(caplog) == []


def test_stop_aborts_albums_with_lookups_in_progress(picard_env, caplog):
    web = picard_env.web
    album = start_album_load(load_fixture("zauberfloete"))
    # What Picard's Stop does: the handlers of requests in progress are
    # called with a cancellation error, queued requests are dropped, albums
    # still loading are aborted, and pending requests are reported changed.
    in_progress, web.queue = web.queue, []
    for _, handler in in_progress:
        handler(b"", MagicMock(), QtNetwork.QNetworkReply.NetworkError.OperationCanceledError)
    if not album.loaded:
        album.abort_loading("Stopped by user")
    web.pending_requests_changed.emit()

    # Shown as stopped, not as loaded with the hierarchy tags incomplete.
    assert album.status == AlbumStatus.ERROR
    assert plugin_errors(caplog) == []


def test_removed_album_that_is_gone(picard_env, caplog):
    album = start_album_load(load_fixture("zauberfloete"))
    album.cancel_tasks()
    removed = weakref.ref(album)
    del album
    gc.collect()
    assert removed() is None
    picard_env.web.serve()

    assert plugin_errors(caplog) == []


def test_request_that_fails_to_send_still_finishes_the_album(picard_env, monkeypatch):
    web = picard_env.web
    get_url = web.get_url
    failed = []

    def get_url_failing_once(url, handler, **kwargs):
        if not failed:
            failed.append(url.path().split("/")[-1])
            raise RuntimeError("broken")
        return get_url(url, handler, **kwargs)

    monkeypatch.setattr(web, "get_url", get_url_failing_once)
    album = start_album_load(load_fixture("zauberfloete"))
    web.serve()

    assert failed == [AKT_I]
    assert album.loaded
    assert not album.has_critical_tasks()
    # The other works were still looked up.
    assert sorted(web.requested) == sorted([AKT_II, ZAUBERFLOETE])
    assert track(album, 1, 2).metadata["work"] == "Die Zauberflöte, K. 620: Akt I"
    assert track(album, 2, 1).metadata["work"] == "Die Zauberflöte, K. 620"

    # It isn't left pending, so the next album looks it up.
    web.requested.clear()
    start_album_load(load_fixture("zauberfloete"))
    assert web.requested == [AKT_I]


def test_lookups_outlasting_the_cache_are_not_repeated(picard_env, monkeypatch):
    web, module = picard_env.web, picard_env.module
    now = [0.0]
    store = module.WorkStore(clock=lambda: now[0])
    store.watch(picard_env.api)
    monkeypatch.setattr(module, "_store", store)
    album = start_album_load(zauberfloete_by_acts())

    # The acts arrive, then the opera after the cache has expired them,
    # as when a long queue of lookups is ahead of it.
    acts, web.queue = web.queue, []
    assert sorted(work_id for work_id, _ in acts) == sorted([AKT_I, AKT_II])
    for work_id, handler in acts:
        handler(load_work(work_id), MagicMock(), None)
    now[0] = 700.0
    web.serve()

    assert sorted(web.requested) == sorted([AKT_I, AKT_II, ZAUBERFLOETE])
    assert album.loaded
    assert all(t.metadata["work"] == "Die Zauberflöte, K. 620" for t in album.tracks)


def test_error_while_requesting_still_loads_the_album(picard_env, monkeypatch):
    def broken_request(*args, **kwargs):
        raise RuntimeError("broken")

    monkeypatch.setattr(picard_env.module._store, "request", broken_request)
    album = start_album_load(load_fixture("zauberfloete"))

    assert album.loaded
    assert not album.has_critical_tasks()
    assert album.errors == []


def test_failed_lookup_still_finishes_the_album(picard_env):
    web = picard_env.web
    album = start_album_load(load_fixture("zauberfloete"))
    web.serve(fail=frozenset([AKT_I]))

    assert album.loaded
    assert not album.has_critical_tasks()
    # Without Act I's own data its numbers stop at the act, which is known
    # from the numbers' relationships.
    t = track(album, 1, 2)
    assert t.metadata["work"] == "Die Zauberflöte, K. 620: Akt I"
    assert t.metadata["movementnumber"] == "1"
    assert t.metadata["movementtotal"] == ""
    # Act II is unaffected.
    assert track(album, 2, 1).metadata["work"] == "Die Zauberflöte, K. 620"


def test_cached_works_are_not_looked_up_again(picard_env):
    web = picard_env.web
    start_album_load(load_fixture("bach_keyboard_suites"))
    web.serve()
    web.requested.clear()

    album = start_album_load(load_fixture("bach_keyboard_suites"))

    assert web.requested == []
    assert album.loaded
    assert track(album, 1, 4).metadata["movement"] == "Passepied I / Passepied II"


def test_without_track_relationships_tags_are_left_alone(picard_env):
    web, settings = picard_env.web, picard_env.settings
    settings["track_ars"] = False
    release = load_fixture("bach_keyboard_suites")
    for medium in release["media"]:
        for t in medium["tracks"]:
            del t["recording"]["relations"]
    album = start_album_load(release)

    assert album.loaded
    assert web.requested == []
    t = track(album, 1, 4)
    assert "work_hierarchy" not in t.metadata
    assert "movement" not in t.metadata.deleted_tags


def test_recording_without_works_clears_stale_tags(picard_env):
    web = picard_env.web
    release = load_fixture("bach_keyboard_suites")
    recording = release["media"][0]["tracks"][3]["recording"]
    recording["relations"] = []
    recording["title"] = "Passepied I/II"
    album = start_album_load(release)
    web.serve()

    t = track(album, 1, 4)
    assert "work_hierarchy" in t.metadata.deleted_tags
    assert "movement" in t.metadata.deleted_tags


def test_recording_without_works_is_tagged_from_its_title(picard_env):
    web = picard_env.web
    release = load_fixture("bach_keyboard_suites")
    release["media"][0]["tracks"][3]["recording"]["relations"] = []
    album = start_album_load(release)
    web.serve()

    t = track(album, 1, 4)
    assert tags(t, "work", "movement", "movementnumber", "showmovement") == {
        "work": ["French Overture in B-minor, BWV 831"],
        "movement": ["Passepied I/II"],
        "movementnumber": ["4"],
        "showmovement": ["1"],
    }
    assert "movementtotal" in t.metadata.deleted_tags
    assert "work_hierarchy" in t.metadata.deleted_tags


def test_standalone_recording(picard_env):
    web = picard_env.web
    recording = load_fixture("zauberfloete")["media"][0]["tracks"][1]["recording"]
    t, file = load_standalone(picard_env, recording)

    # Picard considers the track loaded straight away, with the act known
    # from the recording's own data.
    assert t.loaded
    assert t.metadata["work"] == "Die Zauberflöte, K. 620: Akt I"

    web.serve()

    assert t.metadata["work"] == "Die Zauberflöte, K. 620"
    assert t.metadata["movement"].startswith("Akt I: No. 1 Introduktion")
    assert t.orig_metadata["movement"] == t.metadata["movement"]
    # The track's file gets the tags too.
    [file_metadata] = file.copy_metadata.call_args.args
    assert file_metadata["work"] == "Die Zauberflöte, K. 620"
    assert file_metadata["movement"] == t.metadata["movement"]


def test_error_while_tagging_still_finishes_the_album(picard_env, monkeypatch):
    web = picard_env.web
    album = start_album_load(load_fixture("zauberfloete"))

    def broken_apply_tags(*args, **kwargs):
        raise RuntimeError("broken")

    monkeypatch.setattr(picard_env.module, "apply_tags", broken_apply_tags)
    web.serve()

    assert album.loaded
    assert not album.has_critical_tasks()


def test_standalone_recording_runs_scripts_on_the_new_tags(picard_env):
    enable_script(picard_env.settings, "$set(work,$upper(%work%))$set(composer,$upper(%composer%))")
    release = zauberfloete_with_unlinked_composer()
    t, file = load_standalone(picard_env, release["media"][0]["tracks"][1]["recording"])
    assert t.metadata["work"] == "DIE ZAUBERFLÖTE, K. 620: AKT I"

    picard_env.web.serve()

    assert t.metadata["work"] == "DIE ZAUBERFLÖTE, K. 620"
    assert t.metadata["composer"] == "WOLFGANG AMADEUS MOZART"
    # The original metadata is unscripted, as Picard keeps it.
    assert t.orig_metadata["work"] == "Die Zauberflöte, K. 620"
    [file_metadata] = file.copy_metadata.call_args.args
    assert file_metadata["work"] == "DIE ZAUBERFLÖTE, K. 620"


def test_standalone_recording_is_left_alone_when_lookups_find_nothing(picard_env):
    recording = load_fixture("zauberfloete")["media"][0]["tracks"][1]["recording"]
    t, file = load_standalone(picard_env, recording)
    t.metadata["comment"] = "Edited in Picard"

    picard_env.web.serve(fail=frozenset([AKT_I]))

    assert t.metadata["work"] == "Die Zauberflöte, K. 620: Akt I"
    assert t.metadata["comment"] == "Edited in Picard"
    file.copy_metadata.assert_not_called()


def test_stopped_standalone_recording_is_left_alone(picard_env):
    web = picard_env.web
    recording = load_fixture("zauberfloete")["media"][0]["tracks"][1]["recording"]
    t, file = load_standalone(picard_env, recording)
    t.metadata["comment"] = "Edited in Picard"

    # What Picard's Stop does: the handlers of requests in progress are
    # called with a cancellation error, then pending requests are reported
    # changed.
    in_progress, web.queue = web.queue, []
    for _, handler in in_progress:
        handler(b"", MagicMock(), QtNetwork.QNetworkReply.NetworkError.OperationCanceledError)
    web.pending_requests_changed.emit()

    assert t.metadata["comment"] == "Edited in Picard"
    file.copy_metadata.assert_not_called()


def test_standalone_recording_keeps_changes_made_while_looking_up(picard_env):
    recording = load_fixture("zauberfloete")["media"][0]["tracks"][1]["recording"]
    t, file = load_standalone(picard_env, recording)
    t.metadata["comment"] = "Edited in Picard"
    t.metadata.delete("title")

    picard_env.web.serve()

    assert t.metadata["work"] == "Die Zauberflöte, K. 620"
    assert t.metadata["comment"] == "Edited in Picard"
    assert "title" in t.metadata.deleted_tags
    [file_metadata] = file.copy_metadata.call_args.args
    assert file_metadata["comment"] == "Edited in Picard"


def test_stopped_standalone_recording_keeps_the_levels_found(picard_env):
    web = picard_env.web
    recording = load_fixture("zauberfloete")["media"][0]["tracks"][1]["recording"]
    t, _ = load_standalone(picard_env, recording)
    # The act arrives, so the opera is looked up next.
    [(act, handler)], web.queue = web.queue, []
    handler(load_work(act), MagicMock(), None)
    assert [work_id for work_id, _ in web.queue] == [ZAUBERFLOETE]
    t.metadata["comment"] = "Edited in Picard"

    # What Picard's Stop does with a request that hasn't started: it's
    # dropped without calling its handler.
    web.queue.clear()
    web.pending_requests_changed.emit()

    # The opera is known from the act's own relationships.
    assert t.metadata["work"] == "Die Zauberflöte, K. 620"
    assert t.metadata["movementtotal"] == "31"
    assert t.metadata["comment"] == "Edited in Picard"


def test_standalone_recording_refreshed_without_relationships_is_left_alone(picard_env):
    recording = load_fixture("zauberfloete")["media"][0]["tracks"][1]["recording"]
    t, _ = load_standalone(picard_env, recording)

    # Refreshed meanwhile, with "Use track relationships" turned off.
    t.load()
    t._parse_recording({key: value for key, value in recording.items() if key != "relations"})
    picard_env.web.serve()

    assert "work_hierarchy" not in t.metadata


def test_disabling_the_plugin_finishes_loading_albums(picard_env):
    album = start_album_load(load_fixture("zauberfloete"))
    assert album.has_critical_tasks()

    picard_env.module.disable()

    assert album.loaded
    assert not album.has_critical_tasks()


def test_refreshed_standalone_recording_keeps_its_new_tags(picard_env):
    web = picard_env.web
    recording = load_fixture("zauberfloete")["media"][0]["tracks"][1]["recording"]
    t, _ = load_standalone(picard_env, recording)
    assert web.queue  # The act's lookup is waiting.

    # Refreshed meanwhile, after its work was unlinked on MusicBrainz.
    t.load()
    t._parse_recording({**recording, "relations": []})
    assert "work_hierarchy" not in t.metadata
    web.serve()

    # The earlier lookups don't bring the old work back.
    assert "work_hierarchy" not in t.metadata
    assert "work_hierarchy" not in t.orig_metadata
