"""Cursor Agent CLI 屏幕解析器

Cursor Agent 二进制名为 `agent`，虽然同样基于 Ink，但 TUI 与 Codex 差异显著：
  - 所有内容整体向右缩进 2 列（col=0/1 恒为空格）
  - 输入框由 ▄/▀ 半块字符 + fg=#808080 灰色画边，**非** pyte bg 属性
  - 输入提示符为 → (U+2192)，**非** Codex 的 › (U+203A)
  - 输出内容无 ● / ✱ / › 等首列指示字符，纯文本渲染（灰色 fg）
  - 底部区只有 `Composer X Fast · usage` 与 `/cwd` 两行，无 `─━═` 分割线

因此 AgentParser 完全 override parse()，使用 Cursor 专属的解析路径，不再复用
CodexParser 的 ─/背景色/›提示符那一套。作为子类仅复用共享的工具函数（_get_col0、
_get_row_text、_get_row_ansi_text 等）。
"""

import logging
import time
from typing import List, Optional, Set, Tuple

import pyte

from utils.components import (
    Component, OutputBlock, BottomBar,
)

from .codex_parser import (
    CodexParser,
    _get_col0, _get_row_text, _get_row_ansi_text,
    BOX_CORNER_TOP, BOX_CORNER_BOTTOM,
)

logger = logging.getLogger('AgentParser')


# Cursor Agent 输入框上下边框字符（半块字符，fg=#808080 灰色）
_TOP_BORDER_CHAR = '▄'
_BOT_BORDER_CHAR = '▀'

# Cursor Agent 输入提示符（→，U+2192）
_CURSOR_PROMPT_CHARS: Set[str] = {'→'}

# 判定一行是边框需要的最少连续边框字符数（防止普通文本中的 ▄/▀ 被误判）
_BORDER_MIN_RUN = 10

# 欢迎区文本特征
_WELCOME_TITLE_TEXT = 'Cursor Agent'
_WELCOME_VERSION_PREFIX = 'v20'  # 形如 v2026.04.17-787b533

# 输入框上/下边框与输入内容之间的最大行距（通常 0~2 行）
_MAX_INPUT_HEIGHT = 20

# Cursor Agent 底部栏最大行数（Composer 行 + /cwd 行，一般不超过 3）
_BOTTOM_SCAN_ROWS = 4


def _count_max_run(text: str, ch: str) -> int:
    """统计 text 中 ch 字符的最长连续游程"""
    max_run = 0
    cur = 0
    for c in text:
        if c == ch:
            cur += 1
            if cur > max_run:
                max_run = cur
        else:
            cur = 0
    return max_run


def _is_border_row(screen: pyte.Screen, row: int, border_char: str) -> bool:
    """某行是否为 Cursor Agent 输入框边框（至少 _BORDER_MIN_RUN 个连续 border_char）"""
    text = _get_row_text(screen, row)
    if not text:
        return False
    return _count_max_run(text, border_char) >= _BORDER_MIN_RUN


