"""SQLAlchemy 비동기 엔진 및 세션 (호환 재익스포트).

이 모듈은 하위 호환을 위해 유지되며, 실제 정의는 `api.db.base`에 있다.
신규 코드는 `from api.db import Base, engine, SessionLocal, get_session`을 사용할 것.
"""
from .db.base import Base, SessionLocal, engine, get_session

__all__ = ["Base", "SessionLocal", "engine", "get_session"]
