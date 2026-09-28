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

> 작성 중 — Walking Skeleton 진행에 따라 채운다.
