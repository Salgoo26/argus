# ARGUS

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo/argus-logo-dark.svg">
    <img src="docs/images/logo/argus-logo.svg" alt="ARGUS" height="120">
  </picture>
</p>

<p>
  개인정보처리시스템의 접속기록을 저장하여 이상 행위를 자동으로 탐지하고,<br>
  탐지룰 설정, 이상 행위에 대한 소명 제출, 접속기록 점검 및 보고까지 수행할 수 있는 <b>개인정보 접속기록 점검 시스템</b>
</p>

<p align="center">
  <img src="docs/images/01-detection.gif" alt="취급자가 회원 목록을 CSV로 내려받자 Argus가 대량 다운로드를 탐지하고 담당자에게 알림, 탐지 상세에서 마스킹된 정보주체와 하위 접속기록을 확인" width="100%">
  <br><sub>회원 목록 CSV 다운로드 → 대량 다운로드 탐지·알림 → 탐지 상세(정보주체 마스킹, 하위 접속기록, 자동 소명 요청)</sub>
</p>

| 항목 | 내용 |
|---|---|
| 기간 | 2026-09-07 ~ 2026-10-11 (v0.1) |
| 인원 | 1명 — 문제 정의, 법령 분석, 설계, 구현 검수, 자체 점검. 코드 구현은 AI 코딩 도구(Claude Code) |
| 근거 법령 | 개인정보 보호법, 개인정보의 안전성 확보조치 기준(2026.7.1 시행 고시)과 안내서(2025.11) |
| 구성 | 커머스 플랫폼(쇼핑몰·관리자 화면, 감시 대상) + DB 게이트웨이 + ARGUS(접속기록 점검 시스템) — 2절 |
| 기술 | Python 3.12 · FastAPI · PostgreSQL 16 · Next.js · Docker Compose · GitHub Actions |
| 검증 | 단위·통합 테스트 1,127개(Argus 589 · 플랫폼 324 · 게이트웨이 214), E2E 26개, PR마다 CI 20개 작업 |

> 시스템 내 회원·직원·접속기록 데이터는 모두 가상으로 생성한 값입니다. 실제 개인정보는 없습니다.

---

## 1. 프로젝트 배경

재직 중 개인정보처리시스템의 접속기록을 매월 점검하는 업무를 담당했습니다. 당시 업무를 수행하며 겪었던 불편함과 사각지대를 개선하고자 ARGUS 프로젝트를 시작하게 되었습니다.

### 실무에서 마주친 문제

| 순번 | 내용 |
|---|---|
| 1 | 월 단위로 접속기록 점검 업무를 진행했기 때문에, 개인정보취급자들은 몇 주가 지난 본인의 과거 작업을 파악하고 소명을 작성하는 일에 어려움을 겪었습니다. |
| 2 | 취급자들이 자신의 개인정보 접속기록에 대해 소명을 제출한다고 해도, 이는 개인의 주관과 기억에 의존한 텍스트에 불과했기 때문에 검증이 어려웠습니다. 자칫하면 악의를 품고 소명을 허위로 지어낼 가능성도 존재했습니다. |
| 3 | 점검자는 취급자별 접속기록을 추출하고, 해당 데이터가 접근량 증가·야간 접속 등의 이상행위 기준에 부합하는지 하나하나 대조하여 확인해야 했습니다. 점검자의 실수로 특정 행위를 누락하여 보고할 위험이 있었습니다. |
| 4 | 점검 및 보고의 과정에서 자료 취합 시간이 불필요하게 길었습니다. 점검자는 접속기록을 해당하는 취급자들에게 발송하고, 사유를 전달받아 취합해야 했습니다. 또한 점검 내용을 별도의 결재 문서에 옮겨서 작성해야 하는 불편함이 있었습니다. |
| 5 | 점검자는 점검 과정에서 점검 대상인 개인정보취급자들에게 지속적으로 연락해야 했습니다. 최초 접속기록 전달부터 기한 내에 소명을 제출하지 않은 경우까지 적지 않은 취급자들에게 일일이 연락해야 하는 어려움이 있었습니다. |
| 6 | 업무시스템을 통한 접속기록과 DB 직접접근을 통한 접속기록을 따로 점검하고 보고했습니다. 이에 따라 점검을 수행하는 업무 담당자들이 많은 시간을 쏟아야 했습니다. |

### 어떻게 해결하고자 하는가?

