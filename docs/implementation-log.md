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
