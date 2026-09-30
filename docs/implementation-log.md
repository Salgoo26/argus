# 구현 로그

> Claude Code가 세션을 마칠 때마다 아래 형식으로 **위에 새 항목을 추가**한다(최신이 위).
> 이 로그는 Cowork "구현" 방에서 읽어 claude.ai 프로젝트의 `진행기록.md`로 동기화된다.
> 기각한 대안과 그 이유도 남긴다 — 포트폴리오·면접의 근거 자료가 된다.

---

## 작성 형식

```
## YYYY-MM-DD — 마일스톤 Mx (세션 요약 한 줄)

**한 일**
-

**결정사항**
-

**설계 변경** (없으면 "없음")
- 무엇을 / 왜 / 영향받는 설계 문서

**미결·이슈**
-

**다음 할 일**
-
```

---

## 2026-09-30 — 마일스톤 M2 PR ② (플랫폼 기반: 스키마·시드·관리자 로그인·회원 목록·CSV)

**한 일**
- platform-api 뼈대를 argus-api와 같은 구조로: `create_app` 팩토리, 설정(`PLATFORM_*`), 오류 형식, Dockerfile(dev/runtime, non-root, HEALTHCHECK), Alembic
- 마이그레이션 `0001`: [S] 3개 `operator`·`member`·`outbox` (db-schema 4절 DDL 그대로, `ix_outbox_pending` 부분 인덱스 포함)
- 시드 `app.scripts.seed`: 가상 회원 500명(id 10001부터) + 취급자 5명(`ops_park`·`mkt_lee`·`cs_kim`·`cs_choi`·`admin_han`), 취급자마다 outbox `HANDLER_CREATED`를 같은 트랜잭션에 적재
- 관리자 로그인 `POST /admin/auth/login`·`/logout`, 인증 의존성 `current_operator`, 잠금 해제 스크립트 `app.scripts.unlock_operator`
- 회원 목록 `GET /admin/members`(20건씩), CSV `GET /admin/members/export`(`joined_from`·`joined_to`·`limit`)
- compose: `platform-migrate`·`platform-seed`(일회성) → `platform-api`, `platform-api-test`(profile). 네 컨테이너 모두 `platform-api:local` 이미지
- CI docker-build·Dependabot docker에 platform-api 추가, `.env.example`·README 갱신
- 의존성: FastAPI 등 argus-api와 같은 버전 + `argon2-cffi==25.1.0`, `pyjwt==2.15.1`, `faker==40.40.0`
- 검증
  - pytest 44개 통과: 마이그레이션 왕복, 시드(가상 형식·재현성·멱등·시드 계정 로그인·HANDLER 이벤트 형식과 마이크로초), 로그인(쿠키 속성, 해시 미노출, 실패 횟수 초기화, 없는 ID와 틀린 비밀번호 동일 응답, 5회 잠금, 잠김은 비밀번호가 맞을 때만 공개, 퇴직자 거부, 입력값 미반영 400), 토큰(다른 키·만료·alg=none·exp 없음·형식 오류 거부, 요청마다 연장, 로그인 후 퇴직·잠금 시 즉시 차단, 로그아웃), 회원(페이지, 120건 CSV, KST 날짜 필터, 탈퇴 회원 제외, 수식 주입 무력화, 잘못된 파라미터 400, no-store)
  - `docker compose up` 후 실제 서버: 로그인 전 401 → 틀린 비밀번호 401 → 로그인 200(HttpOnly·SameSite=strict·Secure·Max-Age=1800) → 목록 총 500·첫 id 10001 → CSV 120행 → 로그아웃 후 401. outbox에 HANDLER_CREATED 5건 PENDING

**결정사항**
- **없는 ID로 로그인해도 더미 해시로 argon2 검증을 수행** — 응답 내용뿐 아니라 **응답 시간**으로도 계정 존재 여부가 드러나지 않게
- **잠김·퇴직(403)은 비밀번호가 맞았을 때만 알려준다** — 비밀번호를 모르는 사람에게 "이 계정은 존재하고 잠겨 있다"를 알려주지 않음. 틀린 비밀번호는 잠긴 계정이어도 401 `INVALID_CREDENTIALS`
- 실패 횟수 증가는 `failed_login_count + 1`을 DB에서 원자적으로. 트랜잭션 안에서 결과를 정하고 오류는 커밋 뒤에 던짐(예외로 빠져나가면 증가분까지 롤백되므로)
- 로그인 입력 상한(`login_id` 64자, `password` 256자) — 초대형 비밀번호로 해시 계산 시간을 늘리는 공격 방지
- JWT: 알고리즘 HS256 고정(토큰 헤더의 alg 불신), `sub`·`iat`·`exp` 필수, 토큰엔 operator id만. 서명 키 32자 미만이면 **앱 기동 거부**
- 만료 연장 쿠키는 의존성이 `request.state`에 새 토큰을 두고 미들웨어가 응답에 싣는다 — 핸들러가 `Response`를 직접 반환하는 CSV에서도 빠지지 않게(FastAPI는 이 경우 의존성의 `Response` 헤더를 버림)
- `/admin` 응답 전부에 `Cache-Control: no-store` — 개인정보 응답이 브라우저·프록시 캐시에 남지 않게
- CSV: `=`,`+`,`-`,`@`,탭,CR로 시작하는 셀 앞에 `'`(OWASP CSV Injection), UTF-8 BOM(엑셀 한글), 파일명에 KST 시각, 가입일 필터는 KST 날짜 기준·양 끝 포함, 탈퇴 회원 제외, 최대 10,000행
- 시드
  - 이메일 `userNNNN@example.com`(RFC 2606 예약 도메인), 전화 `010-0000-NNNN` — Faker 결과가 우연히 실존 정보와 겹치는 것 방지
  - Faker·난수 시드 고정(20260929) → 누가 실행해도 같은 회원이 같은 id. 테이블이 비었을 때만 실행
  - 회원 id를 10001부터 — 마스킹 표시(`member_10***`)가 의미 있게 보이도록
  - 회원 비밀번호는 무작위 해시 하나를 공유(고객 로그인은 범위 밖, argon2 500회 계산 회피)
