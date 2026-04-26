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

# Claude Code / Codex CLI 的 session UUID：8-4-4-4-12 hex，无可读含义
# 这种值会通过 `--resume <UUID>` 进入 resume_target，不应作为群名显示
_RESUME_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _is_resume_uuid(value: str) -> bool:
    if not value:
        return False
    return bool(_RESUME_UUID_RE.match(value.strip()))


def _strip_auto_suffix(name: str) -> str:
    """剥离 `_MMDD_HHMMSS` 自动时间戳后缀（会话 ID 的尾巴）。"""
    if not name:
        return ""
    return _AUTO_SESSION_SUFFIX_RE.sub("", name)


def group_path_label(cwd: Optional[str], session_name: str) -> str:
    """群名中的路径标签：优先取 cwd 最后一段，缺失时回退到 session_name 的 basename 并剥离时间戳后缀。

    背景：cwd 可能因进程已退出等原因取不到；此时若直接用 session_name basename，
    `/Users/.../remote_claude_0423_170635` 会把 `remote_claude_0423_170635`（带 session id）
    塞进群名里。回退时剥掉 `_MMDD_HHMMSS` 后缀能得到干净的 `remote_claude`。
    """
    raw = (cwd or "").rstrip("/")
    if raw:
        label = os.path.basename(raw)
        if label:
            return label
    basename = _session_basename(session_name)
    if basename:
        cleaned = _strip_auto_suffix(basename)
        if cleaned:
            return cleaned
    return "session"


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
    """从 `MM-DD HH:MM` / `YYYY-MM-DD HH:MM(:SS)` 中提取"日期 + 小时"精度的标签。

    输出形如 `MM-DD HH`（24h），用作群名降级时的短 disambiguator。
    拿到的是 `04-23 17:06` → 返回 `04-23 17`；只有日期时原样返回。
    """
    raw = (start_time or "").strip()
    if not raw:
        return ""
    if " " not in raw:
        return raw
    date_part, _, time_part = raw.partition(" ")
    hour = time_part.split(":", 1)[0].strip()
    if hour:
        return f"{date_part} {hour}"
    return date_part


def group_session_label(session_name: str, path_label: str = "",
                        resume_target: str = "", start_time: str = "") -> str:
    """群名中的会话标签：优先显示可读的 resume 名，再显示有效会话名，否则回退到会话创建日期。

    resume_target 若是 Claude Code / Codex 的内部 session UUID（8-4-4-4-12 hex），
    没有人类可读含义，跳过不用，避免 `[claude] remote_claude 2c10ebf1-209b-...` 这种群名。
    """
    resume_name = _session_basename(resume_target)
    if resume_name and not _is_resume_uuid(resume_name):
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
    """构造飞书专属群名称：[cli] 路径末段 [name-or-date]。

    用户给的 name 与路径末段相同（如 `cla` 把 cwd basename 当 session_name）时，
    合并成单字段 `[cli] name` 避免 `[claude] mywork mywork` 这样的重复。
    """
    cli_label = _GROUP_NAME_CLI_LABELS.get(cli_type, _GROUP_NAME_CLI_LABELS["claude"])
    path_label = group_path_label(cwd, session_name)
    session_label = group_session_label(session_name, path_label, resume_target, start_time)
    if session_label and session_label != path_label:
        return f"[{cli_label}] {path_label} {session_label}"
    return f"[{cli_label}] {path_label}"
