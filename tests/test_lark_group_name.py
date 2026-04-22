#!/usr/bin/env python3
"""飞书专属群命名规则测试。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lark_client.group_naming import build_group_chat_name


class TestLarkGroupChatName(unittest.TestCase):

    def test_build_group_name_uses_cli_label_and_path_basename(self):
        self.assertEqual(
            build_group_chat_name(
                cli_type="claude",
                cwd="/Users/test/dev/remote_claude",
                session_name="fix-dedup",
            ),
            "[claude] remote_claude fix-dedup",
        )
        self.assertEqual(
            build_group_chat_name(
                cli_type="codex",
                cwd="/tmp/worktree/demo",
                session_name="session-x",
            ),
            "[codex] demo session-x",
        )
        self.assertEqual(
            build_group_chat_name(
                cli_type="agent",
                cwd="/tmp/worktree/demo",
                session_name="session-y",
            ),
            "[cursor] demo session-y",
        )
        self.assertEqual(
            build_group_chat_name(
                cli_type="claude",
                cwd="/Users/test/dev/remote_claude",
                session_name="/Users/test/dev/remote_claude_0423_000938",
                resume_target="remote-claude-dev",
            ),
            "[claude] remote_claude remote-claude-dev",
        )

    def test_build_group_name_falls_back_to_session_date(self):
        self.assertEqual(
            build_group_chat_name(
                cli_type="claude",
                cwd="/tmp/project/demo",
                session_name="",
                start_time="04-23 00:09",
            ),
            "[claude] demo 04-23",
        )

    def test_build_group_name_ignores_path_like_auto_session_name(self):
        self.assertEqual(
            build_group_chat_name(
                cli_type="codex",
                cwd="/Users/test/dev/remote_claude",
                session_name="/Users/test/dev/remote_claude_0423_004137",
                start_time="04-23 00:41",
            ),
            "[codex] remote_claude 04-23",
        )


if __name__ == "__main__":
    unittest.main()
