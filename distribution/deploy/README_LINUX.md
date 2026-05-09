# Linux 환경 — 자동 설치 스크립트

이 폴더의 Linux 자동 설치 스크립트 사용 안내. **Docker 없는 환경** 에서 PostgreSQL + pgvector 를 한 번의 명령으로 설치.

## 시나리오별 명령

| 상황 | 명령 |
|------|------|
| **모두 자동** (PG + pgvector + AI 서버) — Docker 사용 | `bash deploy/install.sh` |
| **PG + pgvector 자동 설치** (Docker 없음) | `sudo bash deploy/install_postgres_linux.sh` |
| **PG 이미 설치됨, pgvector 만** | `sudo bash deploy/install_pgvector_linux.sh` |
| **PG + pgvector 다 됐음, Python 환경만** | `bash deploy/native_install.sh` |

## 검증된 distro

- Ubuntu 22.04 / 24.04
- Debian 12 (bookworm)
- RHEL 9 / Rocky 9 / AlmaLinux 9
- Fedora 39 / 40

## install_postgres_linux.sh

### 동작

1. **distro 자동 감지** (`/etc/os-release`) — Debian 계열 / RHEL 계열 / Fedora
2. **PGDG 공식 저장소** 등록 (`apt.postgresql.org` 또는 `download.postgresql.org/pub/repos/yum`)
3. **PG 설치** (default 18, `--pg-version` 으로 변경)
4. **pgvector** — 패키지 시도 → 실패 시 source build (`postgresql-server-dev` + git + make)
5. 서비스 자동 시작 (`systemctl enable --now`)
6. **postgres 비밀번호** 설정 (default `postgres`, `--password` 로 변경)
7. **ai_data DB** 생성 (`--db-name` 으로 변경)
8. **CREATE EXTENSION vector** 실행

### 옵션

```bash
sudo bash install_postgres_linux.sh                  # default: PG 18 + pgvector
sudo bash install_postgres_linux.sh --pg-version 16  # PG 16
sudo bash install_postgres_linux.sh --skip-pgvector  # PG 만
sudo bash install_postgres_linux.sh --password mypass --db-name myaidh
```

## install_pgvector_linux.sh

PG 가 이미 있을 때 pgvector 만 추가.

```bash
sudo bash install_pgvector_linux.sh                  # PG 버전 자동 감지
sudo bash install_pgvector_linux.sh --pg-version 17  # PG 17 강제
```

## 전체 흐름 (Linux native, Docker 없음)

```bash
# 1. PG + pgvector 자동 설치
sudo bash deploy/install_postgres_linux.sh

# 2. .env 작성
cd api_server
cat > .env <<EOF
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/ai_data
EMBEDDING_PROVIDER=e5_small
AUTO_EMBED_ON_INSERT=true
EOF

# 3. Python venv + 의존성 + alembic + 시드
bash ../deploy/native_install.sh

# 4. 서버 기동
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

## 트러블슈팅

| 증상 | 원인 / 대응 |
|------|-------------|
| `pgvector 패키지 미제공` | distro 가 오래됨 → source build 자동 시도. `gcc/make/git/postgresql-server-dev` 필요 |
| `systemctl: command not found` | systemd 미사용 환경 (예: WSL1) → `service postgresql start` 수동 |
| `peer authentication failed for user "postgres"` | `pg_hba.conf` 의 `peer` → `md5` 변경 또는 `sudo -u postgres psql` 사용 |
| `Could not open extension control file vector.control` | pgvector 빌드/설치 실패 → `install_pgvector_linux.sh` 재실행 |
| `pg_config not found` | `postgresql-server-dev-$PG_VERSION` (Debian) 또는 `postgresql${PG_VERSION}-devel` (RHEL) 설치 |

## 보안 주의

- `--password` 기본값(`postgres`) 은 PoC 용 — **운영 환경에선 강한 비밀번호로 변경**
- `pg_hba.conf` 의 외부 접근 정책은 별도 검토 (default 는 localhost only)
- HTTPS / 방화벽 / 백업 정책은 본 스크립트 범위 밖
