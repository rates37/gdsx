"""The Liberty layer expression parsing and the cell model to encapsulate/model it."""

from itertools import product
import pytest

from gdsx import liberty
from gdsx.functions import GENERIC_ALIASES, base_name, generic_name, lookup
from hand_functions import HAND_WRITTEN


def test_base_name_strips_library_and_drive():
    assert base_name("sky130_fd_sc_hd__nand2_2") == "nand2"
    assert base_name("sky130_fd_sc_hd__a21boi_2") == "a21boi"
    assert base_name("sky130_fd_sc_hd__clkbuf_16") == "clkbuf"
    assert base_name("sky130_fd_sc_hd__and4bb_2") == "and4bb"


# expression parsing


@pytest.mark.parametrize(
    "text,values,expected",
    [
        ("A", {"A": 1}, 1),
        ("!A", {"A": 1}, 0),
        ("A'", {"A": 1}, 0),  # trailing quote is Liberty's other negation
        ("A&B", {"A": 1, "B": 0}, 0),
        ("A B", {"A": 1, "B": 1}, 1),  # juxtaposition is AND
        ("A !B", {"A": 1, "B": 0}, 1),
        ("A*B", {"A": 1, "B": 1}, 1),
        ("A+B", {"A": 0, "B": 1}, 1),
        ("A|B", {"A": 0, "B": 0}, 0),
        ("A^B", {"A": 1, "B": 1}, 0),
        ("(A&B) | (!C)", {"A": 0, "B": 0, "C": 0}, 1),
        ("!(A&B)", {"A": 1, "B": 1}, 0),
        ("1", {}, 1),
        ("0", {}, 0),
    ],
)
def test_expression_evaluation(text, values, expected):
    assert liberty.evaluate(liberty.parse(text), values) == expected


def test_and_binds_tighter_than_or():
    expr = liberty.parse("A & B | C")
    assert liberty.evaluate(expr, {"A": 0, "B": 1, "C": 1}) == 1
    assert liberty.evaluate(expr, {"A": 1, "B": 0, "C": 0}) == 0


def test_bad_expression_is_rejected():
    with pytest.raises(liberty.ExpressionError):
        liberty.parse("A &")
    with pytest.raises(liberty.ExpressionError):
        liberty.parse("(A & B")


def test_verilog_rendering_round_trips():
    for text in ("A&B|!C", "!(A^B)", "A'B'", "(A0&!S) | (A1&S)"):
        expr = liberty.parse(text)
        names = sorted(liberty.variables(expr))
        reparsed = liberty.parse(liberty.to_verilog(expr).replace("~", "!"))
        for combo in product((0, 1), repeat=len(names)):
            values = dict(zip(names, combo))
            assert liberty.evaluate(expr, values) == liberty.evaluate(reparsed, values)


# the parsed library tests. checked against the original (trusted) implementation


@pytest.mark.parametrize("name", sorted(HAND_WRITTEN))
def test_liberty_reproduces_the_hand_written_function(name):
    pins, output, fn = HAND_WRITTEN[name]
    cell = liberty.library()[name]
    assert set(cell.inputs) == set(pins), f"{name} pin names differ"
    assert output in cell.outputs

    for combo in product((0, 1), repeat=len(pins)):
        values = dict(zip(pins, combo))
        assert cell.evaluate(values)[output] == fn(*combo), (
            f"{name} differs at {values}"
        )


def test_every_cell_parses():
    lib = liberty.library()
    assert len(lib) > 100
    for cell in lib.values():
        for expr in cell.functions.values():
            liberty.to_verilog(expr)


def test_multi_output_cells_are_supported():
    fa = liberty.library()["fa"]
    assert set(fa.outputs) == {"SUM", "COUT"}
    for a, b, cin in product((0, 1), repeat=3):
        out = fa.evaluate({"A": a, "B": b, "CIN": cin})
        assert out["SUM"] == (a + b + cin) % 2
        assert out["COUT"] == int(a + b + cin >= 2)


def test_sequential_cells_expose_their_condition_expressions():
    dfrtp = liberty.library()["dfrtp"]
    assert dfrtp.is_sequential
    assert liberty.to_verilog(dfrtp.sequential.clocked_on) == "CLK"
    assert liberty.to_verilog(dfrtp.sequential.next_state) == "D"
    assert liberty.to_verilog(dfrtp.sequential.clear) == "~RESET_B"

    scan = liberty.library()["sdfxtp"]
    assert set(liberty.variables(scan.sequential.next_state)) == {"D", "SCD", "SCE"}


def test_cells_without_behaviour_are_reported_as_such():
    assert lookup("sky130_fd_sc_hd__decap_3") is None  # fill
    assert lookup("sky130_fd_sc_hd__einvp_1") is None  # tri-state
    assert lookup("sky130_fd_sc_hd__nand2_2") is not None


def test_generic_names_do_not_collide_across_different_behaviour():
    # Two cells may share a generic name only if they behave identically
    seen = {}
    for name, cell in liberty.library().items():
        if not cell.has_behaviour:
            continue
        generic = generic_name(name)
        if generic in seen:
            other = seen[generic]
            assert cell.inputs == other.inputs and cell.functions == other.functions, (
                f"{name} and {other.name} both map to {generic} but differ"
            )
        seen[generic] = cell


def test_aliases_all_refer_to_real_cells():
    for base in GENERIC_ALIASES:
        assert base in liberty.library(), f"alias for missing cell {base}"
