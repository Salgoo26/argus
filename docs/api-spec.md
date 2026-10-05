# API 명세서 — 시스템 간 (플랫폼 → Argus)

> 작성일: 2026-09-23 / v0.2 (2026-09-23 개정 — DB 스키마와 상호 대조하여 불일치 8건 수정) / v0.3 (2026-09-29 개정 — 구현 M1 반영: 오류 코드 세분, 원본 개인정보 유입 차단 검증, Argus 전용 코드값 거부) / **v0.4 (2026-09-30 개정 — 구현 M2 반영: relay 401 해석, 경로 템플릿, 로그인·로그아웃·실패 요청 기록 규칙, A5 초기 계정)** / **v0.5 (2026-10-05 개정 — 기능 레이어 8(2티어) 설계 확정: `access_path=DB` 기록 규칙, `context` DB 키, DB 예시, 원문 SQL 유입 차단)**
> 관련 문서: [[아키텍처_설계서.md]], [[DB스키마.md]], [[요구사항정의서.md]], [[정책정의서.md]], [[액터별_플로우.md]]
> 범위: 두 시스템의 **경계 계약**만 다룬다. 화면용 내부 API(argus-web ↔ argus-api 등)는 구현하며 코드로 정의하고 FastAPI OpenAPI(Swagger)로 자동 문서화한다.

| API | 용도 | 방향 |
|---|---|---|
| **① 접속기록 수집** | 플랫폼 Agent(미들웨어)가 생성한 접속기록을 Argus 원장으로 전달 | 플랫폼 relay worker → argus-api |
| **② 취급자 동기화** | 취급자 계정의 생성·변경·퇴직을 Argus에 반영 (소명 대상 지정, 퇴직자 룰, A5 로그인) | 플랫폼 relay worker → argus-api |
| **③ 헬스체크** | 배포 검증·전송 전 가용성 확인 | 배포 스크립트 / relay worker |

> 상용 솔루션으로 치면 **Agent ↔ 수집 서버 프로토콜**에 해당한다. Argus는 이 계약만 지키면 어떤 출처 시스템(2단계의 DB 접근 게이트웨이 포함 — 2026-10-05)이든 받아들인다.

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

### v0.3 개정 내역 (2026-09-29, 구현 M1에서 확정)

| # | 수정 | 사유 |
|---|---|---|
| 1 | **이벤트 100건 초과 → `413 TOO_MANY_EVENTS`** (1-1·1-5절) | v0.2는 코드 미정. 400이면 relay가 배치 전체를 `DEAD` 처리하지만, 413이면 배치를 절반으로 쪼개 재전송 → 접속기록을 버리지 않는 쪽 |
| 2 | **요청 단위 오류 코드 세분** (1-5·1-6절) | v0.2는 예시 1개뿐. 401을 원인별(`UNKNOWN_SOURCE`·`INVALID_TIMESTAMP`·`INVALID_SIGNATURE`)로 나눠야 relay 알림에서 키 문제와 시각 오차를 구분해 사람이 고칠 수 있음(출처 목록은 비밀이 아님) |
| 3 | **rejected 코드 `INVALID_FIELD` 추가** (2-5절) | 형식·길이·타입 오류 등 코드표에 없던 필드 오류의 수용처 |
| 4 | **원본 개인정보 유입 차단 검증** (2-2절) | CLAUDE.md 절대 규칙 #3(원본 개인정보 전송 금지)을 발신 측 약속에만 맡기지 않고 **수신 측에서도 방어**. `subject.ids` 형식 제한, `request.path`의 쿼리스트링 거부 등 |
| 5 | **Argus 전용 코드값은 외부 출처에서 거부** (2-3·2-5절) | `EXPORT`·`UNMASK`·`ACCESS_LOG`의 "(Argus 전용)" 표기를 수신 검증으로 구체화 — 외부 출처가 Argus 내부 행위를 사칭하지 못하게 |
| 6 | **② 취급자 동기화의 멱등·중복 판정 규칙** (3-1절) | `handler`에 event_id 저장 칸이 없어 판정 기준이 미정이었음. 상태 스냅샷 + `last_event_at` 기준으로 확정, event_id 저장 테이블은 기각 |

### v0.5 개정 내역 (2026-10-05, 기능 레이어 8 설계 확정)

