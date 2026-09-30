"""접속지 정보 — 신뢰 프록시일 때만 X-Forwarded-For를 믿는다 (CLAUDE.md 3절 #10)"""

import pytest

from app.agent.client_ip import parse_trusted_proxies, resolve_client_ip

CADDY = parse_trusted_proxies("172.18.0.10, 10.0.0.0/8")


def test_without_trusted_proxy_the_peer_is_the_client():
    assert resolve_client_ip("203.0.113.10", "8.8.8.8", ()) == "203.0.113.10"


def test_header_from_untrusted_peer_is_ignored():
    # 공격자가 직접 접속해 헤더를 위조해도 자기 IP가 기록된다
    assert resolve_client_ip("198.51.100.7", "1.1.1.1", CADDY) == "198.51.100.7"


def test_trusted_proxy_passes_the_real_client():
    assert resolve_client_ip("172.18.0.10", "203.0.113.10", CADDY) == "203.0.113.10"


def test_spoofed_left_entries_are_not_trusted():
    # 클라이언트가 "8.8.8.8"을 미리 적어 보내면 Caddy가 실제 IP를 오른쪽에 덧붙인다
    # → 오른쪽부터 읽어 처음 만나는 신뢰하지 않는 주소 = 실제 클라이언트
    assert resolve_client_ip("172.18.0.10", "8.8.8.8, 203.0.113.10", CADDY) == "203.0.113.10"


def test_chained_trusted_proxies_are_skipped():
    header = "203.0.113.10, 10.1.2.3"
    assert resolve_client_ip("172.18.0.10", header, CADDY) == "203.0.113.10"


def test_malformed_hop_stops_the_walk():
    assert resolve_client_ip("172.18.0.10", "evil, garbage", CADDY) == "172.18.0.10"


def test_ipv6_is_normalized():
    assert resolve_client_ip("2001:0db8:0000:0000:0000:0000:0000:0001", None, ()) == "2001:db8::1"


def test_non_ip_peer_is_refused():
    # 접속지를 지어내지 않는다 — Agent는 이 경우 기록 대신 요청을 실패시킨다(fail-closed)
    with pytest.raises(ValueError):
        resolve_client_ip("testclient", None, ())


def test_empty_setting_trusts_nobody():
    assert parse_trusted_proxies("") == ()
    assert parse_trusted_proxies(" , ") == ()
