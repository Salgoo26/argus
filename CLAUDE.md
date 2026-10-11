# Argus — Claude Code 작업 지침

> 이 파일은 Claude Code가 이 레포에서 작업을 시작할 때 자동으로 읽는 지침이다.
> 기획·설계 문서는 `docs/`에 있다.
> 작성 2026-09-23 · 최종 개정 2026-10-10

---

## 0. 작업 전에 반드시 읽을 것

| 순서 | 파일 | 무엇을 얻나 |
|---|---|---|
| 1 | 이 파일 전체 | 규칙, 구현 단계와 범위, 완료 기준 |
| 2 | `_notes/implementation-log.md` | 지난 세션에서 어디까지 했는지(레포 루트, git 제외 — 8절) |
| 3 | `docs/architecture.md` | 시스템 구성, 접속기록 수집 구조, 배포·CI |
| 4 | `docs/api-spec.md` | 플랫폼 → Argus 시스템 간 API 계약 |
| 5 | `docs/db-schema.md` | 전체 DDL, 룰 조건식 스펙, 탐지 배치 흐름 |
| 필요 시 | `docs/policy.md`, `docs/actor-flows.md`, `docs/requirements.md` | 정책(탐지 룰·상태 전이·마스킹·파기), 액터별 플로우, 요구사항 ID |

---

## 1. 프로젝트 요약

- **Argus**: 개인정보처리시스템의 **접속기록을 수집 → 이상행위 탐지 → 소명 관리 → 점검 보고**하는 서비스. 상용 접속기록관리 솔루션(INFOSAFER, UBI SAFER-PSM 등)과 같은 포지션.
- **플랫폼**: Argus가 감시할 대상인 간단한 커머스(감시 대상 시스템, 최소 구현). 고객 UI + 관리자(백오피스) UI.
- **이후 단계**: 구현 후 이 시스템 자체에 대한 보안성 검토와 ISMS-P 기준 점검으로 이어진다.
- **법적 근거의 중심**: 「개인정보의 안전성 확보조치 기준」 §2 3호(접속기록 5개 항목), §8①②③(보관·점검·무결성), §17①(자동화 분석 → 탐지 → 소명).
- **설명 방식**: 사용자는 Java/Spring 백엔드에 익숙하다. 설명할 때 Spring의 대응 개념(Filter↔ASGI 미들웨어, ThreadLocal↔contextvars, JPA↔SQLAlchemy, Flyway↔Alembic)을 곁들이면 이해가 빠르다.

---

## 2. 시스템 구성

```
[platform-web]──▶[platform-api]──(Agent 미들웨어)──▶[outbox]──▶[platform-relay]──HMAC──▶[argus-api 수집 API]
      Next.js        FastAPI              │                         FastAPI 같은 이미지        │
                        │                 ▼                                                    ▼
                   [platform-db]    관리자 라우트만 로깅                                  [argus-db] 접속기록 원장
                                                                                              ▲
                                             [argus-web]──▶[argus-api]      [argus-worker] 탐지·기한 순찰
```

- **두 시스템, 두 DB 완전 분리.** Argus는 플랫폼 DB에 접근하지 않는다. 연결은 ①수집 API ②취급자 동기화 API ③보호 대상 등록부 동기화(게이트웨이가 DB 구조 목록을 보내고 등록부를 받는다 — 게이트웨이가 시작하는 요청뿐)이다. Argus에서 밖으로 나가는 통신은 웹 푸시 발송뿐이다(`docs/architecture.md` 2-2).
- **모노레포 구조**

