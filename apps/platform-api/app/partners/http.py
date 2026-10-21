"""외부 서비스 JSON 호출 — urllib (relay와 같은 방식, 의존성 추가 없음)"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

HTTP_TIMEOUT_SEC = 5


class PartnerUnavailable(Exception):
    """연결 실패·타임아웃 — 응답 자체가 없음"""


@dataclass(frozen=True)
class PartnerResponse:
    status: int
    body: dict


def call_json(
    method: str, url: str, headers: dict[str, str], payload: dict | None = None
) -> PartnerResponse:
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(  # noqa: S310 — 스킴은 설정 단계에서 http(s)로 제한
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SEC) as response:  # noqa: S310
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise PartnerUnavailable(str(error)) from error
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        body = {}
    return PartnerResponse(status, body if isinstance(body, dict) else {})
