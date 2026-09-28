# API 명세서 — 시스템 간 (플랫폼 → Argus)

> 작성일: 2026-09-23 / **v0.2 (2026-09-23 개정 — DB 스키마와 상호 대조하여 불일치 8건 수정)**
> 관련 문서: [[아키텍처_설계서.md]], [[DB스키마.md]], [[요구사항정의서.md]], [[정책정의서.md]], [[액터별_플로우.md]]
> 범위: 두 시스템의 **경계 계약**만 다룬다. 화면용 내부 API(argus-web ↔ argus-api 등)는 구현하며 코드로 정의하고 FastAPI OpenAPI(Swagger)로 자동 문서화한다.

| API | 용도 | 방향 |
|---|---|---|
| **① 접속기록 수집** | 플랫폼 Agent(미들웨어)가 생성한 접속기록을 Argus 원장으로 전달 | 플랫폼 relay worker → argus-api |
| **② 취급자 동기화** | 취급자 계정의 생성·변경·퇴직을 Argus에 반영 (소명 대상 지정, 퇴직자 룰, A5 로그인) | 플랫폼 relay worker → argus-api |
| **③ 헬스체크** | 배포 검증·전송 전 가용성 확인 | 배포 스크립트 / relay worker |

> 상용 솔루션으로 치면 **Agent ↔ 수집 서버 프로토콜**에 해당한다. Argus는 이 계약만 지키면 어떤 출처 시스템(2단계의 DB 감사로그 수집기 포함)이든 받아들인다.

### v0.2 개정 내역

| # | 수정 | 사유 |
|---|---|---|
| 1 | **배치 커서를 `id` 기준으로 통일** (2-6절) | v0.1은 "`received_at` 기준 신규분"이라 했으나 DB스키마 3-7절은 `id` 커서였음. `received_at`은 시계 오차·동시 삽입으로 누락 위험이 있어 커서 부적합 |
| 2 | **401 시 outbox는 `PENDING` 유지** (1-5절) | v0.1은 "재시도 안 함"이었는데, 키 교체 중 발생한 일시적 401에 접속기록이 유실됨. 설정 오류는 사람이 고치면 자동 재개되어야 함 |
| 3 | 정보주체 식별값 표기 규칙 명시 (1-7절) | 문서마다 `member_10293` / `"10293"`이 섞여 있었음 |
| 4 | `context` 필드 구조 정의 (2-3절) | `UNMASK` 사유·대상, 티켓 ID의 형식이 정해지지 않았음 |
| 5 | **Argus 자체 기록의 정보주체 규칙** (2-7절) | Argus가 회원 PK를 중복 축적하는 문제가 정의되지 않았음 |
| 6 | 취급자 동기화에 `terminated_at` 추가 (3-1절) | 퇴직자 룰을 **행위 시점 기준**으로 판정하려면 퇴직 시각이 필요 (DB스키마 `handler.terminated_at`) |
| 7 | 미동기화 취급자의 로그 처리 규칙 (2-6절) | 수락 후 나중에 매칭 — 명시가 없어 구현이 갈릴 수 있었음 |
| 8 | 시드 데이터 주입 경로 (4절) | baseline용 과거 접속기록을 DB에 직접 INSERT하면 해시체인이 깨짐 |

---

## 1. 공통 규약

### 1-1. 전송

| 항목 | 규약 |
|---|---|
| 프로토콜 | HTTPS (§7④ 전송구간 암호화). 단일 VM 배포에서는 도커 내부 네트워크로 호출되나, 계약은 별도 서버 배치를 전제로 HTTPS 기준으로 정의 |
| 형식 | `Content-Type: application/json; charset=utf-8` |
| 시각 | ISO 8601 + 오프셋 (`2026-09-15T23:41:07+09:00`). 플랫폼·Argus 서버는 NTP로 시각 동기화 |
| 배치 | 요청 1건당 이벤트 최대 **100건** |
| 요청 크기 | 최대 1MB (초과 시 413) |

