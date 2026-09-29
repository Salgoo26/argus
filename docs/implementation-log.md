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
