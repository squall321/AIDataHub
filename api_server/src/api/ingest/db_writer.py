"""정규화된 ``RecordIn`` 을 DB 에 영속화한다.

설계:
    - 멱등(idempotent): ``id`` 중복 시 ``content_hash`` 비교 후 동일이면 skip,
      다르면 update.
    - DOC 변종은 ``content.sections`` 를 walk 하여 ``RecordSection`` 을 평탄화 생성
      (level ≤ 3 까지).
    - ``record.agents`` 목록을 ``agent_records`` junction 으로 동기화한다 (대상 agent 가
      ``agents`` 테이블에 없으면 stub row 를 생성).

Agent 1 의 ``api.db.models`` 가 import 안 될 수 있으므로 함수 단위 import 한다.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas import RecordIn
from ..schemas.id_format import parse_id
from .normalizer import compute_content_hash

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 결과 dataclass
# ---------------------------------------------------------------------------
class WriteResult:
    """write_record 결과를 나타내는 단순 컨테이너.

    Attributes:
        record: ORM ``Record`` 인스턴스.
        action: ``"inserted"`` | ``"updated"`` | ``"skipped"``.
        sections_written: DOC 의 경우 생성된 ``RecordSection`` 수.
    """

    __slots__ = ("record", "action", "sections_written")

    def __init__(self, record: Any, action: str, sections_written: int = 0) -> None:
        self.record = record
        self.action = action
        self.sections_written = sections_written

    def __repr__(self) -> str:  # pragma: no cover
        rid = getattr(self.record, "id", "?")
        return (
            f"<WriteResult id={rid!r} action={self.action!r} "
            f"sections={self.sections_written}>"
        )


# ---------------------------------------------------------------------------
# 섹션 평탄화
# ---------------------------------------------------------------------------
def _flatten_sections(
    sections: list[dict[str, Any]],
    *,
    max_level: int = 3,
) -> list[dict[str, Any]]:
    """중첩된 sections (children 트리) 를 평탄 리스트로 펼친다.

    각 항목은 ``id``/``level``/``title``/``content_text``/``figure_refs``/``table_refs``
    키를 가진다. 동일 ``section_id`` 가 여러 번 나오면 첫 등장만 보존(고유 제약).
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def walk(node: dict[str, Any], depth: int) -> None:
        if not isinstance(node, dict):
            return
        level = int(node.get("level", depth) or depth)
        sid = str(node.get("id", ""))
        if level <= max_level and sid and sid not in seen:
            seen.add(sid)
            out.append(
                {
                    "section_id": sid,
                    "level": level,
                    "title": str(node.get("title", "")),
                    "content_text": _blocks_to_text(node.get("blocks") or []),
                    "figure_refs": list(node.get("figure_refs") or []),
                    "table_refs": list(node.get("table_refs") or []),
                }
            )
        for child in node.get("children") or []:
            walk(child, depth + 1)

    for top in sections:
        walk(top, 1)
    return out


