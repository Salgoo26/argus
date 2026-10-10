# DB 스키마 (Argus / 플랫폼)

> 작성 2026-09-23 · 최종 개정 2026-10-10
> 관련 문서: [아키텍처_설계서](architecture.md), [API명세서_시스템간](api-spec.md), [정책정의서](policy.md), [액터별_플로우](actor-flows.md), [요구사항정의서](requirements.md)
> DBMS: PostgreSQL 16 (플랫폼 DB / Argus DB 별도 인스턴스)
> 표기: **[S]** = Walking Skeleton에 필요한 테이블. 컬럼은 전체를 정의하되 Skeleton에서는 [S] 테이블만 생성한다.
> **검증**: 이 문서의 전체 DDL은 PostgreSQL 16에 실제 적용해 생성 성공 및 제약조건 동작(10개 시나리오)을 확인했다. 결과는 7절.
> 목차: 0 개정 내역 · 1 개념 데이터 모델 · 2 설계 원칙 · 3 Argus DB(3-1 기준·계정 · 3-2 접속기록 원장 · 3-3 탐지 룰 · 3-4 탐지건·소명·알림 · 3-5 무결성·배치·파기 · 3-6 룰 조건식 JSON · 3-7 탐지 배치 흐름 · 3-8 보호 대상 등록부) · 4 플랫폼 DB · 5 요구사항 대조표 · 6 정책정의서 반영 사항 · 7 검증 결과 · 8 미결

---

## 0. 개정 내역

#1~14는 v0.2 개정이다. 요구사항정의서(PLT-01~17 / LOG-01~17)·정책정의서·액터별 플로우를 v0.1과 전수 대조해 보완했다. #15는 v0.1 보강(Argus 마이그레이션 0014~0022, 플랫폼 마이그레이션 0009)이다.

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
| 15 | v0.1 보강 | 수행업무 `LOGOUT`(0014) · `notification` 화면 알림 구조로 구현, `explanation.due_at`(0015) · `push_subscription`(0016) · `access_log` DELETE·TRUNCATE 트리거, 보고서 `integrity` 기준점(0017) · `detection_log.attached_at`, EVENT 탐지건 그룹 키에 `data_category`·`action_group`(0018) · 룰 필드 `client_ip`, 측정 `DISTINCT_IP`·`MAX_SUBJECT_REPEAT`, 기본 룰 3개(0019) · `argus_user_history`(0020) · 보호 대상 등록부 4개 테이블(0021) · 시드 룰 설명 정리(0022) · 플랫폼 `operator_permission_history`, `operator.must_change_password`(플랫폼 0009) · DB 직접 기록의 회원번호 추출과 `context.registry_version` | 고시 §5①③(권한 부여·변경·말소 기록), §8②(점검·사후조치), §8③(위·변조 방지), 안내서 61~62쪽(권한 부여·말소 내역), FAQ 147(로그아웃), 95쪽(비정상 행위 예시, 위·변조 확인 정보 별도 보관), 129쪽(이상행위 탐지 시 알림) |

---

## 1. 개념 데이터 모델

### 1-1. 엔티티

| 시스템 | 엔티티 | 설명 | 근거 |
|---|---|---|---|
| Argus | `source_system` | 접속기록을 보내는 시스템. `PLATFORM`, `ARGUS`(자체 기록) | LOG-17, 2단계 확장 |
| Argus | `handler` | 플랫폼에서 동기화된 개인정보취급자 | API ② |
| Argus | `argus_user` / `argus_user_history` | Argus 로그인 계정. 정보보호 담당자(A4) / 취급자(A5). 계정 이력(부여·변경·말소·재활성화·잠금 해제) | 액터별 플로우 1절, 고시 §5①③ |
| Argus | **`access_log`** | 원장. append-only + 해시체인 | §2 3호, §8①③ |
| Argus | `detection_rule` / `detection_rule_history` | EVENT / AGGREGATE 룰과 변경 이력 | 정책정의서 1절, LOG-04·13 |
| Argus | **`detection`** | 그룹핑 단위의 탐지 결과, 상태 7종 | 정책정의서 2·3절 |
| Argus | `detection_log` | 탐지건의 하위 근거 로그 (N:M) | 정책정의서 2-2 |
| Argus | `detection_status_history` | 모든 상태 전이의 감사 추적 | 점검 수행 증적 |
| Argus | **`explanation`** / `explanation_attachment` | 차수별 소명과 근거자료 | LOG-06~08 |
| Argus | `rule_exception` | 화이트리스트 (시스템 계정 포함) | LOG-12, 정책정의서 5-3 |
| Argus | `notification` / `push_subscription` | 화면 알림(받는 사람별, 본문 없음)과 웹 푸시 구독. 이메일 알림은 v0.2 | LOG-05·16 |
| Argus | `protected_column` / `protected_column_history` | 보호 대상 등록부 — 컬럼별 개인정보 항목·데이터 유형·회원 식별 열과 등록·변경·해제 이력 | 고시 §8①, 아키텍처 설계서 3-4 |
| Argus | `db_schema_column` / `db_schema_receipt` | 게이트웨이가 보낸 DB 구조 목록(테이블·컬럼 이름과 자료형만)과 수신 기록 | 아키텍처 설계서 3-4 |
| Argus | `inspection_report` | 점검 보고서 생성 이력 | LOG-09, §8② |
| Argus | `detection_batch_run` | 탐지 배치 실행 이력 + 커서 | F-04 |
| Argus | `destruction_history` | 파기 증적 (개인정보 미포함) | 정책정의서 5-3 |
| Argus | `setting` | 점검 주기 등 | §8② (설정값) |
| 플랫폼 | `operator` / `operator_permission_history` | 관리자 UI 사용자, 동기화 원천, 권한 이력(부여·변경·말소) | PLT-10·14, 고시 §5①③ |
| 플랫폼 | `db_access_token` | DB 접속 토큰 발급 기록(감사용, 토큰 값 미저장) | 2단계(2티어), 아키텍처 3-4 |
| 플랫폼 | `member` | 고객 = 정보주체 | PLT-01~03 |
| 플랫폼 | `consent_item` / `member_consent` | 동의 항목 정의와 동의·철회 이력 | **PLT-01** |
| 플랫폼 | `orders` / `payment` / `refund_account` / `product` / `inquiry` | 감시 대상 시스템의 업무 데이터 | PLT-04~06 |
| 플랫폼 | **`outbox`** | Argus 전송 대기 버퍼 | 아키텍처 3-3 |
| 플랫폼 | `retained_member_record` / `destruction_history` | 분리보관·파기 증적 | PIPA §21③, 정책정의서 5-3 |

구현된 테이블은 Argus 21개(마이그레이션 0022 기준), 플랫폼 15개(플랫폼 마이그레이션 0009 기준, `shipping_address` 포함)다. Argus의 `rule_exception`·`destruction_history`는 설계만 있고 화이트리스트·파기 배치와 함께 v0.2에 만든다.

### 1-2. 관계

```
[Argus]
 source_system  1 ──< handler,  1 ──< access_log
 handler        1 ──o argus_user            (role=HANDLER일 때 1:1)
 detection_rule 1 ──< detection,  1 ──< detection_rule_history
 detection     >──< access_log              (detection_log로 N:M)
 detection      1 ──< detection_status_history,  1 ──< explanation,  1 ──o< notification
 explanation    1 ──< explanation_attachment
 argus_user     1 ──< inspection_report,  1 ──< notification,  1 ──< push_subscription
 argus_user_history                         (FK 없음 — 계정 id와 아이디를 함께 저장)
 source_system  1 ──< protected_column (1 ──< protected_column_history),  1 ──< db_schema_column,  1 ──< db_schema_receipt

[플랫폼]
 member    1 ──< member_consent >── 1 consent_item
 member    1 ──< refund_account,  1 ──< orders (1 ── payment),  1 ──< inquiry
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
| 최소 보유 | Argus에는 회원의 **내부 PK만**. 이름·연락처 등은 저장하지 않음. 보호 대상 등록부·DB 구조 목록도 테이블·컬럼 이름과 분류만 담고 데이터 값은 담지 않음 |
| 불변성 | `access_log`는 INSERT만 허용 (3-5절). 권한·계정 이력과 보호 대상 등록 이력도 UPDATE·DELETE를 트리거로 거부 |
| 판단 근거 보존 | 탐지건에 **탐지 당시 룰 스냅샷**과 **하위 로그 요약**을 저장 — 룰이 바뀌거나 원본 로그가 파기돼도 "왜 탐지됐는지"가 남음 |
| 감사 추적 | 탐지건 상태 변경은 전부 `detection_status_history`에 행으로 남김 |
| 시점 판정 | 재직상태처럼 변하는 속성은 **행위 시점 기준**으로 판정할 수 있게 `terminated_at`을 둠 |

### 2-1. 정보주체 식별값 표기 규칙 (문서 간 통일)

문서 간 표기를 아래로 통일한다.

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
    last_event_at      timestamptz  NOT NULL,      -- 순서 역전 방지 + 중복 판정 기준 (API ② 3-1)
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

-- Argus 계정 이력 (고시 §5①③, 마이그레이션 0020) — 플랫폼 operator_permission_history와 같은 방식
CREATE TABLE argus_user_history (
    id              bigserial    PRIMARY KEY,
    user_id         bigint       NOT NULL,         -- FK 없음: 계정 행이 정리돼도 이력은 남아야 함
    login_id        varchar(64)  NOT NULL,         -- 대상 계정 아이디를 함께 저장
    change_type     varchar(8)   NOT NULL
                    CHECK (change_type IN ('GRANT','CHANGE','REVOKE','RESTORE','UNLOCK')),
    before_role     varchar(16),
    after_role      varchar(16)  NOT NULL,
    before_status   varchar(16),
    after_status    varchar(16)  NOT NULL,
    reason          varchar(500) NOT NULL CHECK (length(btrim(reason)) > 0),   -- 사유 필수
    actor_user_id   bigint,                        -- NULL = 운영 스크립트·명부 동기화(시스템)
    actor_login_id  varchar(64),
    created_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_argus_user_history_created ON argus_user_history (created_at DESC);
CREATE INDEX ix_argus_user_history_user    ON argus_user_history (user_id, created_at DESC);
-- UPDATE·DELETE는 행 트리거(trg_argus_user_history_append_only)로 거부(소유자 포함). 앱 계정은 SELECT·INSERT만
```

