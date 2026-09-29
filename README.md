# Argus

개인정보처리시스템의 **접속기록을 수집 → 이상행위 탐지 → 소명 관리 → 점검 보고**하는 서비스.

- 근거: 「개인정보의 안전성 확보조치 기준」 §2 3호(접속기록 항목), §8①②③(보관·점검·무결성), §17①(자동화 분석·탐지·소명)
- 구성: 감시 대상 커머스 **플랫폼** + 접속기록 점검 서비스 **Argus** (두 시스템, 두 DB 완전 분리)

> ⚠️ **이 레포의 모든 개인정보·회원·취급자 데이터는 가상(Faker `ko_KR`)이다.** 실제 개인정보는 포함되어 있지 않다.

## 문서

| 문서 | 내용 |
|---|---|
| [CLAUDE.md](CLAUDE.md) | 작업 지침, 현재 단계(Walking Skeleton) |
| [docs/architecture.md](docs/architecture.md) | 시스템 구성, 접속기록 수집 구조, CI/CD |
| [docs/api-spec.md](docs/api-spec.md) | 플랫폼 → Argus 시스템 간 API |
| [docs/db-schema.md](docs/db-schema.md) | DDL, 룰 조건식, 탐지 배치 흐름 |
| [docs/policy.md](docs/policy.md) | 탐지 룰, 상태 전이, 마스킹, 파기 정책 |
| [docs/implementation-log.md](docs/implementation-log.md) | 구현 로그 |

## 로컬 실행 (Windows)

Python·Node는 호스트에 설치하지 않고 모두 컨테이너 안에서 실행한다.

### 준비물

- Git for Windows
- Docker Desktop (WSL 2 엔진) — Windows 기능의 **가상 머신 플랫폼**, **Linux용 Windows 하위 시스템**이 켜져 있어야 한다

### 실행

Git Bash 기준, 레포 루트에서:

```bash
# 1) 환경변수 파일 만들기 — change-me를 무작위 값으로 바꾼다 (.env는 커밋되지 않음)
cp .env.example .env
#    예: openssl rand -hex 24 로 생성한 값을 각 비밀번호·키 칸에 넣는다

# 2) 기동 — 이미지 빌드 → DB → 마이그레이션(argus-migrate) → API 순으로 뜬다
#    (.env의 COMPOSE_FILE이 infra/docker-compose.yml을 가리킨다)
docker compose up -d --build --wait

# 3) 상태 확인
docker compose ps
curl http://127.0.0.1:18000/healthz     # {"status":"ok","db":"ok"}
```

> 이미 `.env`가 있다면 `.env.example`과 비교해 새로 생긴 항목(`ARGUS_APP_DB_USER`, `ARGUS_APP_DB_PASSWORD`, `ARGUS_API_PORT`)을 추가한다.

| 서비스 | 호스트 접속 | 비고 |
|---|---|---|
| platform-db | `127.0.0.1:15432` | 플랫폼 DB (PostgreSQL 16) |
| argus-db | `127.0.0.1:15433` | Argus 접속기록 원장 (PostgreSQL 16) |
| argus-migrate | — | 기동 시 1회 실행: Alembic 마이그레이션 + API용 DB 계정 발급 후 종료 |
| argus-api | `127.0.0.1:18000` | 수집 API `POST /ingest/v1/access-logs`, `GET /healthz`, API 문서 `/docs` |

- 포트는 호스트 루프백(`127.0.0.1`)에만 열린다. 두 DB는 도커 네트워크도 분리되어 있어 플랫폼 쪽 컨테이너에서 argus-db에 접근할 수 없다.
- argus-api는 DB 소유자가 아닌 **앱 계정**(`ARGUS_APP_DB_USER`)으로 접속한다. 이 계정은 접속기록(`access_log`)에 조회·추가만 할 수 있고 수정·삭제는 할 수 없다(§8③).

### 테스트

Python을 호스트에 설치하지 않고 컨테이너 안에서 실행한다. 테스트는 argus-db에 임시 DB를 만들어 마이그레이션을 적용하고, 끝나면 지운다.

```bash
docker compose run --rm argus-api-test                       # pytest
docker compose run --rm argus-api-test ruff check --no-cache .   # lint
```

### 중지

```bash
# 중지 / 데이터까지 삭제
docker compose down
docker compose down -v
```
