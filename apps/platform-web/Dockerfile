# platform-web — 플랫폼 관리자 화면 (Next.js, standalone 출력)
#   docker build -t platform-web apps/platform-web
FROM node:24-alpine AS base
# Next.js 원격 사용 정보 수집 끔 — 빌드·실행 정보가 외부로 나가지 않게
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /app


FROM base AS deps
COPY package.json package-lock.json ./
# lock 파일 그대로 설치 — 빌드마다 같은 버전 (pip의 버전 고정과 같은 역할)
RUN npm ci


FROM base AS build
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# /api/* 를 전달할 목적지 — 빌드 시점에 고정된다 (compose 내부 서비스 이름)
ARG PLATFORM_API_URL=http://platform-api:8000
ENV PLATFORM_API_URL=$PLATFORM_API_URL
RUN npm run build


FROM base AS runtime
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# 앱은 root가 아닌 전용 계정으로 실행 — 컨테이너 탈출 시 피해 범위 축소
RUN addgroup -S -g 10001 web && adduser -S -u 10001 -G web -H -s /sbin/nologin web
COPY --from=build --chown=web:web /app/.next/standalone ./
COPY --from=build --chown=web:web /app/.next/static ./.next/static
USER web
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/admin/login').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]
CMD ["node", "server.js"]
