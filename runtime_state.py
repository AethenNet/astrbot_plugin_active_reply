"""进程内运行时状态。

全部状态按 ``unified_msg_origin`` 分桶，并用一个 LRU 队列限制来源数量，
避免长期运行时内存无界增长。所有状态都是内存态，重启即清空。
"""

from __future__ import annotations

from collections import OrderedDict, defaultdict


class RuntimeState:
    """按会话来源隔离的运行时状态容器。"""

    def __init__(self) -> None:
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
        """标记来源被使用，并在超出上限时淘汰最久未使用的来源。"""
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

    def log_line(self, origin: str, line: str) -> None:
        if line:
            self.turn_logs[origin].append(str(line))

    def set_debug(self, origin: str, enabled: bool) -> None:
        if enabled:
            self.debug_origins.add(origin)
        else:
            self.debug_origins.discard(origin)

    def is_debug(self, origin: str) -> bool:
        return origin in self.debug_origins

    def cleanup_origin(self, origin: str) -> None:
        self._evict_origin(origin)
        self.origin_lru.pop(origin, None)

    # ------------------------------------------------------------------ #
    # 便捷读写
    # ------------------------------------------------------------------ #
    def append_chat(self, origin: str, line: str, max_messages: int) -> str:
        """追加一行历史，返回被挤出的那一行（没有则为空字符串）。"""
        chats = self.session_chats[origin]
        chats.append(line)
        removed = ""
        while len(chats) > max(1, int(max_messages)):
            removed = chats.pop(0)
        return removed

    def remember_message(self, origin: str, msg_id: str, sender_id: str) -> None:
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
        return self.message_senders.get(origin, {}).get(str(msg_id), "")

    def reset_counter(self, origin: str) -> None:
        self.reply_counters[origin] = 0

    def bump_counter(self, origin: str) -> int:
        self.reply_counters[origin] = self.reply_counters.get(origin, 0) + 1
        return self.reply_counters[origin]
