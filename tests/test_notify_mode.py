"""shared_memory_poller 通知模式回归测试

覆盖评审 P2：在 5m/15m/30m 冷却模式下，新任务开始（ready→not_ready）不应重置
`last_notify_ts`，否则跨任务冷却会失效 —— 短任务连跑会每次都立刻 @ 人。
"""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from lark_client import shared_memory_poller as smp
from lark_client.shared_memory_poller import (
    SharedMemoryPoller, StreamTracker, _should_notify_for_mode,
)


class _ModeCtx:
    """上下文管理：临时切换模块级 _notify_mode，测试后恢复"""
    def __init__(self, mode):
        self.mode = mode

    def __enter__(self):
        self._prev = smp._notify_mode
        smp._notify_mode = self.mode

    def __exit__(self, *exc):
        smp._notify_mode = self._prev


def _make_poller():
    return SharedMemoryPoller(card_service=MagicMock())


def _tracker(prev_ready=True, last_ts=0.0):
    t = StreamTracker(chat_id="cid", session_name="s", is_group=True)
    t.prev_is_ready = prev_ready
    t.last_notify_ts = last_ts
    return t


class TestShouldNotifyForMode(unittest.TestCase):
    def test_off_never(self):
        self.assertFalse(_should_notify_for_mode("off", 0.0, 1000.0))

    def test_once_first_time_yes(self):
        self.assertTrue(_should_notify_for_mode("once", 0.0, 1000.0))

    def test_once_second_time_no(self):
        self.assertFalse(_should_notify_for_mode("once", 900.0, 1000.0))

    def test_interval_within_cooldown(self):
        self.assertFalse(_should_notify_for_mode("5m", 900.0, 1000.0))

    def test_interval_after_cooldown(self):
        self.assertTrue(_should_notify_for_mode("5m", 500.0, 1000.0))


class TestOnceModeResetsOnNewTask(unittest.TestCase):
    """once 模式：ready→not_ready 时必须重置 last_notify_ts，否则下一个任务完成不会再通知"""

    def test_reset_on_ready_to_not_ready(self):
        with _ModeCtx("once"):
            poller = _make_poller()
            t = _tracker(prev_ready=True, last_ts=time.time())  # 刚通知过
            # 模拟 ready → not_ready（新任务开始）
            poller._update_ready_state(t, blocks=[{"is_streaming": True}],
                                        status_line=None, option_block=None)
            self.assertEqual(t.last_notify_ts, 0.0,
                             "once 模式下新任务开始必须重置 last_notify_ts")


class TestIntervalModeDoesNotResetOnNewTask(unittest.TestCase):
    """5m/15m/30m 模式：ready→not_ready 时绝不能清零 last_notify_ts，
    否则跨任务冷却失效（评审 P2）"""

    def _run(self, mode):
        with _ModeCtx(mode):
            poller = _make_poller()
            original_ts = time.time() - 60  # 1 分钟前通知过
            t = _tracker(prev_ready=True, last_ts=original_ts)
            # 模拟 ready → not_ready（新任务开始）
            poller._update_ready_state(t, blocks=[{"is_streaming": True}],
                                        status_line=None, option_block=None)
            self.assertAlmostEqual(
                t.last_notify_ts, original_ts, places=3,
                msg=f"{mode} 模式下新任务开始不应重置 last_notify_ts，"
                    f"否则冷却永远不会生效"
            )

    def test_5m(self): self._run("5m")
    def test_15m(self): self._run("15m")
    def test_30m(self): self._run("30m")


class TestIntervalCooldownStaysEffectiveAcrossTasks(unittest.TestCase):
    """端到端：5m 模式下，快速跑完两个任务，第二个任务完成不应立刻通知"""

    def test_back_to_back_short_tasks(self):
        with _ModeCtx("5m"):
            poller = _make_poller()
            t = _tracker(prev_ready=True, last_ts=time.time() - 60)  # 1 分钟前通知过

            # Task 1 开始：ready → not_ready（不该清零）
            poller._update_ready_state(t, blocks=[{"is_streaming": True}],
                                        status_line=None, option_block=None)
            # Task 1 完成：not_ready → ready
            should_notify = poller._update_ready_state(
                t, blocks=[], status_line=None, option_block=None
            )
            self.assertFalse(
                should_notify,
                "5m 模式下距上次通知仅 1 分钟，第二次 ready 必须被冷却压住"
            )

    def test_after_cooldown_elapsed(self):
        """冷却时间到了之后，ready 应该能触发通知"""
        with _ModeCtx("5m"):
            poller = _make_poller()
            t = _tracker(prev_ready=False, last_ts=time.time() - 600)  # 10 分钟前

            # not_ready → ready
            should_notify = poller._update_ready_state(
                t, blocks=[], status_line=None, option_block=None
            )
            self.assertTrue(should_notify, "5m 模式下 10 分钟前的 last_ts，冷却已过")


if __name__ == "__main__":
    unittest.main()
