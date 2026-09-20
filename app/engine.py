"""编织行指令解析与逐行审校引擎。

语法（指令之间可用空白分隔，空白会被忽略）：

    kN        消耗 N 针，产出 N 针
    incN      消耗 1 针，产出 N 针（N >= 1）
    decN      消耗 N 针，产出 1 针（N >= 1）
    [...]*M   将方括号内的指令序列整体重复 M 次，可嵌套（嵌套块只能用固定次数）
    [...]*~N  自适应重复：按顺序重复方括号内序列，直到恰好剩余 N 针供本行
              后续指令使用（N 为非负整数，序列内可含固定次数嵌套块）。
              自适应块只能出现在行内顶层，不能嵌套在任何方括号块内。

解析结果是一棵节点树（InstructionNode）；叶子为 k/inc/dec，repeat 节点持有
固定次数子序列，adaptive 节点持有预留针数，实际重复次数在“展开前”的有序
模拟中推导。每个节点记录其在源文本中的 [start, end) 区间，便于定位
“第一个无法执行的源指令”以及自适应块的运算符位置。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Literal, Optional

NodeType = Literal["k", "inc", "dec", "repeat", "adaptive"]

# 单次方案允许展开的叶子指令上限，防止 [[..]]*999999 之类的输入撑爆内存。
MAX_EXPANDED_LEAVES = 100_000


class ParseError(Exception):
    """语法错误。pos 为出错位置（相对该行的 0 基列号，可能等于行尾长度）。"""

    def __init__(self, message: str, pos: int):
        super().__init__(message)
        self.message = message
        self.pos = pos


class ExpansionLimitError(Exception):
    """展开后的叶子指令数量超过 MAX_EXPANDED_LEAVES。"""


class AdaptiveDeriveError(Exception):
    """自适应重复块在展开前推导重复次数失败（预留不足 / 无法整除 / 超展开上限）。

    pos 为运算符 '*~' 在源文本中的起始列。
    """

    def __init__(self, message: str, pos: int):
        super().__init__(message)
        self.message = message
        self.pos = pos


@dataclass
class InstructionNode:
    type: NodeType
    start: int  # 在源文本中的起始列（0 基，含）
    end: int  # 结束列（0 基，不含）
    n: int = 0  # k/inc/dec 的数字参数；repeat 的重复次数；adaptive 推导前为 0
    children: list["InstructionNode"] = field(default_factory=list)
    # adaptive 专用：运算符 '*~' 的起始列；'*~N' 中预留针数 N
    op_pos: Optional[int] = None
    reserve: int = 0

    @property
    def consumed(self) -> int:
        if self.type == "k":
            return self.n
        if self.type == "inc":
            return 1
        if self.type == "dec":
            return self.n
        # repeat：一次子序列的消耗 × 次数；adaptive 在推导前 n=0
        return sum(c.consumed for c in self.children) * self.n

    @property
    def produced(self) -> int:
        if self.type == "k":
            return self.n
        if self.type == "inc":
            return self.n
        if self.type == "dec":
            return 1
        return sum(c.produced for c in self.children) * self.n


# --------------------------------------------------------------------------- #
# 解析器（递归下降）
# --------------------------------------------------------------------------- #
class _Parser:
    def __init__(self, text: str):
        self.text = text

    def parse_sequence(self, end_char: Optional[str] = None,
                       allow_adaptive: bool = True) -> list[InstructionNode]:
        """解析一条指令序列；遇到 end_char（' ]'）时停下，且不消费该字符。

        allow_adaptive 为 False 时（方括号内部）不允许出现自适应重复
        ``*~N``：自适应块只能位于行内顶层。
        """
        nodes: list[InstructionNode] = []
        i = 0
        text = self.text
        while i < len(text):
            ch = text[i]
            if ch.isspace():
                i += 1
                continue
            if end_char is not None and ch == end_char:
                break
            if ch in "kdi":
                if ch == "i" and not text.startswith("inc", i):
                    raise ParseError(f"无法识别的指令 {text[i:i+4]!r}，应为 kN / incN / decN", i)
                if ch == "d" and not text.startswith("dec", i):
                    raise ParseError(f"无法识别的指令 {text[i:i+4]!r}，应为 kN / incN / decN", i)
                keyword = "inc" if ch == "i" else ("dec" if ch == "d" else "k")
                start = i
                i += len(keyword)
                num, i = self._read_number(i, keyword)
                nodes.append(InstructionNode(type=keyword, start=start, end=i, n=num))
            elif ch == "[":
                start = i
                i += 1
                inner_text, i = self._extract_bracket(i)
                inner_parser = _Parser(inner_text)
                try:
                    # 嵌套块（含其内部）只能使用固定次数 *M
                    children = inner_parser.parse_sequence(end_char=None,
                                                           allow_adaptive=False)
                except ParseError as exc:
                    # inner 中的列号是相对 inner_text 的，偏移为 '[' 之后的位置
                    raise ParseError(exc.message, exc.pos + start + 1) from exc
                # inner_text 相对当前 text 的列偏移 = '[' 之后的位置
                offset = start + 1
                for c in children:
                    c.start += offset
                    c.end += offset
                    self._shift_children(c, offset)
                i = self._skip_ws(i)
                if i >= len(text) or text[i] != "*":
                    raise ParseError("方括号序列后缺少 '*M' 重复说明", i)
                star_pos = i
                i += 1
                adaptive = False
                if i < len(text) and text[i] == "~":
                    if not allow_adaptive:
                        raise ParseError(
                            "自适应重复 '*~N' 只能用于行内顶层，不能嵌套在方括号块内",
                            star_pos,
                        )
                    adaptive = True
                    i += 1
                if adaptive:
                    times, i = self._read_number(i, "*~", star_pos=star_pos,
                                                 nonnegative=True)
                    node = InstructionNode(
                        type="adaptive", start=start, end=i, n=0,
                        children=children, op_pos=star_pos, reserve=times,
                    )
                else:
                    times, i = self._read_number(i, "*", star_pos=star_pos)
                    node = InstructionNode(type="repeat", start=start, end=i,
                                           n=times, children=children)
                nodes.append(node)
            elif ch == "]":
                raise ParseError("出现多余的 ']'，没有与之匹配的 '['", i)
            elif ch == "*":
                raise ParseError("'*' 只能用于方括号序列之后（如 [k2 inc1]*3）", i)
            else:
                raise ParseError(f"意外字符 {ch!r}，应为 k / inc / dec / '['", i)
        return nodes

    @staticmethod
    def _shift_children(node: InstructionNode, offset: int) -> None:
        if node.type == "repeat":
            for c in node.children:
                c.start += offset
                c.end += offset
                _Parser._shift_children(c, offset)

    def _extract_bracket(self, pos: int) -> tuple[str, int]:
        """从 pos（'[' 之后）开始提取到匹配 ']' 为止的内部文本。

        返回 (内部文本, ']' 之后的位置)。
        """
        depth = 1
        start = pos
        text = self.text
        while pos < len(text):
            ch = text[pos]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    inner = text[start:pos]
                    if not inner.strip():
                        raise ParseError("方括号内不允许为空", start)
                    return inner, pos + 1
            pos += 1
        raise ParseError("方括号没有闭合，缺少 ']'", len(text))

    def _skip_ws(self, pos: int) -> int:
        while pos < len(self.text) and self.text[pos].isspace():
            pos += 1
        return pos

    def _read_number(self, pos: int, what: str, star_pos: Optional[int] = None,
                     nonnegative: bool = False) -> tuple[int, int]:
        """读取一个整数。what 用于错误信息；返回 (数字, 数字之后的位置)。

        nonnegative 为 True 时允许 0（用于自适应块预留针数 *~N）。
        """
        text = self.text
        start = pos
        while pos < len(text) and text[pos].isdigit():
            pos += 1
        if pos == start:
            if what == "*":
                raise ParseError("'*' 后必须给出正整数重复次数", pos)
            if what == "*~":
                raise ParseError("'*~' 后必须给出非负整数预留针数（如 *~4）", pos)
            raise ParseError(f"{what} 之后必须给出正整数参数（如 {what}2）", pos)
        value = int(text[start:pos])
        if nonnegative:
            if value < 0:  # 纯数字串不可能为负，保留显式防御
                raise ParseError(f"{what} 的数字必须为非负整数，得到 {value}", start)
        elif value <= 0:
            # 纯数字串不会是 0 以下；0 会被解析成 0，显式拒绝。
            raise ParseError(f"{what} 的数字必须为正整数，得到 {value}", start)
        return value, pos


def parse(text: str) -> list[InstructionNode]:
    """解析一行指令，返回顶层节点列表；空行/纯空白视为语法错误。"""
    parser = _Parser(text)
    nodes = parser.parse_sequence()
    if not nodes:
        raise ParseError("该行没有指令", 0)
    return nodes


# --------------------------------------------------------------------------- #
# 展开与统计
# --------------------------------------------------------------------------- #
def iter_leaves(nodes: list[InstructionNode]) -> Iterator[InstructionNode]:
    """按实际执行顺序产出所有叶子指令（展开 repeat / 已推导的 adaptive）。

    adaptive 节点必须先通过 derive_adaptive 完成推导（n 为实际重复次数）。
    """
    for node in nodes:
        if node.type in ("repeat", "adaptive"):
            for _ in range(node.n):
                yield from iter_leaves(node.children)
        else:
            yield node


def _subtree_leaves(children: list[InstructionNode]) -> int:
    """统计一组子节点完全展开（含固定 repeat）后的叶子数。

    自适应块不会出现在任何方括号内部，因此 children 中不含 adaptive。
    """
    total = 0
    for node in children:
        if node.type == "repeat":
            total += _subtree_leaves(node.children) * node.n
        else:
            total += 1
    return total


def count_leaves(nodes: list[InstructionNode]) -> int:
    """统计展开后的叶子数量，超过上限时抛 ExpansionLimitError。

    adaptive 节点按推导后的实际次数计入（未推导时按 0 计，供调用方预检）。
    """
    total = 0
    for node in nodes:
        if node.type == "repeat":
            total += _subtree_leaves(node.children) * node.n
        elif node.type == "adaptive":
            total += _subtree_leaves(node.children) * node.n
        else:
            total += 1
        if total > MAX_EXPANDED_LEAVES:
            raise ExpansionLimitError(
                f"展开后的指令数超过上限 {MAX_EXPANDED_LEAVES}，请减少重复次数"
            )
    return total


def count_fixed_leaves(nodes: list[InstructionNode]) -> int:
    """统计不含自适应块的固定部分展开叶子数，超过上限时抛 ExpansionLimitError。"""
    total = 0
    for node in nodes:
        if node.type == "repeat":
            total += _subtree_leaves(node.children) * node.n
        elif node.type == "adaptive":
            continue
        else:
            total += 1
        if total > MAX_EXPANDED_LEAVES:
            raise ExpansionLimitError(
                f"展开后的指令数超过上限 {MAX_EXPANDED_LEAVES}，请减少重复次数"
            )
    return total


def expand_tokens(nodes: list[InstructionNode], limit: int = MAX_EXPANDED_LEAVES) -> list[str]:
    """展开为叶子指令文本列表（如 ['k2', 'inc3']），超限时抛 ExpansionLimitError。"""
    tokens: list[str] = []
    for leaf in iter_leaves(nodes):
        tokens.append(f"{leaf.type}{leaf.n}")
        if len(tokens) > limit:
            raise ExpansionLimitError(
                f"展开后的指令数超过上限 {MAX_EXPANDED_LEAVES}，请减少重复次数"
            )
    return tokens

# --------------------------------------------------------------------------- #
# 逐行审校
# --------------------------------------------------------------------------- #
@dataclass
class AdaptiveBlockInfo:
    """单个自适应重复块的推导结果。"""

    start: int  # 源文本中 '[' 的起始列（0 基，含）
    end: int  # 块结束列（0 基，不含）
    op_pos: int  # 运算符 '*~' 的起始列
    reserve: int  # 预留针数 N
    actual: int  # 推导出的实际重复次数

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "op_pos": self.op_pos,
            "reserve": self.reserve,
            "actual": self.actual,
        }


@dataclass
class RowResult:
    index: int
    source: str
    status: Literal["ok", "shortage", "unconsumed", "error", "blocked"]
    available: int = 0  # 进入本行时可用针数
    consumed: int = 0
    produced: int = 0
    remaining: int = 0  # 本行产出（= 下一行可用）
    expanded_count: int = 0
    expanded_preview: list[str] = field(default_factory=list)
    expanded_truncated: bool = False
    error: Optional[str] = None
    error_pos: Optional[int] = None  # 出错位置（0 基列）
    failing_instruction: Optional[str] = None  # 第一个无法执行的源指令文本
    failing_start: Optional[int] = None
    failing_end: Optional[int] = None
    shortage: Optional[int] = None  # 短缺针数（还差多少针才能执行）
    unconsumed: Optional[int] = None  # 本行未消耗的针数
    adaptive_blocks: list[AdaptiveBlockInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "source": self.source,
            "status": self.status,
            "available": self.available,
            "consumed": self.consumed,
            "produced": self.produced,
            "remaining": self.remaining,
            "expanded_count": self.expanded_count,
            "expanded_preview": self.expanded_preview,
            "expanded_truncated": self.expanded_truncated,
            "error": self.error,
            "error_pos": self.error_pos,
            "failing_instruction": self.failing_instruction,
            "failing_start": self.failing_start,
            "failing_end": self.failing_end,
            "shortage": self.shortage,
            "unconsumed": self.unconsumed,
            "adaptive_blocks": [b.to_dict() for b in self.adaptive_blocks],
        }


PREVIEW_LIMIT = 200


def _simulate_with_adaptive(
    nodes: list[InstructionNode],
    available: int,
) -> tuple[Optional[InstructionNode], int, int, list[AdaptiveBlockInfo]]:
    """在展开前做有序模拟：推导自适应块次数并定位第一个短缺的叶子。

    按源顺序执行节点；遇到 adaptive 块时根据当前剩余针数推导实际重复
    次数 M（要求恰好预留 reserve 针：剩余针数可被一轮消耗量整除，且
    展开量不超上限），写回 node.n。返回：

        (第一个无法执行的叶子, 成功执行叶子累计消耗, 最终剩余针数,
         已推导自适应块信息列表)

    推导失败时抛 AdaptiveDeriveError（pos 指向运算符 '*~'）。
    """
    remaining = available
    consumed_before = 0
    failing: Optional[InstructionNode] = None
    blocks: list[AdaptiveBlockInfo] = []
    leaf_budget = count_fixed_leaves(nodes)

    def run(seq: list[InstructionNode]) -> None:
        nonlocal remaining, consumed_before, failing, leaf_budget
        for node in seq:
            if failing is not None:
                return
            if node.type == "repeat":
                for _ in range(node.n):
                    if failing is not None:
                        return
                    run(node.children)
                continue
            if node.type == "adaptive":
                if remaining < node.reserve:
                    raise AdaptiveDeriveError(
                        f"自适应重复无法推导：当前剩余 {remaining} 针，"
                        f"不足以为 *~{node.reserve} 预留 {node.reserve} 针",
                        node.op_pos if node.op_pos is not None else node.start,
                    )
                usable = remaining - node.reserve
                once_consumed = sum(c.consumed for c in node.children)
                if usable % once_consumed != 0:
                    raise AdaptiveDeriveError(
                        f"自适应重复无法整除：可用 {usable} 针（剩余 {remaining} 针"
                        f"减去预留 {node.reserve} 针），每轮消耗 {once_consumed} 针，"
                        f"无法得到整数重复次数",
                        node.op_pos if node.op_pos is not None else node.start,
                    )
                times = usable // once_consumed
                added = _subtree_leaves(node.children) * times
                if leaf_budget + added > MAX_EXPANDED_LEAVES:
                    raise AdaptiveDeriveError(
                        f"自适应重复展开后指令数将超过上限 {MAX_EXPANDED_LEAVES}"
                        f"（至少 {leaf_budget + added} 条），请缩小自适应块范围",
                        node.op_pos if node.op_pos is not None else node.start,
                    )
                node.n = times
                leaf_budget += added
                blocks.append(AdaptiveBlockInfo(
                    start=node.start, end=node.end,
                    op_pos=node.op_pos if node.op_pos is not None else node.start,
                    reserve=node.reserve, actual=times,
                ))
                if times > 0:
                    for _ in range(times):
                        if failing is not None:
                            return
                        run(node.children)
                continue
            # 叶子
            if node.consumed > remaining:
                failing = node
                return
            remaining -= node.consumed
            consumed_before += node.consumed

    run(nodes)
    return failing, consumed_before, remaining, blocks


def analyze_row(index: int, source: str, available: int, blocked: bool) -> RowResult:
    """分析单行。

    :param available: 进入本行时可用针数（上一行产出）
    :param blocked: 之前的行是否已经出错；为 True 时本行无法获得确定的可用针数
    """
    if blocked:
        return RowResult(index=index, source=source, status="blocked",
                         error="上一行无法正常结束，本行针数无法确定")

    try:
        nodes = parse(source)
        # 固定部分（不含自适应块）超限时立即报错；自适应块在模拟中逐块校验
        count_fixed_leaves(nodes)
        failing, consumed_before, remaining_in, blocks = _simulate_with_adaptive(
            nodes, available)
        leaf_total = count_leaves(nodes)
    except ParseError as exc:
        return RowResult(
            index=index, source=source, status="error", available=available,
            error=exc.message, error_pos=exc.pos,
        )
    except AdaptiveDeriveError as exc:
        return RowResult(
            index=index, source=source, status="error", available=available,
            error=exc.message, error_pos=exc.pos,
        )
    except ExpansionLimitError as exc:
        return RowResult(
            index=index, source=source, status="error", available=available,
            error=str(exc), error_pos=len(source),
        )

    total_consumed = sum(n.consumed for n in nodes)
    total_produced = sum(n.produced for n in nodes)

    try:
        tokens = expand_tokens(nodes)
    except ExpansionLimitError as exc:
        return RowResult(
            index=index, source=source, status="error", available=available,
            error=str(exc), error_pos=len(source),
        )
    truncated = len(tokens) > PREVIEW_LIMIT
    preview = tokens[:PREVIEW_LIMIT]

    if failing is not None:
        shortage = failing.consumed - remaining_in
        return RowResult(
            index=index, source=source, status="shortage", available=available,
            consumed=consumed_before, expanded_count=leaf_total,
            expanded_preview=preview, expanded_truncated=truncated,
            adaptive_blocks=blocks,
            error=(
                f"针数不足：执行 {failing.type}{failing.n} 时需要 {failing.consumed} 针，"
                f"本行剩余可用仅 {remaining_in} 针，短缺 {shortage} 针"
            ),
            failing_instruction=f"{failing.type}{failing.n}",
            failing_start=failing.start, failing_end=failing.end,
            shortage=shortage,
        )

    result = RowResult(
        index=index, source=source, status="ok", available=available,
        consumed=total_consumed, produced=total_produced, remaining=total_produced,
        expanded_count=leaf_total, expanded_preview=preview,
        expanded_truncated=truncated,
        adaptive_blocks=blocks,
    )
    if total_consumed < available:
        result.status = "unconsumed"
        result.unconsumed = available - total_consumed
        result.error = (
            f"针数未全部消耗：本行可用 {available} 针，仅消耗 {total_consumed} 针，"
            f"剩余 {available - total_consumed} 针未参与编织"
        )
    return result


def analyze_plan(start_stitches: int, rows: list[str]) -> list[RowResult]:
    """逐行审校：以上一行产出作为下一行可用针数。"""
    results: list[RowResult] = []
    available = start_stitches
    blocked = False
    for i, source in enumerate(rows):
        rr = analyze_row(i, source, available, blocked)
        results.append(rr)
        if rr.status in ("error", "shortage"):
            blocked = True
        else:
            available = rr.remaining
    return results
