# 정기 동기화 원천(sync_sources)을 이 박스에서 보고 · 끄고 · 켜는 명령 — 박스마다 따로인 행이라 이행으로 바꾸지 않는다
"""정기 동기화 원천을 이 박스에서 다룬다.

    python -m api.scripts.sync_sources list
    python -m api.scripts.sync_sources disable <이름> [<이름> …]
    python -m api.scripts.sync_sources enable  <이름> [<이름> …]

왜 명령인가. ``sync_sources`` 는 박스마다 따로 있는 행이다(Drive 조각 동기화가 나르지 않는다). 원천 앱이 같은 박스에 사는 dev 는 정기
동기화가 실제로 당기고, 운영 박스는 내부 포트 가드(``url_safety``)에 막혀 실패만 쌓일 수 있다. 그 박스에서만 끄려면 이행(모든 박스에
적용된다)이 아니라 그 박스에서 도는 명령이어야 한다. 끈 원천은 인앱 스케줄러가 건너뛰고 헬스의 ``sync_stale_sources`` 도 세지 않는다
(둘 다 ``enabled`` 를 본다). 손으로 한 번 돌리는 것(``trigger=manual``)은 꺼져 있어도 된다.

출력은 JSON 한 덩어리다. 비밀(``api_key``)과 주소(``base_url``)는 내지 않는다. 종료 코드: 0 끝 · 1 오류 · 2 인자 오류(없는 이름 —
아무것도 바꾸지 않았다) · 3 표가 없다.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any


class _UnknownNames(ValueError):
    def __init__(self, missing: list[str], have: list[str]):
        super().__init__(f"없는 원천 이름 {', '.join(missing)} — 이 박스에 있는 이름: {', '.join(have) or '(없음)'}")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    from sqlalchemy import select

    from ..db import base as db_base
    from ..db.models import SyncSource

    try:
        async with db_base.SessionLocal() as session:
            rows = list((await session.execute(select(SyncSource).order_by(SyncSource.id))).scalars())
            if args.cmd == "list":
                return {"sources": [{
                    "name": r.name, "enabled": bool(r.enabled), "schedule_cron": r.schedule_cron,
                    "last_status": r.last_status,
                    "last_sync_at": r.last_sync_at.isoformat() if r.last_sync_at else None,
                    "last_error": (r.last_error or "")[:200] or None,
                } for r in rows]}
            want = bool(args.cmd == "enable")
            by_name = {r.name: r for r in rows}
            names = list(dict.fromkeys(n.strip() for n in args.names if n.strip()))
            missing = [n for n in names if n not in by_name]
            if missing or not names:
                raise _UnknownNames(missing, sorted(by_name))
            changed, unchanged = [], []
            for n in names:
                if bool(by_name[n].enabled) == want:
                    unchanged.append(n)
                else:
                    by_name[n].enabled = want
                    changed.append(n)
            if changed:
                await session.commit()
            return {"action": args.cmd, "changed": changed, "unchanged": unchanged}
    finally:
        await db_base.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sync_sources", description="정기 동기화 원천을 이 박스에서 보고 · 끄고 · 켠다")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="원천과 켜짐 · 마지막 상태(읽기만)")
    for cmd, text in (("disable", "원천을 끈다 — 스케줄러가 건너뛰고 헬스가 낡음으로 세지 않는다"), ("enable", "원천을 다시 켠다")):
        p = sub.add_parser(cmd, help=text)
        p.add_argument("names", nargs="+", metavar="이름")
    args = ap.parse_args(argv)
    try:
        res = asyncio.run(_run(args))
    except _UnknownNames as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — 표가 없는 것과 그 밖을 가른다
        msg = str(exc)
        if "sync_sources" in msg and ("does not exist" in msg or "no such table" in msg):
            print("[ERROR] sync_sources 표가 없다 — API 를 한 번 띄워 이행을 올린 뒤 다시 부른다", file=sys.stderr)
            return 3
        print(f"[ERROR] {type(exc).__name__}: {msg[:300]}", file=sys.stderr)
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
