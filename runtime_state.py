"""进程内运行时状态。

全部状态按 ``unified_msg_origin`` 分桶，并用一个 LRU 队列限制来源数量，
避免长期运行时内存无界增长。所有状态都是内存态，重启即清空。
"""

from __future__ import annotations

from collections import OrderedDict, defaultdict


class RuntimeState:
    """管理进程内短期数据，并按 AstrBot 会话来源隔离所有记录。

    该对象保存群历史、消息发送者索引、图片缓存、用量、调试日志和自动记忆
    抽取节流状态，不负责长期持久化。来源进入 LRU 队列后，超过配置上限时
    会连同各映射中的该来源数据一起淘汰；消息索引与昵称索引另有局部上限，
    防止单个长期活跃群持续增长。
    """

    def __init__(self) -> None:
        """创建所有以会话来源为键的进程内状态映射。"""
        # 按 unified_msg_origin 隔离会话相关数据，避免不同群的状态相互混用。
        # 群聊历史文本（每条一行，已格式化）
        self.session_chats: dict[str, list[str]] = defaultdict(list)
        # 消息 ID -> 发送者 ID，用于校验 @ / 引用的目标是否真实存在
        self.message_senders: dict[str, OrderedDict[str, str]] = defaultdict(
            OrderedDict
        )
        # 消息 ID -> {urls: [...], captions: {index: 描述}, resolved: [...]}
        self.image_registry: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
        # QQ 号 -> 昵称，用于把模型输出的 "@昵称" 还原成真实 @
        self.sender_names: dict[str, dict[str, str]] = defaultdict(dict)
        # 主动回复触发计数
        self.reply_counters: dict[str, int] = defaultdict(int)
        # 本轮 token 用量（按来源累计）
        self.turn_usage: dict[str, dict[str, int]] = defaultdict(
            lambda: {"input_cached": 0, "input_other": 0, "output": 0, "calls": 0}
        )
        # 回复调试开关（按来源）
        self.debug_origins: set[str] = set()
        # 待发送的调试报告
        self.pending_report: dict[str, dict] = {}
        # 本轮执行日志
        self.turn_logs: dict[str, list[str]] = defaultdict(list)
        # 自动记忆：抽取计数与上次抽取时间
        self.extract_counters: dict[str, int] = defaultdict(int)
        self.last_extract_at: dict[str, float] = {}
        # LRU：最左侧最久未使用
        self.origin_lru: OrderedDict[str, None] = OrderedDict()

    # ------------------------------------------------------------------ #
    # 来源生命周期
    # ------------------------------------------------------------------ #
    def _evict_origin(self, origin: str) -> None:
        """从各状态容器中移除指定来源的数据，不触碰长期存储。"""
        # 清除来源关联的所有短期数据；debug_origins 的生命周期由插件管理。
        self.session_chats.pop(origin, None)
        self.message_senders.pop(origin, None)
        self.image_registry.pop(origin, None)
        self.sender_names.pop(origin, None)
        self.reply_counters.pop(origin, None)
        self.turn_usage.pop(origin, None)
        self.turn_logs.pop(origin, None)
        self.pending_report.pop(origin, None)
        self.extract_counters.pop(origin, None)
        self.last_extract_at.pop(origin, None)

    def touch_origin(self, origin: str, max_origins: int) -> None:
        """刷新来源的最近使用顺序，并按来源总数上限执行 LRU 淘汰。

        ``OrderedDict`` 左端代表最久未使用的会话。每次触达会先移除旧位置，
        再插入到队尾；当来源数超限时从队首逐个移出，并调用 ``_evict_origin``
        清理其在所有短期状态映射中的数据。限制至少为一个，避免把当前来源
        也立即淘汰掉。
        """
        if not origin:
            return
        self.origin_lru.pop(origin, None)
        self.origin_lru[origin] = None
        limit = max(1, int(max_origins))
        while len(self.origin_lru) > limit:
            oldest, _ = self.origin_lru.popitem(last=False)
            self._evict_origin(oldest)

    # ------------------------------------------------------------------ #
    # 一轮对话的用量 / 日志
    # ------------------------------------------------------------------ #
    def reset_turn(self, origin: str) -> None:
        """新的一轮开始：清空用量与执行日志。"""
        self.turn_usage[origin] = {
            "input_cached": 0,
            "input_other": 0,
            "output": 0,
            "calls": 0,
        }
        self.turn_logs[origin] = []

    def add_usage(self, origin: str, usage: object) -> None:
        """累加一次模型调用的 token 用量（兼容不同版本的 usage 结构）。"""
        bucket = self.turn_usage[origin]
        bucket["input_cached"] += int(getattr(usage, "input_cached", 0) or 0)
        bucket["input_other"] += int(getattr(usage, "input_other", 0) or 0)
        bucket["output"] += int(getattr(usage, "output", 0) or 0)
        bucket["calls"] += 1

    def add_usage_totals(
        self, origin: str, cached: int, other: int, output: int
    ) -> None:
        """按已归一的三个数值累加 token 用量。"""
        bucket = self.turn_usage[origin]
        bucket["input_cached"] += max(int(cached or 0), 0)
        bucket["input_other"] += max(int(other or 0), 0)
        bucket["output"] += max(int(output or 0), 0)
        bucket["calls"] += 1

    def log_line(self, origin: str, line: str) -> None:
        """将非空执行日志追加到指定来源的当前轮次。"""
        # 忽略空日志，防止调试报告出现无意义的空条目。
        if line:
            self.turn_logs[origin].append(str(line))

    def set_debug(self, origin: str, enabled: bool) -> None:
        """启用或关闭指定来源的回复用量调试。"""
        # 调整该来源的 token 调试状态；该集合只存在于当前进程。
        if enabled:
            self.debug_origins.add(origin)
        else:
            self.debug_origins.discard(origin)

    def is_debug(self, origin: str) -> bool:
        """判断指定来源是否开启用量调试。"""
        # 查询当前来源是否启用回复调试报告。
        return origin in self.debug_origins

    def cleanup_origin(self, origin: str) -> None:
        """彻底清除来源状态及其 LRU 记录。"""
        # 完整清理来源状态并将其从 LRU 队列移除。
        self._evict_origin(origin)
        self.origin_lru.pop(origin, None)

    # ------------------------------------------------------------------ #
    # 便捷读写
    # ------------------------------------------------------------------ #
    def append_chat(self, origin: str, line: str, max_messages: int) -> str:
        """追加一条已格式化的历史行，并将列表裁剪到指定长度。

        如果新增后超过上限，会从最旧的一端持续弹出记录。返回最后一条被移除
        的文本，方便调用方在需要时记录或检查被淘汰的历史；没有发生裁剪则
        返回空字符串。
        """
        chats = self.session_chats[origin]
        chats.append(line)
        removed = ""
        while len(chats) > max(1, int(max_messages)):
            removed = chats.pop(0)
        return removed

    def remember_message(self, origin: str, msg_id: str, sender_id: str) -> None:
        """登记消息发送者，供输出标签引用/目标校验使用。"""
        # 保存消息与发送者关联供标签校验，并限制映射最多保留 2000 条。
        if not msg_id:
            return
        registry = self.message_senders[origin]
        registry[msg_id] = str(sender_id or "")
        registry.move_to_end(msg_id)
        while len(registry) > 2000:
            registry.popitem(last=False)

    def remember_sender(self, origin: str, sender_id: str, nickname: str) -> None:
        """记录 QQ 号与昵称的对应关系（用于无标签的 "@昵称" 兜底）。"""
        sender_id = str(sender_id or "").strip()
        nickname = str(nickname or "").strip()
        if not sender_id or not nickname:
            return
        names = self.sender_names[origin]
        names[sender_id] = nickname
        while len(names) > 500:
            names.pop(next(iter(names)))

    def name_map(self, origin: str) -> dict[str, str]:
        """昵称 -> QQ 号，只返回在本群唯一对应一个 QQ 的昵称。"""
        buckets: dict[str, list[str]] = {}
        for qq, name in self.sender_names.get(origin, {}).items():
            buckets.setdefault(name, []).append(qq)
        return {
            name: qq_list[0] for name, qq_list in buckets.items() if len(qq_list) == 1
        }

    def known_sender(self, origin: str, msg_id: str) -> str:
        """按消息 ID 查发送者 ID；未知消息返回空字符串。"""
        # 查询消息对应的发送者；未知消息返回空字符串。
        return self.message_senders.get(origin, {}).get(str(msg_id), "")

    def reset_counter(self, origin: str) -> None:
        """重置指定来源累计的主动回复消息数。"""
        # 清零当前会话的主动回复累计值。
        self.reply_counters[origin] = 0

    def bump_counter(self, origin: str) -> int:
        """将来源的主动回复计数加一并返回新计数。"""
        # 累加当前会话的主动回复计数并返回更新后的值。
        self.reply_counters[origin] = self.reply_counters.get(origin, 0) + 1
        return self.reply_counters[origin]
