"""platform-api 진입점 — uvicorn --factory app.main:create_app

팩토리 방식: import만으로는 설정을 읽거나 DB 엔진을 만들지 않는다
(테스트가 자기 설정으로 앱을 만들 수 있게). argus-api와 같은 구조.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

from app.agent.client_ip import parse_trusted_proxies
from app.agent.decorators import check_admin_routes
from app.agent.middleware import AccessLogMiddleware
from app.auth.router import router as auth_router
from app.auth.tokens import set_session_cookie
from app.config import Settings
from app.crypto import FieldCipher
from app.errors import install_error_handlers
from app.members.router import router as members_router
from app.orders.router import router as orders_router
from app.shop.auth import router as shop_auth_router
from app.shop.me import router as shop_me_router
from app.shop.orders import router as shop_orders_router
from app.shop.refund import router as shop_refund_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Platform API", version="0.1.0")
    app.state.settings = settings
    # 키가 없거나 짧으면 기동 자체를 거부 — 약한 키로 조용히 도는 것보다 낫다
    app.state.auth_secret = settings.require_auth_secret()
    app.state.engine = create_engine(settings.database_url(), pool_pre_ping=True)
    app.state.trusted_proxies = parse_trusted_proxies(settings.trusted_proxies)
    # 결제수단 암호화 키 — 없거나 형식이 틀리면 기동 거부 (CLAUDE.md 3절 #11)
    app.state.cipher = FieldCipher(settings.require_payment_key())

    install_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(members_router)
    app.include_router(orders_router)
    # 고객 화면 API — /admin이 아니라 Agent가 기록하지 않는다 (CLAUDE.md 3절 #4)
    app.include_router(shop_auth_router)
    app.include_router(shop_me_router)
    app.include_router(shop_orders_router)
    app.include_router(shop_refund_router)

    @app.middleware("http")
    async def admin_response_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/admin", "/shop")):
            # 개인정보가 담긴 응답을 브라우저·프록시가 캐시에 남기지 않게
            response.headers["Cache-Control"] = "no-store"
        # 인증된 요청이면 만료를 연장한 새 토큰으로 쿠키를 갈아 끼운다 (auth/deps.py, shop/deps.py)
        session = getattr(request.state, "session_cookie", None)
        if session is not None:
            kind, token = session
            set_session_cookie(response, token, settings, kind)
        return response

    # 가장 바깥에 둔다 — 안쪽 미들웨어(쿠키 연장)까지 끝난 최종 응답 상태로 결과를 판정하고,
    # 기록에 실패하면 그 응답 전체를 막을 수 있게 (add_middleware는 나중에 추가한 것이 바깥)
    app.add_middleware(AccessLogMiddleware, trusted_proxies=app.state.trusted_proxies)

    @app.get("/healthz")
    def healthz(request: Request):
        # 인증 없음, 접속기록 대상 아님(개인정보 처리 없음)
        try:
            with request.app.state.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "error", "db": "error"}, status_code=503)
        return {"status": "ok", "db": "ok"}

    # 문패 없는 관리자 라우트가 있으면 기동 거부 (agent/decorators.py)
    check_admin_routes(app)

    return app