| 순번 | 내용 |
|---|---|
| 1 | 탐지 배치가 일정 시간마다 실행되어 개인정보 처리 행위 직후 해당 기록을 탐지하고, 소명을 자동으로 요청합니다. 취급자는 소명 화면에서 관련 접속기록을 함께 확인할 수 있습니다. |
| 2 | 기존의 텍스트 기반 소명에서 나아가, 개인정보 처리 행위와 관련된 업무 티켓을 연결하거나 근거(결재 문서·요청 메일 등)를 첨부할 수 있습니다. 담당자는 소명 내용을 확인한 뒤 승인·반려·재요청합니다. |
| 3 | 담당자의 수기 점검이 아닌, 룰을 기반으로 한 자동 탐지 방식을 적용합니다. 담당자가 시스템에서 룰을 만들고 수정할 수 있으며, 룰 변경 이력이 남습니다. |
| 4 | 개인정보 처리 행위에 대한 탐지·소명·판단이 한 화면에서 이어지고, 이를 바탕으로 점검 보고서를 자동으로 생성합니다. |
| 5 | 소명 기한이 임박하거나 지나면 취급자와 담당자에게 자동으로 알림을 보냅니다. |
| 6 | 업무시스템과 DB 직접접근 두 경로의 접속기록을 한 곳에 모아 조회·점검합니다. 보고서는 두 경로를 한 문서에 담되 경로별로 나눠 집계하며, 필요하면 한 경로만 따로 생성할 수 있습니다. |

### 왜 자동화했나

「개인정보의 안전성 확보조치 기준」(2026.7.1 시행) §8②는 접속기록을 확인·점검하는 주기·방법·사후조치 절차를 내부 관리계획으로 정해 이행하도록 할 뿐, 점검 방식을 정하지 않습니다. 접속기록을 자동화된 방식으로 분석해 이상 행위를 탐지하고 소명을 요청하는 조치는 공공시스템 운영기관에만 의무입니다(§17). 민간 기업은 수기 점검만으로도 법적 의무를 다할 수 있습니다.

그럼에도 자동화를 적용한 것은, 위의 문제들이 법적 의무를 지키는 것만으로는 해결되지 않았기 때문입니다. 월 단위 수기 점검은 적법하지만 행위와 소명 사이에 몇 주의 시차가 생겨 소명의 정확도가 떨어지고, 점검자가 기준을 하나하나 대조하는 과정에서 누락이 생길 수 있습니다. 자동 탐지는 행위 직후 소명을 받아 취급자의 부담을 줄이고, 같은 기준을 빠짐없이 적용해 점검의 사각지대를 줄입니다. ARGUS의 자동화는 의무 이행 자체보다 점검이 실제로 효과를 내도록 하는 데 목적이 있습니다.

### 목표

ARGUS 프로젝트의 최종 목표는 '접속기록 점검자'와 점검 대상인 '개인정보취급자'가 더욱 정확하고 편리하게 개인정보를 관리하고 점검할 수 있는 환경을 만드는 것이고, 이를 바탕으로 더 견고한 개인정보 접속기록 관리 체계를 갖추는 것입니다.

## 2. 시스템 구성

### 전체 구성

ARGUS를 시연하기 위해 감시 대상이 되는 커머스 플랫폼을 함께 만들었습니다. 개인정보취급자가 개인정보에 접근하는 경로는 두 가지이며, 두 경로의 접속기록이 모두 ARGUS로 모입니다.

```mermaid
flowchart LR
    CUST(["고객"]) --> SHOP
    HANDLER(["개인정보취급자<br/>CS·마케팅·운영"])
    OFFICER(["개인정보보호 담당자<br/>점검자"])

    subgraph PLATFORM["커머스 플랫폼 (감시 대상)"]
        SHOP["쇼핑몰"]
        ADMIN["관리자 화면<br/>(백오피스)"]
        PDB[("플랫폼 DB<br/>회원·주문·결제·문의")]
        SHOP --> PDB
        ADMIN --> PDB
    end

    GW["DB 게이트웨이"]
    ARGUS["ARGUS<br/>개인정보 접속기록 점검 시스템"]

    HANDLER -->|"① 업무시스템"| ADMIN
    HANDLER -->|"② DB 직접접근 (DB 툴)"| GW
    GW --> PDB

    ADMIN -.->|"접속기록"| ARGUS
    GW -.->|"접속기록"| ARGUS

    OFFICER -->|"탐지 확인·소명 검토·보고서"| ARGUS
    HANDLER -->|"소명 제출"| ARGUS
```

