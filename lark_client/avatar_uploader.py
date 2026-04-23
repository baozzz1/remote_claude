"""飞书群头像上传与缓存

为不同 CLI 类型的专属群设置对应的品牌头像：
  - claude  → lark_client/assets/icons/claude.png
  - codex   → lark_client/assets/icons/codex.png
  - agent   → lark_client/assets/icons/cursor.png（Cursor Agent）

流程：
  1. 读取本地 PNG
  2. POST /open-apis/im/v1/images  (image_type=avatar) → image_key
  3. 缓存 image_key 到 ~/.remote-claude/lark_avatar_keys.json（一次上传长期复用）

上传后的 image_key 在 create chat / update chat 的 body 里作为 `avatar` 字段使用。
同一张图上传多次也没关系，飞书侧会去重（返回同一个 key）。
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Optional

import re

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateImageRequest, CreateImageRequestBody,
    UpdateChatRequest, UpdateChatRequestBody,
    GetChatRequest,
)

from . import config

logger = logging.getLogger('AvatarUploader')


# cli_type → 本地图标文件名（注意：agent 使用 cursor.png）
_ICON_FILE_BY_CLI: Dict[str, str] = {
    'claude': 'claude.png',
    'codex':  'codex.png',
    'agent':  'cursor.png',
}

# 群名首部 [cli_label] → cli_type（与 group_naming._GROUP_NAME_CLI_LABELS 保持一致）
_GROUP_NAME_PREFIX_RE = re.compile(r'^\s*\[(?P<label>[a-zA-Z]+)\]')
_LABEL_TO_CLI_TYPE: Dict[str, str] = {
    'claude': 'claude',
    'codex':  'codex',
    'cursor': 'agent',
}


def _icons_dir() -> Path:
    return Path(__file__).parent / 'assets' / 'icons'


def icon_path_for(cli_type: str) -> Optional[Path]:
    """返回 cli_type 对应图标的本地绝对路径；不支持的类型返回 None。"""
    name = _ICON_FILE_BY_CLI.get(cli_type)
    if not name:
        return None
    p = _icons_dir() / name
    return p if p.exists() else None


def _cache_path() -> Path:
    return Path.home() / '.remote-claude' / 'lark_avatar_keys.json'


def _load_cache() -> Dict[str, str]:
    try:
        return json.loads(_cache_path().read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_cache(data: Dict[str, str]) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding='utf-8')
    except Exception as e:
        logger.warning(f"保存头像缓存失败: {e}")


_client_singleton: Optional[lark.Client] = None


def _get_client() -> Optional[lark.Client]:
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton
    if not (config.FEISHU_APP_ID and config.FEISHU_APP_SECRET):
        return None
    _client_singleton = (
        lark.Client.builder()
        .app_id(config.FEISHU_APP_ID)
        .app_secret(config.FEISHU_APP_SECRET)
        .build()
    )
    return _client_singleton


def _upload_sync(cli_type: str) -> Optional[str]:
    """同步上传（在 executor 里调，避免阻塞 event loop）。"""
    client = _get_client()
    if client is None:
        logger.warning("lark client 未初始化（FEISHU_APP_ID/SECRET 未配置）")
        return None

    icon = icon_path_for(cli_type)
    if icon is None:
        logger.warning(f"未找到 {cli_type!r} 对应图标文件")
        return None

    try:
        with open(icon, 'rb') as f:
            body = (
                CreateImageRequestBody.builder()
                .image_type('avatar')
                .image(f)
                .build()
            )
            req = CreateImageRequest.builder().request_body(body).build()
            resp = client.im.v1.image.create(req)
    except Exception as e:
        logger.error(f"上传 {cli_type} 头像异常: {e}")
        return None

    if not resp.success():
        logger.error(
            f"上传 {cli_type} 头像失败: code={resp.code} msg={resp.msg}"
        )
        return None

    image_key = getattr(resp.data, 'image_key', None)
    if not image_key:
        logger.error(f"上传 {cli_type} 头像响应缺少 image_key: {resp.data}")
        return None
    return image_key


async def get_avatar_image_key(cli_type: str, force: bool = False) -> Optional[str]:
    """获取 cli_type 对应的头像 image_key。

    - force=False（默认）：优先读缓存，未命中才上传
    - force=True：无条件重新上传（刷新图标后调用）
    """
    cache = _load_cache()
    if not force:
        cached = cache.get(cli_type)
        if cached:
            return cached

    loop = asyncio.get_event_loop()
    key = await loop.run_in_executor(None, _upload_sync, cli_type)
    if not key:
        return None

    cache[cli_type] = key
    _save_cache(cache)
    return key


def _get_chat_name_sync(chat_id: str) -> Optional[str]:
    """GET /im/v1/chats/{chat_id} 取群名；失败返回 None"""
    client = _get_client()
    if client is None:
        return None
    try:
        req = GetChatRequest.builder().chat_id(chat_id).build()
        resp = client.im.v1.chat.get(req)
    except Exception as e:
        logger.warning(f"GET chat {chat_id[:12]}… 失败: {e}")
        return None
    if not resp.success():
        logger.warning(f"GET chat {chat_id[:12]}… failed: code={resp.code} msg={resp.msg}")
        return None
    return getattr(resp.data, 'name', None)


def cli_type_from_group_name(name: str) -> Optional[str]:
    """从群名首部 `[claude] …` / `[codex] …` / `[cursor] …` 推断 cli_type"""
    if not name:
        return None
    m = _GROUP_NAME_PREFIX_RE.match(name)
    if not m:
        return None
    return _LABEL_TO_CLI_TYPE.get(m.group('label').lower())


async def infer_cli_type_from_chat(chat_id: str) -> Optional[str]:
    """当会话已结束、无法从 list_active_sessions 取 cli_type 时，
    通过 Lark API 取群名并解析 `[cli]` 前缀。"""
    loop = asyncio.get_event_loop()
    name = await loop.run_in_executor(None, _get_chat_name_sync, chat_id)
    return cli_type_from_group_name(name or '')


def _update_chat_avatar_sync(chat_id: str, avatar_key: str) -> tuple:
    """同步更新群头像，返回 (ok: bool, err_msg: str)"""
    client = _get_client()
    if client is None:
        return False, "lark client 未初始化"
    try:
        body = UpdateChatRequestBody.builder().avatar(avatar_key).build()
        req = UpdateChatRequest.builder().chat_id(chat_id).request_body(body).build()
        resp = client.im.v1.chat.update(req)
    except Exception as e:
        return False, str(e)
    if not resp.success():
        return False, f"code={resp.code} msg={resp.msg}"
    return True, ""


async def update_chat_avatar(chat_id: str, avatar_key: str) -> tuple:
    """异步封装：PUT /im/v1/chats/{chat_id} 写 avatar，返回 (ok, err_msg)"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _update_chat_avatar_sync, chat_id, avatar_key)


async def refresh_all() -> Dict[str, Optional[str]]:
    """强制重新上传全部支持的 cli_type 头像，返回 {cli_type: image_key or None}。"""
    result: Dict[str, Optional[str]] = {}
    for cli_type in _ICON_FILE_BY_CLI:
        result[cli_type] = await get_avatar_image_key(cli_type, force=True)
    return result
