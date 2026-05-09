#!/usr/bin/env bash
# =============================================================================
# AI Data Hub — Linux pgvector 단독 설치
# =============================================================================
#
# PostgreSQL 이 이미 설치된 환경에서 pgvector 만 추가 설치.
#
# 사용법:
#   sudo bash install_pgvector_linux.sh                  # 자동 감지
#   sudo bash install_pgvector_linux.sh --pg-version 16  # PG 16 강제
#
# 동작:
#   1) PG 버전 자동 감지 (psql --version)
#   2) distro 패키지 설치 시도 (apt/dnf)
#   3) 패키지 없으면 source build (build-essential + postgresql-server-dev)
#   4) ai_data DB 에 CREATE EXTENSION vector
# =============================================================================
set -euo pipefail

PG_VERSION=""
DB_NAME="ai_data"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pg-version) PG_VERSION="$2"; shift 2 ;;
        --db-name) DB_NAME="$2"; shift 2 ;;
        -h|--help) grep "^# " "$0" | head -20; exit 0 ;;
        *) echo "알 수 없는 인자: $1" >&2; exit 1 ;;
    esac
done

[[ $EUID -ne 0 ]] && { echo "[ERROR] root 권한 필요 — sudo bash $0" >&2; exit 1; }

ok()    { echo -e "\e[32m[OK]\e[0m   $1"; }
info()  { echo -e "\e[36m[INFO]\e[0m $1"; }
warn()  { echo -e "\e[33m[WARN]\e[0m $1"; }
err()   { echo -e "\e[31m[ERROR]\e[0m $1" >&2; }

# -----------------------------------------------------------------------------
# 1) PG 버전 자동 감지
# -----------------------------------------------------------------------------
if [[ -z "$PG_VERSION" ]]; then
    if command -v psql >/dev/null; then
        PG_VERSION=$(psql --version | grep -oE '[0-9]+' | head -1)
    fi
    if [[ -z "$PG_VERSION" ]]; then
        err "PG 버전 자동 감지 실패 — --pg-version <16|17|18> 명시"
        exit 1
    fi
fi
ok "PostgreSQL 버전: $PG_VERSION"

# -----------------------------------------------------------------------------
# 2) distro 감지
# -----------------------------------------------------------------------------
source /etc/os-release
case "${ID,,}" in
    ubuntu|debian) FAMILY="debian"; PKG_MGR="apt" ;;
    rhel|rocky|almalinux|centos|fedora) FAMILY="rhel"; PKG_MGR="dnf"; command -v dnf >/dev/null || PKG_MGR="yum" ;;
    *) err "지원되지 않는 distro"; exit 1 ;;
esac
ok "distro: $ID $VERSION_ID (family=$FAMILY)"

# -----------------------------------------------------------------------------
# 3) 패키지 설치 시도 → 실패 시 source build
# -----------------------------------------------------------------------------
install_from_pkg() {
    case "$FAMILY" in
        debian)
            apt-get update -qq
            apt-get install -y "postgresql-$PG_VERSION-pgvector"
            ;;
        rhel)
            $PKG_MGR install -y "pgvector_${PG_VERSION}"
            ;;
    esac
}

install_from_source() {
    info "source build (build-essential + git + postgresql-server-dev 필요)..."
    case "$FAMILY" in
        debian)
            apt-get install -y -qq build-essential git "postgresql-server-dev-$PG_VERSION" >/dev/null
            ;;
        rhel)
            $PKG_MGR install -y gcc make git "postgresql${PG_VERSION}-devel" >/dev/null
            ;;
    esac

    local tmpdir
    tmpdir=$(mktemp -d)
    git clone --depth 1 https://github.com/pgvector/pgvector.git "$tmpdir/pgvector" >/dev/null 2>&1
    pushd "$tmpdir/pgvector" >/dev/null

    local pgconfig
    if command -v pg_config >/dev/null; then
        pgconfig=$(command -v pg_config)
    else
        for cand in "/usr/pgsql-${PG_VERSION}/bin/pg_config" "/usr/lib/postgresql/${PG_VERSION}/bin/pg_config"; do
            [[ -x "$cand" ]] && pgconfig="$cand" && break
        done
    fi
    [[ -z "${pgconfig:-}" ]] && { err "pg_config 못 찾음"; exit 1; }

    PG_CONFIG="$pgconfig" make >/dev/null
    PG_CONFIG="$pgconfig" make install >/dev/null

    popd >/dev/null
    rm -rf "$tmpdir"
}

if install_from_pkg 2>/dev/null; then
    ok "pgvector 패키지 설치"
else
    warn "패키지 설치 실패 — source build 진행"
    install_from_source
    ok "source build 완료"
fi

# -----------------------------------------------------------------------------
# 4) PG 서비스 reload + CREATE EXTENSION
# -----------------------------------------------------------------------------
SERVICE_NAME=""
for cand in "postgresql@${PG_VERSION}-main" "postgresql-${PG_VERSION}" "postgresql"; do
    if systemctl list-unit-files "${cand}.service" >/dev/null 2>&1; then
        SERVICE_NAME="$cand"
        break
    fi
done
if [[ -n "$SERVICE_NAME" ]]; then
    systemctl restart "$SERVICE_NAME"
    ok "서비스 재시작: $SERVICE_NAME"
fi

if sudo -u postgres psql -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null 2>&1; then
    ok "CREATE EXTENSION vector 완료 (DB: $DB_NAME)"
else
    err "CREATE EXTENSION 실패 — DB '$DB_NAME' 존재 여부 확인"
    exit 1
fi

echo ""
echo "============================================================"
echo " pgvector 설치 완료"
echo "============================================================"
echo " 시맨틱 검색 사용 가능 — EMBEDDING_PROVIDER=e5_small 권장"
echo "============================================================"