| 구성 | 사용자 | 역할 |
|---|---|---|
| 쇼핑몰 | 고객 | 회원가입·주문·결제·1:1 문의. 감시 대상 개인정보가 여기서 생깁니다(모두 가상 데이터) |
| 관리자 화면(백오피스) | 개인정보취급자 | 회원 조회·다운로드, 주문·문의 처리. 쇼핑몰과 같은 플랫폼의 관리자 화면(`/admin`)이며, 화면 요청마다 서버가 접속기록을 남깁니다(① 업무시스템 경로) |
| DB 게이트웨이 | 개인정보취급자 | DB 툴 접속을 중계하며 공용 DB 계정 뒤의 실제 사용자 기준으로 접속기록을 남깁니다(② DB 직접접근 경로). 관리자 화면에서 발급한 1시간 접속 토큰으로만 접속합니다 |
| ARGUS | 담당자·취급자 | 두 경로의 접속기록을 수집·보관하고, 이상 행위를 탐지해 취급자에게 소명을 요청하며, 점검 보고서를 만듭니다 |

### 아키텍처

```mermaid
flowchart TB
    TOOL["DB 툴"]

    subgraph PNET["platform-net"]
        direction LR
        PW["platform-web<br/>쇼핑몰·관리자 화면"] --> PA["platform-api<br/>접속기록 미들웨어"]
        PA -->|"접속기록 적재<br/>(업무와 별도 트랜잭션)"| PDB[("platform-db<br/>업무 데이터 + 전송 대기 기록")]
    end

    subgraph BRIDGE["두 네트워크에 연결"]
        direction LR
        PR["platform-relay<br/>전송 대기 기록 전송"]
        GW["db-gateway<br/>PostgreSQL 프로토콜 중계"]
        GWS[("원문·전송 버퍼<br/>SQLite")]
        GW --- GWS
    end

    subgraph ANET["argus-net"]
        direction LR
        AWEB["argus-web<br/>담당자·취급자 화면"] --> AA["argus-api<br/>수집 API"]
        AA --> ADB[("argus-db<br/>기록 원본<br/>추가만 가능 · 해시체인")]
        AW["argus-worker<br/>탐지 배치 · 기한 순찰 · 알림"] --> ADB
    end

    TOOL -->|"TLS + 1시간 접속 토큰"| GW
    PDB -->|"전송 대기 기록"| PR
    GW -->|"쿼리 중계"| PDB
    PR -->|"HMAC 서명"| AA
    GW -->|"HMAC 서명"| AA
    AA -.->|"보호 대상 등록부"| GW
```

| 구성요소 | 기술 | 역할 |
|---|---|---|
| platform-web | Next.js 16 · React 19 · TypeScript | 쇼핑몰(`/`)과 관리자 화면(`/admin`) |
| platform-api | Python 3.12 · FastAPI · SQLAlchemy 2.1 · Alembic | 쇼핑몰·관리자 API. 관리자 화면 요청을 미들웨어가 기록합니다. 업무가 실패해도 시도는 실패로 남기고, 기록하지 못하면 응답을 막습니다 |
| platform-relay | Python (platform-api와 같은 이미지) | 전송 대기 기록을 2초마다 Argus로 보냅니다(HMAC 서명, 재시도, 중복 제거) |
| db-gateway | Python asyncio · SQLite | DB 툴과 플랫폼 DB 사이에서 PostgreSQL 프로토콜을 중계합니다. SQL 원문은 게이트웨이 안에만 두고, Argus에는 검색값을 뺀 SQL과 원문의 지문만 보냅니다. 데이터 유형은 Argus의 보호 대상 등록부로 판정하며, 등록부를 받지 못하면 마지막 등록부, 그마저 없으면 기본 분류표를 씁니다 |
| argus-api | Python 3.12 · FastAPI · SQLAlchemy 2.1 · Alembic | 수집 API(서명 검증), 담당자·취급자 화면 API |
| argus-worker | Python (argus-api와 같은 이미지) | 5분 주기 탐지 배치, 소명 기한 순찰, 화면 알림·웹 푸시 |
| argus-web | Next.js 16 · React 19 · TypeScript | 담당자(탐지 목록·소명 검토·룰 설정·보고서)·취급자(소명 관리·접속기록) 화면 |
| platform-db · argus-db | PostgreSQL 16 | 두 DB는 서로 다른 네트워크에 둡니다. platform-relay와 db-gateway만 두 네트워크에 함께 연결됩니다 |

