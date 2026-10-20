# Work Hierarchy: a MusicBrainz Picard 3 plugin

Tags classical music with its work and movement structure from MusicBrainz:

- **Any depth.** The plugin follows "part of" relationships up as far as they
  go, so an opera number is tagged as opera → act → number, a mass section
  as mass → section, and a symphony movement as symphony → movement.
- **Several movements in one track.** A track that performs, say, movements
  V and VI of a suite gets both, in the right order.
- **Machine-readable output.** Besides the standard tags, a `work_hierarchy`
  tag holds JSON describing every work in the track and its path from the
  top-level work, for players that want to show the structure or list the
  movements a track contains.

It replaces the Work & Movement plugin, which looks only one level up and uses
only the first work linked to a recording. Disable that plugin when using this
one.

## Requirements

- Picard 3. (Version 0.1 of this plugin is for Picard 2.9 to 2.13.)
- **Options → Metadata → "Use track relationships"** must be enabled. Without it
  MusicBrainz doesn't send work relationships, and the plugin leaves tags
  alone.

## Installing

In Picard, go to **Options → Plugins**, click **Install plugin…**, and on the
**URL** tab enter

```
https://github.com/dachshund7/work-hierarchy
```

leaving **Ref** empty. Or from a terminal:

```sh
picard-cli plugins install https://github.com/dachshund7/work-hierarchy
```

The plugin isn't in Picard's plugin registry, so Picard warns that it's
unregistered before installing it. Updates are installed from **Options →
Plugins** like any other plugin's.

## What gets written

