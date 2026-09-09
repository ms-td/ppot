# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

This repository contains **no implementation code** — the requirement specification
`話題沸騰ポット要求仕様書.xlsx` (the SESSAME "boiling pot" / electric kettle exercise spec,
Japanese), the UML models built from it (`話題沸騰ポット.qeax`, `docs/analysis-analog/`), and the
Python helpers under `scripts/` that read and write those two. There is no build system, test
runner, or linter yet. If asked to implement the pot, the language/toolchain is an open decision —
ask before assuming one.

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
  after the dechlorination hold; lid-off or all-level-sensors-off drops any action to idle. Both
  the boil button and a keep-warm **mode change** leave 保温行為 for 沸騰行為 (see Known internal
  conflicts #2 for why the mode change does).
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

### Known internal conflicts

Sheet3's overview rows disagree with sheet4's numbered requirements in two places. They are
resolved differently — **#1 follows sheet4, #2 follows sheet3** — so check the individual entry
rather than assuming sheet4 always wins.

1. **Error thresholds** (sheet3 `安全性要求`) — summary says over-temp above 100°C and a 5°C
   "temperature not rising" margin, and lists only two errors; `pot-500-11/-21/-31` say **110°C**,
   an **8°C** margin, and three errors including temp-not-falling (keep-warm above 98°C for over
   3 min).

2. **Re-boil on keep-warm mode change** (sheet3 `＜その他の動作仕様＞`) — **decided: follow
   sheet3. A mode change goes through 沸騰行為.** Sheet3 says
   "保温モードに設定した際に100°Cでなかった場合、一度沸騰させたあと、自然に冷ましながら設定温度に保つ",
   so every mode change leaves 保温行為 for 沸騰行為 (the high mode keeps at 98°C, so the water is
   essentially never at 100°C, making the "if not 100°C" guard true in practice — the check still
   belongs in the model, since it is what the requirement states).

   This was raised with the instructor while asking about
   [docs/analysis-analog/memo.md](docs/analysis-analog/memo.md) item C and about how each mode's
   target temperature gets re-applied to 温度制御. The reading was put forward from our side and
   was not contradicted — and this instructor does normally push back with a specific `pot-NNN`
   when a reading is wrong — so it is taken as accepted. It is an argument from silence, so if the
   instructor later objects, this entry is the thing to revisit. Open question still live in
   discussion: whether re-boiling on every mode change is actually *good* for usability (dropping
   from 98°C to the milk mode's 60°C by first boiling to 100°C is hard to defend as a user
   experience, and it is also the more energy-hungry path — `pot-312` exists precisely to avoid
   wasted electricity).

   The tension with sheet4 is real and should be stated if asked, not hidden: `pot-240-21` only
   says the mode is set, and `pot-320-31` enumerates the stop conditions for 保温行為 as a list
   (error detected / lid sensor off / all level sensors off / boil button pressed) that does not
   mention a mode change. Adopting sheet3 means reading that list as non-exhaustive.

   Consequence for `pot-500-21` ("保温の各モードに**なって**3分以上水温が98°Cを超えていた場合"):
   the 3-minute window resets on entry to 保温行為, and because a mode change now re-enters
   保温行為, it resets on every mode change too. Keep the timer reset in 保温行為's entry action —
   that single placement covers both.

   That makes the window restartable by repeated user action, via two paths now: `pot-230-11`
   (boil button) and a mode change. This is accepted as a known limitation because both return
   paths always run カルキ抜き — `pot-311-11` holds the heater unconditionally on for 3 minutes —
   while `pot-500-11` (110°C) is armed throughout, so an anomaly that keeps the water above 98°C
   is more likely to trip `pot-500-11` there than to escape detection. Record this reasoning
   rather than "users would not do that": `pot-500` is a safety requirement.

## Modeling artifacts (Enterprise Architect)

The UML models live in `話題沸騰ポット.qeax`, a **plain SQLite database** — it can be read and
written directly with `sqlite3`/Python. Three rules before touching it:

1. **Close EA first.** A running EA holds the project in memory and writes it back on exit, so any
   direct edit made while it is open is silently lost. Check with PowerShell
   `Get-Process EA` (`ea_scenarios.py` does this itself and refuses to import; from Git Bash,
   `tasklist /FI ...` fails because MSYS rewrites the `/FI` switch as a path).
2. **Back up first** (`cp` to `*.bak_YYYYmmdd_HHMMSS`; those backups are gitignored) and run
   `pragma integrity_check` after.
3. The `sqlite3` CLI cannot open the NFD-normalized Japanese filename on Windows — use Python's
   `sqlite3` with `glob.glob('*.qeax')[0]`, the same trick `convert_spec.py` needs.

### Scenario text ⇄ EA: `scripts/ea_scenarios.py`

Communication diagrams live under the `コミュニケーション-シナリオ` package, one sub-package per
scene, each holding a `Collaboration` diagram and a `User` **Actor used as the container for the
scene's 事前条件 / 事後条件 / シナリオ** (the actor is deliberately not wired to the objects; this
is what lets a scenario be reused across diagrams).

`docs/analysis-analog/communication-scenarios.md` is the human-editable form of all of that, and
the script moves it both ways:

```
python scripts/ea_scenarios.py export              # EA -> markdown
python scripts/ea_scenarios.py import --dry-run    # show what would change
python scripts/ea_scenarios.py import              # markdown -> EA
```

`import` never deletes: an empty section means "not written yet", and a constraint left over in EA
is only warned about. Diagram coordinates are never touched; notes missing from a diagram are
created (element + `NoteLink` + placement) unless `--no-notes`.

Where things are stored, and the gotchas that make hand-written SQL fail:

- 事前/事後条件 → `t_objectconstraint` (`ConstraintType` is the literal `事前条件`/`事後条件`).
  `Constraint` is an SQL keyword — it must be quoted as `"Constraint"`.
- シナリオ → `t_objectscenarios` (`Notes` holds the body).
- The note shown on the diagram is a `t_object` row with `Object_Type='Note'`, and
  **`PDATA3` holds the constraint's literal text (or the scenario's name) as its link key**
  (`PDATA1`=`Constraint`/`Scenario`, `PDATA2`=owning element id, `Note`=rendered text). Editing the
  constraint text without updating `PDATA3` blanks the placed note — which is exactly what happens
  when it is edited through EA's GUI. Always update both. A consequence worth keeping in mind while
  modeling: constraint text *is* an identifier, so wording has to be consistent across scenes.
- When setting element visibility, `t_object.Scope` renders as Public when NULL — update every row,
  not just the ones that already say `'Public'`.

## Working notes

- All spec text is Japanese; keep requirement IDs verbatim so they stay greppable against
  `docs/spec/要求仕様.txt`.
- The workbook has a 改訂履歴 sheet — record spec changes there (then re-run
  `scripts/convert_spec.py`) rather than only in commit messages if you ever edit the xlsx.
