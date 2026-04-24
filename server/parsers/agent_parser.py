"""Cursor Agent CLI 屏幕解析器

Cursor Agent 二进制名为 `agent`，虽然同样基于 Ink，但 TUI 与 Codex 差异显著：
  - 所有内容整体向右缩进 2 列（col=0/1 恒为空格）
  - 输入框由 ▄/▀ 半块字符 + fg=#808080 灰色画边（思考中时 ▄ fg 变为 #151515，
    且可能只有上边框没有下边框）
  - 输入提示符为 → (U+2192)，**非** Codex 的 › (U+203A)
  - 输出内容无 ● / ✱ / › 等首列指示字符，纯文本渲染（灰色 fg）
  - 思考/执行中：输出区尾部出现 "  {braille_spinner} {action} [token_count]" 行
    braille_spinner = U+2800..U+28FF 的盲文点阵字符帧，fg 为绿色
  - 底部区只有 `Composer X Fast · usage` 与 `/cwd` 两行，无 `─━═` 分割线

因此 AgentParser 完全 override parse()，使用 Cursor 专属的解析路径：
  - 输入区通过 → 行定位（▄/▀ 边框为可选装饰，思考中常常缺失下边框）
  - 思考状态通过 braille + green fg 的 spinner 行识别，升级为 StatusLine，
    这样 lark 卡片才能正确显示 "⏳ 思考中" 头部
"""

import logging
import re
import time
from typing import List, Optional, Set, Tuple

import pyte

from utils.components import (
    Component, OutputBlock, BottomBar, StatusLine,
)

from .codex_parser import (
    CodexParser,
    _get_row_text, _get_row_ansi_text,
    BOX_CORNER_TOP, BOX_CORNER_BOTTOM,
)

logger = logging.getLogger('AgentParser')


# Cursor Agent 输入框上下边框字符（半块字符）
_TOP_BORDER_CHAR = '▄'
_BOT_BORDER_CHAR = '▀'

# Cursor Agent 输入提示符（→，U+2192）
_CURSOR_PROMPT: str = '→'

# 判定一行是边框需要的最少连续边框字符数（防止普通文本中的 ▄/▀ 被误判）
_BORDER_MIN_RUN = 10

# 欢迎区文本特征
_WELCOME_TITLE_TEXT = 'Cursor Agent'
_WELCOME_VERSION_PREFIX = 'v20'  # 形如 v2026.04.17-787b533

# Cursor Agent 底部栏最大行数（Composer 行 + /cwd 行，一般不超过 3）
_BOTTOM_SCAN_ROWS = 4

# Braille 点阵字符范围（spinner 帧，每帧一个 U+2800..U+28FF 字符）
_BRAILLE_START = 0x2800
_BRAILLE_END = 0x28FF

# StatusLine 文本解析正则（剥离行首 braille 与空格后匹配动词 + 可选 token 计数）
_STATUS_BODY_RE = re.compile(
    r'^(?P<action>\S+)(?:\s+(?P<tokens>.+?))?$'
)


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


def _is_braille(ch: str) -> bool:
    """判断字符是否为 braille 点阵（spinner 帧字符）"""
    return len(ch) == 1 and _BRAILLE_START <= ord(ch) <= _BRAILLE_END


def _is_greenish(fg) -> bool:
    """判断 fg 颜色是否为绿色系（Cursor Agent spinner 特征色）"""
    if not fg or fg == 'default':
        return False
    if isinstance(fg, str):
        key = fg.lower().replace(' ', '').replace('-', '')
        if 'green' in key:
            return True
        if len(fg) == 6:
            try:
                r = int(fg[0:2], 16)
                g = int(fg[2:4], 16)
                b = int(fg[4:6], 16)
                # G 显著大于 R 和 B 即视为绿色
                return g > 100 and g > r and g > b
            except ValueError:
                return False
    return False


def _is_spinner_row(screen: pyte.Screen, row: int) -> Optional[str]:
    """某行是否为 Cursor Agent 思考中 spinner 行。

    特征：前若干列（通常 col=1/2）有 braille 字符，且至少有一个 braille 字符 fg 是绿色。
    返回 spinner indicator 字符（首个 braille），非 spinner 行返回 None。
    """
    indicator: Optional[str] = None
    for col in range(0, min(6, screen.columns)):
        try:
            ch = screen.buffer[row][col]
        except (KeyError, IndexError):
            continue
        if _is_braille(ch.data):
            if indicator is None:
                indicator = ch.data
            if _is_greenish(getattr(ch, 'fg', 'default')):
                return indicator
    return None


def _find_input_row(screen: pyte.Screen, scan_limit: int) -> Optional[int]:
    """从 scan_limit 向上扫描，找到行首（strip 后）为 → 的行。"""
    for row in range(scan_limit, -1, -1):
        text = _get_row_text(screen, row).strip()
        if text.startswith(_CURSOR_PROMPT):
            return row
    return None


