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
    """记忆记录与角色标签的 SQLite 持久化存储。

    记忆正文、时间、作用域、来源元数据和 embedding 存在 ``memories`` 主表，
    每条记忆关联的角色 ID 单独放入 ``memory_roles``。向量按 JSON 保存，
    检索时只扫描满足结构化条件的一段最新记录，再在 Python 中计算余弦相似度；
    该实现不依赖额外向量数据库，但扫描上限意味着语义检索并非全库精确索引。
    """

    def __init__(
        self,
        db_path: Path | str,
        *,
        default_scan_limit: int = DEFAULT_SCAN_LIMIT,
        display_timezone: str = "Asia/Shanghai",
    ) -> None:
        """初始化 SQLite 记忆存储、展示时区和兼容性迁移。

        Args:
            db_path: SQLite 数据库文件路径。
            default_scan_limit: 语义检索默认扫描的最新记录数上限。
            display_timezone: ISO 时间字段使用的时区名称。
        """
        # 建立数据库目录与 schema，随后将历史群范围格式迁移到规范形式。
        self.db_path = Path(db_path)
        self.default_scan_limit = max(1, int(default_scan_limit))
        self._lock = threading.RLock()
        self._timezone_name = "Asia/Shanghai"
        self._tzinfo: Any = timezone.utc
        self.set_display_timezone(display_timezone)
        self._init_schema()
        self.normalize_group_scopes()

    # ------------------------------------------------------------------ #
    # 基础设施
    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        """创建配置好行映射和外键支持的数据库连接。"""
        # 每次操作独立连接，并开启外键以保证删除记忆时同步删除角色关联。
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_schema(self) -> None:
        """确保记忆表、角色关联表及检索所需索引存在。"""
        # 创建记忆主表、角色关联表及常用筛选索引；已有数据库保持兼容。
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

    _MESSAGE_TYPE_SEGMENTS = {
        "group",
        "groupmessage",
        "friend",
        "friendmessage",
        "private",
    }

    def normalize_group_scopes(self) -> int:
        """把 ``platform:GroupMessage:123`` 之类的旧 scope 归一成 ``platform:123``。"""
        changed = 0
        with self._lock, closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT id, group_scope, group_id, platform_id FROM memories"
            ).fetchall()
            for row in rows:
                scope = str(row["group_scope"] or "").strip()
                parts = scope.split(":")
                if (
                    len(parts) < 3
                    or parts[1].lower() not in self._MESSAGE_TYPE_SEGMENTS
                ):
                    continue
                platform = parts[0].strip()
                group = parts[-1].strip()
                new_scope = f"{platform}:{group}" if platform and group else group
                conn.execute(
                    "UPDATE memories SET group_scope = ?, group_id = ?, platform_id = ? "
                    "WHERE id = ?",
                    (
                        new_scope,
                        str(row["group_id"] or group),
                        str(row["platform_id"] or platform),
                        int(row["id"]),
                    ),
                )
                changed += 1
        return changed

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
        """将 Unix 时间秒数转换为当前展示时区下的 ISO 8601 时间。"""
        # 将 Unix 秒时间戳按当前展示时区格式化为 ISO 8601 字符串。
        return datetime.fromtimestamp(float(timestamp), tz=self._tzinfo).isoformat()

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize_role_ids(role_ids: list[str] | tuple[str, ...]) -> list[str]:
        """清理角色 ID 并按输入顺序去重。"""
        # 清理空 ID 并按首次出现顺序去重，供关系表写入使用。
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
        """将存储的 JSON 向量解析为浮点列表，解析失败时返回空列表。"""
        # 将数据库中的 JSON 向量还原；损坏或不符合数组格式时返回空向量。
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
        """将扩展元数据 JSON 还原为字典，其他内容返回空字典。"""
        # 将扩展元数据安全还原为字典，未知或损坏内容按空元数据处理。
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        """计算两个等维向量的余弦相似度；无效向量返回 -1。"""
        # 计算等维向量的余弦相似度；空向量、零向量和维数不一致视为无效。
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
        """把毫秒时间戳换算为秒，并返回换算后的数值和是否转换标记。"""
        # 兼容秒/毫秒两种 Unix 时间戳，第二个返回值表示是否执行了换算。
        value = float(raw)
        if value > 1e12:  # 毫秒时间戳
            return value / 1000.0, True
        return value, False

    def _time_fields(self, timestamp: float) -> dict[str, Any]:
        """为记忆 API 生成时间戳、ISO 字符串及展示时区字段。"""
        # 生成 API/页面共用的时间字段，明确携带展示时区名称。
        dt = datetime.fromtimestamp(float(timestamp), tz=self._tzinfo)
        return {
            "memory_time": float(timestamp),
            "memory_time_iso": dt.isoformat(),
            "memory_time_timezone": self._timezone_name,
        }

    def _row_to_item(self, row: sqlite3.Row, role_ids: list[str]) -> dict[str, Any]:
        """将数据库行转换为包含角色和时区信息的普通字典。"""
        # 把 SQLite 行转换为不依赖 sqlite3.Row 的普通 API 字典。
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
        """持久化一条记忆及其角色关联，并返回新记录 ID。

        正文、角色列表和 embedding 必须非空；记忆时间支持秒或毫秒。主表行
        与角色关联写入同一个 SQLite 事务，任一 SQL 操作失败时事务会回滚，
        避免留下没有关联角色或关联指向无效记忆的半成品。

        Args:
            content: 记忆正文；保存前去除首尾空白。
            embedding: 正文对应的数值向量；其维度随记录一起存储。
            role_ids: 与这条记忆相关的用户/角色 ID；空值会在写库前拒绝。
            memory_time: 记忆实际发生时间；省略时使用当前 Unix 秒时间。
            group_scope: 规范化群范围；空字符串表示全局记忆。
            group_id: 群号，用于结构化过滤及后台列表展示。
            platform_id: 平台标识，用于区分跨平台的同号群。
            extra_metadata: 来源、置信度等扩展信息，非字典输入不会被保存。

        Returns:
            SQLite 自动分配的正整数主键。

        Raises:
            ValueError: 正文、角色集合或 embedding 为空。
        """
        # 校验正文、角色与 embedding 后，在同一事务写入主记录和角色关联。
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
        """按主键读取完整记忆；无效 ID 或记录不存在时返回 None。"""
        # 按主键取一条完整记忆；无效 ID 或不存在时返回 None。
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
        """按关键词、作用域和角色过滤记忆，并返回一页结果及分页信息。

        关键词同时匹配正文和字符串形式的记忆 ID，方便控制台按内容或编号
        查找。总数查询和当前页查询共用同一组过滤条件；角色关联在页结果取出后
        批量读取，避免对每条记忆各发起一次查询。

        Args:
            page: 从 1 开始的页码，低于 1 时校正为 1。
            page_size: 每页记录数，限定在 1 到 200 之间。
            keyword: 可选正文/记录 ID 子串。
            group_scope: 可选精确作用域；空字符串表示不过滤作用域。
            role_id: 可选精确角色 ID。

        Returns:
            包含 ``items``、``total``、``page``、``page_size`` 与 ``has_more`` 的字典。
        """
        # 组合关键词、群范围和角色条件分页查询，并批量装配角色列表。
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
        """删除指定记忆；关联角色由数据库外键级联清理。"""
        # 删除主记录并依赖外键级联清理关联角色，返回是否删除到记录。
        try:
            target = int(memory_id)
        except (TypeError, ValueError):
            return False
        if target <= 0:
            return False
        with self._lock, closing(self._connect()) as conn, conn:
            cursor = conn.execute("DELETE FROM memories WHERE id = ?", (target,))
            return cursor.rowcount > 0

    def find_similar_memory(
        self,
        embedding: list[float],
        *,
        group_scope: str = "",
        threshold: float = 0.92,
        scan_limit: int = 2000,
        include_global: bool = False,
    ) -> dict[str, Any] | None:
        """在限定作用域的近期记忆中查找最相似项，用于避免重复写入。

        Args:
            embedding: 待比较的查询向量。
            group_scope: 记忆作用域；为空时不限制作用域。
            threshold: 相似度最低阈值。
            scan_limit: 最多扫描的近期记录数。
            include_global: 有指定作用域时是否仅检索该作用域，不含全局记忆。

        Returns:
            达到阈值的最佳记忆摘要；没有匹配项时返回 None。
        """
        if not embedding:
            return None
        where = ""
        params: list[Any] = []
        scope = str(group_scope or "").strip()
        if scope and not include_global:
            where = " WHERE m.group_scope = ? OR m.group_scope = ''"
            params.append(scope)
        elif scope and include_global:
            where = " WHERE m.group_scope = ?"
            params.append(scope)
        sql = (
            "SELECT m.id, m.content, m.embedding, m.group_scope, m.memory_time "
            f"FROM memories m{where} ORDER BY m.memory_time DESC LIMIT ?"
        )
        params.append(max(1, int(scan_limit)))
        best: tuple[float, sqlite3.Row] | None = None
        with self._lock, closing(self._connect()) as conn:
            for row in conn.execute(sql, params).fetchall():
                vector = self._parse_embedding(row["embedding"])
                if not vector or len(vector) != len(embedding):
                    continue
                score = self._cosine_similarity(vector, embedding)
                if score >= threshold and (best is None or score > best[0]):
                    best = (score, row)
        if best is None:
            return None
        row = best[1]
        return {
            "memory_id": int(row["id"]),
            "content": row["content"],
            "group_scope": row["group_scope"],
            "similarity": round(best[0], 4),
        }

    def source_counts(self) -> dict[str, int]:
        """按 ``keyword``、``model``、``manual`` 等来源统计记忆数量。"""
        counts: dict[str, int] = {}
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute("SELECT extra_metadata FROM memories").fetchall()
        for row in rows:
            meta = self._parse_metadata(row["extra_metadata"])
            source = str(meta.get("source") or "manual")
            counts[source] = counts.get(source, 0) + 1
        return counts

    def count_since(self, timestamp: float) -> int:
        """统计记忆时间不早于给定 Unix 时间的记录数量。"""
        # 统计指定 Unix 时间之后的记忆数，用于自动记忆面板的今日新增量。
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM memories WHERE memory_time >= ?",
                (float(timestamp),),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    def get_stats(self) -> dict[str, Any]:
        """统计记忆总量、关联范围/角色数和最早、最近记忆时间。"""
        # 汇总条数、群/角色维度以及时间跨度供控制台状态卡展示。
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
        """按字典序返回最多 200 个非空记忆作用域。"""
        # 返回有限数量的非空作用域，供记忆筛选下拉框使用。
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
        """先按结构化条件筛选，再可选地执行有限窗口内的语义召回。

        执行分为三个阶段：SQL 使用参数化条件筛选作用域、群号、平台、时间和
        角色；按记忆时间倒序最多读取 ``scan_limit`` 条候选并批量取回角色；
        若提供查询向量，则对候选逐条计算余弦相似度并先截取 ``recall_k``，
        最后按时间或相关度排序并应用 ``max_results``。因此扫描上限和召回数
        是两层不同的限制，前者约束数据库读取量，后者约束语义排序后的候选量。

        Args:
            query_embedding: 查询向量；为空时不计算相似度，按时间返回结果。
            recall_k: 语义相关候选的截取数量；小于等于 0 时不做该层截断。
            role_ids: 可选角色 ID 列表。
            role_match_mode: ``any`` 命中任一角色，``all`` 要求所有角色都关联。
            start_time: 可选包含式时间下界（Unix 秒）。
            end_time: 可选包含式时间上界（Unix 秒）。
            group_scope: 可选精确的规范作用域。
            group_id: 可选精确群号。
            platform_id: 可选精确平台 ID。
            sort_by: ``relevance`` 优先按相似度；否则按记忆时间。
            sort_order: ``asc`` 正序，其他值按倒序处理。
            max_results: 最终最大结果数；小于等于 0 时不截断结果。
            scan_limit: SQL 阶段的最大候选数量；未指定时采用实例默认值。

        Returns:
            记忆摘要字典列表，语义检索项额外含 ``similarity`` 分数。
        """
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

        # 只在 SQL 中生成筛选子句，所有外部值仍通过 params 绑定；LIMIT 也受
        # scan_limit 约束，避免在 Python 层一次性载入整个记忆库再做排序。
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

        # 没有查询向量时 similarity 保持为 None，后续自然改用时间排序；
        # 向量检索则先筛出 recall_k 个最相关项，再执行用户要求的最终排序。
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
