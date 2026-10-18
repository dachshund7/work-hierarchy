"""Test helpers.

The plugin tests drive Picard 2's real album and track loading code with the
plugin registered. Only the application object and the MusicBrainz web
service are faked; work lookups are answered from tests/fixtures/works.
"""

import importlib
import json
import pkgutil
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import picard.ui.options
import pytest
from picard import config
from picard.i18n import setup_gettext
from PyQt5 import QtCore, QtNetwork

FIXTURES = Path(__file__).parent / "fixtures"
WORKS = FIXTURES / "works"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def load_work(work_id: str) -> dict:
    return json.loads((WORKS / f"{work_id}.json").read_text(encoding="utf-8"))


class FakeMBAPI:
    """Records work lookups and answers them from the fixtures on demand."""

    def __init__(self) -> None:
        self.queue: list[tuple[str, Callable]] = []
        self.requested: list[str] = []
        self.calls: list[dict] = []

    def _get_by_id(self, entitytype, entityid, handler, inc=None, **kwargs):
        assert entitytype == "work"
        self.calls.append({"inc": inc, **kwargs})
        self.requested.append(entityid)
        self.queue.append((entityid, handler))

    def serve(self, fail: frozenset[str] = frozenset()) -> None:
        """Answer queued lookups, including ones made while answering."""
        while self.queue:
            work_id, handler = self.queue.pop(0)
            if work_id in fail:
                error = QtNetwork.QNetworkReply.NetworkError.ContentNotFoundError
                handler(None, MagicMock(), error)
            else:
                handler(load_work(work_id), MagicMock(), None)


class FakeTagger(QtCore.QObject):
    tagger_stats_changed = QtCore.pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.mb_api = FakeMBAPI()
        self.window = MagicMock()
        self.webservice = MagicMock()
        self.files: dict = {}
        self.albums: dict = {}
        self.stopping = False

    def get_release_group_by_id(self, rg_id):
        from picard.releasegroup import ReleaseGroup

        return ReleaseGroup(rg_id)


def _default_settings() -> dict:
    # Options register their defaults when the modules declaring them are
    # imported, so import every options page, and the plugin, first.
    for module in pkgutil.iter_modules(picard.ui.options.__path__):
        importlib.import_module(f"picard.ui.options.{module.name}")
    importlib.import_module("work_hierarchy")
    return {
        name: option.default
        for (section, name), option in config.Option.registry.items()
        if section == "setting"
    }


_DEFAULTS: dict | None = None


@pytest.fixture
def picard_env(monkeypatch):
    """A fresh fake Picard application with track relationships enabled."""
    global _DEFAULTS
    setup_gettext(None, "C")
    if _DEFAULTS is None:
        _DEFAULTS = _default_settings()

    import work_hierarchy

    fake_config = MagicMock()
    fake_config.setting = dict(_DEFAULTS, release_ars=True, track_ars=True)
    fake_config.persist = {}
    fake_config.profiles = {}
    monkeypatch.setattr(config, "config", fake_config)

    tagger = FakeTagger()
    monkeypatch.setattr(QtCore.QObject, "tagger", tagger, raising=False)
    monkeypatch.setattr(QtCore.QCoreApplication, "instance", lambda: tagger)
    monkeypatch.setattr(work_hierarchy, "_store", work_hierarchy.WorkStore())
    return tagger, fake_config.setting


def start_album_load(release: dict):
    """Load a release into a real Album as if the web service returned it.

    Returns once the release is parsed; the plugin's work lookups are left
    queued on the fake web service.
    """
    from picard.album import Album
    from picard.metadata import Metadata

    album = Album(release["id"])
    album.item = MagicMock()
    # The state Album.load() sets up before requesting the release.
    album._new_metadata = Metadata()
    album._new_tracks = []
    album._requests = 1
    album.loaded = False
    album.load_task = object()
    album._release_request_finished(release, None, None)
    return album
