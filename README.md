# tilecount

**Work out exactly how many tiles, boxes and what it costs for a floor or wall, from one plain-text plan.**

`tilecount` is an offline command-line calculator. You describe the tile, the grout, the layout bond, your waste allowance and the rooms in a small text file. It returns exact tile counts (full and cut), an order quantity with waste, box rounding and cost. Every mistake is reported with its line number. It uses only the Python 3 standard library: no network, no accounts, no API keys.

## Features

- **Plain-text plan format** with one directive per line, `#` comments and CRLF line endings.
- **Line-numbered errors** for unknown directives, duplicates, bad sizes, unknown units and zero dimensions.
- **Mixed units** in millimetres, centimetres, metres, inches and feet, with exact `Decimal` arithmetic.
- **Per-side units**, so `3.2mx250cm` works and a trailing unit applies to any bare side.
- **Grout-aware layout**: tile pitch is tile width plus joint width, so joints are counted, not ignored.
- **Stack and half-bond layouts**: a half bond offsets every other course by half a tile.
- **Full versus cut tile breakdown** for each room, so you can see how many tiles get trimmed.
- **Waste allowance** applied to the whole order, then rounded up to whole tiles.
- **Box rounding and cost**: boxes needed, tiles left over and the total price, to the cent.
- **Area reports** in m² or ft², per room and in total.
- **Lint warnings** for oversized tiles, duplicate room names, unusual waste or grout, and a missing box price. Use `--strict` in CI to fail on them.
- **Text table or JSON** output from `report`, for scripts and spreadsheets.
- **ASCII layout preview** of any room, so you can see the bond pattern and where the grout runs.

## Quick start

`examples/bathroom.tiles` is a single room:

```text
tile Oak-Plank 300x300 mm
grout 3 mm
bond stack
waste 10%
box 10 29.90
room Big 3200x2500 mm
```

Run the report:

```sh
python3 tilecount.py report examples/bathroom.tiles
```

For this plan the room lays out **99 tiles** (80 full, 19 cut). With 10% waste the order is **109 tiles**. That's **11 boxes** of 10, with 1 left over, and costs **328.90**. A test checks these numbers.

For the bundled kitchen example, which uses a half bond across three rooms:

```sh
python3 tilecount.py report examples/kitchen.tiles
python3 tilecount.py report --json examples/kitchen.tiles
python3 tilecount.py layout examples/kitchen.tiles Hall --max-width 40
python3 tilecount.py check --strict examples/kitchen.tiles
```

## Plan file reference

| Directive | Example | Notes |
| --- | --- | --- |
| `tile NAME WxH [UNIT]` | `tile Oak 200x1200 mm` | Required, once. |
| `room NAME WxH [UNIT]` | `room Hall 2.4mx0.9m` | One line per rectangle. Repeat for L-shapes. |
| `grout WIDTH [UNIT]` | `grout 3 mm` or `grout 0.5cm` | Defaults to 0 mm. |
| `bond stack\|half` | `bond half` | Defaults to `stack`. |
| `waste PERCENT` | `waste 10%` | Defaults to 10. |
| `box COUNT PRICE` | `box 10 29.90` | Enables boxes and cost. |

Units are `mm`, `cm`, `m`, `in` and `ft`. An L-shaped room is entered as two rectangles, each laid out on its own.

## Commands

```text
tilecount check  PLAN [--strict]
tilecount report PLAN [--json] [--area-unit {m2,ft2}]
tilecount layout PLAN ROOM [--max-width N]
```

Exit codes: `0` success, `1` warnings with `--strict`, `2` a plan or file error. Errors are printed as `file:line: error: message`.

## How it works

Each rectangle is laid out per axis. Tile `i` covers `[offset + i*pitch, offset + i*pitch + size]`, where `pitch = tile + grout`. From that, the code finds the index range of tiles that touch the room and the range of tiles that lie wholly inside it. A half-bond course uses an offset of `-pitch/2`. Waste is `ceil(needed * (100 + waste) / 100)`, and boxes are `ceil(order / per_box)`.

## Install

Nothing to install for the tool itself; it runs on Python 3. Tests need `pytest`:

```sh
python3 -m pip install pytest
```

## Test

```sh
python3 -m pytest -q
```

The tests cover unit conversion, comment and CRLF handling, line-numbered parse errors, the stack and half-bond counts, the waste and box arithmetic, area units, lint warnings, the layout preview, the JSON output and the CLI exit codes.

## Limits

- Tiles are laid with their width along the room's width and are never rotated. To run planks the other way, swap the room's WxH.
- Rectangular rooms only. Model an L-shape as two rectangles. Shared tiles across the join are not merged, so the count is a slight over-estimate.
- Cut tiles are counted, not their shape. Waste covers the offcuts.
- There is no allowance for border trims, expansion gaps or wall-to-floor edge pieces. Add those to `waste`.
- The layout preview is a sampled approximation for judging the pattern, not a cutting plan.

## Project layout

```text
tilecount.py              the parser, layout maths, reports and CLI
examples/bathroom.tiles   a single-room plan matching the Quick Start numbers
examples/kitchen.tiles    a sample plan with a half bond and three rooms
tests/test_tilecount.py   the test suite
tests/fixtures/           broken and warning-only plans used by the tests
```
