import time
import traceback
import uuid
import weakref
from functools import partial

from picard.album_requests import TaskType
from picard.metadata import Metadata
from picard.plugin3.api import PluginApi
from picard.track import NonAlbumTrack
from PyQt6.QtNetwork import QNetworkReply

from .hierarchy import (
    WorkInfo,
    apply_recording_date,
    apply_tags,
    main_names,
    performances,
    resolve,
)

try:
    from picard.mbjson import _translate_artist_node
except ImportError:
    # Internal to Picard, so a later version could lose it.
    _translate_artist_node = None

#: How long looked-up works are reused before being requested again, so
#: corrections made on MusicBrainz show up when an album is refreshed.
CACHE_SECONDS = 10 * 60


def picard_names(artist):
    """An artist's name and sort name as Picard tags a composer it finds itself.

    That follows "Translate artist names" and "Use artist sort name for
    translation" in Options > Metadata. The web service only gives the
    aliases of related artists in some lookups, and never in work lookups,
    so a composer with a name in another script is often only translated
    from its sort name ("Tchaikovsky, Pyotr Ilyich"). Picard's function is
    internal, so the main names are used if it fails.
    """
    if _translate_artist_node is None:
        return main_names(artist)
    try:
        alias = _translate_artist_node(artist)
        return alias.name, alias.sort_name
    except Exception:
        return main_names(artist)


class WorkStore:
    """Looks up works on MusicBrainz, sharing results between lookups.

    Concurrent requests for the same work (albums of the same opera, say)
    are merged into one, and results are cached for a while. The cache keeps
    the responses, and their composers are named each time they're read, so
    a change to Picard's options for translating names applies right away.
    """

    def __init__(self, cache_seconds=CACHE_SECONDS, clock=time.monotonic, name_artist=main_names):
        self._cache_seconds = cache_seconds
        self._clock = clock
        self._name_artist = name_artist
        self._works = {}  # work ID -> (time fetched, response)
        self._pending = {}  # work ID -> callbacks waiting for it
        self._api = None

    def works(self):
        """The cached works, as a mapping from ID to WorkInfo."""
        now = self._clock()
        expired = [
            work_id
            for work_id, (fetched, _) in self._works.items()
            if now - fetched > self._cache_seconds
        ]
        for work_id in expired:
            del self._works[work_id]
        return {
            work_id: WorkInfo.from_json(response, self._name_artist)
            for work_id, (_, response) in self._works.items()
        }

    def watch(self, api):
        """Start looking up works, and noticing lookups the web service drops."""
        self._api = api
        api.web_service.pending_requests_changed.connect(self._requests_changed)

    def unwatch(self):
        """Stop, failing the lookups still pending.

        Nothing would notice them dropped any more, so albums waiting for
        them could wait forever.
        """
        api = self._api
        if api is None:
            return
        api.web_service.pending_requests_changed.disconnect(self._requests_changed)
        self._api = None
        for work_id in list(self._pending):
            self._notify(api, work_id, None)

    def request(self, work_id, callback):
        """Look up a work, then call ``callback(work_id, work, stopped)``.

        ``work`` is the WorkInfo, or None if the lookup failed. ``stopped``
        is true if it failed because the user stopped network requests.
        Returns False, and won't call ``callback``, if the request couldn't
        be sent.
        """
        if work_id in self._pending:
            self._pending[work_id].append(callback)
            return True
        api = self._api
        if api is None:
            # The plugin was disabled.
            return False
        self._pending[work_id] = [callback]
        try:
            api.mb_api.get(
                f"/work/{work_id}",
                partial(self._received, api, work_id),
                # The aliases of the work only: those of its composers don't
                # come with them
                unencoded_queryargs={"inc": "artist-rels+work-rels+aliases"},
                priority=True,
                important=False,
            )
        except Exception:
            # Nothing will answer, so later lookups mustn't wait for it.
            del self._pending[work_id]
            api.logger.error("Could not request work %s: %s", work_id, traceback.format_exc())
            return False
        return True

    def _received(self, api, work_id, response, reply, error):
        work = None
        stopped = False
        if error == QNetworkReply.NetworkError.OperationCanceledError:
            # Picard only reports cancelled requests to their handlers when
            # the user stops network requests.
            api.logger.info("Lookup of work %s was stopped", work_id)
            stopped = True
        elif error:
            api.logger.warning("Could not load work %s: %s", work_id, error)
        else:
            try:
                work = WorkInfo.from_json(response, self._name_artist)
                self._works[work_id] = (self._clock(), response)
            except Exception:
                api.logger.error("Could not read work %s: %s", work_id, traceback.format_exc())
        self._notify(api, work_id, work, stopped)

    def _requests_changed(self):
        """Fail lookups the web service no longer has.

        Picard drops some requests without calling their handlers: queued
        requests when the user stops network requests, and cancelled ones
        (a transfer timeout, say) at other times. Without this, the lookups
        would stay pending, and every later album needing those works would
        wait for them forever. A lookup counts as a pending request until
        its handler is called, so if the web service has none while lookups
        are pending, they were dropped.
        """
        api = self._api
        if api is None or not self._pending or api.web_service.num_pending_web_requests:
            return
        for work_id in list(self._pending):
            api.logger.warning("Lookup of work %s got no response", work_id)
            self._notify(api, work_id, None)

    def _notify(self, api, work_id, work, stopped=False):
        for callback in self._pending.pop(work_id, []):
            try:
                callback(work_id, work, stopped)
            except Exception:
                api.logger.error("%s", traceback.format_exc())


