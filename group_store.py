"""每个群的独立配置存储。

覆盖项只包含"群内行为"相关的字段（主动回复 / 群聊上下文 / 群聊回复增强），
联网搜索、记忆 RAG、超时等属于全局服务配置，不在群级覆盖范围内。

数据保存在 ``data/plugin_data/astrbot_plugin_active_reply/groups.json``，
与 AstrBot 的插件配置分开，避免 Schema 变更时丢失群配置。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .config_model import (
    GROUP_OVERRIDE_FIELDS,
    coerce_group_value,
    get_by_path,
    set_by_path,
)

STORE_VERSION = 1


class GroupStore:
    """按群保存独立配置覆盖项。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # 读写
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if not self.path.exists():
            self._data = {}
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._data = {}
            return
        groups = payload.get("groups") if isinstance(payload, dict) else None
        self._data = groups if isinstance(groups, dict) else {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": STORE_VERSION, "groups": self._data}
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(self.path)

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def is_independent(self, group_id: str) -> bool:
        entry = self._data.get(str(group_id))
        return bool(entry and entry.get("independent"))

    def get_overrides(self, group_id: str) -> dict[str, Any]:
        """返回该群生效的覆盖值（嵌套 dict），未独立配置时返回空 dict。"""
        entry = self._data.get(str(group_id))
        if not entry or not entry.get("independent"):
            return {}
        overrides = entry.get("config")
        return overrides if isinstance(overrides, dict) else {}

    def get_meta(self, group_id: str) -> dict[str, Any]:
        entry = self._data.get(str(group_id)) or {}
        return {
            "independent": bool(entry.get("independent")),
            "updated_at": float(entry.get("updated_at") or 0),
            "note": str(entry.get("note") or ""),
        }

    def known_groups(self) -> list[str]:
        return sorted(self._data.keys())

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def save_overrides(
        self,
        group_id: str,
        values: dict[str, Any],
        *,
        note: str = "",
    ) -> dict[str, Any]:
        """保存某个群的覆盖值。

        Args:
            group_id: 群号。
            values: 形如 ``{"active_reply.enable": True, ...}`` 的扁平字段字典。
            note: 可选备注（群名等）。
        """
        group_id = str(group_id or "").strip()
        if not group_id:
            raise ValueError("group_id 不能为空")

        nested: dict[str, Any] = {}
        for spec in GROUP_OVERRIDE_FIELDS:
            raw = values.get(spec.path, values.get(spec.path.split(".")[-1]))
            coerced = coerce_group_value(spec, raw)
            set_by_path(nested, spec.path, coerced)

        with self._lock:
            self._data[group_id] = {
                "independent": True,
                "config": nested,
                "note": str(note or "").strip(),
                "updated_at": time.time(),
            }
            self._save()
        return nested

    def set_independent(self, group_id: str, independent: bool, note: str = "") -> None:
        group_id = str(group_id or "").strip()
        if not group_id:
            raise ValueError("group_id 不能为空")
        with self._lock:
            entry = self._data.setdefault(group_id, {})
            entry["independent"] = bool(independent)
            if note:
                entry["note"] = str(note).strip()
            entry["updated_at"] = time.time()
            if not independent and not entry.get("config"):
                self._data.pop(group_id, None)
            self._save()

    def set_note(self, group_id: str, note: str) -> None:
        group_id = str(group_id or "").strip()
        if not group_id:
            return
        with self._lock:
            entry = self._data.setdefault(group_id, {"independent": False})
            entry["note"] = str(note or "").strip()
            entry["updated_at"] = time.time()
            self._save()

    def remove(self, group_id: str) -> bool:
        with self._lock:
            removed = self._data.pop(str(group_id), None) is not None
            if removed:
                self._save()
            return removed

    # ------------------------------------------------------------------ #
    # 面板展示
    # ------------------------------------------------------------------ #
    def effective_values(
        self, group_id: str, global_values: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """混合全局默认值与群覆盖值，返回 (完整字段值, 是否独立配置)。"""
        overrides = self.get_overrides(group_id)
        if not overrides:
            return dict(global_values), False
        values: dict[str, Any] = {}
        for spec in GROUP_OVERRIDE_FIELDS:
            value = get_by_path(overrides, spec.path)
            values[spec.path] = (
                value if value is not None else global_values.get(spec.path)
            )
        return values, True
