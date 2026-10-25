"""Test helpers.

The plugin tests drive Picard 3's real album and track loading code with the
plugin loaded and enabled through Picard's own plugin classes. Only the
application object and the web service are faked; work lookups are answered
from tests/fixtures/works.
"""

import importlib
import pkgutil
from collections.abc import Callable
from unittest.mock import MagicMock

import picard.ui.options
import pytest
from picard import config
from picard.extension_points import set_plugin_uuid, unset_plugin_uuid
from picard.i18n import setup_gettext
from picard.plugin3.api import PluginApi
from picard.tagger import Tagger
from PyQt6 import QtCore, QtNetwork
from support import installed_plugin, load_work


class FakeWebService(QtCore.QObject):
    """Records work lookups and answers them from the fixtures on demand."""

    pending_requests_changed = QtCore.pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.queue: list[tuple[str, Callable]] = []
        self.requested: list[str] = []
        self.calls: list[dict] = []

    def get_url(self, url, handler, **kwargs):
        entity, work_id = url.path().split("/")[-2:]
        assert entity == "work"
        self.calls.append(kwargs)
        self.requested.append(work_id)
        self.queue.append((work_id, handler))
        return MagicMock()

    def serve(self, fail: frozenset[str] = frozenset()) -> None:
        """Answer queued lookups, including ones made while answering."""
        while self.queue:
            work_id, handler = self.queue.pop(0)
            if work_id in fail:
                error = QtNetwork.QNetworkReply.NetworkError.ContentNotFoundError
                handler(None, MagicMock(), error)
            else:
                handler(load_work(work_id), MagicMock(), None)

    @property
    def num_pending_web_requests(self) -> int:
        return len(self.queue)


class FakeTagger(QtCore.QObject):
    tagger_stats_changed = QtCore.pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.webservice = FakeWebService()
        self.mb_api = MagicMock()
        self.window = MagicMock()
        self.files: dict = {}
        self.albums: dict = {}
        self.stopping = False

    def get_release_group_by_id(self, rg_id):
        from picard.releasegroup import ReleaseGroup

        return ReleaseGroup(rg_id)


def _register_default_options() -> None:
    # Options register their defaults when the modules declaring them are
    # imported.
    for module in pkgutil.iter_modules(picard.ui.options.__path__):
        importlib.import_module(f"picard.ui.options.{module.name}")


class PicardEnv:
    def __init__(self, tagger, settings, plugin):
        self.tagger = tagger
        self.web = tagger.webservice
        self.settings = settings
        self.plugin = plugin
        #: The plugin as Picard loaded it (picard.plugins.work_hierarchy_<uuid>).
        self.module = plugin._module
        self.api = PluginApi._instances[plugin.module_name]


@pytest.fixture
def picard_env(monkeypatch, tmp_path):
    """A fresh fake Picard with track relationships and the plugin enabled."""
    setup_gettext(None, "C", MagicMock())
    _register_default_options()

    tagger = FakeTagger()
    monkeypatch.setattr(QtCore.QCoreApplication, "instance", lambda: tagger)
    monkeypatch.setattr(Tagger, "instance", staticmethod(lambda: tagger))
    real_config = config.Config.from_file(None, str(tmp_path / "Picard.ini"))
    monkeypatch.setattr(config, "config", real_config)
    settings = real_config.setting
    settings["release_ars"] = True
    settings["track_ars"] = True

    plugin = installed_plugin(tmp_path / "plugins3")
    plugin_uuid = plugin.uuid
    assert plugin_uuid
    plugin.load_module()
    try:
        plugin.enable(tagger)
        set_plugin_uuid(plugin_uuid, plugin.plugin_id)
        settings["plugins3_enabled_plugins"] = [plugin_uuid]
        yield PicardEnv(tagger, settings, plugin)
    finally:
        plugin.disable()
        unset_plugin_uuid(plugin_uuid)
