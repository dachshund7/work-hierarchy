"""Build the plugin as a zip file Picard can install.

    uv run scripts/build_plugin.py

Writes dist/work_hierarchy.zip. Install it from Picard's Options > Plugins
page with "Install plugin...", or copy it into Picard's plugin folder.
"""

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "work_hierarchy"


def build(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / "work_hierarchy.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PACKAGE.glob("*.py")):
            zf.write(path, f"work_hierarchy/{path.name}")
    return archive


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"))
