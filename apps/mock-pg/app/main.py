"""가상 PG 서비스 진입점 — uvicorn --factory app.main:create_app

쇼핑몰(플랫폼)과 분리된 외부 수탁자(결제대행) 역할. 실제 PG(토스페이먼츠형)의 흐름을 단순화했다.

브라우저(쇼핑몰이 띄운 작은 창)
    POST /checkout                       결제창 열기 — 주문번호·주문명·금액·구매자 정보·돌아갈 주소
    POST /checkout/{paymentKey}/approve  카드 정보 입력 → 성공 주소로 paymentKey·orderId·amount
    POST /checkout/{paymentKey}/abort    결제 취소 → 실패 주소로
    GET  /receipt/{paymentKey}           영수증
쇼핑몰 서버(Basic 인증 — 비밀 키)
    POST /v1/payments/confirm            승인 (금액·주문번호 대조)
    GET  /v1/payments/{paymentKey}       조회
    GET  /v1/payments/orders/{orderId}   주문번호로 조회
    POST /v1/payments/{paymentKey}/cancel 전액 취소

카드번호는 이 서비스만 받는다(쇼핑몰 서버를 거치지 않음). 전체 번호는 저장하지 않고
앞 6자리·뒤 4자리만 남긴 마스킹 값을 승인 응답으로 돌려준다.
"""

import base64
import hmac
import re
import secrets
from typing import Annotated
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from app.config import Settings
from app.db import create_schema
from app.pages import CARD_COMPANIES, INSTALLMENTS, checkout_page, error_page, receipt_page

ORDER_ID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
MAX_FORM_BYTES = 16 * 1024


class PgError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message


class ConfirmRequest(BaseModel):
    paymentKey: Annotated[str, Field(max_length=40)]  # noqa: N815 — 실제 PG API의 필드 이름
    orderId: Annotated[str, Field(max_length=64)]  # noqa: N815
    amount: Annotated[int, Field(gt=0)]


class CancelRequest(BaseModel):
    cancelReason: Annotated[str, Field(min_length=1, max_length=200)]  # noqa: N815


def mask_card_number(digits: str) -> str:
    """앞 6자리(BIN)·뒤 4자리만 남긴다 — 1234567890123452 → 123456******3452"""
    return digits[:6] + "*" * (len(digits) - 10) + digits[-4:]


def payment_json(row, public_url: str) -> dict:
    """승인·조회 응답 — 실제 PG 응답 형식을 단순화. 구매자 정보는 되돌려주지 않는다"""
    return {
        "paymentKey": row["payment_key"],
        "orderId": row["order_id"],
        "orderName": row["order_name"],
        "status": row["status"],
        "method": "카드",
        "totalAmount": row["amount"],
        "requestedAt": row["requested_at"].isoformat(),
        "approvedAt": row["approved_at"].isoformat() if row["approved_at"] else None,
        "card": (
            {
                "company": row["card_company"],
                "number": row["card_number_masked"],
                "installmentPlanMonths": row["installment_months"],
                "approveNo": row["approve_no"],
            }
            if row["card_company"]
            else None
        ),
        "receipt": {"url": f"{public_url}/receipt/{row['payment_key']}"}
        if row["approved_at"]
        else None,
        "cancels": (
            [
                {
                    "transactionKey": row["cancel_transaction_key"],
                    "cancelReason": row["cancel_reason"],
                    "cancelAmount": row["amount"],
                    "canceledAt": row["canceled_at"].isoformat(),
                }
            ]
            if row["canceled_at"]
            else []
        ),
    }