_store = WorkStore(name_artist=picard_names)


class AncestorLookup:
    """Looks up the ancestors of performed works until every path is complete.

    A work only names its parent, so the climb goes a level at a time, and
    through the store's cache as far as it can before requesting anything.
    Then the works that performed parts arrange, and then the works
    performed whole (see _advance). If it had to wait for lookups,
    ``finished()`` is called once the paths are as complete as they can be,
    unless ``abandoned()`` is true by then, or the user stopped network
    requests.
    """

    def __init__(self, api, store, recordings, finished, abandoned):
        self.api = api
        self.store = store
        #: The performances of each recording. Each is resolved on its own,
        #: as which work is the work depends on the others in its track.
        self.recordings = recordings
        self.finished = finished
        self.abandoned = abandoned
        #: The ancestors received, or found in the cache. Kept here as well,
        #: as the cache can expire them during a long queue of lookups.
        self.works = {}
        self.failed = set()
        self.waiting = set()
        self.done = False

    def start(self):
        """Request the ancestors that aren't known; return whether there are none."""
        return self._advance_safely()

    def _advance_safely(self):
        try:
            self.done = self._advance()
        except Exception:
            self.api.logger.error("%s", traceback.format_exc())
            # Better the levels known so far than an album that never loads.
            self.done = True
        return self.done

    def _advance(self):
        """Request the next ancestors; return whether there are none to wait for.

        Then the works that performed parts arrange, to tell whether those
        parts are works, and then the works performed whole, which aren't
        ancestors, for their aliases.
        """
        cached = self.store.works()
        while True:
            resolutions = [
                resolve(performed, self.works, self.failed) for performed in self.recordings
            ]
            unknown = frozenset().union(*(r.unknown | r.arranged | r.whole for r in resolutions))
            if not unknown:
                return True
            found = unknown & cached.keys()
            if found:
                self.works.update((work_id, cached[work_id]) for work_id in found)
                continue
            for work_id in sorted(unknown):
                if self.store.request(work_id, self._received):
                    self.waiting.add(work_id)
                else:
                    self.failed.add(work_id)
            if self.waiting:
                return False

    def _received(self, work_id, work, stopped):
        if self.done:
            # A lookup still in flight when the climb ended.
            return
        if stopped or self.abandoned():
            # Stopped by the user, Picard then aborts the albums still
            # loading, rather than have them shown as loaded with tags
            # missing. (Lookups Stop drops before they start are failed by
            # WorkStore._requests_changed instead.)
            self.done = True
            return
        if work is None:
            self.failed.add(work_id)
        else:
            self.works[work_id] = work
        self.waiting.discard(work_id)
        if not self.waiting and self._advance_safely():
            self.finished()


#: The works looked up for each album, for its tracks.
_album_works = weakref.WeakKeyDictionary()

#: The latest lookup of each standalone recording, so earlier ones can tell
#: it was reloaded. Tokens rather than lookups, as a lookup holds its track.
_standalone_lookups = weakref.WeakKeyDictionary()


