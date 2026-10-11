import pytest

from hammer.vlsi import rtl_check

BODY = """module top(input a, input rst, input [1:0] s, output b, output reg c);
{p0}
  initial $display("sim only");
{p1}
  assign b = a;{p2}
  always @(*) case (s) {fc}
    2'd0: c = a;
    2'd1: c = ~a;
  endcase
endmodule
"""
OFF, ON = "  // synopsys translate_off", "  // synopsys translate_on"


@pytest.fixture
def digest(tmp_path):
    """Fingerprint top.v plus the given headers, with inc/ as the include dir."""
    try:
        rtl_check._checked_binary()
    except rtl_check.SlangNotFound as e:
        pytest.skip(f"needs slang {rtl_check.SLANG_VERSION}: {e}")
    (tmp_path / "inc").mkdir()

    def run(top: str, **headers: str) -> str:
        for name, text in headers.items():
            (tmp_path / "inc" / f"{name}.vh").write_text(text)
        (tmp_path / "top.v").write_text(top)
        return rtl_check.digest_units([str(tmp_path / "top.v")], include_dirs=[str(tmp_path / "inc")],
                                      top_module="top")[0]

    return run


@pytest.fixture
def fp(digest):
    return lambda p0="", p1="", p2="", fc="": digest(BODY.format(p0=p0, p1=p1, p2=p2, fc=fc))


def test_moving_translate_on_past_code_changes_the_fingerprint(fp) -> None:
    assert fp(OFF, ON) != fp(OFF, "", "\n" + ON)


def test_a_trailing_pragma_differs_from_one_on_the_line_before(fp) -> None:
    assert fp(OFF, ON + "\n  // synopsys translate_off", " // synopsys translate_on") != \
        fp(OFF, ON + "\n  // synopsys translate_off\n  // synopsys translate_on")


def test_adding_full_case_changes_the_fingerprint(fp) -> None:
    assert fp() != fp(fc="// synopsys full_case")


def test_pragma_arguments_count(fp) -> None:
    assert fp(fc='// synopsys sync_set_reset "rst"') != fp(fc='// synopsys sync_set_reset "rst_n"')


def test_whitespace_and_other_comments_do_not(fp) -> None:
    assert fp(OFF, ON) == fp("\n  /* a note\n     spanning lines */\n" + OFF, ON + "\n\n")


def test_prose_comments_are_not_pragmas(fp) -> None:
    prose = "  // synthesis of this block is slow\n  /* pragma: keep it simple */"
    assert fp(prose) == fp()
    assert rtl_check._pragmas(BODY.format(p0=prose, p1="", p2="", fc="")) == []


@pytest.mark.parametrize("spelling", [
    "/* synopsys translate_off */", "// pragma translate_off", "//synopsys full_case parallel_case",
    "// synthesis translate_off", "// cadence synthesis off", "// ambit synthesis off",
])
def test_pragma_spellings_are_recognised(spelling) -> None:
    assert len(rtl_check._pragmas(f"module x; {spelling}\nendmodule\n")) == 1


def test_an_escaped_identifier_does_not_hide_a_pragma() -> None:
    assert [p[0] for p in rtl_check._pragmas('module x; wire \\a"b ; // synopsys translate_off\nendmodule\n')] == \
        ["translate_off"]


def test_pragmas_in_an_angle_include_count(digest) -> None:
    top = "`include <hdr.vh>\nmodule top(input a, output b); assign b = a; endmodule\n"
    assert digest(top, hdr="`define X 1\n") != digest(top, hdr="`define X 1\n// synopsys translate_off\n")


@pytest.mark.parametrize("include", ['`include "sim.vh"', "`include <sim.vh>"])
def test_pragmas_in_a_header_slang_skipped_do_not_count(digest, include) -> None:
    plain = digest("module top(input a, output b); assign b = a; endmodule\n", sim="")
    top = f"`ifndef SYNTHESIS\n{include}\n`endif\nmodule top(input a, output b); assign b = a; endmodule\n"
    assert digest(top, sim="// synopsys translate_off\n") == plain


def test_a_pragma_in_an_inactive_branch_does_not_count(digest) -> None:
    plain = digest("module top(input a, output b); assign b = a; endmodule\n")
    skipped = digest("module top(input a, output b);\n`ifndef SYNTHESIS\n  // synopsys translate_off\n`endif\n"
                     "  assign b = a;\nendmodule\n")
    assert skipped == plain


def test_code_moved_into_an_included_region_changes_the_fingerprint(digest) -> None:
    region = "module top(input a, output b);\n// synopsys translate_off\n`include \"chk.vh\"\n// synopsys translate_on\n"
    outside = digest(region + "assign b = a;\nendmodule\n", chk='initial $display("chk");\n')
    inside = digest(region + "endmodule\n", chk='initial $display("chk");\nassign b = a;\n')
    assert inside != outside


def test_moving_a_pragma_only_include_past_code_changes_the_fingerprint(digest) -> None:
    head = "module top(input a, output b);\n`include \"off.vh\"\n  initial $display(\"x\");\n"
    hdrs = {"off": "// synopsys translate_off\n", "on": "// synopsys translate_on\n"}
    before = digest(head + "`include \"on.vh\"\n  assign b = a;\nendmodule\n", **hdrs)
    after = digest(head + "  assign b = a;\n`include \"on.vh\"\nendmodule\n", **hdrs)
    assert before != after


def test_a_comment_marker_in_a_string_hides_no_header(digest) -> None:
    top = 'module top(input a, output b); initial $display("/*"); `include <hdr.vh> assign b = a; /* note */ endmodule\n'
    on_after = digest(top, hdr='// synopsys translate_off\ninitial $display("sim");\n// synopsys translate_on\n')
    on_before = digest(top, hdr='// synopsys translate_off\n// synopsys translate_on\ninitial $display("sim");\n')
    assert on_after != on_before


def test_pragmas_in_a_macro_named_header_count(digest) -> None:
    top = "`define HDR <hdr.vh>\nmodule top(input a, output b); `include `HDR assign b = a; endmodule\n"
    on_after = digest(top, hdr='// synopsys translate_off\ninitial $display("sim");\n// synopsys translate_on\n')
    on_before = digest(top, hdr='// synopsys translate_off\n// synopsys translate_on\ninitial $display("sim");\n')
    assert on_after != on_before
