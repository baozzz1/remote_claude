"""_dedup_blocks 单元测试（server.py 的 Ink 重绘副本合并逻辑）

设计约束（和代码一致）：
  只合并「类型相同 + 整块内容字节一致 + 在列表中紧邻」的 block。
  任何隔着其它 block 的重复、或首行相同但内容不同的情形都必须保留。
"""

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "server"))

from utils.components import (
    OutputBlock, UserInput, PlanBlock, SystemBlock,
)
from server import _dedup_blocks, _dedup_full_content


def _ob(content, **kw):
    return OutputBlock(content=content, **kw)


# ── 基础行为 ──────────────────────────────────────────────────────────────────

class TestDedupBasics(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_dedup_blocks([]), [])

    def test_single(self):
        b = _ob("hello")
        self.assertEqual(_dedup_blocks([b]), [b])

    def test_adjacent_identical_collapsed(self):
        """Ink 重绘：同一个 block 紧邻重复一次 → 合并为一份"""
        a = _ob("summary\nbody", start_row=10)
        b = _ob("summary\nbody", start_row=50)
        result = _dedup_blocks([a, b])
        self.assertEqual(len(result), 1)
        # 保留后者（最新渲染的位置）
        self.assertEqual(result[0].start_row, 50)

    def test_many_adjacent_identical_collapsed(self):
        """Ink 滚动重绘可能留 N 份紧邻副本 → 合并到最后一份"""
        blocks = [_ob("summary\nsame body", start_row=i * 10) for i in range(5)]
        result = _dedup_blocks(blocks)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].start_row, 40)


# ── 回归：不要把合法历史当成副本 ────────────────────────────────────────────

class TestNoFalsePositives(unittest.TestCase):
    """评审 P1：防止首行相同的独立 block 被当成副本静默删掉"""

    def test_same_first_line_different_body_kept(self):
        """两段独立回复都以「Here is the patch:」开头但补丁不同 → 必须都保留"""
        a = _ob("Here is the patch:\n--- patch A ---")
        b = _ob("Here is the patch:\n--- patch B ---")
        result = _dedup_blocks([a, b])
        self.assertEqual(len(result), 2)

    def test_same_title_different_plan_body_kept(self):
        """两次计划都叫「Plan to implement」但步骤不同 → 必须都保留"""
        a = PlanBlock(title="Plan to implement", content="step 1: foo")
        b = PlanBlock(title="Plan to implement", content="step 1: foo\nstep 2: bar")
        result = _dedup_blocks([a, b])
        self.assertEqual(len(result), 2)

    def test_same_system_prompt_across_turns_kept(self):
        """两次系统提示 ✻ Using memory... 在不同时刻触发 → 必须都保留

        注：若完全字节一致且相邻，合并是可接受的（等同 Ink 重绘），这里故意在
        中间塞一个 OutputBlock 模拟两次真实触发之间有别的输出。
        """
        s1 = SystemBlock(content="✻ Using memory...")
        o = _ob("user asked something")
        s2 = SystemBlock(content="✻ Using memory...")
        result = _dedup_blocks([s1, o, s2])
        self.assertEqual(len(result), 3)

    def test_non_adjacent_identical_kept(self):
        """字节完全一致但不相邻 → 不合并（保留合法历史）"""
        a = _ob("content X")
        mid = _ob("something else")
        b = _ob("content X")
        result = _dedup_blocks([a, mid, b])
        self.assertEqual(len(result), 3)

    def test_user_repeats_same_command_kept(self):
        """用户两次输入同一个命令（例如 `ls`）之间有输出 → 两次都保留"""
        u1 = UserInput(text="ls")
        o = _ob("file1 file2")
        u2 = UserInput(text="ls")
        result = _dedup_blocks([u1, o, u2])
        self.assertEqual(len(result), 3)


# ── 类型与空值边界 ────────────────────────────────────────────────────────────

class TestTypeAndEmpty(unittest.TestCase):
    def test_different_types_never_merged(self):
        """内容字符串偶然一致但类型不同 → 不合并"""
        u = UserInput(text="same")
        o = _ob("same")
        result = _dedup_blocks([u, o])
        self.assertEqual(len(result), 2)

    def test_empty_content_not_merged(self):
        """空签名的 block 不参与合并，避免所有空 OutputBlock 塌缩"""
        a = _ob("")
        b = _ob("")
        result = _dedup_blocks([a, b])
        self.assertEqual(len(result), 2)

    def test_whitespace_only_content_not_merged(self):
        a = _ob("   \n  ")
        b = _ob("   \n  ")
        result = _dedup_blocks([a, b])
        self.assertEqual(len(result), 2)


# ── 真实场景：Ink 整屏重绘 ────────────────────────────────────────────────────

class TestInkRedrawScenario(unittest.TestCase):
    def test_summary_repeated_in_sequence(self):
        """用户原始报错场景：最终总结被 Ink 重绘连续输出 N 份 → 合并为一份"""
        blocks = [
            UserInput(text="请帮我总结一下"),
            _ob("正在分析...", start_row=5),
            _ob("● 总结完成\n- 要点 1\n- 要点 2\n- 要点 3", start_row=10),
            _ob("● 总结完成\n- 要点 1\n- 要点 2\n- 要点 3", start_row=60),
            _ob("● 总结完成\n- 要点 1\n- 要点 2\n- 要点 3", start_row=110),
        ]
        result = _dedup_blocks(blocks)
        self.assertEqual(len(result), 3)
        self.assertIsInstance(result[0], UserInput)
        self.assertEqual(result[1].content, "正在分析...")
        self.assertEqual(result[2].start_row, 110)

    def test_user_input_between_summary_and_copy_keeps_both(self):
        """关键回归：summary 出现，中间有其它 block，summary 再次出现 → 都保留

        这是评审关心的"新任务碰巧给出同样首行/同样内容的回复"场景。
        """
        blocks = [
            UserInput(text="第一次请求"),
            _ob("● 总结完成\n同样的内容", start_row=10),
            UserInput(text="第二次请求"),
            _ob("● 总结完成\n同样的内容", start_row=60),
        ]
        result = _dedup_blocks(blocks)
        self.assertEqual(len(result), 4)


# ── 签名函数 ──────────────────────────────────────────────────────────────────

class TestFullContentSignature(unittest.TestCase):
    def test_output_block_full_content(self):
        self.assertEqual(_dedup_full_content(_ob("a\nb")), "a\nb")
        self.assertEqual(_dedup_full_content(_ob("   ")), "")

    def test_user_input_text(self):
        self.assertEqual(_dedup_full_content(UserInput(text="hi")), "hi")

    def test_plan_block_combines_title_and_body(self):
        sig = _dedup_full_content(PlanBlock(title="T", content="x"))
        self.assertIn("T", sig)
        self.assertIn("x", sig)

    def test_system_block(self):
        self.assertEqual(_dedup_full_content(SystemBlock(content="msg")), "msg")


if __name__ == "__main__":
    unittest.main()
