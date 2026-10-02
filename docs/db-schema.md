# DB 스키마 (Argus / 플랫폼)

> 작성일: 2026-09-23 / v0.2 (2026-09-23 개정 — 요구사항 전수 대조 후 6개 테이블·다수 컬럼 추가) / **v0.3 (2026-09-29 개정 — 구현 M1 반영: 해시체인 정규화 규칙 v1 확정, DB 계정 구조·권한 확정, 알려진 한계 명시)** (2026-09-30 보완 — A5 초기 해시 주석) / **v0.4 (2026-10-01 개정 — 구현 M3 반영: 탐지 날짜 KST 기준, 해석 불가 룰 시 순찰 실패, 빈 순찰 기록, 순찰 상한, `log_summary` 잘림 표시)** (2026-10-01 보완 — 구현 M4: `detection_rule.auto_request`, `explanation.requested_by` NULL 허용, 자동 소명 요청, A5 조회 범위) / **v0.5 (2026-10-02 — 기능 레이어 1·4·6·7 반영: 룰 평가 대상, 명부 미등록 처리, `min_baseline`, 집계 윈도우 1회 판단, 룰 변경 이력 운영, 결제수단 재설계, 고객 잠금 컬럼, 탈퇴 즉시 파기)** / **v0.6 (2026-10-02 — 기능 레이어 7 구현 반영: `payment`·`refund_account` 확정, 탈퇴 분리보관 실제 절차, 소명 첨부 권한·무결성, `explanation.ticket_ids`, 동의 항목 `AGE_OVER_14`)**
> 관련 문서: [[아키텍처_설계서.md]], [[API명세서_시스템간.md]], [[정책정의서.md]], [[액터별_플로우.md]], [[요구사항정의서.md]]
> DBMS: PostgreSQL 16 (플랫폼 DB / Argus DB 별도 인스턴스)
> 표기: **[S]** = Walking Skeleton에 필요한 테이블. 컬럼은 전체를 정의하되 Skeleton에서는 [S] 테이블만 생성한다.
> **검증**: 이 문서의 전체 DDL은 PostgreSQL 16에 실제 적용해 생성 성공 및 제약조건 동작(8개 시나리오)을 확인했다. 결과는 7절.

---

## 0. v0.2 개정 내역

요구사항정의서(PLT-01~17 / LOG-01~17)·정책정의서·액터별 플로우를 v0.1과 전수 대조해 아래를 보완했다.

| # | 구분 | 내용 | 근거 |
|---|---|---|---|
| 1 | **신규 테이블** | `consent_item` / `member_consent` — 회원 동의 이력 | **PLT-01**. PIPA §22(동의를 받는 방법), §16①·§22③ **입증책임이 개인정보처리자에게 있음** → 동의 증적이 없으면 요구사항이 성립하지 않음 |
| 2 | **신규 테이블** | `inspection_report` — 점검 보고서 이력 | **LOG-09**. §8② "사후조치절차 이행"의 증적 |
| 3 | **신규 테이블** | `detection_rule_history` — 룰 변경 이력 | **LOG-13** |
| 4 | **신규 테이블** | `notification` — 알림 발송·수신 이력 | **LOG-05·LOG-16**(이메일 발송 성공/실패 추적이 필요) |
| 5 | **신규 테이블** | `retained_member_record` — 법정 보존 항목 분리보관 | **PIPA §21③**. v0.1에서 미결로 남겼던 항목 |
| 6 | **신규 테이블** | 플랫폼 `destruction_history` — v0.1은 글로만 언급하고 DDL 누락 | 정책정의서 5-3 |
| 7 | 컬럼 추가 | `detection_rule.access_path` (APP/DB/ALL) | 정책정의서 1-2가 **"적용 접근 경로"를 룰 스펙의 독립 필드**로 규정. v0.1은 조건식(JSON) 안에만 있었음 |
| 8 | 컬럼 추가 | `detection.log_summary` (jsonb) | 파기와의 충돌 해소 — 아래 #11 |
| 9 | 컬럼 추가 | `handler.terminated_at`, `operator.terminated_at` | "퇴직자 계정 접속" 룰은 **접속 시점의 재직상태**로 판정해야 함. v0.1은 현재 상태만 보유해 과거·지연 로그를 오판정 |
| 10 | 컬럼 추가 | `operator.role` | **PLT-10**(관리자 메뉴 노출)·**PLT-14**(권한 차등) 구현 불가 상태였음 |
| 11 | 설계 보완 | 파기 시 `access_log` 삭제 → `detection_log` CASCADE로 근거가 완전 소멸 → 탐지건이 공허해짐. **정책정의서 5-1 "탐지·소명 이력은 접속기록과 동일 이상 보관"과 모순** | 탐지건에 `log_summary` 스냅샷을 남겨 근거 요약이 파기 후에도 보존되게 함 (7절에서 동작 검증) |
| 12 | 인덱스 추가 | `access_log.subject_ids` GIN, `(source_system_id, action, occurred_at)`, `detection_log(access_log_id)`, `detection_status_history(detection_id, created_at)`, `notification(recipient_id, …)` | **LOG-02**의 정보주체·수행업무 필터가 인덱스 없이 전체 스캔이었음 |
| 13 | 제약 추가 | `LOGIN` 외 행위는 `subject_type` 필수 / 퇴직 시 `terminated_at` 필수 / 언마스킹 보고서는 사유 필수 | §2 3호(처리한 정보주체 필수 항목), §12① |
| 14 | 정책 보완 제안 | 보고서 export 시 **기본 마스킹**, 언마스킹 export는 사유 필수 | 액터별_플로우 8절 마스킹 정책이 화면만 다루고 **보고서 파일은 다루지 않아 우회 경로가 됨** |

---

## 1. 개념 데이터 모델

### 1-1. 엔티티

| 시스템 | 엔티티 | 설명 | 근거 |
|---|---|---|---|
| Argus | `source_system` | 접속기록을 보내는 시스템. `PLATFORM`, `ARGUS`(자체 기록) | LOG-17, 2단계 확장 |
| Argus | `handler` | 플랫폼에서 동기화된 개인정보취급자 | API ② |
| Argus | `argus_user` | Argus 로그인 계정. 정보보호 담당자(A4) / 취급자(A5) | 액터별 플로우 1절 |
| Argus | **`access_log`** | 원장. append-only + 해시체인 | §2 3호, §8①③ |
| Argus | `detection_rule` / `detection_rule_history` | EVENT / AGGREGATE 룰과 변경 이력 | 정책정의서 1절, LOG-04·13 |
| Argus | **`detection`** | 그룹핑 단위의 탐지 결과, 상태 7종 | 정책정의서 2·3절 |
| Argus | `detection_log` | 탐지건의 하위 근거 로그 (N:M) | 정책정의서 2-2 |
| Argus | `detection_status_history` | 모든 상태 전이의 감사 추적 | 점검 수행 증적 |
| Argus | **`explanation`** / `explanation_attachment` | 차수별 소명과 근거자료 | LOG-06~08 |
| Argus | `rule_exception` | 화이트리스트 (시스템 계정 포함) | LOG-12, 정책정의서 5-3 |
| Argus | `notification` | 대시보드·이메일 알림 | LOG-05·16 |
| Argus | `inspection_report` | 점검 보고서 생성 이력 | LOG-09, §8② |
| Argus | `detection_batch_run` | 탐지 배치 실행 이력 + 커서 | F-04 |
| Argus | `destruction_history` | 파기 증적 (개인정보 미포함) | 정책정의서 5-3 |
| Argus | `setting` | 점검 주기 등 | §8② (설정값) |
| 플랫폼 | `operator` / `operator_permission_history` | 관리자 UI 사용자, 동기화 원천, 권한 이력 | PLT-10·14 |
| 플랫폼 | `member` | 고객 = 정보주체 | PLT-01~03 |
| 플랫폼 | `consent_item` / `member_consent` | 동의 항목 정의와 동의·철회 이력 | **PLT-01** |
| 플랫폼 | `payment_method` / `orders` / `product` / `inquiry` | 무대장치 업무 데이터 | PLT-04~06 |
| 플랫폼 | **`outbox`** | Argus 전송 대기 버퍼 | 아키텍처 3-3 |
| 플랫폼 | `retained_member_record` / `destruction_history` | 분리보관·파기 증적 | PIPA §21③, 정책정의서 5-3 |

### 1-2. 관계

```
[Argus]
 source_system  1 ──< handler,  1 ──< access_log
 handler        1 ──o argus_user            (role=HANDLER일 때 1:1)
 detection_rule 1 ──< detection,  1 ──< detection_rule_history
 detection     >──< access_log              (detection_log로 N:M)
 detection      1 ──< detection_status_history,  1 ──< explanation,  1 ──o< notification
 explanation    1 ──< explanation_attachment
 argus_user     1 ──< inspection_report

[플랫폼]
 member    1 ──< member_consent >── 1 consent_item
 member    1 ──< payment_method,  1 ──< orders,  1 ──< inquiry
 operator  1 ──< operator_permission_history
 outbox / retained_member_record / destruction_history  (독립)

[시스템 간]  플랫폼 operator.login_id ══(API ②)══> Argus handler.login_id
            플랫폼 member.id        ══(API ①, 식별자만)══> Argus access_log.subject_ids
```

