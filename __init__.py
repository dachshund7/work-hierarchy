import time
import traceback
import uuid
from functools import partial
from typing import ClassVar

from picard.album_requests import TaskType
from picard.plugin3.api import OptionsPage, PageOptionConfigs, PluginApi, t_
from picard.track import NonAlbumTrack
from PyQt6 import QtWidgets

from .hierarchy import (
    WORK_LEVEL_PARENT,
    WORK_LEVEL_TOP,
    WorkInfo,
    apply_composer_tags,
    apply_tags,
    performances,
    resolve,
)

SETTING_WORK_LEVEL = "work_level"
SETTING_INHERIT_COMPOSERS = "inherit_composers"

#: How long looked-up works are reused before being requested again, so
#: corrections made on MusicBrainz show up when an album is refreshed.
CACHE_SECONDS = 10 * 60


class WorkStore:
    """Looks up works on MusicBrainz, sharing results between tracks.

    Concurrent requests for the same work (every track of a symphony needs
    its parent) are merged into one, and results are cached for a while.
    """

    def __init__(self, cache_seconds=CACHE_SECONDS, clock=time.monotonic):
        self._cache_seconds = cache_seconds
        self._clock = clock
        self._works = {}  # work ID -> (time fetched, WorkInfo)
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
        return {work_id: work for work_id, (_, work) in self._works.items()}

    def watch(self, api):
        """Start noticing lookups the web service drops; see _requests_changed."""
        self._api = api
        api.web_service.pending_requests_changed.connect(self._requests_changed)

    def unwatch(self):
        if self._api is not None:
            self._api.web_service.pending_requests_changed.disconnect(self._requests_changed)
            self._api = None

    def request(self, api, work_id, callback):
        """Look up a work, then call ``callback(work_id, succeeded)``."""
        if work_id in self._pending:
            self._pending[work_id].append(callback)
            return
        self._pending[work_id] = [callback]
        api.mb_api.get(
            f"/work/{work_id}",
            partial(self._received, api, work_id),
            unencoded_queryargs={"inc": "artist-rels+work-rels"},
            priority=True,
            important=False,
        )

    def _received(self, api, work_id, response, reply, error):
        succeeded = False
        if error:
            api.logger.warning("Could not load work %s: %s", work_id, error)
        else:
            try:
                work = WorkInfo.from_json(response)
                self._works[work_id] = (self._clock(), work)
                succeeded = True
            except Exception:
                api.logger.error("Could not read work %s: %s", work_id, traceback.format_exc())
        self._notify(api, work_id, succeeded)

    def _requests_changed(self):
        """Fail lookups the web service no longer has.

        Stopping network requests drops queued requests without calling
        their handlers. Without this, the lookups would stay pending, and
        every later track needing those works would wait for them forever.
        A lookup counts as a pending request until its handler is called, so
        if the web service has none while lookups are pending, they were
        dropped.
        """
        api = self._api
        if api is None or not self._pending or api.web_service.num_pending_web_requests:
            return
        for work_id in list(self._pending):
            api.logger.warning("Lookup of work %s was stopped", work_id)
            self._notify(api, work_id, False)

    def _notify(self, api, work_id, succeeded):
        for callback in self._pending.pop(work_id, []):
            try:
                callback(work_id, succeeded)
            except Exception:
                api.logger.error("%s", traceback.format_exc())


_store = WorkStore()