- 컨테이너별 최소 시크릿: `platform-migrate`는 DB 접속만, `platform-seed`만 시드 비밀번호, `platform-api`만 JWT 키. 잠금 해제 스크립트도 시크릿 없는 `platform-migrate`로 실행
- `.env.example`의 새 더미 값은 일부러 길이 검사에 걸리는 `change-me` — 값을 안 바꾸면 조용히 도는 대신 기동이 거부됨

**기각한 대안**
- FastAPI 의존성에서 `Response` 파라미터로 쿠키 연장: CSV처럼 `Response`를 직접 반환하는 라우트에서 헤더가 버려짐 → 미들웨어
- 시드에 `random` 대신 `secrets`: 재현성(고정 시드)이 목적이라 부적합. 보안 용도가 아니므로 ruff S311은 사유를 적고 예외 처리

**설계 변경**
- 없음 (전날 확정한 설계 변경 2·3을 구현)

**미결·이슈**
- **PR #8이 GitHub에서 "머지됨"으로 처리되지 않음 (GitHub 측 누락)**
  - 경위: 2026-09-29 웹에서 머지 → main에 `e30f958 Merge pull request #8` 생성(작성자 사용자 계정). 버튼이 두 번 처리되며 두 번째가 "Base branch was modified" 오류
  - 이후 하루가 지나도 PR은 open, `mergeable_state=dirty`로 표시. **그 머지 커밋에 대한 main CI도 실행되지 않음**, PR 타임라인에 머지·닫힘 이벤트 없음. GitHub 상태 페이지에 관련 장애 공지 없음
  - 확인한 사실: PR 브랜치가 main의 조상(`merge-base --is-ancestor` 참), 합쳐도 충돌 없음(`merge-tree`), 두 트리 내용 동일 → **코드는 정상 반영, GitHub의 push 후처리(PR 상태 갱신·main CI)만 누락**
  - 대응: 다시 머지하거나 브랜치를 건드리지 않음. PR ② 머지(다음 main push) 때 GitHub가 열린 PR을 재점검해 해소되는지 확인 → 안 되면 근거 코멘트를 남기고 Close. 누락된 main CI는 PR ② 머지 때 합쳐진 트리로 실행됨
- Secure 쿠키는 http 내부망 요청에는 실리지 않음(규칙대로 동작). 브라우저는 `http://localhost`를 예외로 허용하므로 로컬 화면(M5)은 문제없음. M5에서 Next.js가 서버 측에서 platform-api를 호출하는 구조라면 쿠키 전달 방식을 그때 정함

**다음 할 일**
- PR ② 머지 → PR #8 상태 확인 → PR ③(Agent 미들웨어·데코레이터, 접속기록 outbox 적재, relay, LOGIN 기록)

---

## 2026-09-29 — 마일스톤 M2 착수 (계획 확정, PR ①: Dependabot 제외 규칙 + ② 취급자 동기화 수신)

**한 일**
- M2 계획 수립·설명(outbox 패턴, 이미지·컨테이너 개념 포함), 사용자와 결정 6건 확정(아래)
- M2를 PR 3개로 분할: ① Argus 수신 마무리 / ② 플랫폼 기반(스키마·시드·로그인·회원 목록·CSV) / ③ Agent 미들웨어·outbox·relay
- PR ① 구현
  - `dependabot.yml` docker 생태계에 `ignore`(python 이미지 minor·major 제외) — M1 미결 해소
  - platform-api `setuptools>=84.0.0` — M1 미결 해소
  - `POST /ingest/v1/handler-events`: HMAC·배치 한도·응답 형식은 ① 재사용, 건별 검증(`app/ingest/handler_validation.py`), 적용(`app/handlers/sync.py`)
  - 판정: `INSERT ... ON CONFLICT (source_system_id, login_id) DO UPDATE ... WHERE last_event_at < EXCLUDED.last_event_at RETURNING id` — 행이 돌아오면 accepted, 안 돌아오면 duplicates. 비교·쓰기가 한 문장이라 동시 요청에도 안전
  - A5 계정 부수 효과(상태 기준): ACTIVE → 연결된 계정이 없을 때만 생성 / TERMINATED → DISABLED가 아니면 전환
  - 의존성 추가: `argon2-cffi==25.1.0` (A5 초기 해시, M4 로그인에서도 사용)
