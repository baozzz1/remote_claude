#!/usr/bin/env python3
"""会话结束时自动解散专属群 + 瞬时断连时自动 re-attach 的回归测试。

场景：
- server 退出（kill / PTY 自然退出 / server 崩溃）→ bridge 断 → 宽限期后解散群
- bridge 瞬时抖动断开但 session 仍活跃 → 宽限期后自动 re-attach（保留群 + 卡片继续更新）
- _ensure_bridge lazy attach 失败但 session 仍活跃 → 不能误解散（旧 bug 回归保护）
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from lark_client.lark_handler import LarkHandler


class _HandlerHarness:
    """最小化构造 LarkHandler，绕过真实 IO。"""

    def __init__(self, group_chat_id: str = "oc_group_1",
                 session_name: str = "dev-session"):
        self.group_chat_id = group_chat_id
        self.session_name = session_name
        self.handler = LarkHandler.__new__(LarkHandler)
        h = self.handler
        h._bridges = {}
        h._chat_sessions = {}
        h._detached_slices = {}
        h._chat_bindings = {group_chat_id: session_name}
        h._group_chat_ids = {group_chat_id}
        h._poller = MagicMock()
        h._poller.stop_and_get_active_slice = MagicMock(return_value=None)
        h._poller.stop = MagicMock()
        h._poller.start = MagicMock()
        h._save_chat_bindings = MagicMock()
        h._save_group_chat_ids = MagicMock()
        h._update_card_disconnected = AsyncMock()
        h._disband_group_via_api = AsyncMock(return_value=(True, ""))


class TestSessionEndDisband(unittest.IsolatedAsyncioTestCase):

    async def test_disband_fires_when_session_truly_gone(self):
        """session 确认不存在 → 绑定的专属群被解散"""
        harness = _HandlerHarness()
        h = harness.handler

        with patch("lark_client.lark_handler.is_session_active", return_value=False):
            await h._disband_or_reattach_after_disconnect(
                harness.group_chat_id, harness.session_name, grace_seconds=0
            )

        h._disband_group_via_api.assert_awaited_once_with(harness.group_chat_id)
        self.assertNotIn(harness.group_chat_id, h._group_chat_ids)
        self.assertNotIn(harness.group_chat_id, h._chat_bindings)

    async def test_reattach_fires_when_session_still_active(self):
        """session 仍活跃 → 自动 re-attach（不解散）"""
        harness = _HandlerHarness()
        h = harness.handler

        async def fake_attach(chat_id, session_name, user_id=None):
            h._bridges[chat_id] = MagicMock()
            return True

        with patch("lark_client.lark_handler.is_session_active", return_value=True), \
             patch.object(h, "_attach", new=AsyncMock(side_effect=fake_attach)) as mock_attach:
            await h._disband_or_reattach_after_disconnect(
                harness.group_chat_id, harness.session_name, grace_seconds=0
            )

        h._disband_group_via_api.assert_not_awaited()
        mock_attach.assert_awaited_once_with(harness.group_chat_id, harness.session_name)
        self.assertIn(harness.group_chat_id, h._group_chat_ids)

    async def test_no_reattach_when_chat_already_rebound(self):
        """宽限期内 chat_id 已被改绑到别的 session → 不能覆盖"""
        harness = _HandlerHarness()
        h = harness.handler
        # 模拟用户在宽限期内手动 attach 到了另一个 session
        h._chat_bindings[harness.group_chat_id] = "other-session"

        with patch("lark_client.lark_handler.is_session_active", return_value=True), \
             patch.object(h, "_attach", new=AsyncMock()) as mock_attach:
            await h._disband_or_reattach_after_disconnect(
                harness.group_chat_id, harness.session_name, grace_seconds=0
            )

        mock_attach.assert_not_called()
        h._disband_group_via_api.assert_not_awaited()

    async def test_no_reattach_when_bridge_already_present(self):
        """宽限期内已有新 bridge（用户主动 /attach 完成）→ 不重复 attach"""
        harness = _HandlerHarness()
        h = harness.handler
        h._bridges[harness.group_chat_id] = MagicMock()

        with patch("lark_client.lark_handler.is_session_active", return_value=True), \
             patch.object(h, "_attach", new=AsyncMock()) as mock_attach:
            await h._disband_or_reattach_after_disconnect(
                harness.group_chat_id, harness.session_name, grace_seconds=0
            )

        mock_attach.assert_not_called()

    async def test_no_disband_when_no_groups_bound(self):
        """没有任何专属群绑定到该 session → 静默跳过，不触发 API"""
        harness = _HandlerHarness()
        h = harness.handler
        h._chat_bindings.clear()
        h._group_chat_ids.clear()

        with patch("lark_client.lark_handler.is_session_active", return_value=False):
            await h._disband_or_reattach_after_disconnect(
                harness.group_chat_id, harness.session_name, grace_seconds=0
            )

        h._disband_group_via_api.assert_not_awaited()

    async def test_on_disconnect_schedules_decision_task(self):
        """_on_disconnect 应调度后台任务做"解散 / re-attach"分流决策"""
        harness = _HandlerHarness()
        h = harness.handler
        h._bridges[harness.group_chat_id] = MagicMock()
        h._chat_sessions[harness.group_chat_id] = harness.session_name

        with patch.object(h, "_disband_or_reattach_after_disconnect",
                          new=AsyncMock()) as mock_check:
            await h._on_disconnect(harness.group_chat_id, harness.session_name,
                                    disconnected_bridge=None)
            await asyncio.sleep(0)
            mock_check.assert_called_once_with(harness.group_chat_id,
                                                harness.session_name)

    async def test_on_disconnect_skips_when_bridge_replaced(self):
        """新 bridge 已替换旧实例 → 跳过清理与决策（防竞态）"""
        harness = _HandlerHarness()
        h = harness.handler
        new_bridge = MagicMock()
        old_bridge = MagicMock()
        h._bridges[harness.group_chat_id] = new_bridge

        with patch.object(h, "_disband_or_reattach_after_disconnect",
                          new=AsyncMock()) as mock_check:
            await h._on_disconnect(harness.group_chat_id, harness.session_name,
                                    disconnected_bridge=old_bridge)
            await asyncio.sleep(0)
            mock_check.assert_not_called()


class TestEnsureBridgeLazyDisbandGuard(unittest.IsolatedAsyncioTestCase):
    """_ensure_bridge lazy 路径：attach 失败但 session 仍活跃时不能误解散群（旧 bug 回归保护）"""

    async def test_lazy_attach_failure_with_active_session_keeps_binding(self):
        harness = _HandlerHarness()
        h = harness.handler

        with patch("lark_client.lark_handler.is_session_active", return_value=True), \
             patch.object(h, "_attach", new=AsyncMock(return_value=False)), \
             patch.object(h, "_disband_groups_for_session", new=AsyncMock()) as mock_disband:
            result = await h._ensure_bridge(harness.group_chat_id)

        self.assertIsNone(result)
        mock_disband.assert_not_called()
        # 绑定与 group_chat_ids 必须保留，否则群会"消失"
        self.assertIn(harness.group_chat_id, h._chat_bindings)
        self.assertIn(harness.group_chat_id, h._group_chat_ids)

    async def test_lazy_attach_failure_with_dead_session_disbands(self):
        harness = _HandlerHarness()
        h = harness.handler

        with patch("lark_client.lark_handler.is_session_active", return_value=False), \
             patch.object(h, "_attach", new=AsyncMock(return_value=False)), \
             patch.object(h, "_disband_groups_for_session", new=AsyncMock()) as mock_disband:
            result = await h._ensure_bridge(harness.group_chat_id)

        self.assertIsNone(result)
        mock_disband.assert_awaited_once_with(harness.session_name, source="lazy")
        self.assertNotIn(harness.group_chat_id, h._group_chat_ids)
        self.assertNotIn(harness.group_chat_id, h._chat_bindings)


if __name__ == "__main__":
    unittest.main()
