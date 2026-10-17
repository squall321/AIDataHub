# start_api.sh — uvicorn 로그를 덧붙여 쓰고 기동 때 cap 을 넘었을 때만 한 벌 넘기는지(10-17 결정 16)
"""예전엔 `>` 로 열어 기동마다 앞 기록을 지웠다. `>`(O_APPEND 아님)라 포털 logrotate 의 copytruncate 에도 못 넣었다
(넣으면 잘린 뒤 다음 쓰기가 옛 오프셋으로 가 앞이 NUL 구멍이 된다)."""
import re
from pathlib import Path

START_API = (Path(__file__).resolve().parents[2] / "deploy" / "apptainer" / "start_api.sh").read_text(encoding="utf-8")


def test_uvicorn_로그는_덧붙여_쓰고_그_전에_크기를_본다():
    assert '>> "$LOG_DIR/uvicorn.log" 2>&1 &' in START_API
    assert not re.search(r'[^>]> "\$LOG_DIR/uvicorn\.log"', START_API), "uvicorn.log 를 아직 > 로 연다"
    assert START_API.index('rotate_log "$LOG_DIR/uvicorn.log"') < START_API.index('>> "$LOG_DIR/uvicorn.log"')