두 DB 사이에 **FK는 없다.** 연결은 문자열 키(`login_id`, 회원 내부 PK)뿐이며, Argus는 회원 원본 정보를 보유하지 않는다.

---

## 2. 설계 원칙

| 원칙 | 내용 |
|---|---|
| 시각 | 모든 시각은 `timestamptz` |
| 코드값 | PostgreSQL ENUM 대신 `varchar + CHECK` — 마이그레이션이 쉬움 |
| 원문 보존 | 접속기록은 수신한 값을 그대로 저장. 표시용 가공(마스킹)은 조회 시점에 적용 |
| 최소 보유 | Argus에는 회원의 **내부 PK만**. 이름·연락처 등은 저장하지 않음 |
| 불변성 | `access_log`는 INSERT만 허용 (3-5절) |
| 판단 근거 보존 | 탐지건에 **탐지 당시 룰 스냅샷**과 **하위 로그 요약**을 저장 — 룰이 바뀌거나 원본 로그가 파기돼도 "왜 탐지됐는지"가 남음 |
| 감사 추적 | 탐지건 상태 변경은 전부 `detection_status_history`에 행으로 남김 |
| 시점 판정 | 재직상태처럼 변하는 속성은 **행위 시점 기준**으로 판정할 수 있게 `terminated_at`을 둠 |

### 2-1. 정보주체 식별값 표기 규칙 (문서 간 통일)

v0.1까지 문서마다 표기가 달랐으므로 아래로 통일한다.

| 계층 | 표기 | 예 |
|---|---|---|
| 플랫폼 DB | `member.id` (bigint) | `10293` |
| 전송(API ①) · Argus 저장 | `subject_type` + `subject_ids` (문자열 배열) | `MEMBER` + `["10293"]` |
| 화면·보고서 표시 | `{type 접두어}_{id}` | `member_10293` |
| 마스킹 표시 (LOG-10) | 뒤 3자리 마스킹 | `member_10***` |

---

## 3. Argus DB

### 3-1. 기준·계정

```sql
-- [S] 출처 시스템
CREATE TABLE source_system (
    id          smallserial PRIMARY KEY,
    code        varchar(32)  NOT NULL UNIQUE,      -- 'PLATFORM', 'ARGUS'
    name        varchar(100) NOT NULL,
    created_at  timestamptz  NOT NULL DEFAULT now()
);

-- [S] 취급자 (API ②로 동기화)
CREATE TABLE handler (
    id                 bigserial PRIMARY KEY,
    source_system_id   smallint     NOT NULL REFERENCES source_system(id),
    login_id           varchar(64)  NOT NULL,
    name               varchar(50)  NOT NULL,
    team               varchar(50),
    employment_status  varchar(16)  NOT NULL CHECK (employment_status IN ('ACTIVE','TERMINATED')),
    terminated_at      timestamptz,                -- 퇴직 시점 (시점 기준 룰 판정용)
    last_event_at      timestamptz  NOT NULL,      -- 순서 역전 방지 + 중복 판정 기준 (API ② 3-1, v0.3)
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (source_system_id, login_id),
    CHECK (employment_status <> 'TERMINATED' OR terminated_at IS NOT NULL)
);

-- [S] Argus 사용자
CREATE TABLE argus_user (
    id                  bigserial PRIMARY KEY,
    login_id            varchar(64)  NOT NULL UNIQUE,
    password_hash       varchar(255) NOT NULL,     -- argon2id. A5는 동기화 시 무작위 해시(로그인 불가) → 관리 스크립트로 설정 (API ② 3-1)
    role                varchar(16)  NOT NULL CHECK (role IN ('OFFICER','HANDLER')), -- A4 / A5
    handler_id          bigint       REFERENCES handler(id),
    status              varchar(16)  NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','LOCKED','DISABLED')),
    failed_login_count  int          NOT NULL DEFAULT 0,   -- 로그인 실패 제한 (Caddy rate limit 대체)
    last_login_at       timestamptz,
    created_at          timestamptz  NOT NULL DEFAULT now(),
    CHECK (role <> 'HANDLER' OR handler_id IS NOT NULL)
);
```

> **취급자 동기화의 중복 판정 (v0.3)**: `handler`는 API ②의 `event_id`를 저장하지 않는다. 이벤트가 상태 전체(스냅샷)를 싣고 오므로 `occurred_at > last_event_at`일 때만 upsert하고, 그 외는 변경 없이 중복으로 처리한다. event_id 저장 테이블(`handler_event`)은 기각 — 사유는 API명세서 3-1.
>
> **A5의 조회 범위**: 취급자(A5)는 **자신의 `handler_id`에 연결된 탐지건 중 소명 요청을 받은 건(`round ≥ 1`)만** 볼 수 있다(2026-10-01 구체화 — 담당자 검토 대기 `DETECTED` 건은 제외, 남의 건은 404). DB 제약이 아니라 애플리케이션 계층에서 강제하며(모든 조회 쿼리에 actor 필터 주입), 이 규칙은 액터별_플로우 F-06 #2와 대응한다.

### 3-2. 접속기록 원장

```sql
-- [S] 접속기록 (원장)
CREATE TABLE access_log (
    id                bigserial    PRIMARY KEY,
    event_id          uuid         NOT NULL UNIQUE,              -- 멱등 키
    source_system_id  smallint     NOT NULL REFERENCES source_system(id),
    access_path       varchar(8)   NOT NULL CHECK (access_path IN ('APP','DB')),

    -- §2 3호 필수 5항목
    actor_login_id    varchar(64)  NOT NULL,                     -- 식별자
    occurred_at       timestamptz  NOT NULL,                     -- 접속일시
    client_ip         inet         NOT NULL,                     -- 접속지 정보
    subject_type      varchar(16),                               -- 처리한 정보주체 정보
    subject_ids       text[],                                    --   (내부 PK, 최대 1,000개)
    subject_count     int          NOT NULL DEFAULT 0,           --   (건수)
    subject_truncated boolean      NOT NULL DEFAULT false,
    action            varchar(16)  NOT NULL,                     -- 수행업무

    -- 확장 필드
    data_category     varchar(32)  NOT NULL,
    result            varchar(8)   NOT NULL CHECK (result IN ('SUCCESS','FAILURE')),
    request_method    varchar(8),
    request_path      varchar(255),
    request_query_keys text[],
    context           jsonb,                                     -- ticket_id, reason, target 등

    -- 수신·무결성
    received_at       timestamptz  NOT NULL DEFAULT now(),        -- append 함수가 항상 명시 지정 (3-5절 v0.3)
    prev_hash         char(64),
    hash              char(64)     NOT NULL,

    CHECK (action IN ('LOGIN','READ','CREATE','UPDATE','DELETE','DOWNLOAD','EXPORT','UNMASK')),
    CHECK (data_category IN ('MEMBER_BASIC','PAYMENT','ORDER','INQUIRY','ACCESS_LOG','NONE')),
    CHECK (action = 'LOGIN' OR subject_type IS NOT NULL)          -- §2 3호: 처리한 정보주체 정보 필수
);

CREATE INDEX ix_access_log_actor_time ON access_log (source_system_id, actor_login_id, occurred_at);
CREATE INDEX ix_access_log_occurred   ON access_log (occurred_at);
CREATE INDEX ix_access_log_filter     ON access_log (source_system_id, action, occurred_at);
CREATE INDEX ix_access_log_subject    ON access_log USING gin (subject_ids);   -- LOG-02 정보주체 검색
```

- **Argus 자체 접속기록(LOG-17)**도 같은 테이블에 `source_system = ARGUS`로 저장한다. argus-api 미들웨어가 outbox 없이 동일한 append 함수로 직접 기록한다.
- 취급자 매칭은 FK가 아니라 `(source_system_id, actor_login_id)` ↔ `handler(source_system_id, login_id)` 조인. 동기화 전에 도착한 로그도 원문 그대로 저장되고 나중에 자연히 매칭된다.
- **Argus 자체 기록의 정보주체 처리 규칙** (신설): Argus에서 탐지건·로그를 조회하는 행위는 회원 PK를 눈으로 보는 행위이지만, 이때 `subject_ids`에 회원 PK를 **다시 적재하지 않고** `subject_count`만 기록한다. Argus 접속기록이 회원 식별자를 중복 축적하면 최소처리 원칙에 반하기 때문이다. 예외적으로 `UNMASK`는 어떤 대상을 열어봤는지가 감사 핵심이므로 `context.target`에 대상 탐지건 ID를 남긴다.
- **정보주체 ID로 검색하는 행위**(LOG-02)도 식별값을 평문으로 다루는 행위이므로 `READ` + `request_query_keys`에 검색 조건 키를 기록한다. 검색어 값 자체는 기록하지 않는다.

### 3-3. 탐지 룰

