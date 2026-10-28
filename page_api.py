"""插件页面（视图）后端接口。

所有 handler 都返回 ``{"success": ..., "data": ...}`` 形式的 JSON，
由 ``views/console`` 下的前端通过 ``window.AstrBotPluginView`` bridge 调用。
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .config_model import (
    GROUP_OVERRIDE_FIELDS,
    build_global_snapshot,
    build_schema_patch,
    deep_merge,
)
from .web_compat import fail, ok, query_get, read_json


class PageApi:
    """页面后端处理器集合。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin

    def _embedding_options(self) -> list[dict[str, str]]:
        """列出当前可用的 embedding 提供商，方便用户复制 ID / 模型名。"""
        options: list[dict[str, str]] = []
        try:
            providers = self.plugin.context.get_all_embedding_providers() or []
        except Exception:
            return options
        for provider in providers:
            provider_id, model = self.plugin._provider_meta_label(provider)
            if provider_id or model:
                options.append({"id": provider_id, "model": model})
        return options

    # ------------------------------------------------------------------ #
    # 全局配置
    # ------------------------------------------------------------------ #
    def _schema_path(self) -> Path:
        return Path(__file__).with_name("_conf_schema.json")

    def _load_schema(self) -> dict[str, Any]:
        try:
            return json.loads(self._schema_path().read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error(f"[ActiveReply] 读取配置 schema 失败: {exc}")
            return {}

    async def get_config(self) -> dict[str, Any]:
        """返回配置 schema 与当前配置值，供页面渲染全局设置表单。"""
        values = copy.deepcopy(dict(self.plugin.config))
        return ok(
            {
                "schema": self._load_schema(),
                "values": values,
                "providers": self._provider_options(),
                "embedding_providers": self._embedding_options(),
            }
        )

    async def save_config(self) -> dict[str, Any]:
        """按 schema 校验并保存全局配置。"""
        payload = await read_json()
        values = (
            payload.get("values") if isinstance(payload.get("values"), dict) else {}
        )
        if not values:
            return fail("没有需要保存的配置项")

        schema = self._load_schema()
        if not schema:
            return fail("配置 schema 不可用")

        patch = build_schema_patch(schema, values)
        if not patch:
            return fail("没有可保存的配置项（提交的值与 schema 不匹配）")
        merged = deep_merge(copy.deepcopy(dict(self.plugin.config)), patch)

        warning = ""
        try:
            self.plugin.config.clear()
            self.plugin.config.update(merged)
        except Exception as exc:
            logger.error(f"[ActiveReply] 更新内存配置失败: {exc}", exc_info=True)
            return fail(f"更新配置失败：{exc}")

        saver = getattr(self.plugin.config, "save_config", None)
        if callable(saver):
            try:
                saver()
            except Exception as exc:
                warning = str(exc)
                logger.error(f"[ActiveReply] 保存配置文件失败: {exc}", exc_info=True)
        else:
            warning = "当前 AstrBot 版本不支持自动保存，配置仅在本次运行生效"

        self.plugin.invalidate_config_cache()
        logger.info(
            "[ActiveReply] 页面保存全局配置 | 字段数=%s 写盘=%s",
            len(patch),
            "成功" if not warning else "失败",
        )
        return ok({"saved": True, "warning": warning, "applied": patch})

    # ------------------------------------------------------------------ #
    # 概览
    # ------------------------------------------------------------------ #
    async def overview(self) -> dict[str, Any]:
        plugin = self.plugin
        cfg = plugin._global_cfg()
        force = bool(query_get("force", 0, int))
        try:
            groups = await plugin.collect_groups(force=force)
        except Exception as exc:
            logger.error(f"[ActiveReply] 获取群列表失败: {exc}", exc_info=True)
            groups = []

        stats: dict[str, Any] = {}
        if plugin.memory_store is not None:
            try:
                stats = plugin.memory_store.get_stats()
            except Exception as exc:
                logger.error(f"[ActiveReply] 读取记忆统计失败: {exc}", exc_info=True)

        embedding_options = self._embedding_options()
        embedding = {
            "available": bool(embedding_options),
            "count": len(embedding_options),
            "configured": bool(cfg.memory_rag.embedding_provider_id),
            "providers": embedding_options,
        }

        return ok(
            {
                "version": getattr(plugin, "version", "1.0.0"),
                "timezone": plugin._resolve_timezone_name(),
                "data_dir": str(plugin.data_dir),
                "groups": groups,
                "stats": stats,
                "embedding": embedding,
                "global": build_global_snapshot(cfg),
                "switches": {
                    "active_reply": cfg.active_reply.enable,
                    "mode": cfg.active_reply.mode,
                    "context": cfg.group_context.enable,
                    "react_pattern": cfg.group_reply.react_pattern,
                    "smart_reply": cfg.smart_reply_enabled,
                    "web_search": cfg.web_search.enable,
                    "memory_rag": cfg.memory_rag.enable,
                },
                "providers": self._provider_options(),
            }
        )

    # ------------------------------------------------------------------ #
    # 群配置
    # ------------------------------------------------------------------ #
    async def list_groups(self) -> dict[str, Any]:
        force = bool(query_get("force", 0, int))
        try:
            groups = await self.plugin.collect_groups(force=force)
        except Exception as exc:
            logger.error(f"[ActiveReply] 获取群列表失败: {exc}", exc_info=True)
            return fail(f"获取群列表失败：{exc}")
        return ok({"groups": groups})

    @staticmethod
    def _schema_default(schema: dict[str, Any], path: str) -> Any:
        """按路径取出 schema 里该配置项的默认值。"""
        node: Any = schema
        parts = path.split(".")
        for part in parts[:-1]:
            node = (node.get(part) or {}).get("items") or {}
        leaf = node.get(parts[-1]) if isinstance(node, dict) else None
        return (leaf or {}).get("default")

    @staticmethod
    def _schema_hint(schema: dict[str, Any], path: str) -> str:
        """按 "a.b.c" 路径取出 schema 里对应配置项的 hint 文案。"""
        node: Any = schema
        parts = path.split(".")
        for part in parts[:-1]:
            node = (node.get(part) or {}).get("items") or {}
        leaf = node.get(parts[-1]) if isinstance(node, dict) else None
        return str((leaf or {}).get("hint") or "")

    async def get_group(self, group_id: str) -> dict[str, Any]:
        group_id = str(group_id or "").strip()
        if not group_id:
            return fail("group_id 不能为空")
        plugin = self.plugin
        cfg = plugin._global_cfg()
        global_values = build_global_snapshot(cfg)
        values, independent = plugin.group_store.effective_values(
            group_id, global_values
        )
        meta = plugin.group_store.get_meta(group_id)

        # 群名优先取平台返回的群列表，其次才是本地备注
        name = ""
        try:
            for item in await plugin.collect_groups():
                if item.get("group_id") == group_id:
                    name = str(item.get("name") or "")
                    break
        except Exception:
            name = ""
        if not name:
            name = str(meta.get("note") or "")

        schema = self._load_schema()
        return ok(
            {
                "group_id": group_id,
                "name": name,
                "independent": independent,
                "global": global_values,
                "values": values,
                "meta": meta,
                "fields": [
                    {
                        "path": spec.path,
                        "label": spec.label,
                        "kind": spec.kind,
                        "options": list(spec.options),
                        "labels": list(spec.labels),
                        "min": spec.minimum,
                        "max": spec.maximum,
                        "step": spec.step,
                        "hint": self._schema_hint(schema, spec.path),
                        "default": self._schema_default(schema, spec.path),
                    }
                    for spec in GROUP_OVERRIDE_FIELDS
                ],
                "providers": self._provider_options(),
            }
        )

    async def save_group(self, group_id: str) -> dict[str, Any]:
        group_id = str(group_id or "").strip()
        if not group_id:
            return fail("group_id 不能为空")
        payload = await read_json()
        independent = bool(payload.get("independent", True))
        note = str(payload.get("note") or "")
        values = (
            payload.get("values") if isinstance(payload.get("values"), dict) else {}
        )

        plugin = self.plugin
        try:
            if independent:
                if not values:
                    return fail("启用独立配置时至少需要提交一个字段")
                saved = plugin.group_store.save_overrides(group_id, values, note=note)
            else:
                plugin.group_store.set_independent(group_id, False, note=note)
                saved = {}
        except Exception as exc:
            logger.error(f"[ActiveReply] 保存群配置失败: {exc}", exc_info=True)
            return fail(f"保存失败：{exc}")

        plugin.invalidate_config_cache()
        logger.info(
            "[ActiveReply] 群配置已保存 | group=%s independent=%s",
            group_id,
            independent,
        )
        return ok({"group_id": group_id, "independent": independent, "saved": saved})

    async def reset_group(self, group_id: str) -> dict[str, Any]:
        group_id = str(group_id or "").strip()
        if not group_id:
            return fail("group_id 不能为空")
        removed = self.plugin.group_store.remove(group_id)
        self.plugin.invalidate_config_cache()
        return ok({"group_id": group_id, "removed": removed})

    # ------------------------------------------------------------------ #
    # 记忆 RAG
    # ------------------------------------------------------------------ #
    async def rag_stats(self) -> dict[str, Any]:
        store = self.plugin.memory_store
        if store is None:
            return fail("记忆库未初始化")
        try:
            stats = store.get_stats()
            scopes = store.list_group_scopes()
        except Exception as exc:
            logger.error(f"[ActiveReply] 读取记忆统计失败: {exc}", exc_info=True)
            return fail(f"读取失败：{exc}")
        return ok({"stats": stats, "group_scopes": scopes})

    async def rag_list(self) -> dict[str, Any]:
        store = self.plugin.memory_store
        if store is None:
            return fail("记忆库未初始化")
        page = query_get("page", 1, int)
        page_size = query_get("page_size", 20, int)
        keyword = query_get("keyword", "", str)
        group_scope = query_get("group_scope", "", str)
        role_id = query_get("role_id", "", str)
        try:
            data = store.list_memories(
                page=page or 1,
                page_size=page_size or 20,
                keyword=keyword or "",
                group_scope=group_scope or "",
                role_id=role_id or "",
            )
        except Exception as exc:
            logger.error(f"[ActiveReply] 读取记忆列表失败: {exc}", exc_info=True)
            return fail(f"读取失败：{exc}")
        return ok(data)

    async def rag_detail(self, memory_id: str) -> dict[str, Any]:
        store = self.plugin.memory_store
        if store is None:
            return fail("记忆库未初始化")
        item = store.get_memory(memory_id)
        if not item:
            return fail("记忆不存在")
        return ok(item)

    async def rag_delete(self, memory_id: str) -> dict[str, Any]:
        store = self.plugin.memory_store
        if store is None:
            return fail("记忆库未初始化")
        removed = store.delete_memory(memory_id)
        if not removed:
            return fail("记忆不存在或已删除")
        logger.info("[ActiveReply] 页面删除记忆 | id=%s", memory_id)
        return ok({"memory_id": memory_id, "deleted": True})

    async def rag_cleanup(self) -> dict[str, Any]:
        store = self.plugin.memory_store
        if store is None:
            return fail("记忆库未初始化")
        try:
            result = await asyncio.to_thread(store.cleanup_legacy_records)
        except Exception as exc:
            logger.error(f"[ActiveReply] 清理记忆失败: {exc}", exc_info=True)
            return fail(f"清理失败：{exc}")
        return ok(result)

    async def rag_selftest(self) -> dict[str, Any]:
        """记忆 RAG 自检：写入 → 读回 → 语义检索 → 删除一条临时记忆。"""
        plugin = self.plugin
        steps: list[dict[str, Any]] = []

        def add(name: str, passed: bool, detail: str = "") -> None:
            steps.append({"name": name, "ok": bool(passed), "detail": str(detail)})

        store = plugin.memory_store
        if store is None:
            add("记忆库初始化", False, "memory_store 为 None")
            return ok({"passed": False, "steps": steps})
        add("记忆库初始化", True, str(plugin.data_dir / "memory_rag.db"))

        provider = plugin._embedding_provider()
        if provider is None:
            add("Embedding 提供商", False, "未找到可用的 embedding provider")
            return ok({"passed": False, "steps": steps})
        provider_id, model = plugin._provider_meta_label(provider)
        add("Embedding 提供商", True, model or provider_id or "unknown")

        try:
            vector = await provider.get_embedding("ActiveReply 记忆自检")
        except Exception as exc:
            add("生成向量", False, str(exc))
            return ok({"passed": False, "steps": steps})
        if not vector:
            add("生成向量", False, "返回空向量")
            return ok({"passed": False, "steps": steps})
        add("生成向量", True, f"dim={len(vector)}")

        memory_id: int | None = None
        try:
            memory_id = store.add_memory(
                content="ActiveReply 记忆自检",
                embedding=vector,
                role_ids=["selftest"],
                memory_time=time.time(),
                group_scope="selftest",
                group_id="selftest",
                platform_id="selftest",
                extra_metadata={"selftest": True},
            )
            add("写入记忆", True, f"id={memory_id}")
        except Exception as exc:
            add("写入记忆", False, str(exc))
            return ok({"passed": False, "steps": steps})

        try:
            got = store.get_memory(memory_id or 0)
            add(
                "读回记忆",
                bool(got and got.get("content") == "ActiveReply 记忆自检"),
                "内容一致" if got else "读回为空",
            )

            hits = store.search_memories(
                query_embedding=vector,
                recall_k=5,
                group_scope="selftest",
                max_results=5,
            )
            found = any(int(hit["memory_id"]) == int(memory_id or 0) for hit in hits)
            add("语义检索", found, f"返回 {len(hits)} 条，命中={found}")
        except Exception as exc:
            add("读回/检索", False, str(exc))
        finally:
            try:
                removed = store.delete_memory(memory_id or 0)
                add("清理自检数据", removed, f"删除 id={memory_id}")
            except Exception as exc:
                add("清理自检数据", False, str(exc))

        passed = all(step["ok"] for step in steps)
        return ok({"passed": passed, "steps": steps})

    # ------------------------------------------------------------------ #
    # 工具
    # ------------------------------------------------------------------ #
    def _provider_options(self) -> list[dict[str, str]]:
        options: list[dict[str, str]] = []
        try:
            providers = self.plugin.context.get_all_providers() or []
        except Exception:
            return options
        for provider in providers:
            provider_id = ""
            model = ""
            try:
                meta = provider.meta()
                provider_id = str(getattr(meta, "id", "") or "")
                model = str(getattr(meta, "model", "") or "")
            except Exception:
                provider_id = str(getattr(provider, "provider_id", "") or "")
            if not provider_id:
                continue
            options.append(
                {
                    "id": provider_id,
                    "label": f"{provider_id}（{model}）" if model else provider_id,
                }
            )
        options.sort(key=lambda item: item["id"])
        return options