class AgentParser(CodexParser):
    """Cursor Agent CLI 专用解析器

    继承 CodexParser 仅为复用其 __init__ 初始化的缓存字段与 BaseParser 属性，
    真正的解析逻辑完全在本类 override 的 parse() 中实现。
    """

    def parse(self, screen: pyte.Screen) -> List[Component]:
        t0 = time.perf_counter()

        scan_limit = min(screen.cursor.y + 5, screen.lines - 1)

        # Step 1：定位输入框（▄ 上边框 + ▀ 下边框）
        top_border, bot_border = self._find_input_box(screen, scan_limit)

        if top_border is not None and bot_border is not None:
            output_rows = list(range(top_border))
            input_rows = list(range(top_border + 1, bot_border))
            bottom_rows = list(range(bot_border + 1,
                                     min(bot_border + 1 + _BOTTOM_SCAN_ROWS,
                                         screen.lines)))
        else:
            # 输入框尚未渲染（冷启动 / 仅有 Workspace Trust 对话框等场景）
            output_rows = list(range(scan_limit + 1))
            input_rows = []
            bottom_rows = []

        t1 = time.perf_counter()

        # Step 2：跳过欢迎区（Cursor Agent 标题 + 版本号 + Workspace Trust 对话框）
        output_rows = self._trim_cursor_welcome(screen, output_rows)

        # Step 3：输出区按空行切分为 OutputBlock
        components: List[Component] = self._parse_output_indented(screen, output_rows)

        # Step 4：提取输入区 → 后的文本
        self.last_input_text = self._extract_cursor_input_text(screen, input_rows)
        self.last_input_ansi_text = self.last_input_text

        # Step 5：BottomBar（Composer X Fast · usage / /cwd 两行合并）
        bottom_parts: List[str] = []
        ansi_bottom_parts: List[str] = []
        for r in bottom_rows:
            text = _get_row_text(screen, r).strip()
            if not text:
                continue
            bottom_parts.append(text)
            ansi_bottom_parts.append(_get_row_ansi_text(screen, r).strip())
        if bottom_parts:
            components.append(BottomBar(
                text='\n'.join(bottom_parts),
                ansi_text='\n'.join(ansi_bottom_parts),
                has_background_agents=False,
                agent_count=0,
                agent_summary='',
            ))

        t2 = time.perf_counter()
        self.last_layout_mode = 'normal'
        self.last_parse_timing = (
            f"split={1000*(t1-t0):.1f}ms  output={1000*(t2-t1):.1f}ms  "
            f"output_rows={len(output_rows)}  input_rows={len(input_rows)}  "
            f"cursor_y={screen.cursor.y}"
        )
        return components

    # ── 区域定位 ──────────────────────────────────────────────────────────────

    def _find_input_box(
        self, screen: pyte.Screen, scan_limit: int
    ) -> Tuple[Optional[int], Optional[int]]:
        """从 scan_limit 向上找 ▀ 下边框，再向上找 ▄ 上边框。

        限制：上下边框之间距离 ≤ _MAX_INPUT_HEIGHT 行；两个边框必须成对出现。
        返回 (top_row, bot_row)，未找到返回 (None, None)。
        """
        bot: Optional[int] = None
        for row in range(scan_limit, -1, -1):
            if _is_border_row(screen, row, _BOT_BORDER_CHAR):
                bot = row
                break
        if bot is None:
            return (None, None)

        top: Optional[int] = None
        lower_bound = max(-1, bot - _MAX_INPUT_HEIGHT - 1)
        for row in range(bot - 1, lower_bound, -1):
            if _is_border_row(screen, row, _TOP_BORDER_CHAR):
                top = row
                break
        if top is None:
            return (None, None)
        return (top, bot)

    def _trim_cursor_welcome(
        self, screen: pyte.Screen, rows: List[int]
    ) -> List[int]:
        """跳过欢迎区：
        - "Cursor Agent" 标题行、紧随其后的版本号行（vYYYY...）
        - 以 ╭/┌ 开头的 Workspace Trust Required 对话框（整框丢弃）

        注意：Cursor Agent 所有内容向右缩进 2 格，col0 恒为空格，
        因此需要判断行首个非空字符而非 col=0。
        """
        if not rows:
            return rows

        keep = [True] * len(rows)
        i = 0
        while i < len(rows):
            row = rows[i]
            text = _get_row_text(screen, row).strip()
            if not text:
                i += 1
                continue

            first_ch = text[0]

            # 丢弃 "Cursor Agent" 标题 + 版本号
            if text == _WELCOME_TITLE_TEXT:
                keep[i] = False
                # 尝试跳过紧跟的版本号行（允许中间零空行）
                j = i + 1
                while j < len(rows):
                    t2 = _get_row_text(screen, rows[j]).strip()
                    if not t2:
                        j += 1
                        continue
                    if t2.startswith(_WELCOME_VERSION_PREFIX):
                        keep[j] = False
                    break
                i += 1
                continue

            # 丢弃 ╭──╮ 欢迎/Trust 对话框（整框）
            if first_ch in BOX_CORNER_TOP:
                keep[i] = False
                j = i + 1
                while j < len(rows):
                    keep[j] = False
                    t2 = _get_row_text(screen, rows[j]).strip()
                    if t2 and t2[0] in BOX_CORNER_BOTTOM:
                        j += 1
                        break
                    j += 1
                i = j
                continue

            i += 1

        return [rows[k] for k in range(len(rows)) if keep[k]]

    # ── 输出区解析 ────────────────────────────────────────────────────────────

    def _parse_output_indented(
        self, screen: pyte.Screen, rows: List[int]
    ) -> List[Component]:
        """输出区按空行切分为若干 OutputBlock。

        Cursor Agent 输出无圆点/星号首列字符，每条消息是纯文本块，
        块之间用空行分隔（可能有多个空行）。每个 block 去除左侧 2 空格缩进。
        """
        if not rows:
            return []

        components: List[Component] = []
        current: List[int] = []

        def flush():
            if not current:
                return
            lines: List[str] = []
            ansi_lines: List[str] = []
            for r in current:
                raw = _get_row_text(screen, r)
                # 去除最左 2 空格缩进（Cursor Agent 全局 indent）
                if raw.startswith('  '):
                    raw = raw[2:]
                lines.append(raw.rstrip())
                ansi_lines.append(_get_row_ansi_text(screen, r).rstrip())
            content = '\n'.join(lines).strip('\n')
            ansi_content = '\n'.join(ansi_lines)
            if content:
                components.append(OutputBlock(
                    content=content,
                    ansi_content=ansi_content,
                    indicator='',
                    ansi_indicator='',
                    start_row=current[0],
                    is_streaming=False,
                ))

        for row in rows:
            text = _get_row_text(screen, row)
            if text.strip():
                current.append(row)
            else:
                flush()
                current = []
        flush()

        return components

    # ── 输入区文本提取 ────────────────────────────────────────────────────────

    def _extract_cursor_input_text(
        self, screen: pyte.Screen, input_rows: List[int]
    ) -> str:
        """输入区以 → 为提示符，返回 → 之后的当前文本"""
        for row in input_rows:
            text = _get_row_text(screen, row)
            idx = text.find('→')
            if idx >= 0:
                return text[idx + 1:].strip()
        return ''
