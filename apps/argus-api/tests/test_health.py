"""③ 헬스체크 (api-spec 5절)"""

from fastapi.testclient import TestClient

from app.main import create_app


def test_healthz_ok(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "db": "ok"}


def test_healthz_503_when_db_unreachable(settings):
    # 아무도 듣지 않는 포트로 접속 → DB 실패
    broken = settings.model_copy(update={"db_host": "127.0.0.1", "db_port": 1})
    app = create_app(broken)
    with TestClient(app) as c:
        res = c.get("/healthz")
    app.state.engine.dispose()
    assert res.status_code == 503
    assert res.json() == {"status": "error", "db": "error"}
