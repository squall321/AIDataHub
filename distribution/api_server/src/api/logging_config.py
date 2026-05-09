"""구조화 로깅 설정 (stdlib only).

JSON / text 두 가지 포맷 지원. 환경변수:
    - LOG_FORMAT  : "json" (default) | "text"
    - LOG_LEVEL   : default "INFO"

크로스플랫폼: ``logging.StreamHandler(sys.stdout)`` 만 사용 (syslog 등 OS 의존성 없음).
``Path`` 기반 파일 핸들러는 옵션이며 미설정.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

# request 컨텍스트에 부착할 표준 키 화이트리스트 (extra= 로 들어오는 값)
_RESERVED_LOGRECORD_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "asctime", "taskName",
}


class JsonFormatter(logging.Formatter):
    """JSON 한 줄(line) 포맷터. 외부 의존성 없음."""

    def __init__(self, *, ensure_ascii: bool = False) -> None:
        super().__init__()
        self.ensure_ascii = ensure_ascii

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        # extra=... 로 들어온 임의 키들 (request_id, method, path, status, duration_ms, user 등)
        for k, v in record.__dict__.items():
            if k in _RESERVED_LOGRECORD_ATTRS or k.startswith("_"):
                continue
            payload[k] = _safe(v)
        return json.dumps(payload, ensure_ascii=self.ensure_ascii, default=str)


def _safe(value: Any) -> Any:
    """JSON 직렬화 가능한 값으로 best-effort 변환."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    return str(value)


def configure_logging(
    *, level: str = "INFO", fmt: str = "json"
) -> logging.Handler:
    """루트 로거 핸들러를 (재)구성하고 핸들러 인스턴스를 반환.

    멱등: 동일 stream 의 기존 핸들러는 제거 후 재설치.
    """
    root = logging.getLogger()
    root.setLevel(level.upper() if isinstance(level, str) else level)

    handler = logging.StreamHandler(stream=sys.stdout)
    if fmt.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s"
            )
        )

    # 동일한 stream 의 stale 핸들러 제거 후 등록 (테스트 반복 호출 안전)
    keep = [
        h for h in root.handlers
        if not (
            isinstance(h, logging.StreamHandler)
            and getattr(h, "stream", None) is sys.stdout
        )
    ]
    root.handlers = keep + [handler]
    return handler


__all__ = ["JsonFormatter", "configure_logging"]