- 검증
  - pytest 93개 통과(M1 59 + 신규 34): 생성·A5 계정, 재전송 duplicates(updated_at까지 불변), 최신 변경 덮어쓰기, 늦게 도착한 옛 이벤트 duplicates, 한 배치 안 순서 역전, 1마이크로초 차이 두 변경 모두 적용, 퇴직 → DISABLED, 퇴직 재적용 불변, 재입사 시 계정 미복구, 퇴직 상태로 처음 들어온 취급자는 계정 미생성, 담당자 계정과 login_id 충돌 시 탈취 안 함, rejected 17가지 경우, 최소수집(연락처 미저장), 거부 메시지에 이름 미포함, 서명 불일치 401, 101건 413
  - 실행 중인 argus-api에 서명한 요청: 생성 accepted → 재전송 duplicates → 퇴직 accepted → 옛 이벤트 duplicates → 퇴직 시각 누락 rejected. DB에서 TERMINATED·DISABLED·`$argon2id$` 확인 후 점검 행 삭제

**결정사항**
- **로그인 실패 제한은 지금(M2) 기본 정책으로 구현**, 한계는 보안성 검토 과제로 기록(사용자 질문: 검토 후 보완이 포트폴리오에 나은가?) — 설계(CLAUDE.md 5절, architecture 1절 "rate limit 대체")에 이미 있는 법정 기본 통제이고, "빠뜨린 걸 채웠다"보다 "기본 통제의 한계를 스스로 찾아 개선했다"가 설득력이 큼
- ② 수신 검증은 ①의 코드 체계를 재사용: `MISSING_FIELD`·`INVALID_FIELD`·`INVALID_TIMESTAMP` + `TERMINATED_AT_REQUIRED`(api-spec 3-2는 추가 코드만 명시)
  - `occurred_at`에 ①과 같은 "미래 5분 초과 거부" 적용 — 미래 시각이 들어오면 `last_event_at`이 미래로 밀려 그 시각까지의 정상 변경이 전부 duplicates로 무시됨
  - `terminated_at`은 미래 허용(퇴직 예정 시각, api-spec 3-1 예시가 occurred_at보다 늦음), 오프셋 필수
  - 재직(ACTIVE)인데 `terminated_at`이 오면 `INVALID_FIELD`, `HANDLER_TERMINATED`인데 재직 상태면 `INVALID_FIELD`(발신 측 버그를 조용히 적용하지 않음)
- A5 계정은 `argus_user.login_id = handler.login_id`. 같은 login_id를 다른 Argus 계정(예: 담당자)이 쓰고 있으면 **생성하지 않고 경고 로그** — 남의 계정을 취급자 계정으로 바꿔치기하지 않음
- 테스트 정리는 `TRUNCATE ... CASCADE` 대신 `DELETE` — argus_user를 참조하는 탐지 룰(M3 시드)까지 비워지는 것 방지

**기각한 대안**
- 로그인 실패 제한을 보안성 검토 이후로 미루기: 설계·법정 기본 통제를 일부러 비워 두는 셈 → 위 결정
- A5 계정 조회 후 저장(SELECT → INSERT/UPDATE): 사이에 동시 요청이 끼어들 수 있음 → upsert 한 문장
- A5 초기 비밀번호로 해시가 아닌 표식값(`!` 등): 로그인 코드가 특수 처리해야 함 → 정상 형식의 무작위 argon2id 해시

**설계 변경** (Cowork에서 원본 반영 필요)
1. **접속기록 outbox 적재 시점: "응답 직후" → "응답 헤더를 보내기 직전", 적재 실패 시 500(fail-closed)** — 응답 후 적재하면 적재 실패 시 기록 없이 개인정보(CSV 등)가 이미 나간 상태가 됨. "기록할 수 없으면 내보내지 않는다". 대가: 응답이 적재 시간만큼 늦고, 플랫폼 DB 장애 시 관리자 기능 정지. 한계: UPDATE는 업무 커밋이 먼저라 적재 실패 시 "수정은 됐는데 500" 가능 — 보호 효과는 주로 조회·다운로드 / 영향: architecture 3-2 #4 (PR ③에서 구현)
2. **플랫폼 관리자 인증 = JWT(HS256) 쿠키** — `HttpOnly` + `SameSite=Strict`, **30분 미사용 시 만료**(요청마다 재발급, 안전성 확보조치 기준의 "일정 시간 미사용 시 접속 차단"), 매 요청마다 operator 상태(퇴직·잠금) 재확인. 서버 세션은 [S] 스키마에 세션 테이블이 없어 기각. 한계: 로그아웃해도 토큰은 만료 전까지 유효 / 영향: CLAUDE.md 5절 "세션 또는 JWT" 확정, M4 Argus 인증도 같은 방식 예정 (PR ②)
3. **로그인 실패 정책: 5회 연속 실패 → 잠금, 성공 시 0으로, 해제는 스크립트**(관리 UI 범위 밖). **존재하지 않는 ID의 실패는 접속기록에 남기지 않음** — ID 칸에 비밀번호를 잘못 입력하면 append-only 원장에 영구 저장되고, 그 값은 §2 3호의 "취급자 식별자"도 아님. 존재하는 계정의 실패(비밀번호 오류·잠김·퇴직자)는 `LOGIN`/`FAILURE`로 기록 / 영향: policy(로그인 실패 기준값 신설), api-spec 2-4 (PR ②·③)
4. **A5 초기 비밀번호: 동기화 시 무작위 argon2id 해시로 계정 생성(사실상 로그인 불가), M4에서 관리 스크립트로 설정.** 재입사(TERMINATED → ACTIVE) 이벤트가 와도 DISABLED 계정을 자동 복구하지 않음 — 권한 복구는 사람이 판단 / 영향: api-spec 3-1 "A5 계정 발급"·7절 미결, architecture 11절 미결
5. **relay의 401 처리 해석: 백오프 간격(최대 1시간)으로만 재확인 + 매번 ERROR 로그** — api-spec 1-5의 "재시도는 멈추고 알림, 사람이 고치면 자동 재개"는 완전히 멈추면 자동 재개가 불가능해 문구가 상충 / 영향: api-spec 1-5 (PR ③)

