"""请求/响应兼容层。

AstrBot v4 新版插件页面使用 ``astrbot.api.web.request``，旧版本使用 Quart 的
``request``。这里做一层最小适配，业务代码只依赖本模块，不直接接触框架对象。

约定：所有页面接口统一返回 HTTP 200 + ``{"success": bool, ...}``，
由前端根据 ``success`` 判断结果，避免不同框架对错误响应的处理差异。
"""

from __future__ import annotations

from typing import Any

try:  # AstrBot >= 4.14 左右提供的新 API
    from astrbot.api.web import request as _request  # type: ignore

    NEW_WEB_API = True
except Exception:  # pragma: no cover - 兼容旧版本
    try:
        from quart import request as _request  # type: ignore

        NEW_WEB_API = False
    except Exception:
        _request = None  # type: ignore[assignment]
        NEW_WEB_API = False


def _query_source() -> Any:
    """获取当前请求的查询参数映射，兼容 AstrBot 与 Quart 的属性名。"""
    # 新版请求对象使用 query，旧版 Quart 使用 args；均不可用时返回空映射。
    if _request is None:
        return {}
    return getattr(_request, "query", None) or getattr(_request, "args", None) or {}


def query_get(name: str, default: Any = None, cast: Any = None) -> Any:
    """读取 query 参数，支持类型转换。"""
    source = _query_source()
    try:
        value = source.get(name, default)
    except Exception:
        return default
    if value is None or cast is None:
        return value
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


async def read_json(default: dict[str, Any] | None = None) -> dict[str, Any]:
    """读取 JSON 请求体，解析失败时返回默认值。"""
    fallback = default if isinstance(default, dict) else {}
    if _request is None:
        return fallback
    data: Any = None
    if NEW_WEB_API:
        try:
            data = await _request.json(default=fallback)
        except Exception:
            return fallback
    else:  # pragma: no cover - 旧版本分支
        try:
            data = await _request.get_json()
        except Exception:
            return fallback
    return data if isinstance(data, dict) else fallback


def ok(data: Any = None, **extra: Any) -> dict[str, Any]:
    """构造页面接口的成功响应，可附带 data 和额外顶层字段。"""
    # 页面接口统一用 success 字段传递成功状态，便于前端跨版本处理。
    payload: dict[str, Any] = {"success": True}
    if data is not None:
        payload["data"] = data
    payload.update(extra)
    return payload


def fail(message: str) -> dict[str, Any]:
    """构造供前端识别并显示的统一失败响应。"""
    # 错误也采用稳定 JSON 结构，由页面统一显示 error 文本。
    return {"success": False, "error": str(message)}