**`argus_user_history` 구분**

| 구분 | 뜻 |
|---|---|
| `GRANT` | 담당자 권한 부여(취급자 → 담당자 전환, 운영 스크립트로 담당자 계정 생성), 명부 동기화로 취급자 계정 생성 |
| `CHANGE` | 담당자 → 취급자(명부에 연결된 계정만) |
| `REVOKE` | 비활성화(말소) — 계정 관리 API 또는 플랫폼 퇴직 동기화 |
| `RESTORE` | 재활성화(명부에서 재직 중일 때만) |
| `UNLOCK` | 로그인 5회 실패 잠금 해제 |

- 마이그레이션 0020이 이미 있던 계정마다 `GRANT` 1건(사유 "초기 계정")을 채웠다. 비밀번호 설정(`set-password`)은 이력을 남기지 않는다.
- 계정 관리는 담당자 전용 API(`/api/users/*`, API명세서 7-1)와 운영 스크립트(`create-officer`·`unlock`, `--reason` 필수)로 한다. 관리 화면(Argus 메뉴 "계정")은 v0.2에서 노출한다.
- 계정 관리 행위는 Argus 자체 접속기록에서 제외한다(정보주체 처리가 아님). 이 이력이 증적이다.
- `TRUNCATE`는 트리거로 막지 않는다(원장 `access_log`만 막음). 이력 보관·파기와 함께 v0.2에서 다룬다.

> **취급자 동기화의 중복 판정**: `handler`는 API ②의 `event_id`를 저장하지 않는다. 이벤트가 상태 전체(스냅샷)를 싣고 오므로 `occurred_at > last_event_at`일 때만 upsert하고, 그 외는 변경 없이 중복으로 처리한다. event_id 저장 테이블(`handler_event`)은 기각 — 사유는 API명세서 3-1.
>
> **A5의 조회 범위**: 취급자(A5)는 **자신의 `handler_id`에 연결된 탐지건 중 소명 요청을 받은 건(`round ≥ 1`)만** 볼 수 있다(담당자 검토 대기 `DETECTED` 건은 제외, 남의 건은 404). DB 제약이 아니라 애플리케이션 계층에서 강제하며(모든 조회 쿼리에 actor 필터 주입), 이 규칙은 액터별_플로우 F-06 #2와 대응한다. 취급자의 "내 접속기록"도 같은 방식으로, 서버가 `access_log`의 행위자를 연결된 `handler`의 (출처, 아이디)로 고정하고 출처 `PLATFORM`만 조회한다.
>
> **본인 건 판정**: 담당자(A4)가 탐지건 행위자 본인이면 상태 전이를 거부한다(정책정의서 3-5, 403 `SELF_REVIEW_FORBIDDEN` — API명세서 7-1). 본인 여부는 `argus_user.handler_id`로 연결된 명부의 (출처, 아이디)와 탐지건의 `(source_system_id, actor_login_id)`를 비교하고, 연결이 없으면 담당자 로그인 아이디와 출처 `PLATFORM` 탐지건의 `actor_login_id`를 비교한다. 별도 컬럼은 없다.

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
    received_at       timestamptz  NOT NULL DEFAULT now(),        -- append 함수가 항상 명시 지정 (3-5절)
    prev_hash         char(64),
    hash              char(64)     NOT NULL,

    CHECK (action IN ('LOGIN','LOGOUT','READ','CREATE','UPDATE','DELETE','DOWNLOAD','EXPORT','UNMASK')),
    CHECK (data_category IN ('MEMBER_BASIC','PAYMENT','ORDER','INQUIRY','ACCESS_LOG','NONE')),
    CHECK (action IN ('LOGIN','LOGOUT') OR subject_type IS NOT NULL)   -- §2 3호: 처리한 정보주체 정보 필수
);
-- LOGOUT은 마이그레이션 0014에서 추가(위 CHECK 두 개만 바꿈 — 기존 행과 해시체인에는 영향 없음)

CREATE INDEX ix_access_log_actor_time ON access_log (source_system_id, actor_login_id, occurred_at);
CREATE INDEX ix_access_log_occurred   ON access_log (occurred_at);
CREATE INDEX ix_access_log_filter     ON access_log (source_system_id, action, occurred_at);
CREATE INDEX ix_access_log_subject    ON access_log USING gin (subject_ids);   -- LOG-02 정보주체 검색
```

- **로그인·로그아웃**: `LOGIN`·`LOGOUT`은 데이터 유형 `NONE`, 정보주체 없이 기록한다. 로그아웃은 인증된 사용자가 요청했을 때만 남긴다(쿠키가 없거나 만료된 요청은 행위자가 없어 기록하지 않음). 존재하지 않는 아이디의 로그인 실패와 DB 세션 종료는 기록 대상이 아니다.
- **Argus 자체 접속기록(LOG-17)**도 같은 테이블에 `source_system = ARGUS`로 저장한다. argus-api 미들웨어가 outbox 없이 동일한 append 함수로 직접 기록한다.
- 취급자 매칭은 FK가 아니라 `(source_system_id, actor_login_id)` ↔ `handler(source_system_id, login_id)` 조인. 동기화 전에 도착한 로그도 원문 그대로 저장되고 나중에 자연히 매칭된다.
- **Argus 자체 기록의 정보주체 처리 규칙**: Argus에서 탐지건·로그를 조회하는 행위는 회원 PK를 눈으로 보는 행위이지만, 이때 `subject_ids`에 회원 PK를 **다시 적재하지 않고** `subject_count`만 기록한다. Argus 접속기록이 회원 식별자를 중복 축적하면 최소처리 원칙에 반하기 때문이다. 예외적으로 `UNMASK`는 어떤 대상을 열어봤는지가 감사 핵심이므로 `context.target`에 대상 탐지건 ID를 남긴다.
- **정보주체 ID로 검색하는 행위**(LOG-02)도 식별값을 평문으로 다루는 행위이므로 `READ` + `request_query_keys`에 검색 조건 키를 기록한다. 검색어 값 자체는 기록하지 않는다. 플랫폼 관리자 검색(회원·주문·문의)도 같다 — 검색 조건은 요청 본문으로 받고 `request_query_keys`에는 키 이름만(정렬) 남기며, 정보주체는 결과로 보인 회원 PK 전부다(0건이어도 기록).

**DB 직접 기록(`access_path = 'DB'`)의 정보주체와 `context`**

DB 접근 게이트웨이가 보내는 기록은 `request_*` 칸이 비고 `context`에 아래 키를 싣는다. 이 키는 `access_path = 'DB'`일 때만 받는다(형식은 API명세서 2-2·2-3).

| `context` 키 | 내용 |
|---|---|
| `db_user` | DB 접속 계정(공용 계정) — 실사용자는 `actor_login_id`(토큰 주인) |
| `sql_normalized` | 리터럴을 `$1`…로 바꾼 정규화 SQL. 원문 SQL은 Argus에 저장하지 않음 |
| `tables`·`columns` | 문장이 참조한 테이블·컬럼 이름. 보호 대상 현황(3-8)의 테이블별 최근 30일 DB 직접 접근 건수는 `tables`로 집계 |
| `row_count` | 결과 행 수 또는 영향 행 수 |
| `raw_ref`·`raw_fingerprint` | 게이트웨이 원문 저장소의 레코드 참조와 SHA-256 지문 |
| `subject_unresolved` | 정보주체를 특정하지 못했으면 true |
| `token_id` | DB 접속 토큰의 jti(플랫폼 `db_access_token.token_id`) |
| `registry_version` | 데이터 유형 판정에 쓴 보호 대상 등록부 버전. 등록 이력의 마지막 id인 `r{id}`, 등록부를 한 번도 받지 못해 게이트웨이 고정 표를 쓴 경우 `builtin` |

- **회원번호 추출**: 게이트웨이는 SQL을 해석하지 않고, 결과 열 설명(RowDescription)이 주는 원본 테이블 OID·열 번호로 회원을 가리키는 열을 찾아 결과 행에서 그 열의 값만 읽는다. 회원을 가리키는 열은 보호 대상 등록부에서 회원 식별 열(`protected_column.member_key`)로 표시한 컬럼이다(초기 등록: `member.id`, `member_consent`·`shipping_address`·`refund_account`·`inquiry`·`orders`의 `member_id`, `retained_member_record.original_member_id`). 찾으면 `subject_ids` = 고유 회원번호(1,000개 상한·`subject_truncated`는 화면 경유와 같음), `subject_count` = 고유 회원 수, `subject_unresolved = false`. 회원 열이 결과에 있고 0행이면 정보주체 0명으로 기록한다.
- **미특정**: 결과에 회원 열이 없는 조회(예: `SELECT name, email FROM member`), 식·뷰·함수 결과(원본 테이블 OID가 0), 결과를 돌려주지 않는 UPDATE·DELETE·COPY, 값 해석 실패(하나라도 실패하면 그 실행 전체)는 `subject_ids`를 비우고 `subject_count = row_count`, `subject_unresolved = true`로 남긴다. 결과 값 자체는 원장에도 게이트웨이 원문 저장소에도 남기지 않는다.

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
    auto_request boolean      NOT NULL DEFAULT true,  -- 탐지건 생성 시 자동 소명 요청 여부 (정책정의서 3-2)
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
-- (rule_id, version) 유일 — 켜기·끄기도 version +1로 하나의 축
-- 앱 계정은 추가·조회만(감사 추적). 변경 사유 컬럼은 두지 않음(변경 후 스냅숏 + 누가·언제로 추적)
-- 시드 룰의 CREATE 이력은 소급 기록(changed_by NULL = 시스템)
-- 시드 룰을 마이그레이션으로 고칠 때도 version +1과 UPDATE 이력(changed_by NULL)을 남긴다(예: 0022 설명 문구 정리)
```

