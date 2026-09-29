# Argus — Claude Code 작업 지침

> 이 파일은 Claude Code가 이 레포에서 작업을 시작할 때 자동으로 읽는 지침이다.
> 기획·설계 원본은 claude.ai 프로젝트("정보보호 프로젝트")에 있고, `docs/`는 그 사본이다.
> 작성: 2026-09-23 (설계 단계 종료 시점) / 개정: 2026-09-29 — 6절 M1 완료 기준에 Trivy config·도커 빌드 확인 반영(architecture 8-5 개정 이월분), M2 범위에 ② 취급자 동기화 수신(Argus) 추가(구현 M1 설계 변경 #7), 7절에 시크릿 파일 확인 규칙 추가

---

## 0. 작업 전에 반드시 읽을 것

| 순서 | 파일 | 무엇을 얻나 |
|---|---|---|
| 1 | 이 파일 전체 | 규칙, 현재 단계, Walking Skeleton 범위와 완료 기준 |
| 2 | `docs/implementation-log.md` | 지난 세션에서 어디까지 했는지 |
| 3 | `docs/architecture.md` | 시스템 구성, 접속기록 수집 구조, 배포·CI |
| 4 | `docs/api-spec.md` | 플랫폼 → Argus 시스템 간 API 계약 |
| 5 | `docs/db-schema.md` | 전체 DDL, 룰 조건식 스펙, 탐지 배치 흐름 |
| 필요 시 | `docs/policy.md`, `docs/actor-flows.md`, `docs/requirements.md` | 정책(탐지 룰·상태 전이·마스킹·파기), 액터별 플로우, 요구사항 ID |

---

## 1. 프로젝트 요약

- **Argus**: 개인정보처리시스템의 **접속기록을 수집 → 이상행위 탐지 → 소명 관리 → 점검 보고**하는 서비스. 상용 접속기록관리 솔루션(INFOSAFER, UBI SAFER-PSM 등)과 같은 포지션.
- **플랫폼**: Argus가 감시할 대상인 간단한 커머스(무대장치). 고객 UI + 관리자(백오피스) UI.
- **목적**: 사용자(이선구)의 정보보호/개인정보보호 담당자 커리어 전환용 **포트폴리오**. 구현 후 이 시스템 자체에 대한 보안성 검토·ISMS-P 점검 실습까지 이어진다.
- **법적 근거의 중심**: 「개인정보의 안전성 확보조치 기준」 §2 3호(접속기록 5개 항목), §8①②③(보관·점검·무결성), §17①(자동화 분석 → 탐지 → 소명).
- **사용자 배경**: 백엔드 3년(Java/Spring 주력), Python은 경험 있음. 설명할 때 Spring의 대응 개념(Filter↔ASGI 미들웨어, ThreadLocal↔contextvars, JPA↔SQLAlchemy, Flyway↔Alembic)을 곁들이면 이해가 빠르다.

---

## 2. 시스템 구성

```
[platform-web]──▶[platform-api]──(Agent 미들웨어)──▶[outbox]──▶[platform-relay]──HMAC──▶[argus-api 수집 API]
      Next.js        FastAPI              │                         FastAPI 같은 이미지        │
                        │                 ▼                                                    ▼
                   [platform-db]    관리자 라우트만 로깅                                  [argus-db] 접속기록 원장
                                                                                              ▲
                                             [argus-web]──▶[argus-api]      [argus-worker] 탐지·파기 배치
```

- **두 시스템, 두 DB 완전 분리.** Argus는 플랫폼 DB에 접근하지 않는다. 연결은 ①수집 API ②취급자 동기화 API뿐.
- **모노레포 구조**

```
/apps
  /platform-web    Next.js (App Router, TypeScript) — 고객 UI + 관리자 UI
  /platform-api    FastAPI — 업무 API + 접속기록 Agent(미들웨어) + outbox relay(별도 프로세스)
  /argus-web       Next.js — 정보보호 담당자(A4) / 취급자(A5) UI
  /argus-api       FastAPI — 수집·동기화·탐지·소명·보고 API + worker(탐지/파기 배치, 별도 프로세스)
/infra             docker-compose.yml, (나중에) docker-compose.prod.yml, Caddyfile
/.github           workflows, dependabot.yml
/docs              설계 문서 사본 + 구현 로그
```

- 로컬 컨테이너(Skeleton 기준): `platform-db`, `argus-db`(PostgreSQL 16 각각), `platform-api`, `platform-relay`, `argus-api`, `argus-worker`, `platform-web`, `argus-web`. Caddy·backup은 첫 배포 때 추가.

---

## 3. 절대 규칙 (보안·개인정보) — 어기면 안 된다

이 프로젝트는 "보안을 아는 사람이 만든 시스템"을 보여주는 게 목적이다. 아래 규칙 위반은 기능 버그보다 심각하게 취급한다.

1. **시크릿 커밋 금지.** DB 비밀번호, HMAC 키, 암호화 키, 토큰은 `.env`에만. 레포에는 `.env.example`(더미 값)만 둔다. `.gitignore`에 `.env*`(단 `.env.example` 제외) 포함.
2. **실제 개인정보 금지.** 시드 데이터는 100% 가상(Faker `ko_KR`). README에 "모든 데이터는 가상" 명시.
3. **플랫폼 → Argus payload에 원본 개인정보 금지.** 정보주체는 **회원 내부 PK만** 보낸다. 이름·이메일·전화·카드번호 절대 금지. 검색 조건은 **키 이름만**(값 금지).
4. **로깅은 관리자 라우트에만.** 고객(정보주체) 행위는 접속기록 대상이 아니다(§8① 단서). 고객 라우트에 Agent를 걸지 않는다.
5. **`access_log`는 append-only.** UPDATE 금지(트리거 + DB 권한). 모든 INSERT는 **해시체인을 계산하는 단일 append 함수**를 거친다(advisory lock으로 직렬화). **시드 데이터도 수집 API를 통해** 넣는다 — DB 직접 INSERT 금지.
6. **outbox 적재는 업무 트랜잭션과 별도 트랜잭션.** 업무가 실패해도 시도 기록은 남고 `result=FAILURE`로 기록한다.
7. **접속기록은 스스로 버리지 않는다.** 전송 실패(5xx·네트워크·401)는 `PENDING` 유지 + 재시도/알림. `DEAD`는 필드 검증 실패(`rejected`)와 파싱 불가(400)만.
8. **식별값 마스킹.** Argus API 응답의 정보주체 식별값은 기본 마스킹(`member_10***`). 언마스킹은 OFFICER만, 사유 필수, `UNMASK`로 Argus 자체 접속기록에 남긴다. HANDLER(A5)는 해제 불가, **본인 탐지건만** 조회.
9. **HMAC 서명 검증은 상수 시간 비교**(`hmac.compare_digest`). timestamp ±300초.
10. **client_ip는 신뢰하는 프록시가 넘긴 헤더만 신뢰.** 임의의 `X-Forwarded-For`를 그대로 믿지 않는다(설정으로 trusted proxy 지정).
11. **비밀번호는 argon2id**, 결제수단(카드·계좌)은 앱 레벨 양방향 암호화(키는 `.env`, DB와 분리). 결제는 Skeleton 범위 밖이지만 나중에 이 원칙대로.

---

## 4. 설계와 다르게 해야 할 때

- `docs/`의 설계와 **충돌하거나, 설계에 없는 결정**이 필요하면 **멈추고 사용자에게 먼저 설명**한다(무엇이 문제인지, 선택지, 추천). 동의를 받은 뒤 진행한다.
- 진행한 설계 변경은 `docs/implementation-log.md`의 **"설계 변경"** 항목에 반드시 기록한다(무엇을, 왜, 영향받는 문서).
- `docs/`의 설계 문서 자체는 **직접 수정하지 않는다.** 원본은 claude.ai 프로젝트에 있고, 사용자가 Cowork "구현" 방에서 구현 로그를 보고 원본을 갱신한 뒤 사본을 다시 내려받는다.
- 사소한 구현 세부(변수명, 파일 구조 등)는 묻지 않고 진행해도 된다. 기준: **다른 설계 문서나 정책에 영향을 주는가?**

---

## 5. 기술 스택과 컨벤션

| 영역 | 선택 |
|---|---|
| Python | 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2, pytest, ruff |
| 배치 | worker/relay는 API와 **같은 이미지, 다른 실행 명령**의 별도 컨테이너 (API 프로세스가 여러 개여도 배치가 중복 실행되지 않도록) |
| Frontend | Next.js(App Router) + TypeScript, npm. Skeleton에서는 디자인 없이 표·버튼 수준 |
| DB | PostgreSQL 16, 플랫폼·Argus **별도 컨테이너**. 스키마는 Alembic 마이그레이션으로 관리(DDL 원본은 `docs/db-schema.md`) |
| 인증 | 플랫폼 관리자·Argus 사용자 모두 세션 또는 JWT 중 단순한 쪽. 로그인 실패 횟수 제한(계정 잠금)은 앱 레벨에서 |
| 개발 환경 | **Windows + Docker Desktop.** Python/Node는 호스트에 설치하지 않고 컨테이너 안에서 실행. `.gitattributes`로 줄바꿈 LF 고정 |
| Git | main + feature 브랜치, **혼자여도 PR로 병합**(변경관리 증적). Conventional Commits(`feat:`, `fix:`, `docs:`, `test:`, `chore:`) |
| 주석 | 법적 근거가 있는 코드에는 조항을 짧게 표기. 예: `# §8③ 위·변조 방지 — append-only` |

---

## 6. 현재 단계: Walking Skeleton

**정의**: 모든 주요 부품을 가장 얇게 한 번씩 관통하는, 끝에서 끝까지 실제로 동작하는 최소 구현. 기능은 최소지만 두 시스템이 실제로 연결돼 돌아가야 한다.

**관통할 시나리오** (액터별 플로우 F-03 → F-04 → F-05 → F-06 → F-05):

```
플랫폼 관리자(ops_park) 로그인 → 회원 목록 120건 CSV 다운로드
 → Agent가 접속기록 생성 → outbox → relay → Argus 수집 API → 원장 저장(해시체인)
 → 탐지 배치: "대량 다운로드" 룰(subject_count ≥ 50) → 탐지건 생성(DETECTED)
 → 정보보호 담당자(officer) 로그인 → 탐지건 확인(마스킹 상태) → 소명 요청(REQUESTED)
 → 취급자(ops_park) Argus 로그인 → 본인 탐지건 확인 → 소명 제출(SUBMITTED)
 → 담당자 승인(APPROVED, 종결)
```

**범위 안**: `docs/db-schema.md`의 **[S] 테이블**(Argus 11개 — `setting` 포함 / 플랫폼 3개: `operator`, `member`, `outbox`), 룰 1개(대량 다운로드, 시드), 수집 API·취급자 동기화 API·헬스체크, 상태 전이 전체(요청→제출→반려→재요청→승인), Argus 자체 접속기록(LOGIN·READ), 식별값 마스킹 표시.

**범위 밖 (Skeleton 이후 기능 레이어에서)**: 룰 빌더 UI, 나머지 룰 6개, AGGREGATE 룰, 첨부파일, 언마스킹 기능, 보고서, 알림(이메일), 파기 배치, 화이트리스트, 결제·주문·문의, 고객 UI, Caddy, 배포.

### 마일스톤과 완료 기준

| # | 마일스톤 | 완료 기준 |
|---|---|---|
| **M0** | 레포·기반 | `gh`로 **공개** 레포 생성, `.gitignore`/`.gitattributes`/`.env.example`, `infra/docker-compose.yml`로 DB 2개 기동, CI(ruff + pytest + gitleaks), `dependabot.yml`. CodeQL·push protection·secret scanning은 GitHub 설정에서 켜야 하므로 **사용자에게 켜는 방법을 안내** |
| **M1** | Argus 수집 | [S] 테이블 Alembic 마이그레이션, `POST /ingest/v1/access-logs`(HMAC 검증·건별 판정·멱등), 해시체인 append 함수, append-only 트리거·DB 권한, `GET /healthz`, argus-api Dockerfile, **CI에 Trivy config(report-only) + 도커 빌드 확인 job**(architecture 8-5). 테스트: 서명 불일치 401, 중복 event_id → duplicate, 필드 오류 → rejected, 해시체인 연속성 |
| **M2** | 플랫폼 Agent | **② 취급자 동기화 수신(Argus: `POST /ingest/v1/handler-events`, HMAC·응답 형식은 M1 재사용)** → `operator`·`member` + 시드(회원 500명, 취급자 3~5명), 관리자 로그인, `GET /admin/members/export`(CSV), **접속기록 미들웨어 + 데코레이터**(contextvars), outbox 적재(별도 트랜잭션), relay(배치 100건·지수 백오프·401은 PENDING 유지), 취급자 동기화 API(②) 호출. 테스트: 고객 라우트는 로깅 안 됨, 업무 실패 시에도 FAILURE로 기록 |
| **M3** | 탐지 배치 | argus-worker가 `setting.detection_interval_min` 주기로 실행, `id` 커서, EVENT 룰 평가, 진행 중 탐지건에만 하위 로그 추가(부분 유니크 인덱스), `log_summary`·`rule_snapshot`, 상태 이력 기록 |
| **M4** | 소명 API | Argus 로그인(OFFICER/HANDLER), 탐지건 목록·상세(마스킹), 소명 요청·제출·승인·반려·재요청(round+1)·DISMISS, **허용되지 않은 상태 전이는 거부**, HANDLER는 본인 건만, Argus 자체 접속기록(LOGIN·READ, READ는 건수만) |
| **M5** | 최소 화면 | platform-web: 관리자 로그인·회원 목록·다운로드 버튼 / argus-web: 로그인·탐지 목록·상세·요청/제출/승인 버튼 |
| **M6** | E2E 검증 | 위 시나리오를 자동 테스트 스크립트로 재현, 해시체인 검증 스크립트, README에 로컬 실행법(Windows 기준) |

**Skeleton 전체 완료 기준**: 새로 clone한 상태에서 README대로 `docker compose up` → 시나리오를 화면으로 끝까지 수행할 수 있고, M6 테스트가 CI에서 통과한다.

---

## 7. 작업 방식

- **마일스톤 단위로 진행.** 시작할 때 무엇을 만들지 짧게 계획을 공유하고, 끝나면 테스트 결과와 함께 요약한다.
- 한 번에 너무 많이 만들지 않는다. 작게 만들고 → 실행해보고 → 다음으로.
- 사용자가 코드를 이해할 수 있게, **왜 이렇게 짰는지**를 짧게 설명한다. 특히 보안·개인정보 관련 코드는 어떤 조항·정책에 대응하는지 함께.
- 사용자가 직접 해야 하는 일(GitHub 로그인, 설정 토글, Docker 실행 등)은 명확히 단계별로 안내한다. 사용자의 계정 인증 정보를 대신 입력하거나 요구하지 않는다.
- **`.env` 등 시크릿 파일은 값을 전부 가린 형태로만 확인한다**(예: `sed -E 's/=.+/=<set>/' .env`). 키 이름을 골라 가리는 필터는 누락이 생긴다 — 2026-09-29 M1에서 HMAC 키가 작업 대화에 노출되어 교체한 사고의 재발 방지.

---

## 8. 기록 규칙

- **세션을 마칠 때마다** `docs/implementation-log.md`에 날짜별로 추가한다: 한 일 / 결정사항 / 설계 변경(있으면) / 미결·이슈 / 다음 할 일.
- **마일스톤을 완료하면** 사용자에게 "Cowork 구현 방에서 진행기록 동기화"를 한 줄로 리마인드한다.
- 기록은 나중에 포트폴리오·면접 근거가 된다. **기각한 대안과 그 이유**도 남긴다.

---

## 9. 자주 참조할 설계 수치

| 항목 | 값 | 출처 |
|---|---|---|
| 수집 API 배치 크기 | 최대 100건 / 1MB | api-spec 1-1 |
| `subject.ids` 최대 | 1,000개 (초과 시 `truncated=true`, `count`는 전체) | api-spec 2-2 |
| HMAC timestamp 허용 오차 | ±300초 | api-spec 1-2 |
| 미래 시각 거부 | 수신 시각 기준 +5분 초과 | api-spec 2-5 |
| relay 재시도 | 지수 백오프 1분 → 최대 1시간, 상한 없음 | api-spec 1-5 |
| 대량 다운로드 룰 | `action=DOWNLOAD ∧ subject_count ≥ 50`, HIGH | db-schema 3-6 |
| 탐지 배치 주기 | 5분 (`setting.detection_interval_min`) | db-schema 3-5 |
| 탐지 그룹 키 | `(rule, source, actor, occurred_at 날짜)` | policy 2-2 |
| 접속기록 보관 | 1년 | policy 5-1 |
| 식별값 표기 | 저장 `"10293"` / 표시 `member_10293` / 마스킹 `member_10***` | db-schema 2-1 |