- 보안: HMAC-SHA256 요청 서명, TLS(게이트웨이), AES-256-GCM(환불 계좌), 웹 푸시 암호화(RFC 8291·8292)
- 인프라·CI: Docker Compose. GitHub Actions에서 PR마다 ruff·pytest·eslint·tsc·E2E·gitleaks·trivy·Docker 빌드

### 데이터 모델

Argus의 핵심 테이블입니다. 이력 테이블(룰 변경·상태 변경·계정 변경)과 알림·설정 테이블은 생략했습니다.

```mermaid
erDiagram
    source_system ||--o{ handler : "출처 시스템"
    source_system ||--o{ access_log : "출처 시스템"
    handler ||--o{ argus_user : "취급자 계정"
    access_log ||--o{ detection_log : ""
    detection ||--|{ detection_log : "근거 기록"
    detection_rule ||--o{ detection : "탐지"
    detection ||--o{ explanation : "차수별 소명"
    explanation ||--o{ explanation_attachment : "근거 첨부"
    argus_user ||--o{ explanation : "요청·제출·검토"
    argus_user ||--o{ inspection_report : "생성"

    access_log {
        bigint id PK
        uuid event_id UK "중복 수신 방지"
        varchar access_path "APP 업무시스템 / DB 직접접근"
        varchar actor_login_id "행위자"
        timestamptz occurred_at
        inet client_ip "접속지"
        text_array subject_ids "처리한 정보주체(회원번호만)"
        varchar action "수행업무"
        varchar data_category "데이터 유형"
        char prev_hash "앞 기록의 해시"
        char hash "해시체인"
    }
    detection_rule {
        bigint id PK
        varchar rule_type "EVENT / AGGREGATE"
        varchar access_path
        varchar severity
        jsonb condition "룰 조건식"
        int version
    }
    detection {
        bigint id PK
        bigint rule_id FK
        jsonb rule_snapshot "탐지 당시 룰"
        varchar actor_login_id
        varchar status "탐지~승인·반려·이관"
        int round "소명 차수"
    }
    detection_log {
        bigint detection_id PK,FK
        bigint access_log_id PK,FK
    }
    explanation {
        bigint id PK
        bigint detection_id FK
        int round
        text content "소명 내용"
        varchar_array ticket_ids "관련 업무 티켓"
        varchar review_result "승인 / 반려"
        timestamptz due_at "소명 기한"
    }
    explanation_attachment {
        bigint id PK
        bigint explanation_id FK
        char sha256 "제출 후 변조 확인"
    }
    inspection_report {
        bigint id PK
        timestamptz period_from
        timestamptz period_to
        jsonb summary "생성 시점 스냅샷"
    }
    argus_user {
        bigint id PK
        varchar role "OFFICER 담당자 / HANDLER 취급자"
        bigint handler_id FK
    }
    handler {
        bigint id PK
        varchar login_id "플랫폼 계정"
        varchar employment_status "재직 / 퇴직"
    }
    source_system {
        smallint id PK
        varchar code
    }
```

- **access_log**는 추가만 가능합니다. UPDATE·DELETE·TRUNCATE를 트리거로 거부하고, 각 기록이 앞 기록의 해시를 이어받아 위·변조를 확인합니다. 정보주체는 회원번호로만 남기고 이름·연락처 같은 원본 개인정보는 넣지 않습니다
- **detection**은 탐지 당시의 룰을 스냅샷으로 저장합니다. 룰을 나중에 고쳐도 그때 어떤 기준으로 탐지했는지 남습니다
- **explanation**은 탐지건과 차수(round)로 구분합니다. 반려 뒤 재요청하면 새 차수로 쌓여 이전 소명이 덮이지 않습니다

<details>
<summary><b>플랫폼 DB (감시 대상)</b></summary>

