"""编织引擎单元测试。"""

import pytest

from app.engine import (
    MAX_EXPANDED_LEAVES,
    ExpansionLimitError,
    ParseError,
    analyze_plan,
    analyze_row,
    count_leaves,
    expand_tokens,
    parse,
)


# ---------------------------------------------------------------- 解析
def test_parse_basic_leaf_consume_produce():
    nodes = parse("k5")
    assert len(nodes) == 1
    assert nodes[0].consumed == 5 and nodes[0].produced == 5

    nodes = parse("inc3")
    assert nodes[0].consumed == 1 and nodes[0].produced == 3

    nodes = parse("dec4")
    assert nodes[0].consumed == 4 and nodes[0].produced == 1


def test_parse_sequence_and_whitespace():
    nodes = parse("  k2 inc3   dec2 ")
    assert [n.type for n in nodes] == ["k", "inc", "dec"]
    assert [n.n for n in nodes] == [2, 3, 2]


def test_parse_repeat_flat():
    nodes = parse("[k2 inc1]*3")
    assert len(nodes) == 1 and nodes[0].type == "repeat" and nodes[0].n == 3
    # 一轮消耗 2+1=3，产出 2+1=3；重复三轮
    assert nodes[0].consumed == 9
    assert nodes[0].produced == 9


def test_parse_nested_repeat():
    nodes = parse("[[k2]*2 inc1]*3")
    root = nodes[0]
    assert root.type == "repeat" and root.n == 3
    assert [c.type for c in root.children] == ["repeat", "inc"]
    # 内层 [k2]*2 一轮消耗4，加 inc1 一轮消耗5、产出5，重复3轮
    assert root.consumed == 15
    assert root.produced == 15
    assert expand_tokens(nodes) == ["k2", "k2", "inc1"] * 3


def test_repeat_without_times_fails():
    with pytest.raises(ParseError) as ei:
        parse("[k2]*")
    assert "正整数" in ei.value.message


def test_empty_brackets_fails():
    with pytest.raises(ParseError) as ei:
        parse("[  ]*3")
    assert "为空" in ei.value.message


def test_unclosed_bracket_fails():
    with pytest.raises(ParseError) as ei:
        parse("k2 [k3*2")
    assert ei.value.pos == len("k2 [k3*2")


def test_bracket_without_repeat_fails():
    with pytest.raises(ParseError) as ei:
        parse("[k2]")
    assert "*M" in ei.value.message


def test_extra_closing_bracket_fails():
    with pytest.raises(ParseError) as ei:
        parse("k2]")
    assert ei.value.pos == 2


def test_missing_number_fails():
    with pytest.raises(ParseError) as ei:
        parse("k2 inc")
    assert ei.value.pos == 6  # 行尾，'inc' 之后


def test_zero_number_fails():
    with pytest.raises(ParseError):
        parse("k0")
    with pytest.raises(ParseError):
        parse("[k1]*0")


def test_unknown_keyword_fails():
    with pytest.raises(ParseError) as ei:
        parse("x3")
    assert ei.value.pos == 0


def test_empty_row_fails():
    with pytest.raises(ParseError) as ei:
        parse("   ")
    assert ei.value.pos == 0


def test_source_spans_nested():
    nodes = parse("[k2]*1")
    rep = nodes[0]
    leaf = rep.children[0]
    assert rep.start == 0 and rep.end == 6
    assert leaf.start == 1 and leaf.end == 3

    nodes = parse("[[k2]*2]*1")
    inner = nodes[0].children[0]
    leaf = inner.children[0]
    assert leaf.start == 2 and leaf.end == 4


def test_expansion_limit():
    with pytest.raises(ExpansionLimitError):
        count_leaves(parse(f"[k1]*{MAX_EXPANDED_LEAVES + 1}"))


# ---------------------------------------------------------------- 逐行审校
def test_plan_ok_chain():
    results = analyze_plan(10, ["k10", "inc5 k5", "dec2 k8"])
    # 行1: 10 -> 10；行2: 消耗 1+5=6 产 5+5=10（未消耗4）；行3: 消耗 2+8=10 产 1+8=9
    assert [r.status for r in results] == ["ok", "unconsumed", "ok"]
    assert results[0].remaining == 10
    assert results[1].available == 10 and results[1].produced == 10
    assert results[1].unconsumed == 4
    assert results[2].available == 10 and results[2].remaining == 9


def test_shortage_locates_first_failing_instruction():
    # 可用 6 针：k2（剩4）、inc1（剩3）、dec4 需要4 针 -> 短缺 1
    rr = analyze_row(0, "k2 inc1 dec4", 6, False)
    assert rr.status == "shortage"
    assert rr.failing_instruction == "dec4"
    assert rr.failing_start == 8 and rr.failing_end == 12
    assert rr.shortage == 1
    assert rr.consumed == 3  # 只有前两条执行成功


def test_shortage_inside_repeat_points_to_source_leaf():
    # 重复块 [k3 dec2] 每轮消耗5；可用7：第一轮后剩2，第二轮 dec2 刚好...
    # k3 剩 -1 -> 第二轮的 k3 失败，短缺1；源位置指向 k3
    rr = analyze_row(0, "[k3 dec2]*2", 7, False)
    assert rr.status == "shortage"
    assert rr.failing_instruction == "k3"
    assert rr.failing_start == 1 and rr.failing_end == 3
    assert rr.shortage == 1
    assert rr.consumed == 5


def test_syntax_error_blocks_following_rows():
    results = analyze_plan(8, ["k8", "inc", "k2"])
    assert results[0].status == "ok"
    assert results[1].status == "error"
    assert results[1].error_pos == 3  # 'inc' 之后行尾
    assert results[2].status == "blocked"


def test_shortage_blocks_following_rows():
    results = analyze_plan(4, ["dec5", "k1"])
    assert results[0].status == "shortage"
    assert results[1].status == "blocked"


def test_exact_consumption_is_ok():
    rr = analyze_row(0, "[inc1 k1]*3", 6, False)  # 每轮消耗2
    assert rr.status == "ok"
    assert rr.consumed == 6 and rr.produced == 6 and rr.remaining == 6


def test_preview_truncated():
    rr = analyze_row(0, "k1 " * 300, 300, False)
    assert rr.status == "ok"
    assert rr.expanded_count == 300
    assert rr.expanded_truncated is True
    assert len(rr.expanded_preview) == 200


def test_to_dict_keys():
    rr = analyze_row(0, "k1", 1, False)
    d = rr.to_dict()
    assert d["status"] == "ok" and d["remaining"] == 1
