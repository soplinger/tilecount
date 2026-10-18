"""tilecount: offline floor and wall tile calculator driven by one plain-text plan.

A plan file has one directive per line. Blank lines and text after ``#`` are ignored::

    tile Oak-Plank 200x1200 mm   # the tile you are buying (required, once)
    grout 3 mm                   # joint width (optional, default 0)
    bond half                    # stack or half (optional, default stack)
    waste 10%                    # cut-tile allowance (optional, default 10)
    box 10 29.90                 # tiles per box and price per box (optional)
    room Kitchen 3.2mx2.5m       # one rectangle per room (repeat as needed)

All arithmetic uses ``Decimal`` so millimetre sizes and money stay exact.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

# Length units expressed in millimetres.
UNITS = {
    "mm": Decimal("1"),
    "cm": Decimal("10"),
    "m": Decimal("1000"),
    "in": Decimal("25.4"),
    "ft": Decimal("304.8"),
}
MM2_PER_M2 = Decimal("1000000")
MM2_PER_FT2 = Decimal("92903.04")
CENT = Decimal("0.01")
HALF = Decimal("0.5")

DIM_RE = re.compile(r"(\d+(?:\.\d+)?)([a-z]*)x(\d+(?:\.\d+)?)([a-z]*)")
LEN_RE = re.compile(r"(\d+(?:\.\d+)?)([a-z]*)")
BONDS = ("stack", "half")


class PlanError(Exception):
    """A problem with the plan as a whole, with no single line to blame."""


class ParseError(PlanError):
    """A problem on one line of the plan file."""

    def __init__(self, lineno: int, message: str):
        super().__init__(f"line {lineno}: {message}")
        self.lineno = lineno
        self.message = message


@dataclass
class Tile:
    name: str
    width: Decimal  # mm
    height: Decimal  # mm
    lineno: int


@dataclass
class Room:
    name: str
    width: Decimal  # mm
    height: Decimal  # mm
    lineno: int


@dataclass
class Plan:
    tile: Tile | None = None
    grout: Decimal = Decimal("0")  # mm
    waste: Decimal = Decimal("10")  # percent
    bond: str = "stack"
    box: tuple[int, Decimal] | None = None  # (tiles per box, price per box)
    rooms: list[Room] = field(default_factory=list)
    lines: dict[str, int] = field(default_factory=dict)  # directive -> line number


@dataclass
class RoomResult:
    name: str
    width: Decimal
    height: Decimal
    area: Decimal  # mm2
    tiles: int  # tiles touching the room, including cut tiles
    full: int  # tiles lying wholly inside the room
    cut: int  # tiles that overhang or are trimmed by the room edge


@dataclass
class Report:
    tile: Tile
    grout: Decimal
    waste: Decimal
    bond: str
    box: tuple[int, Decimal] | None
    rooms: list[RoomResult]
    tiles_needed: int
    tiles_to_order: int
    boxes: int | None = None
    leftover: int | None = None
    cost: Decimal | None = None


@dataclass
class Finding:
    lineno: int  # 0 when the finding is about the plan as a whole
    message: str


# ---------------------------------------------------------------- parsing


def _decimal(text: str, lineno: int, what: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ParseError(lineno, f"{what} must be a number, got '{text}'") from None
    if not value.is_finite():
        raise ParseError(lineno, f"{what} must be a finite number, got '{text}'")
    return value


def _dims(token: str, trailing: str | None, lineno: int) -> tuple[Decimal, Decimal]:
    """Turn ``3.2mx250`` plus an optional trailing unit into millimetre sizes."""
    match = DIM_RE.fullmatch(token)
    if not match:
        raise ParseError(lineno, f"bad size '{token}', expected WxH such as 320x250 or 3.2mx2.5m")
    value_w, unit_w, value_h, unit_h = match.groups()
    if trailing is not None and trailing not in UNITS:
        raise ParseError(lineno, f"unknown unit '{trailing}' (use mm, cm, m, in or ft)")
    default = trailing or "mm"
    unit_w = unit_w or default
    unit_h = unit_h or default
    for unit in (unit_w, unit_h):
        if unit not in UNITS:
            raise ParseError(lineno, f"unknown unit '{unit}' (use mm, cm, m, in or ft)")
    width = Decimal(value_w) * UNITS[unit_w]
    height = Decimal(value_h) * UNITS[unit_h]
    if width <= 0 or height <= 0:
        raise ParseError(lineno, "sizes must be greater than zero")
    return width, height


def _name_and_dims(args: list[str], lineno: int, usage: str) -> tuple[str, Decimal, Decimal]:
    for index, token in enumerate(args):
        if DIM_RE.fullmatch(token.lower()):
            break
    else:
        raise ParseError(lineno, f"expected {usage}")
    if index == 0:
        raise ParseError(lineno, f"missing name before the size; expected {usage}")
    extra = args[index + 1:]
    if len(extra) > 1:
        raise ParseError(lineno, f"unexpected text after the size: '{' '.join(extra[1:])}'")
    trailing = extra[0].lower() if extra else None
    width, height = _dims(args[index].lower(), trailing, lineno)
    return " ".join(args[:index]), width, height


def _parse_tile(plan: Plan, args: list[str], lineno: int) -> None:
    name, width, height = _name_and_dims(args, lineno, "tile NAME WxH [UNIT]")
    plan.tile = Tile(name, width, height, lineno)


def _parse_room(plan: Plan, args: list[str], lineno: int) -> None:
    name, width, height = _name_and_dims(args, lineno, "room NAME WxH [UNIT]")
    plan.rooms.append(Room(name, width, height, lineno))


def _parse_grout(plan: Plan, args: list[str], lineno: int) -> None:
    text = "".join(args).lower()
    match = LEN_RE.fullmatch(text) if text else None
    if not match:
        raise ParseError(lineno, "expected grout WIDTH [UNIT], e.g. 'grout 3 mm'")
    value, unit = match.group(1), match.group(2) or "mm"
    if unit not in UNITS:
        raise ParseError(lineno, f"unknown unit '{unit}' (use mm, cm, m, in or ft)")
    plan.grout = Decimal(value) * UNITS[unit]


def _parse_waste(plan: Plan, args: list[str], lineno: int) -> None:
    text = "".join(args)
    if text.endswith("%"):
        text = text[:-1]
    value = _decimal(text, lineno, "waste")
    if not 0 <= value <= 100:
        raise ParseError(lineno, "waste must be between 0 and 100 percent")
    plan.waste = value


def _parse_bond(plan: Plan, args: list[str], lineno: int) -> None:
    if len(args) != 1 or args[0].lower() not in BONDS:
        raise ParseError(lineno, "expected 'bond stack' or 'bond half'")
    plan.bond = args[0].lower()


def _parse_box(plan: Plan, args: list[str], lineno: int) -> None:
    if len(args) != 2:
        raise ParseError(lineno, "expected box COUNT PRICE, e.g. 'box 10 29.90'")
    if not args[0].isdigit() or int(args[0]) == 0:
        raise ParseError(lineno, f"tiles per box must be a whole number above zero, got '{args[0]}'")
    price = _decimal(args[1], lineno, "box price")
    if price < 0:
        raise ParseError(lineno, "box price cannot be negative")
    plan.box = (int(args[0]), price)


DIRECTIVES = {
    "tile": _parse_tile,
    "room": _parse_room,
    "grout": _parse_grout,
    "waste": _parse_waste,
    "bond": _parse_bond,
    "box": _parse_box,
}


def parse_plan(text: str) -> Plan:
    """Parse plan text. Raises ParseError with the offending line number."""
    plan = Plan()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        keyword, *args = line.split()
        keyword = keyword.lower()
        if keyword not in DIRECTIVES:
            expected = ", ".join(DIRECTIVES)
            raise ParseError(lineno, f"unknown directive '{keyword}' (expected one of: {expected})")
        if keyword != "room":
            if keyword in plan.lines:
                raise ParseError(lineno, f"'{keyword}' is already set on line {plan.lines[keyword]}")
            plan.lines[keyword] = lineno
        DIRECTIVES[keyword](plan, args, lineno)
    return plan


def load_plan(path: str) -> Plan:
    return parse_plan(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- layout maths


def _span(length: Decimal, size: Decimal, pitch: Decimal, offset: Decimal) -> tuple[int, int, int, int]:
    """Index ranges for tiles along one axis.

    Tile ``i`` covers ``[offset + i*pitch, offset + i*pitch + size]``. Returns
    ``(first, last, first_full, last_full)`` as inclusive index ranges. A range
    is empty when its last index is below its first.
    """
    first = math.floor((-size - offset) / pitch) + 1
    last = math.ceil((length - offset) / pitch) - 1
    first_full = math.ceil(-offset / pitch)
    last_full = math.floor((length - size - offset) / pitch)
    return first, last, first_full, last_full


def layout_counts(width: Decimal, height: Decimal, tile_w: Decimal, tile_h: Decimal,
                  grout: Decimal = Decimal("0"), bond: str = "stack") -> tuple[int, int]:
    """Return ``(tiles touching the area, tiles lying wholly inside it)``."""
    pitch_x = tile_w + grout
    pitch_y = tile_h + grout
    row_first, row_last, row_full_first, row_full_last = _span(height, tile_h, pitch_y, Decimal(0))
    total = 0
    full = 0
    for row in range(row_first, row_last + 1):
        # Half bond shifts every other course by half a tile pitch.
        offset = -pitch_x / 2 if bond == "half" and row % 2 else Decimal(0)
        col_first, col_last, col_full_first, col_full_last = _span(width, tile_w, pitch_x, offset)
        total += max(0, col_last - col_first + 1)
        if row_full_first <= row <= row_full_last:
            full += max(0, col_full_last - col_full_first + 1)
    return total, full


def _require_tile(plan: Plan) -> Tile:
    if plan.tile is None:
        raise PlanError("missing 'tile' directive (e.g. 'tile Oak 200x1200 mm')")
    return plan.tile


def build_report(plan: Plan) -> Report:
    tile = _require_tile(plan)
    if not plan.rooms:
        raise PlanError("no 'room' lines found")
    rooms = []
    for room in plan.rooms:
        total, full = layout_counts(room.width, room.height, tile.width, tile.height,
                                    plan.grout, plan.bond)
        rooms.append(RoomResult(room.name, room.width, room.height, room.width * room.height,
                                total, full, total - full))
    needed = sum(room.tiles for room in rooms)
    to_order = math.ceil(Decimal(needed) * (100 + plan.waste) / 100)
    report = Report(tile, plan.grout, plan.waste, plan.bond, plan.box, rooms, needed, to_order)
    if plan.box is not None:
        per_box, price = plan.box
        report.boxes = -(-to_order // per_box)
        report.leftover = report.boxes * per_box - to_order
        report.cost = (report.boxes * price).quantize(CENT, ROUND_HALF_UP)
    return report


def lint(plan: Plan) -> list[Finding]:
    """Warnings about a plan that parses fine but may be wrong or costly."""
    tile = _require_tile(plan)
    findings = []
    first_seen: dict[str, int] = {}
    for room in plan.rooms:
        key = room.name.lower()
        if key in first_seen:
            findings.append(Finding(room.lineno, f"duplicate room name '{room.name}' "
                                                 f"(first on line {first_seen[key]})"))
        else:
            first_seen[key] = room.lineno
        # Tiles are laid with their width along the room's width and never rotated.
        if tile.width > room.width or tile.height > room.height:
            findings.append(Finding(room.lineno, f"tile is larger than room '{room.name}' "
                                                 "so every tile will be cut; swap the room's WxH "
                                                 "if the tiles should run the other way"))
    if plan.waste < 5:
        findings.append(Finding(plan.lines.get("waste", 0),
                                "waste under 5% leaves little margin for cut tiles"))
    elif plan.waste > 15:
        findings.append(Finding(plan.lines.get("waste", 0),
                                "waste above 15% is unusually high"))
    if plan.grout > 10:
        findings.append(Finding(plan.lines.get("grout", 0),
                                "grout wider than 10 mm is unusual"))
    if plan.box is None:
        findings.append(Finding(0, "no 'box' line, so boxes and cost are not calculated"))
    return findings


# ---------------------------------------------------------------- output


def _num(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _area(mm2: Decimal, unit: str) -> Decimal:
    divisor = MM2_PER_M2 if unit == "m2" else MM2_PER_FT2
    return (mm2 / divisor).quantize(CENT, ROUND_HALF_UP)


def format_report(report: Report, area_unit: str = "m2") -> str:
    tile = report.tile
    label = f"Area {area_unit}"
    lines = [
        f"Tile   {tile.name}  {_num(tile.width)} x {_num(tile.height)} mm",
        f"Setup  grout {_num(report.grout)} mm, {report.bond} bond, waste {_num(report.waste)}%",
        "",
        f"{'Room':<16}{'Size (mm)':<20}{label:>10}{'Tiles':>8}{'Full':>7}{'Cut':>7}",
    ]
    total_area = Decimal("0")
    for room in report.rooms:
        size = f"{_num(room.width)} x {_num(room.height)}"
        total_area += room.area
        lines.append(f"{room.name:<16}{size:<20}{_area(room.area, area_unit):>10}"
                     f"{room.tiles:>8}{room.full:>7}{room.cut:>7}")
    lines.append(f"{'Total':<16}{'':<20}{_area(total_area, area_unit):>10}"
                 f"{report.tiles_needed:>8}"
                 f"{sum(r.full for r in report.rooms):>7}"
                 f"{sum(r.cut for r in report.rooms):>7}")
    lines.append("")
    lines.append(f"Tiles needed: {report.tiles_needed}")
    lines.append(f"Order:        {report.tiles_to_order} tiles (includes {_num(report.waste)}% waste)")
    if report.boxes is not None:
        per_box = report.box[0]
        lines.append(f"Boxes:        {report.boxes} x {per_box} tiles, "
                     f"{report.leftover} left over, cost {report.cost:.2f}")
    return "\n".join(lines)


def report_to_dict(report: Report, area_unit: str = "m2") -> dict:
    return {
        "tile": {"name": report.tile.name, "width_mm": _num(report.tile.width),
                 "height_mm": _num(report.tile.height)},
        "grout_mm": _num(report.grout),
        "bond": report.bond,
        "waste_percent": _num(report.waste),
        "area_unit": area_unit,
        "rooms": [
            {
                "name": room.name,
                "width_mm": _num(room.width),
                "height_mm": _num(room.height),
                "area": str(_area(room.area, area_unit)),
                "tiles": room.tiles,
                "full": room.full,
                "cut": room.cut,
            }
            for room in report.rooms
        ],
        "tiles_needed": report.tiles_needed,
        "tiles_to_order": report.tiles_to_order,
        "boxes": report.boxes,
        "leftover": report.leftover,
        "cost": None if report.cost is None else str(report.cost),
    }


def render_layout(plan: Plan, room_name: str, max_width: int = 60) -> str:
    """ASCII preview of one room: ``#`` is tile body, ``.`` is grout or gap.

    The preview is approximate. Each character samples the centre of its cell,
    so cut tiles at the room edge may look slightly different from the counts.
    """
    if max_width < 1:
        raise PlanError("--max-width must be at least 1")
    tile = _require_tile(plan)
    matches = [room for room in plan.rooms if room.name.lower() == room_name.lower()]
    if not matches:
        names = ", ".join(room.name for room in plan.rooms) or "none"
        raise PlanError(f"no room named '{room_name}' (rooms: {names})")
    room = matches[0]
    pitch_x = tile.width + plan.grout
    pitch_y = tile.height + plan.grout
    cell_x = Decimal(math.ceil(room.width / max_width))
    cell_y = cell_x * 2  # terminal cells are roughly twice as tall as wide
    cols = math.ceil(room.width / cell_x)
    rows = math.ceil(room.height / cell_y)
    lines = [f"{room.name}: {_num(room.width)} x {_num(room.height)} mm, "
             f"1 column = {_num(cell_x)} mm, 1 row = {_num(cell_y)} mm  (# tile, . grout)"]
    for row in range(rows):
        y = (row + HALF) * cell_y
        tile_row = math.floor(y / pitch_y)
        local_y = y - tile_row * pitch_y
        offset = -pitch_x / 2 if plan.bond == "half" and tile_row % 2 else Decimal(0)
        cells = []
        for col in range(cols):
            x = (col + HALF) * cell_x
            tile_col = math.floor((x - offset) / pitch_x)
            local_x = x - offset - tile_col * pitch_x
            in_tile = local_x < tile.width and local_y < tile.height
            cells.append("#" if in_tile else ".")
        lines.append("".join(cells))
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tilecount",
                                     description="Offline tile quantity and cost calculator.")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="lint a plan file")
    check.add_argument("plan")
    check.add_argument("--strict", action="store_true", help="exit 1 when there are warnings")

    report = commands.add_parser("report", help="tile counts, waste, boxes and cost")
    report.add_argument("plan")
    report.add_argument("--json", action="store_true", help="print JSON instead of a table")
    report.add_argument("--area-unit", choices=["m2", "ft2"], default="m2")

    layout = commands.add_parser("layout", help="ASCII preview of one room's tile layout")
    layout.add_argument("plan")
    layout.add_argument("room")
    layout.add_argument("--max-width", type=int, default=60, help="characters across (default 60)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = load_plan(args.plan)
        if args.command == "check":
            findings = lint(plan)
            if not findings:
                print(f"{args.plan}: ok ({len(plan.rooms)} rooms)")
            for finding in findings:
                where = f"{args.plan}:{finding.lineno}" if finding.lineno else args.plan
                print(f"{where}: warning: {finding.message}")
            return 1 if args.strict and findings else 0
        if args.command == "report":
            result = build_report(plan)
            if args.json:
                print(json.dumps(report_to_dict(result, args.area_unit), indent=2))
            else:
                print(format_report(result, args.area_unit))
            return 0
        print(render_layout(plan, args.room, args.max_width))
        return 0
    except ParseError as error:
        print(f"{args.plan}:{error.lineno}: error: {error.message}", file=sys.stderr)
        return 2
    except PlanError as error:
        print(f"{args.plan}: error: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"tilecount: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
