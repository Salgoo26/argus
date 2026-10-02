# argus-api / argus-worker 공용 이미지 (같은 이미지, 다른 실행 명령 — architecture 2절)
#   runtime (기본): API 서버. docker build -t argus-api apps/argus-api
#   dev: 테스트·린트용 (dev 의존성 포함). docker build --target dev ...
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
# 앱은 root가 아닌 전용 계정으로 실행 — 컨테이너 탈출 시 피해 범위 축소
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin argus

COPY pyproject.toml ./
COPY app ./app


FROM base AS dev
RUN pip install ".[dev]"
COPY alembic.ini ./
COPY migrations ./migrations
COPY tests ./tests
USER argus
CMD ["pytest", "-p", "no:cacheprovider"]


FROM base AS runtime
RUN pip install .
COPY alembic.ini ./
COPY migrations ./migrations
# 소명 첨부 저장소 — 앱 계정 소유로 만들어 둔다. 이름 있는 볼륨을 처음 붙이면 이 소유권이 복사된다
RUN mkdir -p /data/attachments && chown argus /data/attachments && chmod 700 /data/attachments
USER argus
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