```sql
-- [S] 탐지 룰
CREATE TABLE detection_rule (
    id           bigserial    PRIMARY KEY,
    name         varchar(100) NOT NULL,
    description  text,                             -- 담당자가 남기는 룰 취지
    rule_type    varchar(16)  NOT NULL CHECK (rule_type IN ('EVENT','AGGREGATE')),
    access_path  varchar(8)   NOT NULL DEFAULT 'APP' CHECK (access_path IN ('APP','DB','ALL')),
    severity     varchar(8)   NOT NULL CHECK (severity IN ('HIGH','MEDIUM','LOW')),
    enabled      boolean      NOT NULL DEFAULT true,
    condition    jsonb        NOT NULL,            -- 조건식 (3-6절)
    aggregate    jsonb,                            -- AGGREGATE 전용 (3-6절)
    group_by     varchar(32)  NOT NULL DEFAULT 'ACTOR_RULE_DATE',
    auto_request boolean      NOT NULL DEFAULT true,  -- 탐지건 생성 시 자동 소명 요청 여부 (2026-10-01, 정책정의서 3-2)
    version      int          NOT NULL DEFAULT 1,  -- 수정 시 +1
    created_by   bigint       REFERENCES argus_user(id),
    created_at   timestamptz  NOT NULL DEFAULT now(),
    updated_at   timestamptz  NOT NULL DEFAULT now(),
    CHECK ((rule_type = 'AGGREGATE') = (aggregate IS NOT NULL))
);

-- 룰 변경 이력 (LOG-13)
CREATE TABLE detection_rule_history (
    id           bigserial   PRIMARY KEY,
    rule_id      bigint      NOT NULL REFERENCES detection_rule(id),
    version      int         NOT NULL,
    change_type  varchar(16) NOT NULL CHECK (change_type IN ('CREATE','UPDATE','ENABLE','DISABLE')),
    snapshot     jsonb       NOT NULL,             -- 변경 후 룰 전체
    changed_by   bigint      REFERENCES argus_user(id),
    changed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_rule_history ON detection_rule_history (rule_id, changed_at);
-- (2026-10-01, 기능 레이어 6) (rule_id, version) 유일 — 켜기·끄기도 version +1로 하나의 축
-- 앱 계정은 추가·조회만(감사 추적). 변경 사유 컬럼은 두지 않음(사용자 결정 — 변경 후 스냅숏 + 누가·언제로 추적)
-- 시드 룰의 CREATE 이력은 소급 기록(changed_by NULL = 시스템)
```

> **룰 삭제 없음** (2026-10-01, 기능 레이어 6): 탐지건이 룰을 참조하고 "그 시점에 운영한 룰"이 점검 근거이므로 룰은 삭제하지 않고 끈다(`enabled=false`, 화면은 켜짐/꺼짐 필터). 앱 계정에는 처음부터 `detection_rule` DELETE 권한이 없다(3-5 DB 계정 구조) — 정책과 DB 권한이 일치. `rule_type`은 생성 후 변경 불가.

> `access_path`를 조건식이 아니라 **독립 컬럼**으로 둔 이유: 정책정의서 1-2가 이를 룰 스펙의 별도 필드로 규정하고, 두 경로는 정상 기준선이 반대여서 룰을 경로별로 분리 적용해야 한다. 컬럼으로 두면 배치가 로그를 경로별로 먼저 나눠 룰 평가 대상을 줄일 수 있다.

### 3-4. 탐지건·소명·알림

```sql
-- [S] 탐지건
CREATE TABLE detection (
    id                 bigserial    PRIMARY KEY,
    rule_id            bigint       NOT NULL REFERENCES detection_rule(id),
    rule_version       int          NOT NULL,
    rule_snapshot      jsonb        NOT NULL,     -- 탐지 당시 룰 전체 (판단 근거 보존)
    source_system_id   smallint     NOT NULL REFERENCES source_system(id),
    actor_login_id     varchar(64)  NOT NULL,
    group_bucket       varchar(64)  NOT NULL,     -- EVENT: '2026-09-15'(occurred_at의 KST 날짜, v0.4) / AGGREGATE: 윈도우 시작 시각
    severity           varchar(8)   NOT NULL,
    status             varchar(16)  NOT NULL DEFAULT 'DETECTED'
                       CHECK (status IN ('DETECTED','REQUESTED','SUBMITTED','APPROVED','REJECTED','DISMISSED','ESCALATED')),
    round              int          NOT NULL DEFAULT 0,   -- 소명 요청 차수 (첫 요청 시 1)
    log_count          int          NOT NULL DEFAULT 0,
    aggregate_value    numeric,                   -- AGGREGATE 집계값 (예: 142건, 2.3배)
    log_summary        jsonb,                     -- 하위 로그 요약 (파기 후에도 남는 근거)
    first_occurred_at  timestamptz  NOT NULL,
    last_occurred_at   timestamptz  NOT NULL,
    detected_at        timestamptz  NOT NULL DEFAULT now(),
    closed_at          timestamptz,               -- APPROVED / DISMISSED / ESCALATED 시각
    close_reason       text                       -- DISMISS 사유 등
);

-- 같은 그룹의 "진행 중" 탐지건은 1개만 (정책 보완: 6절 #1)
CREATE UNIQUE INDEX ux_detection_open_group
    ON detection (rule_id, source_system_id, actor_login_id, group_bucket)
    WHERE status IN ('DETECTED','REQUESTED','SUBMITTED','REJECTED');

CREATE INDEX ix_detection_status ON detection (status, detected_at);
CREATE INDEX ix_detection_actor  ON detection (source_system_id, actor_login_id);

-- [S] 탐지건 하위 로그
CREATE TABLE detection_log (
    detection_id   bigint NOT NULL REFERENCES detection(id)  ON DELETE CASCADE,
    access_log_id  bigint NOT NULL REFERENCES access_log(id) ON DELETE CASCADE,
    PRIMARY KEY (detection_id, access_log_id)
);
CREATE INDEX ix_detection_log_log ON detection_log (access_log_id);   -- 역방향 조회

-- [S] 상태 전이 이력 (감사 추적)
CREATE TABLE detection_status_history (
    id             bigserial   PRIMARY KEY,
    detection_id   bigint      NOT NULL REFERENCES detection(id),
    from_status    varchar(16),
    to_status      varchar(16) NOT NULL,
    round          int         NOT NULL,
    actor_user_id  bigint      REFERENCES argus_user(id),   -- NULL = 시스템(탐지 배치)
    comment        text,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_detection_status_hist ON detection_status_history (detection_id, created_at);

-- [S] 소명 (차수별 1행: 요청 시 생성 → 제출 시 채움 → 검토 시 채움)
CREATE TABLE explanation (
    id               bigserial   PRIMARY KEY,
    detection_id     bigint      NOT NULL REFERENCES detection(id),
    round            int         NOT NULL,
    requested_by     bigint      REFERENCES argus_user(id),  -- NULL = 시스템(자동 소명 요청) (2026-10-01)
    requested_at     timestamptz NOT NULL DEFAULT now(),
    request_message  text,
    submitted_by     bigint      REFERENCES argus_user(id),
    submitted_at     timestamptz,
    content          text,
    reviewed_by      bigint      REFERENCES argus_user(id),
    reviewed_at      timestamptz,
    review_result    varchar(16) CHECK (review_result IN ('APPROVED','REJECTED')),
    review_comment   text,
    ticket_ids       varchar(32)[] NOT NULL DEFAULT '{}',  -- (2026-10-02) 관련 업무 티켓 번호(`INQ-n`), 최대 3개 CHECK. 제출과 함께 저장, 이후 변경 API 없음
    UNIQUE (detection_id, round)
);
```

> **관련 업무 티켓 (2026-10-02)**: 취급자가 소명 제출 시 플랫폼 1:1 문의 번호를 직접 입력한다(서버는 형식 `INQ-[1-9][0-9]{0,9}`만 검사 — 링크 주소에 그대로 들어가므로 엄격히). **Argus는 티켓 내용을 보유하지 않고** 탐지건 상세에 플랫폼 관리자 화면 링크(`ARGUS_PLATFORM_ADMIN_URL` + `/inquiries/{번호}`)만 준다 — 절대 규칙 #3·두 시스템 분리. 담당자가 링크로 문의를 열면 그 열람은 **플랫폼 접속기록으로 Argus에 남는다**(감시자도 감시됨). 검증(티켓이 실제 근거인가)은 담당자 판단, 자동 대조는 v0.2.

```sql
-- 소명 첨부 (2026-10-02 구현, 기능 레이어 7 ③)
CREATE TABLE explanation_attachment (
    id               bigserial    PRIMARY KEY,
    explanation_id   bigint       NOT NULL REFERENCES explanation(id),
    original_name    varchar(255) NOT NULL,
    stored_path      varchar(500) NOT NULL,     -- 로컬 볼륨 경로 (1차)
    content_type     varchar(100) NOT NULL,
    size_bytes       bigint       NOT NULL,
    sha256           char(64)     NOT NULL,     -- 업로드 시 계산, 내려받을 때마다 재계산해 다르면 거부
    uploaded_at      timestamptz  NOT NULL DEFAULT now()
);
```

**소명 첨부 규칙 (2026-10-02)**

