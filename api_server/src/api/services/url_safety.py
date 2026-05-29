"""URL 안전성 검증 — SSRF 방지.

sync_sources.base_url 처럼 외부 호출 시 사용되는 URL 이
private/loopback/metadata endpoint 로 향하지 않게 차단.

차단 대상:
    - scheme: http/https 외 (file://, gopher:// 등)
    - port: 80/443 외 (allow_internal=False 시) — 22/2375/6379 등 차단
    - hostname: localhost / 0.0.0.0
    - IP: 127.0.0.0/8, 10.0.0.0/8, 169.254.0.0/16 (AWS metadata),
          172.16.0.0/12, 192.168.0.0/16, ::1, fe80::/10, fd00::/8
    - 메타데이터 호스트: metadata.google.internal, AWS/Azure/Oracle/GCP/IBM/
      Tencent/Scaleway/Equinix/DigitalOcean
    - IPv4-mapped IPv6 (::ffff:a.b.c.d) — IPv4 로 정규화 후 동일 검사
    - DNS 실패 — fail-CLOSED (이전 audit 발견)

환경변수 옵션:
    AIDH_SYNC_ALLOW_INTERNAL=true   — 사내 네트워크 호출 허용 (개발/테스트)
        이 경우 RFC1918 / loopback / 비표준 포트 허용. 메타데이터 endpoint 는
        계속 차단.
    AIDH_SYNC_ALLOW_DNS_UNRESOLVED=true — DNS 실패 시 통과 (정말 필요할 때만).
        기본 False — fail-closed.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse


# 메타데이터 호스트 (allow_internal 켜도 차단). 대부분 169.254.169.254 로
# resolve 되지만 IP 검사가 우회되는 경로 (DNS 실패 fallthrough 등) 의 안전망.
_METADATA_HOSTS = frozenset({
    # AWS / Azure / GCP / Oracle
    "metadata.google.internal",
    "metadata.aws",
    "metadata.azure.com",
    "metadata.oraclecloud.com",
    # IBM / Tencent / Scaleway / Equinix / DigitalOcean / Exoscale
    "api.metadata.cloud.ibm.com",
    "metadata.tencentyun.com",
    "metadata.scaleway.com",
    "metadata.platformequinix.com",
    "metadata.digitalocean.com",
    "metadata.exoscale.com",
})

# 메타데이터 IP — IPv4 그리고 IPv4-mapped IPv6 양쪽 모두 정규화 후 비교.
_METADATA_IPS = frozenset({
    ipaddress.ip_address("169.254.169.254"),  # AWS / Azure
    ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud
    ipaddress.ip_address("fd00:ec2::254"),    # AWS IPv6
})

# 운영시 허용 포트 — allow_internal=False 일 때만 강제.
_ALLOWED_PORTS = frozenset({80, 443})


def _normalize_ip(ip: ipaddress._BaseAddress) -> ipaddress._BaseAddress:
    """IPv4-mapped IPv6 (::ffff:a.b.c.d) → IPv4. 그 외는 그대로."""
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped if mapped is not None else ip


def _is_metadata(host_or_ip: str) -> bool:
    if host_or_ip.lower() in _METADATA_HOSTS:
        return True
    try:
        ip = _normalize_ip(ipaddress.ip_address(host_or_ip))
        return ip in _METADATA_IPS
    except ValueError:
        return False


def _is_private(ip: ipaddress._BaseAddress) -> bool:
    """RFC1918 / loopback / link-local / multicast / unspecified / unique-local."""
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

    allow_internal = os.environ.get("AIDH_SYNC_ALLOW_INTERNAL", "").lower() in (
        "true", "1", "yes",
    )
    allow_dns_unresolved = os.environ.get(
        "AIDH_SYNC_ALLOW_DNS_UNRESOLVED", ""
    ).lower() in ("true", "1", "yes")

    # 2. port allowlist — allow_internal=False 일 때만 강제
    port = parsed.port
    if port is not None and not allow_internal and port not in _ALLOWED_PORTS:
        return False, (
            f"non-standard port blocked: {port} "
            f"(allowed: {sorted(_ALLOWED_PORTS)} — set AIDH_SYNC_ALLOW_INTERNAL=true if intended)"
        )

    # 3. 메타데이터 호스트 — 절대 허용 안 함
    if _is_metadata(host):
        return False, f"metadata endpoint blocked: {host}"

    # 4. hostname 이 IP 인지 검사 + 호스트명 → IP resolve
    ips_to_check: list[ipaddress._BaseAddress] = []
    try:
        ip = _normalize_ip(ipaddress.ip_address(host))
        ips_to_check.append(ip)
    except ValueError:
        # 호스트명 — DNS resolve 시도
        if host in ("localhost", "localhost.localdomain"):
            if allow_internal:
                return True, "ok (internal allowed)"
            return False, "localhost blocked (set AIDH_SYNC_ALLOW_INTERNAL=true)"
        try:
            infos = socket.getaddrinfo(host, None)
            for info in infos:
                addr = info[4][0]
                try:
                    ips_to_check.append(_normalize_ip(ipaddress.ip_address(addr)))
                except ValueError:
                    continue
        except socket.gaierror as exc:
            # ⚠ 이전 audit 발견: fail-OPEN 위험. 기본은 fail-CLOSED 로 전환.
            # AIDH_SYNC_ALLOW_DNS_UNRESOLVED=true 명시 시에만 통과 (운영 환경
            # 일시 DNS 장애 등에서 강제 우회 필요할 때).
            if allow_dns_unresolved:
                return True, "ok (dns unresolved — opt-in fail-open)"
            return False, (
                f"dns resolution failed for {host}: {exc} "
                "(set AIDH_SYNC_ALLOW_DNS_UNRESOLVED=true to override)"
            )

    # 5. resolve 된 모든 IP 가 메타데이터/private 검사 통과해야 함
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