> **룰 삭제 없음**: 탐지건이 룰을 참조하고 "그 시점에 운영한 룰"이 점검 근거이므로 룰은 삭제하지 않고 끈다(`enabled=false`, 화면은 켜짐/꺼짐 필터). 앱 계정에는 처음부터 `detection_rule` DELETE 권한이 없다(3-5 DB 계정 구조) — 정책과 DB 권한이 일치. `rule_type`은 생성 후 변경 불가.

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
    access_path        varchar(8)   NOT NULL DEFAULT 'APP' CHECK (access_path IN ('APP','DB')),  -- 한 탐지건 = 한 경로. 룰이 ALL이어도 경로별로 따로 생성 — 정책정의서 1-5 / 마이그레이션 0012(기존 행 APP)
    actor_login_id     varchar(64)  NOT NULL,
    group_bucket       varchar(64)  NOT NULL,     -- EVENT: '2026-09-15'(occurred_at의 KST 날짜) / AGGREGATE: 윈도우 시작 시각
    data_category      varchar(16),               -- EVENT 탐지건의 데이터 유형 (0018). AGGREGATE·이전 탐지건은 NULL
    action_group       varchar(16)  CHECK (action_group IN ('READ','DOWNLOAD','CHANGE','SESSION')),  -- EVENT 탐지건의 행위 구분 (0018)
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
-- 그룹 키에 데이터 유형·행위 구분 포함 (0018). NULL끼리도 같은 값으로 보도록 NULLS NOT DISTINCT(PostgreSQL 15+)
CREATE UNIQUE INDEX ux_detection_open_group
    ON detection (rule_id, source_system_id, access_path, actor_login_id, group_bucket,
                  data_category, action_group)
    NULLS NOT DISTINCT
    WHERE status IN ('DETECTED','REQUESTED','SUBMITTED','REJECTED');

CREATE INDEX ix_detection_status ON detection (status, detected_at);
CREATE INDEX ix_detection_actor  ON detection (source_system_id, actor_login_id);

-- [S] 탐지건 하위 로그
CREATE TABLE detection_log (
    detection_id   bigint NOT NULL REFERENCES detection(id)  ON DELETE CASCADE,
    access_log_id  bigint NOT NULL REFERENCES access_log(id) ON DELETE CASCADE,
    attached_at    timestamptz DEFAULT clock_timestamp(),   -- 탐지건에 붙은 시각 (0018). 이전 행은 NULL
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
    requested_by     bigint      REFERENCES argus_user(id),  -- NULL = 시스템(자동 소명 요청)
    requested_at     timestamptz NOT NULL DEFAULT now(),
    request_message  text,
    submitted_by     bigint      REFERENCES argus_user(id),
    submitted_at     timestamptz,
    content          text,
    reviewed_by      bigint      REFERENCES argus_user(id),
    reviewed_at      timestamptz,
    review_result    varchar(16) CHECK (review_result IN ('APPROVED','REJECTED')),
    review_comment   text,
    ticket_ids       varchar(32)[] NOT NULL DEFAULT '{}',  -- 관련 업무 티켓 번호(`INQ-n`), 최대 3개 CHECK. 제출과 함께 저장, 이후 변경 API 없음
    due_at           timestamptz,               -- 소명 기한 = 요청 시각 + setting.explanation_due_days (0015)
    UNIQUE (detection_id, round)
);
```

**탐지건 그룹 키와 소명 단위 (0018)**

| 항목 | 규칙 |
|---|---|
| 행위 구분 `action_group` | `READ` 조회 / `DOWNLOAD` 내려받기(`DOWNLOAD`·`EXPORT`) / `CHANGE` 변경·삭제(`CREATE`·`UPDATE`·`DELETE`) / `SESSION` 로그인·로그아웃 |
| EVENT 탐지건 | 같은 룰·출처·경로·취급자·날짜라도 데이터 유형이나 행위 구분이 다르면 다른 탐지건(예: 같은 날 야간의 회원정보 조회와 결제정보 조회는 2건) — 정책정의서 2-2 |
| AGGREGATE 탐지건 | 두 컬럼 모두 NULL(집계 의미가 바뀌므로 나누지 않음) |
| 이전 탐지건 | 0018 이전 행은 NULL 그대로. 마이그레이션 직후 진행 중인 이전 탐지건이 있으면 같은 날 새 기록은 성격이 정해진 새 탐지건으로 간다 |
| 제출 뒤 추가 기록 | `detection_log.attached_at` > 현재 차수 `explanation.submitted_at`인 하위 기록. 그 소명이 다루지 않은 행위라 화면·보고서에 건수로 표시한다. 재요청 후 다시 제출하면 새 제출 시각이 기준이 된다. `attached_at`이 NULL인 이전 행은 세지 않는다 |

- 붙은 시각을 수신 시각(`access_log.received_at`)으로 대신하지 않는 이유: 수신과 부착 사이에 순찰 주기만큼 차이가 있어, 제출 직전에 수신되고 제출 직후 붙은 기록(취급자가 볼 수 없던 기록)을 구분하지 못한다. 기본값을 `clock_timestamp()`로 둔 것은 순찰 트랜잭션이 탐지건 잠금을 기다리는 사이 제출이 끼어든 경우에도 실제 부착 순간으로 비교하기 위해서다.

**소명 기한 (0015)**: `due_at`은 소명 요청(자동·수동·재요청) 때 요청 시각 + `setting.explanation_due_days`(기본 7일)로 정하고, 재요청하면 그 시각부터 다시 센다. 0015는 이미 있던 요청을 `requested_at + 7일`로 채웠다. 탐지 배치가 순찰마다 기한 임박(남은 시간 24시간 이하)과 초과를 판정해 알림을 한 번씩 만든다. 기한을 넘겨도 탐지건 상태는 바뀌지 않는다(담당자 판단).

> **관련 업무 티켓**: 취급자가 소명 제출 시 플랫폼 1:1 문의 번호를 직접 입력한다(서버는 형식 `INQ-[1-9][0-9]{0,9}`만 검사 — 링크 주소에 그대로 들어가므로 엄격히). **Argus는 티켓 내용을 보유하지 않고** 탐지건 상세에 플랫폼 관리자 화면 링크(`ARGUS_PLATFORM_ADMIN_URL` + `/inquiries/{번호}`)만 준다 — 절대 규칙 #3·두 시스템 분리. 담당자가 링크로 문의를 열면 그 열람은 **플랫폼 접속기록으로 Argus에 남는다**(감시자도 감시됨). 검증(티켓이 실제 근거인가)은 담당자 판단, 자동 대조는 v0.2.

```sql
-- 소명 첨부
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

**소명 첨부 규칙**

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

-- 화면 알림 (LOG-05, 마이그레이션 0015·0016) — 받는 사람별 한 줄, 본문 없음
CREATE TABLE notification (
    id            bigserial   PRIMARY KEY,
    user_id       bigint      NOT NULL REFERENCES argus_user(id) ON DELETE CASCADE,
    kind          varchar(16) NOT NULL
                  CHECK (kind IN ('DETECTED','REQUESTED','DUE_SOON','OVERDUE','SUBMITTED')),
    detection_id  bigint      NOT NULL REFERENCES detection(id) ON DELETE CASCADE,
    severity      varchar(8)  NOT NULL CHECK (severity IN ('HIGH','MEDIUM','LOW')),
    round         int         NOT NULL DEFAULT 0,       -- 소명 차수 (차수마다 따로 알림)
    created_at    timestamptz NOT NULL DEFAULT now(),
    read_at       timestamptz,
    push_pending  boolean     NOT NULL DEFAULT false,   -- 웹 푸시도 보낼 알림 (0016). 한 번 시도하면 false
    UNIQUE (user_id, kind, detection_id, round)         -- 같은 알림은 한 번만 (순찰이 기한을 다시 판정해도 쌓이지 않음)
);
CREATE INDEX ix_notification_user ON notification (user_id, created_at DESC);
CREATE INDEX ix_notification_push_pending ON notification (id) WHERE push_pending;

