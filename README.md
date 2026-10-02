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

Python·Node는 호스트에 설치하지 않고 모두 컨테이너 안에서 실행한다. 아래 명령은 모두 **Git Bash**, 레포 루트 기준이다.

### 준비물

- Git for Windows (Git Bash 포함)
- Docker Desktop (WSL 2 엔진) — Windows 기능의 **가상 머신 플랫폼**, **Linux용 Windows 하위 시스템**이 켜져 있어야 한다

### 1. 처음 실행

```bash
git clone https://github.com/Salgoo26/argus.git
cd argus

# 환경변수 파일(.env) 만들기 — DB 비밀번호·서명 키 등을 무작위 값으로 채운다 (.env는 커밋되지 않음)
bash scripts/init-env.sh

# 기동 — 이미지 빌드 → DB → 마이그레이션·시드(일회성 컨테이너) → API → 화면 순으로 뜬다
# (.env의 COMPOSE_FILE이 infra/docker-compose.yml을 가리킨다. 첫 빌드는 몇 분 걸린다)
docker compose up -d --build

# 상태 확인 — platform-web·argus-web이 (healthy)가 되면 준비 완료
docker compose ps
```

> 이미 `.env`가 있으면 `init-env.sh`는 덮어쓰지 않는다. 버전을 올린 뒤 기동이 거부되면 `.env.example`과 비교해 새로 생긴 항목을 추가한다(값이 없거나 짧으면 기동 거부).

### 2. 화면으로 시나리오 따라하기

Walking Skeleton이 관통하는 시나리오(CLAUDE.md 6절)를 두 화면으로 직접 수행한다.

```
플랫폼 관리자 ops_park이 회원 120명 CSV 다운로드 → 접속기록이 Argus로 전송 → "대량 다운로드" 룰 탐지 + 자동 소명 요청
→ 담당자 확인(마스킹) → 취급자 소명 제출 → 담당자 반려 → 재요청(2차) → 재제출 → 승인
```

**① Argus 계정 준비** — 담당자(A4) 계정은 직접 만들고, 취급자(A5) 계정은 플랫폼에서 자동 동기화되지만 비밀번호가 없어 설정해야 한다. 비밀번호는 실행 후 입력창에서 입력한다(12자 이상, 셸 기록에 남지 않게 명령줄에 쓰지 않음). **플랫폼 백오피스 아이디 = Argus 아이디** 원칙에 따라 담당자도 플랫폼에 같은 아이디(`officer`, 시드 취급자와 같은 비밀번호)의 계정이 있다 — 동기화로 Argus에 먼저 생긴 `officer` 취급자 계정(로그인 불가)은 아래 `create-officer`가 담당자 계정으로 전환한다.

```bash
winpty docker compose exec argus-api python -m app.scripts.users create-officer officer   # 담당자
winpty docker compose exec argus-api python -m app.scripts.users set-password ops_park    # 취급자
```

> `winpty`는 Git Bash에서 비밀번호 입력창을 띄우기 위해 붙인다("the input device is not a TTY" 방지).

**② 플랫폼: 대량 다운로드** — http://localhost:3000/admin/login

- `ops_park`으로 로그인한다. 비밀번호는 시드 취급자 공용 비밀번호로, `.env`에서 확인한다:
  ```bash
  grep '^PLATFORM_SEED_OPERATOR_PASSWORD=' .env
  ```
- 회원 목록 → **CSV 다운로드**(최대 건수 기본값 120) → 이 다운로드가 접속기록이 되어 몇 초 안에 Argus로 전송된다

**③ 탐지 배치** — 5분마다 자동으로 돌지만, 기다리지 않고 바로 한 번 실행할 수 있다:

```bash
docker compose exec argus-worker python -m app.worker --once   # detected=1 이상이면 탐지건 생성
```

