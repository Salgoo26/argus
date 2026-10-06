# db-gateway — DB 툴(DBeaver 등) ↔ platform-db 중계 + 2티어 접속기록 (architecture 3-4)
#   runtime (기본): 게이트웨이. docker build -t db-gateway apps/db-gateway
#   dev: 테스트·린트용 (dev 의존성 포함). docker build --target dev ...
# 보안 경계라 플랫폼 이미지와 분리 — 관리자 로그인 키·결제 암호화 키 같은 다른 시크릿과 섞이지 않게
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
# root가 아닌 전용 계정. 원문·버퍼 볼륨(/data)은 이 계정만 쓴다
RUN useradd --system --uid 10002 --no-create-home --shell /usr/sbin/nologin gateway \
    && mkdir /data && chown gateway:gateway /data && chmod 700 /data

COPY pyproject.toml ./
COPY app ./app


FROM base AS dev
RUN pip install ".[dev]"
COPY tests ./tests
USER gateway
CMD ["pytest", "-p", "no:cacheprovider"]


FROM base AS runtime
RUN pip install .
USER gateway
EXPOSE 6432
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 6432), timeout=2).close()"]
CMD ["python", "-m", "app.main"]
