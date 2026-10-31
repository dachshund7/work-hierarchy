"""Download test fixtures from the MusicBrainz web service.

Fetches each release the way Picard 3 does with "Use track relationships"
enabled, plus every ancestor work the plugin would look up, and saves them
under tests/fixtures/, without the parts neither Picard nor the plugin needs.

    uv run scripts/fetch_fixtures.py
"""

import importlib.util
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Load the module from its file: it doesn't need Picard, unlike the plugin's
# __init__.py.
_spec = importlib.util.spec_from_file_location("hierarchy", ROOT / "hierarchy.py")
assert _spec and _spec.loader
hierarchy = importlib.util.module_from_spec(_spec)
sys.modules["hierarchy"] = hierarchy
_spec.loader.exec_module(hierarchy)
WorkInfo = hierarchy.WorkInfo
performances = hierarchy.performances

RELEASES = {
    # J.S. Bach, Keyboard Suites (2016): several tracks contain two movements.
    "bach_keyboard_suites": "9706c884-fede-44d6-879d-cf19cd75a92e",
    # Mozart, Die Zauberflöte (1989): opera -> act -> number.
    "zauberfloete": "571f466c-c2fc-4f82-bacc-16e68389e608",
    # Bach Nostalghia (2021): two of Busoni's 10 Choral Preludes, BV B 27,
    # each an arrangement of a whole chorale.
    "bach_nostalghia": "fcf1a95f-fe1a-4c6b-a228-a2d4d7f1c075",
}

# Works fetched on their own, as a release would give them: "50/50" (2011),
# a soundtrack that lists the songs the film uses, and three of those songs,
# credited to composers, to writers and to no one. Then the two chorales of
# Bach Nostalghia, which are looked up once they're works, and the works they
# arrange, one of them in a collection.
WORKS = [
    "0833c785-e98e-4df4-896a-4c3a5532042c",  # 50/50
    "192334a0-9459-47b0-8108-04ebfa8379e3",  # Yellow Ledbetter
    "cd9694f3-ade2-3ad6-a6e3-34edeb338928",  # High and Dry
    "c990f0f6-6dc4-4172-bfaa-cbdfe4cdbce7",  # Bricks or Coconuts
    "0596a71a-8239-48c9-89ce-76f70dfaee15",  # Nun komm' der Heiden Heiland, BV B 27/3
    "4d11e286-273a-3a80-b8a6-386272c3bbbc",  # Wachet auf, ruft uns die Stimme, BV B 27/2
    "937fff7e-a992-3606-8fd6-5a402f581d9e",  # Nun komm, der Heiden Heiland, BWV 659.2
    "d729ebbf-c2d5-3193-a698-96ef50541fa6",  # Schübler-Choräle: Wachet auf, BWV 645
]

# The inc parameters Picard 3.0 uses for a release lookup with track
# relationships enabled (see Album.load in picard/album.py).
RELEASE_INC = [
    "aliases", "annotation", "artist-credits", "artists", "collections", "discids", "isrcs",
    "labels", "media", "recordings", "release-groups", "artist-rels", "label-rels",
    "recording-rels", "release-group-level-rels", "release-rels", "series-rels", "url-rels",
    "work-rels",
    "recording-level-rels", "work-level-rels",
]  # fmt: skip

USER_AGENT = "picard-work-hierarchy-fixtures/0.3 ( https://github.com/dachshund7/work-hierarchy )"
FIXTURES = ROOT / "tests" / "fixtures"

_last_request = 0.0


def get(path: str, inc: list[str]) -> dict:
    global _last_request
    url = f"https://musicbrainz.org/ws/2/{path}?fmt=json&inc={'+'.join(inc)}"
    for attempt in range(5):
        time.sleep(max(0.0, _last_request + 1.1 - time.monotonic()))
        _last_request = time.monotonic()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code != 503 or attempt == 4:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


# Optional keys that Picard checks for before reading, and which the plugin
# does not use. Dropping them keeps the fixtures small.
OPTIONAL_KEYS = {
    "aliases", "annotation", "collections", "discs", "genres", "isrcs", "label-info", "tags",
}  # fmt: skip


def trim(node):
    """Drop optional keys and every relationship but to works, composers and writers."""
    if isinstance(node, list):
        return [trim(item) for item in node]
    if not isinstance(node, dict):
        return node
    trimmed = {key: trim(value) for key, value in node.items() if key not in OPTIONAL_KEYS}
    if "relations" in trimmed:
        trimmed["relations"] = [
            rel
            for rel in trimmed["relations"]
            if rel.get("target-type") == "work" or rel.get("type") in ("composer", "writer")
        ]
    return trimmed


def save(path: Path, document: dict) -> None:
    path.write_text(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def main() -> None:
    works_dir = FIXTURES / "works"
    works_dir.mkdir(parents=True, exist_ok=True)
    for name, release_id in RELEASES.items():
        release = trim(get(f"release/{release_id}", RELEASE_INC))
        save(FIXTURES / f"{name}.json", release)
        print(f"release {name}")

        pending = {
            link.id
            for medium in release["media"]
            for track in medium["tracks"]
            for performance in performances(track["recording"])
            if (link := performance.work.parent())
        }
        done: set[str] = set()
        while pending:
            work_id = pending.pop()
            done.add(work_id)
            work = trim(get(f"work/{work_id}", ["work-rels", "artist-rels"]))
            save(works_dir / f"{work_id}.json", work)
            print(f"  work {work['title']}")
            parent = WorkInfo.from_json(work).parent()
            if parent and parent.id not in done:
                pending.add(parent.id)

    for work_id in WORKS:
        work = trim(get(f"work/{work_id}", ["work-rels", "artist-rels"]))
        save(works_dir / f"{work_id}.json", work)
        print(f"work {work['title']}")


if __name__ == "__main__":
    main()
