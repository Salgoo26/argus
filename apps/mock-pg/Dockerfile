# 가상 PG(결제대행) 서비스 — 플랫폼과 분리된 외부 수탁자 역할 (커머스 확장 F6)
#   runtime (기본): API·결제창 서버. docker build -t mock-pg apps/mock-pg
#   dev: 테스트·린트용 (dev 의존성 포함). docker build --target dev ...
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin mockpg

COPY pyproject.toml ./
COPY app ./app


FROM base AS dev
RUN pip install ".[dev]"
COPY tests ./tests
USER mockpg
CMD ["pytest", "-p", "no:cacheprovider"]


FROM base AS runtime
RUN pip install .
USER mockpg
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
