"""插件配置模型。

把 AstrBot 传入的原始 dict 配置统一解析成不可变 dataclass，
并在这里集中处理默认值、类型兼容与范围钳制，避免业务代码到处 .get()。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

DEFAULT_WEB_SEARCH_SYSTEM_PROMPT = (
    "You are a web research assistant. Use live web search/browsing when answering. "
    "Return ONLY a single JSON object with keys: "
    "content (string), sources (array of objects with url/title/snippet when possible). "
    "Keep content concise and evidence-backed. "
    "IMPORTANT: Do NOT use Markdown formatting in the content field - use plain text only."
)
DEFAULT_IMAGE_CAPTION_PROMPT = "Please describe the image using Chinese."


# --------------------------------------------------------------------------- #
# 基础类型转换
# --------------------------------------------------------------------------- #
def _to_bool(value: Any, default: bool) -> bool:
    """将布尔值、常见字符串或数字转换为 bool。

    Args:
        value: 原始配置值。
        default: 输入无法识别时使用的值。
    """
    # 兼容面板、JSON 与历史配置中的布尔写法；不认识的值沿用字段默认值。
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _to_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """解析整数并限制在闭区间 ``[minimum, maximum]`` 内。"""
    # 将输入安全转换为整数，并统一钳制到配置项允许的闭区间。
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _to_float(
    value: Any,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    """解析有限浮点数并限制范围；无效输入返回默认值。"""
    # 过滤非法及非有限浮点数，避免 NaN/无穷值进入运行时配置。
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(minimum, min(maximum, parsed))


def _to_str(value: Any, default: str = "") -> str:
    """去除字符串首尾空白，并为 None 或空字符串应用默认值。"""
    # 去除字符串两侧空白；空值回落到调用方提供的默认值。
    text = str(value).strip() if value is not None else ""
    return text or default


def _to_probability(value: Any, default: float) -> float:
    """解析概率值，保证返回结果位于 0 到 1 之间。"""
    # 概率配置限定在 [0, 1]，复用浮点数的统一校验逻辑。
    return _to_float(value, default, 0.0, 1.0)


def _parse_str_list(value: Any) -> tuple[str, ...]:
    """把 list / 逗号分隔字符串统一成去重后的字符串元组。"""
    items: list[str] = []
    if isinstance(value, str):
        items = [token.strip() for token in value.replace("\n", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        items = [str(token).strip() for token in value]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return tuple(deduped)


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    """读取配置区块；字段缺失或不是字典时返回空字典。"""
    # 配置区块必须为字典，其他类型视为缺省区块以便后续读取安全。
    value = raw.get(key)
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- #
# 各配置区块
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ActiveReplyConfig:
    """主动发言触发策略和生效群范围。

    ``mode`` 决定使用概率抽样还是模型判定；两种模式都依据
    ``trigger_threshold`` 维护每群消息计数。名单模式为白名单时仅允许
    ``whitelist`` 中的群，为黑名单时除 ``blacklist`` 外均允许。
    """

    enable: bool = False
    mode: str = "probability"
    possibility: float = 0.1
    model_history_messages: int = 0
    model_choice_provider_id: str = ""
    trigger_threshold: int = 10
    list_mode: str = "blacklist"
    blacklist: tuple[str, ...] = ()
    whitelist: tuple[str, ...] = ()


@dataclass(frozen=True)
class GroupContextConfig:
    """供模型参考的群聊历史及图片描述设置。

    ``enable`` 控制历史采集/注入，``max_messages`` 限制每个会话保留的消息数；
    发送者 ID 和角色标记用于帮助模型识别发言人。图片描述需要单独开启，
    并可指定用于视觉理解的提供商与提示词。
    """

    enable: bool = True
    max_messages: int = 200
    include_sender_id: bool = True
    include_role_tag: bool = True
    image_caption: bool = False
    image_caption_provider_id: str = ""
    image_caption_prompt: str = DEFAULT_IMAGE_CAPTION_PROMPT


@dataclass(frozen=True)
class GroupReplyConfig:
    """群聊互动增强的父开关及子功能开关。

    ``react_pattern`` 是总开关；``role_display`` 与 ``smart_reply`` 分别控制
    是否把成员角色及 @/引用互动规则提供给模型，单独开启子开关不会越过总开关。
    """

    react_pattern: bool = False
    role_display: bool = True
    smart_reply: bool = True


@dataclass(frozen=True)
class WebSearchConfig:
    """联网搜索工具的提供商、协议和结果展示配置。"""

    enable: bool = False
    provider_id: str = ""
    system_prompt: str = DEFAULT_WEB_SEARCH_SYSTEM_PROMPT
    timeout_sec: float = 60.0
    request_mode: str = "auto"
    base_url_override: str = ""
    show_sources: bool = False
    max_sources: int = 5


@dataclass(frozen=True)
class MemoryRagConfig:
    """长期记忆检索及自动写入/抽取策略。

    ``recall_k`` 限制语义召回候选数，``max_return_results`` 限制工具返回上限；
    自动记忆由关键词快速写入和按消息窗口调用模型抽取两条路径组成。抽取配置
    分别控制模型、消息频率、最小间隔、历史窗口、置信度门槛及群/全局作用域。
    """

    enable: bool = True
    embedding_provider_id: str = ""
    recall_k: int = 20
    max_return_results: int = 100
    # 自动记忆（方案 C：关键词快速通道 + 后台模型抽取）
    auto_extract_enable: bool = True
    auto_extract_provider_id: str = ""
    auto_extract_every_n: int = 10
    auto_extract_interval_minutes: int = 5
    auto_extract_window: int = 20
    auto_extract_min_confidence: float = 0.6
    auto_extract_scope: str = "group_first"


@dataclass(frozen=True)
class RuntimeConfig:
    """运行时内存容量及外部模型调用超时配置。"""

    max_origins: int = 500
    image_caption_timeout: float = 45.0
    model_choice_timeout: float = 45.0


@dataclass(frozen=True)
class PluginConfig:
    """经过规范化的插件配置快照。

    各字段对应一个不可变配置区块，避免业务逻辑直接操作原始 JSON 字典。
    ``default_factory`` 为每个配置对象分别创建缺省子配置，避免实例之间共享状态。
    ``history_enabled``、``react_enabled`` 等属性是底层开关组合出的便捷视图，
    不会在解析过程中额外存储第二份可变状态。
    """

    active_reply: ActiveReplyConfig = field(default_factory=ActiveReplyConfig)
    group_context: GroupContextConfig = field(default_factory=GroupContextConfig)
    group_reply: GroupReplyConfig = field(default_factory=GroupReplyConfig)
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    memory_rag: MemoryRagConfig = field(default_factory=MemoryRagConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    # ---- 派生属性 ------------------------------------------------------- #
    @property
    def history_enabled(self) -> bool:
        """返回群聊历史记录与注入功能的总开关状态。"""
        # 兼容调用方使用 history_enabled 命名访问上下文增强总开关。
        return self.group_context.enable

    @property
    def react_enabled(self) -> bool:
        """群聊回复增强整块是否生效（由 React Pattern 总开关控制）。"""
        return self.group_reply.react_pattern

    @property
    def smart_reply_enabled(self) -> bool:
        """返回 React Pattern 与智能回复子开关同时开启的状态。"""
        # React Pattern 是父级开关；子级智能回复开关不能单独越过它生效。
        return self.react_enabled and self.group_reply.smart_reply

    @property
    def role_display_enabled(self) -> bool:
        """返回 React Pattern 与用户角色显示子开关同时开启的状态。"""
        # 角色提示同样受 React Pattern 总开关约束。
        return self.react_enabled and self.group_reply.role_display


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def parse_plugin_config(raw: dict[str, Any] | None) -> PluginConfig:
    """将 AstrBot 原始配置映射成完整、不可变且范围受限的配置快照。

    每个区块先通过 ``_section`` 确认是字典，再使用对应的类型转换函数读取
    字段；缺失或无法识别的值回退到插件默认值，超出合法范围的数值则被钳制。
    枚举字段会映射到支持值，名单会拆分并去重。此函数不修改传入字典，
    因而调用方可在每次配置更新后重新解析并安全地将结果交给业务代码。

    Args:
        raw: AstrBot 加载的插件配置；``None`` 或非字典区块按默认值处理。

    Returns:
        包含所有配置区块和派生开关的 ``PluginConfig`` 实例。
    """
    raw = raw or {}

    ar_raw = _section(raw, "active_reply")
    mode = _to_str(ar_raw.get("mode"), "probability").lower()
    if mode not in {"probability", "model_choice"}:
        mode = "probability"
    list_mode = _to_str(ar_raw.get("list_mode"), "blacklist").lower()
    if list_mode not in {"blacklist", "whitelist"}:
        list_mode = "blacklist"
    active_reply = ActiveReplyConfig(
        enable=_to_bool(ar_raw.get("enable"), False),
        mode=mode,
        possibility=_to_probability(ar_raw.get("possibility"), 0.1),
        model_history_messages=_to_int(ar_raw.get("model_history_messages"), 0, 0, 100),
        model_choice_provider_id=_to_str(ar_raw.get("model_choice_provider_id")),
        trigger_threshold=_to_int(ar_raw.get("trigger_threshold"), 10, 2, 50),
        list_mode=list_mode,
        blacklist=_parse_str_list(ar_raw.get("blacklist")),
        whitelist=_parse_str_list(ar_raw.get("whitelist")),
    )

    ctx_raw = _section(raw, "group_context_enhancement")
    group_context = GroupContextConfig(
        enable=_to_bool(ctx_raw.get("enable"), True),
        max_messages=_to_int(ctx_raw.get("max_messages"), 200, 50, 1000),
        include_sender_id=_to_bool(ctx_raw.get("include_sender_id"), True),
        include_role_tag=_to_bool(ctx_raw.get("include_role_tag"), True),
        image_caption=_to_bool(ctx_raw.get("image_caption"), False),
        image_caption_provider_id=_to_str(ctx_raw.get("image_caption_provider_id")),
        image_caption_prompt=_to_str(
            ctx_raw.get("image_caption_prompt"), DEFAULT_IMAGE_CAPTION_PROMPT
        ),
    )

    reply_raw = _section(raw, "group_reply_enhancement")
    group_reply = GroupReplyConfig(
        react_pattern=_to_bool(reply_raw.get("react_pattern"), False),
        role_display=_to_bool(reply_raw.get("role_display"), True),
        smart_reply=_to_bool(reply_raw.get("smart_reply"), True),
    )

    ws_raw = _section(raw, "web_search")
    request_mode = _to_str(ws_raw.get("request_mode"), "auto").lower()
    if request_mode not in {"auto", "responses", "chat_completions"}:
        request_mode = "auto"
    web_search = WebSearchConfig(
        enable=_to_bool(ws_raw.get("enable"), False),
        provider_id=_to_str(ws_raw.get("provider_id")),
        system_prompt=_to_str(
            ws_raw.get("system_prompt"), DEFAULT_WEB_SEARCH_SYSTEM_PROMPT
        ),
        timeout_sec=_to_float(ws_raw.get("timeout_sec"), 60.0, 30.0, 180.0),
        request_mode=request_mode,
        base_url_override=_to_str(ws_raw.get("base_url_override")),
        show_sources=_to_bool(ws_raw.get("show_sources"), False),
        max_sources=_to_int(ws_raw.get("max_sources"), 5, 1, 20),
    )

    mem_raw = _section(raw, "memory_rag")
    scope_raw = _to_str(mem_raw.get("auto_extract_scope")) or "group_first"
    if scope_raw not in {"group_first", "global", "group"}:
        scope_raw = "group_first"
    memory_rag = MemoryRagConfig(
        enable=_to_bool(mem_raw.get("enable"), True),
        embedding_provider_id=_to_str(mem_raw.get("embedding_provider_id")),
        recall_k=_to_int(mem_raw.get("recall_k"), 20, 1, 50),
        max_return_results=_to_int(mem_raw.get("max_return_results"), 100, 1, 300),
        auto_extract_enable=_to_bool(mem_raw.get("auto_extract_enable"), True),
        auto_extract_provider_id=_to_str(mem_raw.get("auto_extract_provider_id")),
        auto_extract_every_n=_to_int(mem_raw.get("auto_extract_every_n"), 10, 3, 100),
        auto_extract_interval_minutes=_to_int(
            mem_raw.get("auto_extract_interval_minutes"), 5, 1, 120
        ),
        auto_extract_window=_to_int(mem_raw.get("auto_extract_window"), 20, 5, 60),
        auto_extract_min_confidence=_to_float(
            mem_raw.get("auto_extract_min_confidence"), 0.6, 0.0, 1.0
        ),
        auto_extract_scope=scope_raw,
    )

    rt_raw = _section(raw, "runtime")
    lru_raw = _section(rt_raw, "lru_cache")
    timeouts_raw = _section(rt_raw, "timeouts")
    runtime = RuntimeConfig(
        max_origins=_to_int(lru_raw.get("max_origins"), 500, 50, 500),
        image_caption_timeout=_to_float(
            timeouts_raw.get("image_caption_sec"), 45.0, 10.0, 120.0
        ),
        model_choice_timeout=_to_float(
            timeouts_raw.get("model_choice_sec"), 45.0, 10.0, 120.0
        ),
    )

    return PluginConfig(
        active_reply=active_reply,
        group_context=group_context,
        group_reply=group_reply,
        web_search=web_search,
        memory_rag=memory_rag,
        runtime=runtime,
    )


# --------------------------------------------------------------------------- #
# 每群独立配置
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GroupFieldSpec:
    """每群独立配置中允许覆盖的字段描述。"""

    path: str
    label: str
    kind: str  # bool / enum / int / float / str / text
    options: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    minimum: float = 0
    maximum: float = 0
    step: float = 1


# 说明：只允许覆盖与"群内行为"相关的字段，搜索/记忆/超时属于全局服务配置。
GROUP_OVERRIDE_FIELDS: tuple[GroupFieldSpec, ...] = (
    GroupFieldSpec("active_reply.enable", "启用主动回复", "bool"),
    GroupFieldSpec(
        "active_reply.mode",
        "回复方式",
        "enum",
        options=("probability", "model_choice"),
        labels=("概率", "模型判定"),
    ),
    GroupFieldSpec(
        "active_reply.possibility", "回复概率", "float", minimum=0, maximum=1, step=0.1
    ),
    GroupFieldSpec(
        "active_reply.model_history_messages",
        "模型判定消息附带",
        "int",
        minimum=0,
        maximum=100,
    ),
    GroupFieldSpec("active_reply.model_choice_provider_id", "模型判定提供商", "str"),
    GroupFieldSpec(
        "active_reply.trigger_threshold",
        "回复触发积累",
        "int",
        minimum=2,
        maximum=50,
    ),
    GroupFieldSpec("group_context_enhancement.enable", "启用群聊上下文增强", "bool"),
    GroupFieldSpec(
        "group_context_enhancement.max_messages",
        "会话消息上限",
        "int",
        minimum=50,
        maximum=1000,
        step=10,
    ),
    GroupFieldSpec(
        "group_context_enhancement.include_sender_id", "包含用户信息", "bool"
    ),
    GroupFieldSpec("group_context_enhancement.include_role_tag", "包含标签", "bool"),
    GroupFieldSpec("group_context_enhancement.image_caption", "图片转述", "bool"),
    GroupFieldSpec(
        "group_context_enhancement.image_caption_provider_id", "图片转述提供商", "str"
    ),
    GroupFieldSpec(
        "group_context_enhancement.image_caption_prompt", "图片转述文本", "text"
    ),
    GroupFieldSpec("group_reply_enhancement.react_pattern", "React Pattern", "bool"),
    GroupFieldSpec("group_reply_enhancement.role_display", "用户角色显示", "bool"),
    GroupFieldSpec("group_reply_enhancement.smart_reply", "智能回复", "bool"),
)

GROUP_FIELD_BY_PATH = {spec.path: spec for spec in GROUP_OVERRIDE_FIELDS}


def deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """递归合并配置 dict，overrides 优先。"""
    merged: dict[str, Any] = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def get_by_path(data: dict[str, Any], path: str) -> Any:
    """沿点分隔路径读取嵌套字典；路径不存在时返回 None。

    Args:
        data: 根配置字典。
        path: 例如 ``active_reply.enable``。
    """
    # 按点分隔的路径读取嵌套配置；路径任一层不存在时返回 None。
    current: Any = data
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def set_by_path(data: dict[str, Any], path: str, value: Any) -> None:
    """按点分隔路径创建中间字典并写入配置值。"""
    # 按点分隔路径逐层创建字典节点，再写入字段值。
    keys = path.split(".")
    current = data
    for key in keys[:-1]:
        node = current.get(key)
        if not isinstance(node, dict):
            node = {}
            current[key] = node
        current = node
    current[keys[-1]] = value


def coerce_group_value(spec: GroupFieldSpec, value: Any) -> Any:
    """按字段描述把页面提交的值转换为合法配置值，非法值回落到默认。"""
    default = _field_default(spec)
    if spec.kind == "bool":
        return _to_bool(value, bool(default))
    if spec.kind == "enum":
        text = _to_str(value, str(default))
        return text if text in spec.options else default
    if spec.kind == "int":
        return _to_int(value, int(default), int(spec.minimum), int(spec.maximum))
    if spec.kind == "float":
        return round(
            _to_float(value, float(default), spec.minimum, spec.maximum),
            3,
        )
    if spec.kind == "text":
        return str(value if value is not None else default).strip() or default
    return _to_str(value, str(default))


def _field_default(spec: GroupFieldSpec) -> Any:
    """根据群级字段定义返回该项的插件默认值。"""
    # 群级覆盖表单使用独立默认值，避免依赖未加载的全局配置实例。
    defaults: dict[str, Any] = {
        "active_reply.enable": False,
        "active_reply.mode": "probability",
        "active_reply.possibility": 0.1,
        "active_reply.model_history_messages": 0,
        "active_reply.model_choice_provider_id": "",
        "active_reply.trigger_threshold": 10,
        "group_context_enhancement.enable": True,
        "group_context_enhancement.max_messages": 200,
        "group_context_enhancement.include_sender_id": True,
        "group_context_enhancement.include_role_tag": True,
        "group_context_enhancement.image_caption": False,
        "group_context_enhancement.image_caption_provider_id": "",
        "group_context_enhancement.image_caption_prompt": DEFAULT_IMAGE_CAPTION_PROMPT,
        "group_reply_enhancement.react_pattern": False,
        "group_reply_enhancement.role_display": True,
        "group_reply_enhancement.smart_reply": True,
    }
    return defaults.get(spec.path, "")


def build_global_snapshot(cfg: PluginConfig) -> dict[str, Any]:
    """生成每群配置面板需要的"全局值"快照。"""
    raw = {
        "active_reply": {
            "enable": cfg.active_reply.enable,
            "mode": cfg.active_reply.mode,
            "possibility": cfg.active_reply.possibility,
            "model_history_messages": cfg.active_reply.model_history_messages,
            "model_choice_provider_id": cfg.active_reply.model_choice_provider_id,
            "trigger_threshold": cfg.active_reply.trigger_threshold,
        },
        "group_context_enhancement": {
            "enable": cfg.group_context.enable,
            "max_messages": cfg.group_context.max_messages,
            "include_sender_id": cfg.group_context.include_sender_id,
            "include_role_tag": cfg.group_context.include_role_tag,
            "image_caption": cfg.group_context.image_caption,
            "image_caption_provider_id": cfg.group_context.image_caption_provider_id,
            "image_caption_prompt": cfg.group_context.image_caption_prompt,
        },
        "group_reply_enhancement": {
            "react_pattern": cfg.group_reply.react_pattern,
            "role_display": cfg.group_reply.role_display,
            "smart_reply": cfg.group_reply.smart_reply,
        },
    }
    return {spec.path: get_by_path(raw, spec.path) for spec in GROUP_OVERRIDE_FIELDS}


def active_reply_allowed(group_id: str, cfg: PluginConfig) -> bool:
    """按黑/白名单判断该群是否允许主动回复。"""
    group_id = str(group_id or "").strip()
    ar = cfg.active_reply
    if ar.list_mode == "whitelist":
        return bool(ar.whitelist) and group_id in ar.whitelist
    return group_id not in ar.blacklist


# --------------------------------------------------------------------------- #
# 页面里的"全局设置"表单：按 _conf_schema.json 做类型转换
# --------------------------------------------------------------------------- #
def coerce_schema_leaf(meta: dict[str, Any], raw: Any) -> Any:
    """把一个表单值按 schema 描述转换成合法配置值。"""
    kind = str(meta.get("type") or "string")
    default = meta.get("default")
    slider = meta.get("slider") if isinstance(meta.get("slider"), dict) else {}

    if kind == "bool":
        return _to_bool(raw, bool(default))
    if kind == "int":
        low = int(slider.get("min", -2_147_483_648))
        high = int(slider.get("max", 2_147_483_647))
        return _to_int(raw, int(default or 0), low, high)
    if kind == "float":
        low = float(slider.get("min", -1e18))
        high = float(slider.get("max", 1e18))
        return round(_to_float(raw, float(default or 0.0), low, high), 4)
    if kind == "list":
        return list(_parse_str_list(raw))
    if kind == "dict":
        return raw if isinstance(raw, dict) else (default or {})

    text = str(raw if raw is not None else "").strip()
    options = meta.get("options")
    if isinstance(options, list) and options:
        if text in options:
            return text
        fallback = default if default in options else options[0]
        return str(fallback)
    if not text and default is not None and not text:
        # 允许用户清空字符串（例如 base_url_override），因此不回落到默认值
        return text
    return text


def _lookup_value(data: Any, path: str) -> tuple[bool, Any]:
    """同时支持"扁平点号键"和"嵌套 dict"两种提交格式。"""
    if isinstance(data, dict) and path in data:
        return True, data[path]
    node: Any = data
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False, None
    return True, node


def build_schema_patch(
    schema: dict[str, Any],
    submitted: dict[str, Any],
    prefix: str = "",
) -> dict[str, Any]:
    """根据 schema 从表单值中挑出合法字段，返回可直接合并进配置的嵌套 dict。

    页面提交的 ``submitted`` 既可以是 ``{"active_reply.enable": True}`` 这种
    扁平点号键，也可以是 ``{"active_reply": {"enable": True}}`` 这种嵌套结构。
    """
    patch: dict[str, Any] = {}
    for key, meta in schema.items():
        if not isinstance(meta, dict):
            continue
        path = f"{prefix}{key}"
        items = meta.get("items")
        if meta.get("type") == "object" and isinstance(items, dict):
            child = build_schema_patch(items, submitted, f"{path}.")
            if child:
                patch[key] = child
            continue
        found, value = _lookup_value(submitted, path)
        if not found:
            continue
        patch[key] = coerce_schema_leaf(meta, value)
    return patch