```
/apps
  /platform-web    Next.js (App Router, TypeScript) — 고객 UI + 관리자 UI
  /platform-api    FastAPI — 업무 API + 접속기록 Agent(미들웨어) + outbox relay(별도 프로세스)
  /db-gateway      Python(asyncio) — DB 접근 게이트웨이(2티어): DB 툴 ↔ platform-db 중계, 원문 저장, 자체 버퍼 → Argus 수집 API
  /argus-web       Next.js — 정보보호 담당자(A4) / 취급자(A5) UI, 웹 푸시 서비스 워커(public/sw.js)
  /argus-api       FastAPI — 수집·동기화·보호 대상 등록부·탐지·소명·알림·보고 API + worker(탐지·소명 기한 순찰·웹 푸시 발송, 별도 프로세스)
/infra             docker-compose.yml, docker-compose.e2e.yml, (첫 배포 때) docker-compose.prod.yml, Caddyfile
/.github           workflows, dependabot.yml
/e2e               E2E 시나리오 (Skeleton 시나리오 등, CI e2e job)
/scripts           실행 보조 스크립트 (`e2e.sh`, `init-env.sh`)
/docs              설계 문서
/_notes            구현 로그(implementation-log.md) — .gitignore로 제외, 로컬에만
```

- 로컬 컨테이너(Skeleton 기준): `platform-db`, `argus-db`(PostgreSQL 16 각각), `platform-api`, `platform-relay`, `argus-api`, `argus-worker`, `platform-web`, `argus-web`. Caddy·backup은 첫 배포 때 추가.
- **기능 레이어 8(2티어)에서 추가**: `db-gateway`(DB 접근 게이트웨이, `/apps/db-gateway`) + `gateway-data` 볼륨(원문 저장 + **게이트웨이 자체 전송 버퍼** + 마지막으로 받은 보호 대상 등록부 `registry.json`, 게이트웨이만 마운트 — 플랫폼 outbox를 쓰지 않는다, 출처는 `PLATFORM` HMAC 키). DB 툴은 게이트웨이로만 접속하고 **platform-db의 호스트 포트는 닫는다** — `docs/architecture.md` 3-4.

---

## 3. 절대 규칙 (보안·개인정보) — 어기면 안 된다

Argus는 개인정보 처리를 감시하는 시스템이다. 아래 규칙 위반은 기능 버그보다 심각하게 취급한다.

1. **시크릿 커밋 금지.** DB 비밀번호, HMAC 키, 암호화 키, 토큰, 웹 푸시 VAPID 개인키(`ARGUS_VAPID_PRIVATE_KEY`)는 `.env`에만. 레포에는 `.env.example`(더미 값)만 둔다. `.gitignore`에 `.env*`(단 `.env.example` 제외) 포함.
2. **실제 개인정보 금지.** 시드 데이터는 100% 가상(Faker `ko_KR`). 가상 데이터 고지는 README에 "모든 데이터는 가상"으로 둔다 — 화면에는 가상·시연 안내 띠를 두지 않는다. 예외는 결제창의 "가상 결제 — 실제 카드번호 입력 금지"(실제 카드번호 입력을 막는 장치, 6절 7-4).
3. **플랫폼 → Argus payload에 원본 개인정보 금지.** 정보주체는 **회원 내부 PK만** 보낸다. 이름·이메일·전화·카드번호 절대 금지. 검색 조건은 **키 이름만**(값 금지). **게이트웨이(2티어)도 SQL 원문·매개변수는 보내지 않는다** — 정규화 SQL(리터럴 → `$1`)과 원문 참조·지문만, 원문은 게이트웨이 저장소에만. 게이트웨이가 보내는 DB 구조 목록도 테이블·컬럼 이름과 자료형만(값·예시 금지). **웹 푸시 페이로드**는 알림 종류·심각도·탐지건 번호만 — 회원번호·취급자 이름·룰 상세 금지.
4. **로깅은 관리자 라우트에만.** 고객(정보주체) 행위는 접속기록 대상이 아니다(§8① 단서). 고객 라우트에 Agent를 걸지 않는다.
5. **`access_log`는 append-only.** UPDATE·DELETE·TRUNCATE 금지(소유자까지 막는 트리거 — 마이그레이션 0002·0017, 앱 계정 DB 권한). 모든 INSERT는 **해시체인을 계산하는 단일 append 함수**를 거친다(advisory lock으로 직렬화). **시드 데이터도 수집 API를 통해** 넣는다 — DB 직접 INSERT 금지.
6. **outbox 적재는 업무 트랜잭션과 별도 트랜잭션.** 업무가 실패해도 시도 기록은 남고 `result=FAILURE`로 기록한다.
7. **접속기록은 스스로 버리지 않는다.** 전송 실패(5xx·네트워크·401)는 `PENDING` 유지 + 재시도/알림. `DEAD`는 필드 검증 실패(`rejected`)와 파싱 불가(400)만.
8. **식별값 마스킹.** Argus API 응답의 정보주체 식별값은 기본 마스킹(`member_10***`). 언마스킹(v0.2)은 OFFICER만, 사유 필수, `UNMASK`로 Argus 자체 접속기록에 남긴다. HANDLER(A5)는 해제 불가, **본인 탐지건만** 조회.
9. **HMAC 서명 검증은 상수 시간 비교**(`hmac.compare_digest`). timestamp ±300초.
10. **client_ip는 신뢰하는 프록시가 넘긴 헤더만 신뢰.** 임의의 `X-Forwarded-For`를 그대로 믿지 않는다(설정으로 trusted proxy 지정).
11. **비밀번호는 argon2id**, 결제수단(카드·계좌)은 앱 레벨 양방향 암호화(키는 `.env`, DB와 분리). 카드번호는 서버로 받지 않고(가상 결제창 입력만), 저장하는 환불계좌는 이 원칙대로 암호화한다.