### 1-2. 인증·무결성 — HMAC 서명

출처 시스템별로 공유 비밀키(secret)를 발급하고, 요청마다 서명한다. API 키 단순 전달 대신 HMAC을 쓰는 이유는 **인증 + 전송 중 위·변조 탐지 + 재전송 공격 방지**를 한 번에 얻기 위함이다(§8③ 위·변조 방지 취지를 전송 구간까지 확장).

| 헤더 | 값 |
|---|---|
| `X-Argus-Source` | 출처 시스템 코드 (예: `PLATFORM`) |
| `X-Argus-Timestamp` | 요청 시각 (Unix epoch 초) |
| `X-Argus-Signature` | `v1=` + hex( HMAC-SHA256( secret, `{timestamp}.{raw_body}` ) ) |

**Argus 검증 규칙**

1. `X-Argus-Source`로 비밀키 조회 → 없으면 `401`
2. 현재 시각과 timestamp 차이가 **±300초 초과** → `401` (재전송 방지)
3. 서명을 상수 시간 비교(constant-time compare)로 검증 → 불일치 시 `401`
4. 비밀키는 VM `.env`에만 보관(레포 미포함). 키 교체 시 신·구 키를 일정 기간 병행 허용(`v1` 접두어로 버전 구분)

### 1-3. 멱등성과 전달 보장

- 모든 이벤트는 발신 측에서 생성한 **`event_id`(UUID v4)**를 가진다.
- Argus는 `event_id`가 이미 존재하면 **저장하지 않고 중복(duplicate)으로 응답**한다.
- 발신 측(outbox relay)은 **at-least-once** 재전송, 수신 측은 멱등 → 결과적으로 정확히 한 번 저장.

### 1-4. 부분 수락 (배치 처리 결과)

배치 안의 이벤트는 **건별로 판정**한다. 형식 오류 1건 때문에 나머지 99건을 거부하지 않는다.

| 판정 | 의미 | relay의 후속 조치 |
|---|---|---|
| `accepted` | 저장됨 | outbox에서 삭제 |
| `duplicate` | 이미 저장된 event_id | outbox에서 삭제 |
| `rejected` | 해당 이벤트의 필드 검증 실패 | outbox 상태를 `DEAD`로 전환(재시도 안 함) + 운영 알림. **조용히 버리지 않는다** (§8③) |

### 1-5. HTTP 상태 코드와 재시도

| 코드 | 의미 | relay 동작 | outbox 상태 |
|---|---|---|---|
| `200` | 처리 완료 (건별 결과는 본문) | 1-4절대로 처리 | 건별 |
| `400` | 요청 전체가 파싱 불가 (JSON 오류, `events` 누락 등) | 재시도 안 함 + 알림 | `DEAD` |
| `401` | 인증 실패 (출처·시각·서명) | **재시도는 멈추고 알림**. 사람이 키·시각 설정을 고치면 자동 재개 | **`PENDING` 유지** |
| `413` | 요청 크기 초과 | 배치를 절반으로 쪼개 재전송 | `PENDING` |
| `429` / `5xx` / 네트워크 오류 | 일시 장애 | **지수 백오프로 무기한 재시도** (1분 → 2 → 4 … 최대 1시간 간격) | `PENDING` |

> `401`을 `DEAD`로 처리하지 않는 이유: 서명 키 교체·서버 시각 오차 같은 **설정 문제**로 발생하는데, 이때 로그를 버리면 §8③(분실 방지)을 위반한다. 접속기록은 어떤 경우에도 스스로 폐기하지 않고, 사람의 판단(`DEAD` 전환)이 개입해야 한다.

### 1-6. 오류 응답 형식

```json
{ "error": { "code": "INVALID_SIGNATURE", "message": "signature mismatch" } }
```

### 1-7. 정보주체 식별값 표기 (DB스키마 2-1절과 동일)