```mermaid
erDiagram
    member ||--o{ member_consent : "동의 이력"
    consent_item ||--o{ member_consent : "동의 항목·버전"
    member ||--o{ orders : ""
    orders ||--|| payment : ""
    member ||--o| refund_account : ""
    member ||--o{ shipping_address : ""
    member ||--o{ inquiry : ""
    operator ||--o{ inquiry : "답변"
    operator ||--o{ operator_permission_history : "권한 부여·변경·말소"
    operator ||--o{ db_access_token : "DB 접속 토큰"

    member {
        bigint id PK "접속기록에는 이 값만 전송"
        varchar email
        varchar name
        varchar phone
        varchar status "ACTIVE / WITHDRAWN"
    }
    consent_item {
        varchar code PK
        boolean required "필수 / 선택"
        varchar version
    }
    member_consent {
        bigint id PK
        bigint member_id FK
        varchar item_code FK
        varchar item_version "동의 당시 버전"
        boolean agreed
    }
    refund_account {
        bigint id PK
        bigint member_id FK
        bytea account_number_enc "AES-256-GCM"
    }
    operator {
        bigint id PK
        varchar login_id
        varchar role "ADMIN / CS / MARKETING / OPS"
        varchar employment_status
    }
    operator_permission_history {
        bigint id PK
        bigint operator_id FK
        varchar change_type "GRANT / CHANGE / REVOKE"
        varchar reason "사유 필수"
    }
    db_access_token {
        uuid token_id PK
        bigint operator_id FK
        timestamptz expires_at "발급 후 1시간"
    }
    orders {
        bigint id PK
        bigint member_id FK
    }
    payment {
        bigint id PK
        bigint order_id FK
        varchar card_company
    }
    shipping_address {
        bigint id PK
        bigint member_id FK
    }
    inquiry {
        bigint id PK
        bigint member_id FK
        bigint answered_by FK
    }
```

이 밖에 상품, 전송 대기 기록(outbox), 탈퇴 회원 파기·분리보관 테이블이 있습니다.

</details>

### 설계 문서

| 문서 | 내용 |
|---|---|
| [요구사항](docs/requirements.md) | 기능·비기능 요구, 범위 |
| [정책](docs/policy.md) | 탐지 룰, 소명·상태 전이, 권한, 마스킹, 보관·파기 |
| [액터별 흐름](docs/actor-flows.md) | 담당자·취급자·관리자·고객의 화면 흐름 |
| [아키텍처](docs/architecture.md) | 수집 구조, 보안 설계, 한계와 다음 버전 |
| [API 명세](docs/api-spec.md) | 시스템 간 API와 화면 API |
| [DB 스키마](docs/db-schema.md) | 전체 테이블, 룰 조건식, 탐지 배치 |

## 3. 주요 기능

두 경로의 접속기록이 모인 뒤 담당자와 취급자가 쓰는 기능입니다. 수집 구조는 2절에 있습니다.

| 기능 | 내용 |
|---|---|
| 자동 탐지 | 기본 룰 13개(야간·주말·퇴직자·대량 조회·대량 다운로드·전월 대비 급증·결제수단 조회·허용 범위 밖 접속지·짧은 시간 여러 접속지·특정 회원 반복 처리, DB 직접접근 야간·주말·급증). 룰 빌더로 조건·임계치·심각도를 직접 정한다 |
| 소명 | 탐지 즉시 취급자에게 자동 요청. 글 + 근거 첨부 + 관련 업무 티켓. 기한 7일, 반려 시 재요청(차수), 정보보안팀 이관 |
| 알림 | 접속 중이면 화면 알림, 아니면 웹 푸시. 심각도 상 탐지는 담당자에게, 소명 요청은 취급자에게, 기한 임박·초과는 취급자와 담당자에게 |
| 검색 | 탐지건(기간·룰·취급자·심각도·상태), 접속기록(계정·기간·수행업무·회원번호·접속지·데이터 유형·성공/실패). 취급자는 본인 접속기록을 조회할 수 있다 |
| 점검 보고서 | 기간·경로 선택, 생성 시점 스냅샷. 기록 원본 해시체인 재점검 결과, 탐지 배치 실행 기록, 탐지건별 소명 요지·검토 의견, 접속기록 기준점(마지막 기록의 해시) |
| 권한 관리 | 플랫폼은 역할별 접근 범위와 계정 부여·변경·말소(사유 필수, 이력 3년). Argus 계정 변경도 사유와 이력을 남긴다 |

### 화면으로 보기

<details>
<summary><b>소명 작성 → 담당자 승인</b> — 취급자가 업무 티켓을 붙여 소명하고, 담당자가 확인 후 승인</summary>
<br>
<img src="docs/images/02-explanation.gif" alt="취급자가 소명 관리에서 소명 내용과 관련 1:1 문의 티켓을 입력해 제출하고, 담당자가 탐지 상세에서 사유를 확인해 승인" width="100%">
</details>