-- 웹 푸시 구독 (0016) — 사용자가 브라우저에서 "알림 받기"를 허락했을 때 브라우저가 준 구독 정보
CREATE TABLE push_subscription (
    id          bigserial     PRIMARY KEY,
    user_id     bigint        NOT NULL REFERENCES argus_user(id) ON DELETE CASCADE,
    endpoint    varchar(1000) NOT NULL UNIQUE,   -- 푸시 서비스 주소 (FCM·Mozilla·Apple·WNS 호스트만 허용)
    p256dh      varchar(128)  NOT NULL,          -- 구독자 암호화 공개키 (RFC 8291)
    auth        varchar(64)   NOT NULL,          -- 인증 비밀
    created_at  timestamptz   NOT NULL DEFAULT now()
);
CREATE INDEX ix_push_subscription_user ON push_subscription (user_id);

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
CREATE INDEX ix_inspection_report_generated ON inspection_report (generated_at DESC);
```

**알림 (0015·0016)**

| 종류 `kind` | 사건 | 받는 사람 | 상 | 중 | 하 |
|---|---|---|---|---|---|
| `DETECTED` | 탐지건 생성 | 담당자 전원 | 화면 + 웹 푸시 | 없음(목록에만) | 없음(목록에만) |
| `REQUESTED` | 소명 요청(자동·수동·재요청) | 해당 취급자 | 화면 + 웹 푸시 | 화면 | 화면 |
| `DUE_SOON` / `OVERDUE` | 소명 기한 임박(24시간 이하)·초과 | 취급자 + 담당자 전원 | 화면 + 웹 푸시 | 화면 + 웹 푸시 | 화면 |
| `SUBMITTED` | 소명 제출 | 요청한 담당자(자동 요청이면 담당자 전원) | 화면 | 화면 | 화면 |

- 알림 행에는 본문이 없다. 종류·탐지건 번호·심각도·차수만 저장하고, 화면은 탐지건의 룰 이름을 붙여 보여 준다(개인정보 없음). 알림은 업무(탐지건 생성·상태 전이)와 같은 트랜잭션에서 만든다.
- 기한을 넘긴 뒤 처음 판정하면 `OVERDUE`만 만든다(`DUE_SOON`을 건너뜀).
- 앱 계정 권한: `notification`은 SELECT·INSERT·UPDATE(읽음 표시·`push_pending`), DELETE 없음. `push_subscription`은 SELECT·INSERT·UPDATE·DELETE. 알림·구독의 FK가 `ON DELETE CASCADE`인 것은 파생 데이터이기 때문이다(운영에서는 탐지건·계정을 지우지 않음).
- 웹 푸시는 `push_pending` 알림을 소명 요청 직후와 탐지 배치 순찰마다 발송하고(행 잠금 `SKIP LOCKED`로 중복 방지), 성공·실패와 관계없이 한 번만 시도한다. 푸시 서비스가 404·410을 주면 구독을 지운다. 로그아웃·계정 비활성화 때도 그 사용자의 구독을 지운다. 푸시 내용에는 회원번호·취급자 이름·룰 상세를 넣지 않는다.
- 이메일 알림(LOG-16)은 v0.2다. 이메일 발송 이력이 필요해지면 그때 채널·발송 결과 컬럼(또는 테이블)을 추가한다.

**탐지건 요약과 점검 보고서**

- **`log_summary` 스냅샷 내용**: `{log_count, subject_count_sum, distinct_subject_count, subject_ids_truncated, actions[], data_categories[], first_occurred_at, last_occurred_at, ip_list[]}`. **회원 PK는 담지 않고 숫자만** 담는다. `subject_ids_truncated`는 연결된 기록 중 회원 PK가 1,000개에서 잘린 것이 있으면 true — 이때 `distinct_subject_count`는 **하한값**이다. `subject_count_sum`(행위의 양, 탐지 판정)과 `distinct_subject_count`(피해 범위, 유출 통지·신고 판단)는 서로 다른 질문의 답이라 둘 다 유지한다. 요약은 증분 갱신이 아니라 **연결된 기록 전체에서 다시 계산**한다(재처리해도 같은 값). 접속기록이 보관기간 만료로 파기되면 `detection_log` 링크는 CASCADE로 사라지지만, 이 요약은 탐지건에 남아 점검 증적이 유지된다(7절에서 동작 검증).
- **구현 (마이그레이션 0013)**: 앱 계정은 `inspection_report`를 **조회·추가만**(점검 증적이라 고치거나 지우지 않음). `scope`에 접근 경로 범위(전체/APP/DB), `summary`에 생성 시점 스냅샷(점검 수행 확인 + 경로별 섹션 — 정책정의서 6-1). **`file_path`는 비워 둔다** — 서버에 파일을 만들지 않고 화면에서 인쇄·PDF 저장. `unmasked`는 v0.1에서 항상 false(언마스킹 보류)
- **`summary` 구조**: `scope`는 `{"access_path": "ALL"|"APP"|"DB"}`. `summary`는 아래 항목을 담는다. 감시 대상(플랫폼) 기록만 집계하고, Argus 자체 접속기록은 섹션에 섞지 않는다.

| 항목 | 내용 |
|---|---|
| `period`·`scope` | 점검 기간(한국 날짜 기준 시작·끝), 경로 범위 |
| `integrity` | §8③ 위·변조 점검. `ok`·`checked`·`broken_at`(해시체인 재계산 결과), `total`(원장 전체 건수), `last_id`·`last_hash`(원장 마지막 행의 id와 해시 64자 — 출력본이 DB 밖 기준점), `previous`(`status`·`report_id`·`last_id` — 직전 보고서가 남긴 마지막 행이 같은 해시로 남아 있으면 `MATCH`, 없거나 해시가 다르면 `MISMATCH`, 비교할 보고서가 없으면 `NONE`) |
| `patrol` | 기간 중 탐지 배치 실행 수·성공·실패, 마지막 성공 시각(점검 수행 증적) |
| `paths.{APP,DB}.logs` | 기록 수, 취급자 수, 실패 수, 수행업무별·데이터 유형별 건수, `subject_unresolved`(정보주체 미특정 건수, DB 직접) |
| `paths.{APP,DB}.detections` | 탐지건 수, 심각도별·상태별·룰별 건수, 에스컬레이션 수 |
| `paths.{APP,DB}.explanations` | 소명 요청·제출·승인·반려 수, `overdue`(기한 뒤 제출했거나, 보고서를 만든 시점에 기한이 지났는데 미제출인 차수) |
| `paths.{APP,DB}.cases[]` | 탐지건 목록(심각도순, 최대 50건). 룰 이름·심각도·상태·차수·행위자·첫 발생 시각·기록 수·처리 건수·고유 정보주체 수, `subjects`(마스킹 식별값 앞 3개), `explanation`(마지막 차수 — 소명 요지 앞 200자, 제출 시각, 검토 결과, 검토 의견 앞 200자, 관련 티켓, 첨부 개수), `handled_by`·`handled_at`(담당자가 마지막으로 상태를 바꾼 이력), `after_submission`(제출 뒤 추가 기록 수), `close_reason`(요청 취소 사유 앞 200자, `DISMISSED`일 때만) |

- 제출 전 초안의 내용·첨부는 보고서에 싣지 않는다(담당자 화면과 같은 기준). 소명 요지·검토 의견은 자유 입력이라 자동 마스킹하지 않는다. 소명 작성 칸의 개인정보 입력 주의 안내로 위험을 줄인다.
- 원장 기준점: `integrity.last_id`가 없는 이전 보고서는 비교 대상에서 뺀다. 보고서도 같은 DB에 있으므로, 보고서를 출력해 결재문서(관리대장)에 붙여 두는 운영을 전제로 한다(3-5 "알려진 한계").
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

-- 삭제도 트리거로 거부 (0017) — 소유자 포함. TRUNCATE는 행 트리거를 거치지 않아 문장 트리거를 따로 둔다
CREATE FUNCTION forbid_access_log_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'access_log is append-only (% blocked)', TG_OP; END $$;
CREATE TRIGGER trg_access_log_no_delete BEFORE DELETE ON access_log
    FOR EACH ROW EXECUTE FUNCTION forbid_access_log_delete();
CREATE TRIGGER trg_access_log_no_truncate BEFORE TRUNCATE ON access_log
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_access_log_delete();

CREATE ROLE argus_app;
CREATE ROLE argus_purge;
GRANT SELECT, INSERT ON access_log TO argus_app;    -- UPDATE·DELETE 미부여
GRANT SELECT, DELETE ON access_log TO argus_purge;  -- 파기 배치 전용 (지금은 0017 트리거에 막힘 — 파기 배치를 만들 때 재설계)
```

**setting 시드값**

| key | 기본값 | 근거 |
|---|---|---|
| `inspection_cycle` | `"1mo"` | 정책정의서 5-2 (§8② 자율, 기본 월 1회) |
| `detection_interval_min` | `5` | LOG-03 준실시간 배치 |
| `access_log_retention` | `"1y"` | §8① |
| `subject_ids_limit` | `1000` | API ① 2-2 |
| `explanation_due_days` | `7` | 소명 기한(요청 시각 + 7일, 0015) |

**append-only 보장 (§8③)**

| 장치 | 내용 |
|---|---|
| DB 권한 분리 | 앱 계정(`argus_app`)은 INSERT·SELECT만. DELETE는 파기 전용(`argus_purge`)만. UPDATE는 아무에게도 없음 |
| 트리거 | `BEFORE UPDATE`·`BEFORE DELETE` 행 트리거와 `BEFORE TRUNCATE` 문장 트리거로 예외 발생(0017). 권한 설정 실수 대비 이중 장치이자, 권한과 무관한 소유자의 수정·삭제도 막는다. 파기 배치(v0.2)를 만들 때 파기 전용 경로(파기 롤만 통과 + `destruction_history`에 체인 앵커 기록)로 다시 설계한다 |
| 보고서 기준점 | 점검 보고서 `summary.integrity`에 원장 마지막 `id`·`hash`·전체 건수를 남기고, 다음 보고서가 그 행이 같은 해시로 남아 있는지 대조한다(3-4). 출력한 보고서가 DB 밖 기준점이 된다(안내서 95쪽 "위·변조 확인 정보를 별도 저장매체 또는 관리대장에") |
| 해시체인 | `hash = SHA-256(정규화된 레코드 내용)`, 정규화 대상에 **`prev_hash`와 `id`를 포함**해 앞 레코드와 연결(규칙은 아래 "정규화 규칙 v1"). 중간 레코드가 수정·삭제되면 이후 체인 검증이 깨짐 |
| 순차 기록 | append를 **advisory lock으로 직렬화** → `id`가 커밋 순서대로 증가해, 탐지 배치의 `id` 커서가 누락 없이 안전해짐 |
| 파기와의 공존 | 오래된 순서로 삭제하면 체인 시작점이 바뀜 → 파기 시 **남은 첫 레코드의 prev_hash를 `chain_anchor_hash`로 기록**하고, 검증은 그 앵커에서 시작 |
| 시드 데이터 | baseline용 과거 접속기록(요구사항 4-3)도 **동일한 append 함수를 통해** 주입해 체인을 성립시킨다. DB에 직접 INSERT하지 않는다 |

**해시체인 정규화 규칙 v1**

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

**DB 계정 구조**

| 계정 | 로그인 | 용도 | `access_log` 권한 |
|---|---|---|---|
| 소유자 (`ARGUS_DB_USER`) | O | **마이그레이션 전용** | 전권 (소유자는 GRANT와 무관) — 트리거가 UPDATE·DELETE·TRUNCATE를 차단. 트리거 자체를 끌 수는 있음 |
| 앱 로그인 (`ARGUS_APP_DB_USER`, `argus_app` 멤버) | O | argus-api·worker 접속 | SELECT·INSERT |
| `argus_app` | X (롤) | 앱 권한 묶음 | SELECT·INSERT |
| `argus_purge` | X (롤) | 파기 배치 (Skeleton 이후 로그인 계정 발급) | SELECT·DELETE |

- **API가 소유자 계정으로 접속하면 권한 분리가 무의미**해지므로 앱은 반드시 `argus_app` 멤버 계정으로 접속한다.
- 로그인 계정의 비밀번호는 마이그레이션이 아닌 별도 발급 스크립트에서 설정한다(비밀번호가 마이그레이션 코드·이력에 남지 않게).
- 앱 롤의 테이블별 권한: `access_log`·`detection_status_history`·`detection_log` = SELECT·INSERT(감사 추적은 append-only) / `source_system` = SELECT / 그 밖의 업무 테이블 = SELECT·INSERT·UPDATE(DELETE 없음). 예외: 이력·증적 테이블(`inspection_report`·`argus_user_history`·`protected_column_history`·`db_schema_receipt`)은 SELECT·INSERT만, `explanation_attachment`는 SELECT·INSERT·DELETE(UPDATE 없음, 3-4), `push_subscription`은 DELETE 포함, `db_schema_column`은 수신 때 목록을 통째로 바꾸므로 SELECT·INSERT·DELETE. 이후 테이블을 추가할 때는 마이그레이션에서 **테이블마다 명시적으로 부여**한다(`ALTER DEFAULT PRIVILEGES` 미사용 — 테이블마다 최소 권한을 판단하기 위해).

**알려진 한계** (보안성 검토 단계 과제, 아키텍처 설계서 8-6)