def process_album(api, album, metadata, release_node):
    """Look up the parent works of the whole release before its tracks load.

    Picard creates an album's tracks, running the track processors, once
    the album has no critical tasks left, so each track is tagged once, with
    every level known, before later processors and the tagger scripts run.
    """
    recordings = [performances(recording, picard_names) for recording in _recordings(release_node)]
    task_id = f"{api.plugin_id}_works_{uuid.uuid4().hex}"
    album_ref = weakref.ref(album)

    def abandoned():
        # The album no longer has the task if it was removed, stopped or
        # reloaded while the lookups were running.
        current = album_ref()
        return current is None or task_id not in current._pending_tasks

    def finished():
        current = album_ref()
        if current is not None:
            current.complete_task(task_id)
            # Picard only finishes loading an album when one of its own
            # requests ends, so a plugin's task has to do it.
            current._finalize_loading(None)

    lookup = AncestorLookup(api, _store, recordings, finished, abandoned)
    _album_works[album] = lookup.works
    if not lookup.start():
        # Added directly rather than with api.add_album_task(), which gives
        # blocking tasks a 30 second timeout. Lookups queue behind the web
        # service's rate limit, so a box set with many works can take
        # longer, and an album finished early would miss tags. Failed or
        # stopped lookups still end the task.
        album.add_task(
            task_id,
            TaskType.CRITICAL,
            f"[{api.plugin_id}] Looking up parent works",
            plugin_id=api.plugin_id,
        )


def _recordings(release_node):
    """The recordings of a release's tracks, as Picard loads them."""
    for medium in release_node.get("media", ()):
        tracks = [medium.get("pregap"), *medium.get("tracks", ()), *medium.get("data-tracks", ())]
        for track in tracks:
            if track:
                yield track["recording"]


def process_track(api, track, metadata, track_node, release_node=None):
    # A standalone recording loaded again mustn't be tagged by earlier lookups.
    _standalone_lookups.pop(track, None)
    recording = track_node.get("recording", track_node)
    if "relations" not in recording:
        # "Use track relationships" is off: there is nothing to go on, so
        # leave existing tags alone.
        return
    metadata["~recording_work"] = metadata.getall("work")
    metadata["~recording_workid"] = metadata.getall("musicbrainz_workid")
    # It needs no lookups
    apply_recording_date(metadata, recording)
    write_tags = partial(apply_tags, recording_title=recording.get("title"))
    performed = performances(recording, picard_names)
    if isinstance(track, NonAlbumTrack):
        _tag_standalone(api, track, metadata, performed, write_tags)
    else:
        # The album's lookups have finished by now (see process_album).
        works = _album_works.get(track.album, {})
        write_tags(metadata, resolve(performed, works).segments)


def _tag_standalone(api, track, metadata, performed, write_tags):
    """Tag a standalone recording with what is known, and again if its lookups find more."""
    token = object()
    _standalone_lookups[track] = token

    def abandoned():
        # Being reloaded, or reloaded since, or removed.
        album = track.album
        return (
            _standalone_lookups.get(track) is not token
            or not track.loaded
            or album is None
            or track not in album.tracks
        )

    def finished():
        segments = resolve(performed, lookup.works).segments
        # Nothing new if the lookups failed, so the track is left alone.
        if segments != tagged:
            _update_standalone_track(track, write_tags, segments)

    lookup = AncestorLookup(api, _store, [performed], finished, abandoned)
    lookup.start()
    tagged = resolve(performed, lookup.works).segments
    write_tags(metadata, tagged)


def _update_standalone_track(track, write_tags, segments):
    """Tag a standalone recording whose lookups finished after it loaded.

    Picard finishes loading a standalone recording as soon as the track
    processors return: it copies the metadata to the track's original
    metadata, then runs the tagger scripts on the metadata. The tags are
    written to the original metadata and those steps repeated, so the
    scripts see them, and the files updated. Tags changed in Picard since
    the track loaded, those its scripts didn't give it, are kept.
    """
    loaded = Metadata(track.orig_metadata)
    track.run_scripts(loaded, strip_whitespace=True)
    changes = track.metadata.diff(loaded)
    write_tags(track.orig_metadata, segments)
    track.metadata.copy(track.orig_metadata)
    track.run_scripts(track.metadata, strip_whitespace=True)
    track.metadata.update(changes)
    if track.files:
        for file in track.files:
            # This updates the track's row too.
            track.update_file_metadata(file)
    else:
        track.update()
    album = track.album
    if album is not None:
        album.update(update_tracks=False)


def enable(api: PluginApi) -> None:
    api.register_album_metadata_processor(process_album)
    api.register_track_metadata_processor(process_track)
    _store.watch(api)


def disable() -> None:
    _store.unwatch()
