#!/usr/bin/env python3
"""AgentParser（Cursor Agent CLI 解析器）单元测试

覆盖场景：
  1. 冷启动：Workspace Trust Required 对话框 → 无 OutputBlock，无 BottomBar
  2. 稳态对话：用户输入 + agent 回复 → 2 个 OutputBlock + 1 个 BottomBar
  3. 输入框定位：▄/▀ 边框 + → 提示符 → 正确识别 input_text
  4. 多段输出：空行分隔 → 切成多个 OutputBlock
  5. 标题/版本号 + 欢迎框 → 全部 trim
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pyte
from pyte.screens import Char

from server.parsers.agent_parser import AgentParser
from utils.components import OutputBlock, BottomBar, StatusLine


COLS = 220
ROWS = 100


def _new_screen():
    return pyte.Screen(COLS, ROWS)


def _write(screen, row: int, col: int, text: str, fg: str = 'default', bg: str = 'default'):
    """把 text 直接写入 screen.buffer[row]，从 col 开始"""
    for i, ch in enumerate(text):
        if col + i >= screen.columns:
            break
        screen.buffer[row][col + i] = Char(data=ch, fg=fg, bg=bg)


def _set_cursor(screen, x: int, y: int):
    screen.cursor.x = x
    screen.cursor.y = y


class TestTrustDialog(unittest.TestCase):
    """冷启动只显示 Workspace Trust 对话框时，应全部被 trim"""

    def test_trust_dialog_yields_no_blocks(self):
        screen = _new_screen()
        # ╭───...───╮ 顶边框（col=2）
        _write(screen, 1, 2, '╭' + '─' * 190)
        # 框内容行
        _write(screen, 2, 2, '│')
        _write(screen, 3, 2, '│  ⚠ Workspace Trust Required')
        _write(screen, 4, 2, '│')
        _write(screen, 5, 2, '│  Do you trust the contents of this directory?')
        _write(screen, 6, 2, '│    [a] Trust this workspace')
        _write(screen, 7, 2, '│    [q] Quit')
        # ╰───...───╯ 底边框
        _write(screen, 8, 2, '╰' + '─' * 190)
        _set_cursor(screen, 0, 9)

        parser = AgentParser()
        comps = parser.parse(screen)

        # 欢迎框被整块丢弃，不应产出任何 OutputBlock
        output_blocks = [c for c in comps if isinstance(c, OutputBlock)]
        self.assertEqual(len(output_blocks), 0,
                         f"trust dialog should be trimmed, got {output_blocks!r}")


class TestSteadyStateConversation(unittest.TestCase):
    """稳态：用户输入 + agent 回复 + 输入框 + 底部栏"""

    def _build(self):
        screen = _new_screen()
        _write(screen, 2, 2, 'Cursor Agent')
        _write(screen, 3, 2, 'v2026.04.17-787b533')
        # 用户输入回显（灰色 fg）
        _write(screen, 6, 2, 'say hi in one short sentence', fg='808080')
        # agent 回复
        _write(screen, 9, 2, "Hi there — I'm here and ready to help.", fg='808080')
        # 输入框：▄ 上边框
        _write(screen, 12, 1, '▄' * 200, fg='808080')
        # 输入行：→ prompt
        _write(screen, 13, 2, '→ Add a follow-up')
        # ▀ 下边框
        _write(screen, 14, 1, '▀' * 200, fg='808080')
        # 底部栏
        _write(screen, 15, 2, 'Composer 2 Fast · 5.2%')
        _write(screen, 16, 2, '/private/tmp')
        _set_cursor(screen, 0, 17)
        return screen

    def test_output_blocks_and_bottom_bar(self):
        screen = self._build()
        parser = AgentParser()
        comps = parser.parse(screen)

        output_blocks = [c for c in comps if isinstance(c, OutputBlock)]
        bottom_bars = [c for c in comps if isinstance(c, BottomBar)]

        # 欢迎标题 + 版本号被 trim，剩 2 个 OutputBlock（用户输入 + agent 回复）
        self.assertEqual(len(output_blocks), 2,
                         f"expected 2 output blocks, got {[b.content for b in output_blocks]}")
        self.assertIn('say hi in one short sentence', output_blocks[0].content)
        self.assertIn("Hi there", output_blocks[1].content)

        # 每个 OutputBlock 的左 2 空格 indent 已剥离
        for b in output_blocks:
            self.assertFalse(b.content.startswith('  '),
                             f"indent not stripped: {b.content!r}")

        # BottomBar 合并 Composer + cwd
        self.assertEqual(len(bottom_bars), 1)
        self.assertIn('Composer 2 Fast', bottom_bars[0].text)
        self.assertIn('/private/tmp', bottom_bars[0].text)

    def test_input_text_extracted(self):
        screen = self._build()
        parser = AgentParser()
        parser.parse(screen)
        self.assertEqual(parser.last_input_text, 'Add a follow-up')


class TestOutputBlockSplitting(unittest.TestCase):
    """输出区按空行切分为多个 block"""

    def test_blank_line_splits_blocks(self):
        screen = _new_screen()
        # 两段独立内容，中间空行分隔
        _write(screen, 2, 2, 'First message line 1')
        _write(screen, 3, 2, 'First message line 2')
        # row 4 空行
        _write(screen, 5, 2, 'Second message')
        # 输入框
        _write(screen, 8, 1, '▄' * 200, fg='808080')
        _write(screen, 9, 2, '→')
        _write(screen, 10, 1, '▀' * 200, fg='808080')
        _set_cursor(screen, 0, 11)

        parser = AgentParser()
        comps = parser.parse(screen)
        output_blocks = [c for c in comps if isinstance(c, OutputBlock)]

        self.assertEqual(len(output_blocks), 2,
                         f"blank line should split, got {[b.content for b in output_blocks]}")
        self.assertEqual(output_blocks[0].content,
                         'First message line 1\nFirst message line 2')
        self.assertEqual(output_blocks[1].content, 'Second message')


class TestBorderDetection(unittest.TestCase):
    """▄/▀ 边框识别：需足够长的连续 run"""

    def test_short_run_not_treated_as_border(self):
        # 输出区里出现零散的 ▄（比如作为装饰字符），不应误识别为边框
        screen = _new_screen()
        _write(screen, 2, 2, 'Text with ▄ short decor ▄▄')  # 只有短游程
        # 真正的边框
        _write(screen, 5, 1, '▄' * 200, fg='808080')
        _write(screen, 6, 2, '→ test')
        _write(screen, 7, 1, '▀' * 200, fg='808080')
        _set_cursor(screen, 0, 8)

        parser = AgentParser()
        comps = parser.parse(screen)
        output_blocks = [c for c in comps if isinstance(c, OutputBlock)]

        # row 2 的装饰 ▄ 不应被识别为上边框
        self.assertEqual(len(output_blocks), 1)
        self.assertIn('short decor', output_blocks[0].content)


class TestNoInputBoxFound(unittest.TestCase):
    """输入框尚未渲染（如启动过渡态）时，output_rows 仍能解析"""

    def test_fallback_when_no_borders(self):
        screen = _new_screen()
        _write(screen, 2, 2, 'Cursor Agent')
        _write(screen, 3, 2, 'v2026.04.17-787b533')
        _write(screen, 5, 2, 'Some early message')
        _set_cursor(screen, 0, 6)

        parser = AgentParser()
        comps = parser.parse(screen)
        output_blocks = [c for c in comps if isinstance(c, OutputBlock)]

        self.assertEqual(len(output_blocks), 1)
        self.assertEqual(output_blocks[0].content, 'Some early message')


class TestThinkingStatusLine(unittest.TestCase):
    """思考/工具调用中：输出区尾部 braille spinner 行应解析为 StatusLine
    而非 OutputBlock，否则 lark 卡片会把 header 显示为"就绪"。
    """

    def test_working_spinner_emits_status_line(self):
        screen = _new_screen()
        # 前面一段输出
        _write(screen, 6, 2, 'say something', fg='808080')
        # 空行
        # spinner 行（col=1 braille 字符，fg=green）
        _write(screen, 9, 1, '⠳⠀ Working', fg='green')
        # 输入框：▄ 上边框 + → 输入 + ▀ 下边框
        _write(screen, 10, 1, '▄' * 200, fg='151515')
        _write(screen, 11, 2, '→ Add a follow-up')
        _write(screen, 12, 1, '▀' * 200, fg='151515')
        _write(screen, 13, 2, 'Composer 2 Fast · 5.2%')
        _set_cursor(screen, 0, 14)

        parser = AgentParser()
        comps = parser.parse(screen)

        status_lines = [c for c in comps if isinstance(c, StatusLine)]
        output_blocks = [c for c in comps if isinstance(c, OutputBlock)]

        self.assertEqual(len(status_lines), 1,
                         f"expected 1 StatusLine, got components={[type(c).__name__ for c in comps]}")
        sl = status_lines[0]
        self.assertEqual(sl.action, 'Working')
        self.assertTrue(sl.indicator and 0x2800 <= ord(sl.indicator) <= 0x28FF,
                        f"indicator should be braille, got {sl.indicator!r}")

        # spinner 行被剔除，不应作为 OutputBlock 出现
        for b in output_blocks:
            self.assertNotIn('Working', b.content)

    def test_running_spinner_extracts_token_count(self):
        screen = _new_screen()
        _write(screen, 5, 2, '$ ls *.py', fg='808080')
        _write(screen, 7, 1, '⠰⠃ Running  53 tokens', fg='green')
        _write(screen, 8, 1, '▄' * 200, fg='151515')
        _write(screen, 9, 2, '→')
        _set_cursor(screen, 0, 10)

        parser = AgentParser()
        comps = parser.parse(screen)

        status_lines = [c for c in comps if isinstance(c, StatusLine)]
        self.assertEqual(len(status_lines), 1)
        self.assertEqual(status_lines[0].action, 'Running')
        self.assertEqual(status_lines[0].tokens, '53 tokens')

    def test_optional_bottom_border(self):
        """思考早期 ▀ 下边框可能缺失；input box 应仍然以 → 行为锚定位"""
        screen = _new_screen()
        _write(screen, 2, 2, 'some output line')
        # spinner
        _write(screen, 4, 1, '⠳⠀ Working', fg='green')
        # 只有 ▄ 上边框，没有 ▀ 下边框
        _write(screen, 5, 1, '▄' * 200, fg='151515')
        _write(screen, 6, 2, '→')
        _set_cursor(screen, 0, 7)

        parser = AgentParser()
        comps = parser.parse(screen)

        status_lines = [c for c in comps if isinstance(c, StatusLine)]
        output_blocks = [c for c in comps if isinstance(c, OutputBlock)]

        self.assertEqual(len(status_lines), 1,
                         "StatusLine should be detected even without bottom border")
        self.assertEqual(len(output_blocks), 1)
        self.assertEqual(output_blocks[0].content, 'some output line')

    def test_no_spinner_when_idle(self):
        """稳态（无 spinner）时不应生成 StatusLine"""
        screen = _new_screen()
        _write(screen, 2, 2, 'Hello response')
        _write(screen, 4, 1, '▄' * 200, fg='808080')
        _write(screen, 5, 2, '→ Add a follow-up')
        _write(screen, 6, 1, '▀' * 200, fg='808080')
        _set_cursor(screen, 0, 7)

        parser = AgentParser()
        comps = parser.parse(screen)

        status_lines = [c for c in comps if isinstance(c, StatusLine)]
        self.assertEqual(len(status_lines), 0,
                         "no spinner → no StatusLine")


class TestNonGreenBrailleIgnored(unittest.TestCase):
    """非绿色的 braille 字符（罕见但可能出现在内容里）不应被当作 spinner"""

    def test_grey_braille_not_spinner(self):
        screen = _new_screen()
        # 用户内容中包含 braille 字符但颜色是灰色
        _write(screen, 2, 2, '⠿⠿⠿ some decorative braille', fg='808080')
        _write(screen, 4, 1, '▄' * 200, fg='808080')
        _write(screen, 5, 2, '→')
        _set_cursor(screen, 0, 6)

        parser = AgentParser()
        comps = parser.parse(screen)

        status_lines = [c for c in comps if isinstance(c, StatusLine)]
        self.assertEqual(len(status_lines), 0,
                         "grey braille is content decoration, not a spinner")


if __name__ == '__main__':
    unittest.main(verbosity=2)