class AgentParser(CodexParser):
    """Cursor Agent CLI 专用解析器

    继承 CodexParser 仅为复用其 __init__ 初始化的缓存字段与 BaseParser 属性，
    真正的解析逻辑完全在本类 override 的 parse() 中实现。
    """

    def parse(self, screen: pyte.Screen) -> List[Component]:
        t0 = time.perf_counter()

        scan_limit = min(screen.cursor.y + 5, screen.lines - 1)

        # Step 1：定位输入框（以 → 行为锚，▄/▀ 边框为可选装饰）
        input_row = _find_input_row(screen, scan_limit)

        if input_row is not None:
            top_border = (input_row - 1) if (
                input_row > 0 and _is_border_row(screen, input_row - 1, _TOP_BORDER_CHAR)
            ) else None
            bot_border = (input_row + 1) if (
                input_row < screen.lines - 1
                and _is_border_row(screen, input_row + 1, _BOT_BORDER_CHAR)
            ) else None
            box_start = top_border if top_border is not None else input_row
            box_end = bot_border if bot_border is not None else input_row
            output_rows = list(range(box_start))
            input_rows = [input_row]
            bottom_rows = list(range(box_end + 1,
                                     min(box_end + 1 + _BOTTOM_SCAN_ROWS,
                                         screen.lines)))
        else:
            # 输入框尚未渲染（冷启动 / Workspace Trust 对话框等场景）
            output_rows = list(range(scan_limit + 1))
            input_rows = []
            bottom_rows = []

        t1 = time.perf_counter()

        # Step 2：跳过欢迎区（Cursor Agent 标题 + 版本号 + Workspace Trust 对话框）
        output_rows = self._trim_cursor_welcome(screen, output_rows)

        # Step 3：识别 StatusLine（braille spinner + green fg 行），从 output_rows 中剔除
        status_row, status_component = self._extract_status_line(screen, output_rows)
        if status_row is not None:
            output_rows = [r for r in output_rows if r != status_row]

        # Step 4：输出区按空行切分为 OutputBlock
        components: List[Component] = self._parse_output_indented(screen, output_rows)

        # Step 5：StatusLine（如果检测到）附在 components 末尾，便于 OutputWatcher 分拣
        if status_component is not None:
            components.append(status_component)

        # Step 6：提取输入区 → 后的文本
        self.last_input_text = self._extract_cursor_input_text(screen, input_rows)
        self.last_input_ansi_text = self.last_input_text

        # Step 7：BottomBar（Composer X Fast · usage / /cwd 两行合并）
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

    # ── 欢迎区裁剪 ────────────────────────────────────────────────────────────

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

    # ── StatusLine 识别 ──────────────────────────────────────────────────────

    def _extract_status_line(
        self, screen: pyte.Screen, rows: List[int]
    ) -> Tuple[Optional[int], Optional[StatusLine]]:
        """在 output_rows 中查找 braille spinner 行作为 StatusLine。

        扫描方向：从后向前（spinner 总是出现在输出区尾部，即紧挨 ▄ 上边框）。
        返回 (spinner_row, StatusLine) 或 (None, None)。
        """
        for row in reversed(rows):
            indicator = _is_spinner_row(screen, row)
            if indicator is None:
                continue

            raw_text = _get_row_text(screen, row)

            # 计算 body 起始列：前导空白 + 连续 braille/空白
            skipped_left = 0
            for ch in raw_text:
                if ch == ' ' or ch == '':
                    skipped_left += 1
                else:
                    break
            for ch in raw_text[skipped_left:]:
                if _is_braille(ch) or ch.isspace():
                    skipped_left += 1
                else:
                    break

            body_text = raw_text[skipped_left:].rstrip()
            # 从 body 起始列取 ANSI 文本，避免把会变化的 braille 帧字符留在 ansi_raw 里
            # （否则 spinner 每帧不同会令 status_line hash 持续变化、卡片被无谓频繁更新）
            ansi_body = _get_row_ansi_text(screen, row, start_col=skipped_left).rstrip()

            action = ''
            tokens = ''
            m = _STATUS_BODY_RE.match(body_text)
            if m:
                action = m.group('action') or ''
                tokens = (m.group('tokens') or '').strip()

            # indicator 固定置空：card header 仅使用 action/elapsed/tokens；
            # 保留动画帧字符只会让下游 hash 抖动。
            return row, StatusLine(
                action=action,
                elapsed='',
                tokens=tokens,
                raw=body_text,
                ansi_raw=ansi_body,
                indicator='',
                ansi_indicator='',
            )
        return None, None

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
                # 决定该行的 2 列 indent 是否要剥离（Cursor Agent 全局向右 2 空格）
                strip_cols = 2 if raw.startswith('  ') else 0
                if strip_cols:
                    raw = raw[strip_cols:]
                lines.append(raw.rstrip())
                # ANSI 版本也必须按同样的列数裁剪，否则飞书卡片里 transcript 会整体右移 2 列
                ansi_lines.append(
                    _get_row_ansi_text(screen, r, start_col=strip_cols).rstrip()
                )
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
            idx = text.find(_CURSOR_PROMPT)
            if idx >= 0:
                return text[idx + 1:].strip()
        return ''