<details>
<summary><b>점검 보고서</b> — 기간·경로별 보고서 생성, 무결성 점검 결과와 탐지건별 소명 요지</summary>
<br>
<img src="docs/images/03-report.gif" alt="보고서 관리에서 기간을 정해 보고서를 생성하고, 접속기록 무결성·기준점 대조·경로별 집계·탐지건별 소명과 검토 의견을 확인한 뒤 PDF로 저장" width="100%">
</details>

<details>
<summary><b>탐지 룰 만들기</b> — 담당자가 화면에서 조건·임계치·심각도를 정해 새 룰 추가</summary>
<br>
<img src="docs/images/04-rules.gif" alt="룰 설정에서 DB 직접접근 야간 대량접근 룰을 만들고 수행업무·시간대·처리 건수 조건을 지정" width="100%">
</details>

<details>
<summary><b>DB 직접접근</b> — 1시간 접속 토큰으로 DB 툴에 접속하고, 조회 내역이 Argus에 기록</summary>
<br>
<img src="docs/images/05-db-access.gif" alt="플랫폼 관리자 화면에서 DB 접속 토큰을 발급받아 DB 툴로 게이트웨이에 접속하고 회원 테이블을 조회" width="100%">
<br><br>
<img src="docs/images/06-db-access-log.png" alt="Argus 접속기록에서 DB 직접접근 기록이 검색값을 $1로 바꾼 SQL, 테이블, 마스킹된 정보주체, 처리 건수, 연결된 탐지건과 함께 업무시스템 기록 옆에 표시" width="100%">
<br><sub>DB 툴 조회가 Argus 접속기록에 남은 모습. SQL의 검색값은 <code>$1</code>로 바뀌어 저장되고, 정보주체는 결과에서 읽은 회원번호(마스킹)로 기록된다</sub>
</details>

## 4. 법적 근거와 구현

| 근거 | 요구 | 구현 | 확인 |
|---|---|---|---|
| 고시 §2 3호 | 접속기록 항목: 계정, 일시, 접속지, 처리한 정보주체, 수행업무 | 업무시스템·DB 직접접근 모두 다섯 항목 기록. DB 직접접근은 결과에서 회원번호를 읽는다 | [test_agent.py](apps/platform-api/tests/test_agent.py), [test_db_gateway.py](e2e/test_db_gateway.py) |
| 고시 §8① | 1년 이상 보관, 5만 명 이상·고유식별·민감정보 처리 등은 2년 | 기록 원본은 삭제할 수 없게 막는다. 등록부에 고유식별·민감정보가 있으면 2년 보관 대상으로 판정 | [test_append_only.py](apps/argus-api/tests/test_append_only.py), [test_protection.py](apps/argus-api/tests/test_protection.py) |
| 고시 §8② | 접속기록·다운로드 점검의 주기·방법·사후조치를 내부 관리계획으로 정하고 이행 | 5분 주기 자동 탐지, 다운로드 룰, 소명·판정, 점검 보고서 | [test_scenario.py](e2e/test_scenario.py), [test_report.py](e2e/test_report.py) |
| 고시 §8③ | 위·변조·도난·분실 방지 | 추가만 가능한 기록 원본(UPDATE·DELETE·TRUNCATE 거부), 해시체인, 보고서에 기준점 저장 후 다음 보고서에서 대조, 기록하지 못하면 응답 차단 | [test_reports_api.py](apps/argus-api/tests/test_reports_api.py), [test_self_access_log.py](apps/argus-api/tests/test_self_access_log.py) |
| 고시 §5③ | 권한 부여·변경·말소 내역 3년 보관 | 플랫폼·Argus 계정 이력(추가만 가능, 사유 필수) | [test_accounts.py](apps/platform-api/tests/test_accounts.py), [test_users_api.py](apps/argus-api/tests/test_users_api.py) |
| 고시 §5①, §6① | 업무에 필요한 최소 권한, 비정상 접근 탐지 | 역할별 접근 범위(거부도 기록), 접속지 기반 룰 | [test_roles.py](apps/platform-api/tests/test_roles.py), [test_ip_subject_rules.py](apps/argus-api/tests/test_ip_subject_rules.py) |
| 안내서 FAQ 147 | 로그인·로그아웃·로그인 실패 기록 | 세 가지 모두 기록(존재하지 않는 아이디의 실패는 8절) | [test_v01_reinforcement.py](e2e/test_v01_reinforcement.py) |
| 안내서 94쪽 | 점검의 객관성 | 담당자 본인이 행위자인 탐지건은 처리할 수 없다 | [test_self_review.py](apps/argus-api/tests/test_self_review.py) |
| 고시 §17 (참고) | 공공시스템: 자동 분석·탐지, 사유 소명, 기능 추가 시 기록 누락 방지 | 자동 탐지·소명. 기록 표시가 없는 관리자 화면이 있으면 서버가 기동하지 않는다 | [test_agent.py](apps/platform-api/tests/test_agent.py) |