> 기본 룰은 7개다(Argus 화면 **룰** 메뉴에서 기준값을 고치거나 새 룰을 만들 수 있다 — 다음 순찰부터 적용) — 대량 다운로드(50건 이상), 야간 접속(22:00~06:00 조회·다운로드), 주말 접속, 퇴직자 계정 접속, 대량 조회(매시 정각부터 1시간에 조회 100건 이상), 전월 대비 급증(이달 조회가 지난달 같은 기간의 2배 이상, 지난달 같은 기간이 20건 미만이면 판정 안 함), 결제수단 조회(환불계좌 전체 보기마다). 시각·요일·윈도우는 모두 한국 시각. 밤이나 주말에 따라 하면 같은 다운로드가 야간·주말 룰로도 탐지되어 탐지건이 더 생긴다 — 아래 흐름은 **"대량 다운로드"** 건으로 진행한다. 룰은 플랫폼 기록에만 적용하고, Argus 자체 접속기록(담당자·취급자의 Argus 사용)은 원장에 남기기만 한다.
>
> 처음 기동할 때 시드가 **지난달 1일부터의 가상 조회 기록**(평일 근무 시간)을 수집 API로 보낸다 — 전월 대비 급증 룰의 기준선이다. 이번 달은 `cs_kim`만 지난달의 3배(하루 15건)로 넣어 두어, 월초가 지나면 "전월 대비 급증" 탐지건이 생긴다(월초에는 기준선이 20건 미만이라 판정하지 않음). 이미 시드된 DB에는 넣지 않으므로, 보려면 `docker compose down -v` 후 다시 기동한다.

**④ Argus: 결재 흐름** — http://localhost:3001

> 담당자와 취급자는 **일반 창과 시크릿 창으로 나눠** 로그인한다. 같은 창에서 두 계정을 번갈아 쓰면 세션 쿠키가 덮어써진다.

| 순서 | 창 | 계정 | 할 일 | 상태 |
|---|---|---|---|---|
| 1 | 일반 | `officer` | 탐지건 목록에서 ops_park의 "대량 다운로드" 건 열기 — 정보주체가 `member_10***`로 가려져 있고 1차 요청자가 시스템 | `REQUESTED` |
| 2 | 시크릿 | `ops_park` | 내 소명 요청 → 소명 내용 입력 → (선택) **관련 업무 티켓** 입력(플랫폼 1:1 문의 번호, 예: `INQ-12`, 최대 3개) → (선택) **근거자료 첨부**(PNG·JPG·PDF, 5MB, 최대 3개 — 결재 문서·요청 메일 캡처 등) → **제출** | `SUBMITTED` |
| 3 | 일반 | `officer` | 반려 사유 입력 → **반려** → **재요청** | `REJECTED` → `REQUESTED`(2차) |
| 4 | 시크릿 | `ops_park` | 2차 소명 **제출** | `SUBMITTED` |
| 5 | 일반 | `officer` | **승인** — 상세 하단에 차수별 소명(첨부 포함 — 이름을 누르면 내려받기, 다운로드도 Argus 자체 접속기록)과 상태 이력이 남는다. 관련 티켓의 **플랫폼에서 보기**를 누르면 플랫폼 문의 상세가 새 탭으로 열린다(플랫폼에 `officer`로 로그인돼 있지 않으면 로그인 후 바로 그 문의로 이동 — 그 열람도 플랫폼 접속기록으로 Argus에 남는다) | `APPROVED`(종결) |

- 갈림길: 1에서 오탐이라고 판단하면 사유를 적고 **요청 취소** → `DISMISSED`(종결)
- 취급자는 자기 건만 볼 수 있다. 다른 취급자 계정(예: `cs_kim` — 비밀번호를 ①처럼 설정)으로 로그인하면 이 건은 목록에 없다

**⑤ 원장 무결성 확인** — 지금까지 쌓인 접속기록(플랫폼 + Argus 자체)의 해시체인을 처음부터 다시 계산한다(§8③):

```bash
docker compose exec argus-api python -m app.scripts.verify_chain   # OK: 해시체인 정상 — N건 확인
```

### 2-1. 고객 화면 (개인정보 처리 단계 실습 무대)

http://localhost:3000 — 가상 쇼핑몰의 고객 화면. Argus 시연용이 아니라 **수집·동의·열람·정정·철회·파기** 흐름을 보여 주기 위한 최소 구현이다. 고객(정보주체)의 행위는 접속기록 대상이 아니다(§8① 단서).

