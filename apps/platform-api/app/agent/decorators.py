"""라우트의 "문패" — 이 관리자 기능이 어떤 수행업무·데이터 유형인지 (URL → 업무 매핑)

@router.get("/export")
@access_log(action="DOWNLOAD", data_category="MEMBER_BASIC")
def export_members(...): ...

함수에 표시만 붙이고 그대로 돌려준다(감싸지 않음) — FastAPI가 보는 함수 시그니처가 바뀌지 않게.
미들웨어는 요청이 끝날 때 매칭된 라우트의 함수에서 이 표시를 읽는다.

check_admin_routes: /admin 라우트에 문패도, 명시적 제외 표시도 없으면 앱 기동을 거부한다.
관리자 기능을 추가하며 데코레이터를 빠뜨리면 기록 없이 개인정보가 나가는 구멍이 조용히 생기기 때문
(Spring이라면 ArchUnit으로 "모든 관리자 컨트롤러에 @AccessLog"를 강제하는 것과 같은 역할).
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

ADMIN_PREFIX = "/admin"

# api-spec 2-3. Argus 전용 코드값(EXPORT·UNMASK / ACCESS_LOG)은 플랫폼이 쓸 수 없다
ACTIONS = frozenset({"LOGIN", "LOGOUT", "READ", "CREATE", "UPDATE", "DELETE", "DOWNLOAD"})
# 정보주체를 처리하지 않는 행위 — 데이터 유형 NONE, 정보주체 생략
# (LOGOUT: v0.1 보강 A — 안내서 FAQ 147 "로그인·로그아웃·로그인 실패")
SESSION_ACTIONS = frozenset({"LOGIN", "LOGOUT"})
DATA_CATEGORIES = frozenset({"MEMBER_BASIC", "PAYMENT", "ORDER", "INQUIRY", "NONE"})

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
    if (action in SESSION_ACTIONS) != (data_category == "NONE"):
        # 로그인·로그아웃은 데이터 처리가 없고, 데이터 처리가 없는 행위는 이 둘뿐이다 (api-spec 2-4)
        raise ValueError("LOGIN/LOGOUT must use data_category NONE (and only they may)")
    spec = AccessLogSpec(action, data_category)

    def mark(fn: Callable) -> Callable:
        setattr(fn, _SPEC_ATTR, spec)
        return fn

    return mark


def access_log_exempt(reason: str) -> Callable:
    """기록 대상이 아닌 관리자 라우트 — "빠뜨림"과 구분되도록 사유를 반드시 적는다."""
    if not reason.strip():
        raise ValueError("access_log_exempt requires a reason")

    def mark(fn: Callable) -> Callable:
        setattr(fn, _EXEMPT_ATTR, reason)
        return fn

    return mark


def spec_of(endpoint: Any) -> AccessLogSpec | None:
    return getattr(endpoint, _SPEC_ATTR, None)


def is_admin_path(path: str) -> bool:
    return path == ADMIN_PREFIX or path.startswith(ADMIN_PREFIX + "/")


def check_admin_routes(app: FastAPI) -> None:
    missing = [
        f"{','.join(sorted(route.methods))} {route.path}"
        for route in app.routes
        if isinstance(route, APIRoute)
        and is_admin_path(route.path)
        and spec_of(route.endpoint) is None
        and not hasattr(route.endpoint, _EXEMPT_ATTR)
    ]
    if missing:
        raise RuntimeError(
            "접속기록 설정이 없는 관리자 라우트: "
            + ", ".join(missing)
            + ' → @access_log(...)를 붙이거나, 기록 대상이 아니면 @access_log_exempt("사유")'
        )
