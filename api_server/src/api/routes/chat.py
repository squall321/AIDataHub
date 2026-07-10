"""``/api/chat`` — 자체 챗 (vLLM + 로컬 도구 tool-calling) SSE 엔드포인트.

메인 페이지 챗이 호출한다. 대화로 데이터를 수집·검색한다.
스트리밍은 ``text/event-stream`` (status/result/error/done). 브라우저는 EventSource 가
아니라 fetch+ReadableStream 으로 받는다 (POST + X-API-Key 헤더가 필요하기 때문).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.services import chat_svc

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str = Field(..., description="user | assistant | system")
    content: str = Field("", description="메시지 본문(붙여넣은 표/문서 포함)")


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(..., min_length=1, description="대화 히스토리(전량 왕복)")
    mode: str = Field("", description="'echo' = 원격 vLLM 없이 SSE 경로 검증(dev)")


def _sse(event: str, data: dict[str, Any]) -> str:
    """이벤트 dict → SSE 프레임 문자열."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("", summary="자연어 챗 → 도구 호출로 데이터 수집·검색 (SSE)")
@router.post("/", include_in_schema=False)
async def post_chat(
    payload: ChatRequest,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> StreamingResponse:
    """대화를 받아 SSE 로 status/result 를 스트리밍한다.

    import_record 등 쓰기 도구의 인증은 ``X-API-Key`` 헤더로 전달된다.
    """
    messages = [m.model_dump() for m in payload.messages]

    async def gen():
        async for ev in chat_svc.stream_chat(messages, api_key=x_api_key, mode=payload.mode):
            yield _sse(ev["event"], ev["data"])

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx SSE 버퍼링 방지 (플레이북 §2-C)
            "Connection": "keep-alive",
        },
    )


__all__ = ["router"]
