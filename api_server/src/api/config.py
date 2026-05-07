"""환경변수 기반 설정."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_data"

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = True

    log_level: str = "INFO"
    # ``json`` (default) | ``text`` — JSON 은 stdlib JsonFormatter (외부 의존성 없음).
    log_format: str = "json"

    # ----------------------------------------------------------------- auth
    # AUTH_REQUIRED=false (default): 헤더 없으면 anonymous, 있으면 검증.
    # AUTH_REQUIRED=true            : 헤더 없거나 잘못되면 401.
    auth_required: bool = False
    # 첫 키 발급/관리용 부트스트랩 키 (constant-time 비교). 빈 문자열이면 비활성.
    bootstrap_api_key: str = ""

    # --------------------------------------------------------------- metrics
    enable_metrics: bool = True

    # 그림(figure) 바이너리 저장소. ``/figures`` 정적 마운트의 루트.
    # 환경변수 ``FIGURES_DIR`` 로 오버라이드 가능.
    figures_dir: Path = Path("figures")

    # 첨부(attachment) 바이너리 저장소. ``/attachments`` 정적 마운트의 루트.
    # 환경변수 ``ATTACHMENTS_DIR`` 로 오버라이드 가능. ``Path`` 타입이라
    # Windows / Linux 모두에서 분리자(`\\` vs ``/``) 가 자동 처리된다.
    attachments_dir: Path = Path("attachments")


settings = Settings()
