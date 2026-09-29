"""argus-api 진입점 — uvicorn --factory app.main:create_app

팩토리 방식: import만으로는 설정을 읽거나 DB 엔진을 만들지 않는다
(테스트가 자기 설정으로 앱을 만들 수 있게).
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

from app.config import Settings
from app.errors import install_error_handlers
from app.ingest.router import router as ingest_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Argus API", version="0.1.0")
    app.state.settings = settings
    # pool_pre_ping: DB 재기동 뒤 끊긴 커넥션을 조용히 교체
    app.state.engine = create_engine(settings.database_url(), pool_pre_ping=True)

    install_error_handlers(app)
    app.include_router(ingest_router)

    @app.get("/healthz")
    def healthz(request: Request):
        # ③ 헬스체크 (api-spec 5절). 인증 없음, 접속기록 남기지 않음(개인정보 처리 없음)
        try:
            with request.app.state.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "error", "db": "error"}, status_code=503)
        return {"status": "ok", "db": "ok"}

    return app
