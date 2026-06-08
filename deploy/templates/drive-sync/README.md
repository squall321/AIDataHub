# Drive-Sync 표준 템플릿 (project-agnostic DB 백업/복원 키트)

> 검증 기원: MXWhitePaper (실운영) + AIDataHub (2026-06-01 활성화 검증)

새 프로젝트에 PostgreSQL DB 의 Google Drive 기반 백업/복원을 1회 셋업으로
달려면 이 디렉터리를 **그대로 복사하고 `PROJECT.conf` 만 1개 채우면 됩니다.**

## 디자인 원칙

1. **rclone remote 공유** — 1개 토큰으로 N개 프로젝트 (`ApptainerImages:`)
2. **프로젝트별 폴더 분리** — `<remote>:<Project>/db-dumps/` 로 격리
3. **TS 기반 파일명 정렬** — `<proj>-db-YYYYMMDD-HHMMSSZ.sql.gz` (최신 = `sort | tail -1`)
4. **sha256 동봉** — 네트워크 손상 감지 + 복원 전 무결성 검증
5. **안전백업 후 DROP+CREATE+restore** — restore 실패해도 직전 상태 복귀
6. **retain N개** — Drive 용량 폭주 방지 (기본 5)

## 디렉터리 사용법

### 1. 새 프로젝트로 복사

```bash
cp -r /home/koopark/claude/AIDataHub/deploy/templates/drive-sync \
      /path/to/<NewProject>/deploy/drive-sync
```

### 2. `PROJECT.conf` 만 채우기

```bash
cd /path/to/<NewProject>/deploy/drive-sync
cp PROJECT.conf.example PROJECT.conf
$EDITOR PROJECT.conf
```

채울 항목 (예시):

```bash
PROJ_PREFIX=sf                                 # dump 파일명 prefix (sf-db-...)
PROJ_NAME=SignalForge                          # Drive 폴더명 = <remote>:SignalForge/db-dumps
PROJ_PG_INSTANCE=sf_postgres                   # apptainer instance 이름
PROJ_ENV_FILE=/home/koopark/claude/SignalForge/.env
                                               # POSTGRES_USER/PASSWORD/DB/PORT 가 여기에
PROJ_DUMP_DIR=/home/koopark/claude/SignalForge/backups
PROJ_DRIVE_REMOTE_DEFAULT=ApptainerImages      # rclone remote 이름 (기본)
PROJ_DRIVE_RETAIN_DEFAULT=5                    # Drive 보존 개수 (기본)
```

### 3. 1회 셋업

```bash
bash setup-drive-sync.sh   # rclone remote 확인 + Drive 폴더 보장 + .env 갱신
```

### 4. 첫 백업

```bash
bash backup-to-drive.sh    # pg_dump → tar.gz → sha256 → Drive 업로드
```

### 5. (옵션) 일일 자동 백업 — crontab

```cron
30 4 * * *  /bin/bash /path/to/<NewProject>/deploy/drive-sync/backup-to-drive.sh \
              > /var/log/<proj>-backup.log 2>&1
```

### 6. 다른 서버에서 복원

```bash
# 새 서버에서: 동일하게 복사 + PROJECT.conf 채우고
bash setup-drive-sync.sh
bash sync-from-drive.sh    # Drive 최신 dump 다운로드 → 검증 → restore → /health 확인
```

## 파일 인덱스

| 파일 | 역할 |
|---|---|
| `PROJECT.conf.example` | 변수 템플릿 |
| `_drive_common.sh` | prefix-agnostic 헬퍼 + PROJECT.conf 로딩 |
| `setup-drive-sync.sh` | rclone remote 확인 + Drive 폴더 보장 + .env 갱신 (1회) |
| `backup-db.sh` | pg_dump → 로컬 tar.gz + sha256 |
| `backup-to-drive.sh` | backup-db + rclone copy + 보존정책 |
| `restore-db.sh` | 로컬 sql.gz → 안전백업 후 DROP+CREATE+restore |
| `sync-from-drive.sh` | Drive 최신 dump → 검증 → restore (one-shot) |

## 검증 패턴

이식 후 E2E 검증 (필수 1회):

```bash
# 소스 서버
bash backup-to-drive.sh

# 타깃 서버 (다른 머신)
bash sync-from-drive.sh

# 결과 확인
psql -h 127.0.0.1 -p <PORT> -U <USER> -d <DB> -c "SELECT count(*) FROM <core_table>;"
```

소스/타깃 row count 가 동일하면 PASS.

## FAQ

**Q. rclone 토큰을 다시 받아야 하나?**
A. 같은 호스트에서 다른 프로젝트라면 NO — `ApptainerImages:` remote 가 공유됨.
   다른 호스트면 1회 `rclone authorize "drive"` 필요.

**Q. PG 가 apptainer 가 아니면?**
A. `_drive_common.sh` 의 `pg_dump_cmd()` / `psql_cmd()` 두 함수만
   호스트 pg_dump / psql 로 바꾸면 됨.

**Q. retain 정책을 더 세밀하게 (요일별 / 월별) 하려면?**
A. `backup-to-drive.sh` 의 `rclone delete --min-age` 로직만 교체.
   기본은 "최신 N개만 유지" 단순 정책.
