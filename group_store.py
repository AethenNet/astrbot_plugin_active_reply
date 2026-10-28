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

from config_model import (
    GROUP_OVERRIDE_FIELDS,
    coerce_group_value,
    get_by_path,
    set_by_path,
)

STORE_VERSION = 1


class GroupStore:
    """将各群独立配置以 JSON 文件持久化，并提供全局值合并视图。

    磁盘记录只保存明确允许群级覆盖的字段，不复制全局配置；读取面板数据时
    再将覆盖项与最新全局快照合并。这样全局配置调整后，未覆盖字段会自动
    跟随新值。写操作由可重入锁保护，并通过同目录临时文件替换避免写入中断
    留下半截 JSON。
    """

    def __init__(self, path: Path | str) -> None:
        """创建群配置存储并从指定 JSON 文件载入已有记录。

        Args:
            path: 群级覆盖配置文件路径。
        """
        # 初始化线程安全的内存快照，并立即尝试从 JSON 文件加载现有配置。
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # 读写
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        """从磁盘恢复群记录，并忽略不完整或不兼容的顶层数据。

        文件缺失、读取失败或 JSON 语法损坏时初始化为空配置，不因本地群配置
        文件问题阻断插件启动；载入后仍会确认 ``groups`` 是字典，避免后续
        查询把任意 JSON 类型当作群映射使用。
        """
        # 读取旧配置；文件不存在或损坏时从空映射启动，不阻塞插件启动。
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
        """将当前群配置快照以临时文件替换方式持久化。"""
        # 先写同目录临时文件再替换目标，降低进程中断导致 JSON 被截断的风险。
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
        """判断指定群是否已启用独立配置。"""
        # 仅当群记录明确启用独立配置时返回 True。
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
        """获取面板展示所需的群状态、更新时间和备注。"""
        # 返回面板需要的状态、最近更新时间与可读备注，不暴露内部配置对象。
        entry = self._data.get(str(group_id)) or {}
        return {
            "independent": bool(entry.get("independent")),
            "updated_at": float(entry.get("updated_at") or 0),
            "note": str(entry.get("note") or ""),
        }

    def known_groups(self) -> list[str]:
        """列出本地存储中出现过的全部群号。"""
        # 返回曾保存过元数据或覆盖值的群号，保证面板可重新找到这些群。
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
        """切换群级独立状态，并更新备注或清理无配置的空记录。"""
        # 切换跟随全局/独立状态；关闭后若没有覆盖值则移除空记录。
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
        """保存本地群备注，不修改群当前的独立配置状态。"""
        # 更新群的本地展示备注，不改变其独立配置启用状态。
        group_id = str(group_id or "").strip()
        if not group_id:
            return
        with self._lock:
            entry = self._data.setdefault(group_id, {"independent": False})
            entry["note"] = str(note or "").strip()
            entry["updated_at"] = time.time()
            self._save()

    def remove(self, group_id: str) -> bool:
        """删除指定群的记录，并返回是否实际删除了数据。"""
        # 删除群的全部保存数据；返回是否确实存在并移除了记录。
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
        """为指定群构造面板使用的完整字段快照。

        未启用独立模式时返回全局快照的浅拷贝；独立模式下只遍历白名单字段，
        优先取群配置中非 ``None`` 的值，缺失字段继续回落到全局配置。返回值
        使用扁平点号路径作为键，便于前端按字段元数据生成表单。

        Returns:
            ``(字段路径到有效值的映射, 是否处于独立配置模式)``。
        """
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