**미결·이슈**
- **(보안성 검토 후보) 로그인 실패 제한의 한계** — ①잠금 해제가 스크립트뿐이고 시간 경과로 풀리지 않음 → 일부러 틀려서 정상 사용자를 잠그는 서비스 거부 가능 ②계정 단위로만 계산 → 여러 계정에 흔한 비밀번호를 하나씩 대입하는 password spraying 미탐지 ③IP 단위 제한 없음 ④존재하지 않는 ID 대입은 Argus에 보이지 않음(설계 변경 3의 대가)
- (보안성 검토 후보) JWT 로그아웃 후에도 만료 전까지 토큰 유효 — 서버 측 폐기 목록 또는 세션 저장소 검토
- 플랫폼 DB는 계정 1개(소유자)로 운영 — 무대장치라 Argus 같은 권한 분리는 하지 않음. ISMS-P 점검 실습의 발견사항 후보

**다음 할 일**
- PR ① 머지 → PR ②(platform-api 뼈대·Alembic `operator`·`member`·`outbox`, 시드, 관리자 로그인, 회원 목록·CSV export)

---

## 2026-09-29 — M1 마무리 (PR #5 머지, 설계 사본 v0.3 반영)

**한 일**
- PR #5(M1) 머지 확인: CI 9개 통과(ruff·pytest 59개, gitleaks, Trivy config 발견 0건, docker build 2종, CodeQL 3종). 로컬 main 최신화, 병합된 브랜치 삭제
- 머지 커밋 작성자가 noreply 주소로 기록된 것 확인 — 사용자가 GitHub "Keep my email addresses private" 설정 완료 (M1 로그의 미결 해소)
- Cowork "구현" 방에서 M1 설계 변경을 원본에 반영 → 사본 갱신분(api-spec v0.3, db-schema v0.3, architecture, CLAUDE.md, docs/README.md)을 구현과 대조, 불일치 없음 확인 후 별도 `docs:` PR로 올림
- 사용자에게 M1 단계별 설명, 테스트 59개·CI 검사 9개 내용 설명, DBeaver로 스키마 확인 안내

**결정사항**
- 없음 (설계 판단은 Cowork에서 확정 — 아래)

**설계 변경** (Cowork 확정, 사본 반영)
- **② 취급자 동기화의 중복 판정**: event_id를 저장하지 않고 상태 스냅샷 + `last_event_at` 기준(`occurred_at > last_event_at`일 때만 upsert, 그 외는 `duplicates`). `handler_event` 테이블은 기각. 플랫폼은 `occurred_at`을 마이크로초 정밀도로 보냄 / 영향: api-spec 3-1·3-2, db-schema 3-1 (M1 로그의 미결 해소)
- CLAUDE.md 7절에 "시크릿 파일은 값을 전부 가린 형태로만 확인" 규칙 추가 (M1 HMAC 키 노출 재발 방지)

- Dependabot docker PR #6(`python:3.12-slim` → `3.14-slim`)을 사유 코멘트와 함께 닫음 — Python 3.12 고정 정책(CLAUDE.md 5절, `requires-python >=3.12,<3.13`)과 충돌, 3.14 이미지에서는 패키지 설치 실패

**미결·이슈**
- platform-api setuptools `>=75` — Dependabot PR 미도착, M2에서 맞춤
- **Dependabot docker 설정에 Python 버전 업 제외 규칙 누락**(M1 설정 누락) → M2 PR 첫 커밋에서 `dependabot.yml`에 `ignore`(python 이미지 minor·major 업데이트 제외) 추가. Python 버전 업그레이드는 별도 작업으로 판단

**다음 할 일**
- `docs:` PR 머지 → 새 세션에서 M2 착수 (첫 커밋: Dependabot 제외 규칙 / 첫 기능: ② 취급자 동기화 수신 `POST /ingest/v1/handler-events`)

---

## 2026-09-29 — 마일스톤 M1 (Argus 수집: [S] 스키마, append-only·해시체인, 수집 API, 컨테이너·CI)

**한 일**
- 의존성 버전 고정: FastAPI 0.141.1, uvicorn 0.54.0, SQLAlchemy 2.1.1, Alembic 1.20.0, Pydantic 2.13.5, pydantic-settings 2.15.0, psycopg 3.3.6 / dev: httpx2 2.13.1(TestClient)
- Alembic 마이그레이션 3개
  - `0001` [S] 테이블 11개 — db-schema 3절 DDL을 그대로 옮김(부분 유니크 인덱스·CHECK·GIN 포함)
  - `0002` append-only 이중 장치 — `BEFORE UPDATE` 트리거 + `argus_app`/`argus_purge` 롤·테이블별 권한
  - `0003` 기준 데이터 — `source_system`(PLATFORM·ARGUS), `setting` 4개
