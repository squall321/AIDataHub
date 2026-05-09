# ===========================================================================
# AI Data Hub API — deploy 이미지
#
# Build context : 프로젝트 루트 (d:/Personal/AI_data)
# Dockerfile 경로: deploy/Dockerfile
#
# 호출 예 (compose 가 자동으로):
#   docker build -f deploy/Dockerfile -t aidh-api ..
# ===========================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 시스템 의존성 — lxml/Pillow/asyncpg/pdfplumber 등 빌드 시 필요한 헤더와
# 런타임용 curl (헬스체크) 만 설치한다.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev libxslt-dev \
        libjpeg-dev zlib1g-dev \
        libpq-dev \
        curl \
        ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# requirements 만 먼저 복사해 layer cache 활용
COPY api_server/requirements.txt /app/api_server/requirements.txt
RUN pip install --upgrade pip \
 && pip install -r /app/api_server/requirements.txt

# 앱 소스 복사 (api_server 전체 + deploy 스크립트)
COPY api_server /app/api_server
COPY deploy /app/deploy

# entrypoint 실행권한
RUN chmod +x /app/deploy/entrypoint.sh

ENV PYTHONPATH=/app/api_server/src
WORKDIR /app/api_server

EXPOSE 8000

# compose 가 command 로 동일 entrypoint 를 지정하지만, 단독 실행 대비 기본값
CMD ["bash", "/app/deploy/entrypoint.sh"]
