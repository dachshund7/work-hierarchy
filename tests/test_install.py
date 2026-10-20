"""The plugin installs from git and loads through Picard 3's own plugin manager."""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from support import ROOT, read_manifest

TESTS = Path(__file__).resolve().parent
#: Where Picard installs the plugin, and its module name in picard.plugins.
PLUGIN_ID = f"work_hierarchy_{read_manifest().uuid}"

GIT = [
    "git",
    "-c",
    "user.name=Test",
    "-c",
    "user.email=test@example.com",
    "-c",
    "init.defaultBranch=main",
    "-c",
    "commit.gpgsign=false",
]


def repository_copy(destination: Path) -> Path:
    """A git repository of the working tree's files, as they would be pushed.

    The files git tracks or would track (untracked ones that aren't ignored),
    committed to a new repository, so the test covers changes not yet
    committed, and fails if a file the plugin needs is ignored.
    """
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    for name in listing.stdout.decode().split("\0"):
        source = ROOT / name
        if name and source.is_file():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "--no-verify", "-m", "Test"]):
        subprocess.run([*GIT, *command], cwd=destination, check=True)
    return destination


INSTALL_PLUGIN = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from unittest.mock import MagicMock
    from picard import config
    from picard.extension_points.options_pages import ext_point_options_pages
    from picard.metadata import track_metadata_processors
    from picard.i18n import setup_gettext
    from picard.plugin3.manager import PluginManager

    setup_gettext(None, "C", MagicMock())
    repository, config_file, plugins_dir = sys.argv[1:]
    config.config = config.Config.from_file(None, config_file)

    manager = PluginManager(MagicMock())
    manager.add_directory(plugins_dir, primary=True)
    # Skip the blacklist check, which downloads Picard's plugin registry.
    plugin_id = manager.install_plugin(
        repository, enable_after_install=True, force_blacklisted=True
    )

    [plugin] = manager.plugins
    print(plugin_id)
    print(plugin.name(), plugin.manifest.version.short_str(), plugin.state.value)
    print(plugin.local_path == Path(plugins_dir) / plugin_id)
    print([page.NAME for page in ext_point_options_pages if page.NAME == "work_hierarchy"])
    print([f.__module__ for f in track_metadata_processors.functions if "plugins" in f.__module__])
    print(config.config.setting["plugins3_enabled_plugins"] == [plugin.uuid])
    """
)


def test_plugin_installs_from_git(tmp_path):
    repository = repository_copy(tmp_path / "repository")

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            INSTALL_PLUGIN,
            str(repository),
            str(tmp_path / "Picard.ini"),
            str(tmp_path / "plugins3"),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            # Nothing should need the registry; fail fast if something does.
            "PICARD_PLUGIN_REGISTRY_URL": "http://127.0.0.1:9/plugins.toml",
        },
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        PLUGIN_ID,
        "Work Hierarchy 0.2 enabled",
        # Installed as a copy in Picard's plugin directory.
        "True",
        "['work_hierarchy']",
        f"['picard.plugins.{PLUGIN_ID}']",
        "True",
    ]


OPTIONS_PAGE = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from unittest.mock import MagicMock
    from PyQt6 import QtWidgets
    from picard import config
    from picard.i18n import setup_gettext
    from picard.tagger import Tagger

    tests, config_file, plugins_dir = sys.argv[1:]
    sys.path.insert(0, tests)
    from support import installed_plugin

    setup_gettext(None, "C", MagicMock())
    app = QtWidgets.QApplication([])
    Tagger.instance = staticmethod(lambda: app)
    config.config = config.Config.from_file(None, config_file)

    plugin = installed_plugin(Path(plugins_dir))
    module = plugin.load_module()
    plugin.enable(MagicMock())
    settings = module.WorkHierarchyOptionsPage.api.plugin_config

    page = module.WorkHierarchyOptionsPage()
    page.load()
    print(page.top_level.isChecked(), page.parent_level.isChecked())
    print(page.inherit_composers.isChecked())
    page.parent_level.setChecked(True)
    page.inherit_composers.setChecked(False)
    page.save()
    print(settings["work_level"], settings["inherit_composers"])
    page.load()
    print(page.top_level.isChecked(), page.parent_level.isChecked())
    print(page.inherit_composers.isChecked())
    # Shows the defaults without saving them.
    page.restore_defaults()
    print(page.top_level.isChecked(), page.parent_level.isChecked())
    print(page.inherit_composers.isChecked())
    print(settings["work_level"], settings["inherit_composers"])
    """
)


def test_options_page(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            OPTIONS_PAGE,
            str(TESTS),
            str(tmp_path / "Picard.ini"),
            str(tmp_path / "plugins3"),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "True False",
        "True",
        "parent False",
        "False True",
        "False",
        # Restore Defaults
        "True False",
        "True",
        "parent False",
    ]
