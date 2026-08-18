# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

This repository currently contains **no source code** — only the requirement specification
`話題沸騰ポット要求仕様書.xlsx` (the SESSAME "boiling pot" / electric kettle exercise spec,
Japanese). There is no build system, test runner, or linter yet. If asked to implement the pot,
the language/toolchain is an open decision — ask before assuming one.

## Reading the specification

**Read `docs/spec/*.txt` and `docs/spec/images/*.png`, not the xlsx directly.** These are
generated from `話題沸騰ポット要求仕様書.xlsx` by `scripts/convert_spec.py` — one text file per
sheet (`表紙.txt`, `改訂履歴.txt`, `ハードウエア構成とハードウエア要求仕様.txt`, `要求仕様.txt`,
`状態遷移.txt`), plus every embedded image extracted under `images/` with its original filename
(`imageN.png`). Each text file lists non-empty cells per row as `A1:value | B1:value | ...`, so
`pot-NNN` / `pot-NNN-MM` IDs stay greppable. `表紙.txt` and `状態遷移.txt` say
"drawing/diagram only" — those two sheets hold no cells, only a diagram image (the state-transition
diagram lives in `docs/spec/images/`).

In `要求仕様.txt`, cells that would otherwise read `#VALUE!` are rendered as
`[FIGURE images/imageN.png]` — these are figure placeholders (timing charts, the PID formula,
control-cycle diagrams) whose real content is the referenced image; open it when a requirement's
meaning depends on the figure.

**The xlsx remains the source of truth.** If it changes (check its `改訂履歴` sheet), regenerate
with `python3 scripts/convert_spec.py` — don't hand-edit files under `docs/spec/`. `openpyxl` and
`Pillow` are required (installed via apt as `python3-openpyxl` / `python3-pil`; the apt Pillow
package is named `python3-pil`, not `python3-pillow`, which can make it look absent).

### Notes for maintaining `scripts/convert_spec.py`

Two gotchas that made naive extraction fail before this script existed:

1. **The filename is NFD-normalized** (`ポ` is stored decomposed as `ホ` + combining dakuten), so a
   literally-typed filename will not open. Always glob: `glob.glob('*.xlsx')[0]`, or shell `*.xlsx`.
2. `openpyxl` already strips furigana ruby text (`<rPh>`) and resolves shared strings, so reading
   cell values through it does not need the manual `<rPh>`-stripping that a raw zipfile+ElementTree
   read would.

Sheet order in `xl/workbook.xml` maps to files as: `sheet1`=表紙, `sheet2`=改訂履歴,
`sheet3`=ハードウエア構成とハードウエア要求仕様, `sheet4`=要求仕様, `sheet5`=状態遷移.

The `#VALUE!` figure cells in 要求仕様 are Excel "in-cell rich value" images (a newer feature),
not drawing anchors — sheet4 has no drawing relationship at all. Each such cell carries a `vm="N"`
attribute in `xl/worksheets/sheet4.xml`; those indices are assigned in document order and happen to
line up 1:1 with `image1.png`..`imageN.png` for this workbook (chained through
`xl/metadata.xml` → `xl/richData/rdrichvalue.xml` → `xl/richData/richValueRel.xml` →
`xl/richData/_rels/richValueRel.xml.rels`, verified for all 10 figure cells). If the xlsx is
re-saved by a different Excel version, re-verify this mapping rather than assuming it still holds.

## Specification structure

`要求仕様` (sheet4) is the authoritative document. It is organized as a two-level requirement tree:

- **`pot-NNN`** — a top-level requirement, each stated as a 要求 (requirement) / 理由 (rationale) /
  説明 (notes) triple. The 理由 rows matter: they record *why* a behavior exists and should drive
  design decisions when the spec is ambiguous.
- **`pot-NNN-MM`** — testable sub-specifications under a named heading (`＜…＞`). These are the
  units to implement and test against; cite them by ID in code comments and test names.

Sections, in spec order:

| Range | Topic |
|---|---|
| pot-210 | Plug in/out, idle state, defaults |
| pot-220 / pot-221 | Lid close → boil, lid open → abort temperature control |
| pot-230 | Boil button (valid only while keep-warm and not dispensing) |
| pot-240 | Keep-warm modes (high 98°C / eco 90°C / milk 60°C), cycling, display |
| pot-250 | Lock / unlock button for the spout |
| pot-260 | Dispense button and pump stop conditions |
| pot-280 | Water-level judgment and indicator display |
| pot-310–312 | Boil action, 3-minute dechlorination hold, transition to keep-warm |
| pot-320 | Keep-warm action (PID) and its stop conditions |
| pot-330 | Idle (no temperature control) |
| pot-400 | Control schemes: duty-cycle basis, target-temp ON/OFF, PID, lookup-table |
| pot-500 | Error detection (over-temp, temp-not-falling, temp-not-rising) |

`ハードウエア構成とハードウエア要求仕様` (sheet3) defines the hardware contract: `pot-100-*`
exterior, `pot-110-*` internal parts, `pot-120-*` level meter. Two fail-safe defaults there are
load-bearing for the design — the full-water sensor defaults **on** and each level sensor defaults
**off**, both so a failure biases toward "don't heat".

## Domain model worth knowing before coding

- Three temperature-control *actions* form the core state machine: 沸騰行為 (boil), 保温行為
  (keep-warm), and アイドル (idle, operation amount forced to 0%). Boil → keep-warm happens only
  after the dechlorination hold; lid-off or all-level-sensors-off drops any action to idle.
- Boiling uses target-temperature ON/OFF control; keep-warm uses PID. The PID output is expressed
  incrementally: `ΔM = Kp(T1−T0) + Ki(Tg−T0) + Kd(2T1−T0−T2)`, `M0 = M1 + ΔM` clamped to 0–100%.
  M is a duty percentage of the control cycle, not a continuous power level.
- Debounce/timing constants are part of the spec, not implementation detail: lid closed after
  **3 s** on (also the instant at which water level is judged, to let the surface settle), lid open
  after **1 s** off, buttons require **100 ms**, button beep **100 ms**, dechlorination **3 min**,
  error buzzer **30 s**, error-check period **1 min**, full/empty blink **500 ms / 200 ms**.
- Water level is deliberately sensor-count-agnostic: the "n-th level sensor" array may shrink for
  cost reasons, so no individual sensor may carry unique meaning. The full-water sensor is the one
  exception and is a separate function.
- Errors latch: they clear only by unplugging and replugging.

### Known internal conflict

Sheet3's Japanese summary rows (`安全性要求`) disagree with sheet4's numbered requirements —
summary says over-temp above 100°C and a 5°C "temperature not rising" margin and lists only two
errors; `pot-500-11/-21/-31` say **110°C**, an **8°C** margin, and three errors including
temp-not-falling (keep-warm above 98°C for over 3 min). **Follow sheet4 (`pot-500-*`)**; treat
sheet3's overview as informal notes.

## Working notes

- All spec text is Japanese; keep requirement IDs verbatim so they stay greppable against
  `docs/spec/要求仕様.txt`.
- The workbook has a 改訂履歴 sheet — record spec changes there (then re-run
  `scripts/convert_spec.py`) rather than only in commit messages if you ever edit the xlsx.