- 해시체인: 정규화·계산·검증(`app/ledger/hashchain.py`), 유일한 INSERT 경로 `append_access_logs()`(`app/ledger/append.py`, advisory lock 직렬화 + 락 안에서 중복 판정·id 발급)
- 수집 API `POST /ingest/v1/access-logs`: HMAC 검증(상수 시간 비교, ±300초, 1MB 스트리밍 제한) → 건별 판정 → 멱등 append. `GET /healthz`(DB 실패 시 503)
- API용 DB 로그인 계정 발급 스크립트(`app.scripts.provision_db_roles`) — 소유자가 아닌 `argus_app` 멤버로 접속
- argus-api `Dockerfile`(dev/runtime 멀티스테이지, non-root, HEALTHCHECK), compose에 `argus-migrate`(일회성)·`argus-api`·`argus-api-test`(profile) 추가
- CI: `trivy-config`(report-only), `docker-build`(runtime·dev, push 안 함) job 추가. Dependabot에 docker 생태계(argus-api) 추가
- README: 실행·테스트 방법 갱신
- 검증
  - pytest 59개 통과(로컬 컨테이너) — 서명 불일치·본문 변조·알 수 없는 출처·시각 초과 401, 중복(재전송·같은 배치 안) duplicate, rejected 코드 19종, 100건 초과·1MB 초과 413, 해시체인 연속성, 슈퍼유저 변조·삭제 탐지, 6스레드 동시 append 후 단일 체인 유지, 앱 계정 UPDATE/DELETE/TRUNCATE 권한 거부, 소유자 UPDATE 트리거 거부, 마이그레이션 왕복
  - `docker compose up` 후 실제 서버에 서명한 요청 전송: 정상 2건 accepted → 재전송 duplicates 2 → 잘못된 키 401 → 필드 오류 건별 rejected → 원장 체인 검증 통과
  - Trivy config 0.74.0 로컬 실행: Dockerfile 27개 검사 통과, 실패 0

**결정사항**
- **append 함수는 Python**(`append_access_logs`)에 둔다. 정규화 코드를 append와 검증이 공유 → 규칙이 한 곳에만 존재. 앱 계정이 이 함수를 거치지 않고 INSERT해도 `verify_chain`이 잡아낸다
- **DB 계정 3층 구조**: 소유자(`ARGUS_DB_USER`, 마이그레이션 전용) / 앱 로그인(`ARGUS_APP_DB_USER`, `argus_app` 멤버, API 접속용) / `argus_purge`(NOLOGIN, 파기 배치 때 로그인 계정 발급). 소유자는 GRANT와 무관하게 전권을 가지므로 API가 소유자로 접속하면 권한 분리가 무의미해짐
- 앱 롤 권한: `access_log`·`detection_status_history`·`detection_log`는 SELECT·INSERT만(감사 추적 = append-only), `source_system`은 SELECT만, 나머지 업무 테이블은 SELECT·INSERT·UPDATE(DELETE 없음). 이후 테이블 추가 시 마이그레이션에서 **테이블마다 명시적으로 부여**(ALTER DEFAULT PRIVILEGES 미사용)
- 로그인 계정의 비밀번호는 마이그레이션이 아닌 별도 스크립트에서 설정 — 비밀번호가 마이그레이션 코드·이력에 남지 않게
- `received_at`은 DB default가 아니라 앱이 정해서 넣는다 — INSERT 전에 해시를 계산해야 하므로. 같은 이유로 append 호출자는 모든 컬럼을 명시해야 함(DB default에 기대면 저장값과 해시값이 어긋날 수 있음)
- 마이그레이션은 autogenerate 대신 **DDL 원문을 SQL로** — 부분 인덱스·CHECK·트리거·권한을 설계 문서 그대로 재현. SQLAlchemy 테이블 정의는 쿼리용으로 쓰는 테이블만 둠
- SQLAlchemy는 동기(psycopg3) — HMAC 검증만 async 의존성(원본 바이트 스트리밍), 본 처리는 스레드풀의 동기 함수
- 빈 `context {}`는 NULL로 저장
- 테스트는 세션마다 **임시 DB 생성 → 실제 마이그레이션 → 앱 계정 발급** 후 앱 계정으로 실행. 권한 분리를 모의 객체가 아닌 실제 DB로 검증
- 수집 거부 메시지에 입력값을 되풀이하지 않음(코드값 제외), FastAPI 기본 422 본문(입력값 포함)도 고정 형식 400으로 대체 — 응답·로그로 개인정보가 새지 않게
- Trivy는 gitleaks와 같은 방식(공식 이미지 `aquasec/trivy:0.74.0` 직접 실행, `--exit-code 0` report-only)

**기각한 대안**
- append를 PL/pgSQL `SECURITY DEFINER` 함수로(앱엔 EXECUTE만): 우회 INSERT 자체를 막는 강제력은 더 강하지만, 정규화를 SQL·Python 양쪽에 똑같이 구현해야 해 불일치 위험 → 보안성 검토 단계 개선 과제로 이관
- 해시 대상에서 `id` 제외: 체인만으로도 수정·삭제는 탐지되지만, 탐지 배치 커서가 `id`라 순서 바꿔치기까지 드러나게 포함
- 테스트에서 소유자 계정으로 API 실행: 간단하지만 권한 분리를 검증할 수 없음
- `uvicorn app.main:app`(모듈 전역 앱): import만으로 설정·DB 엔진이 만들어져 테스트가 불편 → `--factory` 방식
- 테스트 클라이언트 `httpx`: Starlette가 deprecated 경고 → 권장 클라이언트 `httpx2`