| 한계 | 설명 | 대응 후보 |
|---|---|---|
| 끝부분 삭제 | 맨 끝 레코드들을 지우면 남은 체인은 그대로 이어져 검증 통과 | 삭제 자체는 0017 트리거가 막는다. 트리거를 끄고 지운 경우는 점검 보고서 기준점 대조(`integrity.previous.status = MISMATCH`)로 드러난다. 단 보고서도 같은 DB에 있어 출력본을 관리대장에 보관하는 운영이 전제다. 남은 대응 후보: 체인 머리(최신 `id`·`hash`)를 DB 밖(WORM 저장소 등)에 주기 기록하는 **외부 앵커링**, `detection_batch_run.to_access_log_id` > 원장 최대 `id` 교차 확인 |
| 전체 재계산 | DB 권한자가 체인을 처음부터 다시 계산해 덮어쓰면 탐지 불가 | 출력해 둔 보고서 기준점(마지막 행 해시)과 대조. 외부 앵커링 (위와 동일) |
| 소유자·슈퍼유저의 트리거 해제 | 0017 트리거가 소유자의 DELETE·TRUNCATE도 막지만, 소유자·슈퍼유저는 `ALTER TABLE … DISABLE TRIGGER`로 트리거를 끌 수 있음 | 보고서 기준점 출력·보관(위), 외부 앵커링 |
| 앱 계정의 직접 INSERT | append 함수를 거치지 않은 INSERT를 DB가 막지 못함(검증 시 체인 불일치로 **사후 탐지**는 됨) | append를 `SECURITY DEFINER` 함수로 옮기고 앱에는 EXECUTE만 부여. 단 정규화를 SQL·Python 양쪽에 구현해야 하는 불일치 위험이 있어 기각 |

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
| `client_ip` | 접속지가 대역(CIDR) 목록 안·밖인가. 값은 CIDR 문자열 목록 1~50개, IPv4·IPv6. 호스트 비트가 있어도 받는다(`10.0.0.1/8` = `10.0.0.0/8`) | `in_cidr`, `not_in_cidr` |

> **접속지 조건의 주의**: 신뢰 프록시를 두지 않은 로컬 구성에서는 화면 경유 기록의 접속지가 화면 서버 컨테이너 주소(사설망)로 남아, 사설망을 허용 범위로 둔 룰이 걸리지 않는다. DB 직접 기록은 게이트웨이가 받은 실제 접속지를 쓴다.

> **취급자 속성 조건의 미동기화 처리**: 해당 취급자가 명부에 없으면 판정 불가로 보고 **탐지한다(fail-closed)**. 퇴직자 룰은 상태 이력에 "취급자 명부에 없는 계정 — 퇴직 여부를 판정할 수 없어 탐지"를 남긴다. 판정 불가를 "이상 없음"으로 처리하면 탐지 누락이 된다는 원칙은 그대로다.
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
| `measure` | `LOG_COUNT` / `SUBJECT_COUNT`(처리 건수 합) / `DISTINCT_SUBJECT`(고유 정보주체 수) / `DISTINCT_IP`(고유 접속지 수) / `MAX_SUBJECT_REPEAT`(윈도우 안에서 한 회원이 든 기록 수의 최댓값 — 기록 하나에 같은 회원이 여러 번 있어도 1회, 회원번호가 있는 기록만 셈) |
| `compare` | `ABSOLUTE` / `RATIO_TO_BASELINE` |
| `baseline` | `PREV_MONTH_SAME_PERIOD` (추후 `PREV_3M_AVG` 등) |
| `threshold` | 숫자 (ABSOLUTE: 양의 정수 / RATIO: 양수 배율) |
| `min_baseline` | (선택, RATIO 전용) 전월 같은 기간 집계값이 이보다 작으면(0 포함) **판정하지 않음**. 기본 룰 값 20 — 정책정의서 1-2 |

- 형식 규칙: 키 집합 정확히 일치, EVENT 룰에 집계 스펙 금지, RATIO는 월 윈도우 + `PREV_MONTH_SAME_PERIOD`만. AGGREGATE 룰도 조건식은 비울 수 없어 집계 대상을 조건으로 적는다(예: 모든 수행업무, 개인정보 데이터 유형 4종).
- `DISTINCT_SUBJECT`·`MAX_SUBJECT_REPEAT`는 회원번호가 1,000개에서 잘린 기록이 있으면 하한값이다. `MAX_SUBJECT_REPEAT` 탐지건은 가장 많이 처리된 회원을 마스킹 식별값으로 상태 이력에 남긴다(예: "최다 처리 member_10*** 23회"). `log_summary`는 순찰마다 다시 계산되므로 이 값을 담지 않는다.
- 윈도우는 **KST 고정 구간**(매시 정각·0시·1일), 그룹 키 = 윈도우 시작 시각(`2026-09-15T14:00+09:00`). 전월 동기 = 전월 1일부터 같은 경과 시간(전월이 짧으면 전월 말에서 자름).

**기본 룰 시드** (정책정의서 1-3절 → JSON. 처음 7개는 `access_path='APP'`, 다음 3개는 `access_path='DB'`, 마지막 3개는 경로 열에 적은 값)

