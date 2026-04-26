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
            "[claude] demo 04-23 00",
        )

    def test_build_group_name_ignores_path_like_auto_session_name(self):
        self.assertEqual(
            build_group_chat_name(
                cli_type="codex",
                cwd="/Users/test/dev/remote_claude",
                session_name="/Users/test/dev/remote_claude_0423_004137",
                start_time="04-23 00:41",
            ),
            "[codex] remote_claude 04-23 00",
        )

    def test_build_group_name_cwd_none_strips_auto_suffix(self):
        """cwd 取不到时，path_label 从 session_name 推导，必须剥离 `_MMDD_HHMMSS` 后缀，
        否则会出现 `[cli] remote_claude_0423_170635 …` 这种带 session id 的群名。"""
        self.assertEqual(
            build_group_chat_name(
                cli_type="codex",
                cwd=None,
                session_name="/Users/test/dev/remote_claude_0423_170635",
                start_time="04-23 17:06",
            ),
            "[codex] remote_claude 04-23 17",
        )
        self.assertEqual(
            build_group_chat_name(
                cli_type="agent",
                cwd=None,
                session_name="/Users/test/dev/disk_clean_0423_184953",
                start_time="04-23 18:49",
            ),
            "[cursor] disk_clean 04-23 18",
        )

    def test_build_group_name_uuid_resume_target_falls_back_to_date(self):
        """Claude/Codex `--resume <UUID>` 的 UUID 不具可读性，
        不应当作为群名的 session_label；应降级到创建时间。"""
        for uuid in (
            "2c10ebf1-209b-4b04-9d0f-39c617627e57",
            "11167546-7cbf-4263-ba12-ce346b09c259",
            "2C10EBF1-209B-4B04-9D0F-39C617627E57",  # 大写也应识别
        ):
            self.assertEqual(
                build_group_chat_name(
                    cli_type="claude",
                    cwd="/Users/test/dev/remote_claude",
                    session_name="/Users/test/dev/remote_claude_0423_185016",
                    resume_target=uuid,
                    start_time="04-23 18:50",
                ),
                "[claude] remote_claude 04-23 18",
                f"UUID resume should be filtered: {uuid}",
            )

    def test_build_group_name_human_resume_target_preserved(self):
        """非 UUID 的 resume_target（用户自定义名）仍应作为 session_label。"""
        self.assertEqual(
            build_group_chat_name(
                cli_type="claude",
                cwd="/Users/test/dev/remote_claude",
                session_name="/Users/test/dev/remote_claude_0423_000938",
                resume_target="remote-claude-dev",
                start_time="04-23 00:09",
            ),
            "[claude] remote_claude remote-claude-dev",
        )

    def test_build_group_name_dedupes_path_and_session(self):
        """path_label 与 session_label 相同时只保留一个，避免 `[claude] mywork mywork`。"""
        self.assertEqual(
            build_group_chat_name(
                cli_type="claude",
                cwd=None,
                session_name="mywork",
            ),
            "[claude] mywork",
        )
        self.assertEqual(
            build_group_chat_name(
                cli_type="claude",
                cwd="/Users/test/dev/remote_claude",
                session_name="remote_claude",
            ),
            "[claude] remote_claude",
        )


if __name__ == "__main__":
    unittest.main()
