"""The built zip loads through Picard's own plugin manager."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LOAD_PLUGIN = textwrap.dedent(
    """
    import sys
    from picard.i18n import setup_gettext
    from picard.pluginmanager import PluginManager, register_plugin_dir
    from picard.ui.options import _pages

    setup_gettext(None, "C")
    plugin_dir = sys.argv[1]
    register_plugin_dir(plugin_dir)
    manager = PluginManager(plugins_directory=plugin_dir)
    manager.load_plugins_from_directory(plugin_dir)

    [plugin] = manager.plugins
    print(plugin.name, plugin.version.to_string(short=True), plugin.compatible)
    # Iterating _pages only lists pages of plugins enabled in the config.
    print([page.NAME for page in _pages._ExtensionPoint__dict["work_hierarchy"]])
    print(sys.modules["picard.plugins.work_hierarchy"].process_track.__module__)
    """
)


def test_zip_loads_in_picard(tmp_path):
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_plugin.py"), str(tmp_path)],
        check=True,
        capture_output=True,
    )

    result = subprocess.run(
        [sys.executable, "-I", "-c", LOAD_PLUGIN, str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    name, options_pages, module = result.stdout.splitlines()
    assert name == "Work Hierarchy 0.1 True"
    assert options_pages == "['work_hierarchy']"
    assert module == "picard.plugins.work_hierarchy"


OPTIONS_PAGE = textwrap.dedent(
    """
    from unittest.mock import MagicMock
    from PyQt5 import QtWidgets
    from picard import config
    from picard.i18n import setup_gettext

    setup_gettext(None, "C")
    app = QtWidgets.QApplication([])
    fake_config = MagicMock()
    fake_config.setting = {"work_hierarchy_work_level": "top"}
    config.config = fake_config

    import work_hierarchy

    page = work_hierarchy.WorkHierarchyOptionsPage()
    page.load()
    print(page.top_level.isChecked(), page.parent_level.isChecked())
    page.parent_level.setChecked(True)
    page.save()
    print(fake_config.setting["work_hierarchy_work_level"])
    page.load()
    print(page.top_level.isChecked(), page.parent_level.isChecked())
    """
)


def test_options_page(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", OPTIONS_PAGE],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["True False", "parent", "False True"]