| 항목 | 규칙 | 이유 |
|---|---|---|
| 형식 | PNG·JPG·PDF — **매직 바이트로 판정**(이름·Content-Type 무시) | `.png` 이름의 HTML 같은 위장 차단 |
| 크기·개수 | 파일당 5MB, 소명 차수당 최대 3개, 빈 파일 거부 | |
| 저장 경로 | 서버가 `년/월/uuid`로 생성, `O_CREAT\|O_EXCL`·0600, 볼륨 밖 경로 거부. 보낸 파일 이름은 표시용으로만(경로·제어문자 제거, 200자) | **파일 이름 불신** — 경로 조작 방지 |
| 무결성 | SHA-256을 업로드 시 계산·저장, **내려받을 때마다 재계산해 다르면 500 `ATTACHMENT_TAMPERED`** | 제출된 근거의 사후 변조 탐지 |
| 권한(앱 계정) | **SELECT·INSERT·DELETE, UPDATE 없음** — 해시·경로를 바꿔 근거를 갈아치울 수 없음 | DELETE는 제출 전 정정용, 제출 후 삭제는 앱에서 409 |
| 올리기·지우기 | 취급자 본인 + `REQUESTED` 상태일 때만 | |
| 내려받기 | 담당자는 **제출된 차수만**(미제출 초안은 소명이 아님), 취급자는 본인 것. `attachment`·`nosniff`·`CSP: sandbox`·`no-store` | 파일 안의 스크립트가 화면 출처에서 실행되지 않게 |
| 기록 | **내려받기 = Argus 자체 접속기록 `READ`**(ACCESS_LOG, 정보주체 0, `context.target = {"detection_id": N}`) — 캡처에 개인정보가 있을 수 있음. 올리기·지우기는 본인 자료 제출이라 기록 제외 | API명세서 2-7 |
| 저장소 | Argus 전용 볼륨(`argus-attachments`) — **argus-api에만** 연결(worker·플랫폼 미연결) | 아키텍처 설계서 7-2 |

- 요청이 취소된 차수의 미제출 첨부는 그대로 둔다(그 시점 소명 과정의 기록, 양이 작음) — 보관 기간은 파기 배치(v0.2)에서 정한다.
- 바이러스·악성 PDF 검사는 하지 않음(형식 판정 + 내려받기 강제까지) — 보안성 검토 이월.

```sql

-- 룰 예외 / 화이트리스트 (Skeleton 제외, 단 시스템 계정 예외는 시드로 투입)
CREATE TABLE rule_exception (
    id               bigserial   PRIMARY KEY,
    rule_id          bigint      REFERENCES detection_rule(id),   -- NULL = 전체 룰
    source_system_id smallint    REFERENCES source_system(id),
    actor_login_id   varchar(64),
    client_ip        cidr,
    reason           text        NOT NULL,
    valid_until      timestamptz,
    created_by       bigint      REFERENCES argus_user(id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (actor_login_id IS NOT NULL OR client_ip IS NOT NULL)
);

-- 알림 (LOG-05 대시보드 / LOG-16 이메일)
CREATE TABLE notification (
    id            bigserial   PRIMARY KEY,
    recipient_id  bigint      NOT NULL REFERENCES argus_user(id),
    type          varchar(32) NOT NULL CHECK (type IN
                  ('DETECTION_CREATED','EXPLANATION_REQUESTED','EXPLANATION_SUBMITTED','EXPLANATION_REJECTED')),
    detection_id  bigint      REFERENCES detection(id),
    channel       varchar(16) NOT NULL DEFAULT 'DASHBOARD' CHECK (channel IN ('DASHBOARD','EMAIL')),
    status        varchar(16) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SENT','FAILED','READ')),
    sent_at       timestamptz,
    read_at       timestamptz,
    error         text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_notification_recipient ON notification (recipient_id, status, created_at DESC);

-- 점검 보고서 이력 (LOG-09)
CREATE TABLE inspection_report (
    id              bigserial   PRIMARY KEY,
    period_from     timestamptz NOT NULL,
    period_to       timestamptz NOT NULL,
    scope           jsonb,                     -- 출처 시스템·접근 경로·팀 등 필터
    summary         jsonb       NOT NULL,      -- 상태별·룰별·팀별 집계 스냅샷
    escalated_count int         NOT NULL DEFAULT 0,
    unmasked        boolean     NOT NULL DEFAULT false,   -- 식별값 노출 여부
    unmask_reason   text,
    file_path       varchar(500),
    generated_by    bigint      NOT NULL REFERENCES argus_user(id),
    generated_at    timestamptz NOT NULL DEFAULT now(),
    CHECK (period_from < period_to),
    CHECK (NOT unmasked OR unmask_reason IS NOT NULL)     -- §12① 용도 특정
);
```