class TrackJob:
    """Tags one track, looking up ancestor works until every path is complete.

    Tags are written straight away from the data in the release, then
    rewritten as each level of ancestors arrives. For album tracks the job
    holds a critical task on the album, so Picard waits for it before running
    scripts and finishing the album.
    """

    def __init__(
        self,
        api,
        track,
        metadata,
        performed,
        work_level,
        store,
        recording_title=None,
        inherit_composers=True,
    ):
        self.api = api
        self.track = track
        self.metadata = metadata
        self.performed = performed
        self.recording_title = recording_title
        self.work_level = work_level
        self.inherit_composers = inherit_composers
        self.store = store
        self.segments = ()
        self.failed = set()
        self.waiting = set()
        self.task_id = None
        self.in_processor = False
        self.finished = False

    @property
    def standalone(self):
        return isinstance(self.track, NonAlbumTrack)

    def start(self):
        """Run from the track metadata processor."""
        self.in_processor = True
        try:
            self.run()
        finally:
            self.in_processor = False

    def run(self):
        try:
            done = self._advance()
        except Exception:
            self.api.logger.error("%s", traceback.format_exc())
            done = True
        if done:
            try:
                self._finish()
            except Exception:
                self.api.logger.error("%s", traceback.format_exc())

    def _advance(self):
        """Tag the track with what is known; return whether that is all there is."""
        resolution = resolve(self.performed, self.store.works(), self.failed)
        self.segments = resolution.segments
        # A standalone recording's tags are written once loading has
        # finished, in _finish.
        if self.in_processor or not self.standalone:
            self._apply_tags(self.metadata)
        if not resolution.unknown:
            return True
        if self.task_id is None and not self.standalone:
            task_id = f"{self.api.plugin_id}_works_{uuid.uuid4().hex}"
            # Added directly rather than with api.add_album_task(), which
            # gives blocking tasks a 30 second timeout. Lookups queue behind
            # the web service's rate limit, so a box set with many works can
            # take longer, and an album finished early would miss tags in its
            # scripts and files. Failed or stopped lookups still end the task.
            self.track.album.add_task(
                task_id,
                TaskType.CRITICAL,
                f"[{self.api.plugin_id}] Looking up parent works",
                plugin_id=self.api.plugin_id,
            )
            self.task_id = task_id
        self.waiting = set(resolution.unknown)
        for work_id in sorted(resolution.unknown):
            self.store.request(self.api, work_id, self._received)
        return False

    def _apply_tags(self, metadata):
        apply_tags(metadata, self.segments, self.work_level, self.recording_title)
        if self.inherit_composers:
            apply_composer_tags(metadata, self.segments)

    def _received(self, work_id, succeeded):
        if self.finished:
            # Lookups still in flight when the job stopped on an error.
            return
        if not succeeded:
            self.failed.add(work_id)
        self.waiting.discard(work_id)
        if not self.waiting:
            self.run()

    def _finish(self):
        self.finished = True
        if self.task_id is not None:
            self._release_album()
        elif self.standalone and not self.in_processor:
            self._update_standalone_track()

    def _release_album(self):
        album = self.track.album
        task_id, self.task_id = self.task_id, None
        # The album no longer has the task if it was removed, stopped or
        # reloaded while the lookups were running. It is then not this job's
        # to finish.
        if album is None or task_id not in album._pending_tasks:
            return
        album.complete_task(task_id)
        # While the track processors run, Picard finishes the album itself
        # once they return.
        if not self.in_processor and not album.loaded:
            album._finalize_loading(None)

    def _update_standalone_track(self):
        """Tag a standalone recording whose lookups finished after it loaded.

        Picard finishes loading a standalone recording as soon as the track
        processors return: it copies the metadata to the track's original
        metadata, then runs the tagger scripts on the metadata. The tags are
        written to the original metadata and those steps repeated, so the
        scripts see them, and the files updated.
        """
        track = self.track
        if not track.loaded:
            # Being reloaded, which runs a new job.
            return
        self._apply_tags(track.orig_metadata)
        track.metadata.copy(track.orig_metadata)
        track.run_scripts(track.metadata, strip_whitespace=True)
        if track.files:
            for file in track.files:
                # This updates the track's row too.
                track.update_file_metadata(file)
        else:
            track.update()
        album = track.album
        if album is not None:
            album.update(update_tracks=False)