def _blocks_to_text(blocks: list[dict[str, Any]]) -> str:
    """``blocks`` 배열에서 텍스트만 이어붙여 RAG 입력용 평문을 만든다."""
    parts: list[str] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "paragraph":
            text = b.get("text")
            if text:
                parts.append(str(text))
        elif t == "heading":
            text = b.get("text")
            if text:
                parts.append(str(text))
        elif t == "list":
            items = b.get("items") or []
            for it in items:
                if isinstance(it, str):
                    parts.append(f"- {it}")
                elif isinstance(it, dict) and "text" in it:
                    parts.append(f"- {it['text']}")
        elif t == "code":
            text = b.get("text") or b.get("code")
            if text:
                parts.append(str(text))
        # figure/table 참조는 figure_refs/table_refs 에 별도 보관되므로 본문에는 포함하지 않음.
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 메인 진입점
# ---------------------------------------------------------------------------
async def write_record(
    session: AsyncSession,
    record_in: RecordIn,
) -> WriteResult:
    """``RecordIn`` 을 DB 에 INSERT or UPDATE 하고 결과를 반환한다.

    Notes:
        - 호출 측에서 ``await session.commit()`` 을 책임진다.
    """
    # 모델은 함수 단위 import (Agent 1 미완성 환경에서도 schema 모듈은 import 가능하도록).
    from ..db.models import (
        Agent,
        AgentRecord,
        Record,
        RecordAttachment,
        RecordSection,
    )

    parts = parse_id(record_in.id)
    content_hash = compute_content_hash(record_in.content)

    existing = await session.get(Record, record_in.id)

    if existing is not None:
        if existing.content_hash == content_hash:
            logger.info("Record %s unchanged — skipping", record_in.id)
            return WriteResult(existing, action="skipped")

        # update — 모든 mutable 필드 갱신.
        existing.data_type = record_in.data_type
        existing.division = parts["division"]
        existing.team = parts["team"]
        existing.year = parts["year"]
        existing.seq = parts["seq"]
        existing.title = record_in.title
        existing.summary = record_in.summary
        existing.tags = list(record_in.tags)
        existing.agents = list(record_in.agents)
        existing.schema_version = record_in.schema_version
        existing.content = dict(record_in.content)
        existing.content_hash = content_hash
        existing.source_file = record_in.source_file
        existing.author = record_in.author
        existing.department = record_in.department
        existing.project = record_in.project
        existing.version = record_in.version
        action = "updated"
        target = existing

        # 자식(sections) 재구성: 기존 삭제 후 재삽입.
        await _resync_sections(session, target, record_in, RecordSection)
    else:
        target = Record(
            id=record_in.id,
            data_type=record_in.data_type,
            division=parts["division"],
            team=parts["team"],
            year=parts["year"],
            seq=parts["seq"],
            title=record_in.title,
            summary=record_in.summary,
            tags=list(record_in.tags),
            agents=list(record_in.agents),
            schema_version=record_in.schema_version,
            content=dict(record_in.content),
            content_hash=content_hash,
            source_file=record_in.source_file,
            author=record_in.author,
            department=record_in.department,
            project=record_in.project,
            version=record_in.version,
        )
        session.add(target)
        action = "inserted"
        # 섹션은 flush 이후 FK 가 살아있도록 add 후 동기화 한 번 수행.
        await session.flush()
        await _resync_sections(session, target, record_in, RecordSection)

    # agent_records junction 동기화.
    sections_written = await _count_sections(session, target.id, RecordSection)
    await _resync_agents(session, target, record_in, Agent, AgentRecord)

    # record_attachments 동기화 + Record.has_attachments / attachment_count 갱신.
    await _resync_attachments(session, target, record_in, RecordAttachment)

    return WriteResult(target, action=action, sections_written=sections_written)


async def _resync_sections(
    session: AsyncSession,
    record: Any,
    record_in: RecordIn,
    RecordSection: Any,
) -> None:
    """DOC variant 일 때 ``record_sections`` 행을 재동기화."""
    # 기존 섹션 제거 (ORM 캐시 + DB 양쪽).
    await session.execute(
        RecordSection.__table__.delete().where(
            RecordSection.record_id == record.id
        )
    )

    if record_in.data_type != "DOC":
        return

    sections = record_in.content.get("sections") or []
    if not isinstance(sections, list):
        return

    flattened = _flatten_sections(sections, max_level=3)
    for row in flattened:
        session.add(
            RecordSection(
                record_id=record.id,
                section_id=row["section_id"],
                level=row["level"],
                title=row["title"],
                content_text=row["content_text"],
                figure_refs=row["figure_refs"],
                table_refs=row["table_refs"],
            )
        )
    await session.flush()


async def _count_sections(
    session: AsyncSession, record_id: str, RecordSection: Any
) -> int:
    from sqlalchemy import func

    result = await session.execute(
        select(func.count())
        .select_from(RecordSection)
        .where(RecordSection.record_id == record_id)
    )
    return int(result.scalar_one() or 0)


