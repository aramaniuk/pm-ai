# Traceability — pm-ai

Companion to `SPEC.md`. The bridge between this spec's capability ids and the ids the source PRD and the adopted architecture spine use.

**Why this file exists:** `ARCHITECTURE-SPINE.md` declares `binds: [FR-01..FR-40, NFR-01..NFR-14, UJ-1..UJ-10, SM-1..SM-5, SM-C1..SM-C3]` and its Capability → Architecture Map is keyed by FR id. Capability ids here are 1:1 with FR ids by construction, so the spine remains readable against this spec without re-editing it.

**Reading the columns.** *UJ* lists the journeys the source declares each requirement realizes (`user-journeys.md`); a dash means the source declares none — the capability is infrastructure rather than a journey step, not an orphan. *AD* points at the governing architecture decisions in the adopted spine; these are pointers, not restatements — the spine is authoritative for them.

| CAP | FR | Journeys | Governing ADs | Phase |
| --- | --- | --- | --- | --- |
| CAP-1 | FR-01 | UJ-3, 6, 7, 8 | AD-12, AD-19, AD-23 | 1 |
| CAP-2 | FR-02 | UJ-1, 2, 5, 6, 9, 10 | AD-9, AD-10, AD-11, AD-12, AD-39 | 2 |
| CAP-3 | FR-03 | UJ-3, 6, 7, 8 | AD-12, AD-19, AD-23, AD-33 | 1 |
| CAP-4 | FR-04 | — | AD-3, AD-20 | 1 |
| CAP-5 | FR-05 | UJ-3, 7 | AD-1, AD-13, AD-32 | 2 |
| CAP-6 | FR-06 | UJ-3, 6, 7, 8 | AD-1, AD-13 | 2 |
| CAP-7 | FR-07 | UJ-7 | AD-1, AD-13, AD-32 | 2 |
| CAP-8 | FR-08 | UJ-8 | AD-12, AD-19, AD-23 | 2 |
| CAP-9 | FR-09 | UJ-1, 9 | AD-15, AD-40, AD-41 | 3 |
| CAP-10 | FR-10 | UJ-1, 9 | AD-3, AD-24, AD-27, AD-42 | 1 |
| CAP-11 | FR-11 | UJ-9 | AD-41, AD-27, AD-34, AD-38, AD-40 | 3 |
| CAP-12 | FR-12 | UJ-1 | AD-15, AD-25, AD-41 | 3 |
| CAP-13 | FR-13 | UJ-1, 3, 6, 9 | AD-40, AD-2, AD-21, AD-35 | 3 |
| CAP-14 | FR-14 | UJ-1 | AD-42, AD-13, AD-5 | 3 |
| CAP-15 | FR-15 | UJ-1 | AD-42, AD-15 | 4 |
| CAP-16 | FR-16 | UJ-1, 9 | AD-4, AD-6, AD-25, AD-31 | 1 |
| CAP-17 | FR-17 | UJ-1 | AD-10, AD-12 | 3 |
| CAP-18 | FR-18 | UJ-1, 4, 5, 6, 8, 9, 10 | AD-2, AD-7, AD-8 | 1 |
| CAP-19 | FR-19 | UJ-1, 2, 4, 5, 6, 7, 8, 9, 10 | AD-2, AD-7, AD-21 | 1 |
| CAP-20 | FR-20 | — | AD-13, AD-42 | 1 |
| CAP-21 | FR-21 | UJ-2 | AD-1, AD-13 | 2 |
| CAP-22 | FR-22 | — | AD-1, AD-2, AD-13, AD-21 | 2 |
| CAP-23 | FR-23 | UJ-5 | AD-15, AD-21, AD-22 | 2 |
| CAP-24 | FR-24 | UJ-5 | AD-15, AD-21, AD-22 | 2 |
| CAP-25 | FR-25 | UJ-5 | AD-15, AD-22, AD-33 | 2 |
| CAP-26 | FR-26 | UJ-6 | AD-1, AD-20, AD-21, AD-40 | 2 |
| CAP-27 | FR-27 | UJ-1, 9 | AD-3, AD-24, AD-27, AD-38 | 2 |
| CAP-28 | FR-28 | — | AD-1, AD-18, AD-20 | 2 |
| CAP-29 | FR-29 | — | AD-1, AD-18, AD-20 | 3 |
| CAP-30 | FR-30 | UJ-4 | AD-4, AD-25, AD-28, AD-31 | 4 |
| CAP-31 | FR-31 | UJ-4 | AD-4, AD-13, AD-15, AD-25, AD-28, AD-31 | 4 |
| CAP-32 | FR-32 | UJ-6 | AD-1, AD-20, AD-21 | 2 |
| CAP-33 | FR-33 | UJ-3, 6 | AD-3, AD-5, AD-14, AD-34, AD-35, AD-36, AD-37 | 2 |
| CAP-34 | FR-34 | UJ-3, 6, 7, 8 | AD-3, AD-5, AD-14, AD-34, AD-35, AD-36, AD-37 | 1, 2 |
| CAP-35 | FR-35 | UJ-10 | AD-9, AD-10, AD-11, AD-12, AD-39 | 1, 2 |
| CAP-36 | FR-36 | — | AD-1, AD-12, AD-18, AD-29 | 1 |
| CAP-37 | FR-37 | — | AD-3, AD-20, AD-22 | 2 |
| CAP-38 | FR-38 | UJ-1 | AD-42 | unphased |
| CAP-39 | FR-39 | — | AD-42, AD-38 | unphased |
| CAP-40 | FR-40 | — | AD-42, AD-22, AD-3 | unphased |

## Metrics and budgets

- Success metrics `SM-1..SM-9` and counter-metrics `SM-C1..SM-C3` keep their source ids in `success-metrics.md`, where each row names the capabilities it validates.
- Non-functional budgets `NFR-01..NFR-14` keep their source ids in `nfr-budgets.md`. `NFR-13` is the cost target and appears out of numeric order in the source, grouped with cost rather than reliability.

## Jobs To Be Done

`JTBD-1..JTBD-12` are held in `user-journeys.md`. They are not mapped per capability here: the source expresses coverage through the journeys, and a second mapping would be a second thing to keep true.

## Where ids do not carry over

- **PRD §6 system topology** has no CAP id — it is superseded by the adopted spine's Design Paradigm and Stack, which are more current on model tiering and local model class.
- **The PRD Addendum Decisions Log** has no ids and is not carried into the spec; it is narrative rationale for how the current rules were reached, and the PRD retains it.