def process_track(api, track, metadata, track_node, release_node=None):
    recording = track_node.get("recording", track_node)
    if "relations" not in recording:
        # "Use track relationships" is off: there is nothing to go on, so
        # leave existing tags alone.
        return
    metadata["~recording_work"] = metadata.getall("work")
    metadata["~recording_workid"] = metadata.getall("musicbrainz_workid")
    TrackJob(
        api,
        track,
        metadata,
        performances(recording),
        api.plugin_config[SETTING_WORK_LEVEL],
        _store,
        recording.get("title"),
        api.plugin_config[SETTING_INHERIT_COMPOSERS],
    ).start()


class WorkHierarchyOptionsPage(OptionsPage):
    # The interface text is translated from locale/<language>.toml; the
    # English text, in locale/en.toml, is also given here as the fallback.
    NAME = "work_hierarchy"
    TITLE = t_("options.title", "Work Hierarchy")
    PARENT = "plugins"
    # Registers the settings with the page, for Restore Defaults.
    OPTIONS: ClassVar[PageOptionConfigs] = {
        SETTING_WORK_LEVEL: {},
        SETTING_INHERIT_COMPOSERS: {},
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        tr = self.api.tr
        layout = QtWidgets.QVBoxLayout(self)
        box = QtWidgets.QGroupBox(tr("options.work_tag.title", "Work tag"), self)
        box_layout = QtWidgets.QVBoxLayout(box)
        self.top_level = QtWidgets.QRadioButton(
            tr(
                "options.work_tag.top_level",
                "Top-level work; the movement tag names every level below it\n"
                "(work: Die Zauberflöte, movement: Akt I: No. 1 Introduktion)",
            ),
            box,
        )
        self.parent_level = QtWidgets.QRadioButton(
            tr(
                "options.work_tag.parent_level",
                "Parent of the performed work; the movement tag names only that work\n"
                "(work: Die Zauberflöte: Akt I, movement: No. 1 Introduktion)",
            ),
            box,
        )
        box_layout.addWidget(self.top_level)
        box_layout.addWidget(self.parent_level)
        layout.addWidget(box)
        note = QtWidgets.QLabel(
            tr(
                "options.work_tag.note",
                "The work_hierarchy tag always describes the complete hierarchy.",
            ),
            self,
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        composer_box = QtWidgets.QGroupBox(tr("options.composer_tags.title", "Composer tags"), self)
        composer_layout = QtWidgets.QVBoxLayout(composer_box)
        self.inherit_composers = QtWidgets.QCheckBox(
            tr(
                "options.composer_tags.inherit",
                "When a performed work has no composer on MusicBrainz, use its\n"
                "nearest parent work's composers",
            ),
            composer_box,
        )
        composer_layout.addWidget(self.inherit_composers)
        layout.addWidget(composer_box)
        layout.addStretch()

    def load(self):
        level = self.api.plugin_config[SETTING_WORK_LEVEL]
        self.parent_level.setChecked(level == WORK_LEVEL_PARENT)
        self.top_level.setChecked(level != WORK_LEVEL_PARENT)
        self.inherit_composers.setChecked(self.api.plugin_config[SETTING_INHERIT_COMPOSERS])

    def save(self):
        self.api.plugin_config[SETTING_WORK_LEVEL] = (
            WORK_LEVEL_PARENT if self.parent_level.isChecked() else WORK_LEVEL_TOP
        )
        self.api.plugin_config[SETTING_INHERIT_COMPOSERS] = self.inherit_composers.isChecked()


def enable(api: PluginApi) -> None:
    api.plugin_config.register_option(SETTING_WORK_LEVEL, WORK_LEVEL_TOP)
    api.plugin_config.register_option(SETTING_INHERIT_COMPOSERS, True)
    api.register_track_metadata_processor(process_track)
    api.register_options_page(WorkHierarchyOptionsPage)
    _store.watch(api)


def disable() -> None:
    _store.unwatch()