| 계층 | 표기 | 예 |
|---|---|---|
| 플랫폼 DB | `member.id` (bigint) | `10293` |
| **전송(이 API) · Argus 저장** | `subject_type` + `subject_ids`(문자열 배열) | `MEMBER` + `["10293"]` |
| 화면·보고서 표시 | `{type}_{id}` | `member_10293` |
| 마스킹 표시 (LOG-10) | 뒤 3자리 마스킹 | `member_10***` |

접두어(`member_`)는 **표시 계층에서만** 붙인다. 전송·저장에는 순수 식별자만 담는다.

---

## 2. ① 접속기록 수집 API

```
POST /ingest/v1/access-logs
```

### 2-1. 요청 본문

```json
{
  "events": [
    {
      "event_id": "3f1c2a9e-7b4d-4e21-9a0b-5c6d7e8f9a01",
      "occurred_at": "2026-09-15T23:41:07+09:00",
      "actor": { "login_id": "ops_park" },
      "client_ip": "10.20.3.55",
      "action": "DOWNLOAD",
      "subject": { "type": "MEMBER", "ids": ["10293", "10294"], "count": 120, "truncated": true },
      "access_path": "APP",
      "data_category": "MEMBER_BASIC",
      "request": { "method": "GET", "path": "/admin/members/export", "query_keys": ["team", "joined_from"] },
      "result": "SUCCESS",
      "context": {}
    }
  ]
}
```

### 2-2. 필드 정의

| 필드 | 타입 | 필수 | 설명 | 근거 |
|---|---|---|---|---|
| `event_id` | UUID | ✅ | 멱등 키 | 1-3절 |
| `occurred_at` | datetime | ✅ | 행위 발생 시각 | **§2 3호 접속일시** |
| `actor.login_id` | string(≤64) | ✅ | 행위한 개인정보취급자 계정 | **§2 3호 식별자** |
| `client_ip` | string(IPv4/IPv6) | ✅ | 취급자의 실제 접속 IP (Caddy가 전달한 값) | **§2 3호 접속지 정보** |
| `action` | enum | ✅ | 수행업무 (2-3절) | **§2 3호 수행업무** |
| `subject.type` | enum | 조건부 | 정보주체 유형. 현재 `MEMBER`만. **`action=LOGIN`일 때만 생략 가능** | **§2 3호 처리한 정보주체 정보** |
| `subject.ids` | string[] | 조건부 | 처리한 정보주체의 **내부 PK**. 이름·이메일 등 원본 정보는 절대 보내지 않음. 최대 1,000개 | 동일 |
| `subject.count` | int | 조건부 | 처리한 정보주체 수 (목록 조회·다운로드 건수) | 동일 / 대량 룰 판정 |
| `subject.truncated` | bool | - | `ids`가 1,000개를 넘어 잘렸는지 (`count`는 항상 전체 건수) | |
| `access_path` | enum | ✅ | `APP` / `DB` (1단계는 APP만) | 요구사항정의서 1-2절 |
| `data_category` | enum | ✅ | 처리한 데이터 유형 (2-3절) | 결제수단 조회 룰 등 |
| `request.method` / `request.path` | string | - | 호출된 관리자 기능 | 수행업무 재구성 근거 |
| `request.query_keys` | string[] | - | 검색 조건의 **키 이름만** (값은 개인정보일 수 있어 제외) | 최소수집 |
| `result` | enum | ✅ | `SUCCESS` / `FAILURE` (실패한 시도도 기록) | 아키텍처 설계서 3-3절 |
| `context` | object | - | 부가 정보 (2-3절 하단) | |

### 2-3. 코드값

**action (수행업무)** — 안내서의 예시(검색·열람·조회·입력·수정·삭제·출력·다운로드)를 묶어 정의

