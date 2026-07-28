"""텍스트 임베딩 REST — 다국어 e5 모델을 다른 서비스(agent-server 도구 시맨틱 검색)가 재사용한다.

모델을 중복 로딩하지 않고(메모리·폐쇄망 모델 배포 문제 회피) 이미 떠 있는 임베더를 공유한다.
multilingual-e5 라 한국어 질의("휨")와 영어 도구 설명("warpage")이 같은 공간에 놓인다.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/embed", tags=["embed"])

MAX_TEXTS = 512
MAX_CHARS = 2000


class EmbedRequest(BaseModel):
    texts: list[str] = Field(default_factory=list, max_length=MAX_TEXTS)
    # 검색 질의는 query 프리픽스, 문서는 passage 프리픽스 — e5 비대칭 모델 규약.
    kind: str = "passage"


@router.post("")
async def post_embed(req: EmbedRequest) -> dict:
    if not req.texts:
        return {"vectors": [], "dim": 0}
    try:
        from ..services.embedding import get_embedder
        emb = get_embedder()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"embedder unavailable: {e}") from e
    texts = [str(t)[:MAX_CHARS] for t in req.texts]
    try:
        if req.kind == "query":
            vecs = [emb.encode_query(t) for t in texts]
        else:
            vecs = emb.encode_many(texts) if hasattr(emb, "encode_many") else [emb.encode(t) for t in texts]
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"encode failed: {e}") from e
    return {"vectors": [list(map(float, v)) for v in vecs], "dim": len(vecs[0]) if vecs else 0}
