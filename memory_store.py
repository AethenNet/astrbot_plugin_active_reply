"""记忆 RAG 的 SQLite 存储层。

设计取舍：
* 向量以 JSON 文本落库，检索时在进程内计算余弦相似度；
* 单次检索最多扫描 ``default_scan_limit`` 条最新记录，避免全表读取；
* 角色与记忆是多对多关系，单独建表并开启外键级联删除；
* 所有查询参数化，不拼接外部输入。

数据文件位于 ``data/plugin_data/astrbot_plugin_active_reply/memory_rag.db``。
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_SCAN_LIMIT = 5000


class MemoryStore:
    """记忆记录与角色标签的持久化存储。"""

    def __init__(
        self,
        db_path: Path | str,
        *,
        default_scan_limit: int = DEFAULT_SCAN_LIMIT,
        display_timezone: str = "Asia/Shanghai",
    ) -> None:
        self.db_path = Path(db_path)
        self.default_scan_limit = max(1, int(default_scan_limit))
        self._lock = threading.RLock()
        self._timezone_name = "Asia/Shanghai"
        self._tzinfo: Any = timezone.utc
        self.set_display_timezone(display_timezone)
        self._init_schema()

    # ------------------------------------------------------------------ #
    # 基础设施
    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, closing(self._connect()) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    embedding_dim INTEGER NOT NULL,
                    memory_time REAL NOT NULL,
                    group_scope TEXT NOT NULL DEFAULT '',
                    group_id TEXT NOT NULL DEFAULT '',
                    platform_id TEXT NOT NULL DEFAULT '',
                    extra_metadata TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_roles (
                    memory_id INTEGER NOT NULL,
                    role_id TEXT NOT NULL,
                    PRIMARY KEY (memory_id, role_id),
                    FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_memories_time ON memories(memory_time);
                CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(group_scope);
                CREATE INDEX IF NOT EXISTS idx_memories_group ON memories(group_id);
                CREATE INDEX IF NOT EXISTS idx_memories_platform ON memories(platform_id);
                CREATE INDEX IF NOT EXISTS idx_roles_role ON memory_roles(role_id);
                CREATE INDEX IF NOT EXISTS idx_roles_memory ON memory_roles(memory_id);
                """
            )

    def set_display_timezone(self, timezone_name: str) -> str:
        """设置时间展示使用的时区（与 AstrBot 全局 timezone 保持一致）。"""
        name = str(timezone_name or "").strip() or "Asia/Shanghai"
        try:
            tzinfo = ZoneInfo(name)
        except Exception:
            name = "Asia/Shanghai"
            try:
                tzinfo = ZoneInfo(name)
            except Exception:  # pragma: no cover - 极端环境兜底
                name = "UTC"
                tzinfo = timezone.utc
        self._timezone_name = name
        self._tzinfo = tzinfo
        return name

    def format_timestamp_iso(self, timestamp: float | int) -> str:
        return datetime.fromtimestamp(float(timestamp), tz=self._tzinfo).isoformat()

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize_role_ids(role_ids: list[str] | tuple[str, ...]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in role_ids or ():
            role_id = str(raw or "").strip()
            if not role_id or role_id in seen:
                continue
            seen.add(role_id)
            result.append(role_id)
        return result

    @staticmethod
    def _parse_embedding(raw: str | None) -> list[float]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        vector: list[float] = []
        for item in parsed:
            try:
                vector.append(float(item))
            except (TypeError, ValueError):
                return []
        return vector

    @staticmethod
    def _parse_metadata(raw: str | None) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right or len(left) != len(right):
            return -1.0
        dot = norm_left = norm_right = 0.0
        for a, b in zip(left, right, strict=False):
            dot += a * b
            norm_left += a * a
            norm_right += b * b
        if norm_left <= 0 or norm_right <= 0:
            return -1.0
        return dot / (math.sqrt(norm_left) * math.sqrt(norm_right))

    @staticmethod
    def _normalize_timestamp(raw: float | int) -> tuple[float, bool]:
        value = float(raw)
        if value > 1e12:  # 毫秒时间戳
            return value / 1000.0, True
        return value, False

    def _time_fields(self, timestamp: float) -> dict[str, Any]:
        dt = datetime.fromtimestamp(float(timestamp), tz=self._tzinfo)
        return {
            "memory_time": float(timestamp),
            "memory_time_iso": dt.isoformat(),
            "memory_time_timezone": self._timezone_name,
        }

    def _row_to_item(self, row: sqlite3.Row, role_ids: list[str]) -> dict[str, Any]:
        item: dict[str, Any] = {
            "memory_id": int(row["id"]),
            "content": str(row["content"]),
            "embedding_dim": int(row["embedding_dim"]),
            "group_scope": str(row["group_scope"] or ""),
            "group_id": str(row["group_id"] or ""),
            "platform_id": str(row["platform_id"] or ""),
            "related_role_ids": role_ids,
            "extra_metadata": self._parse_metadata(row["extra_metadata"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }
        item.update(self._time_fields(float(row["memory_time"])))
        return item

    # ------------------------------------------------------------------ #
    # 写入 / 读取
    # ------------------------------------------------------------------ #
    def add_memory(
        self,
        *,
        content: str,
        embedding: list[float],
        role_ids: list[str],
        memory_time: float | None = None,
        group_scope: str = "",
        group_id: str = "",
        platform_id: str = "",
        extra_metadata: dict[str, Any] | None = None,
    ) -> int:
        clean_content = str(content or "").strip()
        if not clean_content:
            raise ValueError("content 不能为空")
        clean_roles = self._normalize_role_ids(role_ids)
        if not clean_roles:
            raise ValueError("至少需要一个 role_id")
        if not embedding:
            raise ValueError("embedding 不能为空")

        vector = [float(value) for value in embedding]
        raw_time = float(memory_time) if memory_time is not None else time.time()
        timestamp, _ = self._normalize_timestamp(raw_time)
        now = time.time()
        metadata = extra_metadata if isinstance(extra_metadata, dict) else {}

        with self._lock, closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                INSERT INTO memories (
                    content, embedding, embedding_dim, memory_time,
                    group_scope, group_id, platform_id, extra_metadata,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean_content,
                    json.dumps(vector, ensure_ascii=False),
                    len(vector),
                    timestamp,
                    str(group_scope or "").strip(),
                    str(group_id or "").strip(),
                    str(platform_id or "").strip(),
                    json.dumps(metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            memory_id = int(cursor.lastrowid or 0)
            conn.executemany(
                "INSERT OR IGNORE INTO memory_roles(memory_id, role_id) VALUES (?, ?)",
                [(memory_id, role_id) for role_id in clean_roles],
            )
            return memory_id

    def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        try:
            target = int(memory_id)
        except (TypeError, ValueError):
            return None
        if target <= 0:
            return None
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT id, content, embedding_dim, memory_time, group_scope,
                       group_id, platform_id, extra_metadata, created_at, updated_at
                FROM memories WHERE id = ?
                """,
                (target,),
            ).fetchone()
            if not row:
                return None
            role_rows = conn.execute(
                "SELECT role_id FROM memory_roles WHERE memory_id = ? ORDER BY role_id",
                (target,),
            ).fetchall()
        return self._row_to_item(row, [str(r["role_id"]) for r in role_rows])

    def list_memories(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        keyword: str = "",
        group_scope: str = "",
        role_id: str = "",
    ) -> dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        offset = (page - 1) * page_size

        where: list[str] = []
        params: list[Any] = []
        keyword = str(keyword or "").strip()
        if keyword:
            where.append("(m.content LIKE ? OR CAST(m.id AS TEXT) LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        group_scope = str(group_scope or "").strip()
        if group_scope:
            where.append("m.group_scope = ?")
            params.append(group_scope)
        role_id = str(role_id or "").strip()
        if role_id:
            where.append(
                "EXISTS (SELECT 1 FROM memory_roles r "
                "WHERE r.memory_id = m.id AND r.role_id = ?)"
            )
            params.append(role_id)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        with self._lock, closing(self._connect()) as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS total FROM memories m {where_sql}", params
            ).fetchone()
            total = int(total_row["total"]) if total_row else 0
            rows = conn.execute(
                f"""
                SELECT m.id, m.content, m.embedding_dim, m.memory_time,
                       m.group_scope, m.group_id, m.platform_id,
                       m.extra_metadata, m.created_at, m.updated_at
                FROM memories m {where_sql}
                ORDER BY m.memory_time DESC, m.id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, page_size, offset],
            ).fetchall()

            ids = [int(row["id"]) for row in rows]
            roles_map: dict[int, list[str]] = {}
            if ids:
                placeholders = ",".join("?" for _ in ids)
                role_rows = conn.execute(
                    f"""
                    SELECT memory_id, role_id FROM memory_roles
                    WHERE memory_id IN ({placeholders})
                    ORDER BY memory_id, role_id
                    """,
                    ids,
                ).fetchall()
                for role_row in role_rows:
                    roles_map.setdefault(int(role_row["memory_id"]), []).append(
                        str(role_row["role_id"])
                    )

        items = [
            self._row_to_item(row, roles_map.get(int(row["id"]), [])) for row in rows
        ]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": offset + page_size < total,
        }

    def delete_memory(self, memory_id: int) -> bool:
        try:
            target = int(memory_id)
        except (TypeError, ValueError):
            return False
        if target <= 0:
            return False
        with self._lock, closing(self._connect()) as conn, conn:
            cursor = conn.execute("DELETE FROM memories WHERE id = ?", (target,))
            return cursor.rowcount > 0

    def get_stats(self) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as conn:
            total_row = conn.execute(
                "SELECT COUNT(*) AS total FROM memories"
            ).fetchone()
            total = int(total_row["total"]) if total_row else 0
            scope_row = conn.execute(
                "SELECT COUNT(DISTINCT group_scope) AS cnt FROM memories "
                "WHERE group_scope <> ''"
            ).fetchone()
            group_row = conn.execute(
                "SELECT COUNT(DISTINCT group_id) AS cnt FROM memories "
                "WHERE group_id <> ''"
            ).fetchone()
            role_row = conn.execute(
                "SELECT COUNT(DISTINCT role_id) AS cnt FROM memory_roles"
            ).fetchone()
            range_row = conn.execute(
                "SELECT MIN(memory_time) AS oldest, MAX(memory_time) AS latest "
                "FROM memories"
            ).fetchone()

        oldest = (
            float(range_row["oldest"])
            if range_row and range_row["oldest"] is not None
            else None
        )
        latest = (
            float(range_row["latest"])
            if range_row and range_row["latest"] is not None
            else None
        )
        return {
            "total_memories": total,
            "group_scope_count": int(scope_row["cnt"]) if scope_row else 0,
            "group_id_count": int(group_row["cnt"]) if group_row else 0,
            "role_count": int(role_row["cnt"]) if role_row else 0,
            "oldest_memory_time": oldest,
            "latest_memory_time": latest,
            "oldest_memory_time_iso": (
                self.format_timestamp_iso(oldest) if oldest is not None else None
            ),
            "latest_memory_time_iso": (
                self.format_timestamp_iso(latest) if latest is not None else None
            ),
            "display_timezone": self._timezone_name,
        }

    def list_group_scopes(self) -> list[str]:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT DISTINCT group_scope FROM memories "
                "WHERE group_scope <> '' ORDER BY group_scope LIMIT 200"
            ).fetchall()
        return [str(row["group_scope"]) for row in rows]

    # ------------------------------------------------------------------ #
    # 语义检索
    # ------------------------------------------------------------------ #
    def search_memories(
        self,
        *,
        query_embedding: list[float] | None = None,
        recall_k: int = 20,
        role_ids: list[str] | None = None,
        role_match_mode: str = "any",
        group_scope: str = "",
        group_id: str = "",
        platform_id: str = "",
        start_time: float | None = None,
        end_time: float | None = None,
        sort_by: str = "relevance",
        sort_order: str = "desc",
        max_results: int = 10,
        scan_limit: int | None = None,
    ) -> list[dict[str, Any]]:
        roles = self._normalize_role_ids(role_ids or [])
        mode = "all" if str(role_match_mode).strip().lower() == "all" else "any"

        where: list[str] = []
        params: list[Any] = []
        if group_scope:
            where.append("m.group_scope = ?")
            params.append(str(group_scope).strip())
        if group_id:
            where.append("m.group_id = ?")
            params.append(str(group_id).strip())
        if platform_id:
            where.append("m.platform_id = ?")
            params.append(str(platform_id).strip())
        if start_time is not None:
            where.append("m.memory_time >= ?")
            params.append(float(start_time))
        if end_time is not None:
            where.append("m.memory_time <= ?")
            params.append(float(end_time))
        if roles:
            placeholders = ",".join("?" for _ in roles)
            if mode == "all":
                where.append(
                    f"m.id IN (SELECT memory_id FROM memory_roles "
                    f"WHERE role_id IN ({placeholders}) "
                    f"GROUP BY memory_id HAVING COUNT(DISTINCT role_id) = ?)"
                )
                params.extend(roles)
                params.append(len(roles))
            else:
                where.append(
                    f"m.id IN (SELECT DISTINCT memory_id FROM memory_roles "
                    f"WHERE role_id IN ({placeholders}))"
                )
                params.extend(roles)

        sql = (
            "SELECT m.id, m.content, m.embedding, m.embedding_dim, m.memory_time, "
            "m.group_scope, m.group_id, m.platform_id, m.extra_metadata, "
            "m.created_at, m.updated_at FROM memories m"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY m.memory_time DESC LIMIT ?"
        limit = (
            self.default_scan_limit if scan_limit is None else max(1, int(scan_limit))
        )
        params.append(limit)

        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
            if not rows:
                return []
            ids = [int(row["id"]) for row in rows]
            placeholders = ",".join("?" for _ in ids)
            role_rows = conn.execute(
                f"SELECT memory_id, role_id FROM memory_roles "
                f"WHERE memory_id IN ({placeholders}) ORDER BY memory_id, role_id",
                ids,
            ).fetchall()

        roles_map: dict[int, list[str]] = {}
        for role_row in role_rows:
            roles_map.setdefault(int(role_row["memory_id"]), []).append(
                str(role_row["role_id"])
            )

        has_query = bool(query_embedding)
        query_vec = [float(value) for value in (query_embedding or [])]

        candidates: list[dict[str, Any]] = []
        for row in rows:
            memory_id = int(row["id"])
            item = self._row_to_item(row, roles_map.get(memory_id, []))
            item["similarity"] = (
                self._cosine_similarity(
                    query_vec, self._parse_embedding(row["embedding"])
                )
                if has_query
                else None
            )
            candidates.append(item)

        if has_query:
            candidates.sort(
                key=lambda item: (
                    item["similarity"]
                    if isinstance(item["similarity"], (int, float))
                    else -1.0
                ),
                reverse=True,
            )
            if recall_k > 0:
                candidates = candidates[: int(recall_k)]

        reverse = str(sort_order).strip().lower() != "asc"
        if str(sort_by).strip().lower() == "relevance" and has_query:
            candidates.sort(
                key=lambda item: (
                    item["similarity"]
                    if isinstance(item["similarity"], (int, float))
                    else -1.0
                ),
                reverse=reverse,
            )
        else:
            candidates.sort(key=lambda item: item["memory_time"], reverse=reverse)

        if max_results > 0:
            return candidates[: int(max_results)]
        return candidates

    # ------------------------------------------------------------------ #
    # 维护
    # ------------------------------------------------------------------ #
    def cleanup_legacy_records(self) -> dict[str, Any]:
        """把毫秒时间戳规范化，并刷新时区相关的元数据（幂等）。"""
        updated = 0
        normalized_ms = 0
        metadata_refreshed = 0
        with self._lock, closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT id, memory_time, extra_metadata FROM memories ORDER BY id"
            ).fetchall()
            now = time.time()
            cleaned_at = self.format_timestamp_iso(now)
            for row in rows:
                memory_id = int(row["id"])
                normalized_time, converted = self._normalize_timestamp(
                    float(row["memory_time"])
                )
                if converted:
                    normalized_ms += 1

                metadata = self._parse_metadata(row["extra_metadata"])
                previous = metadata.get("_active_reply_cleanup")
                previous_cleaned_at = (
                    str(previous.get("cleaned_at") or "").strip()
                    if isinstance(previous, dict)
                    else ""
                )
                payload = {
                    "version": 1,
                    "timezone": self._timezone_name,
                    "memory_time_iso": self.format_timestamp_iso(normalized_time),
                    "cleaned_at": previous_cleaned_at or cleaned_at,
                }
                changed = previous != payload
                if changed:
                    metadata["_active_reply_cleanup"] = payload
                    metadata_refreshed += 1
                if not converted and not changed:
                    continue
                conn.execute(
                    "UPDATE memories SET memory_time = ?, extra_metadata = ?, "
                    "updated_at = ? WHERE id = ?",
                    (
                        normalized_time,
                        json.dumps(metadata, ensure_ascii=False),
                        now,
                        memory_id,
                    ),
                )
                updated += 1

        return {
            "status": "ok",
            "scanned": len(rows),
            "updated": updated,
            "normalized_millisecond_timestamps": normalized_ms,
            "metadata_refreshed": metadata_refreshed,
            "display_timezone": self._timezone_name,
        }