| 값 | 의미 |
|---|---|
| `LOGIN` | 개인정보처리시스템 로그인 |
| `READ` | 조회·열람·검색 (목록·상세 모두) |
| `CREATE` | 입력 |
| `UPDATE` | 수정 |
| `DELETE` | 삭제 |
| `DOWNLOAD` | 파일 다운로드·출력 |
| `EXPORT` | (Argus 전용) 점검 보고서 export |
| `UNMASK` | (Argus 전용) 정보주체 식별값 마스킹 해제 — `context.reason` 필수 |

**data_category (데이터 유형)**

| 값 | 대상 |
|---|---|
| `MEMBER_BASIC` | 회원 기본정보 (이름·연락처·주소 등) |
| `PAYMENT` | 결제수단 정보 (카드번호·계좌번호) — 민감도 높음 |
| `ORDER` | 주문·배송 정보 |
| `INQUIRY` | 1:1 문의 |
| `ACCESS_LOG` | (Argus 전용) 접속기록·탐지건 자체 |
| `NONE` | 로그인 등 데이터 처리 없음 |

**context (부가 정보)** — 정의된 키만 사용한다

| 키 | 타입 | 쓰이는 곳 |
|---|---|---|
| `ticket_id` | string | 1:1 문의 처리 시 연계 티켓 ID (스트레치: 업무시스템 연동 대조) |
| `reason` | string(≤500) | `UNMASK`·`EXPORT` 시 사유 — **§12① 용도 특정** |
| `target` | object | `UNMASK` 대상 (`{"detection_id": 123}` 또는 `{"access_log_id": 456}`) |
| `report_id` | int | `EXPORT` 시 생성된 보고서 ID |
| `query` | string | (2단계) 실행된 SQL |
| `row_count` | int | (2단계) 결과 건수 |

### 2-4. 이벤트 예시 (플랫폼 관리자 행위별)