def _with_query(url: str, params: dict) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query) + list(params.items())
    return urlunsplit(parts._replace(query=urlencode(query)))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    secret_key = settings.require_secret_key()
    origins = settings.return_origins()
    public_url = settings.public_url.rstrip("/")
    engine = create_engine(settings.database_url(), pool_pre_ping=True)
    create_schema(engine)

    app = FastAPI(title="Mock PG", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.engine = engine

    @app.exception_handler(PgError)
    async def _pg_error(_: Request, exc: PgError) -> JSONResponse:
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {"code": "INVALID_REQUEST", "message": "malformed request"}, status_code=400
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # 스크립트 없음. 폼 전송 뒤 리다이렉트는 허용한 쇼핑몰 출처로만
        # (form-action은 리다이렉트에도 적용된다)
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
            f"frame-ancestors 'none'; form-action 'self' {' '.join(sorted(origins))}"
        )
        return response

    def allowed_url(url: str) -> bool:
        parts = urlsplit(url)
        return parts.scheme in ("http", "https") and f"{parts.scheme}://{parts.netloc}" in origins

    async def read_form(request: Request) -> dict[str, str]:
        body = await request.body()
        if len(body) > MAX_FORM_BYTES:
            raise PgError(413, "INVALID_REQUEST", "form too large")
        return {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}

    def expire_if_stale(conn, payment_key: str) -> None:
        conn.execute(
            text(
                "UPDATE payment SET status = 'EXPIRED' WHERE payment_key = :k"
                " AND status IN ('READY', 'IN_PROGRESS')"
                " AND requested_at < now() - make_interval(mins => :ttl)"
            ),
            {"k": payment_key, "ttl": settings.payment_ttl_minutes},
        )

    select_payment = text("SELECT * FROM payment WHERE payment_key = :k")
    select_payment_for_update = text("SELECT * FROM payment WHERE payment_key = :k FOR UPDATE")

    def load(conn, payment_key: str, *, lock: bool = False):
        expire_if_stale(conn, payment_key)
        query = select_payment_for_update if lock else select_payment
        return conn.execute(query, {"k": payment_key}).mappings().first()

    expected_auth = ("Basic " + base64.b64encode(f"{secret_key}:".encode()).decode()).encode()

    @app.middleware("http")
    async def require_secret(request: Request, call_next):
        # 승인·조회·취소 API는 비밀 키로만. 본문 검사보다 먼저 확인한다
        if request.url.path.startswith("/v1/"):
            header = request.headers.get("authorization", "").encode()
            # 상수 시간 비교 — 키를 한 글자씩 맞혀 보는 시간 차 공격 방지
            if not hmac.compare_digest(header, expected_auth):
                return JSONResponse(
                    {"code": "UNAUTHORIZED_KEY", "message": "invalid secret key"}, status_code=401
                )
        return await call_next(request)

    # ── 결제창 (브라우저) ─────────────────────────────────────

    @app.post("/checkout", response_class=HTMLResponse)
    async def open_checkout(request: Request):
        form = await read_form(request)
        if not hmac.compare_digest(
            form.get("clientKey", "").encode(), settings.client_key.encode()
        ):
            return HTMLResponse(error_page("등록되지 않은 가맹점입니다."), status_code=400)
        success_url, fail_url = form.get("successUrl", ""), form.get("failUrl", "")
        if not (allowed_url(success_url) and allowed_url(fail_url)):
            return HTMLResponse(error_page("허용되지 않은 돌아갈 주소입니다."), status_code=400)
        order_id, order_name = form.get("orderId", ""), form.get("orderName", "").strip()
        amount = form.get("amount", "")
        if not ORDER_ID.match(order_id) or not order_name or not amount.isdigit():
            return HTMLResponse(error_page("결제 요청 형식이 올바르지 않습니다."), status_code=400)
        customer = {
            "customer_name": form.get("customerName", "")[:50] or None,
            "customer_email": form.get("customerEmail", "")[:255] or None,
            "customer_mobile_phone": form.get("customerMobilePhone", "")[:20] or None,
        }
        with engine.begin() as conn:
            row = (
                conn.execute(
                    text(
                        "INSERT INTO payment (payment_key, order_id, order_name, amount, status,"
                        " customer_name, customer_email, customer_mobile_phone,"
                        " success_url, fail_url)"
                        " VALUES (:k, :o, :n, :a, 'READY', :customer_name, :customer_email,"
                        " :customer_mobile_phone, :s, :f) RETURNING *"
                    ),
                    {
                        "k": "pay_" + secrets.token_hex(16),
                        "o": order_id,
                        "n": order_name[:100],
                        "a": int(amount),
                        "s": success_url,
                        "f": fail_url,
                        **customer,
                    },
                )
                .mappings()
                .one()
            )
        return HTMLResponse(checkout_page(row))

    @app.post("/checkout/{payment_key}/approve")
    async def approve_checkout(payment_key: str, request: Request):
        form = await read_form(request)
        with engine.begin() as conn:
            row = load(conn, payment_key, lock=True)
            if row is None or row["status"] != "READY":
                return HTMLResponse(
                    error_page("결제 시간이 지났거나 이미 처리된 결제입니다."), status_code=400
                )
            # 카드번호는 변수로만 거쳐 간다 — 로그·DB 어디에도 전체 번호를 남기지 않는다
            digits = re.sub(r"\D", "", form.get("card_number", ""))
            company = form.get("card_company", "")
            months = form.get("installment", "0")
            if not 12 <= len(digits) <= 19:
                return HTMLResponse(
                    checkout_page(row, "카드번호를 확인해 주세요 (숫자 12~19자리)."),
                    status_code=400,
                )
            if company not in CARD_COMPANIES or not months.isdigit():
                return HTMLResponse(checkout_page(row, "입력값을 확인해 주세요."), status_code=400)
            if int(months) not in INSTALLMENTS:
                return HTMLResponse(checkout_page(row, "할부 개월을 확인해 주세요."), 400)
            conn.execute(
                text(
                    "UPDATE payment SET status = 'IN_PROGRESS', authenticated_at = now(),"
                    " card_company = :c, card_number_masked = :m, installment_months = :i"
                    " WHERE payment_key = :k"
                ),
                {"c": company, "m": mask_card_number(digits), "i": int(months), "k": payment_key},
            )
        target = _with_query(
            row["success_url"],
            {"paymentKey": payment_key, "orderId": row["order_id"], "amount": row["amount"]},
        )
        return RedirectResponse(target, status_code=303)

    @app.post("/checkout/{payment_key}/abort")
    def abort_checkout(payment_key: str):
        with engine.begin() as conn:
            row = load(conn, payment_key, lock=True)
            if row is None:
                return HTMLResponse(error_page("결제 정보를 찾을 수 없습니다."), status_code=404)
            if row["status"] == "READY":
                conn.execute(
                    text("UPDATE payment SET status = 'ABORTED' WHERE payment_key = :k"),
                    {"k": payment_key},
                )
        target = _with_query(
            row["fail_url"],
            {
                "code": "PAY_PROCESS_CANCELED",
                "message": "사용자가 결제를 취소했습니다",
                "orderId": row["order_id"],
            },
        )
        return RedirectResponse(target, status_code=303)

    @app.get("/receipt/{payment_key}", response_class=HTMLResponse)
    def receipt(payment_key: str):
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT * FROM payment WHERE payment_key = :k AND approved_at IS NOT NULL"
                    ),
                    {"k": payment_key},
                )
                .mappings()
                .first()
            )
        if row is None:
            return HTMLResponse(error_page("영수증을 찾을 수 없습니다."), status_code=404)
        return HTMLResponse(receipt_page(row))

    # ── 승인·조회·취소 API (쇼핑몰 서버) ─────────────────────

    @app.post("/v1/payments/confirm")
    def confirm(body: ConfirmRequest):
        with engine.begin() as conn:
            row = load(conn, body.paymentKey, lock=True)
            if row is None:
                raise PgError(404, "NOT_FOUND_PAYMENT", "payment not found")
            if row["order_id"] != body.orderId or row["amount"] != body.amount:
                # 결제창에서 돌아온 값이 바뀌었으면 승인하지 않는다
                raise PgError(400, "INVALID_REQUEST", "orderId or amount mismatch")
            if row["status"] == "DONE":
                return payment_json(row, public_url)  # 같은 승인 재요청 — 결과를 그대로
            if row["status"] == "EXPIRED":
                raise PgError(400, "EXPIRED_PAYMENT", "payment expired")
            if row["status"] != "IN_PROGRESS":
                raise PgError(400, "NOT_AUTHENTICATED_PAYMENT", f"status is {row['status']}")
            row = (
                conn.execute(
                    text(
                        "UPDATE payment SET status = 'DONE', approved_at = now(), approve_no = :n"
                        " WHERE payment_key = :k RETURNING *"
                    ),
                    {"n": f"{secrets.randbelow(10**8):08d}", "k": body.paymentKey},
                )
                .mappings()
                .one()
            )
        return payment_json(row, public_url)

    @app.get("/v1/payments/orders/{order_id}")
    def get_by_order(order_id: str):
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT * FROM payment WHERE order_id = :o"
                        " ORDER BY (status IN ('DONE', 'CANCELED')) DESC, requested_at DESC LIMIT 1"
                    ),
                    {"o": order_id},
                )
                .mappings()
                .first()
            )
        if row is None:
            raise PgError(404, "NOT_FOUND_PAYMENT", "payment not found")
        return payment_json(row, public_url)

    @app.get("/v1/payments/{payment_key}")
    def get_payment(payment_key: str):
        with engine.begin() as conn:
            row = load(conn, payment_key)
        if row is None:
            raise PgError(404, "NOT_FOUND_PAYMENT", "payment not found")
        return payment_json(row, public_url)

    @app.post("/v1/payments/{payment_key}/cancel")
    def cancel(payment_key: str, body: CancelRequest):
        """전액 취소만 (부분 취소는 쓰지 않는다 — 주문 1건 = 상품 1개)"""
        with engine.begin() as conn:
            row = load(conn, payment_key, lock=True)
            if row is None:
                raise PgError(404, "NOT_FOUND_PAYMENT", "payment not found")
            if row["status"] == "CANCELED":
                raise PgError(409, "ALREADY_CANCELED_PAYMENT", "already canceled")
            if row["status"] != "DONE":
                raise PgError(400, "NOT_CANCELABLE_PAYMENT", f"status is {row['status']}")
            row = (
                conn.execute(
                    text(
                        "UPDATE payment SET status = 'CANCELED', canceled_at = now(),"
                        " cancel_reason = :r, cancel_transaction_key = :t"
                        " WHERE payment_key = :k RETURNING *"
                    ),
                    {"r": body.cancelReason, "t": "ctx_" + secrets.token_hex(12), "k": payment_key},
                )
                .mappings()
                .one()
            )
        return payment_json(row, public_url)

    @app.get("/healthz")
    def healthz():
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "error", "db": "error"}, status_code=503)
        return {"status": "ok", "db": "ok"}

    return app
