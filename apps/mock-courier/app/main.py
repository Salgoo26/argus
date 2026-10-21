"""가상 택배 서비스 진입점 — uvicorn --factory app.main:create_app

쇼핑몰(플랫폼)과 분리된 외부 수탁자(배송) 역할. 택배사 연동 API(접수·조회)를 단순화했다.

쇼핑몰 서버(Bearer API 키)
    POST /v1/shipments               배송 접수 — 받는 사람·배송 메모·상품·주문번호·보내는 곳
    GET  /v1/shipments/{id}          배송·회수 조회 (이력 포함)
    POST /v1/shipments/{id}/cancel   접수 취소 — 집하 전만
    POST /v1/returns                 반품 회수 접수 — 회수지(고객)·돌려보낼 곳(쇼핑몰)·상품
이 서비스 → 쇼핑몰
    상태가 바뀔 때마다 POST {COURIER_WEBHOOK_URL} (서명 X-Courier-Signature) — app/worker.py

화면은 없다. 진행 속도는 setting 표에서 바꾸고, 상태를 직접 바꾸려면 DB의 shipment.status를
고친다(트리거가 나머지를 맞춤).
"""

import hmac
import json
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

from app import worker
from app.config import Settings
from app.db import create_schema, mark_source
from app.shipments import CreateReturn, CreateShipment, Party, shipment_json


class CourierError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message


def _party(prefix: str, party: Party) -> dict:
    return {
        f"{prefix}_name": party.name,
        f"{prefix}_phone": party.phone,
        f"{prefix}_zip_code": party.zipCode,
        f"{prefix}_address": party.address,
        f"{prefix}_address_detail": party.addressDetail,
    }


def create_app(settings: Settings | None = None, send: worker.Sender | None = None) -> FastAPI:
    settings = settings or Settings()
    api_key, webhook_secret = settings.require_keys()
    company = settings.company_name
    engine = create_engine(settings.database_url(), pool_pre_ping=True)
    create_schema(engine)
    send = send or worker.urllib_send

    def run_tick() -> None:
        worker.tick(engine, settings.webhook_url, webhook_secret, company, send)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop = worker.start(settings.tick_seconds, run_tick) if settings.worker_enabled else None
        yield
        if stop is not None:
            stop.set()

    app = FastAPI(
        title="Mock Courier", version="0.1.0", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.state.engine = engine
    app.state.tick = run_tick

    @app.exception_handler(CourierError)
    async def _courier_error(_: Request, exc: CourierError) -> JSONResponse:
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {"code": "INVALID_REQUEST", "message": "malformed request"}, status_code=400
        )

    @app.middleware("http")
    async def require_key(request: Request, call_next):
        # 본문 검사보다 먼저 — 키 없는 요청에는 입력 형식에 대한 단서도 주지 않는다
        if request.url.path.startswith("/v1/"):
            header = request.headers.get("authorization", "")
            if not hmac.compare_digest(header.encode(), f"Bearer {api_key}".encode()):
                return JSONResponse(
                    {"code": "UNAUTHORIZED", "message": "invalid api key"}, status_code=401
                )
        return await call_next(request)

    select_shipment = text("SELECT * FROM shipment WHERE shipment_id = :id")
    select_shipment_for_update = text("SELECT * FROM shipment WHERE shipment_id = :id FOR UPDATE")

    def load(conn, shipment_id: str, *, lock: bool = False):
        query = select_shipment_for_update if lock else select_shipment
        row = conn.execute(query, {"id": shipment_id}).mappings().first()
        if row is None:
            raise CourierError(404, "NOT_FOUND", "shipment not found")
        return row

    def create(kind: str, order_no: str, values: dict) -> tuple[dict, bool]:
        status = "RECEIVED" if kind == "DELIVERY" else "RETURN_RECEIVED"
        with engine.begin() as conn:
            mark_source(conn, "API")
            row = (
                conn.execute(
                    text(
                        # 열 이름은 코드 상수(_party·고정 키)뿐, 값은 모두 바인딩
                        "INSERT INTO shipment (shipment_id, kind, client_order_no, status,"  # noqa: S608
                        " notified_status, " + ", ".join(values) + ")"
                        " VALUES (:shipment_id, :kind, :order_no, :status, :status, "
                        + ", ".join(f":{k}" for k in values)
                        + ") ON CONFLICT ON CONSTRAINT shipment_order_unique DO NOTHING"
                        " RETURNING *"
                    ),
                    {
                        "shipment_id": ("SHP" if kind == "DELIVERY" else "RTN")
                        + secrets.token_hex(8).upper(),
                        "kind": kind,
                        "order_no": order_no,
                        "status": status,
                        **values,
                    },
                )
                .mappings()
                .first()
            )
            if row is not None:
                return shipment_json(row, company), True
            # 같은 주문을 다시 접수하면 기존 접수를 돌려준다 (네트워크 재시도 대비)
            row = (
                conn.execute(
                    text("SELECT * FROM shipment WHERE kind = :k AND client_order_no = :o"),
                    {"k": kind, "o": order_no},
                )
                .mappings()
                .one()
            )
            return shipment_json(row, company), False

    @app.post("/v1/shipments")
    def create_shipment(body: CreateShipment):
        shipment, created = create(
            "DELIVERY",
            body.orderNo,
            {
                **_party("sender", body.sender),
                **_party("receiver", body.receiver),
                "memo": body.memo,
                "items": json.dumps([i.model_dump() for i in body.items], ensure_ascii=False),
            },
        )
        return JSONResponse(shipment, status_code=201 if created else 200)

    @app.post("/v1/returns")
    def create_return(body: CreateReturn):
        shipment, created = create(
            "RETURN",
            body.orderNo,
            {
                **_party("sender", body.pickup),
                **_party("receiver", body.returnTo),
                "memo": body.memo,
                "original_tracking_no": body.originalTrackingNo,
                "items": json.dumps([i.model_dump() for i in body.items], ensure_ascii=False),
            },
        )
        return JSONResponse(shipment, status_code=201 if created else 200)

    @app.get("/v1/shipments/{shipment_id}")
    def get_shipment(shipment_id: str):
        with engine.connect() as conn:
            row = load(conn, shipment_id)
            events = (
                conn.execute(
                    text(
                        "SELECT status, occurred_at FROM shipment_event"
                        " WHERE shipment_id = :id ORDER BY id"
                    ),
                    {"id": shipment_id},
                )
                .mappings()
                .all()
            )
        return shipment_json(row, company, events)

    @app.post("/v1/shipments/{shipment_id}/cancel")
    def cancel_shipment(shipment_id: str):
        """접수 취소 — 기사가 집하하기 전(접수 상태)에만"""
        with engine.begin() as conn:
            mark_source(conn, "API")
            row = load(conn, shipment_id, lock=True)
            if row["status"] == "CANCELED":
                return shipment_json(row, company)
            if row["status"] not in ("RECEIVED", "RETURN_RECEIVED"):
                raise CourierError(409, "ALREADY_PICKED_UP", f"status is {row['status']}")
            # 쇼핑몰이 요청한 취소라 통지하지 않는다(통지 완료로 표시)
            row = (
                conn.execute(
                    text(
                        "UPDATE shipment SET status = 'CANCELED', notified_status = 'CANCELED'"
                        " WHERE shipment_id = :id RETURNING *"
                    ),
                    {"id": shipment_id},
                )
                .mappings()
                .one()
            )
        return shipment_json(row, company)

    @app.get("/healthz")
    def healthz():
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "error", "db": "error"}, status_code=503)
        return {"status": "ok", "db": "ok"}

    return app
