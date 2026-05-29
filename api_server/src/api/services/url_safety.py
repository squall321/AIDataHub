"""URL 안전성 검증 — SSRF 방지.

sync_sources.base_url 처럼 외부 호출 시 사용되는 URL 이
private/loopback/metadata endpoint 로 향하지 않게 차단.

차단 대상:
    - scheme: http/https 외 (file://, gopher:// 등)
    - hostname: localhost / 0.0.0.0
    - IP: 127.0.0.0/8, 10.0.0.0/8, 169.254.0.0/16 (AWS metadata),
          172.16.0.0/12, 192.168.0.0/16, ::1, fe80::/10
    - 메타데이터 호스트: metadata.google.internal, metadata.azure.com 등

환경변수 옵션:
    AIDH_SYNC_ALLOW_INTERNAL=true  — 사내 네트워크 호출 허용 (개발/테스트)
        이 경우 private RFC1918 / loopback 만 허용 (메타데이터 endpoint 는 계속 차단)
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse


# 메타데이터 호스트 (allow_internal 켜도 차단)
_METADATA_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata.aws",
    "metadata.azure.com",
    "metadata.oraclecloud.com",
})

# 메타데이터 IP (link-local)
_METADATA_IPS = frozenset({
    ipaddress.ip_address("169.254.169.254"),  # AWS / Azure
    ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud
    ipaddress.ip_address("fd00:ec2::254"),     # AWS IPv6
})


def _is_metadata(host_or_ip: str) -> bool:
    if host_or_ip.lower() in _METADATA_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host_or_ip)
        return ip in _METADATA_IPS
    except ValueError:
        return False


def _is_private(ip: ipaddress._BaseAddress) -> bool:
    """RFC1918 / loopback / link-local / multicast / unspecified."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_external_url(url: str) -> tuple[bool, str]:
    """외부 호출 안전성 검증.

    Returns:
        (ok, reason). ok=False 면 reason 에 차단 사유.
    """
    if not isinstance(url, str) or not url.strip():
        return False, "empty url"

    try:
        parsed = urlparse(url)
    except Exception as exc:
        return False, f"url parse failed: {exc}"

    # 1. scheme
    if parsed.scheme not in ("http", "https"):
        return False, f"scheme must be http/https, got {parsed.scheme!r}"

    host = (parsed.hostname or "").lower()
    if not host:
        return False, "missing hostname"

    # 2. 메타데이터 호스트 — 절대 허용 안 함
    if _is_metadata(host):
        return False, f"metadata endpoint blocked: {host}"

    allow_internal = os.environ.get("AIDH_SYNC_ALLOW_INTERNAL", "").lower() in (
        "true", "1", "yes",
    )

    # 3. hostname 이 IP 인지 검사 + 호스트명 → IP resolve
    ips_to_check: list[ipaddress._BaseAddress] = []
    try:
        ip = ipaddress.ip_address(host)
        ips_to_check.append(ip)
    except ValueError:
        # 호스트명 — DNS resolve 시도 (best-effort, 실패해도 허용)
        # localhost 같은 명시적 차단 호스트 우선
        if host in ("localhost", "localhost.localdomain"):
            if allow_internal:
                return True, "ok (internal allowed)"
            return False, "localhost blocked (set AIDH_SYNC_ALLOW_INTERNAL=true)"
        try:
            infos = socket.getaddrinfo(host, None)
            for info in infos:
                addr = info[4][0]
                try:
                    ips_to_check.append(ipaddress.ip_address(addr))
                except ValueError:
                    continue
        except socket.gaierror:
            # DNS 실패 — 호스트명만으로는 검증 불가, 보수적으로 통과
            # (실제 sync 시 fetch 단계에서 실패할 것)
            return True, "ok (dns unresolved)"

    # 4. resolve 된 모든 IP 가 메타데이터/private 검사 통과해야 함
    for ip in ips_to_check:
        if ip in _METADATA_IPS:
            return False, f"resolves to metadata IP: {ip}"
        if _is_private(ip) and not allow_internal:
            return False, (
                f"resolves to private/loopback IP: {ip} "
                "(set AIDH_SYNC_ALLOW_INTERNAL=true if intended)"
            )

    return True, "ok"


__all__ = ["validate_external_url"]
