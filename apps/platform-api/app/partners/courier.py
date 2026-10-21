"""가상 택배 클라이언트 — 배송 접수·조회·접수 취소·반품 회수 접수 (Bearer API 키)"""

from app.partners.http import PartnerResponse, call_json


class CourierError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code
        self.message = message


class CourierClient:
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        res: PartnerResponse = call_json(method, self.api_url + path, self._headers, payload)
        if not 200 <= res.status < 300:
            raise CourierError(
                res.status, res.body.get("code", "COURIER_ERROR"), res.body.get("message", "")
            )
        return res.body

    def create_shipment(self, payload: dict) -> dict:
        return self._call("POST", "/v1/shipments", payload)

    def create_return(self, payload: dict) -> dict:
        return self._call("POST", "/v1/returns", payload)

    def get(self, shipment_id: str) -> dict:
        return self._call("GET", f"/v1/shipments/{shipment_id}")

    def cancel(self, shipment_id: str) -> dict:
        return self._call("POST", f"/v1/shipments/{shipment_id}/cancel")