- **회원가입** — 필수(이용약관·개인정보 수집이용·만 14세 이상)와 선택(마케팅) 동의를 따로 받고, 항목마다 목적·항목·보유 기간을 보여 준다. 동의·미동의 모두 항목·버전·시각·IP로 이력에 남는다. 시드 회원 500명은 로그인할 수 없으니 새로 가입해서 쓴다(실제 개인정보 입력 금지 — `@example.com` 등 가상 값 사용)
- **로그인** — 5회 실패 시 15분 잠금(자동 해제). 고객 계정과 관리자 계정은 쿠키·토큰 용도가 달라 서로 바꿔 쓸 수 없다
- **마이페이지** — 내 정보 열람·정정, 비밀번호 변경, 마케팅 동의 철회·재동의(이력이 쌓임), **탈퇴 = 즉시 파기**(회원 행·동의 이력·환불계좌 실제 삭제, 주문 기록은 최소 항목만 5년·문의 내용은 3년 분리보관 — 전자상거래법 시행령 §6)
- **주문·결제** — 상품을 고르면 **가상 PG 결제창**(데모페이)이 뜬다. 카드번호 입력칸이 없다: 쇼핑몰은 카드번호를 받지도 저장하지도 않고 PG 승인 결과(카드사·거래번호·금액)만 저장한다
- **1:1 문의** — 고객이 문의를 남기고 답변을 확인한다. 이 문의가 CS가 고객 정보를 조회하는 업무 근거가 된다
- **환불계좌** (마이페이지) — 계좌번호는 AES-256-GCM으로 암호화해 저장(키는 `.env`의 `PAYMENT_ENCRYPTION_KEY`, DB와 분리), 화면에는 끝 4자리만
- **개인정보 처리방침·이용약관** — 자리표시 문안(정식 문안은 기획 단계에서 교체)

**관리자: 결제수단 조회 탐지** — `/admin/members`에서 회원 이름 → 회원 상세(환불계좌는 끝 4자리) → **전체 보기**. 이 요청은 데이터 유형 "결제수단"으로 기록되어 Argus의 **결제수단 조회** 룰(상)에 매번 탐지된다(시드 회원 중 60명에게 가상 환불계좌가 있다). 주문 목록은 `/admin/orders`.

**관리자: 1:1 문의 처리 (CS)** — `/admin/inquiries` → 문의 열기(티켓 `INQ-번호`) → 작성자 링크로 회원 상세 → 답변 등록. 문의 상세·답변 기록에는 티켓 번호가 함께 실려 Argus로 간다(`context.ticket_id` — 문의 내용은 가지 않음). 이 기록이 탐지되면 Argus 탐지건 상세의 **연계 티켓** 칸에 보인다 — 취급자는 "INQ-12 처리 중 조회"처럼 소명할 수 있다.


### 3. 자동 검증 (E2E)

위 시나리오 전체를 화면 대신 같은 API로 자동 재현하고, 마지막에 해시체인을 검증한다. CI가 PR마다 같은 스크립트를 실행한다.

```bash
bash scripts/e2e.sh
```

- 평소 스택과 별도인 프로젝트(`argus-e2e`)를 **빈 DB로 새로 띄우고, 끝나면 볼륨까지 지운다** — 위에서 직접 만든 데이터는 건드리지 않는다
- 확인 항목: 고객 가입(필수 동의)·동의 철회·탈퇴 즉시 파기, 고객 토큰으로 관리자 API 불가, 가상 PG 구매·환불계좌 → 관리자 전체 보기 → 결제수단 조회 탐지(계좌번호·이름은 Argus로 안 감), 고객 문의 → CS 처리의 티켓 번호가 Argus 원장에 도착(문의 내용은 안 감), 소명 첨부(형식 위장 거부·제출 후 변경 불가·다운로드 해시 일치·자체 접속기록), 시나리오의 각 상태 전이, 원본 식별값·개인정보(이름·이메일·전화) 미노출, 남의 건 404, 역할 위반 403, 허용되지 않은 전이 409, 해시체인
- 실패 시 컨테이너 로그를 출력한다. `E2E_KEEP=1 bash scripts/e2e.sh`로 실행하면 스택을 남겨 두고 살펴볼 수 있다

### 서비스 구성

