#!/usr/bin/env python3
"""会话结束时自动解散专属群的回归测试。

场景：server 退出（kill / PTY 自然退出 / server 崩溃）→ lark daemon bridge 断连
→ _on_disconnect → 延迟一段宽限期后若 session 仍未活跃，解散所有绑定的专属群。
"""

import asyncio
import sys
import unittest
from pathlib import Path
from typing import List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from lark_client.lark_handler import LarkHandler


class _HandlerHarness:
    """最小化构造 LarkHandler，绕过真实 IO。"""

    def __init__(self, group_chat_id: str = "oc_group_1",
                 session_name: str = "dev-session"):
        self.group_chat_id = group_chat_id
        self.session_name = session_name
        # LarkHandler.__init__ 会触发 ensure_user_data_dir + 读绑定文件；
        # 我们用 __new__ 绕过，手动装配最小字段。
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
            await h._disband_if_session_gone(harness.session_name, grace_seconds=0)

        h._disband_group_via_api.assert_awaited_once_with(harness.group_chat_id)
        self.assertNotIn(harness.group_chat_id, h._group_chat_ids)
        self.assertNotIn(harness.group_chat_id, h._chat_bindings)

    async def test_no_disband_when_session_still_active(self):
        """session 仍活跃（网络抖动/临时断连）→ 不应误解散"""
        harness = _HandlerHarness()
        h = harness.handler

        with patch("lark_client.lark_handler.is_session_active", return_value=True):
            await h._disband_if_session_gone(harness.session_name, grace_seconds=0)

        h._disband_group_via_api.assert_not_awaited()
        self.assertIn(harness.group_chat_id, h._group_chat_ids)
        self.assertEqual(h._chat_bindings.get(harness.group_chat_id),
                         harness.session_name)

    async def test_no_disband_when_no_groups_bound(self):
        """没有任何专属群绑定到该 session → 静默跳过，不触发 API"""
        harness = _HandlerHarness()
        h = harness.handler
        h._chat_bindings.clear()
        h._group_chat_ids.clear()

        with patch("lark_client.lark_handler.is_session_active", return_value=False):
            await h._disband_if_session_gone(harness.session_name, grace_seconds=0)

        h._disband_group_via_api.assert_not_awaited()

    async def test_on_disconnect_schedules_disband_task(self):
        """_on_disconnect 应调度后台任务去检查 session 是否真的结束"""
        harness = _HandlerHarness()
        h = harness.handler

        # 模拟 bridge 状态：_on_disconnect 会从 _bridges 中弹出
        h._bridges[harness.group_chat_id] = MagicMock()
        h._chat_sessions[harness.group_chat_id] = harness.session_name

        with patch.object(h, "_disband_if_session_gone",
                          new=AsyncMock()) as mock_check:
            await h._on_disconnect(harness.group_chat_id, harness.session_name,
                                    disconnected_bridge=None)
            # 允许事件循环调度 create_task 产生的协程
            await asyncio.sleep(0)
            mock_check.assert_called_once_with(harness.session_name)

    async def test_on_disconnect_skips_when_bridge_replaced(self):
        """新 bridge 已替换旧实例 → 跳过清理与解散检查（防竞态）"""
        harness = _HandlerHarness()
        h = harness.handler
        new_bridge = MagicMock()
        old_bridge = MagicMock()
        h._bridges[harness.group_chat_id] = new_bridge

        with patch.object(h, "_disband_if_session_gone",
                          new=AsyncMock()) as mock_check:
            await h._on_disconnect(harness.group_chat_id, harness.session_name,
                                    disconnected_bridge=old_bridge)
            await asyncio.sleep(0)
            mock_check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