async def _resync_attachments(
    session: AsyncSession,
    record: Any,
    record_in: RecordIn,
    RecordAttachment: Any,
) -> None:
    """``record.content`` 의 ``attachments[]`` (+ legacy ``figures[]``) 를
    ``record_attachments`` 행으로 동기화.

    - 기존 첨부는 모두 제거 후 재삽입 (멱등 동작).
    - ``figures[]`` 만 존재하는 legacy 산출물은 ``kind="figure"`` 인 첨부로
      자동 변환된다.
    - 캡션 누락 시 placeholder ``"(캡션 누락 — 검수 필요)"`` 를 채운다.
    - ``Record.has_attachments`` / ``Record.attachment_count`` 를 갱신.
    """
    from ..schemas.attachment import (
        CAPTION_MISSING_PLACEHOLDER,
        infer_attachment_kind,
    )

    # 기존 첨부 제거.
    await session.execute(
        RecordAttachment.__table__.delete().where(
            RecordAttachment.record_id == record.id
        )
    )

    content = record_in.content or {}

    raw_attachments: list[dict[str, Any]] = []
    if isinstance(content.get("attachments"), list):
        raw_attachments.extend(
            a for a in content["attachments"] if isinstance(a, dict)
        )

    # legacy 호환: figures[] → kind="figure" attachment.
    if isinstance(content.get("figures"), list):
        for fig in content["figures"]:
            if not isinstance(fig, dict):
                continue
            # 같은 id 가 attachments[] 에도 있으면 중복 추가 방지.
            fig_id = fig.get("id")
            if fig_id and any(a.get("id") == fig_id for a in raw_attachments):
                continue
            raw_attachments.append(
                {
                    "id": fig.get("id"),
                    "number": fig.get("number"),
                    "kind": "figure",
                    "caption": fig.get("caption"),
                    "section_ref": fig.get("section_ref"),
                    "file_name": fig.get("file_name"),
                    "file_path": fig.get("image_path") or fig.get("file_path"),
                    "mime_type": fig.get("mime_type"),
                    "size_bytes": fig.get("size_bytes"),
                    "hash_sha256": fig.get("hash_sha256"),
                    # legacy 변환 표시.
                    "extra": {"legacy_from": "figures"},
                }
            )

    inserted = 0
    seen_ids: set[str] = set()
    seen_numbers: set[int] = set()
    next_number = 0

    for raw in raw_attachments:
        # number 결정 — 없거나 충돌이면 자동 부여.
        try:
            num = int(raw.get("number")) if raw.get("number") is not None else None
        except (TypeError, ValueError):
            num = None
        if num is None or num in seen_numbers:
            next_number += 1
            while next_number in seen_numbers:
                next_number += 1
            num = next_number
        seen_numbers.add(num)
        next_number = max(next_number, num)

        # id 결정 — 명시 또는 ``{record_id}-A{nnn}``.
        att_id = raw.get("id") or f"{record.id}-A{num:03d}"
        if att_id in seen_ids:
            # 충돌 시 새 id 생성.
            att_id = f"{record.id}-A{num:03d}"
        seen_ids.add(att_id)

        # caption 보강 (필수).
        caption = raw.get("caption")
        if not caption or not str(caption).strip():
            caption = CAPTION_MISSING_PLACEHOLDER
            logger.warning(
                "attachment %s: caption missing — using placeholder", att_id
            )

        # kind — 명시 우선, 없으면 file_name/mime 으로 추정.
        kind = raw.get("kind")
        if not kind or not str(kind).strip():
            kind = infer_attachment_kind(
                filename=raw.get("file_name") or raw.get("file_path"),
                mime=raw.get("mime_type"),
            )

        # file_path — POSIX-style 강제 (cross-platform).
        file_path = raw.get("file_path")
        if isinstance(file_path, str) and file_path:
            file_path = file_path.replace("\\", "/")

        extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}

        session.add(
            RecordAttachment(
                id=str(att_id),
                record_id=record.id,
                number=int(num),
                kind=str(kind),
                caption=str(caption).strip(),
                file_name=raw.get("file_name"),
                file_path=file_path,
                mime_type=raw.get("mime_type"),
                size_bytes=raw.get("size_bytes"),
                hash_sha256=raw.get("hash_sha256"),
                section_ref=raw.get("section_ref"),
                extra=dict(extra),
            )
        )
        inserted += 1

    # records summary 컬럼 갱신.
    record.has_attachments = inserted > 0
    record.attachment_count = inserted

    await session.flush()


async def _resync_agents(
    session: AsyncSession,
    record: Any,
    record_in: RecordIn,
    Agent: Any,
    AgentRecord: Any,
) -> None:
    """``agents`` 배열에 따라 junction 테이블을 갱신.

    누락된 agent stub 은 자동 생성한다 (외래키 충족 목적, 메타데이터는 비어있음).
    """
    # 기존 매핑 제거.
    await session.execute(
        AgentRecord.__table__.delete().where(AgentRecord.record_id == record.id)
    )

    seen: set[str] = set()
    for idx, agent_type in enumerate(record_in.agents or []):
        if not agent_type or agent_type in seen:
            continue
        seen.add(agent_type)
        # agents 테이블에 stub 생성 (없을 때만).
        existing_agent = await session.get(Agent, agent_type)
        if existing_agent is None:
            session.add(
                Agent(
                    agent_type=agent_type,
                    name=agent_type,
                    description="",
                    common_tags=[],
                    data_types=[],
                )
            )
        session.add(
            AgentRecord(
                agent_type=agent_type,
                record_id=record.id,
                priority=idx + 1,
            )
        )
    await session.flush()


__all__ = ["WriteResult", "write_record"]