For [*Keyboard Suites*](https://musicbrainz.org/release/9706c884-fede-44d6-879d-cf19cd75a92e)
track 4, which is a performance of BWV 831 movements V and VI:

| Tag              | Value                                               |
| ---------------- | --------------------------------------------------- |
| `work`           | Ouvertüre nach französischer Art h-Moll, BWV 831     |
| `movement`       | Passepied I / Passepied II                          |
| `movementnumber` | 5                                                   |
| `movementtotal`  | 11                                                  |
| `showmovement`   | 1                                                   |
| `work_hierarchy` | JSON, see below                                     |

And for an opera number (Die Zauberflöte → Akt I → No. 1):

| Tag              | Value                                                              |
| ---------------- | ------------------------------------------------------------------ |
| `work`           | Die Zauberflöte, K. 620                                            |
| `movement`       | Akt I: No. 1 Introduktion „Zu Hilfe! Zu Hilfe!” (Tamino, Damen)    |
| `movementnumber` | 1                                                                  |
| `movementtotal`  | 31                                                                 |

The rules:

- `work` is the top-level work, and `movement` names every level below it,
  joined with ": ". In **Options → Plugins → Work Hierarchy** you can make
  `work` the performed work's parent instead (`Die Zauberflöte, K. 620: Akt I`),
  in which case `movement` is only the performed work (`No. 1 Introduktion …`).
- Movement names have their parent's title removed, along with a leading
  number like "V." when it matches the movement's position. MusicBrainz often
  titles parts with a shortened form of the parent's title ("Die Zauberflöte:
  Akt I. No. 1 …" under "Die Zauberflöte, K. 620: Akt I"), so the match allows
  words to be skipped.
- When a track contains several works, their names are joined with " / ".
  Levels they share are named once ("Akt II: „Die Strahlen…” / „Heil sei
  euch…”"). `movementnumber` is the first one's.
- `movementnumber` is the position in the parent work (MusicBrainz's ordering
  of the parts). `movementtotal` is the parent's highest position. Positions
  sometimes have gaps; Act I of *Die Zauberflöte* has 27 numbered parts
  positioned up to 31.
- "Part of collection" relationships (e.g. *6 Partitas, BWV 825-830*) are not
  followed. The partita stays the work; the collection is recorded in the JSON.
- A recording of a work without parts (a song, say) gets `work` and no
  movement tags.
- Many movements on MusicBrainz aren't linked to their work, but are titled
  like one of its parts ("Suite for Cello Solo no. 1 in G major, BWV 1007: I.
  Prélude"), and many recordings aren't linked to works at all. When the
  performed works have no parent and aren't in a collection, or there are
  none, their titles, or the recording's, are read as "<Work>: <Roman
  numeral>. <Movement>", as the Work & Movement plugin does, for `work`,
  `movement` and `movementnumber`. `work_hierarchy` still only describes
  what MusicBrainz links.
- Otherwise, if the recording has no work relationships, the movement tags
  and `work_hierarchy` are deleted so stale values don't linger in files.
- When a performed work has no composer on MusicBrainz, but a work above it
  does, the nearest such work's composers are added to `composer`,
  `composersort` and `musicbrainz_composerid`. They're added after any
  composers Picard set for the track's other works, using their MusicBrainz
  names (not translated or as credited). Turn this off in **Options →
  Plugins → Work Hierarchy**.
- `musicbrainz_workid` is left as Picard sets it: the IDs of the works the
  recording performs. Picard's original `work` and `musicbrainz_workid` values
  are kept in the `_recording_work` and `_recording_workid` script variables,
  as with Work & Movement.

Tagger scripts run after the plugin has finished, so they can use all of
these tags.

## The `work_hierarchy` tag

A JSON object. Track 4 above:

```json
{
  "version": 1,
  "segments": [
    {
      "path": [
        {
          "id": "13c7fae5-83ee-4a34-be2f-efbf7a0e6639",
          "title": "Ouvertüre nach französischer Art h-Moll, BWV 831",
          "name": "Ouvertüre nach französischer Art h-Moll, BWV 831",
          "type": "Suite",
          "position": null,
          "part_total": 11,
          "part_attributes": [],
          "collections": [],
          "composers": [
            { "id": "24f1766e-9635-4d58-a4d4-9413f9f98a4c", "name": "Johann Sebastian Bach" }
          ]
        },
        {
          "id": "87f88e03-4f1d-3230-a37f-c12d23d8b091",
          "title": "Ouvertüre nach französischer Art h-Moll, BWV 831: V. Passepied I",
          "name": "Passepied I",
          "type": null,
          "position": 5,
          "part_total": 0,
          "part_attributes": [],
          "collections": [],
          "composers": [
            { "id": "24f1766e-9635-4d58-a4d4-9413f9f98a4c", "name": "Johann Sebastian Bach" }
          ]
        }
      ],
      "attributes": []
    },
    {
      "path": [
        { "id": "13c7fae5-…", "…": "same top-level work as above" },
        {
          "id": "36580573-9b66-3a7f-95ef-b12fb0b915ef",
          "title": "Ouvertüre nach französischer Art h-Moll, BWV 831: VI. Passepied II",
          "name": "Passepied II",
          "type": null,
          "position": 6,
          "part_total": 0,
          "part_attributes": [],
          "collections": [],
          "composers": [
            { "id": "24f1766e-9635-4d58-a4d4-9413f9f98a4c", "name": "Johann Sebastian Bach" }
          ]
        }
      ],
      "attributes": []
    }
  ]
}
```

(The tag itself is written compactly, without whitespace.)

- **`version`**: format version, currently `1`. It will change only if
  existing fields change meaning; new fields may be added without a change.
- **`segments`**: one entry per work the track performs, in performance order.
  - **`path`**: from the top-level work (first) down to the performed work
    (last). Paths in one track can have different lengths.
  - **`attributes`**: the recording–work relationship's attributes, such as
    `partial`, `live`, `medley`, `instrumental` or `cover`.
- Each **path node**:
  - `id`: MusicBrainz work ID.
  - `title`: the full MusicBrainz title.
  - `name`: the title relative to the parent (see above); for the top-level
    work, the title.
  - `type`: MusicBrainz work type (`Opera`, `Symphony`, `Aria`, …) or `null`.
  - `position`: position in the parent work, or `null` for the top-level work
    or when MusicBrainz doesn't give one.
  - `part_total`: the highest position among this work's parts (the number of
    parts if none are numbered; `0` if it has none), or `null` if the work's
    own data couldn't be looked up.
  - `part_attributes`: attributes of the relationship to the parent, such as
    `act`, `movement` or `number`, when MusicBrainz has them.
  - `collections`: collections this work is part of, each with `id`, `title`
    and `position`.
  - `composers`: the work's composers as linked on MusicBrainz, each with
    `id` (MusicBrainz artist ID) and `name`; `null` if the work's own data
    couldn't be looked up. Picard's own `composer` and
    `musicbrainz_composerid` tags cover only the performed works, and
    `composer` may be as credited or translated, while these are given for
    every level of the hierarchy, with their MusicBrainz names. A part's
    `composers` are empty when MusicBrainz links the composer only to its
    parent, even when the composer tags are inherited (see above).

### Grouping

- To group tracks by composition, use the first path node's `id`. Within a
  composition, sort by the `position` values along each path.
- Each segment is one movement, so a track with two segments can be listed
  under both movements.
- **Timing isn't included.** MusicBrainz doesn't record where in a recording
  each work starts, so the plugin doesn't say where one segment ends and the
  next begins, and it doesn't estimate. A track with several segments can't be
  split into one playable item per movement from this data alone.

## How it works

When Picard loads an album, the release data already includes, for each
recording, the works it performs and each work's immediate parent. The plugin
looks up each parent with `/ws/2/work/<id>?inc=work-rels+artist-rels` to find
the next level up, how many parts it has and who composed it, and repeats
until it reaches a top-level work. Lookups are shared between tracks (twenty
tracks of three suites cost three requests) and cached for 10 minutes. Picard
waits for them before finishing the album. If a lookup fails, the affected
tracks are tagged with the levels that are known, and the album still loads.
Picard's Stop button stops the lookups too, and the albums still loading are
shown as stopped, as with Picard's own requests, rather than as loaded with
the hierarchy incomplete.

For standalone recordings ("non-album tracks"), Picard finishes loading before
the lookups return. Once they arrive, the plugin tags the track, runs the
tagger scripts on it again, as Picard does when loading, and updates its files.
Changes made to the track in Picard before then are lost.

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run ty check
```

The repository is the plugin: Picard installs it by cloning it, and loads
`MANIFEST.toml` and `__init__.py` from the top level.

- `hierarchy.py`: resolution, naming and tag logic, with no Picard
  dependency.
- `__init__.py`: the Picard plugin (metadata processor, work lookups, options
  page).
- `tests/test_hierarchy.py`: the logic, against real MusicBrainz data.
- `tests/test_plugin.py`: the plugin, loaded by Picard's own plugin code,
  running inside Picard 3's real album and track loading code, with only the
  web service faked.
- `tests/test_install.py`: the working tree installing from git through
  Picard's plugin manager, and the options page.
- `locale/en.toml`: the interface text, in English; see Translating below.
- `tests/test_translations.py`: `locale/en.toml` against the text in the
  code, and a translation being used.
- `conftest.py`: stops pytest importing the top-level `__init__.py`, which
  only works inside Picard.
- `scripts/fetch_fixtures.py`: re-downloads the test fixtures in
  `tests/fixtures/`.

The development environment uses Python 3.14, but the plugin runs on the
Python bundled with Picard (3.10+ for Picard 3), so the plugin code avoids
newer syntax. Ruff and ty target 3.10 for the plugin's files.

To try changes in Picard without committing them, install from the **Local**
tab instead, choosing this directory and ticking **Load in-place (ignore
git)**. Picard then loads the plugin from here, and changes take effect the
next time it starts. Uninstall any copy installed from GitHub first: both have
the same UUID.

## Translating

The options page's text is in `locale/en.toml`. To translate it, copy that
file to `locale/<language>.toml`, for example `locale/de.toml` or
`locale/pt_BR.toml`, and translate the values, keeping the keys. Picard uses
the file for its interface language, and English for any text it doesn't
have.

The plugin's name and descriptions, shown in Picard's plugin list, are
translated in `MANIFEST.toml` instead, in `[name_i18n]`, `[description_i18n]`
and `[long_description_i18n]` tables:

```toml
[name_i18n]
de = "Werkhierarchie"
```

`locale/en.toml` repeats them under `manifest.*` keys, as other Picard plugins
do, so translators have all the text in one place.

When adding interface text to the code, use `self.api.tr("key", "English
text")` (or `t_()` for class attributes), and add the key and text to
`locale/en.toml`; the tests check the two match.

## License

GPL-2.0-or-later, like Picard.
