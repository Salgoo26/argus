"""가상 PG 클라이언트 — 승인·조회·취소 (비밀 키 Basic 인증, 쇼핑몰 서버에서만)

결제창은 브라우저가 연다(작은 창). 서버는 결제창이 돌려준 paymentKey로 승인만 요청한다 —
카드번호는 이 서버를 거치지 않는다.
"""

import base64

from app.partners.http import PartnerResponse, call_json


class PgError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code
        self.message = message


class PgClient:
    def __init__(self, api_url: str, checkout_url: str, client_key: str, secret_key: str):
        self.api_url = api_url.rstrip("/")
        self.checkout_url = checkout_url
        self.client_key = client_key
        token = base64.b64encode(f"{secret_key}:".encode()).decode()
        self._headers = {"Authorization": f"Basic {token}"}

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        res: PartnerResponse = call_json(method, self.api_url + path, self._headers, payload)
        if not 200 <= res.status < 300:
            raise PgError(res.status, res.body.get("code", "PG_ERROR"), res.body.get("message", ""))
        return res.body

    def confirm(self, payment_key: str, order_id: str, amount: int) -> dict:
        return self._call(
            "POST",
            "/v1/payments/confirm",
            {"paymentKey": payment_key, "orderId": order_id, "amount": amount},
        )

    def get(self, payment_key: str) -> dict:
        return self._call("GET", f"/v1/payments/{payment_key}")

    def cancel(self, payment_key: str, reason: str) -> dict:
        """전액 취소. 이미 취소된 결제면 조회 결과를 돌려준다 (재시도해도 같은 결과)"""
        try:
            return self._call(
                "POST", f"/v1/payments/{payment_key}/cancel", {"cancelReason": reason}
            )
        except PgError as error:
            if error.code == "ALREADY_CANCELED_PAYMENT":
                return self.get(payment_key)
            raise
