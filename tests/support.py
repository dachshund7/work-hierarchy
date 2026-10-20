"""Helpers shared by the tests, which they import directly.

Fixtures live in conftest.py; the repository root has a conftest.py of its
own, so importing from "conftest" would be ambiguous.
"""

import json
from pathlib import Path

from picard.album_requests import TaskType
from picard.plugin3.manager.install import get_plugin_directory_name
from picard.plugin3.manifest import PluginManifest
from picard.plugin3.plugin import Plugin

#: The repository, which is the plugin.
ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
WORKS = FIXTURES / "works"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def load_work(work_id: str) -> dict:
    return json.loads((WORKS / f"{work_id}.json").read_text(encoding="utf-8"))


def read_manifest() -> PluginManifest:
    with (ROOT / "MANIFEST.toml").open("rb") as manifest_file:
        return PluginManifest("work_hierarchy", manifest_file)


def installed_plugin(plugins_dir: Path) -> Plugin:
    """The plugin as Picard has it once installed from git, but loaded in place.

    Picard installs a plugin in a directory named after it and its UUID, which
    is also its module name, picard.plugins.<directory>. A link with that name
    to the repository gives the plugin the same name in the tests.
    """
    link = plugins_dir / get_plugin_directory_name(read_manifest())
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(ROOT, target_is_directory=True)
    plugin = Plugin(plugins_dir, link.name)
    plugin.read_manifest()
    assert plugin.uuid
    return plugin


def start_album_load(release: dict):
    """Load a release into a real Album as if the web service returned it.

    Returns once the release is parsed; the plugin's work lookups are left
    queued on the fake web service.
    """
    from picard.album import Album
    from picard.metadata import Metadata

    album = Album(release["id"])
    # The state Album.load() sets up before requesting the release.
    album._new_metadata = Metadata()
    album._new_tracks = []
    album.loaded = False
    album.add_task("release_metadata", TaskType.CRITICAL, "Release metadata")
    album._load_request = object()
    album._release_request_finished(release, None, None)
    return album
