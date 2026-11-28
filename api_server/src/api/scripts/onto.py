# 카드 ↔ 온톨로지 객체 연결의 박스 명령 — 카드 낱말 짓기(build) · 허브 사전으로 잇기(bind) · 상태(status) · 전문가 프로필(profile)
"""카드 ↔ 온톨로지 객체 연결(HWAXPortal docs/expert-ontology 6절).

    bash deploy/apptainer/onto.sh build [--force]            # 카드 낱말을 다시 짓는다(지문이 같으면 건너뛴다 — 허브가 필요 없다)
    bash deploy/apptainer/onto.sh bind --hub <허브> --dict <파일>   # 허브 사전으로 잇는다(그 허브에서 사전에 든 타입의 연결만 다시 쓴다)
    bash deploy/apptainer/onto.sh status                     # 마지막 짓기 · 잇기와 줄 수(읽기만)
    bash deploy/apptainer/onto.sh profile [--domains]        # 전문가(또는 도메인)마다 타입별 셈(읽기만)

사전 파일은 ``{"hub": "...", "dict_version": "...", "entries": [{"type_slug", "key", "term"}, …]}`` 이다 — 허브가 exact 로
확인한 이름만 든다. 받는 일은 포털 ``infra/scripts/card-entity-bind.py`` 가 한다(이 명령은 게이트웨이 · 허브를 모른다).
사전에는 사내 객체 이름이 들어 있다 — 추적 폴더에 두지 않는다.

결과는 표준 출력에 JSON 한 덩이다(셈만 — 카드 글 · 객체 이름은 내지 않는다). bind 는 먼저 build 를 돈다(낡은 낱말로 잇지
않게). 종료 코드: 0 끝 · 1 오류(아무것도 쓰지 않았다 — 한 트랜잭션이다) · 2 인자 오류 · 3 표가 없다(이행 0033 전).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("api.scripts.onto")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    from ..db import base as db_base
    from ..services import onto_svc

    try:
        async with db_base.SessionLocal() as session:
            if args.cmd == "status":
                return await onto_svc.status(session)
            if args.cmd == "profile":
                prof = await onto_svc.profile(session)
                return {"domains": prof["domains"]} if args.domains else prof
            if args.cmd == "build":
                out = await onto_svc.build_mentions(session, force=args.force)
                await session.commit()
                return out
            doc = json.loads(Path(args.dict).read_text(encoding="utf-8"))
            if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
                raise ValueError("사전 파일 꼴이 틀렸다 — {hub, dict_version, entries[]} 여야 한다")
            hub = (args.hub or doc.get("hub") or "").strip()
            if not hub:
                raise ValueError("허브 이름이 없다 — --hub 또는 사전의 hub")
            built = await onto_svc.build_mentions(session)
            out = await onto_svc.bind_entities(
                session, hub=hub, entries=doc["entries"], dict_version=str(doc.get("dict_version") or ""),
                progress=lambda n: log.info("절 %d개를 훑었다", n))
            await session.commit()
            return {"mentions": {k: built.get(k) for k in ("rows", "skipped", "rules")}, "bind": out}
    finally:
        await db_base.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("sqlalchemy", "api.db.base"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(prog="python -m api.scripts.onto", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="카드 낱말을 다시 짓는다")
    b.add_argument("--force", action="store_true", help="지문이 같아도 다시 짓는다")
    d = sub.add_parser("bind", help="허브 사전으로 잇는다")
    d.add_argument("--dict", required=True, help="사전 파일(JSON)")
    d.add_argument("--hub", default="", help="허브 이름(없으면 사전의 hub)")
    sub.add_parser("status", help="마지막 상태와 줄 수")
    p = sub.add_parser("profile", help="전문가 · 도메인의 타입별 셈")
    p.add_argument("--domains", action="store_true", help="도메인 묶음만")
    args = ap.parse_args(argv)
    try:
        res = asyncio.run(_run(args))
    except (ValueError, OSError) as exc:
        log.error("%s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001 — 한 트랜잭션이라 쓴 것이 없다
        text = f"{type(exc).__name__}: {exc}"
        if any(t in text for t in ("record_mentions", "record_entities", "onto_terms", "onto_state")) and (
                "does not exist" in text or "no such table" in text):
            log.error("표가 없다 — 이행 0033 을 먼저 올린다(API 를 다시 띄우면 올라간다 — start_api.sh)")
            return 3
        log.exception("실패 — 아무것도 쓰지 않았다")
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