| 서비스 | 호스트 접속 | 비고 |
|---|---|---|
| platform-web | http://localhost:3000 | **플랫폼 화면** — 고객은 `/`(회원가입·로그인·상품·가상 PG 결제·주문 내역·1:1 문의·마이페이지·처리방침), 관리자는 `/admin` 아래(로그인·회원 목록·상세·CSV 다운로드·주문 조회·1:1 문의 처리). 운영에서는 Caddy가 `/admin` 경로를 허용 IP로 제한(architecture 7-2). `/api/*`는 Next.js가 platform-api로 전달(같은 출처라 세션 쿠키 그대로) |
| argus-web | http://localhost:3001 | **Argus 화면** — 담당자·취급자 로그인, 탐지건 목록·상세(정보주체 마스킹), 소명 요청·제출·승인·반려·요청 취소, **접속기록 검색**(담당자 전용 — 계정·기간·수행업무·회원번호·접근 경로·출처), **룰 관리**(담당자 전용 — 생성·수정·켜기/끄기, 변경 이력), **소명 근거자료 첨부**(취급자 업로드 — 파일 내용으로 형식 판정, 저장 이름은 서버가 정함, 내려받을 때마다 SHA-256 확인) |
| platform-db | `127.0.0.1:15432` | 플랫폼 DB (PostgreSQL 16) |
| argus-db | `127.0.0.1:15433` | Argus 접속기록 원장 (PostgreSQL 16) |
| argus-migrate | — | 기동 시 1회 실행: Alembic 마이그레이션 + API용 DB 계정 발급 후 종료 |
| argus-api | `127.0.0.1:18000` | 수집 API `POST /ingest/v1/access-logs`, 취급자 동기화 `POST /ingest/v1/handler-events`, 로그인 `POST /api/auth/login`, `GET /healthz`, API 문서 `/docs`. `/api` 요청은 Argus 자체 접속기록(ARGUS 출처)으로 원장에 기록 |
| argus-worker | — | 탐지 배치: `setting.detection_interval_min`(기본 5분)마다 원장을 순찰해 룰에 걸린 기록으로 탐지건 생성 + 자동 소명 요청 |
| platform-migrate | — | 기동 시 1회 실행: 플랫폼 Alembic 마이그레이션 후 종료 |
| platform-seed | — | 기동 시 1회 실행: 가상 회원 500명·취급자 5명(비어 있을 때만), 동의 이력이 없는 회원에게 가입 시점 동의 이력, 주문이 없으면 가상 주문 300건·환불계좌 60개(암호화), 문의가 없으면 가상 문의 40건(⅔ 답변 완료), 담당자 플랫폼 계정 `officer`가 없으면 생성 후 종료 |
| platform-api | `127.0.0.1:18001` | 고객 API `/shop/*`(접속기록 대상 아님), 관리자 로그인 `POST /admin/auth/login`, 회원 목록 `GET /admin/members`, CSV `GET /admin/members/export`, API 문서 `/docs`. 관리자 라우트 요청은 접속기록으로 outbox에 적재 |
| platform-relay | — | outbox → Argus 전송(HMAC 서명). 2초 주기, 실패 시 1분→최대 1시간 백오프. 플랫폼 쪽에서 유일하게 Argus 네트워크에 붙는다 |

- 소명 첨부 파일은 Argus 전용 볼륨 `argus-attachments`에 저장된다(argus-api만 붙음). `docker compose down -v`로 DB와 함께 지워진다.
- 포트는 호스트 루프백(`127.0.0.1`)에만 열린다. 두 DB는 도커 네트워크도 분리되어 있어 플랫폼 쪽 컨테이너에서 argus-db에 접근할 수 없다.
- argus-api는 DB 소유자가 아닌 **앱 계정**(`ARGUS_APP_DB_USER`)으로 접속한다. 이 계정은 접속기록(`access_log`)에 조회·추가만 할 수 있고 수정·삭제는 할 수 없다(§8③).
- API 헬스체크: `curl http://127.0.0.1:18000/healthz` (argus-api), `curl http://127.0.0.1:18001/healthz` (platform-api) → `{"status":"ok","db":"ok"}`
- 로컬에서는 화면 경유 요청의 접속지(IP)가 화면 서버 컨테이너 IP로 기록된다. 화면 서버는 신뢰 프록시가 아니기 때문이다(architecture 3-2). 운영에서는 Caddy가 `/api`를 API로 직접 보내 실제 IP가 기록된다.

### 계정 관리

로그인 **5회 연속 실패 시 잠긴다**(플랫폼 관리자·Argus 사용자 모두). 관리 UI는 Skeleton 범위 밖이라 스크립트로 해제한다. (고객 계정은 15분 뒤 자동 해제)

```bash
docker compose run --rm platform-migrate python -m app.scripts.unlock_operator ops_park   # 플랫폼 관리자
docker compose exec argus-api python -m app.scripts.users unlock ops_park                # Argus 사용자
```

### 단위·통합 테스트

테스트는 argus-db·platform-db에 임시 DB를 만들어 마이그레이션을 적용하고, 끝나면 지운다.

```bash
docker compose run --rm argus-api-test                           # pytest
docker compose run --rm argus-api-test ruff check --no-cache .   # lint
docker compose run --rm platform-api-test                        # 플랫폼도 같은 방식
```

### 중지

```bash
docker compose down      # 중지 (데이터 유지)
docker compose down -v   # 데이터까지 삭제 — 다음 기동 때 시드부터 새로 시작
```