---

## 4. 설계와 다르게 해야 할 때

- `docs/`의 설계와 **충돌하거나, 설계에 없는 결정**이 필요하면 **멈추고 사용자에게 먼저 설명**한다(무엇이 문제인지, 선택지, 추천). 동의를 받은 뒤 진행한다.
- 진행한 설계 변경은 `_notes/implementation-log.md`의 **"설계 변경"** 항목에 반드시 기록한다(무엇을, 왜, 영향받는 문서).
- `docs/`의 설계 문서 자체는 **직접 수정하지 않는다.** 설계 변경은 구현 로그에 기록하고, 문서 개정은 사용자가 따로 반영한다.
- 사소한 구현 세부(변수명, 파일 구조 등)는 묻지 않고 진행해도 된다. 기준: **다른 설계 문서나 정책에 영향을 주는가?**

---

## 5. 기술 스택과 컨벤션

| 영역 | 선택 |
|---|---|
| Python | 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2, pytest, ruff |
| 배치 | worker/relay는 API와 **같은 이미지, 다른 실행 명령**의 별도 컨테이너 (API 프로세스가 여러 개여도 배치가 중복 실행되지 않도록) |
| Frontend | Next.js(App Router) + TypeScript, npm. **v0.1부터 기본 디자인 포함**: CSS 한 장, 외부 글꼴·CDN 미사용, 강조색으로 두 시스템 구분. 브라우저 ↔ API는 Next.js rewrite(같은 출처). 화면 서버는 신뢰 프록시가 아님(`docs/architecture.md` 3-2) |
| DB | PostgreSQL 16, 플랫폼·Argus **별도 컨테이너**. 스키마는 Alembic 마이그레이션으로 관리(DDL 원본은 `docs/db-schema.md`) |
| 인증 | **JWT(HS256) `HttpOnly`·`SameSite=Strict` 쿠키, 30분 미사용 시 만료(요청마다 재발급), 매 요청 계정 상태 재확인** — 플랫폼 관리자 확정(M2), Argus 사용자(M4)도 같은 방식. 로그인 5회 연속 실패 시 잠금(앱 레벨). 상세는 `docs/policy.md` 4-3 |
| 개발 환경 | **Windows + Docker Desktop.** Python/Node는 호스트에 설치하지 않고 컨테이너 안에서 실행. `.gitattributes`로 줄바꿈 LF 고정 |
| Git | main + feature 브랜치, **혼자여도 PR로 병합**(변경관리 증적). Conventional Commits(`feat:`, `fix:`, `docs:`, `test:`, `chore:`) |
| 주석 | 법적 근거가 있는 코드에는 조항을 짧게 표기. 예: `# §8③ 위·변조 방지 — append-only` |

---

## 6. 구현 단계: Walking Skeleton → v0.1

**정의**: 모든 주요 부품을 가장 얇게 한 번씩 관통하는, 끝에서 끝까지 실제로 동작하는 최소 구현. 기능은 최소지만 두 시스템이 실제로 연결돼 돌아가야 한다.

