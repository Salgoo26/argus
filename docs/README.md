# 설계 문서

Argus v0.1의 설계 문서입니다. 기획에서 구현까지 아래 순서로 작성했고, 각 문서에는 결정과 함께 검토했다가 택하지 않은 대안을 남겼습니다.

| 문서 | 내용 |
|---|---|
| [requirements.md](requirements.md) | 요구사항 — 감시 대상 플랫폼과 Argus의 기능·비기능 요구, 범위, 근거 법령 |
| [policy.md](policy.md) | 정책 — 탐지 룰, 소명·상태 전이, 알림, 권한, 마스킹, 보관·파기 |
| [actor-flows.md](actor-flows.md) | 액터별 흐름 — 정보보호 담당자·개인정보취급자·플랫폼 관리자·고객의 화면 흐름 |
| [architecture.md](architecture.md) | 아키텍처 — 시스템 구성, 접속기록 수집(화면 경유·DB 직접), 보안 설계, 알려진 한계와 다음 버전 |
| [api-spec.md](api-spec.md) | API 명세 — 시스템 간 API(수집·동기화·게이트웨이)와 화면 API |
| [db-schema.md](db-schema.md) | DB 스키마 — 두 DB의 테이블, 룰 조건식, 탐지 배치 흐름 |
| [run.md](run.md) | 실행 안내 — 로컬 실행, 시연 순서, DB 툴 접속, 테스트 |

구현 규칙은 레포 루트의 [CLAUDE.md](../CLAUDE.md)에 있습니다.