| 행위 (액터별 플로우) | action | data_category | subject |
|---|---|---|---|
| 관리자 로그인 (F-02/03 #1) | `LOGIN` | `NONE` | 생략 |
| 회원 목록 조회, 20건 표시 (F-03 #2) | `READ` | `MEMBER_BASIC` | ids 20개, count 20 |
| 회원 상세 조회 (F-02 #4) | `READ` | `MEMBER_BASIC` | ids 1개, count 1 |
| 문의 상세 확인 (F-02 #3) | `READ` | `INQUIRY` | ids 1개 + `context.ticket_id` |
| 주문·결제 조회 (F-03 #4) | `READ` | `PAYMENT` | ids 1개, count 1 |
| 회원 목록 다운로드 120건 (F-03 #5) | `DOWNLOAD` | `MEMBER_BASIC` | ids 120개, count 120 |
| 회원정보 수정 실패 (검증 오류) | `UPDATE` | `MEMBER_BASIC` | ids 1개, `result: FAILURE` |

### 2-5. 응답

```json
{
  "accepted": 98,
  "duplicates": 1,
  "rejected": [
    { "event_id": "…", "code": "INVALID_ACTION", "message": "unknown action: VIEW" }
  ]
}
```

| rejected 코드 | 조건 |
|---|---|
| `MISSING_FIELD` | 필수 필드 누락 |
| `INVALID_ACTION` / `INVALID_CATEGORY` / `INVALID_ACCESS_PATH` | 코드값 오류 |
| `INVALID_TIMESTAMP` | 형식 오류, 또는 수신 시각 기준 **미래 5분 초과** |
| `INVALID_IP` | IP 형식 오류 |
| `SUBJECT_REQUIRED` | `LOGIN` 외 행위인데 subject 누락 |
| `REASON_REQUIRED` | `UNMASK`인데 `context.reason` 누락 |
| `UNKNOWN_CONTEXT_KEY` | `context`에 정의되지 않은 키 |

### 2-6. Argus 수신 처리

1. 서명 검증(1-2절) → 건별 필드 검증
2. `event_id` 중복 확인
3. **해시체인 계산 후 원장 append** — advisory lock으로 직렬화, 직전 레코드 hash와 이번 레코드 내용을 이어 SHA-256 (DB스키마 3-5절)
4. `received_at`은 Argus 수신 시각으로 기록

**탐지 배치와의 관계 (v0.2 수정)**

- 탐지 배치는 **`access_log.id` 커서**로 신규분을 가져온다. append가 직렬화되므로 `id`는 커밋 순서와 일치하고 누락이 없다.
- `received_at`은 **커서가 아니라 전송 지연 모니터링**(`received_at - occurred_at`) 용도다.
- 판정은 언제나 `occurred_at` 기준이다. 재시도 끝에 늦게 도착한 로그도 자신이 발생한 시각의 윈도우로 평가된다(AGGREGATE 룰은 해당 윈도우를 재집계).

**미동기화 취급자의 로그 (v0.2 신설)**

`actor.login_id`가 아직 `handler`에 없어도 이벤트는 **정상 수락**한다. 접속기록은 원문 그대로 저장되고, 이후 취급자 동기화가 도착하면 `(source_system_id, actor_login_id)` 조인으로 자연히 연결된다. 단, 취급자 속성이 필요한 룰(`actor_team` 등)은 그 시점에 **평가를 보류**하고 다음 배치에서 재평가한다(DB스키마 3-6절).

### 2-7. Argus 자체 접속기록의 특례 (v0.2 신설)

Argus 내부 행위(LOG-17)도 같은 규격으로 기록하지만, **outbox를 경유하지 않고** argus-api 미들웨어가 직접 append한다(자기 자신에게 HTTP 호출을 하지 않음). 이때 정보주체 기록 규칙이 다르다.

| Argus 행위 | action | subject | 이유 |
|---|---|---|---|
| 로그인 | `LOGIN` | 생략 | |
| 탐지건·접속기록 조회 | `READ` | **`count`만 기록, `ids`는 비움** | 조회할 때마다 회원 PK를 Argus 접속기록에 다시 쌓으면 식별자가 중복 축적되어 최소처리 원칙에 반한다 |
| 정보주체 ID로 검색 | `READ` | `count`만 + `request.query_keys` | 검색어 값 자체는 기록하지 않음 |
| **마스킹 해제** | `UNMASK` | `count` + `context.reason`·`context.target` | 무엇을 열어봤는지가 감사의 핵심 |
| 보고서 export | `EXPORT` | `count` + `context.report_id`, 언마스킹 시 `context.reason` | 보고서가 마스킹 우회 경로가 되지 않게 |

---

## 3. ② 취급자 동기화 API

```
POST /ingest/v1/handler-events
```

### 3-1. 요청 본문

```json
{
  "events": [
    {
      "event_id": "a7c1…",
      "type": "HANDLER_TERMINATED",
      "occurred_at": "2026-09-20T10:00:00+09:00",
      "handler": {
        "login_id": "cs_kim",
        "name": "김민지",
        "team": "CS",
        "employment_status": "TERMINATED",
        "terminated_at": "2026-09-20T18:00:00+09:00"
      }
    }
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `event_id` | UUID | ✅ | 멱등 키 |
| `type` | enum | ✅ | `HANDLER_CREATED` / `HANDLER_UPDATED` / `HANDLER_TERMINATED` |
| `occurred_at` | datetime | ✅ | 플랫폼에서 변경이 일어난 시각 |
| `handler.login_id` | string | ✅ | 취급자 계정 (접속기록 `actor.login_id`와 매칭 키) |
| `handler.name` | string | ✅ | 보고서·소명 화면 표시용 |
| `handler.team` | enum | - | `CS` / `MARKETING` / `OPS` |
| `handler.employment_status` | enum | ✅ | `ACTIVE` / `TERMINATED` |
| `handler.terminated_at` | datetime | 조건부 | **`employment_status=TERMINATED`면 필수.** 퇴직자 룰을 행위 시점 기준으로 판정하기 위함 (v0.2 신설) |

- **최소수집**: 취급자(직원)의 정보도 개인정보다. Argus가 받는 건 계정·이름·소속·재직상태·퇴직시각뿐이고, 연락처·사번·권한 상세는 받지 않는다.
- **순서 역전 대응**: Argus는 취급자별 `last_event_at`보다 오래된 이벤트는 무시한다(늦게 도착한 옛 변경이 최신 상태를 덮어쓰지 않도록).
- **A5 계정 발급**: `HANDLER_CREATED` 수신 시 Argus에 취급자용 로그인 계정(`role=HANDLER`)을 생성한다. 초기 비밀번호 전달 방식은 구현 시 결정(MVP는 시드 계정 사용).
- `HANDLER_TERMINATED` 수신 시 해당 A5 계정을 `DISABLED`로 전환한다. 진행 중인 소명 건은 담당자가 판단한다(DISMISS 또는 ESCALATE).

### 3-2. 응답

①과 동일한 형식 (`accepted` / `duplicates` / `rejected`). 추가 rejected 코드: `TERMINATED_AT_REQUIRED`.

---

## 4. 발신 측(플랫폼) 구현 규약

| 항목 | 규약 |
|---|---|
| 적재 | 관리자 라우트 요청 종료 시 미들웨어가 outbox에 적재 — **업무와 별도 트랜잭션** (실패한 시도도 남기기 위함) |
| topic | outbox 한 테이블에 `ACCESS_LOG` / `HANDLER` 두 topic을 함께 적재, relay가 topic별 엔드포인트로 전송 |
| 전송 주기 | relay가 수 초 간격으로 `PENDING` 건을 최대 100건씩 묶어 전송 |
| 순서 | 같은 topic 안에서 `created_at` 순으로 전송 (엄격한 순서 보장은 요구하지 않음 — 수신 측이 `occurred_at`·`last_event_at`으로 처리) |
| 금지 | 이름·이메일·연락처·카드번호 등 **원본 개인정보를 payload에 넣지 않는다** |
| **시드 데이터** | baseline용 과거 접속기록(요구사항 4-3)도 **이 수집 API를 통해** 주입한다. Argus DB에 직접 INSERT하면 해시체인이 성립하지 않는다. 시드 스크립트는 `occurred_at`을 과거로 지정하고 `X-Argus-Timestamp`는 현재 시각을 쓴다 |

> 시드 주입 시 `INVALID_TIMESTAMP`는 **미래 5분 초과**만 거부하므로 과거 시각은 통과한다.

---

## 5. ③ 헬스체크

```
GET /healthz
```

인증 불필요. 응답 `200 {"status":"ok","db":"ok"}`. DB 연결 실패 시 `503`. 배포 스크립트의 헬스체크(아키텍처 설계서 8-1절)와 relay의 사전 확인에 쓴다. 이 엔드포인트는 접속기록을 남기지 않는다(개인정보 처리가 없고, 주기적 호출이라 로그를 오염시킴).

---

## 6. 버전 관리

- 경로에 `/v1` 포함. 하위 호환이 깨지는 변경은 `/v2`로 추가하고 일정 기간 병행.
- 필드 추가는 하위 호환 변경으로 간주(수신 측은 모르는 필드를 무시). 단 `context`는 정의된 키만 허용하므로(`UNKNOWN_CONTEXT_KEY`) 키 추가 시 양쪽을 함께 배포한다.

---

## 7. 미결 / 구현 시 확정

- [ ] A5 초기 비밀번호 전달 방식 (MVP는 시드 계정)
- [x] 2단계(`access_path=DB`) 확장 필드 → `context.query`·`context.row_count` 예약 (v0.2)
- [ ] DEAD 상태 이벤트 운영 알림 채널 (로그 / 이메일)
- [ ] HMAC 비밀키 교체 절차의 운영 문서화