- **`log_summary` 스냅샷 내용**: `{log_count, subject_count_sum, distinct_subject_count, subject_ids_truncated, actions[], data_categories[], first_occurred_at, last_occurred_at, ip_list[]}`. **회원 PK는 담지 않고 숫자만** 담는다. `subject_ids_truncated`(v0.4)는 연결된 기록 중 회원 PK가 1,000개에서 잘린 것이 있으면 true — 이때 `distinct_subject_count`는 **하한값**이다. `subject_count_sum`(행위의 양, 탐지 판정)과 `distinct_subject_count`(피해 범위, 유출 통지·신고 판단)는 서로 다른 질문의 답이라 둘 다 유지한다. 요약은 증분 갱신이 아니라 **연결된 기록 전체에서 다시 계산**한다(재처리해도 같은 값). 접속기록이 보관기간 만료로 파기되면 `detection_log` 링크는 CASCADE로 사라지지만, 이 요약은 탐지건에 남아 점검 증적이 유지된다(7절에서 동작 검증).
- **보고서 마스킹**(신규 정책 제안 — 6절 #3): 보고서는 기본적으로 마스킹된 식별값으로 생성한다. 식별값을 포함한 보고서를 만들려면 `unmasked=true` + 사유가 필요하고, 그 행위는 Argus 접속기록에 `EXPORT`(+`context.reason`)로 기록된다. 화면에서는 마스킹을 강제하면서 보고서 파일로는 그냥 빠져나가는 구멍을 막기 위함이다.
- **LOG-15**(발생-제출 시간차) = `explanation.submitted_at - detection.first_occurred_at`, **LOG-14**(취급자별 소명 이력) = `detection` × `explanation`을 `actor_login_id`로 집계. 별도 테이블 불필요.

### 3-5. 무결성 · 배치 · 파기

```sql
-- [S] 배치 실행 이력 + 커서
CREATE TABLE detection_batch_run (
    id                  bigserial   PRIMARY KEY,
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    from_access_log_id  bigint      NOT NULL,   -- 이번 실행이 처리한 범위 (초과)
    to_access_log_id    bigint      NOT NULL,   --                       (이하)
    processed_count     int         NOT NULL DEFAULT 0,
    detected_count      int         NOT NULL DEFAULT 0,
    status              varchar(16) NOT NULL CHECK (status IN ('RUNNING','SUCCESS','FAILED')),
    error               text
);

-- 파기 이력 (개인정보 미포함)
CREATE TABLE destruction_history (
    id                 bigserial    PRIMARY KEY,
    executed_at        timestamptz  NOT NULL DEFAULT now(),
    target_type        varchar(32)  NOT NULL,     -- 'ACCESS_LOG' 등
    cutoff_at          timestamptz  NOT NULL,     -- 이 시각 이전 발생분 파기
    deleted_count      int          NOT NULL,
    legal_basis        varchar(100) NOT NULL,     -- '안전성 확보조치 기준 §8① (1년)'
    chain_anchor_hash  char(64)                   -- 파기 후 남은 가장 오래된 레코드의 prev_hash
);

-- [S] 설정 (탐지 배치 주기 등)
CREATE TABLE setting (
    key         varchar(64) PRIMARY KEY,
    value       jsonb       NOT NULL,
    updated_by  bigint      REFERENCES argus_user(id),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- append-only 이중 장치
CREATE FUNCTION forbid_access_log_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'access_log is append-only'; END $$;
CREATE TRIGGER trg_access_log_no_update BEFORE UPDATE ON access_log
    FOR EACH ROW EXECUTE FUNCTION forbid_access_log_update();

CREATE ROLE argus_app;
CREATE ROLE argus_purge;
GRANT SELECT, INSERT ON access_log TO argus_app;    -- UPDATE·DELETE 미부여
GRANT SELECT, DELETE ON access_log TO argus_purge;  -- 파기 배치 전용
```

**setting 시드값**

| key | 기본값 | 근거 |
|---|---|---|
| `inspection_cycle` | `"1mo"` | 정책정의서 5-2 (§8② 자율, 기본 월 1회) |
| `detection_interval_min` | `5` | LOG-03 준실시간 배치 |
| `access_log_retention` | `"1y"` | §8① |
| `subject_ids_limit` | `1000` | API ① 2-2 |

**append-only 보장 (§8③)**

| 장치 | 내용 |
|---|---|
| DB 권한 분리 | 앱 계정(`argus_app`)은 INSERT·SELECT만. DELETE는 파기 전용(`argus_purge`)만. UPDATE는 아무에게도 없음 |
| 트리거 | `BEFORE UPDATE` 트리거로 예외 발생 (권한 설정 실수 대비 이중 장치) |
| 해시체인 | `hash = SHA-256(정규화된 레코드 내용)`, 정규화 대상에 **`prev_hash`와 `id`를 포함**해 앞 레코드와 연결(규칙은 아래 "정규화 규칙 v1"). 중간 레코드가 수정·삭제되면 이후 체인 검증이 깨짐 |
| 순차 기록 | append를 **advisory lock으로 직렬화** → `id`가 커밋 순서대로 증가해, 탐지 배치의 `id` 커서가 누락 없이 안전해짐 |
| 파기와의 공존 | 오래된 순서로 삭제하면 체인 시작점이 바뀜 → 파기 시 **남은 첫 레코드의 prev_hash를 `chain_anchor_hash`로 기록**하고, 검증은 그 앵커에서 시작 |
| 시드 데이터 | baseline용 과거 접속기록(요구사항 4-3)도 **동일한 append 함수를 통해** 주입해 체인을 성립시킨다. DB에 직접 INSERT하지 않는다 |

**해시체인 정규화 규칙 v1** (v0.3 — 2026-09-29 구현 M1에서 확정, 8절 미결 해소)

| 항목 | 규칙 |
|---|---|
| 대상 | `hash`를 제외한 `access_log` **전 컬럼**. `id`·`prev_hash`·`received_at` 포함 |
| `id`를 넣는 이유 | 탐지 배치 커서가 `id`라서, 레코드 순서를 바꿔치기해도 드러나게 |
| 시각 | UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ` (마이크로초 6자리) |
| IP | 정규형 (예: `2001:db8::1`) |
| UUID | 소문자 하이픈 형식 |
| NULL / 배열 | NULL은 JSON `null`, 배열은 순서 유지 |
| 직렬화 | JSON, **키 정렬, 공백 없음**(`,` `:`), UTF-8 (비ASCII 문자 이스케이프 안 함) |
| 해시 | SHA-256, 소문자 hex 64자 |
| 체인 시작 | 첫 레코드(파기 전)의 `prev_hash` = NULL. 파기 후에는 `destruction_history.chain_anchor_hash`에서 검증 시작 |

- 정규화 코드는 **append와 검증이 같은 함수를 공유**한다(argus-api `app/ledger/hashchain.py`) → 규칙이 한 곳에만 존재.
- `received_at`은 DB default에 맡기지 않고 **append 함수가 값을 정해 넣는다.** INSERT 전에 해시를 계산해야 하므로, DB가 채우는 값에 기대면 저장값과 해시값이 어긋날 수 있다. 같은 이유로 append 호출자는 모든 컬럼을 명시한다.
- 빈 `context`(`{}`)는 NULL로 저장한다.
- 규칙을 바꾸면 기존 체인 검증이 깨지므로 버전(v1)으로 관리한다. 변경이 필요하면 v2를 정의하고 전환 시점을 기록한다.

**DB 계정 구조** (v0.3 — 구현 M1에서 확정)

| 계정 | 로그인 | 용도 | `access_log` 권한 |
|---|---|---|---|
| 소유자 (`ARGUS_DB_USER`) | O | **마이그레이션 전용** | 전권 (소유자는 GRANT와 무관) — 트리거가 UPDATE만 차단 |
| 앱 로그인 (`ARGUS_APP_DB_USER`, `argus_app` 멤버) | O | argus-api·worker 접속 | SELECT·INSERT |
| `argus_app` | X (롤) | 앱 권한 묶음 | SELECT·INSERT |
| `argus_purge` | X (롤) | 파기 배치 (Skeleton 이후 로그인 계정 발급) | SELECT·DELETE |

- **API가 소유자 계정으로 접속하면 권한 분리가 무의미**해지므로 앱은 반드시 `argus_app` 멤버 계정으로 접속한다.
- 로그인 계정의 비밀번호는 마이그레이션이 아닌 별도 발급 스크립트에서 설정한다(비밀번호가 마이그레이션 코드·이력에 남지 않게).
- 앱 롤의 테이블별 권한: `access_log`·`detection_status_history`·`detection_log` = SELECT·INSERT(감사 추적은 append-only) / `source_system` = SELECT / 그 밖의 업무 테이블 = SELECT·INSERT·UPDATE(DELETE 없음). 이후 테이블을 추가할 때는 마이그레이션에서 **테이블마다 명시적으로 부여**한다(`ALTER DEFAULT PRIVILEGES` 미사용 — 테이블마다 최소 권한을 판단하기 위해).

**알려진 한계** (v0.3 — 보안성 검토 단계 과제, 아키텍처 설계서 8-6)

| 한계 | 설명 | 대응 후보 |
|---|---|---|
| 끝부분 삭제 | 맨 끝 레코드들을 지우면 남은 체인은 그대로 이어져 검증 통과 | 체인 머리(최신 `id`·`hash`)를 DB 밖(WORM 저장소 등)에 주기 기록하는 **외부 앵커링**. 부분적 교차 확인: `detection_batch_run.to_access_log_id` > 원장 최대 `id`면 삭제 흔적(단 같은 DB라 권한자는 함께 고칠 수 있음) |
| 전체 재계산 | DB 권한자가 체인을 처음부터 다시 계산해 덮어쓰면 탐지 불가 | 외부 앵커링 (위와 동일) |
| 소유자의 TRUNCATE·DELETE | 트리거는 UPDATE만 막음 | `BEFORE TRUNCATE`·`BEFORE DELETE` 트리거 (파기 롤 예외 처리 필요) |
| 앱 계정의 직접 INSERT | append 함수를 거치지 않은 INSERT를 DB가 막지 못함(검증 시 체인 불일치로 **사후 탐지**는 됨) | append를 `SECURITY DEFINER` 함수로 옮기고 앱에는 EXECUTE만 부여. 단 정규화를 SQL·Python 양쪽에 구현해야 하는 불일치 위험이 있어 M1에서는 기각 |

### 3-6. 룰 조건식 JSON 스펙

**condition**

```json
{ "all": [ { "field": "...", "op": "...", "value": ... } ] }
```

`all`(AND) / `any`(OR), 1단계 중첩 허용. `access_path`는 조건식이 아니라 룰의 독립 컬럼이다.

| field | 설명 | op |
|---|---|---|
| `action` | 수행업무 | `eq`, `in` |
| `data_category` | 데이터 유형 | `eq`, `in` |
| `result` | SUCCESS / FAILURE | `eq` |
| `subject_count` | 처리 건수 | `gte`, `lte`, `eq` |
| `occurred_time` | 발생 시각(HH:MM, KST) | `between` (자정 넘김 허용: `["22:00","06:00"]`). **시작 포함·끝 미포함, 분 단위**, 두 값은 서로 달라야 함 |
| `occurred_weekday` | 요일 | `in` (`["SAT","SUN"]`) |
| `actor_team` | 소속 (handler 조인) | `eq`, `in` |
| `actor_terminated_at_or_before` | **행위 시점에 이미 퇴직 상태였는지** (`handler.terminated_at <= occurred_at`). 명부에 없는 계정도 참 | `eq: true`만 허용 |

> **취급자 속성 조건의 미동기화 처리** — **2026-10-01 개정(기능 레이어 1)**: 해당 취급자가 명부에 없으면 판정 불가로 보고 **탐지한다(fail-closed)**. 퇴직자 룰은 상태 이력에 "취급자 명부에 없는 계정 — 퇴직 여부를 판정할 수 없어 탐지"를 남긴다. 판정 불가를 "이상 없음"으로 처리하면 탐지 누락이 된다는 원칙은 그대로다.
> - 기존 설계("평가 보류 후 다음 배치에서 재평가")를 바꾼 이유: 배치는 `id` 커서로 지나간 기록을 다시 보지 않으므로 보류하려면 **순찰 정지**(계정 하나가 동기화되지 않으면 모든 룰의 탐지가 멈춤)나 **보류 테이블**(스키마 변경)이 필요했다. relay가 명부를 기록보다 먼저 보내므로 정상이면 생기지 않고, 생겼다면 그 자체가 점검 대상이다.
> - 행위자 명부는 순찰마다 범위의 계정만 한 번 읽는다. `actor_team`은 쓰는 룰이 없어 아직 구현하지 않았다(v0.2 후보).

**aggregate** (AGGREGATE 전용)

```json
{ "window": "1h",  "measure": "LOG_COUNT", "compare": "ABSOLUTE", "threshold": 100 }
{ "window": "1mo", "measure": "LOG_COUNT", "compare": "RATIO_TO_BASELINE",
  "baseline": "PREV_MONTH_SAME_PERIOD", "threshold": 2.0 }
```

| 키 | 값 |
|---|---|
| `window` | `1h` / `1d` / `1mo` |
| `measure` | `LOG_COUNT` / `SUBJECT_COUNT`(처리 건수 합) / `DISTINCT_SUBJECT`(고유 정보주체 수) |
| `compare` | `ABSOLUTE` / `RATIO_TO_BASELINE` |
| `baseline` | `PREV_MONTH_SAME_PERIOD` (추후 `PREV_3M_AVG` 등) |
| `threshold` | 숫자 (ABSOLUTE: 양의 정수 / RATIO: 양수 배율) |
| `min_baseline` | (선택, RATIO 전용, 2026-10-01) 전월 같은 기간 집계값이 이보다 작으면(0 포함) **판정하지 않음**. 기본 룰 값 20 — 정책정의서 1-2 |

- 형식 규칙: 키 집합 정확히 일치, EVENT 룰에 집계 스펙 금지, RATIO는 월 윈도우 + `PREV_MONTH_SAME_PERIOD`만.
- 윈도우는 **KST 고정 구간**(매시 정각·0시·1일), 그룹 키 = 윈도우 시작 시각(`2026-09-15T14:00+09:00`). 전월 동기 = 전월 1일부터 같은 경과 시간(전월이 짧으면 전월 말에서 자름).

**기본 룰 시드** (정책정의서 1-3절 → JSON, 모두 `access_path='APP'`)

| 룰 | type | condition | aggregate | severity | Skeleton |
|---|---|---|---|---|---|
| 대량 다운로드 | EVENT | `action=DOWNLOAD ∧ subject_count≥50` | — | HIGH | **[S]** |
| 야간 접속 | EVENT | `action∈{READ,DOWNLOAD} ∧ occurred_time between 22:00~06:00` | — | MEDIUM | |
| 주말 접속 | EVENT | `occurred_weekday∈{SAT,SUN}` | — | LOW | |
| 결제수단 조회 | EVENT | `data_category=PAYMENT ∧ action=READ` | — | HIGH | |
| 대량 조회 | AGGREGATE | `action=READ` | 1h / LOG_COUNT / ABSOLUTE / 100 | HIGH | |
| 전월 대비 급증 | AGGREGATE | `action=READ` | 1mo / LOG_COUNT / RATIO / 2.0 / `min_baseline` 20 | MEDIUM | |
| **퇴직자 계정 접속** | EVENT | `actor_terminated_at_or_before=true` | — | HIGH | (6절 #2 제안) |

### 3-7. 탐지 배치의 데이터 흐름 (F-04)

1. 직전 성공 실행의 `to_access_log_id` **초과** ~ 현재 최대 `id` **이하**를 이번 범위로 잡고 `detection_batch_run` 생성(RUNNING — 별도 트랜잭션으로 먼저 기록해 진행 중인 순찰이 밖에서 보이게). **순찰 1회 최대 10,000건**, 밀려 있으면 쉬지 않고 다음 순찰. **새 기록이 없어도 순찰 이력을 남긴다**(v0.4 — "탐지가 주기적으로 수행됐다"는 점검 증적이자, worker 중단 기간과 기록 없는 기간을 구분하는 근거). 동시 실행은 advisory lock으로 막고, 비정상 종료로 남은 RUNNING은 다음 순찰이 정리한다.
2. 활성 룰 로드 → **켜진 룰 중 해석할 수 없는 룰(모르는 필드·연산자, 값 타입, 구조, 미지원 유형)이 하나라도 있으면 순찰 전체를 FAILED로 끝내고 책갈피를 유지**(v0.4 — 그 룰만 건너뛰면 책갈피가 넘어가 그 사이 기록이 그 룰로 영영 평가되지 않음. 기준값의 적절성은 해석 가능성과 별개로 담당자 판단) → **출처 ARGUS(Argus 자체 접속기록)는 평가에서 제외**(2026-10-01 — 룰은 감시 대상 시스템 기록에만 적용, 정책정의서 1-2. 순찰 건수에는 포함) → 룰의 `access_path`로 대상 로그를 먼저 필터 → `rule_exception` 해당 건 제외(화이트리스트는 v0.2)
3. **EVENT**: 조건식 판정 → 그룹 키 `(rule, source, actor, occurred_at의 날짜)`로 **진행 중** 탐지건 조회(`FOR UPDATE`) → 없으면 생성(`DETECTED`) + 룰 사본 + 상태 이력(시스템) + 알림, 있으면 하위 로그만 추가하고 `log_count`·`last_occurred_at`·`log_summary` 갱신. 같은 룰로 이미 어떤 탐지건에 붙은 기록은 다시 붙이지 않는다(재처리 시 증거 복제 방지)
   - **자동 소명 요청** (2026-10-01): 탐지건을 **새로 만들 때** 룰의 `auto_request`가 켜져 있고 행위자에게 재직 중·비활성 아닌 A5 계정이 있으면 같은 트랜잭션에서 `REQUESTED`(round 1, `explanation.requested_by` NULL) + 상태 이력 2줄(DETECTED·REQUESTED, 모두 시스템). 룰 사본에 `auto_request` 포함. `explanation (detection_id, round)` 유일 제약이 이중 요청을 DB에서 막는다
   - **날짜는 한국 시각(KST, +09:00 고정) 기준**(v0.4) — UTC 날짜를 쓰면 KST 새벽 0~9시 행위가 전날 탐지건으로 묶인다. 야간·주말 룰의 시각·요일 판정도 KST. DB 시간대 데이터에 의존하지 않도록 앱에서 고정 오프셋으로 계산
4. **AGGREGATE**: 범위의 기록으로 "다시 볼 (취급자, 윈도우)"를 고르고, 원장에서 **윈도우를 통째로 다시 집계**(순찰 경계에서 윈도우가 쪼개지지 않게, 늦게 도착한 기록 포함, 이번 순찰이 본 id까지만) → 기준 초과 시 같은 방식으로 upsert + `aggregate_value` 갱신, 상태 이력에 판정 근거("집계 100 ≥ 기준 100", "당월 63 / 전월 동기 23 = 2.74배 ≥ 2.0배")
   - **윈도우는 한 번만 판단**(2026-10-01): 같은 (룰, 취급자, 윈도우)의 탐지건이 **종결됐으면** 새 탐지건을 만들지 않음 — 정책정의서 2-3 AGGREGATE 예외
   - 월 윈도우는 순찰마다 한 달치를 다시 읽음 — 데이터가 커지면 윈도우별 누적값 테이블 검토(v0.2 후보)
5. 판정·탐지건·하위 로그·SUCCESS를 **한 트랜잭션**으로 기록. 실패 시 FAILED로 남기고 다음 실행이 같은 범위부터 재처리(멱등: 하위 로그 PK 중복은 무시). 실패 사유에는 예외 메시지 **첫 줄만** 남긴다(DB 오류의 상세 줄에 값이 실릴 수 있음)

> **왜 배치이고 5분인가** (2026-10-01 정리): Argus는 접근을 막는 통제가 아니라 **이미 일어난 행위를 보고 소명을 받는 탐지 통제**다. 소명은 사람의 속도로 진행되고, 배치는 집계형 룰·늦게 도착한 기록·실패 재처리·수집과의 분리·단일 VM 인프라에 모두 유리하다. 실시간 스트리밍은 인프라 부담으로 스트레치(요구사항정의서 5-3). 5분은 당일 대응이 가능하면서 부담 없는 간격이며 `setting`으로 조정한다.

> **커서는 `id` 기준이다.** `received_at`은 시계 오차·동시 삽입 때문에 누락 위험이 있어 커서로 쓰지 않고, 전송 지연 모니터링(`received_at - occurred_at`) 용도로만 쓴다. (API 명세 2-6과 일치)

---

## 4. 플랫폼 DB (무대장치 — 최소 구성)

```sql
-- [S] 취급자 계정 (관리자 UI 사용자, API ② 동기화 원천)
CREATE TABLE operator (
    id                 bigserial    PRIMARY KEY,
    login_id           varchar(64)  NOT NULL UNIQUE,
    password_hash      varchar(255) NOT NULL,
    name               varchar(50)  NOT NULL,
    team               varchar(50)  NOT NULL CHECK (team IN ('CS','MARKETING','OPS')),
    role               varchar(16)  NOT NULL CHECK (role IN ('ADMIN','CS','MARKETING','OPS')),  -- PLT-10/14
    employment_status  varchar(16)  NOT NULL DEFAULT 'ACTIVE' CHECK (employment_status IN ('ACTIVE','TERMINATED')),
    terminated_at      timestamptz,
    failed_login_count int          NOT NULL DEFAULT 0,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now()
);

-- [S] 회원 (정보주체)
CREATE TABLE member (
    id             bigserial    PRIMARY KEY,           -- 접속기록에는 이 값만 전송
    email          varchar(255) NOT NULL UNIQUE,
    password_hash  varchar(255) NOT NULL,
    name           varchar(50)  NOT NULL,
    phone          varchar(20),
    address        varchar(255),
    status         varchar(16)  NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','WITHDRAWN')),
    -- (2026-10-01 결정, 고객 화면 최소판) 로그인 실패 5회 → 15분 자동 잠금 — 정책정의서 4-3 "고객 인증". 컬럼명은 구현 시 확정
    failed_login_count int      NOT NULL DEFAULT 0,
    locked_until   timestamptz,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    withdrawn_at   timestamptz,
    CHECK (status <> 'WITHDRAWN' OR withdrawn_at IS NOT NULL)
);

-- 동의 항목 정의 (PLT-01)
CREATE TABLE consent_item (
    code            varchar(32)  PRIMARY KEY,   -- 'TOS','PRIVACY_REQUIRED','AGE_OVER_14'(필수, 2026-10-02),'MARKETING'
    name            varchar(100) NOT NULL,
    required        boolean      NOT NULL,      -- 필수/선택 (PIPA §22①·⑤)
    version         varchar(16)  NOT NULL,
    purpose         text         NOT NULL,      -- 수집·이용 목적 (§15②1호)
    items           text         NOT NULL,      -- 수집 항목 (§15②2호)
    retention       text         NOT NULL,      -- 보유·이용 기간 (§15②3호)
    effective_from  timestamptz  NOT NULL
);

-- 동의·철회 이력 (append) — PLT-01
CREATE TABLE member_consent (
    id            bigserial   PRIMARY KEY,
    member_id     bigint      NOT NULL REFERENCES member(id),
    item_code     varchar(32) NOT NULL REFERENCES consent_item(code),
    item_version  varchar(16) NOT NULL,          -- 동의 당시 약관 버전
    agreed        boolean     NOT NULL,          -- true=동의, false=미동의/철회
    acted_at      timestamptz NOT NULL DEFAULT now(),
    client_ip     inet,                          -- 동의 증적의 신뢰성 확보용
    method        varchar(16) NOT NULL DEFAULT 'WEB_FORM' CHECK (method IN ('WEB_FORM'))
);
CREATE INDEX ix_member_consent ON member_consent (member_id, item_code, acted_at DESC);

-- [S] outbox (Argus 전송 대기)
CREATE TABLE outbox (
    id              bigserial    PRIMARY KEY,
    event_id        uuid         NOT NULL UNIQUE,
    topic           varchar(16)  NOT NULL CHECK (topic IN ('ACCESS_LOG','HANDLER')),
    payload         jsonb        NOT NULL,             -- API 명세 2-1 / 3-1의 이벤트 1건
    status          varchar(16)  NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','DEAD')),
    attempts        int          NOT NULL DEFAULT 0,
    next_retry_at   timestamptz  NOT NULL DEFAULT now(),
    last_error      text,
    created_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_outbox_pending ON outbox (status, next_retry_at) WHERE status = 'PENDING';

-- 결제수단 — 2026-10-01 재설계 → 2026-10-02 구현 확정: 아래 설계안 대신 `payment` + `refund_account` 두 테이블(표 참고)
-- ① 카드: PG 목업. 플랫폼은 카드번호를 받지도 저장하지도 않음(끝 4자리도 미저장 — 카드 등록·간편결제 기능이 없어 쓸 목적이 없음, 최소수집)
--    → 주문의 결제 정보로 PG 거래 정보만 저장: 결제수단 종류, 카드사, PG 거래번호, 승인 시각, 금액
-- ② 계좌: 고객이 마이페이지에서 등록하는 환불계좌만 직접 수집 — §7② 6호(계좌번호) 앱 레벨 암호화
CREATE TABLE refund_account (                -- (설계안) 기존 payment_method의 계좌 부분
    id                  bigserial   PRIMARY KEY,
    member_id           bigint      NOT NULL REFERENCES member(id),
    bank_name           varchar(50) NOT NULL,
    account_number_enc  bytea       NOT NULL,   -- AES-GCM 앱 레벨 암호화 (키 PAYMENT_ENCRYPTION_KEY, .env — DB와 분리, 절대 규칙 #11)
    account_last4       char(4)     NOT NULL,   -- 관리자 화면 기본 표시용
    created_at          timestamptz NOT NULL DEFAULT now()
);
-- 관리자 화면은 끝 4자리만 기본 표시, "전체 보기"는 별도 동작 → 데이터 유형 PAYMENT로 접속기록 → 결제수단 조회 룰(HIGH)로 항상 탐지
-- 기각: 기존 설계(카드번호 직접 저장·암호화) — 실무 커머스는 PCI DSS·여신전문금융업법 때문에 카드번호를 PG에 맡김. 보안성 검토에서 수집 최소화 위반으로 지적받을 구조
```

**결제 관련 테이블 — 구현 확정 (2026-10-02, 플랫폼 마이그레이션 0003. 정확한 컬럼은 레포 마이그레이션 기준)**

| 테이블 | 담는 것 | 담지 않는 것 / 규칙 |
|---|---|---|
| `payment` | PG 승인 결과 — 결제수단 종류, 카드사, **PG 거래번호**(서버 생성 `MOCKPG-…`), 승인 시각, 금액(서버가 상품 가격으로 결정) | **카드번호 컬럼 자체가 없음**(컬럼 집합을 테스트로 고정). 주문 API는 카드번호·금액을 보내도 무시 |
| `refund_account` | 은행, 예금주, **계좌번호 암호문**, 끝 4자리(평문), 회원당 1개 | **AES-256-GCM**, 버전 1바이트 + nonce 12바이트, **AAD = `refund_account:{회원번호}`**(암호문을 다른 회원 행으로 옮기면 복호화 실패). 키 `PAYMENT_ENCRYPTION_KEY`(16진수 64자, 없거나 형식 오류면 기동 거부) — platform-api·seed에만 전달 |
| `orders` | 상품 1개, 상태 `PAID`만 | 취소·환불 처리는 v0.2 |

- `payment_method` 한 테이블 대신 둘로 나눈 이유: 카드는 저장할 번호가 없어 컬럼 절반이 항상 비고, **성격(거래 기록 / 고객 정보)과 보존 규칙(5년 분리보관 / 탈퇴 즉시 삭제)이 다르다.**
- 끝 4자리를 평문으로 둔 이유: 목록·상세마다 복호화하지 않게 해 **복호화 지점을 "전체 보기" 하나로 좁힘**. 고객 본인 화면도 끝 4자리만(세션 탈취 시 노출 최소화).
- 관리자 접속기록: 주문 목록 `READ`·`ORDER`, 회원 상세 `READ`·`MEMBER_BASIC`(환불계좌 끝 4자리 포함), **환불계좌 전체 보기 `READ`·`PAYMENT`** → 결제수단 조회 룰(HIGH)로 항상 탐지·자동 소명 요청.

```sql

CREATE TABLE product (
    id     bigserial    PRIMARY KEY,
    name   varchar(100) NOT NULL,
    price  int          NOT NULL
);

CREATE TABLE orders (
    id          bigserial   PRIMARY KEY,
    member_id   bigint      REFERENCES member(id) ON DELETE SET NULL,
    product_id  bigint      NOT NULL REFERENCES product(id),
    amount      int         NOT NULL,
    status      varchar(16) NOT NULL,
    ordered_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE inquiry (
    id           bigserial    PRIMARY KEY,
    member_id    bigint       REFERENCES member(id) ON DELETE SET NULL,
    title        varchar(200) NOT NULL,
    body         text         NOT NULL,
    status       varchar(16)  NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ANSWERED')),
    answer       text,
    answered_by  bigint       REFERENCES operator(id),
    created_at   timestamptz  NOT NULL DEFAULT now(),
    answered_at  timestamptz
);

-- 권한 부여·변경·말소 이력 (§5③, 3년) — Should
CREATE TABLE operator_permission_history (
    id           bigserial   PRIMARY KEY,
    operator_id  bigint      NOT NULL REFERENCES operator(id),
    change_type  varchar(16) NOT NULL CHECK (change_type IN ('GRANT','CHANGE','REVOKE')),
    detail       jsonb       NOT NULL,
    changed_by   bigint      REFERENCES operator(id),
    changed_at   timestamptz NOT NULL DEFAULT now()
);

-- 법정 보존 항목 분리보관 (PIPA §21③)
CREATE TABLE retained_member_record (
    id                  bigserial    PRIMARY KEY,
    original_member_id  bigint       NOT NULL,          -- FK 아님 (원본 member 행은 파기됨)
    retain_reason       varchar(64)  NOT NULL,          -- 'PAYMENT_5Y','DISPUTE_3Y'
    legal_basis         varchar(100) NOT NULL,          -- '전자상거래법 시행령 §6'
    data                jsonb        NOT NULL,          -- 보존 목적에 필요한 최소 항목만
    retain_until        timestamptz  NOT NULL,
    created_at          timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_retained_until ON retained_member_record (retain_until);

-- 파기 이력
CREATE TABLE destruction_history (
    id             bigserial    PRIMARY KEY,
    executed_at    timestamptz  NOT NULL DEFAULT now(),
    target_type    varchar(32)  NOT NULL,     -- 'MEMBER','ACCESS_LOG_OUTBOX' 등
    cutoff_at      timestamptz  NOT NULL,
    deleted_count  int          NOT NULL,
    legal_basis    varchar(100) NOT NULL
);
```

### 4-1. 회원 탈퇴 시 파기·분리보관 절차 (v0.1 미결 해소)

> **2026-10-01 개정 — v0.1은 탈퇴 즉시 파기** (기능 레이어 7, 고객 화면 최소판): 파기 배치는 v0.2라, 아래 절차대로 "상태만 바꾸고 배치를 기다리면" v0.1 동안 탈퇴 회원 정보가 계속 남아 **PIPA §21(지체 없이 파기) 위반 상태**가 된다. 따라서 **탈퇴 처리 트랜잭션 안에서** 아래를 수행한다(2026-10-02 구현). 아래 번호 절차는 v0.2 파기 배치의 설계로 유지. **보안성 검토 때 재확인.**
>
> | 순서 | v0.1 탈퇴 처리 (비밀번호 재확인 후, 한 트랜잭션) |
> |---|---|
> | 1 | 주문이 있으면 `retained_member_record`에 **PAYMENT_5Y**(전자상거래법 시행령 §6①3호, 5년) — 주문번호·상품·금액·일시·PG 거래번호·카드사 + 분쟁 시 본인 확인용 이름·이메일·전화. 비밀번호·주소·환불계좌는 담지 않음 |
> | 2 | 문의가 있으면 **DISPUTE_3Y**(시행령 §6①4호, 3년) — 제목·본문·답변·시각 + 연락처를 **옮기고**, 운영 테이블 `inquiry`의 제목·본문·답변은 "(탈퇴 회원 문의 — 분리보관됨)"으로 지움(번호·상태·시각·답변자만 남음) |
> | 3 | 환불계좌·동의 이력·회원 행 **실제 삭제**(`orders.member_id`는 SET NULL) |
> | 4 | `destruction_history`에 MEMBER 1건(개인정보 미기록) |
>
> - 문의 **내용을 옮기고 지우는** 이유: 아래 3번은 "member_id SET NULL로 끊겨 통계 데이터가 된다"였지만, 문의 본문에는 고객이 쓴 개인정보(주소·전화 등)가 있을 수 있어 **연결만 끊으면 사실상 보관 기간 없는 보관**. 기각: 그대로 둠(§21 위반 소지), 문의 행 삭제(CS 처리 통계·답변자 이력 소실)
> - **보안성 검토 이월**: 분리보관 데이터의 연락처 범위(최소수집 관점), **분리보관 테이블의 접근 권한 분리 미이행**(플랫폼 DB 계정 하나 — 아래 "분리보관의 의미"의 권한 분리가 실제로는 안 돼 있음)

1. 탈퇴 요청 → `member.status='WITHDRAWN'`, `withdrawn_at` 기록
2. 파기 배치가 탈퇴 회원을 집어 **법정 보존 대상 항목만** `retained_member_record`로 옮긴다
   - 대금결제·재화공급 기록 5년, 소비자 불만·분쟁처리 기록 3년 (전자상거래법 시행령 §6)
   - `data`에는 보존 목적에 **필요한 최소 항목만** 담는다 (주문번호·금액·일시·연락처 등). 비밀번호·주소 전체는 담지 않는다
3. `member` 행과 결제수단(환불계좌)을 **실제 삭제**(논리삭제 아님). `orders`·`inquiry`의 `member_id`는 `SET NULL`로 끊어져 개인과 연결되지 않는 통계 데이터가 된다
4. `destruction_history`에 파기 이력 기록 (개인정보 자체는 미기록)
5. `retain_until` 경과 시 `retained_member_record`도 파기

> **분리보관의 의미**: 같은 DB의 별도 테이블로 옮기고 접근 권한을 달리 부여하는 것으로 §21③의 "다른 개인정보와 분리하여 저장·관리"를 이행한다. 물리적 DB 분리까지는 규모상 과도하다고 판단했다.
> **동의 이력**: `member_consent`는 회원 파기 시 같이 삭제한다. 동의 증적의 보관 필요성과 §21① 파기 의무가 충돌하는데, 정보주체가 탈퇴하면 동의 자체가 실효되므로 파기를 우선한다. (동의 기록의 법정 보관기간을 정한 규정은 확인하지 못했다 — 실무에서는 분쟁 대응 목적으로 일정 기간 남기는 경우가 있어, 운영 정책 판단 사항으로 남긴다.)

---

## 5. 요구사항 ↔ 스키마 대조표

| 요구사항 | 대응 |
|---|---|
| PLT-01 회원가입·동의 | `member`, **`consent_item`·`member_consent`** |
| PLT-02 로그인 | `member`, `operator.failed_login_count` |
| PLT-03 마이페이지·탈퇴 | `member.status/withdrawn_at`, 4-1절 절차 |
| PLT-04·05 주문·결제 | `orders`, `product`, `payment_method`(암호화 컬럼) |
| PLT-06·17 1:1 문의 | `inquiry` |
| PLT-10 관리자 진입 | **`operator.role`** |
| PLT-11~13·16 조회·수정·다운로드 | 로깅 대상 (스키마 아님) |
| PLT-14 권한 관리 | `operator.role`, `operator_permission_history` |
| PLT-15 접속기록 생성·전송 | `outbox` |
| LOG-01 수집·저장 | `access_log` |
| LOG-02 조회·검색 | `access_log` + **GIN·복합 인덱스** |
| LOG-03 탐지 | `detection_rule`, `detection`, `detection_batch_run` |
| LOG-04 룰 빌더 | `detection_rule.condition/aggregate` |
| LOG-05 알림센터 | `detection`, **`notification`** |
| LOG-06~08 소명 | `explanation`, `explanation_attachment`, `detection_status_history` |
| LOG-09 보고서 | **`inspection_report`** |
| LOG-10 마스킹/해제 | `access_log.action='UNMASK'` + `context.reason/target`, 2-1절 표기 규칙 |
| LOG-11 심각도 | `detection_rule.severity`, `detection.severity` |
| LOG-12 화이트리스트 | `rule_exception` |
| LOG-13 룰 변경 이력 | **`detection_rule_history`** |
| LOG-14 소명 이력 누적 | 쿼리 집계 |
| LOG-15 시간차 | 계산 |
| LOG-16 이메일 알림 | **`notification.channel='EMAIL'`** |
| LOG-17 Argus 자체 기록 | `access_log` (`source_system='ARGUS'`) |
| 2단계 DB 직접 접근 | `access_path='DB'` + `context`에 쿼리문·결과 건수 |

---

## 6. 정책정의서 반영 사항

| # | 발견 | 제안 | 상태 |
|---|---|---|---|
| 1 | 정책정의서 2-2는 같은 `(취급자, 룰, 날짜)` 탐지건이 "있으면 하위 로그만 추가"라고 정의. 그런데 그 건이 **이미 승인·종결**된 뒤 같은 날 같은 행위가 또 발생하면 새 행위가 종결된 건에 붙어 **아무도 보지 않게 됨** | **진행 중(DETECTED·REQUESTED·SUBMITTED·REJECTED) 건에만 추가**하고, 종결됐으면 새 탐지건 생성 | 정책정의서 2-3절 반영, DB 부분 유니크 인덱스, 7절 검증 완료 |
| 2 | 정책정의서 4-2는 재직상태를 "퇴직자 계정 접속 탐지용"으로 유지한다고 하면서 기본 룰 목록(1-3)에는 없음 | 기본 룰에 **"퇴직자 계정 접속"(EVENT, HIGH)** 추가. 판정은 `handler.terminated_at <= occurred_at`(행위 시점 기준) | 정책정의서 1-3절 반영, 스키마·룰 시드 반영 |
| 3 | 액터별_플로우 8절 마스킹 정책이 **화면만** 다루고 보고서 파일은 다루지 않아, export가 마스킹 우회 경로가 됨 | 보고서 기본 마스킹, 언마스킹 export는 **사유 필수 + `EXPORT` 기록** | 정책정의서 6-1절 반영, `inspection_report` CHECK |
| 4 | 탐지 후 룰이 수정되면 과거 탐지건의 판단 근거가 흐려짐 | 탐지건에 룰 스냅샷 저장 | 반영 완료 |
| 5 | "탐지·소명 이력은 접속기록과 동일 이상 보관"(5-1)인데 근거 로그는 1년 후 파기되어 탐지건이 공허해짐 | 탐지건에 `log_summary` 스냅샷 | 정책정의서 5-1 반영 + 7절 검증 완료 |

---

## 7. 검증 결과 (PostgreSQL 16, 2026-09-23)

이 문서의 전체 DDL을 실제 PostgreSQL 16 인스턴스에 적용했다. Argus 17개 / 플랫폼 12개 테이블이 모두 생성되고, 아래 시나리오가 의도대로 동작했다.

| # | 시나리오 | 결과 |
|---|---|---|
| 1 | 같은 그룹에 진행 중 탐지건 중복 생성 | 차단 ✅ |
| 2 | 같은 탐지건·같은 차수 소명 중복 생성 | 차단 ✅ |
| 3 | 종결(APPROVED) 후 같은 그룹 새 탐지건 생성 | 허용 ✅ |
| 4 | `access_log` UPDATE 시도 | 트리거 차단 ✅ |
| 5 | AGGREGATE 룰을 집계 설정 없이 생성 | 차단 ✅ |
| 6 | `LOGIN` 외 행위를 `subject_type` 없이 기록 | 차단 ✅ |
| 7 | 퇴직 상태로 변경하며 `terminated_at` 누락 | 차단 ✅ |
| 8 | 언마스킹 보고서를 사유 없이 생성 | 차단 ✅ |
| 9 | 정보주체 ID 배열 검색 (`subject_ids @> ARRAY['10293']`) | 정상 조회 ✅ |
| 10 | 파기 시뮬레이션 — `access_log` 삭제 후 탐지건 | `detection_log` 링크는 0으로 사라지고 `log_summary`(subject_count_sum=120)는 보존 ✅ |

또한 소명 전체 사이클(요청 → 제출 → 반려 → 재요청 round+1 → 승인)을 실제 INSERT/UPDATE로 수행해 상태 전이와 제약이 충돌하지 않음을 확인했다.

---

## 8. 미결 / 구현 시 확정

- [x] 회원 탈퇴 시 법정 보존 항목 분리보관 구조 → 4-1절 (v0.2)
- [x] 해시체인 정규화(canonical) 규칙 세부 → 3-5절 "정규화 규칙 v1" (v0.3, 2026-09-29 구현 M1). 검증 함수 `verify_chain` 구현
- [x] DB 계정·권한 구조 → 3-5절 "DB 계정 구조" (v0.3, 2026-09-29 구현 M1)
- [x] Argus 비밀번호 해시 → argon2id
- [x] 알림 테이블 필요 여부 → `notification` 추가 (이메일 발송 성공/실패 추적 때문에 필요)
- [ ] 동의 기록의 탈퇴 후 보관 여부 — 4-1절 각주 (운영 정책 판단)
- [ ] 첨부파일 파기: 소명 이력 파기 시 볼륨의 실제 파일도 함께 삭제하는 절차 (구현 시)
