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
- **Recording dates.** A `recordingdate` tag says when the recording was
  made, from the dates MusicBrainz gives its performances, so a reissue
  can be told from the recording it reissues.
- **Arrangements.** Each work in the `work_hierarchy` tag says which work
  it's an arrangement of, if it is one, so a lute arrangement of a violin
  partita can be told from the partita, whose title it has.
- **Other names.** Each work also gives its other names on MusicBrainz, in
  any language, so a player can find Schubert's "Quintett A-Dur, D. 667
  „Forellenquintett“" by "Trout".

It replaces the Work & Movement plugin, which looks only one level up and uses
only the first work linked to a recording. Disable that plugin when using this
one.

## Requirements

- Picard 3. (Version 0.1 of this plugin is for Picard 2.9 to 2.13.)
- **Options → Metadata → "Use track relationships"** must be enabled. Without it
  MusicBrainz doesn't send work relationships, and the plugin leaves tags
  alone.

## Composer names in other scripts

MusicBrainz names some composers in the script of their language, such as
Валентин Сильвестров or Пётр Ильич Чайковский, and Picard writes those names
to `composer` unless it translates them. To have them in Latin letters, turn
on both of these in **Options → Metadata**:

- **"Translate artist names"**, with a language such as English.
- **"Use artist sort name for translation"**. MusicBrainz sends no aliases of
  the composers linked to works, which Picard would translate from, so
  without this option their names stay as they are. With it, a name in
  another script is taken from the composer's sort name: "Tchaikovsky, Pyotr
  Ilyich" gives "Pyotr Ilyich Tchaikovsky". Names already in Latin letters
  stay as they are.

The composers this plugin adds from parent works, and the composers in its
`work_hierarchy` tag, are named the same way as the ones Picard writes, so
they all match whichever options are on.

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
| `recordingdate`  | 1937-11-08/1938-03-08                                              |

The rules:

- `work` is the top-level work, unless a part of it has a catalogue number
  of its own, one the works above it don't have. Then the deepest such part
  is the work: each Prelude and Fugue of *The Well-Tempered Clavier* (BWV 846
  and on), each sonata of a set like *12 sonate da camera, op. 2* (op. 2 no.
  4). Acts and books have no catalogue numbers, so an opera stays the work.
  A track of a whole such part, a Prelude and Fugue in one track, has no
  movement tags. Movements often repeat their work's number, or give it
  another one, so the performed work only counts when it has parts.
- `movement` names every level below the work, joined with ": ", with their
  numbers ("I. oddíl: III. Sequentia: Quid sum miser"), except the performed
  work's, which is `movementnumber`. Players that want a different level can
  read it from `work_hierarchy`.
- Movement names have their parent's title removed, along with a leading
  number like "V." when it matches the movement's position. MusicBrainz often
  titles parts with a shortened form of the parent's title ("Die Zauberflöte:
  Akt I. No. 1 …" under "Die Zauberflöte, K. 620: Akt I"), so the match allows
  words to be skipped.
- A track with only part of a movement (MusicBrainz marks the performance
  "partial"), such as one half of an overture in two tracks, is named after
  its recording's title: "Ia. Overture – Adagio" in "Music for the Royal
  Fireworks, HWV 351: Ia. Overture – Adagio", rather than "Ouverture", which
  MusicBrainz links all the halves to. The levels above stay in `movement`.
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
  the works MusicBrainz links.
- Otherwise, if the recording has no work relationships, the movement tags
  and `work_hierarchy` are deleted so stale values don't linger in files.
- When a performed work has no composer on MusicBrainz, but a work above it
  does, the nearest such work's composers are added to `composer`,
  `composersort` and `musicbrainz_composerid`. They're added after any
  composers Picard set for the track's other works, named the way Picard
  names the composers it sets itself (see [Composer names in other
  scripts](#composer-names-in-other-scripts)).
- `recordingdate` runs from the earliest to the latest date MusicBrainz gives
  the recording's performance of its works and the sessions of its players,
  singers, conductors and recording engineer, as an ISO 8601 interval
  ("1937-11-08/1938-03-08"), or is a single date when they're the same. The
  dates can be partial ("2011-12", "1992"). The dates of other relationships
  are left out, as they can be later (a mix) or about the release (the
  phonographic copyright). Picard's `date` is the release date, and
  `originaldate` the first release's, which for a reissue is the closer of
  the two. When MusicBrainz has no date, the tag is deleted. Picard has no
  standard field for it, so it's a Vorbis comment `RECORDINGDATE`, a `TXXX`
  frame described `recordingdate`, an APE item `Recordingdate`, and a
  `----:com.apple.iTunes:recordingdate` atom, like `work_hierarchy`.
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
          "number": null,
          "numeral": null,
          "part_total": 11,
          "part_attributes": [],
          "collections": [],
          "composers": [
            { "id": "24f1766e-9635-4d58-a4d4-9413f9f98a4c", "name": "Johann Sebastian Bach" }
          ],
          "disambiguation": "",
          "arrangement_of": null,
          "aliases": [
            "French overture in B minor, BWV 831",
            "Overture in the French style, BWV 831"
          ]
        },
        {
          "id": "87f88e03-4f1d-3230-a37f-c12d23d8b091",
          "title": "Ouvertüre nach französischer Art h-Moll, BWV 831: V. Passepied I",
          "name": "Passepied I",
          "type": null,
          "position": 5,
          "number": 5,
          "numeral": "V",
          "part_total": 0,
          "part_attributes": [],
          "collections": [],
          "composers": [
            { "id": "24f1766e-9635-4d58-a4d4-9413f9f98a4c", "name": "Johann Sebastian Bach" }
          ],
          "disambiguation": "",
          "arrangement_of": null,
          "aliases": null
        }
      ],
      "work_index": 0,
      "attributes": [],
      "excerpt": null
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
          "number": 6,
          "numeral": "VI",
          "part_total": 0,
          "part_attributes": [],
          "collections": [],
          "composers": [
            { "id": "24f1766e-9635-4d58-a4d4-9413f9f98a4c", "name": "Johann Sebastian Bach" }
          ],
          "disambiguation": "",
          "arrangement_of": null,
          "aliases": null
        }
      ],
      "work_index": 0,
      "attributes": [],
      "excerpt": null
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
  - **`work_index`**: which node of `path` is the work, as for the `work` tag:
    `0` for the top-level work, or a part with a catalogue number of its own
    (see above). It's the same for the segments under one top-level work.
  - **`attributes`**: the recording–work relationship's attributes, such as
    `partial`, `live`, `medley`, `instrumental` or `cover`.
  - **`excerpt`**: for a track with only part of the performed work
    (`partial`), the name the recording's title gives that part, in the form
    of `name`, when it isn't the performed work's `name`; otherwise `null`.
