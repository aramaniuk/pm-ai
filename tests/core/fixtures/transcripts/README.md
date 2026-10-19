# Recorded Teams `.docx` export (story 11d)

`teams_export_recorded.docx` and `teams_export_recorded.md` are the fixtures `11d`'s
`.docx` and speaker-and-time readers are fitted to.

## Where they came from

A transcript of a two-person weekly meeting, downloaded from Teams as `.docx` by Andrei
on 2026-10-15 — the export field-test finding 22 records as refused. It was read on his
machine on 2026-10-19 by a one-off script that was not committed (it needs the original).
The original was deleted once these fixtures were recorded and the slice's tests passed.

## What the original held (measured 2026-10-19)

- `word/document.xml` with 184 paragraphs and a section's page settings. No tables, no
  text boxes, no alternative content, no hyperlinks, no tracked changes, no `-->` timings.
- Three header paragraphs: the meeting's title with a recording stamp, the date and start
  time (`DD Month YYYY, HH:MMam`), and the meeting's length (`NNm NNs`).
- 180 turn paragraphs, one per turn. Each is: a run holding a small avatar image
  (`w:drawing`); a bold run holding a line break, then the display name followed by three
  spaces; a run holding the time; then one run per spoken line, each starting with a line
  break. So in the paragraph's text the speaker-and-time line is
  `<display name>   <m:ss or mm:ss>` and every spoken line is a line of its own.
- 1 to 6 spoken lines per turn (98 turns of one line, 53 of two, 15 of three, 9 of four,
  3 of five, 2 of six); no turn without words; no spoken line holding a colon.
- Times of two fields only (`m:ss` for 71 turns, `mm:ss` for 109), in order, the last
  under 30 minutes. No three-field time occurs, so `1:03:07` is covered by tests only.
- A closing paragraph `<display name> stopped transcription`: the avatar run, the bold
  name, then the words, with no time.
- Two speakers, each a two-word display name in Latin letters; the spoken lines were in a
  non-Latin script.
- Speaker-and-time lines are 180 of the 493 non-blank lines from the first turn on
  (0.365), above the reader's quarter.
- Other parts, none carried over: styles, numbering, settings, footnotes and its
  relationships, comments, the document's relationships, 181 images, and three
  document-property parts (core, app, custom).

## What was replaced

- `[Content_Types].xml` and `_rels/.rels` were rewritten to name only `word/document.xml`;
  nothing else is in the archive. No document properties and no list of people.
- In `word/document.xml` only the text of each `w:t` element was replaced (677 of them),
  in place, so every element, attribute, run and line break is as exported:
  - the title by `Example Weekly Sync-up-20260914_093000-Meeting Recording`, the date line
    by `14 September 2026, 09:30am`, the length by `29m 03s`;
  - the two display names by `Alex Morgan` and `Sam Rivera` (in the turns and the closing
    line);
  - every spoken line by `Placeholder line <n> of turn <t>.` for one speaker and
    `Тестовая строка <n>, реплика <t>.` for the other, so the non-Latin text still passes
    through the reader;
  - the times and `stopped transcription` kept as they were.
- Every attribute in `word/document.xml` was listed before the copy was made. They are
  layout values only: sizes, positions, colours, fonts and image ids. There are no
  revision ids (`w:rsid…`) and no paragraph ids (`w14:paraId`), and the drawings' `name`,
  `descr` and `title` are empty.
- The avatar images' relationships are not in the archive, so the drawings point at
  nothing; Word may offer to repair the file. The reader never follows them.

`teams_export_recorded.md` is the same document converted by that script, not by a tool
Andrei used (which one the field test used is not recorded): a paragraph per block with
blank lines between, the bold name as `**…**`, the line break after the speaker-and-time
line as a trailing backslash (Markdown's hard line break), and each spoken line on a line
of its own.

## The search

Before committing, both files (every part of the `.docx`) were searched, case-insensitively
and as whole words, for every real name in the original: each display name whole and word
by word, the words of the title and the file name that are not generic meeting words, and
the words of the document properties and comment authors — 10 names. The search also
looked for every spoken line of six characters or more (240) and every document-property
value (1). It found none of them in either file.