## 5. 개인정보 보호 설계

Argus의 접속기록에는 처리한 정보주체가 들어가므로 Argus 자신도 개인정보처리시스템입니다. 세 가지 원칙을 지켰습니다.

1. 감시 시스템이 또 하나의 개인정보 저장소가 되지 않게 한다. 플랫폼은 회원 내부 번호만 보내고, 검색 조건은 값 없이 키 이름만, SQL 원문은 게이트웨이에만 둔다. 화면과 보고서의 정보주체는 기본 마스킹(`member_10***`)이다. 재측정에서 원장 1,171건 전체에 이메일·전화번호 형태의 값이 0건이었다
2. 기록할 수 없으면 내보내지 않는다. 접속기록을 남기지 못하면 업무시스템은 응답을, DB 직접접근은 쿼리 결과를 막는다. CSV 같은 개인정보가 기록 없이 나가는 경로를 없애기 위해서다
3. 감시받는 쪽이 기록을 고칠 수 없다. 플랫폼과 Argus의 DB를 완전히 분리하고, 기록 원본은 추가만 가능하며 해시체인으로 이어진다

감시 대상 쇼핑몰도 최소 수집으로 만들었습니다. 생년월일·성별은 받지 않고, 카드번호는 결제창 안에서만 입력되어 쇼핑몰 서버에 오지 않으며, 환불계좌는 암호화해 저장합니다. 탈퇴하면 즉시 파기하고 법정 보존분만 분리 보관합니다.

## 6. 스스로 점검한 결과

v0.1 구현을 마친 뒤 고시 §8·§5와 안내서 기준으로 체크리스트를 만들고 실제 동작을 측정해 판정했습니다. 판정 중 안내서 128~130쪽이 공공시스템(§17) 해설임을 확인해, 그 항목들은 의무가 아니라 "공공 기준 참고"로 다시 분류했습니다.

| 영역 | 첫 판정 | 조치 후 재측정 | 조치 |
|---|---|---|---|
| Argus가 고객사의 준수를 돕는가 (20개) | 충족 7 · 부분 8 · 미충족 5 | 충족 14 · 부분 6 · 미충족 0 | 로그아웃 기록, 접속지·특정 회원 반복 처리 룰, 담당자 본인 건 처리 차단, 보고서 소명 내용, 권한 이력, 알림, 검색 조건, 기록 원본 삭제 차단과 보고서 기준점 |
| Argus 자체의 준수 (9개) | 충족 5 · 부분 2 · 미충족 1 · 다음 버전 1 | 충족 6 · 부분 2 · 다음 버전 1 | 계정 변경 사유와 이력 |
| 감시 대상 쇼핑몰 (8개) | 충족 3 · 부분 2 · 미충족 2 · 위험 수용 1 | 충족 5 · 부분 2 · 위험 수용 1 | 처리방침·이용약관 정식 문안, 동의 항목 문안 보완 |

재측정은 조치를 반영한 빈 환경에서 실제 화면과 수집 API로 다시 재현했고, 기록 원본 전체에 이메일·전화번호 형태의 값이 0건, 해시체인 검증 정상(1,171건)인 것도 함께 확인했습니다.

부분 충족으로 남긴 것과 이유는 아래 7·8절에 적었습니다.

## 7. 판단이 필요했던 지점