| 룰 | type | condition | aggregate | severity | Skeleton |
|---|---|---|---|---|---|
| 대량 다운로드 | EVENT | `action=DOWNLOAD ∧ subject_count≥50` | — | HIGH | **[S]** |
| 야간 접속 | EVENT | `action∈{READ,DOWNLOAD} ∧ occurred_time between 22:00~06:00` | — | MEDIUM | |
| 주말 접속 | EVENT | `occurred_weekday∈{SAT,SUN}` | — | LOW | |
| 결제수단 조회 | EVENT | `data_category=PAYMENT ∧ action=READ` | — | HIGH | |
| 대량 조회 | AGGREGATE | `action=READ` | 1h / LOG_COUNT / ABSOLUTE / 100 | HIGH | |
| 전월 대비 급증 | AGGREGATE | `action=READ` | 1mo / LOG_COUNT / RATIO / 2.0 / `min_baseline` 20 | MEDIUM | |
| **퇴직자 계정 접속** | EVENT | `actor_terminated_at_or_before=true` | — | HIGH | (6절 #2 제안) |
| DB 직접 야간 접근 (`DB`) | EVENT | `data_category∈{MEMBER_BASIC,ORDER,INQUIRY,PAYMENT} ∧ occurred_time between 22:00~06:00` | — | HIGH | |
| DB 직접 주말 접근 (`DB`) | EVENT | `data_category∈{MEMBER_BASIC,ORDER,INQUIRY,PAYMENT} ∧ occurred_weekday∈{SAT,SUN}` | — | MEDIUM | |
| DB 직접 전월 대비 급증 (`DB`) | AGGREGATE | `data_category∈{MEMBER_BASIC,ORDER,INQUIRY,PAYMENT}` | 1mo / LOG_COUNT / RATIO / 2.0 / `min_baseline` 20 | MEDIUM | |
| 허용 범위 밖 접속지 (`ALL`) | EVENT | `client_ip not_in_cidr [10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8]` | — | HIGH | |
| 짧은 시간 여러 접속지 (`ALL`) | AGGREGATE | `action∈{LOGIN,LOGOUT,READ,CREATE,UPDATE,DELETE,DOWNLOAD}` | 1h / DISTINCT_IP / ABSOLUTE / 3 | MEDIUM | |
| 특정 회원 반복 처리 (`APP`) | AGGREGATE | `data_category∈{MEMBER_BASIC,PAYMENT,ORDER,INQUIRY}` | 1d / MAX_SUBJECT_REPEAT / ABSOLUTE / 20 | MEDIUM | |

> **DB 직접 기본 룰 3개 (마이그레이션 0012)**: 시드와 룰 변경 이력을 함께 넣었다. 개인정보 데이터 유형만 조건으로 두어 `LOGIN`(대상 데이터 `NONE`)·업무 외 테이블은 탐지하지 않는다. 퇴직자 룰은 `APP`에만 둔다. 근거는 정책정의서 1-3 "2단계 룰".

> **접속지·특정 정보주체 기본 룰 3개 (마이그레이션 0019)**: 안내서 95쪽이 비정상 행위 예시로 드는 "인가되지 않은 단말·지역(IP)에서 접속", "짧은 시간 여러 IP", "특정 정보주체 과도 조회"를 기본 룰로 둔다. 시드와 룰 변경 이력(`CREATE`, 변경자 NULL)을 함께 넣었다. 허용 범위의 기본값은 사설망 3대역과 루프백이다(사내망 가정). 운영에서는 담당자가 룰 빌더로 회사 대역으로 바꾼다. 특정 회원 반복 처리는 회원번호가 있는 기록만 세므로 `APP`에만 둔다(DB 직접 기록은 미특정일 수 있음).
>
> **시드 룰 설명 정리 (마이그레이션 0022)**: 룰 설명은 룰 목록·탐지건 상세에 그대로 보이므로, 시스템이 만든 룰 중 담당자가 한 번도 고치지 않은 룰만 설명 끝의 문서 번호 표기("(policy 1-3)", "(§2 3호 …)")를 지웠다. 룰 변경은 언제나 이력으로 남긴다는 원칙대로 `version` +1, `detection_rule_history`에 `UPDATE`(변경자 NULL)를 남겼다. 되돌리기는 하지 않는다(이전 문장은 이력에 있음).

### 3-7. 탐지 배치의 데이터 흐름 (F-04)

1. 직전 성공 실행의 `to_access_log_id` **초과** ~ 현재 최대 `id` **이하**를 이번 범위로 잡고 `detection_batch_run` 생성(RUNNING — 별도 트랜잭션으로 먼저 기록해 진행 중인 순찰이 밖에서 보이게). **순찰 1회 최대 10,000건**, 밀려 있으면 쉬지 않고 다음 순찰. **새 기록이 없어도 순찰 이력을 남긴다**("탐지가 주기적으로 수행됐다"는 점검 증적이자, worker 중단 기간과 기록 없는 기간을 구분하는 근거). 동시 실행은 advisory lock으로 막고, 비정상 종료로 남은 RUNNING은 다음 순찰이 정리한다.
2. 활성 룰 로드 → **켜진 룰 중 해석할 수 없는 룰(모르는 필드·연산자, 값 타입, 구조, 미지원 유형)이 하나라도 있으면 순찰 전체를 FAILED로 끝내고 책갈피를 유지**(그 룰만 건너뛰면 책갈피가 넘어가 그 사이 기록이 그 룰로 영영 평가되지 않음. 기준값의 적절성은 해석 가능성과 별개로 담당자 판단) → **출처 ARGUS(Argus 자체 접속기록)는 평가에서 제외**(룰은 감시 대상 시스템 기록에만 적용, 정책정의서 1-2. 순찰 건수에는 포함) → 룰의 `access_path`로 대상 로그를 먼저 필터 → `rule_exception` 해당 건 제외(화이트리스트는 v0.2)
3. **EVENT**: 조건식 판정 → 그룹 키 `(rule, source, path, actor, occurred_at의 날짜, data_category, action_group)`로 **진행 중** 탐지건 조회(`FOR UPDATE`) → 없으면 생성(`DETECTED`) + 룰 사본 + 상태 이력(시스템) + 알림(심각도 상이면 담당자 전원, 3-4 "알림"), 있으면 하위 로그만 추가하고 `log_count`·`last_occurred_at`·`log_summary` 갱신. 같은 룰로 이미 어떤 탐지건에 붙은 기록은 다시 붙이지 않는다(재처리 시 증거 복제 방지). 하위 기록을 붙일 때 `detection_log.attached_at`에 부착 시각이 남는다
   - **자동 소명 요청**: 탐지건을 **새로 만들 때** 룰의 `auto_request`가 켜져 있고 행위자에게 재직 중·비활성 아닌 A5 계정이 있으면 같은 트랜잭션에서 `REQUESTED`(round 1, `explanation.requested_by` NULL) + 상태 이력 2줄(DETECTED·REQUESTED, 모두 시스템) + 소명 기한(`explanation.due_at`) + 취급자 알림(`REQUESTED`). 룰 사본에 `auto_request` 포함. `explanation (detection_id, round)` 유일 제약이 이중 요청을 DB에서 막는다
   - **날짜는 한국 시각(KST, +09:00 고정) 기준** — UTC 날짜를 쓰면 KST 새벽 0~9시 행위가 전날 탐지건으로 묶인다. 야간·주말 룰의 시각·요일 판정도 KST. DB 시간대 데이터에 의존하지 않도록 앱에서 고정 오프셋으로 계산
4. **AGGREGATE**: 범위의 기록으로 "다시 볼 (취급자, 윈도우)"를 고르고, 원장에서 **윈도우를 통째로 다시 집계**(순찰 경계에서 윈도우가 쪼개지지 않게, 늦게 도착한 기록 포함, 이번 순찰이 본 id까지만) → 기준 초과 시 같은 방식으로 upsert + `aggregate_value` 갱신, 상태 이력에 판정 근거("집계 100 ≥ 기준 100", "당월 63 / 전월 동기 23 = 2.74배 ≥ 2.0배")
   - **윈도우는 한 번만 판단**: 같은 (룰, 취급자, 윈도우)의 탐지건이 **종결됐으면** 새 탐지건을 만들지 않음 — 정책정의서 2-3 AGGREGATE 예외
   - 월 윈도우는 순찰마다 한 달치를 다시 읽음 — 데이터가 커지면 윈도우별 누적값 테이블 검토(v0.2 후보)
5. 판정·탐지건·하위 로그·SUCCESS를 **한 트랜잭션**으로 기록. 실패 시 FAILED로 남기고 다음 실행이 같은 범위부터 재처리(멱등: 하위 로그 PK 중복은 무시). 실패 사유에는 예외 메시지 **첫 줄만** 남긴다(DB 오류의 상세 줄에 값이 실릴 수 있음)
6. 순찰마다 미제출 소명 요청의 기한을 판정해 `DUE_SOON`(남은 시간 24시간 이하)·`OVERDUE` 알림을 만들고(같은 알림은 UNIQUE로 한 번만), `push_pending` 알림의 웹 푸시를 발송한다(3-4 "알림")

> **왜 배치이고 5분인가**: Argus는 접근을 막는 통제가 아니라 **이미 일어난 행위를 보고 소명을 받는 탐지 통제**다. 소명은 사람의 속도로 진행되고, 배치는 집계형 룰·늦게 도착한 기록·실패 재처리·수집과의 분리·단일 VM 인프라에 모두 유리하다. 실시간 스트리밍은 인프라 부담으로 스트레치(요구사항정의서 5-3). 5분은 당일 대응이 가능하면서 부담 없는 간격이며 `setting`으로 조정한다.

> **커서는 `id` 기준이다.** `received_at`은 시계 오차·동시 삽입 때문에 누락 위험이 있어 커서로 쓰지 않고, 전송 지연 모니터링(`received_at - occurred_at`) 용도로만 쓴다. (API 명세 2-6과 일치)

---

### 3-8. 보호 대상 등록부 (마이그레이션 0021)

어느 테이블·컬럼이 어떤 개인정보인지를 Argus가 등록부로 관리한다. DB 접근 게이트웨이는 이 등록부로 DB 직접 기록의 데이터 유형과 회원 식별 열을 판정한다(아키텍처 설계서 3-4, 정책정의서 1-6, 요구사항 LOG-20). 등록·변경·해제 API와 게이트웨이 판정 연동은 v0.1에 구현했고, 관리 화면(Argus 메뉴 "보호 대상")은 v0.2에서 노출한다.

```sql
-- 등록부 — 출처 → DB → 테이블 → 컬럼마다 개인정보 항목·데이터 유형·회원 식별 열
CREATE TABLE protected_column (
    id                bigserial    PRIMARY KEY,
    source_system_id  smallint     NOT NULL REFERENCES source_system(id),
    db_name           varchar(63)  NOT NULL,          -- 논리 이름. v0.1은 'platform' 하나
    table_name        varchar(63)  NOT NULL,
    column_name       varchar(63)  NOT NULL,
    item              varchar(16)  NOT NULL,          -- 개인정보 항목 (아래 표)
    data_category     varchar(16),                    -- 개인정보 항목이면 필수, NOT_PERSONAL이면 NULL
    member_key        boolean      NOT NULL DEFAULT false,   -- 회원 식별 열 (DB 직접 결과에서 회원번호를 읽는 열)
    active            boolean      NOT NULL DEFAULT true,    -- 해제는 삭제가 아니라 false
    updated_by        bigint       REFERENCES argus_user(id),
    updated_at        timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (source_system_id, db_name, table_name, column_name),
    CHECK (item IN ('NAME','EMAIL','PHONE','ADDRESS','BIRTH','ACCOUNT','CARD','UNIQUE_ID',
                    'SENSITIVE','CREDENTIAL','MEMBER_ID','OTHER','NOT_PERSONAL')),
    CHECK ((item = 'NOT_PERSONAL') = (data_category IS NULL)),
    CHECK (data_category IS NULL OR data_category IN ('MEMBER_BASIC','PAYMENT','ORDER','INQUIRY')),
    CHECK (NOT member_key OR item = 'MEMBER_ID')
);

-- 등록·변경·해제 이력 (append-only, 사유 필수) — 룰 변경 이력과 같은 방식
CREATE TABLE protected_column_history (
    id            bigserial    PRIMARY KEY,
    column_id     bigint       NOT NULL REFERENCES protected_column(id),
    change_type   varchar(8)   NOT NULL CHECK (change_type IN ('REGISTER','CHANGE','RELEASE')),
    snapshot      jsonb        NOT NULL,              -- 변경 후 등록 내용 (DB·테이블·컬럼·항목·유형·회원 식별 열·활성)
    reason        varchar(500) NOT NULL CHECK (length(btrim(reason)) > 0),
    changed_by    bigint       REFERENCES argus_user(id),   -- NULL = 시스템(초기 등록)
    changed_at    timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_protected_column_history_column ON protected_column_history (column_id, id);
-- UPDATE·DELETE는 행 트리거(trg_protected_history_append_only)로 거부(소유자 포함)

-- 게이트웨이가 보낸 DB 구조 목록 — 테이블·컬럼 이름과 자료형만 (데이터 값 없음)
CREATE TABLE db_schema_column (
    source_system_id  smallint     NOT NULL REFERENCES source_system(id),
    db_name           varchar(63)  NOT NULL,
    table_name        varchar(63)  NOT NULL,
    column_name       varchar(63)  NOT NULL,
    data_type         varchar(64)  NOT NULL,          -- format_type 결과 (예: character varying(255))
    ordinal           int          NOT NULL,
    PRIMARY KEY (source_system_id, db_name, table_name, column_name)
);

-- 구조 목록 수신 기록
CREATE TABLE db_schema_receipt (
    id                bigserial    PRIMARY KEY,
    source_system_id  smallint     NOT NULL REFERENCES source_system(id),
    db_name           varchar(63)  NOT NULL,
    table_count       int          NOT NULL,
    column_count      int          NOT NULL,
    received_at       timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_db_schema_receipt_received ON db_schema_receipt (received_at DESC);
```

| 개인정보 항목 `item` | 값 |
|---|---|
| 일반 개인정보 | `NAME` 이름, `EMAIL` 이메일, `PHONE` 연락처, `ADDRESS` 주소, `BIRTH` 생년월일, `ACCOUNT` 계좌번호, `CARD` 카드번호, `OTHER` 기타 개인정보 |
| 2년 보관 사유 항목 | `UNIQUE_ID` 고유식별정보, `SENSITIVE` 민감정보 |
| 계정·식별 | `CREDENTIAL` 인증정보(비밀번호 해시 등), `MEMBER_ID` 회원 식별자(내부번호) |
| 분류 결과 | `NOT_PERSONAL` 개인정보 아님 — 명시적으로 분류한다. 등록부에 없는 컬럼은 "미분류"로 드러난다 |

- 저장은 컬럼 단위, 판정은 테이블 단위다. 테이블의 데이터 유형은 그 테이블의 활성 개인정보 컬럼 중 가장 민감한 유형이다(민감도 `PAYMENT` > `MEMBER_BASIC` > `INQUIRY` > `ORDER`). 개인정보 컬럼이 없는 테이블은 `NONE`. 개인정보 컬럼을 건드리지 않은 조회를 빼는 컬럼 단위 판정은 v0.2다.
- 회원 식별 열(`member_key`)은 항목이 `MEMBER_ID`인 컬럼에만 둘 수 있다. 게이트웨이는 DB 직접 조회 결과에서 이 열의 값만 읽어 정보주체로 기록한다(3-2 "회원번호 추출").
- 해제는 `active = false`다. 등록·변경·해제마다 `protected_column_history`에 변경 후 스냅샷과 사유를 남긴다. 앱 계정은 등록부를 조회·추가·수정, 이력은 조회·추가만 할 수 있다.
- 등록부 버전은 등록 이력의 마지막 id(`r{id}`)다. 게이트웨이가 원장 `context.registry_version`에 남겨, 기록 당시 어떤 분류로 판정했는지 설명할 수 있다.
- 구조 목록은 게이트웨이가 기동 시와 1시간마다 `pg_catalog`에서 읽은 `public` 스키마 실제 테이블의 컬럼 이름·자료형이다(API명세서 6절). 받을 때마다 그 DB의 목록을 통째로 바꾸고(앱 계정에 DELETE 허용) `db_schema_receipt`에 남긴다. 등록은 구조 목록에 있는 컬럼만 받는다(오타 방지). 등록했는데 구조 목록에 없는 컬럼은 "DB에 없음"으로 표시한다.
- 게이트웨이는 5분마다 등록부를 받는다(실패하면 30초 뒤 재시도). 받지 못하면 마지막으로 받은 등록부(게이트웨이 볼륨에 저장)를, 한 번도 받지 못했으면 게이트웨이 코드의 고정 표를 쓴다(`registry_version = builtin`). 등록부가 없다고 판정을 멈추거나 모두 `NONE`으로 보내지 않는다.
- 2년 보관 대상: `UNIQUE_ID`·`SENSITIVE` 항목이 활성으로 등록돼 있으면 현황에 "2년 보관 대상"으로 표시한다(고시 §8① — 고유식별정보·민감정보 처리 시 접속기록 2년). 실제 보관 기간 적용은 파기 배치(v0.2)와 함께다.
- 현황(`GET /api/protection`): 개인정보 테이블·컬럼 수, 미분류·DB에 없음 컬럼 수, 마지막 구조 목록 수신 시각, 2년 보관 대상 여부, 등록부 버전, 테이블별 최근 30일 DB 직접 접근 건수·마지막 접근 시각(원장 `context.tables` 집계).

**초기 등록 (0021)**: 게이트웨이 고정 표를 컬럼 단위로 옮겼다. 아래 8개 테이블은 모든 컬럼을 분류했고(적지 않은 컬럼은 `NOT_PERSONAL`), 이력에 `REGISTER`(사유 "초기 등록 — 게이트웨이 고정 표 이관", 처리자 시스템)를 남겼다. 그 밖의 테이블(`operator`·`operator_permission_history`·`product`·`outbox`·`db_access_token` 등)은 미분류로 둔다. 데이터 유형은 정보주체(회원) 기준이라 직원 정보 테이블은 회원 개인정보 유형으로 분류하지 않는다.

| 테이블 | 데이터 유형 | 개인정보 컬럼 (항목) | 회원 식별 열 |
|---|---|---|---|
| `member` | `MEMBER_BASIC` | `name`(NAME), `email`(EMAIL), `phone`(PHONE), `password_hash`(CREDENTIAL) | `id` |
| `member_consent` | `MEMBER_BASIC` | `client_ip`(OTHER) | `member_id` |
| `retained_member_record` | `MEMBER_BASIC` | `data`(OTHER) | `original_member_id` |
| `shipping_address` | `MEMBER_BASIC` | `recipient`(NAME), `phone`(PHONE), `zip_code`·`address`·`address_detail`(ADDRESS) | `member_id` |
| `refund_account` | `PAYMENT` | `bank_name`·`account_number_enc`·`account_last4`(ACCOUNT), `account_holder`(NAME) | `member_id` |
| `inquiry` | `INQUIRY` | `title`·`body`·`answer`(OTHER) | `member_id` |
| `orders` | `ORDER` | `ship_recipient`(NAME), `ship_phone`(PHONE), `ship_zip_code`·`ship_address`·`ship_address_detail`(ADDRESS) | `member_id` |
| `payment` | `ORDER` | `card_company`·`pg_tid`(OTHER) | 없음(주문을 거쳐 회원과 연결) |

---

## 4. 플랫폼 DB (감시 대상 시스템 — 최소 구성)

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
    must_change_password boolean    NOT NULL DEFAULT false,  -- 계정 관리에서 만든 임시 비밀번호 계정 → 첫 로그인 때 변경 강제 (플랫폼 0009)
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now()
);

