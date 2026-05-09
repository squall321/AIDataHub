#!/usr/bin/env bash
# =============================================================================
# AI Data Hub — Linux PostgreSQL + pgvector 자동 설치 (Docker 없는 환경)
# =============================================================================
#
# 동작:
#   1) distro 자동 감지 (Ubuntu/Debian/RHEL/Fedora/Rocky/Alma)
#   2) PGDG 공식 저장소 등록
#   3) PostgreSQL (default 18, --pg-version 으로 변경) + pgvector 패키지 설치
#   4) 서비스 시작 및 enable
#   5) ai_data DB 생성 + pgvector 확장 활성화
#
# 사용법:
#   sudo bash install_postgres_linux.sh                  # PG 18 + pgvector
#   sudo bash install_postgres_linux.sh --pg-version 16  # PG 16
#   sudo bash install_postgres_linux.sh --skip-pgvector  # PG 만, pgvector skip
#
# 검증된 distro:
#   - Ubuntu 22.04 / 24.04
#   - Debian 12 (bookworm)
#   - RHEL 9 / Rocky 9 / AlmaLinux 9
#   - Fedora 39 / 40
#
# 사전 요구:
#   - root 권한 (sudo)
#   - 인터넷 연결 (PGDG 저장소 + 패키지 다운로드)
# =============================================================================
set -euo pipefail

# -----------------------------------------------------------------------------
# 인자 파싱
# -----------------------------------------------------------------------------
PG_VERSION="18"
SKIP_PGVECTOR=0
SUPER_PASSWORD="postgres"
DB_NAME="ai_data"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pg-version) PG_VERSION="$2"; shift 2 ;;
        --skip-pgvector) SKIP_PGVECTOR=1; shift ;;
        --password) SUPER_PASSWORD="$2"; shift 2 ;;
        --db-name) DB_NAME="$2"; shift 2 ;;
        -h|--help)
            grep "^# " "$0" | head -30
            exit 0
            ;;
        *) echo "알 수 없는 인자: $1" >&2; exit 1 ;;
    esac
done

# -----------------------------------------------------------------------------
# root 권한 검증
# -----------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    echo "[ERROR] root 권한 필요 — sudo bash $0" >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# 색상 출력 헬퍼
# -----------------------------------------------------------------------------
ok()    { echo -e "\e[32m[OK]\e[0m   $1"; }
info()  { echo -e "\e[36m[INFO]\e[0m $1"; }
warn()  { echo -e "\e[33m[WARN]\e[0m $1"; }
err()   { echo -e "\e[31m[ERROR]\e[0m $1" >&2; }

echo "============================================================"
echo " AI Data Hub — Linux PostgreSQL $PG_VERSION + pgvector 자동 설치"
echo "============================================================"

# -----------------------------------------------------------------------------
# 1) distro 자동 감지
# -----------------------------------------------------------------------------
if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    DISTRO_ID="${ID,,}"
    DISTRO_VER="${VERSION_ID:-unknown}"
else
    err "distro 감지 불가 — /etc/os-release 가 없음"
    exit 1
fi

case "$DISTRO_ID" in
    ubuntu|debian)
        FAMILY="debian"
        PKG_MGR="apt"
        ;;
    rhel|rocky|almalinux|centos)
        FAMILY="rhel"
        PKG_MGR="dnf"
        command -v dnf >/dev/null || PKG_MGR="yum"
        ;;
    fedora)
        FAMILY="fedora"
        PKG_MGR="dnf"
        ;;
    *)
        err "지원되지 않는 distro: $DISTRO_ID ($DISTRO_VER)"
        err "수동 설치: https://www.postgresql.org/download/"
        exit 1
        ;;
esac
ok "distro 감지: $DISTRO_ID $DISTRO_VER (family=$FAMILY)"

# -----------------------------------------------------------------------------
# 2) PGDG 저장소 등록 + PG 설치
# -----------------------------------------------------------------------------
install_postgres_debian() {
    info "PGDG 저장소 등록 (Debian/Ubuntu)..."
    apt-get update -qq
    apt-get install -y curl ca-certificates gnupg lsb-release >/dev/null

    # PGDG 키 등록
    install -d /usr/share/postgresql-common/pgdg
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc

    local codename
    codename=$(lsb_release -cs)
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt $codename-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list
    apt-get update -qq
    ok "PGDG 저장소 등록"

    info "PostgreSQL $PG_VERSION 설치..."
    apt-get install -y "postgresql-$PG_VERSION" "postgresql-client-$PG_VERSION" >/dev/null
    ok "PostgreSQL $PG_VERSION 설치"

    if [[ $SKIP_PGVECTOR -eq 0 ]]; then
        info "pgvector 설치..."
        if apt-get install -y "postgresql-$PG_VERSION-pgvector" >/dev/null 2>&1; then
            ok "pgvector 패키지 설치 (postgresql-$PG_VERSION-pgvector)"
        else
            warn "pgvector 패키지 미제공 — source build 시도"
            install_pgvector_from_source
        fi
    fi
}

