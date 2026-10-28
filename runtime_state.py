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
