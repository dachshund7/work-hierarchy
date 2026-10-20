"""The interface text and its translations in locale/.

Picard has no tool to extract a plugin's strings, so the English source,
locale/en.toml, is checked against the tr() and t_() calls in the code.
"""

import ast
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import tomlkit
from picard.plugin3.manager.install import get_plugin_directory_name
from support import ROOT, read_manifest

LOCALE = ROOT / "locale"
#: Keys copied from MANIFEST.toml for translators; Picard reads the
#: translations of these from the manifest's *_i18n tables instead.
MANIFEST_FIELDS = ("name", "description", "long_description")


def load_toml(path: Path) -> dict:
    # tomlkit, which Picard uses, rather than tomllib: type checking assumes
    # Python 3.10, the oldest Picard 3 runs on, which doesn't have tomllib.
    return tomlkit.parse(path.read_text(encoding="utf-8")).unwrap()


def literal_string(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def strings_in_code() -> dict[str, str]:
    """The keys and English text of every tr() and t_() call in the plugin."""
    strings: dict[str, str] = {}
    tree = ast.parse((ROOT / "__init__.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name not in ("tr", "t_"):
            continue
        args = [*node.args, None, None]
        key, text = literal_string(args[0]), literal_string(args[1])
        assert key and text, f"line {node.lineno}: {name}() needs a literal key and English text"
        assert strings.setdefault(key, text) == text, key
    return strings


def test_english_text_matches_the_code():
    english = load_toml(LOCALE / "en.toml")
    in_code = strings_in_code()
    assert in_code, "no tr() or t_() calls found"

    assert {key: english.get(key) for key in in_code} == in_code
    unused = set(english) - set(in_code) - {f"manifest.{field}" for field in MANIFEST_FIELDS}
    assert unused == set()


def test_english_manifest_text_matches_the_manifest():
    english = load_toml(LOCALE / "en.toml")
    manifest = load_toml(ROOT / "MANIFEST.toml")
    for field in MANIFEST_FIELDS:
        assert english[f"manifest.{field}"] == manifest[field], field


def test_translations_use_known_keys():
    english = set(load_toml(LOCALE / "en.toml"))
    unknown = {
        path.name: sorted(set(load_toml(path)) - english)
        for path in LOCALE.glob("*.toml")
        if path.name != "en.toml"
    }
    assert {name: keys for name, keys in unknown.items() if keys} == {}


TRANSLATED_PAGE = textwrap.dedent(
    """
    import sys
    from unittest.mock import MagicMock
    from PyQt6 import QtWidgets
    from picard import config
    from picard.i18n import setup_gettext
    from picard.plugin3.plugin import Plugin
    from picard.tagger import Tagger

    config_file, plugins_dir, plugin_id = sys.argv[1:]
    setup_gettext(None, "C", MagicMock())
    app = QtWidgets.QApplication([])
    Tagger.instance = staticmethod(lambda: app)
    config.config = config.Config.from_file(None, config_file)
    config.config.setting["ui_language"] = "fr"

    from pathlib import Path
    plugin = Plugin(Path(plugins_dir), plugin_id)
    plugin.read_manifest()
    module = plugin.load_module()
    plugin.enable(MagicMock())

    page = module.WorkHierarchyOptionsPage()
    print(page.display_title())
    print([box.title() for box in page.findChildren(QtWidgets.QGroupBox)])
    """
)


def test_options_page_is_translated(tmp_path):
    # A copy of the plugin with a French translation of some of the text.
    plugin_id = get_plugin_directory_name(read_manifest())
    plugin = tmp_path / "plugins3" / plugin_id
    (plugin / "locale").mkdir(parents=True)
    for name in ("MANIFEST.toml", "__init__.py", "hierarchy.py", "locale/en.toml"):
        shutil.copy(ROOT / name, plugin / name)
    (plugin / "locale" / "fr.toml").write_text(
        '"options.title" = "Hiérarchie des œuvres"\n"options.work_tag.title" = "Balise work"\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            TRANSLATED_PAGE,
            str(tmp_path / "Picard.ini"),
            str(tmp_path / "plugins3"),
            plugin_id,
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Hiérarchie des œuvres",
        # Text not translated yet falls back to the English.
        "['Balise work', 'Composer tags']",
    ]
