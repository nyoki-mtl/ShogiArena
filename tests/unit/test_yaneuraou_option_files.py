from shogiarena._core.shared.kernel.yaneuraou_option_files import (
    parse_yaneuraou_option_line,
    parse_yaneuraou_option_lines,
)


def test_parse_name_equals_value_line_strips_comment() -> None:
    entry = parse_yaneuraou_option_line("Hash = 1024 // comment", line_no=7)

    assert entry is not None
    assert entry.name == "Hash"
    assert entry.value == "1024"
    assert entry.line_no == 7


def test_parse_bare_name_value_line() -> None:
    entry = parse_yaneuraou_option_line("EvalDir eval")

    assert entry is not None
    assert entry.name == "EvalDir"
    assert entry.value == "eval"


def test_parse_usi_style_option_line() -> None:
    entry = parse_yaneuraou_option_line("option name Tune.Param type spin default 1 min 0 max 10")

    assert entry is not None
    assert entry.name == "Tune.Param"
    assert entry.value == "1"


def test_parse_usi_style_option_name_with_spaces() -> None:
    entry = parse_yaneuraou_option_line("option name Multi Word Option type string default foo")

    assert entry is not None
    assert entry.name == "Multi Word Option"
    assert entry.value == "foo"


def test_parse_lines_ignores_blank_and_comment_only_lines() -> None:
    entries = parse_yaneuraou_option_lines(["", "// comment", "Threads 1"])

    assert len(entries) == 1
    assert entries[0].name == "Threads"
    assert entries[0].value == "1"
    assert entries[0].line_no == 3
