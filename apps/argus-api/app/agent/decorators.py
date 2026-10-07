"""라우트의 "문패" — Argus 화면용 API(/api)의 수행업무·데이터 유형

/api 라우트에 문패(@access_log)도 명시적 제외(@access_log_exempt("사유"))도 없으면
앱 기동을 거부한다
(architecture 3-2 — 기록 누락을 조용한 사고가 아닌 즉시 드러나는 실패로).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

API_PREFIX = "/api"

# Argus 자체 기록에서 쓰는 코드값 (api-spec 2-3·2-7). UNMASK는 해당 기능을 만들 때 추가(v0.2)
ACTIONS = frozenset({"LOGIN", "READ", "EXPORT"})  # EXPORT = 점검 보고서 생성 (기능 레이어 9)
DATA_CATEGORIES = frozenset({"NONE", "ACCESS_LOG"})

_SPEC_ATTR = "__access_log__"
_EXEMPT_ATTR = "__access_log_exempt__"


@dataclass(frozen=True)
class AccessLogSpec:
    action: str
    data_category: str


def access_log(*, action: str, data_category: str) -> Callable:
    if action not in ACTIONS:
        raise ValueError(f"unknown access log action: {action}")
    if data_category not in DATA_CATEGORIES:
        raise ValueError(f"unknown access log data_category: {data_category}")
    if (action == "LOGIN") != (data_category == "NONE"):
        raise ValueError("LOGIN must use data_category NONE (and only LOGIN may)")
    spec = AccessLogSpec(action, data_category)

    def mark(fn: Callable) -> Callable:
        setattr(fn, _SPEC_ATTR, spec)
        return fn

    return mark


def access_log_exempt(reason: str) -> Callable:
    """기록 대상이 아닌 /api 라우트 — "빠뜨림"과 구분되도록 사유를 반드시 적는다"""
    if not reason.strip():
        raise ValueError("access_log_exempt requires a reason")

    def mark(fn: Callable) -> Callable:
        setattr(fn, _EXEMPT_ATTR, reason)
        return fn

    return mark


def spec_of(endpoint: Any) -> AccessLogSpec | None:
    return getattr(endpoint, _SPEC_ATTR, None)


def is_api_path(path: str) -> bool:
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


def check_api_routes(app: FastAPI) -> None:
    missing = [
        f"{','.join(sorted(route.methods))} {route.path}"
        for route in app.routes
        if isinstance(route, APIRoute)
        and is_api_path(route.path)
        and spec_of(route.endpoint) is None
        and not hasattr(route.endpoint, _EXEMPT_ATTR)
    ]
    if missing:
        raise RuntimeError(
            "접속기록 설정이 없는 Argus 라우트: "
            + ", ".join(missing)
            + ' → @access_log(...)를 붙이거나, 기록 대상이 아니면 @access_log_exempt("사유")'
        )