| # | 수정 | 사유 |
|---|---|---|
| 1 | **`access_path=DB` 기록 규칙** (2-2절) | DB 접근 게이트웨이(아키텍처 설계서 3-4)가 보내는 기록의 필드별 의미 정의 |
| 2 | **`context` DB 키 확정** (2-3절) — `db_user`·`sql_normalized`·`tables`·`columns`·`row_count`·`raw_ref`·`raw_fingerprint`·`subject_unresolved`·`token_id` | v0.2에 예약한 `query`(실행된 SQL)는 **원문이라 폐기** — 매개변수·리터럴에 개인정보가 실림(절대 규칙 #3). 원문은 게이트웨이 저장소에만 |
| 3 | DB 이벤트 예시 (2-4절) | 회원번호 추출·정보주체 미특정·연결 인증 |
| 4 | **원문 SQL 유입 차단 검증** (2-2절) | `sql_normalized`에 문자열 리터럴(`'`)이 있으면 거부 — 정규화가 깨졌거나 원문이 실린 것 |

### v0.4 개정 내역 (2026-09-30, 구현 M2에서 확정)

| # | 수정 | 사유 |
|---|---|---|
| 1 | **relay의 401 처리 해석** (1-5절) | "재시도는 멈추고 알림, 사람이 고치면 자동 재개"는 완전히 멈추면 자동 재개가 불가능해 문구가 상충 → **다른 실패와 같은 지수 백오프(1분 → 2 → 4 … 최대 1시간)로 재확인 + 매번 ERROR 로그** (2026-10-01 문구 정정 — 기존 "최대 간격(1시간)으로만"은 구현과 달랐음. 키를 고친 직후 빨리 재개되는 쪽이 의도) |
| 2 | **`request.path` = 라우트 템플릿** (2-2절) | 실제 URL을 보내면 경로 변수에 실린 값(검색어·이름 등)이 원장에 남음 |
| 3 | **로그인 실패·로그아웃 기록 규칙** (2-4절) | 존재하지 않는 ID의 로그인 실패는 미기록(ID 칸에 잘못 입력된 비밀번호가 append-only 원장에 영구 저장되는 것 방지), 로그아웃은 제외 |
| 4 | **정보주체 기록 전 실패 → `subject.count = 0`** (2-2·2-4절) | 입력 검증 오류 등 대상이 정해지기 전에 실패한 요청의 건수 규칙이 없었음 |
| 5 | **A5 초기 계정** (3-1절) | 무작위 argon2id 해시로 생성(로그인 불가), 재입사 시 자동 복구 안 함, login_id 충돌 시 생성 안 함 |
| 6 | ② 수신 검증 세부 (3-1·3-2절) | `occurred_at` 미래 5분 초과 거부, `terminated_at` 미래 허용, 상태·유형 불일치 거부 |

---

## 1. 공통 규약

### 1-1. 전송

| 항목 | 규약 |
|---|---|
| 프로토콜 | HTTPS (§7④ 전송구간 암호화). 단일 VM 배포에서는 도커 내부 네트워크로 호출되나, 계약은 별도 서버 배치를 전제로 HTTPS 기준으로 정의 |
| 형식 | `Content-Type: application/json; charset=utf-8` |
| 시각 | ISO 8601 + 오프셋 (`2026-09-15T23:41:07+09:00`). 플랫폼·Argus 서버는 NTP로 시각 동기화 |
| 배치 | 요청 1건당 이벤트 최대 **100건** (초과 시 `413 TOO_MANY_EVENTS`) |
| 요청 크기 | 최대 1MB (초과 시 `413 PAYLOAD_TOO_LARGE`. 수신 측은 본문을 스트리밍으로 읽으며 한도 초과 즉시 중단) |

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
4. 비밀키는 VM `.env`에만 보관(레포 미포함). 키 교체 시 신·구 키를 일정 기간 병행 허용(`v1` 접두어로 버전 구분) — **Walking Skeleton은 미구현(출처당 키 1개).** 7절 미결 참조

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
| `400` | 요청 전체가 파싱 불가 (`MALFORMED_JSON`, `MISSING_EVENTS`, `BAD_REQUEST`) | 재시도 안 함 + 알림 | `DEAD` |
| `401` | 인증 실패 (`UNKNOWN_SOURCE`, `INVALID_TIMESTAMP`, `INVALID_SIGNATURE`) | **다른 실패와 같은 지수 백오프(1분 → 2 → 4 … 최대 1시간)로 재확인 + 매번 ERROR 로그**(코드를 포함해 원인 구분). 사람이 키·시각 설정을 고치면 다음 재확인 때 자동 재개 (v0.4 해석, 2026-10-01 문구 정정) | **`PENDING` 유지** |
| `413` | 요청 크기 초과(`PAYLOAD_TOO_LARGE`) 또는 이벤트 수 초과(`TOO_MANY_EVENTS`) | 배치를 절반으로 쪼개 재전송 | `PENDING` |
| `429` / `5xx` / 네트워크 오류 | 일시 장애 | **지수 백오프로 무기한 재시도** (1분 → 2 → 4 … 최대 1시간 간격) | `PENDING` |

> `401`을 `DEAD`로 처리하지 않는 이유: 서명 키 교체·서버 시각 오차 같은 **설정 문제**로 발생하는데, 이때 로그를 버리면 §8③(분실 방지)을 위반한다. 접속기록은 어떤 경우에도 스스로 폐기하지 않고, 사람의 판단(`DEAD` 전환)이 개입해야 한다.
>
> relay 구현 세부(v0.4): 200인데 본문 해석 불가, 404 등 **예상 밖 응답, 1건짜리 413도 `DEAD`가 아니라 `PENDING` + 백오프**(절대 규칙 #7 — 스스로 버리지 않음). 일시 장애(네트워크·429·5xx)는 WARNING, 설정 문제(401·404 등)는 ERROR 로그. 로그에는 건수·오류 코드만 남기고 payload(회원 PK)는 남기지 않는다.
>
> 이벤트 수 초과를 `400`이 아닌 `413`으로 두는 이유(v0.3): `400`은 relay가 배치 전체를 `DEAD`로 보내는 코드다. 이벤트 수 초과는 데이터 자체의 결함이 아니라 **나눠 보내면 해결되는 문제**이므로, 쪼개서 재전송하는 `413`으로 분류한다.

### 1-6. 오류 응답 형식

```json
{ "error": { "code": "INVALID_SIGNATURE", "message": "signature mismatch" } }
```

**요청 단위 오류 코드** (v0.3 — 건별 판정 `rejected` 코드는 2-5절)

| HTTP | code | 조건 |
|---|---|---|
| 401 | `UNKNOWN_SOURCE` | `X-Argus-Source`에 해당하는 비밀키 없음 |
| 401 | `INVALID_TIMESTAMP` | `X-Argus-Timestamp` 누락 또는 ±300초 초과 |
| 401 | `INVALID_SIGNATURE` | 서명 누락·형식 오류·불일치 |
| 400 | `MALFORMED_JSON` | 본문이 JSON이 아님 |
| 400 | `MISSING_EVENTS` | `events` 배열 없음 |
| 400 | `BAD_REQUEST` | 그 밖의 요청 형식 오류 (프레임워크 기본 검증 오류를 고정 형식으로 대체) |
| 413 | `PAYLOAD_TOO_LARGE` | 본문 1MB 초과 |
| 413 | `TOO_MANY_EVENTS` | 이벤트 100건 초과 |

- **오류 메시지에 입력값을 되풀이하지 않는다**(코드값 제외). 잘못 들어온 개인정보가 응답·로그로 다시 새지 않게 하기 위함이다. 같은 이유로 프레임워크 기본 422 응답(입력값 포함)도 쓰지 않는다.

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
| `subject.count` | int | 조건부 | 처리한 정보주체 수 (목록 조회·다운로드 건수). **정보주체를 기록하기 전에 실패한 요청은 0** (v0.4) | 동일 / 대량 룰 판정 |
| `subject.truncated` | bool | - | `ids`가 1,000개를 넘어 잘렸는지 (`count`는 항상 전체 건수) | |
| `access_path` | enum | ✅ | `APP`(관리자 화면 경유) / `DB`(**DB 접근 게이트웨이 경유** 직접 접근 — 아키텍처 설계서 3-4, v0.5). DB 기록의 필드 규칙은 아래 "`access_path=DB` 기록 규칙" | 요구사항정의서 1-2절 |
| `data_category` | enum | ✅ | 처리한 데이터 유형 (2-3절) | 결제수단 조회 룰 등 |
| `request.method` / `request.path` | string | - | 호출된 관리자 기능. **`path`는 실제 URL이 아니라 라우트 템플릿**(예: `/admin/members/{member_id}`) — 경로 변수의 값이 원장에 남지 않게 (v0.4) | 수행업무 재구성 근거 |
| `request.query_keys` | string[] | - | 검색 조건의 **키 이름만** (값은 개인정보일 수 있어 제외) | 최소수집 |
| `result` | enum | ✅ | `SUCCESS` / `FAILURE` (실패한 시도도 기록) | 아키텍처 설계서 3-3절 |
| `context` | object | - | 부가 정보 (2-3절 하단) | |

**수신 측 형식 검증** (v0.3 — 위반 시 해당 이벤트 `rejected`, 코드는 괄호)

| 필드 | 규칙 | 목적 |
|---|---|---|
| `event_id` | UUID 형식 (`INVALID_FIELD`) | |
| `actor.login_id` | 공백·제어문자 없는 출력 가능 ASCII 1~64자 (`INVALID_FIELD`) | |
| `occurred_at` | ISO 8601 + **오프셋 필수**, 미래 5분 초과 불가 (`INVALID_TIMESTAMP`) | 시각 모호성 제거 |
| `subject` | `LOGIN` 외에는 필수 (`SUBJECT_REQUIRED`), `type`은 `MEMBER`만, `ids`·`count` 중 하나 이상 필수 | §2 3호 |
| **`subject.ids`** | 각 원소 **`[A-Za-z0-9_-]{1,64}`만 허용**, 최대 1,000개 (`INVALID_FIELD`) | **원본 개인정보 유입 차단** — 이메일(`@`·`.`), 이름(공백·한글), 전화번호 형식 등이 식별값 자리에 들어오면 거부 |
| `subject.count` | 0 이상 정수, **`ids` 개수 이상** (`INVALID_FIELD`). 생략 시 `ids` 개수 | 건수 축소 기록 방지 |
| **`request.path`** | `/`로 시작, 255자 이하, **`?`·`#` 포함 불가** (`INVALID_FIELD`) | 쿼리스트링에 실린 검색값(개인정보)이 경로에 섞여 들어오는 것 차단 |
| `request.method` | 표준 HTTP 메서드 | |
| **`request.query_keys`** | 최대 50개, 각 원소 **키 이름 형식** `[A-Za-z0-9_.\[\]-]{1,64}`만 (`INVALID_FIELD`) | 키 대신 값이 실려 오는 것 차단 |
| `context` 값 | `reason` 1~500자, `ticket_id` 1~64자, `report_id`·`row_count` 정수, `target`은 `{"detection_id": int}` 또는 `{"access_log_id": int}` (`INVALID_FIELD`) | |
| **`context` DB 키** (v0.5) | 2-3절 DB 키는 **`access_path=DB`일 때만** 허용(APP 기록에 오면 `UNKNOWN_CONTEXT_KEY`). DB 기록은 `db_user`·`raw_ref`·`raw_fingerprint` 필수, `LOGIN` 외에는 `sql_normalized`·`row_count`도 필수 (`MISSING_FIELD`). 형식: `db_user` `[A-Za-z0-9_]{1,63}` / `sql_normalized` 1~4,000자, **작은따옴표(`'`) 포함 불가** / `tables`·`columns` 각 최대 50·200개, 원소는 `[A-Za-z0-9_."]{1,128}` / `raw_ref` `[A-Za-z0-9:_-]{1,128}` / `raw_fingerprint` `sha256:` + 소문자 hex 64자 / `subject_unresolved` bool / `token_id` UUID (`INVALID_FIELD`) | **원문 SQL 유입 차단** — 정규화 SQL은 리터럴을 `$1`…로 바꾸므로 따옴표가 남을 수 없다. 남았다면 정규화 실패이거나 원문이 실린 것 |

> 절대 규칙 #3(원본 개인정보 전송 금지)은 발신 측 약속이다. 수신 측 검증은 그 약속이 깨졌을 때를 대비한 **두 번째 방어선**이며, 형식만으로 모든 개인정보를 걸러낼 수는 없다(예: 숫자로만 된 값). 1차 책임은 여전히 발신 측에 있다.

**`access_path=DB` 기록 규칙** (v0.5 — 발신 측: DB 접근 게이트웨이, 아키텍처 설계서 3-4)

| 필드 | DB 기록에서의 의미 |
|---|---|
| `event_id` | 게이트웨이가 문장(또는 연결 인증) 1건마다 생성. 게이트웨이 원문 저장소의 키와 같다 |
| `occurred_at` | 문장 실행 시작 시각 (`LOGIN`은 인증 시각) |
| `actor.login_id` | **토큰의 주인 = 플랫폼 관리자 아이디**. DB 계정(공용)이 아니다 → 3티어 기록과 같은 아이디라 취급자 명부·탐지 그룹이 그대로 맞는다 |
| `client_ip` | 게이트웨이가 TCP 연결에서 직접 본 DB 툴의 IP(프록시 헤더 없음) |
| `action` | 문장 종류 → 수행업무 (아키텍처 설계서 3-4 "수행업무 매핑"). 연결 인증은 `LOGIN` |
| `subject` | `type=MEMBER`. 결과·조건에서 추출한 회원번호를 `ids`(중복 제거), `count`는 고유 회원 수. **정보주체 미특정**이면 `ids` 비움 + `count = row_count` + `context.subject_unresolved = true` |
| `data_category` | 문장이 건드린 테이블 중 가장 민감한 유형 (3-4 "데이터 유형") |
| `request` | 생략 (HTTP 요청이 아님). 대신 `context.sql_normalized`·`tables`·`columns` |
| `result` | DB가 오류를 돌려주거나 실행 중 연결이 끊기면 `FAILURE` |

- 연결 인증 실패(`LOGIN` + `FAILURE`)는 **존재하는 플랫폼 아이디일 때만** 기록한다(2-4절 v0.4 규칙과 동일 — 아이디 칸에 잘못 입력된 값이 원장에 영구 저장되는 것 방지).
- 시스템 카탈로그만 읽는 문장(DB 툴의 메타데이터 조회)·트랜잭션 제어·`SET`·`SHOW`는 보내지 않는다. **DDL·DCL은 v0.1에서 보내지 않는다**(게이트웨이 원문 저장소에만 — 수행업무 코드가 없음, v0.2에서 명령어 통제와 함께 정의).

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
| `UNMASK` | (Argus 전용) 정보주체 식별값 마스킹 해제 — `context.reason` 필수. **⚠ 재검토 메모(2026-10-01)**: Argus 해제 기능은 v0.2 이후로 보류됐고, 보안성 검토 후 **플랫폼 관리자 화면에 표시제한 + 사유 입력 해제**를 구현할 예정 — 그때 플랫폼 출처의 해제 행위를 받기 위해 `UNMASK`의 외부 출처 허용 또는 `READ` + `context.reason`으로 명세를 바꿔야 함 |

> **(Argus 전용)** 코드값(`EXPORT`·`UNMASK`, 아래 `ACCESS_LOG`)은 Argus 자체 기록(2-7절)에서만 쓴다. **외부 출처가 보내면 거부**한다(`INVALID_ACTION` / `INVALID_CATEGORY`) — 외부 시스템이 Argus 내부 행위를 사칭한 기록을 원장에 남기지 못하게 하기 위함 (v0.3).

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
| ~~`query`~~ | ~~string~~ | ~~(2단계) 실행된 SQL~~ — **v0.5 폐기**: 원문은 리터럴·매개변수에 개인정보가 실림. `sql_normalized` + 게이트웨이 원문 참조로 대체 |
| `row_count` | int | (DB) 결과 행 수(SELECT) 또는 영향받은 행 수(DML) — DB가 돌려준 완료 태그 기준 |
| `db_user` | string | (DB, v0.5) 실제로 DB에 연결한 계정(공용 계정). 실사용자는 `actor.login_id` |
| `sql_normalized` | string(≤4,000) | (DB, v0.5) **정규화 SQL** — 리터럴을 `$1`…로 치환(`SELECT id, name FROM member WHERE email = $1`). 4,000자 초과는 잘라 보낸다 |
| `tables` | string[] | (DB, v0.5) 문장이 참조한 테이블(`member`, `public.orders` 등) |
| `columns` | string[] | (DB, v0.5) 참조·반환한 컬럼(`member.email`). `*`는 펼친 결과 기준 |
| `raw_ref` | string | (DB, v0.5) **게이트웨이 원문 저장소의 참조** — 원문(SQL·매개변수)을 찾는 키 |
| `raw_fingerprint` | string | (DB, v0.5) 원문 레코드의 SHA-256 지문(`sha256:…`). 원문 저장소와 대조해 위·변조·유실 확인 |
| `subject_unresolved` | bool | (DB, v0.5) **정보주체 미특정** — 회원번호를 추출하지 못한 처리 |
| `token_id` | string(UUID) | (DB, v0.5) 이 연결에 쓴 DB 접속 토큰의 ID — 플랫폼 토큰 발급 기록과 연결(DB스키마 4절) |

### 2-4. 이벤트 예시 (플랫폼 관리자 행위별)

| 행위 (액터별 플로우) | action | data_category | subject |
|---|---|---|---|
| 관리자 로그인 (F-02/03 #1) | `LOGIN` | `NONE` | 생략 |
| 회원 목록 조회, 20건 표시 (F-03 #2) | `READ` | `MEMBER_BASIC` | ids 20개, count 20 |
| 회원 상세 조회 (F-02 #4) | `READ` | `MEMBER_BASIC` | ids 1개, count 1 |
| 문의 상세 확인 (F-02 #3) | `READ` | `INQUIRY` | ids 1개 + `context.ticket_id` |
| 주문 목록 조회 (2026-10-02 구현) | `READ` | `ORDER` | 표시된 주문의 회원(중복 제거) |
| 회원 상세 조회 — 환불계좌 끝 4자리 포함 (2026-10-02) | `READ` | `MEMBER_BASIC` | ids 1개, count 1 |
| **환불계좌 전체 보기** (2026-10-02, 구 "주문·결제 조회") | `READ` | `PAYMENT` | ids 1개, count 1 — 결제수단 조회 룰(HIGH)로 항상 탐지 |
| 1:1 문의 목록 (2026-10-02) | `READ` | `INQUIRY` | 표시된 작성자(중복 제거), 본문 제외 |
| 1:1 문의 답변 (2026-10-02) | `UPDATE` | `INQUIRY` | ids 1개 + `context.ticket_id` (한 번만 — 이미 답변이면 409, 실패도 기록) |
| 회원 목록 다운로드 120건 (F-03 #5) | `DOWNLOAD` | `MEMBER_BASIC` | ids 120개, count 120 |
| 회원정보 수정 실패 (검증 오류) | `UPDATE` | `MEMBER_BASIC` | ids 1개, `result: FAILURE` |
| 관리자 로그인 실패 — **존재하는 계정**(비밀번호 오류·잠김·퇴직자) (v0.4) | `LOGIN` | `NONE` | 생략, `result: FAILURE` |
| 관리자 로그인 실패 — **존재하지 않는 ID** (v0.4) | **기록하지 않음** | | |
| 관리자 로그아웃 (v0.4) | **기록하지 않음** (명시적 제외) | | |
| 정보주체를 기록하기 전에 실패한 요청(입력 검증 오류 400 등) (v0.4) | 해당 행위 | 해당 유형 | count 0, `result: FAILURE` |

**DB 직접 접근 (게이트웨이, `access_path=DB`, v0.5)** — `context` 공통: `db_user`, `raw_ref`, `raw_fingerprint`, `token_id`

| 행위 (DB 툴) | action | data_category | subject / context |
|---|---|---|---|
| 게이트웨이 접속(토큰 인증 성공) | `LOGIN` | `NONE` | 생략 |
| 접속 실패 — 존재하는 아이디, 토큰 오류·만료·퇴직 | `LOGIN` | `NONE` | 생략, `result: FAILURE` |
| `SELECT * FROM member WHERE email = 'a@x.com'` | `READ` | `MEMBER_BASIC` | 결과의 `member.id`에서 ids 1개 · `sql_normalized: …WHERE email = $1` · `row_count: 1` |
| `SELECT name, phone FROM member LIMIT 50` | `READ` | `MEMBER_BASIC` | **미특정** — ids 비움, count 50, `subject_unresolved: true` |
| `SELECT o.member_id, r.bank_name FROM orders o JOIN refund_account r …` | `READ` | `PAYMENT` | 결과의 `member_id`에서 ids · `tables: [orders, refund_account]` |
| `UPDATE member SET phone = $1 WHERE id = $2` | `UPDATE` | `MEMBER_BASIC` | 매개변수 `$2`에서 ids 1개 |
| `DELETE FROM inquiry WHERE created_at < $1` | `DELETE` | `INQUIRY` | **미특정** — count = 삭제 행 수 |
| `SELECT` 실행 중 DB 오류 | `READ` | 해당 유형 | count 0, `result: FAILURE` |

- **존재하지 않는 ID의 로그인 실패를 기록하지 않는 이유**(v0.4): ID 칸에 비밀번호를 잘못 입력하는 일이 흔한데, 그 값이 append-only 원장에 영구 저장된다. 게다가 그 값은 §2 3호의 "개인정보취급자 식별자"도 아니다. 대가로 존재하지 않는 ID 대입 시도는 Argus에 보이지 않는다(아키텍처 설계서 8-6 과제).
- **로그아웃을 제외하는 이유**(v0.4): 개인정보 처리가 없고 수행업무 코드(2-3절)에도 없다. 발신 측은 `@access_log_exempt("사유")`로 **명시적으로** 제외한다 — 표시가 없는 관리자 라우트는 기동 자체가 거부된다(아키텍처 설계서 3-2).
- 대상이 정해진 요청은 발신 측이 **업무 로직보다 먼저** 대상 PK를 기록한다 — 업무가 실패해도 시도 대상이 남도록.

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
| `INVALID_FIELD` | (v0.3) 형식·길이·타입 오류 — 2-2절 "수신 측 형식 검증" |
| `INVALID_ACTION` / `INVALID_CATEGORY` / `INVALID_ACCESS_PATH` | 코드값 오류. 외부 출처의 **Argus 전용 코드값**(`EXPORT`·`UNMASK` / `ACCESS_LOG`)도 여기에 해당 (v0.3) |
| `INVALID_TIMESTAMP` | 형식 오류, 또는 수신 시각 기준 **미래 5분 초과** |
| `INVALID_IP` | IP 형식 오류 |
| `SUBJECT_REQUIRED` | `LOGIN` 외 행위인데 subject 누락 |
| `REASON_REQUIRED` | `UNMASK`인데 `context.reason` 누락 |
| `UNKNOWN_CONTEXT_KEY` | `context`에 정의되지 않은 키 |

- `duplicates`에는 이미 저장된 `event_id`뿐 아니라 **같은 배치 안에서 반복된 `event_id`**도 포함된다(첫 건만 저장).
- `rejected[].message`는 고정 문구이며 입력값을 되풀이하지 않는다(1-6절).

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
| **마스킹 해제** (v0.2 이후 — 2-3 재검토 메모) | `UNMASK` | `count` + `context.reason`·`context.target` | 무엇을 열어봤는지가 감사의 핵심 |
| 보고서 export | `EXPORT` | `count` + `context.report_id`, 언마스킹 시 `context.reason` | 보고서가 마스킹 우회 경로가 되지 않게 |
| **소명 첨부 내려받기** (2026-10-02) | `READ` (`ACCESS_LOG`) | count 0 + `context.target = {"detection_id": N}` | 캡처에 개인정보가 있을 수 있음. 경로 변수 값은 원장에 남기지 않으므로 대상은 `target`으로. 코드는 `DOWNLOAD`를 섞지 않고 Argus 자체 기록 코드(LOGIN·READ·UNMASK·EXPORT) 안에서 |

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
- **멱등·중복 판정 (v0.3 — 2026-09-29 확정)**: 이 API는 `event_id`를 저장하지 않고 **`last_event_at` 기준**으로 판정한다. ①과 달리 이벤트가 매번 취급자 **상태 전체**(스냅샷)를 싣기 때문에, 같은 이벤트를 다시 적용해도 결과가 같아 event_id 없이 멱등성이 성립한다.
  1. **상태 스냅샷 upsert**: `(source_system, login_id)` 기준으로 없으면 생성, 있으면 덮어쓴다. `type`은 부수 효과(아래 2) 판단과 기록 구분에 쓰고, 최종 상태는 `handler` 필드 값으로 정한다.
  2. **판정**: `occurred_at > last_event_at`이면 적용 후 `accepted`, `occurred_at ≤ last_event_at`이면 **변경 없이 `duplicates`로 집계**한다. 이 `duplicates`에는 재전송된 같은 이벤트와 **늦게 도착한 옛 이벤트가 함께 포함**된다(둘을 구분하지 않음). relay 입장에서는 둘 다 "더 보낼 필요 없음"이라 후속 조치가 같다.
  3. **부수 효과도 상태 기준**: A5 계정은 "없으면 생성", 퇴직 반영은 "이미 `DISABLED`가 아니면 전환" — 여러 번 적용돼도 결과가 같게 한다.
  4. **발신 측 시각 정밀도**: 플랫폼은 `occurred_at`을 **마이크로초 정밀도**로 보낸다. 서로 다른 두 변경의 시각이 같으면 뒤의 것이 `duplicates`로 판정돼 유실되기 때문이다.
  - **기각한 대안**: `handler_event(event_id, login_id, type, occurred_at, received_at)` 테이블로 event_id를 저장 — 정확한 중복 판별과 Argus 쪽 수신 이력을 얻지만, ①취급자 정보의 원천은 플랫폼이고 권한 변경 이력은 플랫폼 `operator_permission_history`(§5③)가 담당하므로 Argus에 이력을 이중 보관할 실익이 작고 ②직원 개인정보 보관을 늘리지 않는 편이 최소처리 원칙에 맞다고 판단. 수신 이력이 필요해지면(예: 동기화 누락 추적) 이 테이블을 추가한다.
- **A5 계정 발급** (v0.4 확정): 재직(`ACTIVE`) 상태 이벤트 수신 시 연결된 계정이 없으면 Argus에 취급자용 로그인 계정(`role=HANDLER`, `argus_user.login_id = handler.login_id`)을 생성한다.
  - 초기 비밀번호는 **무작위 argon2id 해시**(사실상 로그인 불가) — 해시가 아닌 표식값을 넣으면 로그인 코드가 특수 처리해야 하므로 정상 형식을 쓴다. 실제 비밀번호는 **M4에서 관리 스크립트로 설정**한다.
  - 같은 `login_id`를 다른 Argus 계정(예: 정보보호 담당자)이 이미 쓰고 있으면 **생성하지 않고 경고 로그** — 남의 계정을 취급자 계정으로 바꿔치기하지 않는다.
  - **반대 순서 (2026-10-02)**: **플랫폼 백오피스 아이디 = Argus 아이디** 원칙(정책정의서 4-4)에 따라 담당자도 플랫폼 계정을 가지므로, 동기화가 먼저 돌면 담당자 아이디로 A5 계정(로그인 불가)이 먼저 생긴다. 이때 담당자 생성 명령(`create-officer`)은 **로그인 이력이 없는 A5 계정만 담당자로 전환**한다(명부 연결 유지 — 플랫폼에서 퇴직하면 Argus 담당자 계정도 막힘, `DISABLED`는 되살리지 않음). 실제로 쓰던 취급자 계정은 거부.
  - 퇴직 상태로 처음 들어온 취급자는 계정을 만들지 않는다.
- `HANDLER_TERMINATED` 수신 시 해당 A5 계정을 `DISABLED`로 전환한다. 진행 중인 소명 건은 담당자가 판단한다 — `DETECTED`·`REQUESTED`면 DISMISS(요청 취소), `REJECTED`면 ESCALATE 가능 (2026-10-01 정합화: 정책정의서 3-2 상태도에 `REQUESTED → DISMISSED` 추가로 "요청 후 제출 전 퇴직 시 건이 멈추는" 모순 해소).
- **재입사(`TERMINATED` → `ACTIVE`) 이벤트가 와도 `DISABLED` 계정을 자동 복구하지 않는다** (v0.4) — 권한 복구는 사람이 판단한다.
- **수신 검증** (v0.4): `occurred_at`은 ①과 같이 **미래 5분 초과 거부**(미래 시각이 들어오면 `last_event_at`이 미래로 밀려 그 시각까지의 정상 변경이 전부 `duplicates`로 무시됨) / `terminated_at`은 **미래 허용**(퇴직 예정 시각), 오프셋 필수 / 재직(`ACTIVE`)인데 `terminated_at`이 오거나, `HANDLER_TERMINATED`인데 재직 상태면 `INVALID_FIELD`(발신 측 버그를 조용히 적용하지 않음)
- **동시성**: 비교와 쓰기를 한 문장으로 처리한다(`INSERT ... ON CONFLICT ... DO UPDATE ... WHERE last_event_at < EXCLUDED.last_event_at`) — 조회 후 저장 방식은 사이에 동시 요청이 끼어들 수 있다.

### 3-2. 응답

①과 동일한 형식 (`accepted` / `duplicates` / `rejected`). rejected 코드는 ①의 체계(`MISSING_FIELD`·`INVALID_FIELD`·`INVALID_TIMESTAMP`)를 재사용하고, 추가 코드는 `TERMINATED_AT_REQUIRED`. `duplicates`의 의미는 3-1절 "멱등·중복 판정" 2번(재전송 + 늦게 도착한 옛 이벤트)이다.

---

## 4. 발신 측(플랫폼) 구현 규약

| 항목 | 규약 |
|---|---|
| 적재 | 관리자 라우트 요청 종료 시 미들웨어가 outbox에 적재 — **업무와 별도 트랜잭션** (실패한 시도도 남기기 위함) |
| topic | outbox 한 테이블에 `ACCESS_LOG` / `HANDLER` 두 topic을 함께 적재, relay가 topic별 엔드포인트로 전송 |
| 전송 주기 | relay가 수 초 간격으로 `PENDING` 건을 최대 100건씩 묶어 전송 |
| 순서 | 같은 topic 안에서 `created_at` 순으로 전송 (엄격한 순서 보장은 요구하지 않음 — 수신 측이 `occurred_at`·`last_event_at`으로 처리) |
| 금지 | 이름·이메일·연락처·카드번호 등 **원본 개인정보를 payload에 넣지 않는다** |
| **DB 접근 게이트웨이** (v0.5) | 2티어 기록은 게이트웨이가 **자체 버퍼**(플랫폼 outbox 아님 — 공용 계정으로 변조 가능)에 쌓고 자체 전송 루프로 보낸다. 출처는 `PLATFORM`(같은 HMAC 키) → 취급자 매칭이 3티어와 같다. 재시도·건별 판정·413 분할·스스로 버리지 않음은 이 표와 1-4·1-5절 그대로 |
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

- [x] A5 초기 계정 발급 → 무작위 해시로 생성, 비밀번호는 M4 관리 스크립트로 설정 (v0.4, 3-1절)
- [x] 2단계(`access_path=DB`) 확장 필드 → `context.query`·`context.row_count` 예약 (v0.2) → **v0.5에서 확정**: `query` 폐기, DB 키 9종(2-3절) (2026-10-05)
- [ ] DEAD 상태 이벤트 운영 알림 채널 (로그 / 이메일) — M2 구현은 ERROR 로그뿐, DEAD 재처리 도구 없음
- [ ] HMAC 비밀키 교체 절차의 운영 문서화
- [ ] HMAC 신·구 키 병행 허용(1-2절 #4) — Walking Skeleton 미구현(출처당 키 1개). 보안성 검토 단계 과제(아키텍처 설계서 8-6)
- [x] 요청 단위 오류 코드, 이벤트 수 초과 처리, 수신 측 형식 검증 → v0.3 (2026-09-29, 구현 M1)