DB 직접접근의 "처리한 정보주체". DB 툴로 조회하면 누구의 정보를 봤는지 화면처럼 알 수 없습니다. 안내서는 대량 처리 시 검색 조건문을 정보주체 정보로 기록할 수 있되, 해당 시점 백업 등 책임 추적 조치를 함께 요구합니다(93쪽). 그래서 SQL을 해석하지 않고, PostgreSQL이 결과와 함께 주는 열 정보(테이블 OID·열 번호)로 회원 식별 열을 찾아 그 값만 기록하게 했습니다. 결과에 회원 식별 열이 없는 쿼리는 정규화 SQL과 원문 지문으로 남기고, 시점 백업이 없으므로 이 부분은 부분 충족으로 판정했습니다.

소명은 자동으로 요청한다. 담당자가 모든 탐지건을 먼저 보고 요청하면 병목이 생기고 방치됩니다. 사람의 판단을 "요청할지"가 아니라 "받아들일지"에 쓰도록 했고, 오탐은 담당자가 사유를 적고 요청을 취소합니다.

탐지 알림은 심각도 상만 보낸다. 모든 탐지를 알리면 담당자가 알림을 무시하게 됩니다. 중·하는 목록과 기한 관리로 다룹니다.

웹 푸시는 라이브러리 없이 구현했다. 표준(RFC 8291·8292)을 이미 쓰고 있던 암호 라이브러리로 직접 구현해 새 의존성을 늘리지 않았고, 테스트에서 받는 쪽 입장으로 복호화해 정확성을 확인했습니다. 푸시 내용에는 종류·심각도·탐지건 번호만 담아 개인정보가 브라우저 푸시 서버를 거치지 않게 했습니다.

## 8. 알려진 한계와 다음 버전

| 한계 | 계획 |
|---|---|
| 업무시스템 기록의 임시 버퍼가 플랫폼 DB 안에 있고, DB 게이트웨이의 공용 계정이 DB 소유자 권한이라 전송 전 기록을 지울 수 있는 위치다 | 버퍼를 플랫폼 DB 밖으로 옮기고 공용 계정 권한을 분리 |
| 기록 원본의 삭제 차단 트리거는 DB 소유자·슈퍼유저가 끌 수 있다. 보고서 기준점을 출력해 결재 문서에 붙이는 운영을 전제로 한다 | 외부 기준점 보관(WORM 저장소 등) |
| 보관 기간이 지난 접속기록을 지우는 파기 배치가 없다(지금은 무기한 보관) | 파기 전용 경로와 함께 구현 |
| DB 직접접근의 시점 백업이 없어 검색 조건문 기록은 부분 충족 | 시점 복구(PITR)와 재현 절차 |
| 보호 대상 등록부의 관리 화면(미분류 컬럼 표시 포함)과 Argus 계정 관리 화면을 아직 공개하지 않았다. 판정은 테이블 단위이고, 업무시스템 기록의 데이터 유형은 코드의 기록 표시로 따로 관리된다 | 관리 화면 공개, 컬럼 단위 판정, 자동 식별, 화면 기록도 등록부를 따르게 |
| 존재하지 않는 아이디의 로그인 실패는 기록하지 않는다(행위자를 특정할 수 없음) | 접속지 기준 집계로 대응 검토 |
| 공휴일 달력이 없어 업무시간 외 판정은 야간·주말만 | 공휴일 반영 |
| DB 접속 토큰은 접속할 때만 재직·잠금을 확인한다. 이미 열린 연결은 말소 뒤에도 만료(최대 1시간)까지 유지되고, 역할이 바뀌어도 발급된 토큰은 쓸 수 있다 | 말소·역할 변경 시 토큰 폐기와 연결 종료 |

## 9. 실행

```bash
git clone https://github.com/Salgoo26/argus.git && cd argus
bash scripts/init-env.sh          # .env 생성(무작위 비밀값)
docker compose up -d --build      # 플랫폼 http://localhost:3000 · Argus http://localhost:3001
bash scripts/e2e.sh               # 전체 시나리오 자동 검증
```

계정 준비, 시연 순서, DB 툴 접속 방법은 [실행 안내](docs/run.md)에 있습니다.

## 10. 만든 방식

문제 정의 → 요구사항·정책 → 아키텍처·API·DB 설계 → 구현 → 자체 점검 → 조치 순서로 진행했고, 단계마다 설계 문서에 결정과 기각한 대안을 남겼습니다. 코드 구현은 AI 코딩 도구(Claude Code)로 했습니다. 문제 정의, 법령 해석, 설계 결정, 구현 결과 검수와 점검 판정은 직접 했습니다. 모든 변경은 PR과 CI(테스트·린트·E2E·보안 검사)를 거쳐 반영했습니다.
