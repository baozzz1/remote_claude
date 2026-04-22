"""_dedup_blocks 单元测试（server.py 的 block 去重逻辑）

场景覆盖：
1. 空列表 / 单元素直接返回
2. 按 block_id（首行内容）去重，保留最后一次出现
3. 按 OutputBlock 正文 hash 兜底去重
4. 空 block_id（首行为空）不参与去重
5. UserInput / PlanBlock / SystemBlock 与 OutputBlock 混合
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
from server import _dedup_blocks, _dedup_block_id, _dedup_content_hash


def _ob(content, **kw):
    return OutputBlock(content=content, **kw)


class TestDedupBlockId(unittest.TestCase):
    def test_empty_returns_empty(self):
        self.assertEqual(_dedup_blocks([]), [])

    def test_single_block_returns_as_is(self):
        b = _ob("hello")
        self.assertEqual(_dedup_blocks([b]), [b])

    def test_duplicate_output_block_collapsed_to_last(self):
        """首行相同的 OutputBlock（Ink 重绘副本）只保留最后一次"""
        a = _ob("summary\nold body", start_row=0)
        b = _ob("summary\nnew body", start_row=50)
        result = _dedup_blocks([a, b])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].content, "summary\nnew body")
        self.assertEqual(result[0].start_row, 50)

    def test_many_duplicates_collapsed(self):
        """Ink 滚动重绘可能留 N 份副本，全部归并到最后一次"""
        blocks = [_ob(f"summary\nbody v{i}", start_row=i * 10) for i in range(5)]
        result = _dedup_blocks(blocks)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].content, "summary\nbody v4")

    def test_different_blocks_preserved_in_order(self):
        """不同首行的 block 保留原序"""
        a = _ob("first")
        b = _ob("second")
        c = _ob("third")
        result = _dedup_blocks([a, b, c])
        self.assertEqual([x.content for x in result], ["first", "second", "third"])

    def test_duplicate_sandwiched(self):
        """a, b, a 中间夹一个 b → 保留最后的 a，b 不变"""
        a1 = _ob("summary", start_row=0)
        b = _ob("middle", start_row=20)
        a2 = _ob("summary", start_row=40)
        result = _dedup_blocks([a1, b, a2])
        self.assertEqual(len(result), 2)
        # 保留顺序：b 在 a2 之前（因为 a2 是最后一次出现的 summary）
        self.assertEqual(result[0].content, "middle")
        self.assertEqual(result[1].content, "summary")
        self.assertEqual(result[1].start_row, 40)


class TestDedupContentHash(unittest.TestCase):
    def test_same_body_different_first_line(self):
        """首行不同但正文完全相同 → pass 2 按内容 hash 去重"""
        body = "line1\nshared body text that's identical"
        a = _ob("● " + body, start_row=0)
        b = _ob("○ " + body, start_row=30)
        result = _dedup_blocks([a, b])
        # 首行 "● line1" 与 "○ line1" 不同，pass 1 保留两个
        # 正文 hash 相同，pass 2 只保留最后一个
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].start_row, 30)

    def test_different_body_kept(self):
        """正文不同 → hash 不同 → 保留"""
        a = _ob("shared\nbody A")
        b = _ob("different\nbody B")
        result = _dedup_blocks([a, b])
        self.assertEqual(len(result), 2)


class TestDedupEmptyBlockId(unittest.TestCase):
    def test_empty_content_not_collapsed(self):
        """content 为空的 OutputBlock 不会被 block_id 去重折叠"""
        a = _ob("")
        b = _ob("")
        # 两个空 block 都没有 block_id，pass 1 不合并；pass 2 hash 也为空
        result = _dedup_blocks([a, b])
        self.assertEqual(len(result), 2)

    def test_whitespace_only_content_not_collapsed(self):
        """纯空白首行不参与去重"""
        a = _ob("   \nreal content")
        b = _ob("   \nother content")
        result = _dedup_blocks([a, b])
        # 首行都是空白 → block_id = ""，pass 1 不合并；正文不同 → pass 2 也不合并
        self.assertEqual(len(result), 2)


class TestDedupMixedTypes(unittest.TestCase):
    def test_user_input_and_output_block_independent(self):
        """UserInput 与 OutputBlock 走不同前缀，不会互相干扰"""
        u = UserInput(text="summary")
        o = _ob("summary")
        result = _dedup_blocks([u, o])
        self.assertEqual(len(result), 2)

    def test_system_block_dedup(self):
        a = SystemBlock(content="✻ Loading memory...", start_row=0)
        b = SystemBlock(content="✻ Loading memory...", start_row=20)
        result = _dedup_blocks([a, b])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].start_row, 20)

    def test_plan_block_dedup_by_title(self):
        a = PlanBlock(title="Implement feature X", content="step 1", start_row=0)
        b = PlanBlock(title="Implement feature X", content="step 1\nstep 2", start_row=30)
        result = _dedup_blocks([a, b])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].content, "step 1\nstep 2")

    def test_realistic_ink_redraw_pattern(self):
        """模拟真实 Ink 重绘场景：一段对话中 summary 被滚出 history 又在 buffer 重绘"""
        blocks = [
            UserInput(text="请帮我总结一下"),
            _ob("正在分析...", start_row=5),
            _ob("● 总结完成\n- 要点 1\n- 要点 2\n- 要点 3", start_row=10),
            # Ink 整屏重绘，history.top 留了两份旧副本
            _ob("● 总结完成\n- 要点 1\n- 要点 2\n- 要点 3", start_row=60),
            _ob("● 总结完成\n- 要点 1\n- 要点 2\n- 要点 3", start_row=110),
        ]
        result = _dedup_blocks(blocks)
        self.assertEqual(len(result), 3)
        self.assertIsInstance(result[0], UserInput)
        self.assertEqual(result[1].content, "正在分析...")
        self.assertEqual(result[2].content, "● 总结完成\n- 要点 1\n- 要点 2\n- 要点 3")
        # 保留的是最后一次出现的 start_row
        self.assertEqual(result[2].start_row, 110)


class TestBlockIdComputation(unittest.TestCase):
    def test_block_id_matches_shared_state_format(self):
        """_dedup_block_id 与 shared_state._block_id_from_dict 保持一致（前缀约定）"""
        self.assertTrue(_dedup_block_id(_ob("hello")).startswith("O:"))
        self.assertTrue(_dedup_block_id(UserInput(text="hi")).startswith("U:"))
        self.assertTrue(_dedup_block_id(PlanBlock(title="T", content="x")).startswith("PL:"))
        self.assertTrue(_dedup_block_id(SystemBlock(content="msg")).startswith("S:"))

    def test_content_hash_only_for_output_block(self):
        # 多行 OutputBlock → 有 hash（hash 首行之后的正文）
        self.assertNotEqual(_dedup_content_hash(_ob("first\nbody")), "")
        # 单行 OutputBlock → pass 2 不兜底，hash 为空
        self.assertEqual(_dedup_content_hash(_ob("single line")), "")
        # 非 OutputBlock → 完全跳过
        self.assertEqual(_dedup_content_hash(UserInput(text="hello")), "")
        self.assertEqual(_dedup_content_hash(PlanBlock(title="T", content="x")), "")


if __name__ == "__main__":
    unittest.main()
