#!/usr/bin/env python3
"""会话 metadata 文件（.meta.json）的读写与 list_active_sessions 集成测试。

metadata 方案目的：cmd_start 阶段写入 {cli_type, resume_target, cwd, start_time}，
list_active_sessions 优先读这个文件，避免 PTY 首帧尚未写入 .mq 时 cli_type 被默认成
'claude' 的竞态（Review P2）。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import session as _sm


class TestWriteReadRoundtrip(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._p = patch.object(_sm, "SOCKET_DIR", Path(self._tmp.name))
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def test_write_then_read_full_fields(self):
        _sm.write_session_metadata(
            session_name="/tmp/foo/demo_0424_180000",
            cli_type="codex",
            resume_target="deadbeef-1234-5678-9abc-def012345678",
            cwd="/tmp/foo/demo",
            start_time="04-24 18:00",
        )
        safe = _sm._safe_filename("/tmp/foo/demo_0424_180000")
        meta = _sm._read_session_metadata_by_safe_name(safe)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["cli_type"], "codex")
        self.assertEqual(meta["resume_target"], "deadbeef-1234-5678-9abc-def012345678")
        self.assertEqual(meta["cwd"], "/tmp/foo/demo")
        self.assertEqual(meta["start_time"], "04-24 18:00")

    def test_write_is_atomic_no_partial_reads(self):
        """write 用 tmp + rename，读者永远看到完整 JSON，不会读到半写状态。"""
        _sm.write_session_metadata(
            session_name="name1", cli_type="claude", resume_target="",
            cwd="/x", start_time="04-24 00:00",
        )
        safe = _sm._safe_filename("name1")
        meta_path = Path(self._tmp.name) / f"{safe}.meta.json"
        self.assertTrue(meta_path.exists())
        # tmp 文件不应残留
        self.assertFalse(meta_path.with_suffix(meta_path.suffix + ".tmp").exists())
        data = json.loads(meta_path.read_text())
        self.assertEqual(data["cli_type"], "claude")

    def test_read_missing_file_returns_none(self):
        self.assertIsNone(_sm._read_session_metadata_by_safe_name("nonexistent-safe-name"))

    def test_read_corrupt_file_returns_none(self):
        safe = _sm._safe_filename("corrupt")
        bad = Path(self._tmp.name) / f"{safe}.meta.json"
        bad.write_text("{not valid json")
        self.assertIsNone(_sm._read_session_metadata_by_safe_name(safe))

    def test_write_file_permissions(self):
        """meta.json 写入权限应为 0600（resume_target 可能含敏感信息）"""
        _sm.write_session_metadata(
            session_name="perm-test", cli_type="claude",
            resume_target="", cwd="/x", start_time="",
        )
        safe = _sm._safe_filename("perm-test")
        p = Path(self._tmp.name) / f"{safe}.meta.json"
        mode = p.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600, f"expected 0o600, got {oct(mode)}")


class TestCleanupRemovesMetaFile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._p = patch.object(_sm, "SOCKET_DIR", Path(self._tmp.name))
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def test_cleanup_removes_meta(self):
        _sm.write_session_metadata(
            session_name="to-cleanup", cli_type="claude",
            resume_target="", cwd="/x", start_time="",
        )
        safe = _sm._safe_filename("to-cleanup")
        meta_path = Path(self._tmp.name) / f"{safe}.meta.json"
        self.assertTrue(meta_path.exists())

        _sm._cleanup_by_safe_name(safe, "to-cleanup")
        self.assertFalse(meta_path.exists())


class TestListActiveSessionsPrefersMeta(unittest.TestCase):
    """list_active_sessions 应优先读 .meta.json，而非 .mq 快照的 cli_type 默认值。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._p = patch.object(_sm, "SOCKET_DIR", Path(self._tmp.name))
        self._p.start()
        self._dir = Path(self._tmp.name)

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def _fake_session(self, name, cli_type, resume_target=""):
        """伪造一个 "运行中" 的会话：sock + pid（指向自己）+ name + meta"""
        safe = _sm._safe_filename(name)
        (self._dir / f"{safe}.sock").touch()
        (self._dir / f"{safe}.pid").write_text(str(os.getpid()))
        (self._dir / f"{safe}.name").write_text(name)
        _sm.write_session_metadata(
            session_name=name, cli_type=cli_type,
            resume_target=resume_target, cwd="", start_time="",
        )
        return safe

    def test_cli_type_from_meta_wins_over_mq_default(self):
        """即便 .mq 不存在（首帧未到），meta 里的 cli_type 也必须胜出。"""
        self._fake_session("/tmp/my-codex-sess", cli_type="codex")
        self._fake_session("/tmp/my-agent-sess", cli_type="agent",
                           resume_target="foo-resume")

        # 不创建 .mq 文件 → 模拟 PTY 首帧之前
        sessions = {s["name"]: s for s in _sm.list_active_sessions()}
        self.assertEqual(sessions["/tmp/my-codex-sess"]["cli_type"], "codex",
                         "codex 会话不能被默认回 claude")
        self.assertEqual(sessions["/tmp/my-agent-sess"]["cli_type"], "agent")
        self.assertEqual(sessions["/tmp/my-agent-sess"]["resume_target"], "foo-resume")


if __name__ == "__main__":
    unittest.main(verbosity=2)
