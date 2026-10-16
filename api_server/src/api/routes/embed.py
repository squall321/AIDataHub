"""텍스트 임베딩 REST — 다국어 임베더를 다른 서비스(agent-server 도구 시맨틱 검색 · 포털 대화 검색)가 재사용한다.

모델을 중복 로딩하지 않고(메모리·폐쇄망 모델 배포 문제 회피) 이미 떠 있는 임베더를 공유한다.
다국어 모델이라 한국어 질의("휨")와 영어 도구 설명("warpage")이 같은 공간에 놓인다.

벡터는 **활성 임베딩 슬롯**(``AIDH_EMBED_ACTIVE`` — ``api.services.embed_slots``)의 모델에서 나온다. 응답의
``model``(모델의 정식 이름) · ``dim`` 이 그 공간이다 — 소비처는 벡터 캐시를 이 둘로 가르고, 바뀌면 다시 색인한다
(슬롯을 v1 → m3 로 옮기면 e5-base 768 → BGE-M3 1024 로 바뀐다).
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/embed", tags=["embed"])

MAX_TEXTS = 512


class EmbedRequest(BaseModel):
    texts: list[str] = Field(default_factory=list, max_length=MAX_TEXTS)
    # 검색 질의는 query 프리픽스, 문서는 passage 프리픽스 — e5 비대칭 모델 규약(BGE-M3 는 접두사 없음).
    kind: str = "passage"


@router.post("")
async def post_embed(req: EmbedRequest) -> dict:
    from ..services import embed_slots

    slot = embed_slots.active()
    if not req.texts:
        # 모델을 올리지 않고도 공간을 알린다 — 소비처가 빈 요청으로 지금 공간을 물을 수 있다.
        return {"vectors": [], "dim": slot.dim, "model": embed_slots.model_id(slot), "slot": slot.name}
    try:
        from ..services.embedding import input_overflow
        emb = await asyncio.to_thread(embed_slots.embedder, slot)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"embedder unavailable: {e}") from e
    # 글자로 먼저 자르지 않는다 — 예전 2,000자 선절단은 말없이 걸렸다. 실효 한도는 모델 입력
    # 토큰(e5 512)이고, 넘친 텍스트의 순번을 over_limit 로 돌려준다(뒤쪽은 벡터에 안 들어간다).
    texts = [str(t) for t in req.texts]
    is_query = req.kind == "query"

    def _run() -> tuple[list, list[int]]:
        # 스레드에서 돈다 — 같은 모델로 백필이 도는 동안 그 잠금(한 묶음)을 기다려도 이벤트 루프는 서지 않는다.
        if is_query:
            vecs = [emb.encode_query(t) for t in texts]
        else:
            vecs = emb.encode_many(texts) if hasattr(emb, "encode_many") else [emb.encode(t) for t in texts]
        over = [i for i, t in enumerate(texts) if input_overflow(emb, t, is_query=is_query)]
        return vecs, over

    try:
        vecs, over = await asyncio.to_thread(_run)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"encode failed: {e}") from e
    return {
        "vectors": [list(map(float, v)) for v in vecs],
        "dim": len(vecs[0]) if vecs else 0,
        "model": getattr(emb, "model_id", "") or getattr(emb, "name", ""),
        "slot": slot.name,
        "max_tokens": emb.input_limit(),
        "over_limit": over,
    }
