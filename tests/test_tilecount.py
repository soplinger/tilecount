import json
from decimal import Decimal
from pathlib import Path

import pytest

import tilecount as tc

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = str(ROOT / "examples" / "kitchen.tiles")
BROKEN = str(ROOT / "tests" / "fixtures" / "broken.tiles")
WARNINGS = str(ROOT / "tests" / "fixtures" / "warnings.tiles")

BASE = """\
tile Tile300 300x300 mm
grout 3 mm
waste 10
box 10 29.90
room Big 3200x2500 mm
"""


def test_units_and_decimal_sizes_convert_to_mm():
    plan = tc.parse_plan("tile T 200x1200\nroom A 3.2mx2.5m\nroom B 3inx2ft\n")
    assert (plan.rooms[0].width, plan.rooms[0].height) == (Decimal("3200"), Decimal("2500"))
    assert plan.rooms[1].width == Decimal("3") * Decimal("25.4")
    assert plan.rooms[1].height == Decimal("2") * Decimal("304.8")


def test_suffix_overrides_trailing_unit_and_trailing_applies_to_bare_side():
    plan = tc.parse_plan("tile T 200x1200\nroom A 3mx250 cm\nroom B 3mx250cm\n")
    assert (plan.rooms[0].width, plan.rooms[0].height) == (Decimal("3000"), Decimal("2500"))
    assert (plan.rooms[1].width, plan.rooms[1].height) == (Decimal("3000"), Decimal("2500"))


def test_comments_blank_lines_and_crlf_are_ignored():
    text = "# plan\r\n\r\ntile Oak 200x1200 # the planks\r\n\r\nroom Hall 2400x900 # trailing\r\n"
    plan = tc.parse_plan(text)
    assert plan.tile.name == "Oak"
    assert len(plan.rooms) == 1
    assert plan.rooms[0].lineno == 5


def test_parse_errors_carry_the_line_number():
    with pytest.raises(tc.ParseError) as error:
        tc.parse_plan("tile Oak 200x1200\nroom Hall 2400x900\nroom Study 3xq\n")
    assert error.value.lineno == 3


def test_bad_directives_duplicates_and_zero_sizes_are_reported():
    with pytest.raises(tc.ParseError, match="unknown directive 'colour'") as unknown:
        tc.parse_plan("colour red\n")
    assert unknown.value.lineno == 1
    with pytest.raises(tc.ParseError, match="already set on line 1"):
        tc.parse_plan("tile A 200x200\ntile B 300x300\n")
    with pytest.raises(tc.ParseError, match="greater than zero"):
        tc.parse_plan("tile A 0x200\n")
    with pytest.raises(tc.ParseError, match="unknown unit 'yd'"):
        tc.parse_plan("tile A 200x200\nroom A 3x2yd\n")


def test_grout_and_waste_accept_several_spellings():
    plan = tc.parse_plan("grout 3mm\nwaste 12.5%\n")
    assert plan.grout == Decimal("3")
    assert plan.waste == Decimal("12.5")
    assert tc.parse_plan("grout 0.5 cm\n").grout == Decimal("5")


def test_stack_layout_counts_cut_and_full_tiles():
    # 300 mm tiles with 3 mm grout: 11 columns and 9 rows touch a 3200x2500 room.
    assert tc.layout_counts(Decimal(3200), Decimal(2500), Decimal(300), Decimal(300),
                            Decimal(3), "stack") == (99, 80)


def test_half_bond_shifts_alternate_courses():
    # Two courses: the even course has 11 tiles, the half-shifted odd course has 12.
    assert tc.layout_counts(Decimal(3200), Decimal(606), Decimal(300), Decimal(300),
                            Decimal(3), "half") == (23, 20)


def test_report_applies_waste_then_rounds_up_to_boxes():
    report = tc.build_report(tc.parse_plan(BASE))
    assert report.tiles_needed == 99
    assert report.tiles_to_order == 109  # ceil(99 * 1.10)
    assert report.boxes == 11
    assert report.leftover == 1
    assert report.cost == Decimal("328.90")


def test_area_units_in_table():
    plan = tc.parse_plan("tile T 300x300\nroom Den 3000x2000\n")
    report = tc.build_report(plan)
    assert tc.format_report(report, "m2").count("6.00") == 2  # room row and total row
    assert "64.58" in tc.format_report(report, "ft2")


def test_lint_flags_oversized_tiles_duplicates_waste_and_missing_box():
    findings = tc.lint(tc.load_plan(WARNINGS))
    messages = [finding.message for finding in findings]
    assert any("larger than room 'Hall'" in message for message in messages)
    assert any("duplicate room name 'Hall'" in message for message in messages)
    assert any("waste above 15%" in message for message in messages)
    assert any("no 'box' line" in message for message in messages)


def test_missing_tile_and_missing_rooms_are_plan_errors():
    with pytest.raises(tc.PlanError, match="missing 'tile'"):
        tc.build_report(tc.parse_plan("room A 1000x1000\n"))
    with pytest.raises(tc.PlanError, match="no 'room' lines"):
        tc.build_report(tc.parse_plan("tile T 200x200\n"))


def test_layout_preview_marks_tiles_and_grout():
    plan = tc.parse_plan("tile T 300x300\nroom Strip 600x300\n")
    preview = tc.render_layout(plan, "strip", max_width=2)
    assert preview.splitlines()[1] == "##"
    with pytest.raises(tc.PlanError, match="no room named 'Attic'"):
        tc.render_layout(plan, "Attic")


def test_bathroom_example_matches_readme_numbers():
    report = tc.build_report(tc.load_plan(str(ROOT / "examples" / "bathroom.tiles")))
    room = report.rooms[0]
    assert (room.tiles, room.full, room.cut) == (99, 80, 19)
    assert report.tiles_to_order == 109
    assert (report.boxes, report.leftover) == (11, 1)
    assert report.cost == Decimal("328.90")


def test_report_json_matches_table_totals():
    report = tc.build_report(tc.load_plan(EXAMPLE))
    data = tc.report_to_dict(report)
    assert data["tiles_to_order"] >= data["tiles_needed"]
    assert data["boxes"] * 10 >= data["tiles_to_order"]
    assert sum(room["tiles"] for room in data["rooms"]) == data["tiles_needed"]
    assert json.loads(json.dumps(data))["cost"] == data["cost"]


def test_cli_report_prints_table_and_json(capsys):
    assert tc.main(["report", EXAMPLE]) == 0
    assert "Order:" in capsys.readouterr().out
    assert tc.main(["report", "--json", "--area-unit", "ft2", EXAMPLE]) == 0
    assert json.loads(capsys.readouterr().out)["area_unit"] == "ft2"


def test_cli_check_and_strict_exit_codes(capsys):
    assert tc.main(["check", EXAMPLE]) == 0
    assert "ok (3 rooms)" in capsys.readouterr().out
    assert tc.main(["check", "--strict", WARNINGS]) == 1
    assert tc.main(["check", WARNINGS]) == 0


def test_cli_errors_go_to_stderr_with_line_number(capsys):
    assert tc.main(["report", BROKEN]) == 2
    assert f"{BROKEN}:3: error:" in capsys.readouterr().err


def test_cli_layout_command(capsys):
    assert tc.main(["layout", EXAMPLE, "Hall", "--max-width", "20"]) == 0
    output = capsys.readouterr().out.splitlines()
    assert output[0].startswith("Hall: 900 x 2400 mm")
    assert len(output) > 1
    assert tc.main(["layout", EXAMPLE, "Attic"]) == 2