install_postgres_rhel() {
    info "PGDG 저장소 등록 (RHEL/Rocky/Alma/Fedora)..."
    local arch
    arch=$(uname -m)

    if [[ "$FAMILY" == "fedora" ]]; then
        # Fedora 는 PGDG fedora repo
        $PKG_MGR install -y "https://download.postgresql.org/pub/repos/yum/reporpms/F-${DISTRO_VER}-${arch}/pgdg-fedora-repo-latest.noarch.rpm" >/dev/null 2>&1 || \
            warn "Fedora $DISTRO_VER PGDG repo 설치 실패 (이미 등록되어 있으면 무시)"
    else
        local rhel_ver="${DISTRO_VER%%.*}"
        $PKG_MGR install -y "https://download.postgresql.org/pub/repos/yum/reporpms/EL-${rhel_ver}-${arch}/pgdg-redhat-repo-latest.noarch.rpm" >/dev/null 2>&1 || \
            warn "RHEL $rhel_ver PGDG repo 설치 실패 (이미 등록되어 있으면 무시)"
    fi

    # 기본 PostgreSQL 모듈 비활성화 (RHEL 9 + dnf module)
    if command -v dnf >/dev/null; then
        dnf -qy module disable postgresql >/dev/null 2>&1 || true
    fi
    ok "PGDG 저장소 등록"

    info "PostgreSQL $PG_VERSION 설치..."
    $PKG_MGR install -y "postgresql${PG_VERSION}-server" "postgresql${PG_VERSION}" "postgresql${PG_VERSION}-contrib" >/dev/null
    ok "PostgreSQL $PG_VERSION 설치"

    # initdb (RHEL 계열은 수동 초기화 필요)
    local pgsetup="/usr/pgsql-${PG_VERSION}/bin/postgresql-${PG_VERSION}-setup"
    if [[ -x "$pgsetup" ]]; then
        if [[ ! -d "/var/lib/pgsql/${PG_VERSION}/data/base" ]]; then
            "$pgsetup" initdb >/dev/null
            ok "initdb 완료"
        else
            ok "initdb 이미 완료됨 (skip)"
        fi
    fi

    if [[ $SKIP_PGVECTOR -eq 0 ]]; then
        info "pgvector 설치..."
        if $PKG_MGR install -y "pgvector_${PG_VERSION}" >/dev/null 2>&1; then
            ok "pgvector 패키지 설치"
        else
            warn "pgvector 패키지 미제공 — source build 시도"
            install_pgvector_from_source
        fi
    fi
}

install_pgvector_from_source() {
    info "pgvector source build (build-essential + git 필요)..."
    case "$FAMILY" in
        debian)
            apt-get install -y build-essential git "postgresql-server-dev-$PG_VERSION" >/dev/null
            ;;
        rhel|fedora)
            $PKG_MGR install -y gcc make git "postgresql${PG_VERSION}-devel" >/dev/null
            ;;
    esac

    local tmpdir
    tmpdir=$(mktemp -d)
    git clone --depth 1 https://github.com/pgvector/pgvector.git "$tmpdir/pgvector" >/dev/null 2>&1
    pushd "$tmpdir/pgvector" >/dev/null

    # PG_CONFIG 자동 탐지
    local pgconfig
    if command -v pg_config >/dev/null; then
        pgconfig=$(command -v pg_config)
    else
        for cand in "/usr/pgsql-${PG_VERSION}/bin/pg_config" "/usr/lib/postgresql/${PG_VERSION}/bin/pg_config"; do
            [[ -x "$cand" ]] && pgconfig="$cand" && break
        done
    fi
    [[ -z "$pgconfig" ]] && { err "pg_config 못 찾음"; exit 1; }

    PG_CONFIG="$pgconfig" make >/dev/null
    PG_CONFIG="$pgconfig" make install >/dev/null

    popd >/dev/null
    rm -rf "$tmpdir"
    ok "pgvector source build 완료"
}

case "$FAMILY" in
    debian) install_postgres_debian ;;
    rhel|fedora) install_postgres_rhel ;;
esac

# -----------------------------------------------------------------------------
# 3) 서비스 시작 + enable
# -----------------------------------------------------------------------------
info "PostgreSQL 서비스 시작..."
SERVICE_NAME=""
for cand in "postgresql@${PG_VERSION}-main" "postgresql-${PG_VERSION}" "postgresql"; do
    if systemctl list-unit-files "${cand}.service" >/dev/null 2>&1; then
        SERVICE_NAME="$cand"
        break
    fi
done
if [[ -z "$SERVICE_NAME" ]]; then
    warn "PostgreSQL systemd 서비스 자동 감지 실패 — 수동 시작 필요"
else
    systemctl enable --now "$SERVICE_NAME" >/dev/null
    ok "서비스 활성화: $SERVICE_NAME"
fi

# -----------------------------------------------------------------------------
# 4) postgres 비밀번호 + ai_data DB + pgvector 확장
# -----------------------------------------------------------------------------
info "postgres 비밀번호 설정 + DB '$DB_NAME' 생성..."
sudo -u postgres psql -tAc "ALTER USER postgres WITH PASSWORD '$SUPER_PASSWORD';" >/dev/null
ok "postgres 비밀번호 설정"

if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
    ok "DB '$DB_NAME' 이미 존재"
else
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME;" >/dev/null
    ok "DB '$DB_NAME' 생성"
fi

if [[ $SKIP_PGVECTOR -eq 0 ]]; then
    if sudo -u postgres psql -d "$DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null 2>&1; then
        ok "pgvector 확장 활성화 (시맨틱 검색 사용 가능)"
    else
        warn "pgvector CREATE EXTENSION 실패 — 패키지 또는 source build 확인 필요"
    fi
fi

# -----------------------------------------------------------------------------
# 완료
# -----------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " 설치 완료"
echo "============================================================"
echo " PostgreSQL : $PG_VERSION (서비스: $SERVICE_NAME)"
echo " 데이터베이스: $DB_NAME"
echo " 포트       : 5432 (default)"
echo " superuser  : postgres / 비밀번호: $SUPER_PASSWORD"
echo ""
echo " 다음 단계:"
echo "   cd ../api_server"
echo "   echo 'DATABASE_URL=postgresql+asyncpg://postgres:$SUPER_PASSWORD@localhost:5432/$DB_NAME' > .env"
echo "   bash ../deploy/native_install.sh"
echo ""
echo "============================================================"
