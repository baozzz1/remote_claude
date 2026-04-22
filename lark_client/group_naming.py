"""飞书专属群命名规则。"""

import os
import re
from typing import Dict, Optional


_GROUP_NAME_CLI_LABELS: Dict[str, str] = {
    "claude": "claude",
    "codex": "codex",
    "agent": "cursor",
}

_AUTO_SESSION_SUFFIX_RE = re.compile(r"_\d{4}_\d{6}$")


def group_path_label(cwd: Optional[str], session_name: str) -> str:
    """群名中的路径标签：优先取 cwd 最后一段，缺失时回退到会话名。"""
    raw = (cwd or "").rstrip("/")
    if raw:
        label = os.path.basename(raw)
        if label:
            return label
    return _session_basename(session_name) or "session"


def _session_basename(session_name: str) -> str:
    """提取会话名的可读 basename，避免绝对路径直接泄露到群名里。"""
    name = (session_name or "").strip().rstrip("/")
    if not name:
        return ""
    return os.path.basename(name) or name


def _is_auto_session_name(name: str, path_label: str) -> bool:
    """判断是否为从路径自动生成的会话名，如 `remote_claude_0423_004137`。"""
    if not name:
        return False
    if not _AUTO_SESSION_SUFFIX_RE.search(name):
        return False
    if not path_label:
        return True
    return name == path_label or name.startswith(path_label + "_")


def _session_date_label(start_time: str) -> str:
    """从 `MM-DD HH:MM` / `YYYY-MM-DD HH:MM(:SS)` 中提取日期部分。"""
    raw = (start_time or "").strip()
    if not raw:
        return ""
    if " " in raw:
        return raw.split(" ", 1)[0]
    return raw


def group_session_label(session_name: str, path_label: str = "",
                        resume_target: str = "", start_time: str = "") -> str:
    """群名中的会话标签：优先显示 resume 名，再显示有效会话名，否则回退到会话创建日期。"""
    resume_name = _session_basename(resume_target)
    if resume_name:
        return resume_name
    name = _session_basename(session_name)
    if name and not _is_auto_session_name(name, path_label):
        return name
    date_label = _session_date_label(start_time)
    if date_label:
        return date_label
    return "sess"


def build_group_chat_name(cli_type: str, cwd: Optional[str], session_name: str,
                          resume_target: str = "", start_time: str = "") -> str:
    """构造飞书专属群名称：[cli] 路径末段 会话名/创建日期。"""
    cli_label = _GROUP_NAME_CLI_LABELS.get(cli_type, _GROUP_NAME_CLI_LABELS["claude"])
    path_label = group_path_label(cwd, session_name)
    session_label = group_session_label(session_name, path_label, resume_target, start_time)
    return f"[{cli_label}] {path_label} {session_label}"
