import time
import traceback
from functools import partial

from picard import log
from picard.album import NatAlbum
from picard.config import TextOption, get_config
from picard.metadata import register_track_metadata_processor
from picard.ui.options import OptionsPage, register_options_page
from PyQt5 import QtWidgets

from .hierarchy import (
    WORK_LEVEL_PARENT,
    WORK_LEVEL_TOP,
    WorkInfo,
    apply_tags,
    performances,
    resolve,
)

PLUGIN_NAME = "Work Hierarchy"
PLUGIN_AUTHOR = "John Hamilton"
PLUGIN_DESCRIPTION = """
Sets work and movement tags from MusicBrainz work relationships, following the
work hierarchy as many levels as it goes (for example opera, act, number) and
handling tracks that contain several movements.
<br><br>
Writes the standard <code>work</code>, <code>movement</code>,
<code>movementnumber</code>, <code>movementtotal</code> and
<code>showmovement</code> tags, plus a <code>work_hierarchy</code> tag holding
a JSON description of every work in the track and its path from the top-level
work.
<br><br>
Requires "Use track relationships" in Options &gt; Metadata. The original
values of <code>work</code> and <code>musicbrainz_workid</code> are kept in the
<code>_recording_work</code> and <code>_recording_workid</code> script variables.
"""
PLUGIN_VERSION = "0.1.0"
PLUGIN_API_VERSIONS = ["2.9", "2.10", "2.11", "2.12", "2.13"]
PLUGIN_LICENSE = "GPL-2.0-or-later"
PLUGIN_LICENSE_URL = "https://www.gnu.org/licenses/gpl-2.0.html"

SETTING_WORK_LEVEL = "work_hierarchy_work_level"

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

    def request(self, tagger, work_id, callback):
        """Look up a work, then call ``callback(work_id, succeeded)``."""
        if work_id in self._pending:
            self._pending[work_id].append(callback)
            return
        self._pending[work_id] = [callback]
        tagger.mb_api._get_by_id(
            "work",
            work_id,
            partial(self._received, work_id),
            inc=["work-rels", "artist-rels"],
            priority=True,
            important=False,
        )

    def _received(self, work_id, response, reply, error):
        succeeded = False
        if error:
            log.warning("%s: could not load work %s: %s", PLUGIN_NAME, work_id, error)
        else:
            try:
                work = WorkInfo.from_json(response)
                self._works[work_id] = (self._clock(), work)
                succeeded = True
            except Exception:
                log.error(
                    "%s: could not read work %s: %s",
                    PLUGIN_NAME,
                    work_id,
                    traceback.format_exc(),
                )
        for callback in self._pending.pop(work_id, []):
            try:
                callback(work_id, succeeded)
            except Exception:
                log.error("%s: %s", PLUGIN_NAME, traceback.format_exc())


_store = WorkStore()


class TrackJob:
    """Tags one track, looking up ancestor works until every path is complete.

    Tags are written straight away from the data in the release, then
    rewritten as each level of ancestors arrives. For album tracks the job
    counts as one of the album's pending requests, so Picard waits for it
    before running scripts and finishing the album.
    """

    def __init__(self, album, metadata, performed, work_level, store, recording_title=None):
        self.album = album
        self.metadata = metadata
        self.performed = performed
        self.recording_title = recording_title
        self.work_level = work_level
        self.store = store
        self.segments = ()
        self.failed = set()
        self.waiting = set()
        self.waited = False
        self.holding_album = False
        self.finished = False

    def run(self):
        try:
            resolution = resolve(self.performed, self.store.works(), self.failed)
            self.segments = resolution.segments
            apply_tags(self.metadata, self.segments, self.work_level, self.recording_title)
            if not resolution.unknown:
                self._finish()
                return
            self.waited = True
            if not self.holding_album and not isinstance(self.album, NatAlbum):
                self.album._requests += 1
                self.holding_album = True
            self.waiting = set(resolution.unknown)
            for work_id in sorted(resolution.unknown):
                self.store.request(self.album.tagger, work_id, self._received)
        except Exception:
            log.error("%s: %s", PLUGIN_NAME, traceback.format_exc())
            self._finish()

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
        if self.holding_album:
            self.holding_album = False
            self.album._requests -= 1
            self.album._finalize_loading(None)
        elif self.waited and isinstance(self.album, NatAlbum):
            self._update_non_album_track()

    def _update_non_album_track(self):
        """Push tags written after loading to a standalone recording's files.

        Picard finishes loading a standalone recording as soon as the track
        processors return, so tags written later have to be copied to the
        track's original metadata, and the files updated, explicitly.
        """
        for track in self.album.tracks:
            if track.metadata is self.metadata:
                apply_tags(
                    track.orig_metadata, self.segments, self.work_level, self.recording_title
                )
                break
        # For the standalone recordings "album" this updates the tracks' files.
        self.album._finalize_loading(None)


def process_track(album, metadata, track_node, release_node=None):
    recording = track_node.get("recording", track_node)
    if "relations" not in recording:
        # "Use track relationships" is off: there is nothing to go on, so
        # leave existing tags alone.
        return
    metadata["~recording_work"] = metadata.getall("work")
    metadata["~recording_workid"] = metadata.getall("musicbrainz_workid")
    work_level = get_config().setting[SETTING_WORK_LEVEL]
    TrackJob(
        album, metadata, performances(recording), work_level, _store, recording.get("title")
    ).run()


class WorkHierarchyOptionsPage(OptionsPage):
    NAME = "work_hierarchy"
    TITLE = "Work Hierarchy"
    PARENT = "plugins"

    options = [
        TextOption("setting", SETTING_WORK_LEVEL, WORK_LEVEL_TOP),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        box = QtWidgets.QGroupBox("Work tag", self)
        box_layout = QtWidgets.QVBoxLayout(box)
        self.top_level = QtWidgets.QRadioButton(
            "Top-level work; the movement tag names every level below it\n"
            "(work: Die Zauberflöte, movement: Akt I: No. 1 Introduktion)",
            box,
        )
        self.parent_level = QtWidgets.QRadioButton(
            "Parent of the performed work; the movement tag names only that work\n"
            "(work: Die Zauberflöte: Akt I, movement: No. 1 Introduktion)",
            box,
        )
        box_layout.addWidget(self.top_level)
        box_layout.addWidget(self.parent_level)
        layout.addWidget(box)
        note = QtWidgets.QLabel(
            "The work_hierarchy tag always describes the complete hierarchy.", self
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()

    def load(self):
        level = get_config().setting[SETTING_WORK_LEVEL]
        self.parent_level.setChecked(level == WORK_LEVEL_PARENT)
        self.top_level.setChecked(level != WORK_LEVEL_PARENT)

    def save(self):
        get_config().setting[SETTING_WORK_LEVEL] = (
            WORK_LEVEL_PARENT if self.parent_level.isChecked() else WORK_LEVEL_TOP
        )


register_track_metadata_processor(process_track)
register_options_page(WorkHierarchyOptionsPage)