- Each **path node**:
  - `id`: MusicBrainz work ID.
  - `title`: the full MusicBrainz title.
  - `name`: the title relative to the parent (see above); for the top-level
    work, the title.
  - `type`: MusicBrainz work type (`Opera`, `Symphony`, `Aria`, …) or `null`.
  - `position`: position in the parent work, or `null` for the top-level work
    or when MusicBrainz doesn't give one.
  - `number`: the number to show for the work: its `position`, or else the
    number its title starts with (as in "Mass: II. Gloria"), or `null`.
  - `numeral`: the number removed from the start of the title to give
    `name`, as the title writes it (`"V"` for "V. Passepied I"), or `null`.
    A number that disagrees with `position` stays in `name` instead.
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
    `musicbrainz_composerid` tags cover only the performed works, while
    these are given for every level of the hierarchy, named as the composer
    tags are (see above), though never as credited. A part's `composers` are
    empty when MusicBrainz links the composer only to its parent, even when
    the composer tags are inherited (see above).
  - `disambiguation`: the comment that MusicBrainz gives the work to tell it
    from works with the same title, such as "for two pianos, Busoni", or
    `""` when it has none; `null` if the work's own data couldn't be looked
    up.
  - `arrangement_of`: for an arrangement, the work it arranges, with `id`
    and `title`; otherwise `null`, also when the work's own data couldn't be
    looked up. MusicBrainz keeps the arrangements that have no work of
    their own under a "catch-all for arrangements" work, such as the one
    for Bach's Violin Partita no. 2, which is an arrangement of the partita.
    Some originals are linked to their catch-all the wrong way round, as
    arrangements of it, so a catch-all is never taken for the work
    arranged.
  - `aliases`: the work's other names on MusicBrainz, in any language, once
    each and without its `title`, such as "Piano quintet in A, 'Trout
    Quintet'"; `null` if they weren't looked up. They're looked up for the
    works above the performed ones and for a work performed whole, but not
    for the movements of a work.

### Grouping

- To group tracks by composition, use the `id` of the path node at
  `work_index`. Within a composition, sort by the `position` values along
  each path.
- Each segment is one movement, so a track with two segments can be listed
  under both movements.
- **Timing isn't included.** MusicBrainz doesn't record where in a recording
  each work starts, so the plugin doesn't say where one segment ends and the
  next begins, and it doesn't estimate. A track with several segments can't be
  split into one playable item per movement from this data alone.

## How it works

When Picard loads an album, the release data already includes, for each
recording, the works it performs and each work's immediate parent. The plugin
looks up each parent with `/ws/2/work/<id>?inc=work-rels+artist-rels+aliases` to
find the next level up, how many parts it has, who composed it and its other
names, and repeats until it reaches a top-level work. A work performed whole,
which isn't a parent, is looked up too, for its other names. The lookups are made for the whole release
at once (twenty tracks of three suites cost three requests), shared with
other albums loading at the same time, and cached for 10 minutes. Picard
waits for them before creating the album's tracks, so each track is tagged
once, with the complete hierarchy, before other plugins' track processors and
the tagger scripts run. If a lookup fails, the affected tracks are tagged with
the levels that are known, and the album still loads.
Picard's Stop button stops the lookups too, and the albums still loading are
shown as stopped, as with Picard's own requests, rather than as loaded with
the hierarchy incomplete.

For standalone recordings ("non-album tracks"), Picard finishes loading before
the lookups return. Once they arrive, the plugin tags the track, runs the
tagger scripts on it again, as Picard does when loading, and updates its files,
keeping any tags changed in Picard meanwhile. If the lookups find nothing new,
the track is left as it is.

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
- `__init__.py`: the Picard plugin (metadata processors and work lookups). It
  has no settings: the tags are meant to be right as they are.
- `tests/test_hierarchy.py`: the logic, against real MusicBrainz data.
- `tests/test_plugin.py`: the plugin, loaded by Picard's own plugin code,
  running inside Picard 3's real album and track loading code, with only the
  web service faked.
- `tests/test_install.py`: the working tree installing from git through
  Picard's plugin manager.
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

The plugin has no interface of its own. Its name and descriptions, shown in
Picard's plugin list, are translated in `MANIFEST.toml`, in `[name_i18n]`,
`[description_i18n]` and `[long_description_i18n]` tables:

```toml
[name_i18n]
de = "Werkhierarchie"
```

## License

GPL-2.0-or-later, like Picard.
