"""접속지 정보(§2 3호) — Argus 사용자(담당자·취급자)의 실제 IP를 정한다 (CLAUDE.md 3절 #10)

X-Forwarded-For는 클라이언트가 마음대로 써 보낼 수 있다. 그대로 믿으면 접속지를 위조할 수 있으므로
**설정에 등록한 신뢰 프록시(운영의 Caddy·argus-web)에서 온 요청일 때만** 이 헤더를 본다.
(Tomcat RemoteIpValve의 internalProxies / Spring의 ForwardedHeaderFilter + 신뢰 설정과 같은 역할)

헤더는 "원래 클라이언트, 프록시1, 프록시2 …" 순으로 쌓이므로, 오른쪽(가까운 쪽)부터 읽으며
신뢰 프록시를 건너뛰고 처음 만나는 신뢰하지 않는 주소를 클라이언트로 본다.
"""

import ipaddress
from collections.abc import Iterable

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def parse_trusted_proxies(value: str) -> tuple[IPNetwork, ...]:
    """쉼표로 구분한 IP·CIDR 목록 (예: 10.0.0.5, 172.16.0.0/12). 비우면 아무도 믿지 않는다."""
    return tuple(
        ipaddress.ip_network(p.strip(), strict=False) for p in value.split(",") if p.strip()
    )


def _parse(value: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _trusted(ip: IPAddress, proxies: Iterable[IPNetwork]) -> bool:
    return any(ip in network for network in proxies)


def resolve_client_ip(peer: str, forwarded_for: str | None, proxies: tuple[IPNetwork, ...]) -> str:
    """peer = TCP로 직접 연결된 상대. 형식이 IP가 아니면 ValueError (접속지를 지어내지 않는다)"""
    peer_ip = _parse(peer)
    if peer_ip is None:
        raise ValueError("peer address is not an IP")

    client = peer_ip
    if forwarded_for and _trusted(peer_ip, proxies):
        for hop in reversed(forwarded_for.split(",")):
            hop_ip = _parse(hop)
            if hop_ip is None:
                break  # 형식이 깨진 값 — 그 너머는 믿지 않고 지금까지 확인한 주소를 쓴다
            client = hop_ip
            if not _trusted(hop_ip, proxies):
                break
    return client.compressed
