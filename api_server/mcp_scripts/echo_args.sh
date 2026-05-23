#!/usr/bin/env bash
# 예제 — 받은 인자를 그대로 stdout 으로 echo. mcp_scripts 동작 검증용.
# Manifest: echo_args.mcp.yaml
#
# long_flags 스타일이므로 호출은 다음과 같다:
#   echo_args --message "hello" --repeat 3 --upper
#
# upper 가 켜져 있으면 결과를 대문자로 변환.
set -euo pipefail

MESSAGE=""
REPEAT=1
UPPER=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --message) MESSAGE="$2"; shift 2 ;;
    --repeat)  REPEAT="$2";  shift 2 ;;
    --upper)   UPPER=1;      shift   ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$MESSAGE" ]]; then
  echo "missing --message" >&2
  exit 2
fi

out=""
for ((i=0; i<REPEAT; i++)); do
  out+="$MESSAGE"$'\n'
done

if [[ "$UPPER" == "1" ]]; then
  out=$(echo "$out" | tr '[:lower:]' '[:upper:]')
fi

printf '%s' "$out"
