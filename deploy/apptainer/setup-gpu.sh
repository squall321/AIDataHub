#!/usr/bin/env bash
# AI Data Hub — GPU 사용 셋업 (드라이버 설치 X, venv 만 다룸).
#
# 전제 (사람이 직접 끝낸 상태로 들어와야 함):
#   1) NVIDIA 드라이버 설치(예: nvidia-driver-535)
#   2) 재부팅
#   3) `nvidia-smi` 정상 — GPU 인식, Driver/CUDA 표시
#   (드라이버 설치는 커널/Secure Boot/MOK/재부팅 등 환경 의존이라
#    이 스크립트가 자동화하지 않음 = 의도된 설계.)
#
# 이 스크립트가 하는 일 (전부 user-space, sudo 불필요):
#   A) nvidia-smi 사전점검 — 안 되면 수동 절차 안내 후 exit
#   B) venv 의 torch 를 CUDA 휠(기본 cu121)로 교체 — 멱등
#   C) torch.cuda.is_available() + 모델 device 검증
#   D) API 재기동
#   E) recommend/search 라이브 latency 측정 (전/후 비교용)
#
# 옵션:
#   bash setup-gpu.sh                  # 기본 cu121
#   AIDH_TORCH_CUDA=cu124 bash setup-gpu.sh    # 휠 버전 지정 (cu118/cu121/cu124/cu128)
#   bash setup-gpu.sh --check-only     # 설치/재기동 없이 현재 상태만 진단
#   bash setup-gpu.sh --skip-restart   # torch 교체만, API 재기동/벤치 생략
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
load_env
export_proxy

CU="${AIDH_TORCH_CUDA:-cu121}"
CHECK_ONLY=0
SKIP_RESTART=0
for a in "$@"; do
  case "$a" in
    --check-only)   CHECK_ONLY=1 ;;
    --skip-restart) SKIP_RESTART=1 ;;
    -h|--help)      sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

VENV_PY="$API_DIR/.venv/bin/python"
[[ -x "$VENV_PY" ]] || { echo "[ERROR] venv python 없음: $VENV_PY  (먼저 bash setup.sh)" >&2; exit 1; }

echo "================================================================"
echo " AI Data Hub — GPU 사용 셋업  (cu wheel: $CU, check-only=$CHECK_ONLY)"
echo "================================================================"

# ── A) nvidia-smi 사전점검 ─────────────────────────────────────────
echo "[A] nvidia-smi 사전점검"
if ! command -v nvidia-smi >/dev/null 2>&1; then
  cat >&2 <<EOF
[ERROR] nvidia-smi 없음 — 드라이버 미설치.
이 스크립트는 드라이버를 설치하지 않습니다. 사람이 직접 1회:
  1) sudo apt-get update
  2) sudo apt-get install -y nvidia-driver-535 nvidia-utils-535
  3) sudo reboot
  4) nvidia-smi  로 'NVIDIA RTX A4000' + Driver/CUDA Version 보이면 성공
끝나면 다시 이 스크립트 실행하세요.
Secure Boot ON 환경은 설치 중 MOK 비번 → 재부팅 시 enroll.
EOF
  exit 1
fi
if ! nvidia-smi >/dev/null 2>&1; then
  echo "[ERROR] nvidia-smi 실행 실패 — 재부팅/모듈 로드 미완. 'sudo reboot' 후 재시도." >&2
  exit 1
fi
_drv="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1 || echo '?')"
_gpu="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1 || echo '?')"
echo "  ✓ Driver $_drv  |  GPU $_gpu"

# 환경변수 가드
if [[ -n "${CUDA_VISIBLE_DEVICES+x}" && -z "${CUDA_VISIBLE_DEVICES:-}" ]]; then
  echo "[WARN] CUDA_VISIBLE_DEVICES='' (빈 문자열) 가 export 됨 — GPU 숨김. 해제: 'unset CUDA_VISIBLE_DEVICES'" >&2
fi
if env | grep -q '^PIP_INDEX_URL='; then
  echo "[WARN] PIP_INDEX_URL 가 환경에 박혀 있음 — 아래 pip 호출이 cu wheel 인덱스를 무시할 수 있어 임시 unset 합니다."
  unset PIP_INDEX_URL PIP_EXTRA_INDEX_URL
fi

# ── B) torch 상태 점검 + 필요시 cu wheel 로 교체 ───────────────────
echo "[B] torch 상태 점검"
TORCH_INFO="$("$VENV_PY" -m pip show torch 2>/dev/null | grep -E '^(Version|Location)' || true)"
echo "$TORCH_INFO" | sed 's/^/    /'
TORCH_VER="$(echo "$TORCH_INFO" | awk '/^Version/ {print $2}')"

needs_install=1
if [[ -n "$TORCH_VER" && "$TORCH_VER" == *+"$CU"* ]]; then
  echo "    ✓ 이미 $CU wheel — 추가 설치 skip"
  needs_install=0
