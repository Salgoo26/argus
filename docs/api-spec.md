# API 명세서 — 시스템 간 (플랫폼 ↔ Argus)

> 작성 2026-09-23 · 최종 개정 2026-10-10 (문서 버전 v0.6)
> 관련 문서: [아키텍처_설계서](architecture.md), [DB스키마](db-schema.md), [요구사항정의서](requirements.md), [정책정의서](policy.md), [액터별_플로우](actor-flows.md)
> 범위: 두 시스템의 **경계 계약**을 다룬다. 화면용 내부 API(argus-web ↔ argus-api 등)의 세부 형식은 코드로 정의하고 FastAPI OpenAPI(Swagger)로 자동 문서화한다. 다만 접속기록을 어떻게 남기는지, 누가 쓸 수 있는지, 어떤 오류 코드로 거부하는지가 점검 근거가 되는 화면 API는 7절에 요약한다.

| API | 용도 | 방향 |
|---|---|---|
| **① 접속기록 수집** | 플랫폼 Agent(미들웨어)가 생성한 접속기록을 Argus 원장으로 전달 | 플랫폼 relay worker → argus-api |
| **② 취급자 동기화** | 취급자 계정의 생성·변경·퇴직을 Argus에 반영 (소명 대상 지정, 퇴직자 룰, A5 로그인) | 플랫폼 relay worker → argus-api |
| **③ 헬스체크** | 배포 검증·전송 전 가용성 확인 | 배포 스크립트 / relay worker |
| **④ DB 구조 수신** | 게이트웨이가 플랫폼 DB의 테이블·컬럼 이름과 자료형을 Argus에 보냄 (보호 대상 등록부의 "미분류" 판정 기준) | DB 접근 게이트웨이 → argus-api |
| **⑤ 보호 대상 등록부 제공** | 게이트웨이가 DB 직접 기록의 데이터 유형·회원 식별 열을 판정할 표를 받아 감 | argus-api → DB 접근 게이트웨이 (게이트웨이가 조회) |

> 상용 솔루션으로 치면 **Agent ↔ 수집 서버 프로토콜**에 해당한다. Argus는 이 계약만 지키면 어떤 출처 시스템(2단계의 DB 접근 게이트웨이 포함)이든 받아들인다.

> 목차: 개정 내역 · 1 공통 규약 · 2 ① 접속기록 수집 · 3 ② 취급자 동기화 · 4 발신 측(플랫폼) 구현 규약 · 5 ③ 헬스체크 · 6 ④ DB 구조 수신 · ⑤ 보호 대상 등록부 제공 · 7 화면 API 요약 · 8 버전 관리 · 9 미결

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

### v0.3 개정 내역

| # | 수정 | 사유 |
|---|---|---|
| 1 | **이벤트 100건 초과 → `413 TOO_MANY_EVENTS`** (1-1·1-5절) | v0.2는 코드 미정. 400이면 relay가 배치 전체를 `DEAD` 처리하지만, 413이면 배치를 절반으로 쪼개 재전송 → 접속기록을 버리지 않는 쪽 |
| 2 | **요청 단위 오류 코드 세분** (1-5·1-6절) | v0.2는 예시 1개뿐. 401을 원인별(`UNKNOWN_SOURCE`·`INVALID_TIMESTAMP`·`INVALID_SIGNATURE`)로 나눠야 relay 알림에서 키 문제와 시각 오차를 구분해 사람이 고칠 수 있음(출처 목록은 비밀이 아님) |
| 3 | **rejected 코드 `INVALID_FIELD` 추가** (2-5절) | 형식·길이·타입 오류 등 코드표에 없던 필드 오류의 수용처 |
| 4 | **원본 개인정보 유입 차단 검증** (2-2절) | CLAUDE.md 절대 규칙 #3(원본 개인정보 전송 금지)을 발신 측 약속에만 맡기지 않고 **수신 측에서도 방어**. `subject.ids` 형식 제한, `request.path`의 쿼리스트링 거부 등 |
| 5 | **Argus 전용 코드값은 외부 출처에서 거부** (2-3·2-5절) | `EXPORT`·`UNMASK`·`ACCESS_LOG`의 "(Argus 전용)" 표기를 수신 검증으로 구체화 — 외부 출처가 Argus 내부 행위를 사칭하지 못하게 |
| 6 | **② 취급자 동기화의 멱등·중복 판정 규칙** (3-1절) | `handler`에 event_id 저장 칸이 없어 판정 기준이 미정이었음. 상태 스냅샷 + `last_event_at` 기준으로 확정, event_id 저장 테이블은 기각 |

### v0.4 개정 내역

| # | 수정 | 사유 |
|---|---|---|
| 1 | **relay의 401 처리 해석** (1-5절) | "재시도는 멈추고 알림, 사람이 고치면 자동 재개"는 완전히 멈추면 자동 재개가 불가능해 문구가 상충 → **다른 실패와 같은 지수 백오프(1분 → 2 → 4 … 최대 1시간)로 재확인 + 매번 ERROR 로그** — 키를 고친 직후 빨리 재개되게 하기 위함 |
| 2 | **`request.path` = 라우트 템플릿** (2-2절) | 실제 URL을 보내면 경로 변수에 실린 값(검색어·이름 등)이 원장에 남음 |
| 3 | **로그인 실패·로그아웃 기록 규칙** (2-4절) | 존재하지 않는 ID의 로그인 실패는 미기록(ID 칸에 잘못 입력된 비밀번호가 append-only 원장에 영구 저장되는 것 방지), 로그아웃은 제외 |
| 4 | **정보주체 기록 전 실패 → `subject.count = 0`** (2-2·2-4절) | 입력 검증 오류 등 대상이 정해지기 전에 실패한 요청의 건수 규칙이 없었음 |
| 5 | **A5 초기 계정** (3-1절) | 무작위 argon2id 해시로 생성(로그인 불가), 재입사 시 자동 복구 안 함, login_id 충돌 시 생성 안 함 |
| 6 | ② 수신 검증 세부 (3-1·3-2절) | `occurred_at` 미래 5분 초과 거부, `terminated_at` 미래 허용, 상태·유형 불일치 거부 |

### v0.5 개정 내역 (2티어 DB 직접 접근 설계)

