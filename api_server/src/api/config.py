"""환경변수 기반 설정."""
import tempfile
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore" — Settings 모델에 정의되지 않은 .env 변수는 무시 (process env 엔
    # 살아남으므로 ``os.environ.get(...)`` 으로 읽는 코드는 영향 X). 예: EMBEDDING_PROVIDER,
    # SENTENCE_TRANSFORMER_MODEL 같은 embedder 측 변수.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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

    # ------------------------------------------------------ HWAX Portal SSO
    # 포털 launch 토큰(RS256) 검증용 JWKS URL. 빈 문자열이면 portal-callback
    # 엔드포인트가 404 를 반환한다 (standalone 배포는 영향 없음).
    portal_jwks_url: str = ""
    # 토큰 ``aud`` 클레임이 일치해야 하는 값. systems.yaml 의 audience 와 동일.
    portal_audience: str = "ai-data-hub"
    # SSO 성공 후 리다이렉트할 브라우저 경로 (포털 prefix 포함 — app 은 root_path 없음).
    portal_sso_landing: str = "/ai-data-hub/dashboard/"
    # 발급되는 SSO ApiKey 의 만료 일수.
    portal_sso_key_ttl_days: int = 30
    # 핸드오프 쿠키 Secure 플래그. 포털이 TLS 로 서빙하면 true.
    portal_cookie_secure: bool = False

    # --------------------------------------------------------------- metrics
    enable_metrics: bool = True

    # ------------------------------------------ org master Strict ingest 검증
    # records ingest 시 ``team`` 값이 ``org_teams`` 마스터에 등록돼 있어야 한다.
    # 비활성화 (false) 하면 미등록 team 도 ingest 허용 (마이그레이션 기간용).
    # ``group`` 은 lenient — 마스터 미존재 시 경고만 출력 (이 플래그와 무관).
    strict_team_validation: bool = True

    # 그림(figure) 바이너리 저장소. ``/figures`` 정적 마운트의 루트.
    # 환경변수 ``FIGURES_DIR`` 로 오버라이드 가능.
    figures_dir: Path = Path("figures")

    # 첨부(attachment) 바이너리 저장소. ``/attachments`` 정적 마운트의 루트.
    # 환경변수 ``ATTACHMENTS_DIR`` 로 오버라이드 가능. ``Path`` 타입이라
    # Windows / Linux 모두에서 분리자(`\\` vs ``/``) 가 자동 처리된다.
    attachments_dir: Path = Path("attachments")

    # ------------------------------------------------------- /api/convert
    # 업로드 가능한 최대 파일 크기 (MB). ``MAX_UPLOAD_MB`` 로 오버라이드.
    max_upload_mb: int = 50
    # 업로드 임시 저장소. ``UPLOAD_TEMP_DIR`` 로 오버라이드.
    upload_temp_dir: Path = Path(tempfile.gettempdir()) / "ai_data_uploads"

    # ------------------------------------------------------- /api/jobs/*
    # 레코드 INSERT/UPDATE 후 임베딩 잡을 자동 등록할지 여부.
    # ``AUTO_EMBED_ON_INSERT`` 환경변수로 토글한다 (기본 false).
    auto_embed_on_insert: bool = False
    # in-memory job 보관 TTL (초). 기본 1 시간.
    jobs_ttl_seconds: int = 3600
    # /api/jobs?kind= 응답의 최대 개수.
    jobs_list_limit: int = 100

    # ----------------------------------------------------------------- CORS
    # 추가 허용 오리진 (CSV). 예: "https://datahub.example.com,https://staging.x.io"
    # ``vscode-webview://*`` 는 별도 정규식으로 항상 허용된다.
    extra_allowed_origins: list[str] = []

    @field_validator("extra_allowed_origins", mode="before")
    @classmethod
    def _split_extra_origins(cls, v):
        """CSV 문자열 → list[str]. 비어 있으면 빈 리스트."""
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [piece.strip() for piece in v.split(",") if piece.strip()]
        if isinstance(v, list):
            return [str(item).strip() for item in v if str(item).strip()]
        return v


settings = Settings()
