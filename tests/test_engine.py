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


# ---------------------------------------------------------------- 自适应重复解析
def test_parse_adaptive_node():
    nodes = parse("[k2 inc1]*~5")
    assert len(nodes) == 1
    node = nodes[0]
    assert node.type == "adaptive"
    assert node.reserve == 5
    assert node.n == 0  # 推导前次数未知
    assert node.op_pos == 9  # '*~' 起始列
    assert node.start == 0 and node.end == 12


def test_parse_adaptive_reserve_zero_allowed():
    nodes = parse("[k1]*~0")
    assert nodes[0].type == "adaptive" and nodes[0].reserve == 0


def test_parse_adaptive_with_nested_fixed_repeat():
    # 序列可包含固定次数嵌套块
    nodes = parse("[[k2]*2 inc1]*~3")
    root = nodes[0]
    assert root.type == "adaptive" and root.reserve == 3
    assert [c.type for c in root.children] == ["repeat", "inc"]


def test_parse_adaptive_missing_number_fails():
    with pytest.raises(ParseError) as ei:
        parse("[k2]*~")
    assert "非负整数" in ei.value.message
    assert ei.value.pos == 6  # 行尾，'*~' 之后


def test_parse_adaptive_nested_inside_repeat_fails():
    # 嵌套块只能用固定次数；错误位置指向运算符 '*~'
    with pytest.raises(ParseError) as ei:
        parse("[[k1]*~2]*3")
    assert "行内顶层" in ei.value.message
    assert ei.value.pos == 5


def test_parse_adaptive_nested_inside_adaptive_fails():
    with pytest.raises(ParseError) as ei:
        parse("[[k1]*~0]*~2")
    assert ei.value.pos == 5


def test_parse_adaptive_tilde_requires_digit():
    with pytest.raises(ParseError) as ei:
        parse("[k1]*~x")
    assert ei.value.pos == 6  # '*~' 之后


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
    assert d["adaptive_blocks"] == []


# ---------------------------------------------------------------- 自适应重复审校
def test_adaptive_derives_repeat_count():
    # 可用 20：每轮消耗 3，预留 5（k5），(20-5)/3 = 5 轮
    rr = analyze_row(0, "[k2 inc1]*~5 k5", 20, False)
    assert rr.status == "ok"
    assert rr.consumed == 20 and rr.produced == 20 and rr.remaining == 20
    assert rr.expanded_count == 11  # 5*2 + 1
    assert len(rr.adaptive_blocks) == 1
    blk = rr.adaptive_blocks[0]
    assert blk.reserve == 5 and blk.actual == 5
    assert blk.start == 0 and blk.end == 12 and blk.op_pos == 9
    assert rr.expanded_preview == ["k2", "inc1"] * 5 + ["k5"]


def test_adaptive_zero_repetitions():
    # 可用 4，预留 4 -> 实际重复 0 次，块不产出叶子，后续 k4 全部消耗
    rr = analyze_row(0, "[k2]*~4 k4", 4, False)
    assert rr.status == "ok"
    assert rr.adaptive_blocks[0].actual == 0
    assert rr.expanded_count == 1 and rr.expanded_preview == ["k4"]
    assert rr.consumed == 4


def test_adaptive_indivisible_is_error_at_operator():
    # (20-4)=16，每轮消耗 3，无法整除；错误位置在 '*~'（列 9）
    rr = analyze_row(0, "[k2 inc1]*~4 k4", 20, False)
    assert rr.status == "error"
    assert rr.error_pos == 9
    assert "无法整除" in rr.error
    assert rr.adaptive_blocks == []


def test_adaptive_reserve_exceeds_available_is_error():
    rr = analyze_row(0, "[k2]*~20", 10, False)
    assert rr.status == "error"
    assert rr.error_pos == 4
    assert "预留" in rr.error


def test_adaptive_with_nested_fixed_repeat():
    # 一轮消耗 2*2+1=5；可用 17 预留 2 => (17-2)/5 = 3 轮
    rr = analyze_row(0, "[[k2]*2 inc1]*~2 k2", 17, False)
    assert rr.status == "ok"
    assert rr.adaptive_blocks[0].actual == 3
    assert rr.consumed == 17
    assert rr.expanded_count == 10  # 3*3 + 1
    assert rr.expanded_preview == ["k2", "k2", "inc1"] * 3 + ["k2"]


def test_adaptive_multiple_blocks_in_row():
    # 可用 20：[k2]*~8 -> (20-8)/2=6 轮，剩 8；[k3]*~2 -> (8-2)/3=2 轮，剩 2
    rr = analyze_row(0, "[k2]*~8 [k3]*~2", 20, False)
    assert rr.status == "unconsumed"
    assert rr.unconsumed == 2
    assert [b.actual for b in rr.adaptive_blocks] == [6, 2]
    assert rr.expanded_preview == ["k2"] * 6 + ["k3"] * 2


def test_adaptive_expansion_limit_is_error_at_operator():
    # 可用 200000 全留给块、每轮 1 叶 -> 200000 次，超 100000 上限
    rr = analyze_row(0, "[k1]*~0", 200_000, False)
    assert rr.status == "error"
    assert rr.error_pos == 4
    assert "上限" in rr.error


def test_adaptive_shortage_after_block_locates_leaf():
    # 可用 20：(20-6)/2=7 轮后剩 6，k7 需要 7 针 -> 短缺 1
    rr = analyze_row(0, "[k2]*~6 k7", 20, False)
    assert rr.status == "shortage"
    assert rr.failing_instruction == "k7"
    assert rr.failing_start == 8 and rr.failing_end == 10
    assert rr.shortage == 1
    assert rr.consumed == 14
    # 短缺行仍返回已经执行的自适应块实际次数
    assert rr.adaptive_blocks[0].actual == 7


def test_adaptive_block_error_blocks_following_rows():
    results = analyze_plan(20, ["k20", "[k2 inc1]*~4", "k1"])
    assert results[0].status == "ok"
    assert results[1].status == "error"
    assert results[1].error_pos == 9  # 运算符 '*~' 位置
    assert results[2].status == "blocked"


def test_adaptive_chains_previous_row_output():
    # 行1：20 -> 16；行2：16 中 [k2]*~16（0 次）+ k16 -> 16；
    # 行3：[k2]*~4 用 16：(16-4)/2=6 轮后 k4，产出 16
    results = analyze_plan(20, ["[k3 dec2]*4", "[k2]*~16 k16", "[k2]*~4 k4"])
    assert [r.status for r in results] == ["ok", "ok", "ok"]
    assert results[0].remaining == 16
    assert results[1].available == 16
    assert results[2].available == 16
    assert results[2].consumed == 16
    assert [b.actual for b in results[1].adaptive_blocks] == [0]
    assert [b.actual for b in results[2].adaptive_blocks] == [6]