-- [S] 회원 (정보주체)
CREATE TABLE member (
    id             bigserial    PRIMARY KEY,           -- 접속기록에는 이 값만 전송
    email          varchar(255) NOT NULL UNIQUE,
    password_hash  varchar(255) NOT NULL,
    name           varchar(50)  NOT NULL,
    phone          varchar(20),                         -- 가입 필수 — 주문·배송 연락. 필수는 API에서, DB는 NULL 허용(휴대폰 없는 기존 회원에 가짜 값을 채우지 않음)
    -- address: 플랫폼 0007에서 제거. 주소는 shipping_address로 이동 — 기존 값은 기본 배송지로 옮김
    status         varchar(16)  NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','WITHDRAWN')),
    -- 로그인 실패 5회 → 15분 자동 잠금 — 정책정의서 4-3 "고객 인증". 컬럼명은 구현 시 확정
    failed_login_count int      NOT NULL DEFAULT 0,
    locked_until   timestamptz,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    withdrawn_at   timestamptz,
    CHECK (status <> 'WITHDRAWN' OR withdrawn_at IS NOT NULL)
);

-- 동의 항목 정의 (PLT-01)
CREATE TABLE consent_item (
    code            varchar(32)  PRIMARY KEY,   -- 'TOS','PRIVACY_REQUIRED','AGE_OVER_14'(필수),'MARKETING'
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
    item_version  varchar(16) NOT NULL,          -- 동의 당시 약관 버전. 문안이 바뀌면 고쳐 쓰지 않고 버전을 올린다(예: 수집·이용 동의 v2 — 휴대폰·배송지 추가)
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

-- 결제수단: 실제 구현은 아래 설계안이 아니라 `payment` + `refund_account` 두 테이블(아래 표 참고)
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


**결제 관련 테이블 — 구현 (플랫폼 마이그레이션 0003. 정확한 컬럼은 레포 마이그레이션 기준)**

| 테이블 | 담는 것 | 담지 않는 것 / 규칙 |
|---|---|---|
| `payment` | PG 승인 결과 — 결제수단 종류, 카드사, **PG 거래번호**(서버 생성 `MOCKPG-…`), 승인 시각, 금액(서버가 상품 가격으로 결정) | **카드번호 컬럼 자체가 없음**(컬럼 집합을 테스트로 고정). 주문 API는 카드번호·금액을 보내도 무시 |
| `refund_account` | 은행, 예금주, **계좌번호 암호문**, 끝 4자리(평문), 회원당 1개 | **AES-256-GCM**, 버전 1바이트 + nonce 12바이트, **AAD = `refund_account:{회원번호}`**(암호문을 다른 회원 행으로 옮기면 복호화 실패). 키 `PAYMENT_ENCRYPTION_KEY`(16진수 64자, 없거나 형식 오류면 기동 거부) — platform-api·seed에만 전달 |
| `orders` | 상품 1개, 상태 `PAID`만 | 취소·환불 처리는 v0.2 |

**플랫폼 보강 (플랫폼 마이그레이션 0006~0008)**

| 테이블 | 담는 것 | 규칙 |
|---|---|---|
| `shipping_address` (신규) | 회원별 배송지 여러 개 — 배송지 이름, 받는 사람, 연락처, 우편번호, 주소·상세주소, 기본 배송지 여부 | 회원당 기본 배송지 1개(**부분 유니크 인덱스**로 DB에서도 강제), 회원당 최대 10개. 첫 배송지는 자동 기본, 기본을 지우면 가장 먼저 등록한 것이 승계. 연락처·우편번호는 API 필수·DB NULL 허용(옮겨 온 기존 주소에 없는 값을 지어 넣지 않음). 탈퇴 시 즉시 삭제. 게이트웨이 데이터 유형 `MEMBER_BASIC`(분류표를 테스트로 고정). 0007에서 기존 `member.address`를 기본 배송지로 옮기고 컬럼 삭제 |
| `orders` (컬럼 추가) | **배송 정보 스냅샷** — 받는 사람·연락처·우편번호·주소 | 주문 시점 값을 복사 — FK 아님(배송지를 고치거나 지워도 주문 기록 유지). 0008 이전 주문은 비워 둠. 탈퇴 시 PAYMENT_5Y 분리보관으로 옮기고 운영 테이블에서는 지움(문의 본문과 같은 원칙) |

- **카드**: 카드 저장·관리 없음. 결제할 때마다 PG 목업 결제창에서 카드번호를 직접 입력하고, 카드번호는 **결제창(브라우저) 안에만 두고 플랫폼 서버로 보내지 않는다**(형식 확인 없이 입력만, CVC 칸 없음) → `payment` 테이블은 그대로(카드사·PG 거래번호·승인 시각·금액, 카드번호·끝 4자리 없음). 빌링키 저장안(`payment_card`)은 채택하지 않았다
- 관리자 화면: **주문 상세에 배송 정보 표시**(운영팀 배송 업무 — 주문 조회 접속기록 `READ`·`ORDER`). 회원 상세의 배송지 마스킹·전체 보기는 v0.2.
- **보호 대상 등록부 분류 필수**: 새 테이블·컬럼은 Argus 보호 대상 등록부(3-8)에 분류한다. 분류하지 않은 컬럼은 "미분류"로 드러나고, 개인정보 컬럼이 등록되지 않은 테이블은 데이터 유형 `NONE`이 되어 DB 직접 접근 탐지(기본 룰은 개인정보 유형만 봄)에서 빠진다.

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

-- 권한 부여·변경·말소 이력 (고시 §5①③, 3년 — 플랫폼 0009)
CREATE TABLE operator_permission_history (
    id              bigserial    PRIMARY KEY,
    operator_id     bigint       NOT NULL REFERENCES operator(id),
    change_type     varchar(8)   NOT NULL CHECK (change_type IN ('GRANT','CHANGE','REVOKE')),
    before_role     varchar(16),
    after_role      varchar(16)  NOT NULL,
    before_team     varchar(50),
    after_team      varchar(50)  NOT NULL,
    before_status   varchar(16),
    after_status    varchar(16)  NOT NULL,
    reason          varchar(500) NOT NULL CHECK (length(btrim(reason)) > 0),   -- 사유 필수
    actor_id        bigint       REFERENCES operator(id),    -- NULL = 시스템(시드·마이그레이션)
    created_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX ix_operator_permission_history_target  ON operator_permission_history (operator_id, created_at DESC);
CREATE INDEX ix_operator_permission_history_created ON operator_permission_history (created_at DESC);
-- UPDATE·DELETE는 행 트리거(trg_permission_history_append_only)로 거부 — 플랫폼은 DB 계정이 하나라 권한으로 막을 수 없어 소유자도 막는다

-- DB 접속 토큰 발급 기록 (아키텍처 설계서 3-4)
-- 감사용 기록일 뿐 검증에 쓰지 않는다: 게이트웨이는 서명 토큰(JWT)의 서명·만료만 검증하고 이 테이블을 조회하지 않음
-- (공용 계정이 DB 소유자라 이 테이블은 게이트웨이 접속자가 고칠 수 있음 → 검증 근거로 쓰면 위장 가능)
CREATE TABLE db_access_token (
    token_id     uuid         PRIMARY KEY,       -- 토큰의 jti. 게이트웨이 기록의 context.token_id와 연결
    operator_id  bigint       NOT NULL REFERENCES operator(id),
    issued_at    timestamptz  NOT NULL DEFAULT now(),
    expires_at   timestamptz  NOT NULL,          -- issued_at + 1시간 (고정)
    issued_ip    inet         NOT NULL,          -- 발급 요청 IP (관리자 화면 접속지)
    CHECK (expires_at = issued_at + interval '1 hour')
);
-- 토큰 값·해시는 저장하지 않는다(발급 시 화면에 한 번만 표시). 발급 사유 칸 없음(이상 징후 시 소명 단계가 있음)
-- SQL 원문·매개변수는 이 DB가 아니라 게이트웨이 원문 저장소(gateway-data 볼륨)에 둔다 — 같은 이유(소유자 계정의 변조 가능성)
CREATE INDEX ix_db_access_token_operator ON db_access_token (operator_id, issued_at);

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

**계정·권한 이력 (플랫폼 마이그레이션 0009)**

- 관리자 화면 "계정·권한"(ADMIN 전용)에서 부여(`GRANT` — 새 계정, 임시 비밀번호는 응답에 한 번만), 변경(`CHANGE` — 역할·팀), 말소(`REVOKE` — 퇴직 처리)를 하면 같은 트랜잭션에서 이력 1건과 Argus 취급자 동기화 이벤트를 outbox에 넣는다. 사유는 필수이고 본인 계정은 바꿀 수 없다(정책정의서 4-5).
- 0009가 이미 있던 계정마다 `GRANT` 1건(사유 "초기 계정")을 채웠고, 시드 스크립트도 같은 방식으로 남긴다.
- 계정·권한 화면과 본인 비밀번호 변경은 회원 개인정보 처리가 아니라 접속기록(Agent) 대상이 아니다. 이 이력이 증적이다.
- `TRUNCATE`는 막지 않는다(Argus 원장만 막음). 3년 보관 뒤 파기는 파기 배치(v0.2)와 함께 다룬다.

### 4-1. 회원 탈퇴 시 파기·분리보관 절차

> **v0.1은 탈퇴 즉시 파기**: 파기 배치는 v0.2라, 아래 절차대로 "상태만 바꾸고 배치를 기다리면" v0.1 동안 탈퇴 회원 정보가 계속 남아 **PIPA §21(지체 없이 파기) 위반 상태**가 된다. 따라서 **탈퇴 처리 트랜잭션 안에서** 아래를 수행한다. 아래 번호 절차는 v0.2 파기 배치의 설계로 유지. **보안성 검토 때 재확인.**
>
> | 순서 | v0.1 탈퇴 처리 (비밀번호 재확인 후, 한 트랜잭션) |
> |---|---|
> | 1 | 주문이 있으면 `retained_member_record`에 **PAYMENT_5Y**(전자상거래법 시행령 §6①3호, 5년) — 주문번호·상품·금액·일시·PG 거래번호·카드사 + **주문의 배송 정보 스냅샷**(재화 공급 기록) + 분쟁 시 본인 확인용 이름·이메일·전화. 비밀번호·배송지 목록·환불계좌는 담지 않음 |
> | 2 | 문의가 있으면 **DISPUTE_3Y**(시행령 §6①4호, 3년) — 제목·본문·답변·시각 + 연락처를 **옮기고**, 운영 테이블 `inquiry`의 제목·본문·답변은 "(탈퇴 회원 문의 — 분리보관됨)"으로 지움(번호·상태·시각·답변자만 남음) |
> | 3 | 환불계좌·동의 이력·배송지·회원 행 **실제 삭제**(`orders.member_id`는 SET NULL). 주문의 배송 정보 스냅샷은 1번 PAYMENT_5Y에 옮긴 뒤 `orders`에서 지움 |
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
| PLT-04·05 주문·결제 | `orders`, `payment`, `product`, `refund_account`(계좌번호 암호화 컬럼) |
| PLT-06·17 1:1 문의 | `inquiry` |
| PLT-10 관리자 진입 | **`operator.role`** |
| PLT-11·13·16 조회·다운로드 | 로깅 대상 (스키마 아님) |
| PLT-14 권한 관리 | `operator.role`·`must_change_password`, `operator_permission_history`(플랫폼 0009) |
| PLT-15 접속기록 생성·전송 | `outbox` |
| LOG-01 수집·저장 | `access_log` |
| LOG-02 조회·검색 | `access_log` + **GIN·복합 인덱스** |
| LOG-03 탐지 | `detection_rule`, `detection`, `detection_batch_run` |
| LOG-04 룰 빌더 | `detection_rule.condition/aggregate` |
| LOG-05 화면 알림·웹 푸시 | `notification`(0015), `push_subscription`(0016), 소명 기한 `explanation.due_at`(0015) |
| LOG-06~08 소명 | `explanation`, `explanation_attachment`, `detection_status_history` |
| LOG-09 보고서 | **`inspection_report`** |
| LOG-10 마스킹/해제 | `access_log.action='UNMASK'` + `context.reason/target`, 2-1절 표기 규칙 |
| LOG-11 심각도 | `detection_rule.severity`, `detection.severity` |
| LOG-12 화이트리스트 | `rule_exception` |
| LOG-13 룰 변경 이력 | **`detection_rule_history`** |
| LOG-14 소명 이력 누적 | 쿼리 집계 |
| LOG-15 시간차 | 계산 |
| LOG-16 이메일 알림 | v0.2 — 발송 이력이 필요해지면 그때 채널·발송 결과 컬럼 추가(3-4 "알림") |
| LOG-17 Argus 자체 기록 | `access_log` (`source_system='ARGUS'`) |
| LOG-18 취급자 본인 접속기록 | `access_log` — 행위자를 연결된 `handler`로 고정(3-1 "A5의 조회 범위") |
| LOG-19 Argus 계정 관리 | `argus_user`, **`argus_user_history`**(0020) |
| LOG-20 보호 대상 등록부 | **`protected_column`·`protected_column_history`·`db_schema_column`·`db_schema_receipt`**(0021, 3-8) |
| 원장 보호(§8③) | `access_log` UPDATE·DELETE·TRUNCATE 트리거(0002·0017), `inspection_report.summary.integrity` 기준점 |
| 2단계 DB 직접 접근 | `access_path='DB'` + `context`에 **정규화 SQL·DB 계정·테이블·컬럼·건수·원문 참조·지문·정보주체 미특정·토큰 ID**(API명세서 2-3 — 원문 SQL은 Argus에 저장하지 않음). 실사용자는 `actor_login_id`(토큰 주인 = 플랫폼 아이디). 플랫폼 쪽은 **`db_access_token` 발급 기록**(4절). 탐지는 `detection.access_path`(0012)로 경로별, DB 기본 룰 3개(3-6 시드). 정보주체는 결과의 회원 식별 열에서 추출하고, 회원 열이 없는 조회·결과를 돌려주지 않는 변경은 미특정(3-2). 데이터 유형은 보호 대상 등록부(3-8) 기준이며 `context.registry_version`에 판정 버전이 남는다 |

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

v0.1 보강 마이그레이션(Argus 0014~0022, 플랫폼 0009)은 각 서비스의 테스트로 확인한다. 소유자 계정의 `access_log` DELETE·TRUNCATE 거부(`test_append_only`), 원장 끝부분 삭제가 보고서 기준점 대조에서 `MISMATCH`로 드러나는지(`test_reports_api`), 계정·권한·등록 이력의 UPDATE·DELETE 거부(`test_users_api`·`test_accounts`·`test_protection`), 데이터 유형·행위 구분이 다른 기록의 탐지건 분리(`test_explanation_scope`), 기본 룰 시드와 설명 문구(`test_ip_subject_rules`·`test_migrations`)가 포함된다.

---

## 8. 미결 / 구현 시 확정

- [x] 회원 탈퇴 시 법정 보존 항목 분리보관 구조 → 4-1절
- [x] 해시체인 정규화(canonical) 규칙 세부 → 3-5절 "정규화 규칙 v1". 검증 함수 `verify_chain` 구현
- [x] DB 계정·권한 구조 → 3-5절 "DB 계정 구조"
- [x] Argus 비밀번호 해시 → argon2id
- [x] 알림 테이블 필요 여부 → `notification`은 화면 알림(받는 사람별, 본문 없음, 0015), 웹 푸시는 `push_subscription`·`notification.push_pending`(0016). 이메일 발송 추적은 v0.2
- [ ] 동의 기록의 탈퇴 후 보관 여부 — 4-1절 각주 (운영 정책 판단)
- [ ] 필수 수집·이용 동의를 계약 이행 고지(§15①4, §22③)로 바꿀지 검토 — 바꾸면 기존 동의 이력은 증적으로 보존
- [ ] 파기 배치(v0.2)의 원장 삭제 경로 — 0017 트리거를 지나는 파기 전용 경로와 `destruction_history` 체인 앵커(3-5)
- [ ] 계정·권한·등록 이력의 TRUNCATE 차단과 보관 기간 파기 (v0.2)
- [ ] 보호 대상 컬럼 단위 판정, 여러 DB 등록 (v0.2, 3-8)
- [ ] 첨부파일 파기: 소명 이력 파기 시 볼륨의 실제 파일도 함께 삭제하는 절차 (구현 시)