| # | 수정 | 사유 |
|---|---|---|
| 1 | **`access_path=DB` 기록 규칙** (2-2절) | DB 접근 게이트웨이(아키텍처 설계서 3-4)가 보내는 기록의 필드별 의미 정의 |
| 2 | **`context` DB 키 확정** (2-3절) — `db_user`·`sql_normalized`·`tables`·`columns`·`row_count`·`raw_ref`·`raw_fingerprint`·`subject_unresolved`·`token_id` | v0.2에 예약한 `query`(실행된 SQL)는 **원문이라 폐기** — 매개변수·리터럴에 개인정보가 실림(절대 규칙 #3). 원문은 게이트웨이 저장소에만 |
| 3 | DB 이벤트 예시 (2-4절) | 회원번호 추출·정보주체 미특정·연결 인증 |
| 4 | **원문 SQL 유입 차단 검증** (2-2절) | `sql_normalized`에 문자열 리터럴(`'`)이 있으면 거부 — 정규화가 깨졌거나 원문이 실린 것 |
| 5 | **연결 인증(`LOGIN`)의 원문 범위** (2-2절) | `raw_ref` 필수 규칙이 `LOGIN`에도 적용되는지 불명확했음 → 접속 정보만 저장, 토큰 값 제외, 미존재 아이디 실패는 원문 저장소에도 미기록 |
| 6 | **DB 접속 토큰 발급 미기록** (2-4절) | 개인정보 처리가 아님 — 발급 추적은 `db_access_token` + `LOGIN`의 `token_id` |
| 7 | **달러 인용 거부** (2-2절) | `sql_normalized`에 `$$…$$`·`$tag$…$tag$`가 있으면 원문 유입으로 보고 거부 |
| 8 | **기록 제외 범위** (2-2절) | 사용자 테이블을 참조하지 않는 문장은 미전송(원문 저장소엔 남김), 단 사용자 함수·`DO`·`CALL`은 전송 |

### v0.6 개정 내역 (v0.1 보강)

| # | 수정 | 사유 |
|---|---|---|
| 1 | 수행업무 `LOGOUT` 추가 (2-2·2-3·2-4·2-7절) — 인증된 사용자의 로그아웃만 기록, 정보주체 없음 | 안내서 FAQ 147. v0.4의 "로그아웃 미기록"을 바꿈. 원장 CHECK는 DB스키마 3-2(0014) |
| 2 | DB 직접 기록의 회원번호를 결과 열에서 추출 (2-2·2-4절) | 결과 열의 원본 테이블·열 번호가 회원 식별 열이면 그 값을 정보주체로. SQL 조건·매개변수에서는 추출하지 않음. 회원 열이 없거나 결과가 없으면 미특정 |
| 3 | `context.registry_version` 추가 (2-3절) | 게이트웨이가 데이터 유형 판정에 쓴 보호 대상 등록부 버전. DB 키 10종 |
| 4 | ④ DB 구조 수신 · ⑤ 보호 대상 등록부 제공 신설 (6절) | 데이터 유형 판정 표를 게이트웨이 코드의 고정 표에서 Argus 등록부로 옮김(DB스키마 3-8) |
| 5 | Argus 자체 기록 특례 갱신 (2-7절) | 검색 조건 키 기록, 취급자 본인 접속기록, 본인 건 처리 거부 시도의 `FAILURE` 기록, 기록 제외 라우트(알림·웹 푸시·계정·보호 대상)와 그 증적 |
| 6 | 취급자 동기화의 계정 이력 (3-1절) | A5 계정 생성·퇴직 비활성화를 Argus 계정 이력(`argus_user_history`)에 시스템 처리로 남김. 재활성화는 담당자가 사유와 함께 |
| 7 | 화면 API 요약 신설 (7절) | 탐지건·접속기록 검색 POST 통일, 알림·웹 푸시, 소명 기한, 보고서 기준점, 본인 건 차단(`SELF_REVIEW_FORBIDDEN`), 플랫폼 역할별 403·계정 관리·관리자 검색 |
| 8 | 절 번호 변경 | 6절(게이트웨이 ↔ Argus)·7절(화면 API) 신설로 버전 관리 8절, 미결 9절 |

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
4. 비밀키는 VM `.env`에만 보관(레포 미포함). 키 교체 시 신·구 키를 일정 기간 병행 허용(`v1` 접두어로 버전 구분) — **v0.1은 미구현(출처당 키 1개).** 7절 미결 참조

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
| `401` | 인증 실패 (`UNKNOWN_SOURCE`, `INVALID_TIMESTAMP`, `INVALID_SIGNATURE`) | **다른 실패와 같은 지수 백오프(1분 → 2 → 4 … 최대 1시간)로 재확인 + 매번 ERROR 로그**(코드를 포함해 원인 구분). 사람이 키·시각 설정을 고치면 다음 재확인 때 자동 재개 (v0.4) | **`PENDING` 유지** |
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
| `subject.type` | enum | 조건부 | 정보주체 유형. 현재 `MEMBER`만. **`action`이 `LOGIN`·`LOGOUT`일 때만 생략 가능** | **§2 3호 처리한 정보주체 정보** |
| `subject.ids` | string[] | 조건부 | 처리한 정보주체의 **내부 PK**. 이름·이메일 등 원본 정보는 절대 보내지 않음. 최대 1,000개 | 동일 |
| `subject.count` | int | 조건부 | 처리한 정보주체 수 (목록 조회·다운로드 건수). **정보주체를 기록하기 전에 실패한 요청은 0** (v0.4) | 동일 / 대량 룰 판정 |
| `subject.truncated` | bool | - | `ids`가 1,000개를 넘어 잘렸는지 (`count`는 항상 전체 건수) | |
| `access_path` | enum | ✅ | `APP`(관리자 화면 경유) / `DB`(**DB 접근 게이트웨이 경유** 직접 접근 — 아키텍처 설계서 3-4, v0.5). DB 기록의 필드 규칙은 아래 "`access_path=DB` 기록 규칙" | 요구사항정의서 1-2절 |
| `data_category` | enum | ✅ | 처리한 데이터 유형 (2-3절) | 결제수단 조회 룰 등 |
| `request.method` / `request.path` | string | - | 호출된 관리자 기능. **`path`는 실제 URL이 아니라 라우트 템플릿**(예: `/admin/members/{member_id}`) — 경로 변수의 값이 원장에 남지 않게 (v0.4) | 수행업무 재구성 근거 |
| `request.query_keys` | string[] | - | 검색 조건의 **키 이름만** (값은 개인정보일 수 있어 제외). 관리자 검색은 조건을 요청 본문으로 받고 실제로 넣은 조건의 키 이름을 정렬해 싣는다(2-4절) | 최소수집 |
| `result` | enum | ✅ | `SUCCESS` / `FAILURE` (실패한 시도도 기록) | 아키텍처 설계서 3-3절 |
| `context` | object | - | 부가 정보 (2-3절 하단) | |

**수신 측 형식 검증** (v0.3 — 위반 시 해당 이벤트 `rejected`, 코드는 괄호)

| 필드 | 규칙 | 목적 |
|---|---|---|
| `event_id` | UUID 형식 (`INVALID_FIELD`) | |
| `actor.login_id` | 공백·제어문자 없는 출력 가능 ASCII 1~64자 (`INVALID_FIELD`) | |
| `occurred_at` | ISO 8601 + **오프셋 필수**, 미래 5분 초과 불가 (`INVALID_TIMESTAMP`) | 시각 모호성 제거 |
| `subject` | `LOGIN`·`LOGOUT` 외에는 필수 (`SUBJECT_REQUIRED`), `type`은 `MEMBER`만, `ids`·`count` 중 하나 이상 필수 | §2 3호 |
| **`subject.ids`** | 각 원소 **`[A-Za-z0-9_-]{1,64}`만 허용**, 최대 1,000개 (`INVALID_FIELD`) | **원본 개인정보 유입 차단** — 이메일(`@`·`.`), 이름(공백·한글), 전화번호 형식 등이 식별값 자리에 들어오면 거부 |
| `subject.count` | 0 이상 정수, **`ids` 개수 이상** (`INVALID_FIELD`). 생략 시 `ids` 개수 | 건수 축소 기록 방지 |
| **`request.path`** | `/`로 시작, 255자 이하, **`?`·`#` 포함 불가** (`INVALID_FIELD`) | 쿼리스트링에 실린 검색값(개인정보)이 경로에 섞여 들어오는 것 차단 |
| `request.method` | 표준 HTTP 메서드 | |
| **`request.query_keys`** | 최대 50개, 각 원소 **키 이름 형식** `[A-Za-z0-9_.\[\]-]{1,64}`만 (`INVALID_FIELD`) | 키 대신 값이 실려 오는 것 차단 |
| `context` 값 | `reason` 1~500자, `ticket_id` 1~64자, `report_id`·`row_count` 정수, `target`은 `{"detection_id": int}` 또는 `{"access_log_id": int}` (`INVALID_FIELD`) | |
| **`context` DB 키** (v0.5) | 2-3절 DB 키는 **`access_path=DB`일 때만** 허용(APP 기록에 오면 `UNKNOWN_CONTEXT_KEY`). DB 기록은 `db_user`·`raw_ref`·`raw_fingerprint` 필수, `LOGIN` 외에는 `sql_normalized`·`row_count`도 필수 (`MISSING_FIELD`). 형식: `db_user` `[A-Za-z0-9_]{1,63}` / `sql_normalized` 1~4,000자, **작은따옴표(`'`)·달러 인용(`$$…$$`, `$tag$…$tag$`) 포함 불가**(PostgreSQL은 달러 인용으로도 문자열 리터럴을 쓰므로 따옴표 검사만으론 원문이 통과. 정규화 SQL엔 `$1` 같은 자리표시만 남아 정상 기록엔 영향 없음) / `tables`·`columns` 각 최대 50·200개, 원소는 `[A-Za-z0-9_."]{1,128}` / `raw_ref` `[A-Za-z0-9:_-]{1,128}` / `raw_fingerprint` `sha256:` + 소문자 hex 64자 / `subject_unresolved` bool / `token_id` UUID / `registry_version` `builtin` 또는 `r` + 숫자 (`INVALID_FIELD`) | **원문 SQL 유입 차단** — 정규화 SQL은 리터럴을 `$1`…로 바꾸므로 따옴표가 남을 수 없다. 남았다면 정규화 실패이거나 원문이 실린 것 |

> 절대 규칙 #3(원본 개인정보 전송 금지)은 발신 측 약속이다. 수신 측 검증은 그 약속이 깨졌을 때를 대비한 **두 번째 방어선**이며, 형식만으로 모든 개인정보를 걸러낼 수는 없다(예: 숫자로만 된 값). 1차 책임은 여전히 발신 측에 있다.

**`access_path=DB` 기록 규칙** (v0.5 — 발신 측: DB 접근 게이트웨이, 아키텍처 설계서 3-4)

| 필드 | DB 기록에서의 의미 |
|---|---|
| `event_id` | 게이트웨이가 문장(또는 연결 인증) 1건마다 생성. 게이트웨이 원문 저장소의 키와 같다 |
| `occurred_at` | 문장 실행 시작 시각 (`LOGIN`은 인증 시각) |
| `actor.login_id` | **토큰의 주인 = 플랫폼 관리자 아이디**. DB 계정(공용)이 아니다 → 3티어 기록과 같은 아이디라 취급자 명부·탐지 그룹이 그대로 맞는다 |
| `client_ip` | 게이트웨이가 TCP 연결에서 직접 본 DB 툴의 IP(프록시 헤더 없음) |
| `action` | 문장 종류 → 수행업무 (아키텍처 설계서 3-4 "수행업무 매핑"). 연결 인증은 `LOGIN` |
| `subject` | `type=MEMBER`. 결과 열 중 보호 대상 등록부의 회원 식별 열(결과 열 설명이 주는 원본 테이블 OID·열 번호로 판별)에서 읽은 회원번호를 `ids`(중복 제거, 1,000개 상한), `count`는 고유 회원 수. SQL을 해석하지 않으므로 조건(`WHERE`)·매개변수에서는 추출하지 않는다. 결과에 회원 열이 있고 0행이면 `ids` 비움 + `count = 0`. **정보주체 미특정**(회원 열이 없는 조회, 식·뷰·함수 결과, 결과를 돌려주지 않는 UPDATE·DELETE·COPY, 값 해석 실패)이면 `ids` 비움 + `count = row_count` + `context.subject_unresolved = true` (DB스키마 3-2) |
| `data_category` | 문장이 건드린 테이블 중 가장 민감한 유형 (아키텍처 설계서 3-4 "데이터 유형"). 테이블의 유형은 보호 대상 등록부 기준이고(6절), 판정에 쓴 등록부 버전을 `context.registry_version`에 싣는다 |
| `request` | 생략 (HTTP 요청이 아님). 대신 `context.sql_normalized`·`tables`·`columns` |
| `result` | DB가 오류를 돌려주거나 실행 중 연결이 끊기면 `FAILURE` |

- 연결 인증 실패(`LOGIN` + `FAILURE`)는 **존재하는 플랫폼 아이디일 때만** 기록한다(2-4절 v0.4 규칙과 동일 — 아이디 칸에 잘못 입력된 값이 원장에 영구 저장되는 것 방지).
- **연결 인증(`LOGIN`)도 원문 저장소에 남긴다** (`raw_ref`·`raw_fingerprint`가 DB 기록 전체에 필수이므로). 원문 레코드에는 **접속 정보만** 담는다: 아이디, 접속 IP, TLS 여부, 접속 파라미터(`database`·`application_name`), 결과·실패 사유, 서명이 유효했으면 토큰 `jti`. **토큰 값(비밀번호)은 담지 않는다.** 존재하지 않는 아이디의 실패는 Argus에 보내지 않을 뿐 아니라 **원문 저장소에도 남기지 않는다**(같은 이유 — 아이디 칸에 잘못 입력된 비밀번호 등).
- **사용자 테이블을 하나도 참조하지 않는 문장**(시스템 카탈로그만 읽는 문장, `SELECT version()`, `pg_get_keywords()` 같은 시스템 함수 FROM 등 DB 툴의 메타데이터·상태 조회)·트랜잭션 제어·`SET`·`SHOW`는 보내지 않는다(원문 저장소에는 남김). **단 사용자 스키마의 함수·프로시저 호출과 `DO`·`CALL`은 보낸다** — 테이블 이름 없이도 내부에서 개인정보를 처리할 수 있어서다(`subject_unresolved = true`, 사용자 함수 → `READ`, `CALL`·`DO` → `UPDATE`, 데이터 유형 `MEMBER_BASIC`, 아키텍처 설계서 3-4 "수행업무 매핑"). **DDL·DCL은 v0.1에서 보내지 않는다**(게이트웨이 원문 저장소에만 — 수행업무 코드가 없음, v0.2에서 명령어 통제와 함께 정의).

### 2-3. 코드값

**action (수행업무)** — 안내서의 예시(검색·열람·조회·입력·수정·삭제·출력·다운로드)를 묶어 정의

| 값 | 의미 |
|---|---|
| `LOGIN` | 개인정보처리시스템 로그인 |
| `LOGOUT` | 로그아웃 (v0.6). 인증된 사용자의 요청만 기록 |
| `READ` | 조회·열람·검색 (목록·상세 모두) |
| `CREATE` | 입력 |
| `UPDATE` | 수정 |
| `DELETE` | 삭제 |
| `DOWNLOAD` | 파일 다운로드·출력 |
| `EXPORT` | (Argus 전용) 점검 보고서 export |
| `UNMASK` | (Argus 전용) 정보주체 식별값 마스킹 해제 — `context.reason` 필수. **재검토 메모**: Argus 해제 기능은 v0.2 이후로 보류됐고, 보안성 검토 후 **플랫폼 관리자 화면에 표시제한 + 사유 입력 해제**를 구현할 예정 — 그때 플랫폼 출처의 해제 행위를 받기 위해 `UNMASK`의 외부 출처 허용 또는 `READ` + `context.reason`으로 명세를 바꿔야 함 |

> **(Argus 전용)** 코드값(`EXPORT`·`UNMASK`, 아래 `ACCESS_LOG`)은 Argus 자체 기록(2-7절)에서만 쓴다. **외부 출처가 보내면 거부**한다(`INVALID_ACTION` / `INVALID_CATEGORY`) — 외부 시스템이 Argus 내부 행위를 사칭한 기록을 원장에 남기지 못하게 하기 위함 (v0.3).

**data_category (데이터 유형)**

| 값 | 대상 |
|---|---|
| `MEMBER_BASIC` | 회원 기본정보 (이름·연락처·주소 등) |
| `PAYMENT` | 결제수단 정보 (카드번호·계좌번호) — 민감도 높음 |
| `ORDER` | 주문·배송 정보 |
| `INQUIRY` | 1:1 문의 |
| `ACCESS_LOG` | (Argus 전용) 접속기록·탐지건 자체 |
| `NONE` | 로그인·로그아웃 등 데이터 처리 없음 |

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
| `registry_version` | string | (DB, v0.6) 데이터 유형 판정에 쓴 보호 대상 등록부 버전. `r{등록 이력 마지막 id}`, 등록부를 한 번도 받지 못해 게이트웨이 고정 표를 쓴 경우 `builtin` (6절) |

### 2-4. 이벤트 예시 (플랫폼 관리자 행위별)

| 행위 (액터별 플로우) | action | data_category | subject |
|---|---|---|---|
| 관리자 로그인 (F-02/03 #1) | `LOGIN` | `NONE` | 생략 |
| 회원 목록 조회, 20건 표시 (F-03 #2) | `READ` | `MEMBER_BASIC` | ids 20개, count 20 |
| 회원 상세 조회 (F-02 #4) | `READ` | `MEMBER_BASIC` | ids 1개, count 1 |
| 문의 상세 확인 (F-02 #3) | `READ` | `INQUIRY` | ids 1개 + `context.ticket_id` |
| 주문 목록 조회 | `READ` | `ORDER` | 표시된 주문의 회원(중복 제거) |
| 회원 상세 조회 — 환불계좌 끝 4자리 포함 | `READ` | `MEMBER_BASIC` | ids 1개, count 1 |
| **환불계좌 전체 보기** (요구사항의 "주문·결제 조회") | `READ` | `PAYMENT` | ids 1개, count 1 — 결제수단 조회 룰(HIGH)로 항상 탐지 |
| 1:1 문의 목록 | `READ` | `INQUIRY` | 표시된 작성자(중복 제거), 본문 제외 |
| 1:1 문의 답변 | `UPDATE` | `INQUIRY` | ids 1개 + `context.ticket_id` (한 번만 — 이미 답변이면 409, 실패도 기록) |
| 회원 목록 다운로드 120건 (F-03 #5) | `DOWNLOAD` | `MEMBER_BASIC` | ids 120개, count 120 |
| 관리자 로그인 실패 — **존재하는 계정**(비밀번호 오류·잠김·퇴직자) (v0.4) | `LOGIN` | `NONE` | 생략, `result: FAILURE` |
| 관리자 로그인 실패 — **존재하지 않는 ID** (v0.4) | **기록하지 않음** | | |
| 관리자 로그아웃 (v0.6) | `LOGOUT` | `NONE` | 생략. 로그인한 관리자만 — 쿠키가 없거나 만료된 요청은 행위자가 없어 기록하지 않음 |
| 회원·주문·문의 검색 (`POST /admin/{members,orders,inquiries}/search`, v0.6) | `READ` | `MEMBER_BASIC` / `ORDER` / `INQUIRY` | 결과로 화면에 보인 회원 PK 전부(0건이면 count 0) + `request.query_keys`에 조건 키 이름만 |
| 역할에 없는 기능 요청(예: 마케팅의 주문 조회 — 7-2절) (v0.6) | 해당 행위 | 해당 유형 | count 0, `result: FAILURE` |
| **DB 접속 토큰 발급** | **기록하지 않음** (명시적 제외, `@access_log_exempt`) | | |
| 정보주체를 기록하기 전에 실패한 요청(입력 검증 오류 400 등) (v0.4) | 해당 행위 | 해당 유형 | count 0, `result: FAILURE` |

**DB 직접 접근 (게이트웨이, `access_path=DB`, v0.5)** — `context` 공통: `db_user`, `raw_ref`, `raw_fingerprint`, `token_id`

| 행위 (DB 툴) | action | data_category | subject / context |
|---|---|---|---|
| 게이트웨이 접속(토큰 인증 성공) | `LOGIN` | `NONE` | 생략 |
| 접속 실패 — 존재하는 아이디, 토큰 오류·만료·퇴직 | `LOGIN` | `NONE` | 생략, `result: FAILURE` |
| `SELECT * FROM member WHERE email = 'a@x.com'` | `READ` | `MEMBER_BASIC` | 결과의 `member.id` 열에서 ids 1개 · `sql_normalized: …WHERE email = $1` · `row_count: 1` |
| `SELECT name, phone FROM member LIMIT 50` | `READ` | `MEMBER_BASIC` | **미특정** — ids 비움, count 50, `subject_unresolved: true` |
| `SELECT o.member_id, r.bank_name FROM orders o JOIN refund_account r …` | `READ` | `PAYMENT` | 결과의 `member_id`에서 ids · `tables: [orders, refund_account]` |
| `UPDATE member SET phone = $1 WHERE id = $2` | `UPDATE` | `MEMBER_BASIC` | **미특정** — 결과를 돌려주지 않는 변경, count = 영향 행 수 (매개변수에서는 추출하지 않음) |
| `SELECT id FROM member WHERE email = $1` → 0행 | `READ` | `MEMBER_BASIC` | 회원 열이 있고 0행 — ids 비움, count 0, `subject_unresolved: false` |
| `DELETE FROM inquiry WHERE created_at < $1` | `DELETE` | `INQUIRY` | **미특정** — count = 삭제 행 수 |
| `SELECT` 실행 중 DB 오류 | `READ` | 해당 유형 | count 0, `result: FAILURE` |

- **존재하지 않는 ID의 로그인 실패를 기록하지 않는 이유**(v0.4): ID 칸에 비밀번호를 잘못 입력하는 일이 흔한데, 그 값이 append-only 원장에 영구 저장된다. 게다가 그 값은 §2 3호의 "개인정보취급자 식별자"도 아니다. 대가로 존재하지 않는 ID 대입 시도는 Argus에 보이지 않는다(아키텍처 설계서 8-6 과제).
- **로그아웃을 기록하는 이유**(v0.6): 안내서 FAQ 147에 따라 로그인과 함께 접속 세션의 끝을 남긴다(v0.4에서는 개인정보 처리가 없다는 이유로 제외했다). 발신 측은 `@access_log(action="LOGOUT", data_category="NONE")`로 표시한다. 행위자를 확인하느라 재발급한 세션 쿠키는 로그아웃 응답에 싣지 않는다. 표시가 없는 관리자 라우트는 기동 자체가 거부되는 규칙(아키텍처 설계서 3-2)은 그대로다. DB 세션 종료(2티어)는 기록하지 않는다.
- **DB 접속 토큰 발급을 제외하는 이유**: 개인정보 처리가 아니다. 발급 추적은 플랫폼 `db_access_token`(DB스키마 4절) + 게이트웨이 `LOGIN` 기록의 `context.token_id`로 충분하다. 기각: `CREATE` + `NONE`으로 기록 — 원장에 한 번 더 남는 이점은 있으나 개인정보 처리가 아닌 행위로 범위가 넓어짐.
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
| `SUBJECT_REQUIRED` | `LOGIN`·`LOGOUT` 외 행위인데 subject 누락 |
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
- EVENT 탐지건은 (룰, 출처, 경로, 취급자, 한국 날짜)에 데이터 유형·행위 구분을 더한 키로 묶는다. 하위 기록이 붙은 시각(`detection_log.attached_at`)이 남아, 소명 제출 뒤에 붙은 기록을 따로 셀 수 있다(DB스키마 3-4·3-7).
- 탐지건 생성·소명 요청과 같은 트랜잭션에서 화면 알림을 만들고, 웹 푸시는 그 뒤에 보낸다(7-1절 "알림").

**미동기화 취급자의 로그 (v0.2 신설)**

`actor.login_id`가 아직 `handler`에 없어도 이벤트는 **정상 수락**한다. 접속기록은 원문 그대로 저장되고, 이후 취급자 동기화가 도착하면 `(source_system_id, actor_login_id)` 조인으로 자연히 연결된다. 단, 취급자 속성이 필요한 룰(퇴직 여부 등)은 명부에 없는 계정을 판정 불가로 보고 **탐지한다(fail-closed)** — DB스키마 3-6절.

### 2-7. Argus 자체 접속기록의 특례 (v0.2 신설)

Argus 내부 행위(LOG-17)도 같은 규격으로 기록하지만, **outbox를 경유하지 않고** argus-api 미들웨어가 직접 append한다(자기 자신에게 HTTP 호출을 하지 않음). 이때 정보주체 기록 규칙이 다르다.

| Argus 행위 | action | subject | 이유 |
|---|---|---|---|
| 로그인·로그아웃 | `LOGIN` / `LOGOUT` | 생략 | 로그아웃은 인증된 사용자만 (v0.6) |
| 탐지건·접속기록 조회 | `READ` | **`count`만 기록, `ids`는 비움** | 조회할 때마다 회원 PK를 Argus 접속기록에 다시 쌓으면 식별자가 중복 축적되어 최소처리 원칙에 반한다 |
| 검색 — 탐지건(`POST /api/detections/search`)·접속기록(`POST /api/access-logs/search`) | `READ` (`ACCESS_LOG`) | `count`만 + `request.query_keys`(본문에 실제로 넣은 조건의 키 이름, 정렬) | 검색어 값(아이디·회원번호·IP)은 기록하지 않음. 조건을 URL이 아니라 본문으로 받아 서버 접근 로그·방문 기록에도 남지 않게 함 |
| 취급자 본인 접속기록 (같은 검색 API, v0.6) | `READ` (`ACCESS_LOG`) | `count`만 + `request.query_keys` | 서버가 행위자를 본인으로 고정. 남의 아이디나 출처 `ARGUS`를 요청하면 403이고 그 시도도 `FAILURE`로 남음 |
| 본인 건 상태 전이 거부 (v0.6) | `UPDATE` (`ACCESS_LOG`) | count 0 + `context.target = {"detection_id": N}`, `result: FAILURE` | 성공한 상태 전이는 상태 이력이 증적이라 자체 기록에서 제외하지만, 규칙으로 거부된 시도는 상태 이력에 남지 않으므로 자체 기록에 남긴다. 입력한 사유 문장은 싣지 않음 |
| **마스킹 해제** (v0.2 이후 — 2-3 재검토 메모) | `UNMASK` | `count` + `context.reason`·`context.target` | 무엇을 열어봤는지가 감사의 핵심 |
| 보고서 export | `EXPORT` | `count` + `context.report_id`, 언마스킹 시 `context.reason` | 보고서가 마스킹 우회 경로가 되지 않게 |
| **소명 첨부 내려받기** | `READ` (`ACCESS_LOG`) | count 0 + `context.target = {"detection_id": N}` | 캡처에 개인정보가 있을 수 있음. 경로 변수 값은 원장에 남기지 않으므로 대상은 `target`으로. 코드는 `DOWNLOAD`를 섞지 않고 Argus 자체 기록 코드(LOGIN·LOGOUT·READ·UPDATE·UNMASK·EXPORT) 안에서 |

**기록 제외 라우트와 그 증적** — 정보주체를 처리하지 않는 Argus 화면 API는 `@access_log_exempt("사유")`로 명시적으로 제외한다. 대신 각자의 이력이 증적이다.

| 라우트 | 제외 사유 | 증적 |
|---|---|---|
| 탐지건 상태 전이(요청·취소·제출·승인·반려·에스컬레이션) 성공 | 상태 변경 자체는 정보주체 처리가 아님 | `detection_status_history` (거부된 본인 건 시도는 위 표처럼 `FAILURE`) |
| 룰 목록·상세·생성·수정·켜기·끄기 | 룰 정의는 개인정보가 아님 | `detection_rule_history` |
| 알림 목록·읽음 (`/api/notifications*`) | 본인 알림(탐지건 번호·룰 이름·심각도)만, 30초마다 확인해 원장을 채우지 않게 | 알림이 가리키는 탐지건을 열면 그 조회가 `READ`로 남음 |
| 웹 푸시 설정 (`/api/push/*`) | 본인 브라우저 설정 | — |
| 계정 관리 (`/api/users/*`) | 정보주체 처리가 아님 | `argus_user_history` (DB스키마 3-1) |
| 보호 대상 등록부 (`/api/protection*`) | 테이블·컬럼 분류만 다룸 | `protected_column_history` (DB스키마 3-8) |

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
- **멱등·중복 판정 (v0.3)**: 이 API는 `event_id`를 저장하지 않고 **`last_event_at` 기준**으로 판정한다. ①과 달리 이벤트가 매번 취급자 **상태 전체**(스냅샷)를 싣기 때문에, 같은 이벤트를 다시 적용해도 결과가 같아 event_id 없이 멱등성이 성립한다.
  1. **상태 스냅샷 upsert**: `(source_system, login_id)` 기준으로 없으면 생성, 있으면 덮어쓴다. `type`은 부수 효과(아래 2) 판단과 기록 구분에 쓰고, 최종 상태는 `handler` 필드 값으로 정한다.
  2. **판정**: `occurred_at > last_event_at`이면 적용 후 `accepted`, `occurred_at ≤ last_event_at`이면 **변경 없이 `duplicates`로 집계**한다. 이 `duplicates`에는 재전송된 같은 이벤트와 **늦게 도착한 옛 이벤트가 함께 포함**된다(둘을 구분하지 않음). relay 입장에서는 둘 다 "더 보낼 필요 없음"이라 후속 조치가 같다.
  3. **부수 효과도 상태 기준**: A5 계정은 "없으면 생성", 퇴직 반영은 "이미 `DISABLED`가 아니면 전환" — 여러 번 적용돼도 결과가 같게 한다.
  4. **발신 측 시각 정밀도**: 플랫폼은 `occurred_at`을 **마이크로초 정밀도**로 보낸다. 서로 다른 두 변경의 시각이 같으면 뒤의 것이 `duplicates`로 판정돼 유실되기 때문이다.
  - **기각한 대안**: `handler_event(event_id, login_id, type, occurred_at, received_at)` 테이블로 event_id를 저장 — 정확한 중복 판별과 Argus 쪽 수신 이력을 얻지만, ①취급자 정보의 원천은 플랫폼이고 권한 변경 이력은 플랫폼 `operator_permission_history`(§5③)가 담당하므로 Argus에 이력을 이중 보관할 실익이 작고 ②직원 개인정보 보관을 늘리지 않는 편이 최소처리 원칙에 맞다고 판단. 수신 이력이 필요해지면(예: 동기화 누락 추적) 이 테이블을 추가한다.
- **A5 계정 발급** (v0.4 확정): 재직(`ACTIVE`) 상태 이벤트 수신 시 연결된 계정이 없으면 Argus에 취급자용 로그인 계정(`role=HANDLER`, `argus_user.login_id = handler.login_id`)을 생성한다.
  - 초기 비밀번호는 **무작위 argon2id 해시**(사실상 로그인 불가) — 해시가 아닌 표식값을 넣으면 로그인 코드가 특수 처리해야 하므로 정상 형식을 쓴다. 실제 비밀번호는 **관리 스크립트로 설정**한다.
  - 같은 `login_id`를 다른 Argus 계정(예: 정보보호 담당자)이 이미 쓰고 있으면 **생성하지 않고 경고 로그** — 남의 계정을 취급자 계정으로 바꿔치기하지 않는다.
  - **반대 순서**: **플랫폼 백오피스 아이디 = Argus 아이디** 원칙(정책정의서 4-4)에 따라 담당자도 플랫폼 계정을 가지므로, 동기화가 먼저 돌면 담당자 아이디로 A5 계정(로그인 불가)이 먼저 생긴다. 이때 담당자 생성 명령(`create-officer`)은 **로그인 이력이 없는 A5 계정만 담당자로 전환**한다(명부 연결 유지 — 플랫폼에서 퇴직하면 Argus 담당자 계정도 막힘, `DISABLED`는 되살리지 않음). 실제로 쓰던 취급자 계정은 거부.
  - 퇴직 상태로 처음 들어온 취급자는 계정을 만들지 않는다.
- **계정 이력** (v0.6): 동기화로 A5 계정을 만들면 `argus_user_history`에 `GRANT`(사유 "취급자 명부 동기화 — 플랫폼 계정 생성"), 퇴직으로 비활성화하면 `REVOKE`(사유 "취급자 명부 동기화 — 플랫폼 퇴직")를 처리자 없이(시스템) 남긴다(DB스키마 3-1, 고시 §5①③). 플랫폼 쪽 원천 이력은 `operator_permission_history`이고, 플랫폼 계정·권한 화면(7-2절)의 부여·변경·말소가 같은 트랜잭션에서 이 이벤트를 outbox에 넣는다. 역할(role)은 보내지 않는다 — Argus 역할은 Argus 계정 관리에서 정한다.
- `HANDLER_TERMINATED` 수신 시 해당 A5 계정을 `DISABLED`로 전환한다. 진행 중인 소명 건은 담당자가 판단한다 — `DETECTED`·`REQUESTED`면 DISMISS(요청 취소), `REJECTED`면 ESCALATE 가능 (정책정의서 3-2 상태도의 `REQUESTED → DISMISSED` — 요청 후 제출 전에 퇴직해도 건이 멈추지 않는다).
- **재입사(`TERMINATED` → `ACTIVE`) 이벤트가 와도 `DISABLED` 계정을 자동 복구하지 않는다** (v0.4) — 권한 복구는 사람이 판단한다. 담당자가 `POST /api/users/{id}/enable`(사유 필수, 명부에서 재직 중일 때만)로 재활성화하고 `RESTORE` 이력이 남는다(7-1절).
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
| **DB 접근 게이트웨이** (v0.5) | 2티어 기록은 게이트웨이가 **자체 버퍼**(플랫폼 outbox 아님 — 공용 계정으로 변조 가능)에 쌓고 자체 전송 루프로 보낸다. 출처는 `PLATFORM`(같은 HMAC 키) → 취급자 매칭이 3티어와 같다. 재시도·건별 판정·413 분할·스스로 버리지 않음은 이 표와 1-4·1-5절 그대로. 보호 대상 등록부 동기화는 6절 |
| **시드 데이터** | baseline용 과거 접속기록(요구사항 4-3)도 **이 수집 API를 통해** 주입한다. Argus DB에 직접 INSERT하면 해시체인이 성립하지 않는다. 시드 스크립트는 `occurred_at`을 과거로 지정하고 `X-Argus-Timestamp`는 현재 시각을 쓴다 |

> 시드 주입 시 `INVALID_TIMESTAMP`는 **미래 5분 초과**만 거부하므로 과거 시각은 통과한다.

---

## 5. ③ 헬스체크

```
GET /healthz
```

인증 불필요. 응답 `200 {"status":"ok","db":"ok"}`. DB 연결 실패 시 `503`. 배포 스크립트의 헬스체크(아키텍처 설계서 8-1절)와 relay의 사전 확인에 쓴다. 이 엔드포인트는 접속기록을 남기지 않는다(개인정보 처리가 없고, 주기적 호출이라 로그를 오염시킴).

---

## 6. ④ DB 구조 수신 · ⑤ 보호 대상 등록부 제공 (게이트웨이 ↔ Argus, v0.6)

DB 접근 게이트웨이가 데이터 유형을 판정하는 표(테이블 → 데이터 유형, 회원 식별 열)를 Argus의 보호 대상 등록부(DB스키마 3-8, 정책정의서 1-6)에서 받아 간다. 등록부가 "아직 분류하지 않은 컬럼"을 보여 주려면 실제 DB 구조가 필요해서, 게이트웨이가 구조 목록을 함께 보낸다.

```
POST /ingest/v1/db-schema              게이트웨이 → Argus: DB 구조 목록
GET  /ingest/v1/protection-registry    Argus → 게이트웨이: 판정 표 (게이트웨이가 조회)
```

| 항목 | 규약 |
|---|---|
| 인증 | ①과 같은 HMAC 서명(1-2절). 출처는 `PLATFORM`(게이트웨이는 relay와 같은 키). 다른 출처면 `403 FORBIDDEN`. GET은 빈 본문으로 서명 |
| 주기 | 등록부: 게이트웨이 기동 시 + 5분마다. 구조 목록: 기동 시 + 1시간마다. 실패하면 30초 뒤 다시 |
| 접속기록 | 남기지 않는다. 게이트웨이 자체 연결의 카탈로그 조회이고 사용자 행위가 아니다 |

**④ 요청 본문**

```json
{
  "database": "platform",
  "tables": [
    { "name": "member", "columns": [ { "name": "id", "type": "bigint" }, { "name": "email", "type": "character varying(255)" } ] }
  ]
}
```

- 게이트웨이는 `pg_catalog`만 읽는다(`public` 스키마의 실제 테이블, 삭제된 열 제외). 데이터 값을 읽는 쿼리는 쓰지 않는다.
- 수신 측은 키 집합을 정확히 검사한다. 본문은 `{database, tables}`, 테이블은 `{name, columns}`, 컬럼은 `{name, type}`만 허용하고 그 밖의 칸(값·예시가 실릴 수 있는 칸)은 거부한다(절대 규칙 #3의 수신 측 방어). 이름은 `[A-Za-z_][A-Za-z0-9_]{0,62}`, 자료형은 `format_type` 결과 형식만. `database`는 v0.1에서 `platform`만. 테이블 500개·컬럼 5,000개 상한, 중복 컬럼 거부.
- 처리: 그 DB의 구조 목록을 통째로 바꾸고 수신 기록을 남긴다(사라진 테이블·컬럼은 목록에서도 사라지고, 등록부는 그대로 — 화면에서 "DB에 없음"으로 보임).
- 응답 `200 {"tables": 15, "columns": 92}`. 오류: `400 MALFORMED_JSON`, `400 INVALID_SCHEMA`(형식·키·이름·상한), 서명 오류는 1-6절과 같음.

**⑤ 응답 본문**

```json
{
  "version": "r57",
  "database": "platform",
  "tables": { "member": "MEMBER_BASIC", "refund_account": "PAYMENT", "orders": "ORDER" },
  "member_columns": [ ["member", "id"], ["orders", "member_id"] ]
}
```

| 필드 | 뜻 |
|---|---|
| `version` | 등록부 버전 = 등록 이력의 마지막 id(`r{id}`). 게이트웨이가 기록마다 `context.registry_version`에 싣는다 |
| `tables` | 테이블 → 데이터 유형. 활성 개인정보 컬럼 중 가장 민감한 유형이고, 개인정보 컬럼이 없는 테이블은 싣지 않는다(`NONE`) |
| `member_columns` | 회원 식별 열 (테이블, 컬럼) 목록 — DB 직접 결과에서 회원번호를 읽는 열(2-2절) |

- 게이트웨이도 응답의 키 집합과 값을 검사하고, 잘못된 응답은 적용하지 않는다.
- 대체 순서: 받지 못하면 마지막으로 받은 등록부(게이트웨이 볼륨에 저장, 재기동 때 복원)를 쓰고, 한 번도 받지 못했으면 게이트웨이 코드의 고정 표를 쓴다(`registry_version = builtin`). 등록부를 받지 못했다고 모든 테이블을 `NONE`으로 보내지 않는다.
- 판정은 테이블 단위다. 컬럼 단위 판정과 여러 DB는 v0.2(9절).

---

## 7. 화면 API 요약 (접속기록·권한 규칙)

세부 필드는 OpenAPI 문서가 기준이고, 여기에는 접속기록 기록 방식과 권한·오류 규칙만 적는다. 오류 응답 형식은 1-6절과 같다(`{"error": {"code", "message"}}`, 입력값을 되풀이하지 않음). 로그인 사용자 인증은 세션 쿠키다.

### 7-1. Argus (argus-api)

| 메서드 · 경로 | 쓰는 사람 | 요청 / 응답 요점 | 접속기록 (2-7절) | 오류 |
|---|---|---|---|---|
| `POST /api/auth/logout` | 로그인 사용자 | 204. 세션 쿠키 삭제, 그 사용자의 웹 푸시 구독 삭제 | `LOGOUT` (인증된 경우만) | — |
| `POST /api/detections/search` | 담당자 전체, 취급자는 본인 건 중 소명 요청을 받은 건 | 본문 `status`·`access_path`·`severity`·`rule_id`·`actor`·`date_from`·`date_to`(탐지 시각의 한국 날짜, 양 끝 포함, 비우면 제한 없음 — 담당자 화면 기본은 이번 달)·`sort`(`LATEST`/`SEVERITY`)·`page`·`size`(≤100). 기존 `GET /api/detections`를 대체 | `READ` `ACCESS_LOG`, count 0 + 키 이름 | `400 BAD_REQUEST` |
| `GET /api/detections/{id}` | 담당자, 취급자(노출 범위 안) | 아래 "탐지건 응답 필드" | `READ` `ACCESS_LOG`, count만 | `404 NOT_FOUND`(남의 건 포함) |
| `POST /api/detections/{id}/{request·dismiss·approve·reject·escalate}` | 담당자 | 상태 전이(정책정의서 3-2). 본인이 행위자인 건은 어떤 상태든 거부 | 제외(상태 이력). 본인 건 거부는 `UPDATE` `FAILURE` | `403 FORBIDDEN`(역할), `403 SELF_REVIEW_FORBIDDEN`(본인 건, 정책정의서 3-5), `409 INVALID_TRANSITION` |
| `POST /api/detections/{id}/submit` | 취급자 | 소명 제출(본문 `content` ≤5,000자, `ticket_ids`) | 제외(상태 이력) | `403 FORBIDDEN`, `409 INVALID_TRANSITION` |
| `POST /api/access-logs/search` | 담당자 전체, 취급자는 본인 기록만 | 본문 `date_from`·`date_to`(비우면 최근 7일, 최대 1년)·`actor`·`action`·`subject`(회원번호)·`access_path`·`source`(`PLATFORM`/`ARGUS`, 기본 `PLATFORM`)·`client_ip`(완전한 IP면 일치, 아니면 앞부분 일치 — 16진수 숫자·`.`·`:`만, 문자열 기준이라 `10.20.3`은 `10.20.30.x`도 찾음)·`data_category`·`result`(`SUCCESS`/`FAILURE`)·`page`·`size`(≤100). 응답 `items`·`total`·`period`·`linked` | `READ` `ACCESS_LOG`, count만 + 키 이름 | `400 BAD_REQUEST`, 취급자가 남의 `actor`나 `source=ARGUS`를 요청하면 `403 FORBIDDEN`(조용히 바꾸지 않음) |
| `GET /api/rules` | 담당자 | 쿼리 `enabled`·`name`(부분 일치)·`access_path`·`severity`·`rule_type` — 룰 정의는 개인정보가 아니라 쿼리로 받음 | 제외(룰 변경 이력) | `403 FORBIDDEN` |
| `GET /api/notifications` | 로그인 사용자 | 내 알림 최근 30개(`kind`·`detection_id`·`rule_name`·`severity`·`round`·`created_at`·`read_at`) + `unread`. 화면은 30초마다 확인 | 제외 | — |
| `POST /api/notifications/{id}/read`, `POST /api/notifications/read-all` | 로그인 사용자 | 204. 내 알림만 | 제외 | `404 NOT_FOUND`(남의 알림 포함) |
| `GET /api/push/config` | 로그인 사용자 | `{"enabled", "public_key"}` — VAPID 공개키. 서버에 키가 없으면 `enabled: false` | 제외 | — |
| `POST /api/push/subscriptions` | 로그인 사용자 | 204. 본문 `endpoint`(알려진 브라우저 푸시 서비스의 https 주소만, ≤1,000자)·`keys.p256dh`(65바이트)·`keys.auth`(16바이트), base64url. 같은 주소면 갱신하고 주인을 지금 사용자로 바꿈 | 제외 | `409 PUSH_DISABLED`, `400 BAD_REQUEST` |
| `POST /api/push/unsubscribe` | 로그인 사용자 | 204. 본문 `endpoint` — 내 구독만 삭제 | 제외 | — |
| `POST /api/reports`, `GET /api/reports`, `GET /api/reports/{id}` | 담당자 | 생성 본문 `date_from`·`date_to`(≤366일)·`scope`(`ALL`/`APP`/`DB`). 본문 `summary`는 DB스키마 3-4 "`summary` 구조" — 탐지건별 마지막 차수 소명 요지·검토 결과·처리 담당자·`after_submission`, `explanations.overdue`, `integrity`(`total`·`last_id`·`last_hash`·`previous`) | 생성 `EXPORT` + `context.report_id`, 열람 `READ` | `403 FORBIDDEN`, `404 NOT_FOUND` |
| `GET /api/users`, `POST /api/users/{id}/{promote·demote·disable·enable·unlock}`, `POST /api/users/history/search` | 담당자 | 계정 목록·변경·이력(DB스키마 3-1). 변경 본문 `reason`(1~500자, 필수). 관리 화면은 v0.2에서 노출 | 제외(계정 이력) | `403 FORBIDDEN`, `403 SELF_CHANGE_FORBIDDEN`(본인 계정), `404 NOT_FOUND`, `409 NOT_A_HANDLER`·`NOT_AN_OFFICER`·`NO_HANDLER_LINK`(명부 연결 없는 담당자를 취급자로)·`ALREADY_DISABLED`·`NOT_DISABLED`·`HANDLER_TERMINATED`(퇴직자 재활성화)·`NOT_LOCKED` |
| `GET /api/protection`, `POST /api/protection/columns`, `POST /api/protection/columns/{id}/release`, `GET /api/protection/history` | 담당자 | 현황과 DB → 테이블 → 컬럼 트리, 등록·변경(본문 `table_name`·`column_name`·`item`·`data_category`·`member_key`·`reason`), 해제(`reason`), 이력. 관리 화면은 v0.2에서 노출 | 제외(등록 이력) | `403 FORBIDDEN`, `404 NOT_FOUND`, `409 UNKNOWN_COLUMN`(구조 목록에 없음)·`NO_CHANGE`·`ALREADY_RELEASED`, `400 BAD_REQUEST`(항목과 데이터 유형 불일치 등) |

**탐지건 응답 필드 (v0.6 추가분)**

| 필드 | 위치 | 뜻 |
|---|---|---|
| `due_at` | 목록·상세(`REQUESTED`일 때만), `explanations[]` | 소명 기한 = 요청 시각 + 7일(재요청하면 다시 7일). 넘겨도 상태는 그대로(정책정의서 3-4) |
| `data_category`·`action_group` | 목록·상세 | 처리 성격 — EVENT 탐지건의 데이터 유형과 행위 구분(`READ`/`DOWNLOAD`/`CHANGE`/`SESSION`). AGGREGATE·이전 건은 null |
| `after_submission_count` | 목록·상세 | 현재 차수 소명 제출 뒤에 붙은 하위 기록 수 — 그 소명이 다루지 않은 행위 |
| `logs[].attached_at`·`logs[].after_submission_round` | 상세 | 하위 기록이 붙은 시각, 몇 차 소명 제출 뒤에 붙었는지(그 뒤 제출이 없을 때) |
| `own_case` | 상세 | 담당자 본인이 행위자인 건. 화면은 처리 칸 대신 "본인 건은 다른 담당자가 처리합니다."를 보여 준다 |

본인 판정: 담당자 계정이 명부에 연결돼 있으면 연결된 (출처, 아이디)로, 없으면 담당자 아이디와 출처 `PLATFORM` 탐지건의 행위자로 비교한다(DB스키마 3-1). 판정 순서는 역할 → 본인 → 전이 표다.

**알림**: 화면 알림은 업무와 같은 트랜잭션에서 받는 사람별로 만든다(종류·받는 사람·웹 푸시 대상은 DB스키마 3-4 "알림", 정책정의서 3-6). 웹 푸시는 VAPID(RFC 8292)와 aes128gcm(RFC 8291)로 직접 보내며, 내용에는 회원번호·취급자 이름·룰 상세를 넣지 않는다(아키텍처 설계서 2-2). 브라우저 쪽 동작:

- 로그아웃하면 서버는 그 사용자의 구독을 모두 지우고, 화면도 브라우저(서비스 워커)의 구독을 해제한다. 다음 사람은 "알림 받기"를 직접 다시 눌러야 한다.
- 로그아웃을 거치지 않고 다시 로그인한 경우(세션 만료 등) 화면이 계정마다 한 번 브라우저에 남은 구독을 `POST /api/push/subscriptions`로 다시 등록한다. 같은 주소면 주인이 지금 계정으로 바뀐다.
- 서버의 VAPID 공개키가 바뀌었으면 옛 구독을 해제하고 "알림 받기"를 다시 받는다.

### 7-2. 플랫폼 관리자 (platform-api)

역할별 기능은 서버가 매 요청 검사한다(정책정의서 4-5). 표에 없는 역할이면 `403 FORBIDDEN`이고, Agent가 그 시도를 접속기록 `FAILURE`(count 0)로 남긴다.

| 기능 | 허용 역할 |
|---|---|
| 회원 조회·검색 | ADMIN · OPS · CS · MARKETING (마케팅의 회원 상세에는 주문 없음) |
| 주문 조회·검색, 1:1 문의 | ADMIN · OPS · CS |
| 회원 목록 다운로드 | ADMIN · OPS |
| 환불계좌 전체 보기 | ADMIN · OPS · CS |
| DB 접속 토큰 발급 | ADMIN · OPS |
| 계정·권한 관리 | ADMIN |

| 메서드 · 경로 | 요청 / 응답 요점 | 접속기록 | 오류 |
|---|---|---|---|
| `POST /admin/auth/logout` | 204 | `LOGOUT` (인증된 경우만) | — |
| `GET /admin/auth/me` | `login_id`·`name`·`team`·`role`·`permissions`(위 기능 코드 `MEMBERS`·`ORDERS`·`INQUIRIES`·`MEMBER_EXPORT`·`REFUND_FULL_VIEW`·`DB_TOKEN`·`ACCOUNTS` 중 허용된 것)·`must_change_password`. 화면은 `permissions`로 메뉴·버튼을 숨기기만 하고, 허용 여부는 각 라우트가 다시 판단 | 제외 | `401 UNAUTHENTICATED` |
| `POST /admin/auth/password` | 204. 본문 `current_password`·`new_password`(고객 비밀번호와 같은 규칙). 성공하면 `must_change_password = false` | 제외 | `400 SAME_PASSWORD`·`WRONG_PASSWORD` |
| `POST /admin/members/search` | 본문 `name`·`email`·`phone`(부분 일치, 연락처는 숫자끼리 비교)·`status`·`joined_from`·`joined_to`·`page`·`size` | `READ` `MEMBER_BASIC`, 결과로 보인 회원 전부 + 키 이름 | `400 BAD_REQUEST`, `403 FORBIDDEN` |
| `POST /admin/orders/search` | 본문 `order_id`·`member_id`·`status`·`ordered_from`·`ordered_to`·`page`·`size` | `READ` `ORDER`, 같음 | 같음 |
| `POST /admin/inquiries/search` | 본문 `status`·`member_id`·`created_from`·`created_to`·`title`(부분 일치)·`page`·`size` | `READ` `INQUIRY`, 같음 | 같음 |
| `GET /admin/accounts`, `POST /admin/accounts`, `POST /admin/accounts/{id}/change`, `POST /admin/accounts/{id}/terminate`, `POST /admin/accounts/history/search` | 계정 목록, 부여(본문 `login_id`·`name`·`team`·`role`·`reason` — 응답에 `temporary_password`를 이 한 번만), 변경(`role`·`team`·`reason`), 말소(`reason`), 권한 이력 검색(대상 아이디는 본문) | 제외(권한 이력 `operator_permission_history`, DB스키마 4절) | `403 FORBIDDEN`(ADMIN 외), `403 SELF_CHANGE_FORBIDDEN`, `404 NOT_FOUND`, `409 LOGIN_ID_TAKEN`·`ACCOUNT_TERMINATED`·`NO_CHANGE` |

- 검색어 값은 본문으로만 받고 원장·서버 로그에 남기지 않는다. `%`·`_`는 글자 그대로 비교한다. 기존 GET 목록(페이지 번호만 받음)은 그대로 둔다.
- 임시 비밀번호로 로그인한 계정은 비밀번호를 바꾸기 전까지 `GET /admin/auth/me`·`POST /admin/auth/password`·로그아웃 외의 관리자 API에서 `403 PASSWORD_CHANGE_REQUIRED`를 받는다.

---

## 8. 버전 관리

- 경로에 `/v1` 포함. 하위 호환이 깨지는 변경은 `/v2`로 추가하고 일정 기간 병행.
- 필드 추가는 하위 호환 변경으로 간주(수신 측은 모르는 필드를 무시). 단 `context`는 정의된 키만 허용하므로(`UNKNOWN_CONTEXT_KEY`) 키 추가 시 양쪽을 함께 배포한다(v0.6의 `registry_version`·`LOGOUT`도 게이트웨이·플랫폼과 Argus를 함께 배포).
- ④⑤는 수신 측이 키 집합을 정확히 검사하므로 필드 추가도 양쪽을 함께 배포한다.

---

## 9. 미결 / 구현 시 확정

- [x] A5 초기 계정 발급 → 무작위 해시로 생성, 비밀번호는 관리 스크립트로 설정 (v0.4, 3-1절)
- [x] 2단계(`access_path=DB`) 확장 필드 → `context.query`·`context.row_count` 예약 (v0.2) → **v0.5에서 확정**: `query` 폐기, DB 키 9종(2-3절). v0.6에서 `registry_version`을 더해 10종
- [ ] DEAD 상태 이벤트 운영 알림 채널 (로그 / 이메일) — 현재 구현은 ERROR 로그뿐, DEAD 재처리 도구 없음
- [ ] HMAC 비밀키 교체 절차의 운영 문서화
- [ ] HMAC 신·구 키 병행 허용(1-2절 #4) — v0.1 미구현(출처당 키 1개). 보안성 검토 단계 과제(아키텍처 설계서 8-6)
- [x] 요청 단위 오류 코드, 이벤트 수 초과 처리, 수신 측 형식 검증 → v0.3
- [x] DB 직접 기록의 회원번호 추출 → 결과 열에서 추출 (v0.6, 2-2절). 회원 열이 없는 조회·결과 없는 변경의 특정(시점 복구 대조)은 v0.2
- [ ] (v0.2) 보호 대상 컬럼 단위 판정, 여러 DB 등록, 화면 경유 수집 지점(플랫폼 Agent의 데이터 유형 표시)과 등록부의 대조 — 지금은 화면 경유의 데이터 유형을 코드가 정하고 등록부와 따로 관리한다
- [ ] (v0.2) 계정 말소의 DB 접속 토큰 즉시 반영 — 말소 직전 발급된 토큰(최대 1시간)은 게이트웨이가 재직을 다시 확인하지 않아 만료까지 유효
- [ ] (v0.2) 이메일 알림(LOG-16)
