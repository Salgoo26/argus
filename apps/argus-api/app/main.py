"""argus-api 진입점 — uvicorn --factory app.main:create_app

팩토리 방식: import만으로는 설정을 읽거나 DB 엔진을 만들지 않는다
(테스트가 자기 설정으로 앱을 만들 수 있게).

경로 구분
- /ingest/v1/*  시스템 간 수신(①·②) — HMAC 서명 인증, 자체 접속기록 대상 아님
- /api/*        Argus 화면용 API(담당자·취급자) — 로그인 인증, 자체 접속기록 대상(LOG-17)
- /healthz      헬스체크
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

from app.agent.client_ip import parse_trusted_proxies
from app.agent.decorators import check_api_routes, is_api_path
from app.agent.middleware import AccessLogMiddleware
from app.auth.router import router as auth_router
from app.auth.tokens import set_session_cookie
from app.config import Settings
from app.detections.router import router as detections_router
from app.errors import install_error_handlers
from app.ingest.router import router as ingest_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Argus API", version="0.1.0")
    app.state.settings = settings
    # 키가 없거나 짧으면 기동 자체를 거부 — 약한 키로 조용히 도는 것보다 낫다
    app.state.auth_secret = settings.require_auth_secret()
    # pool_pre_ping: DB 재기동 뒤 끊긴 커넥션을 조용히 교체
    app.state.engine = create_engine(settings.database_url(), pool_pre_ping=True)

    install_error_handlers(app)
    app.include_router(ingest_router)
    app.include_router(auth_router)
    app.include_router(detections_router)

    @app.middleware("http")
    async def api_response_headers(request: Request, call_next):
        response = await call_next(request)
        if is_api_path(request.url.path):
            # 탐지건·접속기록이 담긴 응답을 브라우저·프록시가 캐시에 남기지 않게
            response.headers["Cache-Control"] = "no-store"
        # 인증된 요청이면 만료를 연장한 새 토큰으로 쿠키를 갈아 끼운다 (auth/deps.py)
        token = getattr(request.state, "session_token", None)
        if token is not None:
            set_session_cookie(response, token, settings)
        return response

    # 가장 바깥 — 최종 응답 상태로 결과를 판정하고, 기록 실패 시 응답 전체를 막는다
    app.add_middleware(
        AccessLogMiddleware, trusted_proxies=parse_trusted_proxies(settings.trusted_proxies)
    )

    @app.get("/healthz")
    def healthz(request: Request):
        # ③ 헬스체크 (api-spec 5절). 인증 없음, 접속기록 남기지 않음(개인정보 처리 없음)
        try:
            with request.app.state.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "error", "db": "error"}, status_code=503)
        return {"status": "ok", "db": "ok"}

    # 문패 없는 /api 라우트가 있으면 기동 거부 (agent/decorators.py)
    check_api_routes(app)

    return app