fi

if [[ $CHECK_ONLY -eq 1 ]]; then
  echo "  (--check-only — pip 설치 건너뜀)"
elif [[ $needs_install -eq 1 ]]; then
  echo "  → pip install torch ($CU wheel) — venv 만, sudo 불필요"
  INDEX="https://download.pytorch.org/whl/$CU"
  set +e
  "$VENV_PY" -m pip install --upgrade --force-reinstall torch --index-url "$INDEX"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    echo "[ERROR] pip 설치 실패 ($rc) — 보통 프록시/인덱스 도달 문제. 수동:" >&2
    echo "  HTTPS_PROXY=\$HTTPS_PROXY $VENV_PY -m pip install --upgrade --force-reinstall torch --index-url $INDEX" >&2
    exit 1
  fi
fi

# ── C) torch CUDA 인식 + 모델 device 검증 ────────────────────────
echo "[C] torch ↔ GPU 인식 검증"
"$VENV_PY" - <<'PY'
import torch, sys
ok = torch.cuda.is_available()
name = torch.cuda.get_device_name(0) if ok else 'NONE'
print(f"    cuda={ok}  |  device={name}  |  torch={torch.__version__}")
if not ok:
    print("[ERROR] torch.cuda.is_available()=False — wheel 이 CPU 거나 드라이버 미인식.", file=sys.stderr)
    sys.exit(1)
# sentence-transformers 모델 device 확인 (있을 때만; 무겁지 않음 — 로드만)
try:
    from sentence_transformers import SentenceTransformer
    # provider 와 별개로, e5_base 모델명 기준만 디바이스 확인용 로드
    m = SentenceTransformer('intfloat/multilingual-e5-base')
    print(f"    model_device={m.device}")
    if str(m.device).startswith('cuda'):
        print("    ✓ 모델이 GPU 위에 있음")
    else:
        print("[WARN] 모델이 cpu 위에 있음 — env(CUDA_VISIBLE_DEVICES)/import 순서 확인", file=sys.stderr)
except Exception as e:
    print(f"    (sentence-transformers 미설치 또는 모델 로드 실패: {e})", file=sys.stderr)
PY

# ── D) API 재기동 ────────────────────────────────────────────────
if [[ $CHECK_ONLY -eq 1 || $SKIP_RESTART -eq 1 ]]; then
  echo "[D] API 재기동 skip (옵션)"
else
  echo "[D] API 재기동"
  bash "$APPT_DIR/restart.sh" 2>&1 | tail -8 | sed 's/^/    /'
fi

# ── E) 라이브 latency 측정 (워밍 1회 + 본 3회) ───────────────────
if [[ $CHECK_ONLY -eq 0 ]]; then
  echo "[E] latency 측정 (예상: CPU 약 3.2s/1.6s → GPU 0.1~0.4s)"
  HC="${HOST_IP:-127.0.0.1}:${API_PORT:-8001}"
  if ! curl -s --max-time 3 "http://${HC}/api/system/health" >/dev/null 2>&1; then
    echo "    (API 응답 없음 — 재기동 직후라면 잠시 후 재실행)"
  else
    echo "    → 워밍 (1회, 첫 호출은 CUDA 컨텍스트 초기화로 다소 느림)"
    curl -s -o /dev/null --max-time 30 -X POST "http://${HC}/api/recommend/agents" \
      -H 'Content-Type: application/json' -d '{"q":"warmup","top_k":3}' >/dev/null || true
    echo "    → recommend ×3:"
    for i in 1 2 3; do
      curl -s -o /dev/null --max-time 20 -X POST "http://${HC}/api/recommend/agents" \
        -H 'Content-Type: application/json' -d '{"q":"배터리 스웰링","top_k":5}' \
        -w "      $i: %{time_total}s\n"
    done
    echo "    → search semantic ×3:"
    for i in 1 2 3; do
      curl -s -o /dev/null --max-time 20 -G "http://${HC}/api/search" \
        --data-urlencode "mode=semantic" --data-urlencode "q=배터리 스웰링" \
        --data-urlencode "limit=10" -w "      $i: %{time_total}s\n"
    done
    echo "    (별도 창에서 모니터: nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv -l 1)"
  fi
fi

echo
echo "================================================================"
echo "✓ GPU 사용 셋업 완료"
echo "  · torch:        $("$VENV_PY" -m pip show torch | awk '/^Version/{print $2}')"
echo "  · driver:       $_drv"
echo "  · gpu:          $_gpu"
echo "  · 추후 'pip install --upgrade torch' 같은 실수로 CPU 휠 회귀할 때 발견용:"
echo "    $VENV_PY -m pip show torch | grep Version    # 끝에 +$CU 가 보여야 정상"
echo "================================================================"
