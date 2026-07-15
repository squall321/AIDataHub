#!/usr/bin/env bash
# AI Data Hub — DB → .sql.gz dump (+ retention).
# 사용:
#   bash deploy/apptainer/backup-db.sh                  # /tmp 에 자동 이름
#   bash deploy/apptainer/backup-db.sh /path/to/x.sql.gz
#
# retention: 같은 디렉토리의 aidh-db-*.sql.gz / pre-update-db-*.sql.gz 중
#   최신 AIDH_BACKUP_KEEP(기본 10) 개만 남기고 오래된 것 삭제. 무한 누적
#   방지 (update.sh 자동백업이 디스크를 채우는 것을 막음).
#   AIDH_BACKUP_KEEP=0 이면 정리 비활성.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env

OUT="${1:-/tmp/aidh-db-$(date +%Y%m%d-%H%M%S).sql.gz}"

if ! instance_running "$INST_POSTGRES"; then
  echo "[ERROR] $INST_POSTGRES 인스턴스 없음 — 먼저 start_postgres.sh" >&2
  exit 1
fi

# 좀비 인스턴스 방지 — instance_running 은 list 에 있으면 true 라 마운트가 죽은
# 좀비도 통과한다(빈 덤프의 원인). 실제 쿼리(SELECT 1)로 살아있는지 확인.
if ! apptainer exec "instance://$INST_POSTGRES" \
      psql -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      -tAc 'select 1' >/dev/null 2>&1; then
  echo "[ERROR] $INST_POSTGRES 가 쿼리에 응답 안 함 (좀비/기동중) — pg_dump 중단(빈 덤프 방지)." >&2
  echo "        복구: bash deploy/apptainer/restart.sh --pg  (또는 boot.sh --force)" >&2
  exit 1
fi

echo "→ pg_dump $POSTGRES_DB → $OUT"
# 임시 파일에 덤프 후 검증 → 원자적 이동. 검증 실패 시 OUT 을 만들지 않는다(fail-closed):
# 빈/손상 덤프가 정상 이름으로 남아 retention 이나 Drive 업로드를 오염시키는 것을 막는다.
TMP="${OUT}.partial"
rm -f "$TMP"
if ! apptainer exec "instance://$INST_POSTGRES" \
      pg_dump -h 127.0.0.1 -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
              --no-owner --no-acl -Fp 2>/dev/null \
      | gzip > "$TMP"; then
  echo "[ERROR] pg_dump 실패 — 덤프 폐기(OUT 미생성)." >&2
  rm -f "$TMP"; exit 1
fi

# 검증: gzip 무결성 + 스키마 존재(CREATE TABLE) + 최소 크기(빈 20바이트 gz 차단).
if ! gzip -t "$TMP" 2>/dev/null; then
  echo "[ERROR] 덤프 gzip 손상 — 폐기." >&2; rm -f "$TMP"; exit 1
fi
_bytes=$(stat -c%s "$TMP" 2>/dev/null || echo 0)
# grep -c(스트림 전체 읽음)로 CREATE TABLE 수를 센다 — grep -q 는 조기 종료해
# zcat 에 SIGPIPE 를 유발하고, pipefail 하에서 파이프라인을 오탐(실패)시킨다.
_ntab=$(zcat "$TMP" 2>/dev/null | grep -c 'CREATE TABLE' || true)
if [[ "$_bytes" -lt 1000 || "${_ntab:-0}" -lt 1 ]]; then
  echo "[ERROR] 덤프가 비었거나 스키마 없음 (${_bytes} bytes, CREATE TABLE=${_ntab:-0}) — 폐기(좀비/실패 의심)." >&2
  rm -f "$TMP"; exit 1
fi
mv -f "$TMP" "$OUT"

SIZE=$(ls -lh "$OUT" | awk '{print $5}')
SHA=$(sha256sum "$OUT" | awk '{print $1}')
echo
echo "✓ backup 완료"
echo "  file: $OUT"
echo "  size: $SIZE"
echo "  sha256: $SHA"
echo
echo "복원:"
echo "  bash deploy/apptainer/restore-db.sh $OUT"

# ── retention — 오래된 백업 정리 ────────────────────────────────────
KEEP="${AIDH_BACKUP_KEEP:-10}"
if [[ "$KEEP" -gt 0 ]]; then
  BK_DIR="$(cd "$(dirname "$OUT")" && pwd)"
  # aidh-db-*.sql.gz + pre-update-db-*.sql.gz 를 한 풀로 보고 최신 KEEP 개 유지.
  mapfile -t _all < <(ls -1t "$BK_DIR"/aidh-db-*.sql.gz "$BK_DIR"/pre-update-db-*.sql.gz 2>/dev/null)
  if [[ "${#_all[@]}" -gt "$KEEP" ]]; then
    _del=("${_all[@]:$KEEP}")
    echo
    echo "→ retention: ${#_all[@]}개 중 오래된 ${#_del[@]}개 삭제 (keep=$KEEP, dir=$BK_DIR)"
    for f in "${_del[@]}"; do rm -f "$f" && echo "    - $(basename "$f")"; done
  fi
fi