**설계 변경** (Cowork에서 원본 반영 필요)
1. **해시체인 정규화 규칙 v1 확정** — db-schema 8절·architecture 11절 미결 해소 / 영향: db-schema 3-5
   - 대상: `hash`를 뺀 access_log 전 컬럼(`id`·`prev_hash`·`received_at` 포함)
   - 표기: 시각 UTC `YYYY-MM-DDTHH:MM:SS.ffffffZ`, IP는 정규형(`2001:db8::1`), UUID 소문자 하이픈, NULL은 JSON null, 배열 순서 유지
   - 직렬화: JSON 키 정렬·공백 없음·UTF-8 / `hash = SHA-256` 소문자 hex / 첫 레코드 `prev_hash` = NULL
2. **이벤트 100건 초과 → `413 TOO_MANY_EVENTS`** (spec에 코드 미정) — 400이면 relay가 배치 전체를 DEAD 처리, 413이면 절반으로 쪼개 재전송(api-spec 1-5) → 접속기록을 버리지 않는 쪽 / 영향: api-spec 1-1·1-5·2-5
3. **rejected 코드 `INVALID_FIELD` 추가** — 코드표에 없던 필드 오류: `login_id` 형식·길이, `subject.ids` 1,000개 초과, `count < len(ids)`, `subject.type`·`result` 값 오류, 타입 오류, `request.path`에 쿼리스트링 / 영향: api-spec 2-5
4. **원본 개인정보 유입 차단 검증 추가**(CLAUDE.md 3절 #3의 수신 측 방어) — `subject.ids`는 `[A-Za-z0-9_-]{1,64}`만(이메일 등 거부), `request.path`에 `?`·`#` 금지, `request.query_keys`는 키 이름 형식만 / 영향: api-spec 2-2
5. **Argus 전용 코드값은 외부 출처에서 거부** — `EXPORT`·`UNMASK` → `INVALID_ACTION`, `ACCESS_LOG` → `INVALID_CATEGORY`. api-spec 2-3의 "(Argus 전용)" 표기를 수신 검증으로 구체화 / 영향: api-spec 2-3·2-5
6. **요청 단위 오류 코드 세분** (spec 1-6은 예시 1개뿐): 401 `UNKNOWN_SOURCE`·`INVALID_TIMESTAMP`·`INVALID_SIGNATURE` / 400 `MALFORMED_JSON`·`MISSING_EVENTS` / 413 `PAYLOAD_TOO_LARGE`·`TOO_MANY_EVENTS`. 401을 원인별로 나눈 이유: relay 알림에서 키 문제와 시각 오차를 구분해야 사람이 고칠 수 있음(출처 목록은 비밀이 아님) / 영향: api-spec 1-5·1-6
7. **취급자 동기화 API(②)의 Argus 수신 쪽 → M2 초반으로** — M1 크기 조절. HMAC·응답 형식은 M1 것을 재사용 / 영향: CLAUDE.md 6절 M1·M2 범위
8. **Trivy config는 docker-compose 파일을 인식하지 않음**(0.74.0으로 확인, "Detected config files num=1" = Dockerfile만) — architecture 11절 미결 해소 / 영향: architecture 8-1 문구에서 compose 제외

**미결·이슈**
- **(사용자 조치) GitHub 웹에서 PR을 머지해 생긴 머지 커밋에 개인 이메일이 작성자로 기록됨**(#1~#4). M0의 "공개 레포 커밋에 개인 이메일 노출 방지" 결정이 웹 머지에는 적용되지 않았음. GitHub 설정 → Emails → "Keep my email addresses private"로 이후 머지를 막는다. 이미 push된 이력은 main 이력을 다시 써야 해서 되돌리지 않음(공개된 사실로 간주)
- **로컬 HMAC 키가 작업 대화에 노출되어 교체함** — `.env` 확인 시 마스킹 필터가 `ARGUS_INGEST_SECRET_PLATFORM`을 놓침. 레포에는 들어가지 않았고, 사용처(argus-api)만 있던 시점이라 새 값으로 교체. 교훈: `.env` 확인은 값을 전부 가리는 방식(`sed -E 's/=.+/=<set>/'`)으로만 한다
- (보안성 검토 후보) 소유자는 `access_log`를 TRUNCATE·DELETE할 수 있음 — 트리거는 UPDATE만 막음(설계대로). `BEFORE TRUNCATE` 트리거 추가 검토
- (보안성 검토 후보) 앱 계정의 직접 INSERT 차단(SECURITY DEFINER append) — 위 기각 대안 참고
- **(보안성 검토 후보) 해시체인만으로는 못 잡는 경우 두 가지**
  - ① 맨 끝 레코드들을 지우면 남은 체인은 그대로 이어져 있어 검증을 통과함
  - ② DB 권한자가 전체 체인을 처음부터 다시 계산해 덮어쓰면 탐지 불가
  - 대응 후보: 체인 머리(최신 hash·id)를 DB 밖(WORM 저장소, 외부 로그 등)에 주기적으로 기록하는 **외부 앵커링**. 상용 솔루션의 WORM 백업(architecture 5절 INFOSAFER)과 같은 취지
  - 부분적 교차 확인: `detection_batch_run.to_access_log_id`보다 원장 최대 id가 작으면 끝부분 삭제 흔적(단 같은 DB라 권한자는 함께 고칠 수 있음)
- HMAC 신·구 키 병행(api-spec 1-2 #4)은 미구현 — Skeleton은 출처당 키 1개
- ② 수신 시 중복 판정: `handler` 테이블에 event_id 저장 칸이 없어 `last_event_at` 기준으로만 가능 — M2에서 정리
- CLAUDE.md 6절 M1 완료 기준에 Trivy config 미반영(지난 세션부터 이월, Cowork 판단)
- platform-api setuptools `>=75` — Dependabot PR 대기, 안 오면 M2에서 맞춤

**다음 할 일**
- M1 PR 머지 → Cowork "구현" 방에서 진행기록·설계 변경(위 1~8) 동기화
- M2: ② 취급자 동기화 수신(Argus) → 플랫폼 `operator`·`member`·`outbox` + 시드, 관리자 로그인, CSV export, Agent 미들웨어, relay

---

## 2026-09-29 — 마일스톤 M0 마무리 (PR #1 머지, 아키텍처 설계서 사본 갱신)

**한 일**
- PR #1(M0 기반 구성) 머지 확인, 로컬 main 최신화, 병합된 feature 브랜치 삭제
- Cowork "구현" 방에서 개정한 아키텍처 설계서 원본의 사본을 `docs/architecture.md`에 반영하고, `docs/README.md`의 사본 기준일을 갱신 → 별도 `docs:` PR로 올림
- Dependabot 첫 실행으로 PR 2건 생성 확인: #2 `actions/checkout` 4→7, `actions/setup-python` 버전 상향 / #3 argus-api `setuptools>=84`. 둘 다 CI 통과

**결정사항**
- 설계 문서 사본 갱신은 코드 PR과 섞지 않고 **별도 `docs:` PR**로 반영. 변경 이력에서 "기준이 언제 바뀌었나"가 분리되어 보이게 하기 위함

**설계 변경**
- **architecture 8-5 개정 (Cowork, 2026-09-28)**: Trivy를 성격에 따라 둘로 나눔
  - **config 스캔**(Dockerfile 등 설정 파일, PR 단계)은 **첫 Dockerfile이 추가되는 PR(M1)**에 도입
  - **image 스캔**(빌드 산출물, main 빌드 단계)은 기존대로 "Skeleton 로컬 동작 후"
  - 원칙 신설: "점검 도구는 점검 대상이 생기는 PR에서 함께 도입한다"
  - 8-1에 "흐름도는 목표 상태, 도입 시점은 8-5를 따른다" 명시, 8-6에 "Actions 커밋 SHA 고정" 과제 추가
  - 영향: M0 로그의 "Trivy는 이미지 빌드 단계에서 config·이미지 함께 추가" 결정을 **대체**한다. **M1 범위에 CI의 Trivy config + 도커 빌드 확인 job이 추가됨**

**미결·이슈**
- CLAUDE.md 6절 M1 완료 기준에는 Trivy config가 없음. 개정된 architecture 8-5를 기준으로 M1에 포함한다(CLAUDE.md 갱신 여부는 Cowork에서 판단)
- Trivy config의 docker-compose 파일 지원 여부 — M1에서 확인(architecture 11절 미결)
- Dependabot PR #3는 argus-api만 대상 — platform-api의 같은 갱신이 별도 PR로 오는지 확인 필요

**다음 할 일**
- `docs:` PR 머지 → 새 세션에서 M1 착수

---

## 2026-09-28 — 마일스톤 M0 (레포·기반: 공개 레포, DB 2개 compose, CI, Dependabot)

**한 일**
- 로컬 개발 환경 구성: Docker Desktop이 "Virtualization support not detected"로 기동 실패 → Docker 로그로 원인 확인(`Virtual Machine Platform not enabled`). BIOS 가상화는 이미 켜져 있었고(HVCI가 하이퍼바이저 위에서 동작 중), Windows 기능 "가상 머신 플랫폼"을 켜고 재부팅해 해결
- 공개 레포 `Salgoo26/argus` 생성. 첫 커밋(설계 문서 사본·지침·`.gitattributes`·`.gitignore`)만 main에 직접 올리고, 이후는 `feat/m0-foundation` → PR로 병합
- `.gitattributes`(LF 고정), `.gitignore`(`.env*` 제외, `.env.example`만 허용), `.env.example`(더미 값), README(가상 데이터 명시, Windows 로컬 실행법)
- `infra/docker-compose.yml`: `platform-db`, `argus-db`(PostgreSQL 16 각각, 별도 볼륨, healthcheck)
- `apps/argus-api`, `apps/platform-api` 최소 뼈대: `pyproject.toml`(ruff·pytest 설정) + 스모크 테스트(패키지 import, DB 연결)
- `.github/workflows/ci.yml`: ruff(check·format) + pytest(Postgres 16 서비스 컨테이너, 앱별 matrix) + gitleaks(전체 커밋 이력)
- `.github/dependabot.yml`: github-actions, pip(두 API)
- 검증: compose로 DB 2개 healthy, 컨테이너 안에서 ruff·pytest 통과(DB 연결 테스트 포함), 플랫폼 네트워크에서 argus-db 접근 불가 확인, gitleaks 로컬 스캔 누출 없음

**결정사항**
- **Trivy는 M0에서 제외**, 앱 이미지가 생기는 "Skeleton 로컬 동작 후" 단계에서 config·이미지 스캔을 함께 추가. 근거: architecture 8-5 시점표. 스캔할 Dockerfile·이미지가 아직 없음
- **보안 점검의 두 층위 구분**: 사고를 막는 가드레일(gitleaks, push protection, Dependabot, CodeQL report-only)은 첫날부터. 결과물을 평가하는 점검(Trivy 이미지, DAST, 보안성 검토)은 결과물이 생긴 뒤. 공개 레포에 시크릿이 한 번 push되면 되돌릴 수 없기 때문
- **Docker 네트워크 분리**: `platform-net` / `argus-net`. 플랫폼 쪽 컨테이너는 argus-db를 이름 해석조차 못 함. M2에서 `platform-relay`만 두 네트워크에 붙인다. "연결은 API 두 개뿐"(architecture 2절)을 네트워크 계층에서도 강제하는 구현 세부
- DB 포트는 `127.0.0.1`에만 바인딩(호스트 외부 노출 차단). 호스트 포트는 기본 5432와의 충돌을 피해 15432/15433
- compose 파일은 `infra/`에 두고, `.env`의 `COMPOSE_FILE`로 레포 루트에서 `docker compose up` 가능하게 함. 필수 변수는 `${VAR:?}`로 누락 시 기동 거부
- gitleaks는 서드파티 Action(`gitleaks-action`) 대신 **공식 Docker 이미지를 버전 고정(v8.30.1)으로 직접 실행**. Action에 토큰 권한을 줄 필요가 없고 공급망 노출이 줄어듦
- 워크플로 권한 최소화(`permissions: contents: read`), `actions/checkout`은 `persist-credentials: false`
- ruff 규칙에 `S`(bandit 보안 규칙) 포함. 테스트의 `assert`(S101)만 예외
- 커밋 작성자 이메일은 개인 주소 대신 **GitHub noreply 주소**를 레포 로컬 설정으로 사용(공개 레포 커밋에 개인 이메일 노출 방지)
- 의존성 버전 고정: pytest 9.1.1, ruff 0.16.9, psycopg 3.3.6. 이후 갱신은 Dependabot PR로
- **기록 운영 방식**: 구현 로그는 세션마다 Claude Code가 작성하되 로그만 따로 push하지 않고 해당 feature PR에 함께 싣는다(코드 변경 없는 세션의 로그는 다음 PR에). Cowork "구현" 방은 로컬 레포 폴더의 로그를 읽고, 동기화는 마일스톤 완료 시·설계 변경 시에만 한다. 실무의 커밋 메시지·PR 설명·ADR 역할을 로그 하나가 겸하는 구조

**기각한 대안**
- `gitleaks-action` 사용: 편하지만 서드파티 Action에 `GITHUB_TOKEN`을 넘겨야 함 → 이미지 직접 실행으로 대체
- Dependabot에 npm·docker 생태계를 미리 등록: 해당 디렉터리가 없으면 Dependabot이 오류를 냄 → 앱이 생길 때 추가
- 테스트 0개로 CI 구성: pytest가 exit 5를 내며 실패 → 스모크 테스트를 두고, 이 기회에 CI의 DB 서비스 컨테이너 연결까지 미리 검증

**설계 변경**
- 없음
- 문서 간 불일치 1건(원본 정리 요청): architecture **8-1** 흐름도는 PR CI에 "Trivy config"를 포함하지만, **8-5** 시점표는 Trivy를 "Skeleton 로컬 동작 후"로 둠. 구현은 8-5를 따랐음

**미결·이슈**
- GitHub 설정(사용자 작업): CodeQL default setup, secret scanning, push protection 활성화
- (보안성 검토 단계 후보) GitHub Actions를 태그(`@v4`) 대신 커밋 SHA로 고정 — 공급망 공격 대비
- 해시체인 정규화 규칙 — M1에서 확정

**다음 할 일**
- M1: Argus [S] 테이블 Alembic 마이그레이션, 수집 API(HMAC·건별 판정·멱등), 해시체인 append 함수, append-only 트리거·DB 권한(`argus_app` 로그인 계정 분리), `/healthz`, argus-api Dockerfile

---

## 2026-09-23 — 착수 전 (설계 단계 종료)

**한 일**
- 기획·설계 산출물 완료, 이 레포로 인수인계 (CLAUDE.md + docs/ 사본 6종)

**결정사항**
- 구현은 Claude Code, 기록·설계 변경 판단은 Cowork "구현" 방으로 역할 분담
- Walking Skeleton 마일스톤 M0~M6 정의 (CLAUDE.md 6절)
- platform outbox relay는 platform-api와 같은 이미지를 쓰는 별도 컨테이너(`platform-relay`)로 실행

**설계 변경**
- 없음

**미결·이슈**
- 해시체인 정규화(canonical) 규칙 세부 — M1에서 확정해 이 로그에 기록
- A5(취급자) Argus 초기 계정 발급 방식 — Skeleton은 시드 계정

**다음 할 일**
- M0 착수: 레포 생성, docker-compose, CI