**관통할 시나리오** (액터별 플로우 F-03 → F-04 → F-05 → F-06 → F-05):

```
플랫폼 관리자(ops_park) 로그인 → 회원 목록 120건 CSV 다운로드
 → Agent가 접속기록 생성 → outbox → relay → Argus 수집 API → 원장 저장(해시체인)
 → 탐지 배치: "대량 다운로드" 룰(subject_count ≥ 50) → 탐지건 생성 + **자동 소명 요청(REQUESTED, 요청자 = 시스템)**
 → 정보보호 담당자(officer) 로그인 → 탐지건 확인(마스킹 상태) → (오탐이면 요청 취소 → DISMISSED)
 → 취급자(ops_park) Argus 로그인 → 본인 탐지건 확인 → 소명 제출(SUBMITTED)
 → 담당자 반려(REJECTED) → 재요청(REQUESTED, round 2) → 재제출 → 승인(APPROVED, 종결)
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

### Skeleton 이후: v0.1 범위

첫 공개 버전(v0.1)은 **완벽한 완성보다 보여줄 수 있는 상태를 먼저** 만들고, 이후 버전업한다.

| 구분 | 항목 |
|---|---|
| **Must** | Walking Skeleton 완성(M3~M6), **기본 디자인**(M5에서 적용), README(구현 현황·실행법), 시연 자료(시나리오 GIF 또는 스크린샷) |
| **기능 레이어 — 이 순서대로, 앞 항목이 끝나야 다음으로** (Skeleton 완료 후) | 아래 "기능 레이어 순서" 표 |
| **v0.2 이후** | 파기 배치, 배포(Caddy·CD·백업), 이메일 알림, **언마스킹(LOG-10 해제)**, **화이트리스트(LOG-12)**, 기능 동결까지 끝나지 못한 기능 레이어 항목, **⭐ 3티어 접속기록 버퍼를 플랫폼 DB `outbox` → platform-api 전용 볼륨으로 이전(v0.2 필수, `docs/architecture.md` 8-6 — v0.1에서는 손대지 않는다)**, **2티어 사고 조사(재현) 체계**(미특정 건 축소 — 시점 복구 백업·재현 도구·백업 보관 기간 ↔ 파기 정책·원문 저장소 암호화, `docs/architecture.md` 3-4·10절), 보호 대상·Argus 계정 관리 화면 노출(서버 기능은 v0.1에 있음), 컬럼 단위 판정·개인정보 자동 식별, 화면 경유 점검 대상 대조(`docs/architecture.md` 6절) |

**기능 레이어 순서** — 기능 동결까지 **끝난 것만** v0.1에 넣고, 남은 항목은 README "구현 현황표"에 설계 완료로 표시한다.

| 순서 | 항목 | 참고 |
|---|---|---|
| 1 | ✅ EVENT 룰 3개: 야간·주말·퇴직자 계정 접속 | LOG-03, `docs/policy.md` 1-3 (시각·요일 KST, 명부 미등록 계정은 탐지 — fail-closed) |
| 2 | ⏸ **보류(v0.2 이후)** — 식별값 언마스킹 | LOG-10, `docs/policy.md` 6절: Argus는 회원번호만 보유해 해제 가치가 낮음. 표시제한은 보안성 검토 후 **플랫폼**에 조치 |
| 3 | ✅ 접속기록 조회·검색 화면 | LOG-02 (검색 조건은 POST 본문, 키 이름만 기록, 출처 필터로 Argus 자체 기록 점검) |
| 4 | ✅ AGGREGATE 룰 2개 + 기준선 시드(수집 API 경유) | LOG-03, `docs/db-schema.md` 3-6·3-7 #4 (`min_baseline`, 윈도우 1회 판단) |
| 5 | **해체** — 룰 변경 이력·심각도 색 → 6에 포함 / 화이트리스트 → v0.2 | LOG-11~13 |
| 6 | ✅ 룰 빌더 UI + 룰 변경 이력 + 심각도 색 | LOG-04·11·13 (삭제 없음 — 끄기·필터, 사유 입력 없음) |
| 7 | ✅ **플랫폼(감시 대상 시스템) 최소 구현** — 아래 순서, PR은 각각 따로 (PR #29~#34) | |
| 7-0 | ✅ 고객 화면 최소판: 관리자 화면 `/admin` 이동(PR A) → 회원가입(필수·선택 동의 분리 — 이용약관·개인정보 수집·이용·만 14세 이상 확인은 필수, 마케팅은 선택. 항목마다 목적·항목·보유 기간 고지)·로그인·마이페이지(열람·정정·비밀번호 변경·선택 동의 철회·탈퇴 즉시 파기)·처리방침/약관 페이지(PR B) | PLT-01~03, `docs/policy.md` 4-3 "고객 인증"(audience 분리, 5회 → 15분 잠금), `docs/db-schema.md` 4-1. 처리방침·약관 문안은 코드 밖에서 작성해 반영한다(버전·시행일을 올려 새 행으로 — 기존 행을 고쳐 쓰지 않는다) |
| 7-1 | ✅ 주문·결제: 카드는 PG 목업(카드번호·끝 4자리 미저장, PG 거래 정보만), 환불계좌 AES-GCM 암호화 + 관리자 "전체 보기" → 결제수단 조회 룰 | PLT-04·05·16, `docs/db-schema.md` 4절, 절대 규칙 #11. 법정 보존 분리보관도 여기서 |
| 7-2 | ✅ 1:1 문의: 고객 → CS 문의, 문의에 회원번호 — CS 조회의 업무 근거 | PLT-06·17 |
| 7-4 | ✅ **플랫폼 보강** (기능 동결 전 마지막 항목, PR #54~#58). PR 3개: ① 회원가입·마이페이지 정보(휴대폰 필수, 생년월일·성별 미수집, 이메일 외 정정) ② 배송지(마이페이지 관리, 주문 시 선택·스냅샷) ③ 결제·주문(주문 로그인 필수, **PG 목업 결제창에서 카드번호 직접 입력 — 형식 확인 없이 입력만, platform-api로 보내지 않음**, 카드 저장·관리 없음, 서버 로그·DB·Argus 어디에도 카드번호가 없음을 테스트로 확인, 결제창에 "가상 결제 — 실제 카드번호 입력 금지" 안내). 범위 밖: 회원 상세의 배송지 마스킹·전체 보기(v0.2) | PLT-01·03·04·05, `docs/db-schema.md` 4절·4-1, `docs/architecture.md` 3-4 "데이터 유형"(새 테이블·컬럼은 **보호 대상 등록부**에 분류한다 — 분류 전에는 "미분류"로 드러나고 DB 직접 접근은 `NONE`으로 기록돼 탐지에서 빠진다), 관리자 주문 상세의 배송 정보 = 주문 조회 접속기록 |
| 7-3 | ✅ 소명 근거자료 첨부: PNG·JPG·PDF, 파일당 5MB, 차수당 3개, Argus 전용 볼륨, 파일 이름 불신, SHA-256, 다운로드는 담당자·본인만 + **다운로드도 Argus 자체 접속기록** | LOG-07. 소명 = 텍스트(필수) + 첨부(선택) + **관련 업무 티켓(별도 입력칸, 플랫폼 링크 — `docs/policy.md` 3-3)** |
| 8 | ✅ **DB 직접 접근(2티어) — 게이트웨이 중계** (pgaudit안 폐기). 구현 순서: **① 게이트웨이 중계 + DB 접속 토큰 인증 + 원장 도착(정규화 SQL·테이블·컬럼·건수) + 원문 저장 → ② 회원번호 추출 → ③ 탐지 룰 → ④ 보호 대상 등록부 연동.** PR은 단계별로 따로. **상태: ① ✅ · ② ✅(결과 열의 테이블 OID·열 번호로 회원 열을 찾아 값만 읽는다 — SQL 파싱·조건·매개변수 추출 없음, 못 찾으면 미특정. `docs/architecture.md` 3-4 "정보주체 식별", "정보주체 기록 방식") · ③ ✅ · ④ ✅(등록부 5분 수신, 실패 시 마지막 등록부 → 고정 표, fail-closed)** (DB 기본 룰은 야간·주말·전월 대비 급증 3개, 나머지는 담당자가 룰 빌더로 — `docs/policy.md` 1-3) | `docs/architecture.md` 3-4(구조·인증·기록·세부 결정), `docs/api-spec.md` 2-2 "`access_path=DB` 기록 규칙"·2-3 `context` DB 키·2-4 예시, `docs/db-schema.md` 4절 토큰 발급 기록, `docs/policy.md` 1-3 "2단계 룰". **커넥션 풀링 금지**(사용자 연결 1개 = DB 연결 1개), 기록 실패 시 결과 완료 대신 오류(fail-closed), 전송은 게이트웨이 자체 버퍼 + relay 코드 재사용, 토큰은 서명 JWT(`DB_GATEWAY_TOKEN_KEY`, 관리자 JWT 키와 별개), DB 툴 연결은 **TLS 필수**(비TLS 거부). 명령어 통제·DB 롤 분리는 **v0.2** — 구현하지 않는다. **③에 경로 구분 포함**(`docs/policy.md` 1-5): `detection.access_path` + 그룹 키에 경로, 담당자·취급자 화면의 경로 표시·필터("화면 경유(3티어)"/"DB 직접(2티어)"), 소명 화면의 경로별 작성 안내(3-3) |
| 9 | ✅ 점검 보고서 — **v0.1은 마스킹 보고서만**. 생성 때 범위(전체/화면 경유/DB 직접) 선택, 생성 시점 스냅샷, 서버 파일 없이 화면 인쇄·PDF 저장 | LOG-09, `docs/policy.md` 6-1 (언마스킹 export·`EXPORT` 사유는 언마스킹과 함께 보류). **3티어·2티어 경로별로 나눠 집계**(`docs/policy.md` 1-5) |
| 10 | ✅ **v0.1 보강** — 기능 동결 뒤 법·안내서 대조 점검에서 나온 보완 (PR #60~#72) | 로그아웃 기록(`LOGOUT`, 데이터 유형 `NONE`, 로그인한 사용자의 로그아웃만 — 플랫폼·Argus 모두), 보고서에 소명 요지·검토 의견·처리자, 검색 조건 확대·취급자 "내 접속기록", 플랫폼 관리자 검색(POST 본문), 2티어 회원번호 추출(8 ②), 화면 알림(30초 확인)·웹 푸시·소명 기한, 담당자 본인 건 처리 차단, 원장 DELETE·TRUNCATE 차단·보고서 기준점, 소명 단위 보완, 접속지·특정 정보주체 기반 룰, 플랫폼 역할별 접근 범위·계정·권한 이력, Argus 계정 관리·계정 이력(운영 스크립트 `create-officer`·`unlock`은 `--reason` 필수), 보호 대상 등록부(8 ④), 화면 문구 정리, 회원가입 동의 구조(7-0). `docs/architecture.md` 2-1~2-4·3-4, `docs/policy.md` 1-6·3-4~3-6·4-5·4-6 |

- 순서의 근거: Argus 본연의 기능(탐지·통제·조회) 먼저 → 플랫폼 기능이 있어야 의미가 커지는 것(첨부의 근거 업무 기록, 2티어의 감사 대상 민감 테이블) → 모든 데이터가 갖춰진 뒤 취합하는 보고서
- 큰 항목(룰 빌더, 플랫폼 최소 구현, 2티어)은 착수 전에 계획을 사용자에게 짧게 설명하고 시작한다.

**판단 지점**

- **게이트 A — Skeleton 완성 확인**: ✅ 통과(M6 완료).
- **게이트 B — 2티어 진행 여부**: ✅ 통과 — ①·③ 머지, ②·④는 v0.1 보강에서 머지. 원래 기준: 2티어(순서 8)에 착수했으나 기능 동결까지 **구현 순서 ①(게이트웨이 중계 + 토큰 인증 + Argus 원장 도착 + 원문 저장)**에 이르지 못하면 브랜치를 머지하지 않고 다음 버전으로 넘긴다. ①은 됐는데 ②·③이 못 들어가면 ①까지만 머지하고 나머지는 README에 📋(설계 완료)로 표시한다.
- **기능 동결**: ✅ **2026-10-08 동결**(기능 레이어 1~9 종료). 동결 이후에는 버그 수정과 문서만 한다 — 점검·검수(법·안내서 대조, 보안 점검), 발견사항 기록과 보완. 점검에서 법 요구에 못 미친 항목은 보완(순서 10 v0.1 보강)으로 반영했고, 그 밖의 새 기능은 v0.2로 넘긴다.
- **범위 밖 아이디어는 구현하지 않는다.** 구현 로그의 "미결·이슈"에 **"v0.2 후보"** 로 기록만 하고 사용자에게 알린다.
- 2티어 착수 시 가장 먼저 확인할 것: ①DB 툴(DBeaver)이 게이트웨이의 **평문 비밀번호 요청**(AuthenticationCleartextPassword)에 응해 토큰을 보내는지, TLS 설정과 함께 실측 ②DB 툴이 쓰는 **확장 쿼리 프로토콜**(Parse·Bind·Execute)에서 SQL·매개변수·결과 컬럼 정보(RowDescription의 테이블·컬럼 번호)를 얻을 수 있는지 ③SQL 정규화 라이브러리(`pglast` 기본안)가 PostgreSQL 16 문법을 처리하는지. 설계와 다르게 해야 하면 4절 절차대로 멈추고 알린다

---

## 7. 작업 방식

- **마일스톤 단위로 진행.** 시작할 때 무엇을 만들지 짧게 계획을 공유하고, 끝나면 테스트 결과와 함께 요약한다.
- 한 번에 너무 많이 만들지 않는다. 작게 만들고 → 실행해보고 → 다음으로.
- 사용자가 코드를 이해할 수 있게, **왜 이렇게 짰는지**를 짧게 설명한다. 특히 보안·개인정보 관련 코드는 어떤 조항·정책에 대응하는지 함께.
- 사용자가 직접 해야 하는 일(GitHub 로그인, 설정 토글, Docker 실행 등)은 명확히 단계별로 안내한다. 사용자의 계정 인증 정보를 대신 입력하거나 요구하지 않는다.
- **모든 응답·진행 메시지·문서는 한국어로 쓴다.**
- **`.env` 등 시크릿 파일은 값을 전부 가린 형태로만 확인한다**(예: `sed -E 's/=.+/=<set>/' .env`). 키 이름을 골라 가리는 필터는 누락이 생긴다(이 방식으로 HMAC 키가 노출되어 키를 교체한 적이 있다).

---

## 8. 기록 규칙

- **세션을 마칠 때마다** `_notes/implementation-log.md`(레포 루트, `.gitignore`로 git 제외 — 공개 레포에 올리지 않는 작업 일지)에 날짜별로 추가한다: 한 일 / 결정사항 / 설계 변경(있으면) / 미결·이슈 / 다음 할 일.
- 결정의 근거가 남도록 **기각한 대안과 그 이유**도 남긴다.

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
| 탐지 그룹 키 (EVENT) | `(rule, source, access_path, actor, occurred_at 날짜(KST), data_category, action_group)` — 데이터 유형·행위 구분(READ·DOWNLOAD·CHANGE·SESSION)이 다르면 소명 단위가 다르다 | policy 2-2, db-schema(0018) |
| 소명 기한 | 요청 + 7일(`setting.explanation_due_days`, 재요청은 다시 7일), 남은 시간 24시간 이하면 임박 알림 | policy 3-4 |
| 화면 알림 확인 주기 | 30초 (`GET /api/notifications`) | architecture 2-1 |
| 보호 대상 등록부 | 게이트웨이 수신 5분(실패 시 30초 뒤 재시도), DB 구조 목록 전송 1시간 | architecture 3-4 |
| 접속기록 보관 | 1년 | policy 5-1 |
| 식별값 표기 | 저장 `"10293"` / 표시 `member_10293` / 마스킹 `member_10***` | db-schema 2-1 |
