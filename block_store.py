"""用户屏蔽记录的持久化存储。

全局屏蔽（scope=``all``）对所有会话生效；群级屏蔽（scope=``group``）只对
对应群生效。记录以 JSON 保存，时间为 0 表示永久，否则为到期时间戳。
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

STORE_VERSION = 1
USER_ID_RE = re.compile(r"^[A-Za-z0-9]+$")

# 默认全局屏蔽用户与原因（与插件配置里的默认黑名单保持一致）
DEFAULT_REASONS: dict[str, str] = {
    "2854196310": "Q群管家",
    "2854197266": "AL_1S",
    "3889239358": "chaob",
    "3889001246": "幽幽子",
}


def default_reason(user_id: str) -> str:
    return DEFAULT_REASONS.get(str(user_id), "配置黑名单")


def parse_until(text: str) -> float | None:
    """解析屏蔽时长，返回绝对到期时间；0 表示永久；非法返回 None。

    支持：``0``/``永久``、``1d2h30m``、``30m``、``2h``、裸数字（按分钟）。
    """
    raw = str(text or "").strip().lower()
    if raw in {"0", "永久", "永久屏蔽", "forever", "perm", "none", ""}:
        return 0.0
    match = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", raw)
    if match and any(match.groups()):
        days, hours, minutes, seconds = (int(x or 0) for x in match.groups())
        total = days * 86400 + hours * 3600 + minutes * 60 + seconds
        return time.time() + total if total > 0 else 0.0
    if raw.isdigit():
        return time.time() + int(raw) * 60
    return None


def format_remaining(until: float) -> str:
    """把到期时间格式化为「永久 / 剩余 X」的易读文本。"""
    if not until:
        return "永久"
    remaining = float(until) - time.time()
    if remaining <= 0:
        return "已过期"
    total = int(remaining)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes and len(parts) < 2:
        parts.append(f"{minutes}分钟")
    if not parts:
        parts.append(f"{seconds}秒")
    return "剩余" + "".join(parts)


class BlockStore:
    """用户屏蔽记录的读写与查询。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._global: list[dict[str, Any]] = []
        self._groups: dict[str, list[dict[str, Any]]] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        raw_global = data.get("global")
        if isinstance(raw_global, list):
            self._global = [item for item in map(self._normalize, raw_global) if item]
        raw_groups = data.get("groups")
        if isinstance(raw_groups, dict):
            for group_id, entries in raw_groups.items():
                if not isinstance(entries, list):
                    continue
                cleaned = [item for item in map(self._normalize, entries) if item]
                if cleaned:
                    self._groups[str(group_id)] = cleaned

    @staticmethod
    def _normalize(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        user_id = str(item.get("user_id") or "").strip()
        if not USER_ID_RE.fullmatch(user_id):
            return None
        scope = str(item.get("scope") or "all").strip().lower()
        if scope not in {"all", "group"}:
            scope = "all"
        if scope == "group" and not str(item.get("group_id") or "").strip():
            return None
        try:
            until = float(item.get("until") or 0)
        except (TypeError, ValueError):
            until = 0.0
        return {
            "user_id": user_id,
            "reason": str(item.get("reason") or "").strip() or default_reason(user_id),
            "scope": scope,
            "group_id": str(item.get("group_id") or "").strip(),
            "until": until,
            "created_at": float(item.get("created_at") or time.time()),
            "source": str(item.get("source") or "page"),
        }

    def _save(self) -> None:
        payload = {
            "version": STORE_VERSION,
            "global": self._global,
            "groups": self._groups,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)

    @staticmethod
    def _active(entry: dict[str, Any]) -> bool:
        until = float(entry.get("until") or 0)
        return until == 0 or until > time.time()

    # ------------------------------------------------------------------ #
    # 配置同步
    # ------------------------------------------------------------------ #
    def sync_from_config(self, user_ids: tuple[str, ...] | list[str]) -> None:
        """让全局记录与插件配置的黑名单列表保持一致。"""
        wanted = [str(uid).strip() for uid in user_ids if str(uid).strip()]
        with self._lock:
            existing = {entry["user_id"]: entry for entry in self._global}
            changed = False
            for user_id in wanted:
                if user_id not in existing:
                    self._global.append(
                        {
                            "user_id": user_id,
                            "reason": default_reason(user_id),
                            "scope": "all",
                            "group_id": "",
                            "until": 0.0,
                            "created_at": time.time(),
                            "source": "config",
                        }
                    )
                    changed = True
            keep = set(wanted)
            new_global = [e for e in self._global if e["user_id"] in keep]
            if len(new_global) != len(self._global):
                changed = True
                self._global = new_global
            if changed:
                self._save()

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def global_entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._global if self._active(item)]

    def group_entries(self, group_id: str) -> list[dict[str, Any]]:
        group_id = str(group_id or "").strip()
        if not group_id:
            return []
        with self._lock:
            entries = self._groups.get(group_id, [])
            return [dict(item) for item in entries if self._active(item)]

    def effective_entries(self, group_id: str) -> list[dict[str, Any]]:
        entries = self.global_entries()
        entries.extend(self.group_entries(group_id))
        return entries

    def is_blocked(self, user_id: str, group_id: str) -> dict[str, Any] | None:
        user_id = str(user_id or "").strip()
        if not user_id:
            return None
        for entry in self.effective_entries(group_id):
            if entry["user_id"] == user_id:
                return entry
        return None

    # ------------------------------------------------------------------ #
    # 写入 / 删除
    # ------------------------------------------------------------------ #
    def add(
        self,
        user_id: str,
        *,
        reason: str = "",
        scope: str = "all",
        until: float = 0.0,
        group_id: str = "",
        source: str = "page",
    ) -> dict[str, Any]:
        user_id = str(user_id or "").strip()
        if not USER_ID_RE.fullmatch(user_id):
            raise ValueError("用户 ID 只能包含英文字母和数字")
        scope = "group" if str(scope).lower() == "group" else "all"
        group_id = str(group_id or "").strip()
        if scope == "group" and not group_id:
            raise ValueError("群级屏蔽缺少群号")
        entry = {
            "user_id": user_id,
            "reason": str(reason or "").strip() or default_reason(user_id),
            "scope": scope,
            "group_id": group_id if scope == "group" else "",
            "until": float(until or 0),
            "created_at": time.time(),
            "source": source,
        }
        with self._lock:
            if scope == "all":
                self._global = [e for e in self._global if e["user_id"] != user_id]
                self._global.append(entry)
            else:
                entries = [
                    e for e in self._groups.get(group_id, []) if e["user_id"] != user_id
                ]
                entries.append(entry)
                self._groups[group_id] = entries
            self._save()
        return dict(entry)

    def remove(self, user_id: str, *, scope: str = "all", group_id: str = "") -> bool:
        user_id = str(user_id or "").strip()
        scope = "group" if str(scope).lower() == "group" else "all"
        group_id = str(group_id or "").strip()
        removed = False
        with self._lock:
            if scope == "all":
                before = len(self._global)
                self._global = [e for e in self._global if e["user_id"] != user_id]
                removed = len(self._global) != before
            else:
                entries = self._groups.get(group_id, [])
                new_entries = [e for e in entries if e["user_id"] != user_id]
                removed = len(new_entries) != len(entries)
                if new_entries:
                    self._groups[group_id] = new_entries
                else:
                    self._groups.pop(group_id, None)
            if removed:
                self._save()
        return removed

    def remove_all_for_user(self, user_id: str) -> int:
        """删除某用户的全部记录（全局 + 群级），返回删除数量。"""
        user_id = str(user_id or "").strip()
        removed = 0
        with self._lock:
            before = len(self._global)
            self._global = [e for e in self._global if e["user_id"] != user_id]
            removed += before - len(self._global)
            for group_id in list(self._groups):
                entries = self._groups[group_id]
                new_entries = [e for e in entries if e["user_id"] != user_id]
                if len(new_entries) != len(entries):
                    removed += len(entries) - len(new_entries)
                    if new_entries:
                        self._groups[group_id] = new_entries
                    else:
                        self._groups.pop(group_id, None)
            if removed:
                self._save()
        return removed

    def flat_entries(self) -> list[dict[str, Any]]:
        """返回全部有效记录（供页面展示）。"""
        with self._lock:
            items = [dict(e) for e in self._global if self._active(e)]
            for group_id, entries in self._groups.items():
                for entry in entries:
                    if self._active(entry):
                        item = dict(entry)
                        item["group_id"] = group_id
                        items.append(item)
            return items
