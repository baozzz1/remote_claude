#!/usr/bin/env python3
"""avatar_uploader 单元测试（不触达飞书 API，只验证路径映射 + 缓存逻辑）"""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from lark_client import avatar_uploader


class TestIconPathMapping(unittest.TestCase):
    def test_known_cli_types_have_icons(self):
        for cli in ('claude', 'codex', 'agent'):
            p = avatar_uploader.icon_path_for(cli)
            self.assertIsNotNone(p, f"{cli} 未配置图标")
            self.assertTrue(p.exists(), f"{cli} 图标文件不存在: {p}")
            self.assertGreater(p.stat().st_size, 100,
                               f"{cli} 图标过小，可能损坏")

    def test_agent_uses_cursor_png(self):
        p = avatar_uploader.icon_path_for('agent')
        self.assertEqual(p.name, 'cursor.png')

    def test_unknown_cli_returns_none(self):
        self.assertIsNone(avatar_uploader.icon_path_for('unknown'))
        self.assertIsNone(avatar_uploader.icon_path_for(''))


class TestCacheLogic(unittest.TestCase):
    """验证 get_avatar_image_key 的缓存命中与 force 刷新。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cache_file = Path(self._tmp.name) / 'keys.json'
        self._patch = patch.object(avatar_uploader, '_cache_path',
                                    return_value=self._cache_file)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_cached_value_returned_without_upload(self):
        self._cache_file.write_text(json.dumps({'claude': 'cached_key_1'}))

        with patch.object(avatar_uploader, '_upload_sync',
                          side_effect=AssertionError('should not upload')):
            result = asyncio.run(avatar_uploader.get_avatar_image_key('claude'))
        self.assertEqual(result, 'cached_key_1')

    def test_miss_triggers_upload_and_caches(self):
        self._cache_file.write_text(json.dumps({}))

        with patch.object(avatar_uploader, '_upload_sync',
                          return_value='fresh_key_xyz') as mu:
            result = asyncio.run(avatar_uploader.get_avatar_image_key('codex'))

        self.assertEqual(result, 'fresh_key_xyz')
        mu.assert_called_once_with('codex')
        saved = json.loads(self._cache_file.read_text())
        self.assertEqual(saved.get('codex'), 'fresh_key_xyz')

    def test_force_bypasses_cache(self):
        self._cache_file.write_text(json.dumps({'agent': 'stale_key'}))

        with patch.object(avatar_uploader, '_upload_sync',
                          return_value='new_key') as mu:
            result = asyncio.run(
                avatar_uploader.get_avatar_image_key('agent', force=True)
            )

        self.assertEqual(result, 'new_key')
        mu.assert_called_once_with('agent')
        saved = json.loads(self._cache_file.read_text())
        self.assertEqual(saved.get('agent'), 'new_key')

    def test_upload_failure_returns_none_and_no_cache_write(self):
        self._cache_file.write_text(json.dumps({}))

        with patch.object(avatar_uploader, '_upload_sync', return_value=None):
            result = asyncio.run(avatar_uploader.get_avatar_image_key('claude'))

        self.assertIsNone(result)
        # 缓存没有写入失败结果
        saved = json.loads(self._cache_file.read_text())
        self.assertNotIn('claude', saved)


class TestRefreshAll(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cache_file = Path(self._tmp.name) / 'keys.json'
        self._cache_file.write_text('{}')
        self._patch = patch.object(avatar_uploader, '_cache_path',
                                    return_value=self._cache_file)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_refresh_all_uploads_every_cli_type(self):
        calls = []

        def _fake_upload(cli_type):
            calls.append(cli_type)
            return f'key_for_{cli_type}'

        with patch.object(avatar_uploader, '_upload_sync',
                          side_effect=_fake_upload):
            result = asyncio.run(avatar_uploader.refresh_all())

        self.assertEqual(set(calls), {'claude', 'codex', 'agent'})
        self.assertEqual(result['claude'], 'key_for_claude')
        self.assertEqual(result['codex'], 'key_for_codex')
        self.assertEqual(result['agent'], 'key_for_agent')


class TestCliTypeFromGroupName(unittest.TestCase):
    """校验从群名前缀推断 cli_type（会话结束后的回退路径）"""

    def test_claude_prefix(self):
        self.assertEqual(
            avatar_uploader.cli_type_from_group_name('[claude] path session'),
            'claude'
        )

    def test_codex_prefix(self):
        self.assertEqual(
            avatar_uploader.cli_type_from_group_name('[codex] some/path abc'),
            'codex'
        )

    def test_cursor_prefix_maps_to_agent(self):
        """[cursor] 是群名里的 label，cli_type 应该是 agent"""
        self.assertEqual(
            avatar_uploader.cli_type_from_group_name('[cursor] foo bar'),
            'agent'
        )

    def test_case_insensitive(self):
        self.assertEqual(
            avatar_uploader.cli_type_from_group_name('[Codex] X Y'),
            'codex'
        )

    def test_no_prefix_returns_none(self):
        self.assertIsNone(avatar_uploader.cli_type_from_group_name('some plain name'))
        self.assertIsNone(avatar_uploader.cli_type_from_group_name(''))
        self.assertIsNone(avatar_uploader.cli_type_from_group_name(None))

    def test_unknown_label_returns_none(self):
        self.assertIsNone(
            avatar_uploader.cli_type_from_group_name('[gemini] path session')
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
