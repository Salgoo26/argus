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

## 2026-10-10 (4) — v0.1 보강 PR 4: 알림 — 화면 알림·소명 기한·웹 푸시 (설계 F)

**한 일**
- **F-1 받는 사람**: 탐지건 생성(상만)→담당자 전원 / 소명 요청(자동·수동·재요청)→해당 취급자 / 기한 임박·초과→취급자+담당자 전원 / 소명 제출→요청한 담당자(자동 요청이면 담당자 전원). 업무와 같은 트랜잭션에서 만든다
- **F-2 화면 알림**: `notification`(마이그레이션 0015 — 본문 없음, 같은 (사람·종류·탐지건·차수)는 한 번만), `GET /api/notifications`·읽음 표시, 헤더 "알림" 배지 + 목록(심각도 색·기호), 30초마다 확인
- **F-3 소명 기한**: `explanation.due_at` = 요청 시각 + `setting.explanation_due_days`(7일), 재요청은 다시 7일. worker가 순찰마다 임박(24시간 이하)·초과 판정, 상태는 바꾸지 않음. 목록·상세에 기한, 보고서에 기한 초과 건수
- **F-4 웹 푸시**: VAPID(RFC 8292)·aes128gcm 암호화(RFC 8291)를 `cryptography`(platform-api와 같은 50.0.2)·PyJWT로 직접 구현, `push_subscription`(0016)·`notification.push_pending`, 구독 API(`/api/push/config`·`subscriptions`·`unsubscribe`), 서비스 워커 `public/sw.js`, 알림 목록의 "알림 받기". 키 생성 `python -m app.scripts.vapid_keys`, 키가 없으면 웹 푸시만 꺼짐
- 테스트: argus `test_notifications.py` 17개(누가 받는가·기한·API·원장 미기록·행에 본문 없음), `test_webpush.py` 27개(브라우저 쪽 RFC 8291 복호화로 암호화 검증, VAPID 서명, 구독 주소 허용 목록·SSRF 차단, 페이로드에 개인정보 없음, 모의 발송·410 삭제·키 없음·비활성 계정·발송 실패, 구독 API·로그아웃·퇴직 시 삭제), `test_reports_api.py::test_overdue_explanations_are_counted` / E2E 2개
- 결과: argus-api 468 passed, argus-web lint·typecheck, E2E 19 passed

**결정사항**
- 알림 API는 **자체 접속기록 제외** — 정보주체 처리가 없고 30초 확인이 원장을 채운다. 알림이 가리키는 탐지건을 열면 READ로 남는다
- 알림 FK는 `ON DELETE CASCADE` — 파생 데이터이고 운영에서는 탐지건·계정을 지우지 않음(테스트 정리를 위해)
- 기한을 넘긴 뒤 처음 판정하면 **초과만**(임박 건너뜀)
- 웹 푸시 라이브러리(pywebpush)를 쓰지 않고 직접 구현 — 새 의존성(requests·aiohttp 등)을 늘리지 않고, 이미 검증해 쓰는 cryptography만으로. 정확성은 테스트에서 **구독자 입장의 RFC 8291 복호화**로 확인
- **구독 주소는 브라우저 푸시 서비스 https 호스트만**(FCM·Mozilla·Apple·WNS) — 서버가 사용자가 넣은 임의 주소로 POST하는 SSRF 통로가 되지 않게. 발송 직전에도 다시 확인
- 발송 시점: 상태 변경(소명 요청) 직후 응답 뒤 백그라운드 + worker 순찰마다. 행 잠금(SKIP LOCKED)으로 중복 발송 방지. **한 번만 시도**(화면 알림이 주 수단), 404·410이면 구독 삭제
- 로그아웃·퇴직(계정 비활성화) 시 구독 삭제. 같은 브라우저에 다른 사람이 로그인해 구독하면 주인이 바뀐다
- VAPID 키는 `.env`(비우면 꺼짐), 하나만 있거나 짝이 안 맞으면 **기동 거부**. `init-env.sh`는 VAPID를 채우지 않는다(형식이 hex가 아님) — 필요하면 키 생성 명령으로
- argus-web Dockerfile에 `public/` 복사 추가(standalone 출력에 빠짐)

**설계 변경**
- 무엇을: 설계 F 전체. 알림 API의 자체 접속기록 제외, 구독 주소 허용 목록, 푸시 1회 시도 원칙을 추가로 정함
- 영향 문서: policy(알림·소명 기한 7일·기한 초과는 상태 불변), api-spec(알림·푸시 API, 자체 기록 제외 사유), db-schema(`notification`·`push_subscription`·`explanation.due_at`·setting `explanation_due_days`), architecture(웹 푸시 발송 경로, 외부 푸시 서비스로의 아웃바운드 — 운영 방화벽), actor-flows(F-05·F-06 알림 확인), requirements(알림 — 이메일은 v0.2 유지), CLAUDE.md 5절·6절(VAPID 시크릿)

**미결·이슈**
- 실제 브라우저·푸시 서비스로의 종단 발송은 확인하지 않음(로컬·CI에 VAPID 키 없음) — 암호화·서명은 테스트로, 발송은 모의 객체로
- 웹 푸시는 브라우저 제조사 서버를 거친다 — 내용은 암호화되지만 "언제 알림이 갔는지"는 그쪽에 남는다(내용에 개인정보 없음)
- 로그아웃 시 브라우저 쪽 구독(서비스 워커)은 그대로 남는다 — 서버가 지워 더는 보내지 않음

**다음 할 일**
- 사용자 머지(#60 → #61 → #62 → #63), Cowork에서 설계 원본 반영

---

## 2026-10-10 (3) — v0.1 보강 PR 3: 2티어 회원번호 추출 (설계 G-1)

**한 일**
- 게이트웨이가 결과 열 설명(RowDescription)의 **테이블 OID·열 번호**로 회원을 가리키는 열을 찾고, 결과 행(DataRow)에서 **그 열의 값만** 읽어 `subject_ids`로 보낸다. SQL은 해석하지 않음
- 회원 열 목록 `catalog.MEMBER_COLUMNS`(7개) + OID 조회(게이트웨이 자체 연결, 연결마다 처음 한 번)
- Bind의 **결과 형식 코드**를 읽어 텍스트/바이너리(int2·int4·int8) 판단. 이름 있는 문장 재사용 시 기억한 열 설명 사용
- 찾으면 고유 회원번호(1,000 상한·`truncated`, `count`=전체)·`subject_unresolved=false`. 없거나 해석 실패면 기존처럼 미특정 + 건수
- 테스트: db-gateway `test_subjects.py` — `test_member_id_column_is_extracted_and_other_values_are_not_kept`(이름·이메일이 원장·원문 저장소에 없음), `test_alias_and_join_use_the_column_origin`, `test_repeated_members_are_counted_once`, `test_null_member_references_are_skipped`, `test_expression_is_not_a_member_column`, `test_query_without_member_column_stays_unresolved`, `test_empty_result_with_member_column_is_resolved_to_nobody`, `test_extended_query_text_format`, `test_extended_query_binary_format`, `test_reused_prepared_statement_keeps_extracting`, `test_more_than_1000_members_are_truncated_with_full_count`, `test_decode_subject`, `test_undecodable_value_raises` / `test_table_category.py::test_every_personal_table_with_a_member_reference_is_in_member_columns` / 변경 `test_statements.py::test_literal_query_reaches_argus_normalized_only`, E2E `test_db_gateway.py`(회원번호 = 조회한 행, 이름 값 없음, 배송지 조회는 미특정)
- 결과: db-gateway 196 passed, argus-api lint, E2E 17 passed

**결정사항**
- **값 해석에 하나라도 실패하면 그 실행 전체를 미특정**으로 — 일부 회원번호만 적으면 "이 사람들만 봤다"로 오해될 수 있음
- 회원 열이 결과에 있으나 0행이면 "아무도 처리하지 않음"(`ids=[]`, `count=0`, `unresolved=false`)
- 결과를 돌려주지 않는 실행(UPDATE·DELETE without RETURNING)·COPY는 지금처럼 미특정 + 영향 행 수
- 카탈로그 조회 실패 시 사용자 질의는 막지 않고 미특정으로 남긴다(기록 자체는 fail-closed 유지)
- 회원 열 OID는 `public` 스키마의 실제 테이블만(`relkind r·p`) — 사용자가 만든 복사 테이블·뷰 결과(OID가 다름)는 미특정
- 고유 회원번호 집합은 실행이 끝날 때까지 메모리에 — 수백만 행 결과의 메모리 상한은 두지 않음(관찰)

**설계 변경**
- 무엇을: 2티어 구현 순서 ②(보류)를 SQL 재현 방식이 아닌 **결과 열 기반 추출**로 구현(설계 G-1)
- 영향 문서: architecture 3-4 "정보주체 기록 방식"(② 보류 → 결과 열 추출, PITR은 v0.2 문서만 — G-2), api-spec 2-2 "access_path=DB 기록 규칙"(`subject_unresolved=false`일 때 ids·truncated), CLAUDE.md 6절 8행(②), README 구현 현황(📋 → ✅)

**미결·이슈**
- DBeaver(pgjdbc) 실측은 하지 않음 — psycopg의 확장 질의·바이너리·이름 있는 문장 재사용 테스트로 대신(2026-10-06 실측에서 pgjdbc가 같은 메시지 흐름을 쓰는 것은 확인됨)
- 회원 열이 없는 조회(예: `SELECT name, email FROM member`)는 여전히 미특정 — 시점 복구(PITR)는 v0.2

**다음 할 일**
- PR 4(알림)

---

## 2026-10-10 (2) — v0.1 보강 PR 2: 플랫폼 관리자 검색 (설계 E)

**한 일**
- `POST /admin/members/search`(이름·이메일·연락처 부분 일치, 상태, 가입일), `POST /admin/orders/search`(주문번호·회원번호·상태·주문일), `POST /admin/inquiries/search`(상태·회원번호·작성일·제목 부분 일치)
- 플랫폼 Agent에 `record_query_keys` — 본문 검색 조건의 키 이름만 기록. 정보주체 = 결과로 보인 회원 PK 전부, 0건도 READ(정보주체 0명)
- 관리자 화면(회원·주문·문의)에 검색 폼 — 첫 진입도 같은 검색 API(조건 없음)
- 테스트: platform `test_admin_search.py` — `test_member_search_by_name_email_phone_status_and_join_date`, `test_member_search_records_every_shown_member_and_key_names_only`(검색어 값이 기록에 없음 단언), `test_empty_result_is_still_recorded`, `test_like_wildcards_are_literal`, `test_invalid_member_search_is_400`, `test_search_requires_login`, `test_order_search_by_number_member_status_and_date`, `test_inquiry_search_by_status_member_date_and_title` / E2E `test_platform_member_search_records_shown_members_not_search_values` + `scripts/e2e.sh` 서버 로그 검색어 검사
- 결과: platform-api 253 passed, platform-web lint·typecheck, E2E 17 passed

**결정사항**
- 기존 `GET /admin/{members,orders,inquiries}` 목록은 **남긴다** — 조건이 페이지 번호뿐이라 원칙 2(검색 값 비노출)에 걸리지 않고, 기존 테스트·기준선 시드의 경로와 맞물려 있음. 화면은 첫 진입부터 검색 API만 쓴다
- 연락처는 하이픈을 지우고 숫자끼리 부분 일치(입력·저장 표기 차이 흡수). 입력은 숫자로 시작하는 `[0-9-]`만
- 부분 일치는 `autoescape` — 검색어의 `%`·`_`가 와일드카드로 해석되지 않음(테스트로 고정)
- 검색 키 이름은 정렬해 기록(Argus 쪽과 같은 형식)
- 서버 로그 검사는 E2E 스크립트에 고유 검색어(needle) grep으로 — 카드번호 검사와 같은 방식

**설계 변경**
- 무엇을: 플랫폼 관리자 검색 API 3개, Agent의 본문 검색 키 기록
- 영향 문서: api-spec(플랫폼 관리자 API — 내부 API지만 접속기록 규칙 2-2 "query_keys"에 본문 키 포함 명시), requirements PLT-11·16·17(검색), actor-flows F-02·F-03(검색 단계), architecture 3-2(검색 조건 기록)

**미결·이슈**
- 주문 상태는 지금 `PAID`뿐이라 상태 조건은 사실상 형식만 있음

**다음 할 일**
- PR 3(2티어 회원번호 추출)

---

## 2026-10-10 — v0.1 보강 PR 1: 로그아웃 기록·보고서 소명 내용·Argus 검색·취급자 내 접속기록

> 배경: 10/09 갭 분석 실측(증적은 `_gap/`, 레포 밖) → 사용자 결정(10/10 00:15)으로 기능 동결을 풀고
> v0.1 보강 설계(`_gap/v01_보강설계.md`, Cowork 작성 — 원본에 합칠 예정)를 구현한다. PR 4개를 쌓아서 올리고
> 머지는 사용자가 순서대로 한다. 이 항목은 PR 1(설계 A·B·C·D)

**한 일**
- **A 로그아웃 기록**(갭 A6, 안내서 FAQ 147): 수행업무 `LOGOUT` 추가 — 플랫폼 Agent·Argus 자체 기록·수집 검증·원장 CHECK(마이그레이션 0014)·검색/룰 빌더 선택지. 플랫폼·Argus 로그아웃을 `@access_log(LOGOUT, NONE)`으로. 로그인한 사용자만 기록(쿠키 없음·만료는 행위자가 없어 미기록, 응답은 같은 204). 행위자 확인 때 재발급한 세션 쿠키는 로그아웃 응답에 싣지 않음
- **B 보고서 소명 내용**: 탐지건 항목에 마지막 차수 소명 요지(200자)·검토 결과/의견·관련 티켓·첨부 개수, 처리 담당자·일시, 요청 취소 사유. 소명 작성 칸에 "개인정보는 적지 말고 회원번호·주문번호로" 안내
- **C-1 탐지건 검색**: `GET /api/detections` → `POST /api/detections/search`(기간·룰·취급자·심각도·상태·경로, 최신순/심각도순). 화면 기본 기간 = 담당자 이번 달, 취급자 제한 없음
- **C-2 접속기록 검색**: 접속지(IP 정확히/앞부분 일치)·데이터 유형·결과 추가
- **C-3 룰 목록**: 이름(부분 일치)·적용 경로·심각도·유형 필터
- **D 취급자 "내 접속기록"**: 같은 검색 API·화면. 행위자를 서버가 명부의 본인으로 고정, 남의 아이디·Argus 자체 기록 요청은 403. 취급자 메뉴(내 소명 요청 / 내 접속기록)
- 테스트(추가·변경): platform `test_agent.py::test_logout_is_recorded`, `::test_unauthenticated_logout_is_not_recorded` / argus `test_self_access_log.py::test_logout_is_recorded`, `::test_unauthenticated_logout_is_not_recorded`, `test_ingest.py::test_logout_without_subject_is_accepted`, `test_reports_api.py::test_cases_carry_the_latest_explanation_and_who_handled_it`, `::test_dismissed_case_carries_the_cancel_reason`, `::test_unsubmitted_draft_attachments_are_not_counted`, `test_detections_api.py::test_search_by_period_rule_actor_and_severity`, `::test_search_sorts_latest_or_by_severity`, `::test_invalid_search_is_400`, `::test_handler_search_cannot_reach_others_by_actor`, `::test_search_values_are_not_recorded`, `test_access_log_search.py::test_ip_category_and_result_filters`, `::test_invalid_new_conditions_are_400`, `::test_handler_sees_only_own_platform_logs`, `::test_handler_cannot_search_others_or_argus_logs`, `::test_handler_self_search_is_logged`, `::test_handler_period_and_filters_still_apply`, `test_rules_api.py::test_list_filters_by_name_path_severity_and_type` / E2E `test_v01_reinforcement.py` 3개
- 결과: argus-api 423 passed, platform-api 241 passed, argus-web lint·typecheck, E2E 16 passed

**결정사항**
- 탐지건 목록은 GET을 남기지 않고 POST 검색으로 **대체**(설계 【기본값】 "통일") — 경로가 둘이면 접근 통제·기록 규칙을 두 번 지켜야 함. E2E·테스트 호출도 함께 바꿈
- 탐지건 기간을 비우면 서버는 **전체 기간**, "이번 달" 기본값은 담당자 화면에만. 취급자 화면은 기간 기본값 없음 — 지난달에 받은 미제출 요청이 안 보이면 안 되므로
- 처리 담당자 = 상태 이력에서 **담당자(OFFICER)가 마지막으로 상태를 바꾼 기록**(시스템 자동 요청·취급자 제출은 제외)
- 보고서의 제출 전 초안은 내용·첨부 개수를 싣지 않음 — 담당자 화면(제출된 차수의 첨부만)과 같은 기준
- 취급자 "내 접속기록"에서 남의 아이디·`source=ARGUS`는 **조용히 바꾸지 않고 403** — 시도가 자체 기록에 FAILURE로 남아 점검 대상이 된다. 본인 아이디를 적은 요청은 허용
- IP 앞부분 일치: 완전한 주소면 `inet` 비교, 아니면 표준 표기의 문자열 앞부분 일치. 입력은 `[0-9A-Fa-f:.]`만 받아 LIKE 와일드카드가 들어올 수 없게
- 룰 목록 필터는 URL 쿼리 — 룰 정의는 개인정보가 아니라 원칙 2의 대상이 아님

**설계 변경**
- 무엇을: 위 보강 A~D (설계 기준 `_gap/v01_보강설계.md`). 수행업무 코드 `LOGOUT`, `POST /api/detections/search`, 접속기록 검색 조건 3개, 취급자의 접속기록 검색 허용(본인 고정), 보고서 탐지건 항목 확장
- 왜: 갭 분석(10/09) — 안내서 FAQ 147(로그아웃), 실무 결재문서 수준의 보고서, 점검 편의
- 영향 문서: api-spec 2-3(수행업무 코드)·2-7(Argus 자체 기록)·탐지건/접속기록/룰 API 절, db-schema access_log CHECK(0014)·보고서 summary 구조, policy 6-2(검색 조건 기록)·6절(취급자 열람), actor-flows F-06(취급자 메뉴), architecture 3-2(로그아웃 제외 문구 삭제), CLAUDE.md 6절

**미결·이슈**
- 명부와 연결되지 않은 HANDLER는 로그인 자체가 막혀(`block_reason`) "빈 결과 + 안내" 분기는 실제로 닿지 않는다 — 방어용으로만 두고 API 테스트는 없음
- 보고서 소명 요지는 자유 입력 그대로(자동 마스킹 없음) — 설계대로 한계로 기록

**다음 할 일**
- PR 2(플랫폼 관리자 검색) → PR 3(2티어 회원번호 추출) → PR 4(알림)

---

## 2026-10-08 — 가상 PG 결제창의 카드번호 형식 검사 제거

**한 일**
- PR #54~#57 병합 확인(사용자)
- 가상 PG 결제창에서 카드번호·유효기간 **형식 검사(자릿수·Luhn·MM/YY·만료) 제거** — 입력칸과 "가상 결제 — 실제 카드번호 입력 금지" 안내는 그대로, 무엇을 입력해도 승인. 입력값은 여전히 서버로 보내지 않고 승인을 누르면 지운다
- 관련 주석·테스트 설명의 "Luhn" 문구 정리 (서버 쪽 동작·카드번호 부재 테스트는 변경 없음)

**결정사항**
- 형식 검증은 이 데모의 목적이 아니다(사용자 결정) — 보여 주려는 것은 "카드번호가 쇼핑몰 서버에 오지 않는 구조"

**설계 변경**
- 무엇을: 결제창 카드번호 처리 "브라우저 안에서만 형식 확인(자릿수·Luhn)" → **형식 확인 없이 입력만**(서버 미전송은 그대로)
- 왜: 검증 자체가 목적이 아니고, 입력 실수로 데모 흐름이 막히는 것만 늘어남
- 영향 문서: CLAUDE.md 6절 7-4 ③, requirements PLT-05, actor-flows F-01 #4, db-schema 4절 "플랫폼 보강"의 카드 항목 (Cowork 반영 필요)

**미결·이슈**
- 없음

**다음 할 일**
- 기능 동결(10/08) 이후 점검·검수, Cowork 동기화(7-4 완료·위 설계 변경)
- **신규 기능 개발 종료(사용자 확인, #58 병합 후).** 점검·검수 진행 방식(사용자 결정): Cowork에서 점검 체크리스트(근거 조항·확인 방법) 작성 → Claude Code에서 항목별 실측·증적 수집 → Cowork에서 판정·보고서 → Claude Code에서 부적합 조치·재확인. 이어서 Cowork에서 다듬기·포트폴리오 작성
  - 기각: Claude Code에서 점검까지 일괄 수행 — 구현자가 자기 코드를 점검하는 맹점, 법령 대조·판정 결과물은 설계 원본(Cowork)과 함께 관리돼야 함

---

## 2026-10-07 (2) — 기능 레이어 7-4 플랫폼 보강 (기능 동결 전 마지막) — PR ①②③

**한 일**
- 문서 PR #54(Cowork 사본 — 7-4 플랫폼 보강 설계) 생성
- **PR ① 회원가입·마이페이지 정보** (브랜치 `feat/platform-member-profile`)
  - 가입 필수 항목 = 이메일·비밀번호·이름·**휴대폰**. 가입 요청에서 주소 칸 제거. 생년월일·성별은 칸 자체가 없다 — 보내도 모델에서 버려지고 `member`에 컬럼이 없음을 테스트로 고정
  - 마이페이지 정정: 이름·휴대폰(필수 — 비우거나 형식이 틀리면 400, 기존 값 유지). 이메일은 정정 칸이 없고 보내도 무시(로그인 아이디, policy 4-3). 화면에는 이메일을 읽기 전용으로 표시
  - 마이그레이션 0006: 개인정보 수집·이용 동의(필수) 문안을 **v2로 올림** — 수집 항목에 휴대전화번호(필수)·배송지, 목적에 배송·배송 연락. 처리방침 자리표시 문안도 같이(시행일 10/07, v2)
  - e2e: 가입 요청에 휴대폰, 휴대폰 없는 가입 거부·정정(이메일 무시) 확인 추가
  - 테스트: platform-api 207개 통과(+11 — 휴대폰 없음·빈 값·null 가입 거부, 휴대폰 지우기·형식 오류 정정 거부 4종, 이메일 정정 무시, 생년월일·성별·주소 미수집, 동의 문안 v2·버전 기록), ruff 통과 / platform-web eslint·typecheck 통과

- **PR ② 배송지 관리** (브랜치 `feat/platform-shipping-address`, ① 위에 쌓음)
  - 마이그레이션 0007 `shipping_address`: 배송지 이름·받는 사람·연락처·우편번호·주소·상세주소·기본 여부. **회원당 기본 1개는 부분 유니크 인덱스**로 DB에서도 막음. 기존 `member.address`는 그 회원의 **기본 배송지로 옮긴 뒤 컬럼 삭제**(받는 사람·연락처 = 회원 이름·휴대폰). 되돌리기(downgrade)는 기본 배송지를 회원 주소로 복원
  - API `/shop/me/addresses`: 목록·추가·수정·기본 지정·삭제. 첫 배송지는 자동 기본, 기본을 지우면 남은 것 중 가장 먼저 등록한 것이 기본. 회원당 최대 10개. 모든 조회·변경에 `member_id` 조건(남의 번호는 404). 쓰기는 회원 행을 잠그고(상한·기본 1개를 동시 요청에서도 지킴)
  - 마이페이지 "내 정보"에서 주소 칸 제거 → "배송지" 카드. 관리자 회원 상세·회원 CSV에서도 주소 제거(배송지 마스킹·전체 보기는 v0.2 — 설계대로 회원 상세에 싣지 않음)
  - 탈퇴: 배송지 즉시 삭제(보존 대상 아님 — 4-1)
  - **게이트웨이 분류**: `TABLE_CATEGORY`에 `shipping_address → MEMBER_BASIC`. 분류표 자체를 테스트로 고정(`test_table_category.py` — 플랫폼 개인정보 테이블 목록과 같아야 함) + e2e에서 게이트웨이로 `shipping_address`를 조회해 원장에 `MEMBER_BASIC`으로 도착하는지 확인
  - 시드: 회원마다 기본 배송지 1개(Faker 주소, 우편번호는 실재하지 않는 9로 시작하는 5자리). Faker 호출 순서를 유지해 기존 시드와 같은 이름이 나오게
  - 입력 검증: 휴대폰 정규식 `\d` → `[0-9]`(다른 문자권 숫자 차단 — 우편번호도 같은 방식)
  - 테스트: platform-api 228개 통과(+21 — 첫 배송지 기본·기본 1개·수정해도 기본 유지·기본 삭제 시 승계·상한·남의 배송지 404 3종·입력 오류 10종·고객 행위 outbox 0건·회원 컬럼 제거·**0007 이관/되돌리기**·시드 기본 배송지·탈퇴 시 삭제), db-gateway 176개(+5), ruff 통과 / platform-web eslint·typecheck 통과

- **PR ③ 결제·주문** (브랜치 `feat/platform-checkout`, ② 위에 쌓음)
  - 마이그레이션 0008: `orders`에 배송 정보 스냅샷(받는 사람·연락처·우편번호·주소·상세주소). 배송지를 FK로 가리키지 않고 **복사** — 배송지를 고치거나 지워도 주문 기록 유지. 이전 주문은 비워 둠(지어 넣지 않음)
  - 주문 API: 로그인 필수 + **내 배송지 번호 필수**(남의 것·없는 것은 400 `SHIPPING_ADDRESS_REQUIRED`), 연락처·우편번호 없는 이관 배송지는 400 `SHIPPING_ADDRESS_INCOMPLETE`(마이페이지에서 보완). 카드번호 칸은 여전히 없음
  - **가상 PG 결제창**: 카드사 선택 + 카드번호·유효기간 입력, **브라우저에서만 형식 확인**(15~16자리·Luhn·MM/YY·만료). 카드번호는 결제창 컴포넌트 상태에만 두고(`<form>`·`name` 없음) 승인 요청에는 상품·배송지·카드사만 실음, 확인 통과 즉시 지움. 창 상단에 "가상 결제 — 실제 카드번호 입력 금지"
  - 주문 화면: 비로그인 → `/login?next=/checkout/{id}` → 로그인(또는 가입) 뒤 원래 상품으로. 돌아갈 주소는 같은 출처의 고객 경로만(`safeShopPath` — 외부·`/admin` 거부, 오픈 리다이렉트 방지). 배송지 선택(기본 배송지 미리 선택), 없으면 "마이페이지에서 배송지를 등록해 주세요"
  - **관리자 주문 상세** `GET /admin/orders/{id}` + 화면: 주문·배송 정보·PG 승인 결과. 접속기록 `READ`·`ORDER`, 정보주체 = 그 주문의 회원(탈퇴 주문은 없음), 없는 주문은 FAILURE. 목록의 주문번호에서 링크
  - 탈퇴: 주문별 배송 스냅샷을 PAYMENT_5Y `data.orders[].shipping`으로 옮기고 `orders`의 배송 칸을 비움(4-1, 문의 본문과 같은 원칙). 주문에 안 쓴 배송지는 보존 대상 아님
  - 시드 주문: 주문한 회원의 기본 배송지를 복사(실제 주문 경로와 같게, 난수 소비 순서 유지)
  - 고객 주문 내역·처리방침 자리표시 문안에 배송 정보
  - **카드번호 부재 확인**: 단위 테스트 — 화면이 일부러 카드번호(3가지 표기)·유효기간·CVC를 보내도 **응답·서버 로그(caplog·stdout/err)·플랫폼 DB 전체 테이블·Argus 전송(outbox)** 어디에도 없음, `payment` 컬럼 집합 고정 / E2E `test_checkout.py` — 같은 상황에서 응답·관리자 상세·**Argus 원장**에 없음 / `scripts/e2e.sh` — 실행 뒤 **모든 컨테이너 로그**에서 가상 카드번호 검색, 있으면 실패
  - 테스트: platform-api 239개 통과(+11), ruff 통과 / platform-web eslint·typecheck 통과 / **E2E 13개 통과**(결제 E2E 추가, 로그 검사 통과)

**결정사항**
- 가상 카드번호 예시는 `1234-5678-9012-3452`(Luhn만 통과하는 가상 번호) — 화면 안내·테스트에 같은 값 (10/08 형식 검사 제거로 화면 예시는 삭제, 테스트 값으로만 사용)
- 카드 CVC는 받지 않음(형식 확인에 필요 없음 — 입력칸 최소화)
- 배송지 연락처·우편번호는 API 필수, DB는 NULL 허용 — 옮겨 온 기존 주소에는 우편번호가 없고 휴대폰 없는 기존 회원도 있을 수 있어서(가짜 값을 채우지 않음). 화면은 "연락처 없음"으로 표시
- 배송지 상한 10개, 기본 배송지 해제 기능 없음(다른 배송지를 기본으로 지정해서 바꿈)
- **동의 문안은 고쳐 쓰지 않고 버전을 올린다**: `member_consent.item_version`이 "그때 알린 항목"의 증적이라, 같은 v1의 문안을 바꾸면 기존 동의 기록과 문안이 어긋난다. 기각: v1 문안만 수정(증적 불일치)
- `member.phone`은 DB에서는 NULL 허용 유지, 필수는 API에서 — 이미 가입한 고객 중 휴대폰이 없는 계정이 있을 수 있어(가짜 값을 채울 수 없음). 그런 계정은 마이페이지에서 저장할 때 휴대폰을 입력해야 한다

**설계 변경**
- 없음 (동의 문안 v2는 자리표시 문안의 갱신 — Cowork 정식 문안 작성 시 반영 필요)

**미결·이슈**
- 기존 회원(v1 동의)의 v2 재동의 절차는 없다 — 보안성 검토 이월(수집 항목 변경 시 재동의·고지 방식)

**다음 할 일**
- 사용자 확인(화면) 후 #54~#57 머지 → 기능 동결(10/08) 이후 점검·검수

---

## 2026-10-07 — 기능 레이어 9: 점검 보고서 (마스킹 보고서·경로별 집계) → 신규 기능 개발 종료

**한 일**
- 문서 PR #49·#50(Cowork 사본 — 기능 레이어 8 마무리, 안내서 90쪽 원문 확인·법령 해석 성립) 병합
- **9-1 API (Argus)**
  - 마이그레이션 0013 `inspection_report`(db-schema DDL 그대로, 앱 계정은 조회·추가만 — 점검 증적이라 고치거나 지우지 않음). Skeleton 범위 밖이라 그동안 없던 테이블
  - `POST /api/reports`(담당자 전용): 기간(한국 날짜, 최대 1년) + 범위(전체/화면 경유/DB 직접) → **생성 시점 스냅샷**을 `summary`에 저장(나중에 탐지건 상태가 바뀌어도 보고한 내용 유지). Argus 자체 접속기록 **`EXPORT`** + `context.report_id` + 실린 마스킹 식별값 수. `GET /api/reports`·`/{id}`(READ + report_id)
  - 스냅샷(`app/reports/summary.py`): **점검 수행 증적** — 원장 해시체인 전체 재계산 결과(§8③), 기간 중 탐지 배치 실행·실패(§8②) / **경로별 섹션**(범위가 한 경로면 그 섹션만) — 접속기록(건수·행위자·실패·행위별·데이터 유형별·정보주체 미특정), 탐지건(룰별·심각도별·상태별·에스컬레이션), 소명(요청·제출·승인·반려), 탐지건 목록(심각도 순 최대 50, **마스킹 식별값 앞 3개** + 고유 정보주체 수 → 화면에서 "외 N명", DB는 "미특정 N건"). 감시 대상(플랫폼) 기록만 — Argus 자체 기록은 섹션에 섞지 않음
  - 자체 접속기록 코드값에 `EXPORT` 추가(`UNMASK`는 v0.2)
  - 테스트 7개(경로별 섹션·마스킹·원본 회원번호 미노출, EXPORT 기록·report_id·식별값 수, 단일 경로 범위, 스냅샷 유지, 담당자 전용 403, 잘못된 기간·범위 400, 없는 보고서 404) — argus-api 399개 통과, ruff 통과
- **9-2 화면 (argus-web)**
  - 담당자 메뉴 "점검 보고서": 새 보고서(기간 기본 = 지난달 1일~말일, 범위 선택) + 지난 보고서 목록
  - 보고서 화면: 머리(번호·기간·범위·작성자·생성 시각·근거 조항) → **점검 수행 확인**(해시체인 정상/이상 + 재계산 건수, 탐지 배치 실행·실패·마지막 정상 점검, 경로별 요약 표) → **경로별 섹션**(접속기록·탐지/소명 수치, 룰별, 탐지건 목록 — 정보주체 "member_10***, … 외 N명" / DB는 "미특정 N건", 상태는 생성 시점)
  - "인쇄 / PDF로 저장" 버튼(`window.print()`), 인쇄용 CSS: 메뉴·버튼 숨김, **경로 섹션마다 새 쪽** — 경로별로 떼어 보고할 수 있게
  - 표기 함수는 `lib/reports.ts`(Next.js 페이지 파일은 추가 export 불가)
- **E2E `e2e/test_report.py`**: 담당자가 이번 달 보고서 생성 → 경로별 섹션·해시체인 정상·원본 회원번호 표기 없음·다시 열어도 같은 스냅샷 — E2E 12개 통과
- README 서비스 표(argus-web)·E2E 확인 항목에 점검 보고서
- PR #51(9-1)·#52(9-2) 병합, 로컬 스택 갱신(마이그레이션 0013) → **사용자가 보고서 화면 확인 완료**(생성·경로별 섹션·마스킹 표시·인쇄)

**결정사항**
- **기능 동결 2026-10-08(목)**(사용자 결정). 신규 기능은 10/07까지, 이후는 점검·검수·법/가이드 준수 확인·이해·정리·보완(새 기능 없음). 포트폴리오는 그 뒤 — 검수의 발견사항·조치도 포트폴리오 소재
- **보고서 범위**: 한 보고서 안에 경로별 섹션(policy 1-5)을 기본으로 하고, 생성 때 범위(전체/화면 경유/DB 직접)를 고를 수 있게(사용자 동의 — 경로별로 보고처가 다른 조직은 단일 경로 보고서로)
- **식별값 표시**: 마스킹 값 몇 개 + "외 N명"(사용자 결정), DB 직접은 "미특정 N건"
- **파일은 서버에 만들지 않음**: 화면(인쇄용)이 스냅샷을 그리고 담당자가 브라우저에서 PDF로 저장 — 외부 라이브러리 없이(CSS 한 장 원칙). `file_path`는 비워 둠

**설계 변경**
- **policy 1-5 "보고" 보완**: 경로별 섹션 + **범위 선택(전체/단일 경로)**. 영향 문서: policy 1-5·6-1, actor-flows F-05 #9(Cowork 반영)

**미결·이슈**
- 브라우저 인쇄(PDF 저장)는 클라이언트 동작이라 기록할 수 없다 — 생성(EXPORT)과 열람(READ)만 남는다. 보고서 화면 열람 자체가 기록되므로 실무상 충분하다고 판단(보안성 검토 때 재확인)

**세션 마무리 — v0.1 기능 레이어 현황 (동결 직전)**
- 1 ✅ EVENT 룰 3개 / 2 ⏸ 언마스킹(v0.2) / 3 ✅ 접속기록 조회·검색 / 4 ✅ AGGREGATE 룰·기준선 / 5 해체 / 6 ✅ 룰 빌더·변경 이력·심각도 색 / 7 ✅ 플랫폼 무대장치(고객 화면·주문결제·문의·소명 첨부) / 8 ✅ 2티어(① 완료 · ② ⏸ 보류 · ③ 완료) / 9 ✅ 점검 보고서(마스킹·경로별)
- 게이트 A(10/01)·게이트 B(10/06) 통과. 오늘 병합 PR: #49~#52. 열린 PR 없음
- 이 로그 갱신분(세션 마무리)은 다음 PR(Cowork 사본 docs PR)에 함께 싣는다(로그만 따로 push하지 않음)

**다음 할 일**
- (기능 동결 10/08) 점검·검수: 법/안내서 대조, 보안 점검, 사용자 이해·정리, 발견사항 기록 → 보완
- 동결 후 문서: 심사자용 README(구현 현황표 — 기능 레이어 8 ② 보류 📋 포함), 시연 자료(GIF·스크린샷) → 포트폴리오
- Cowork 동기화: 10/07 설계 변경(보고서 범위 선택), 기능 동결 일자

---

## 2026-10-06 (6) — 구현 순서 ③-3: DB 직접 접근 기준선 시드 → 기능 레이어 8 마무리

**한 일**
- PR #47(③-2 + 게이트웨이 카탈로그 캐시 버그 수정) 병합
- **`apps/db-gateway/app/seed_baseline.py`** + compose **`db-gateway-seed`**(일회성): 지난달 1일부터의 가상 DB 조회 기록을 **게이트웨이 원문 저장소 + 전송 버퍼**에 넣는다 → 게이트웨이 전송 루프가 수집 API로(CLAUDE.md 3절 #5)
  - 3티어 기준선 시드(platform-api `baseline.py`)와 같은 원칙: 평일 09:00~17:59만(야간·주말 룰 회피), 지난달 하루 5건 / 이번 달 평소 3건, **`mkt_lee`만 15건**(마케팅 담당자의 DB 직접 조회 급증 → 대량 추출 의심 시나리오). 평소 DB 사용자는 운영팀 `ops_park`. 접속지 IP는 3티어 시드와 같은 사람-PC 대응
  - 회원·주문·문의를 회원번호로 찾는 조회 3종(리터럴 포함 원문) → **실제 중계와 같은 함수**(`sql.analyze` → `session.statement_event`)로 접속기록 생성 — 형식이 갈라지지 않게 `Session._event`를 모듈 함수 `statement_event`로 뺐다
  - 원문 레코드에 `seed: true` 표시(사고 조사 때 실제 기록과 구분), 원문·버퍼·완료 표시(`meta` 테이블)를 **한 트랜잭션**으로 — 다시 실행하면 건너뜀(`Store.record_many`·`marked`)
  - 시드 컨테이너엔 DB 비밀번호·서명 키를 주지 않고(DB 계정 이름과 볼륨만), 네트워크도 없음(`network_mode: none`)
- 테스트 124개 추가(`test_seed_baseline.py` — 모양·평일 근무 시간·정규화 SQL에 회원번호 없음, **1년치 날짜(3일 간격)에서 평소 취급자는 기준선이 판정될 때 2배 미만 / mkt_lee는 2배 이상**, 한 번만·원문 지문 대조) — 게이트웨이 171개 통과, ruff 통과
- **로컬 확인**: 시드 292건 → 게이트웨이 전송(거부 0) → 탐지 배치 → **"DB 직접 전월 대비 급증 — mkt_lee 3.0배"** 탐지건 생성 + 자동 소명 요청. 재실행 시 "already seeded — skipped"
- README 2-2에 DB 기본 룰·경로 구분·기준선 시드·보류 항목, 서비스 표에 `db-gateway-seed`
- **E2E**: 3티어 기준선 확인 테스트가 경로 구분 없이 `ops_park`의 지난달 조회를 세고 있어 DB 기준선(같은 계정)까지 더해져 실패(220건) → 경로 "화면 경유"로 한정하고, **DB 기준선이 게이트웨이 → 수집 API로 도착했는지 확인하는 테스트 추가** — E2E 11개 통과

**결정사항**
- 급증 시연 계정 `mkt_lee`(E2E는 룰 이름으로 탐지건을 고르므로 충돌 없음), 평소 DB 사용자 `ops_park`

**설계 변경**
- 없음

**미결·이슈**
- 기능 레이어 8 상태: **① 완료 · ② 보류 · ③ 완료** — Cowork 동기화 필요(10/06 (4) 설계 변경 2건: ② 보류, policy 1-3 기본 룰 3개 / v0.2 후보: 사고 조사(재현) 체계 / 법령 해석 확인: 미특정 + SQL 원문 기록으로 갈음)
- README "구현 현황표"(v0.1 Must — 심사자용 README)는 아직 없음 — 기능 동결 즈음 작성

**다음 할 일**
- Cowork 동기화 → 사본 갱신 docs PR
- 기능 레이어 9(점검 보고서 — 마스킹 보고서, 3티어·2티어 경로별 집계) 착수 여부·기능 동결 일자 확인

---

## 2026-10-06 (5) — 구현 순서 ③-2: 화면의 경로 구분·DB 기록 상세·경로별 소명 안내

**한 일**
- PR #45(문서)·#46(③-1) 병합
- **API**: 탐지건 상세의 하위 기록·접속기록 검색 결과에 `db` 블록(DB 직접 기록일 때만) — 정규화 SQL·테이블·컬럼·건수·원문 참조·정보주체 미특정 여부. DB 계정·토큰 ID·지문은 싣지 않음(`app/detections/db_detail.py`, 표시할 키만 허용 목록)
- **화면(argus-web)** — 용어는 policy 1-5 그대로 "화면 경유(3티어) / DB 직접(2티어)"
  - 탐지건 목록: 경로 열(DB 직접은 청록 배지) + 접근 경로 필터
  - 탐지건 상세: 제목·요약에 경로, 하위 기록의 "기능" 칸이 DB 기록이면 **정규화 SQL + 테이블**, 정보주체 칸은 회원번호를 특정 못 한 DB 기록이면 "미특정 N건" 배지
  - **경로별 소명 기준(policy 3-3)**: 취급자가 소명할 때(REQUESTED) 작성 안내, 담당자가 검토할 때(SUBMITTED) 검토 기준 — DB 직접은 요청자·요청 근거(첨부)·목적·대상 범위·변경 전후 확인 / 요청 근거 실재·범위 초과 여부·앱 우회 이유
  - 접속기록 검색: 경로 열·필터 표기 통일, DB 기록은 SQL 표시, 미특정 표시
  - 룰 빌더의 경로 선택지 표기도 같은 용어로("응용프로그램" → "화면 경유(3티어)")
- 테스트: API 2개(검색 결과·탐지건 상세의 `db` 블록, DB 계정 미노출) — argus-api 390개 통과, ruff 통과 / argus-web eslint·typecheck·build 통과

**결정사항**
- SQL은 텍스트로만 표시(HTML 해석 없음). 정규화 SQL에는 값이 없다는 전제(수집 검증에서 따옴표·달러 인용 거부)라 취급자에게도 보여 준다(policy 1-5 "취급자 화면")
- 원문 참조(`raw_ref`)는 화면에 표시하지 않고 API에만 — 사고 조사 때 게이트웨이 원문을 찾는 키

**설계 변경**
- 없음

- **버그 수정(CI에서 발견, PR #47에 함께)**: 게이트웨이 카탈로그의 사용자 함수 목록 캐시가 "마지막 조회 시각" 초깃값을 `0.0`으로 두고 `time.monotonic()`과 60초 차이로 갱신을 판단 → `monotonic()`은 부팅 후 경과 시간이라 **갓 켜진 머신(CI 러너·재부팅 직후 서버)에서는 첫 1분 동안 빈 목록을 최신으로 취급**해 `SELECT 사용자함수()`를 "테이블 없음"으로 분류, Argus 전송에서 누락. 초깃값을 "조회 전(None)"으로 바꾸고 회귀 테스트(`test_catalog.py` — monotonic 5초로 고정해도 첫 조회는 DB를 본다, 수정 전 코드에서 실패 확인) 추가. 로컬·#44 CI에서는 머신이 켜진 지 오래라 드러나지 않았음

**미결·이슈**
- 화면 확인은 사용자 몫(로그인 필요) — DB 기본 룰이 야간·주말이라 평일 낮엔 탐지가 안 생긴다. 확인용으로 룰 빌더에서 "DB 직접 + 회원 기본정보 조회" 룰을 잠깐 만들면 바로 볼 수 있음

**다음 할 일**
- ③-3: DB 경로 전월 기준선 시드(게이트웨이 경유) — 시연용
- 기능 레이어 8 마무리: README 구현 현황(① 완료·② 보류·③ 완료), Cowork 동기화

---

## 2026-10-06 (4) — 게이트 B 통과 후 방향 결정 + 구현 순서 ③-1: 탐지건 경로 구분·DB 기본 룰

**한 일**
- PR #43(sqlalchemy 2.1.3)·#44(3b) 병합 확인, Cowork 동기화분 사본 → 문서 PR #45
- **② 회원번호 추출 vs 사용자 제안(SQL 저장 → 사고 시 당시 백업에서 재실행) 검토·조사**
  - 「개인정보의 안전성 확보조치 기준 안내서」(2024.10) 90쪽: 검색조건문(쿼리)으로 대량 처리한 경우 그 쿼리를 "처리한 정보주체 정보"로 기록할 수 있으나, 테이블 변경 등으로 추적이 어려울 수 있어 **해당 시점 DB 백업 등 책임 추적성 확보 조치**가 필요하다고 명시 → 사용자 제안과 같은 방식이고, 쿼리 기록 부분은 게이트웨이 원문 저장소(SQL·매개변수 + 지문)로 이미 충족
  - 남는 숙제: ①하루 1회 백업으로는 당시 상태를 못 만든다 → 시점 복구(PITR·WAL 보관) ②**백업 보관 기간 ↔ 파기 정책 충돌**(접속기록 1년(5만 명 이상 2년)만큼 백업을 두면 "탈퇴 즉시 파기·백업 7일" 설계와 정면 충돌) ③재현 불가 SQL(ORDER BY 없는 LIMIT, `now()`·`random()`, 트랜잭션·세션 상태) ④재현 자체도 개인정보 처리 → 격리 환경·기록 필요
  - 상용 사례(공개 자료 한도): 국내 3티어 접속기록 솔루션(UBI SAFER-PSM)은 요청·응답 분석으로 정보주체 자동 식별, IBM Guardium은 반환 결과 검사 규칙(extrusion rule), 국내 DB 접근제어(DBSafer 등)는 SQL·건수·실사용자 기록 수준까지만 확인 — 사고 조사는 SQL 로그 + 백업 복구에 의존하는 셈
- **구현 순서 ③-1 (Argus)**
  - 마이그레이션 0012: `detection.access_path`(기존 행 APP) + 진행 중 유니크 인덱스에 경로, DB 직접 기본 룰 3개 시드(+ 변경 이력)
  - 탐지 배치: EVENT·AGGREGATE 묶음 기준에 경로 추가 — "전체" 경로 룰도 화면 경유·DB 직접을 한 탐지건에 섞지 않음, 집계·전월 기준선도 경로별
  - 탐지건 API: 목록·상세에 `access_path`, 목록에 `access_path` 필터
  - 확인: 룰 빌더는 이미 접근 경로(응용프로그램/DB 직접/전체) 선택을 지원하고 배치도 경로로 거른다 — 담당자가 UI로 DB 룰을 만드는 것은 이미 가능했고, 빠진 것은 탐지건의 경로 구분·화면 표시였다
  - 테스트 10개 추가(전체 경로 룰의 경로별 탐지건, DB 야간 5경우, DB 주말, DB 급증의 경로 한정 집계, 전체 경로 집계의 경로별 기준선, API 경로 표시·필터·잘못된 값 400) — argus-api pytest 388개 통과, ruff 통과

**결정사항**
- **② 회원번호 추출 보류** + SQL 재현 방식은 여유 있을 때 추가 논의(사용자 결정 — 이번 버전 구현 여부 미정). README에는 📋(설계 완료/보류)로 표시
- **DB 직접 접근 기본 룰 = 야간·주말·전월 대비 급증 3개**(사용자 결정, "주간/야간" = 3티어와 같은 야간+주말 짝 — 안내서 비정상 행위 예시 "업무시간 외·휴무일"과 일치). 나머지는 담당자가 룰 빌더로
  - 심각도: 같은 3티어 룰보다 한 단계 높게(야간 HIGH·주말 MEDIUM, 급증 MEDIUM) — DB 직접 접근은 평소 드문 경로
  - 조건: 개인정보 데이터(회원·주문·문의·결제수단)를 처리한 문장만 — DB 툴 접속(LOGIN)·업무 외 테이블까지 잡으면 접속 한 번에 탐지가 여러 건
  - 급증: 1개월 윈도우, 문장 수(LOG_COUNT), 전월 동기 2배, 기준선 최소 20건(3티어와 같은 값)
- **퇴직자 계정 룰은 화면 경유(APP)에만 유지**(사용자 결정 — DB 경로로 넓히지 않음)
- 전월 대비 급증의 시연용 기준선: 지난달 DB 접근 기록을 가상으로 만들어 **게이트웨이 원문 저장소 + 수집 API**로 넣는다(원문 대조가 깨지지 않게) — 이후 PR

**설계 변경**
- **policy 1-3 "2단계 룰"**: 5개(DB 경로 개인정보 조회·결제수단 접근·개인정보 변경·대량 처리·정보주체 미특정) → **기본 3개(야간·주말·전월 대비 급증)**, 나머지는 룰 빌더로 담당자가 추가. 미특정 룰은 ② 보류로 제외. 퇴직자 룰 "APP → 전체" 변경도 취소. 영향 문서: policy 1-3, db-schema 3-6(기본 룰 시드), CLAUDE.md 6절 8행(③ 내용) → ✅ **원본 반영됨**(2026-10-07, policy 1-3·db-schema 3-6·CLAUDE.md 6절)
- **구현 순서 ② 보류**: architecture 3-4 "정보주체 식별(C안)"·구현 순서, CLAUDE.md 6절 8행·게이트 B 문구, requirements 1-2 — "보류, SQL 재현 방식과 함께 재논의"로. 영향 문서: architecture 3-4, CLAUDE.md, requirements 1-2·5-3 → ✅ **원본 반영됨**(2026-10-07, architecture 3-4 "정보주체 기록 방식"·CLAUDE.md 6절 8행·게이트 B·requirements 1-2·5-3)

**미결·이슈**
- **v0.2 후보 — 사고 조사(재현) 체계**: 시점 복구 백업(WAL 보관), 재현 도구(격리 환경 + 재현 행위 기록), 백업 보관 기간과 파기 정책 정리(정책 판단 — Cowork), 원문 저장소 암호화(안내서 "필요시 암호화") → ✅ **원본 반영됨**(2026-10-07, architecture 11절 미결·CLAUDE.md v0.2 이후)
- DB 기록의 "정보주체 미특정"은 계속 유지(건수만) — 안내서 기준으로는 원문 SQL(검색조건문) 기록으로 갈음하는 해석. 법령 해석 확인 요청(Cowork) → ✅ **원본 반영됨(해석 성립)**(2026-10-07, 안내서 2024.10 인쇄 90쪽 원문 확인 — architecture 3-4 "해석 정리"·11절 미결 해소. 책임 추적성 조치(시점 백업)가 없어 v0.1은 부분 충족, v0.2 사고 조사(재현) 체계로)

**다음 할 일**
- ③-2: 화면 — 탐지 목록·상세·취급자 화면·접속기록 검색에 "화면 경유(3티어)/DB 직접(2티어)" 표시·필터, DB 기록 상세(정규화 SQL·테이블·컬럼·건수), 경로별 소명 안내
- ③-3: DB 경로 전월 기준선 시드(게이트웨이 경유)

---

## 2026-10-06 (3) — 기능 레이어 8(2티어) 구현 순서 ① PR 3b: 문장 기록 + 직통 포트 닫기 (게이트 B 기준)

**한 일**
- PR #42(3a) 병합, 사용자 DBeaver 확인(16432 접속·LOGIN 도착)
- **`app/sql.py` — SQL 분석(pglast 8.5)**
  - 정규화: `scan()` 토큰으로 문자열·숫자·비트·16진·달러 인용 리터럴 → `$n`(기존 매개변수 번호 다음부터), 주석 토큰 삭제, 공백 정리. 따옴표·달러 인용이 남으면(따옴표 든 식별자 등) 정규화 실패 문구로 바꿔 Argus 검증과 같은 기준으로 원문 유입 차단
  - 수행업무: SELECT=READ / INSERT=CREATE / UPDATE·MERGE=UPDATE / DELETE·TRUNCATE=DELETE / COPY TO=DOWNLOAD·COPY FROM=CREATE / **CALL·DO·SQL EXECUTE=UPDATE**(안을 볼 수 없음) / EXPLAIN은 안의 문장 기준(ANALYZE는 실제 실행)
  - 테이블: RangeVar(CTE 이름 제외), 시스템 테이블(`pg_catalog`·`information_schema`·스키마 없는 `pg_*`) 제외. 데이터 유형 = 테이블 매핑 중 가장 민감한 것(architecture 3-4 표)
  - 기록 제외(원문만 저장): 트랜잭션 제어·SET·SHOW 등 `CONTROL`, DDL·DCL `DDL_DCL`, 사용자 테이블 없음 `NO_USER_TABLE`. **사용자 함수(카탈로그로 판별)·DO·CALL은 전송 — `MEMBER_BASIC`·`subject_unresolved`**(10/06 결정)
  - 파서가 못 읽은 문장: READ·MEMBER_BASIC·미특정으로 전송(실행됐다면 무엇을 했는지 모르므로 보수적으로) — 대개 DB도 거부해 FAILURE로 남음
- **`app/session.py` — 연결별 문장 추적**: 단순 질의는 문장마다(pglast `split`), 확장 질의는 Parse(이름→SQL·타입)·Bind(포털→매개변수)·Execute로 실행 단위를 만들고 응답 순서(CommandComplete·ErrorResponse·PortalSuspended·EmptyQuery·ReadyForQuery)로 맞춘다. 오류 뒤 Sync까지 건너뛴 실행은 일어나지 않았으므로 기록 안 함. 이름 있는 문장 재사용(Bind·Execute만)에 대비해 문장 이름별 결과 컬럼 설명 캐시. 바이너리 매개변수는 타입별 해석(int·bool·text·uuid, 그 밖은 hex). 건수: INSERT·UPDATE·DELETE·MERGE·COPY는 완료 태그, 그 밖은 흘려보낸 행 수(나눠 가져오기 누적). fastpath 함수 호출은 기록할 수 없어 연결 종료
- **`app/catalog.py`**: 게이트웨이 자체 연결로 결과 컬럼(테이블 OID·컬럼 번호 → `member.email`, OID별 캐시)과 사용자 함수 이름(60초 캐시) 조회
- **fail-closed**: 완료·오류 신호를 DB 툴에 넘기기 전에 원문+접속기록 저장, 실패하면 `access log unavailable` 오류로 바꾸고 연결 종료(카탈로그 조회 실패 포함)
- 문장 기록 형식(api-spec 2-2): `subject = {type: MEMBER, ids: [], count}` — 개인정보 테이블이면 `count = 건수` + `context.subject_unresolved = true`(구현 순서 ① — 회원번호 추출 전), 업무 데이터가 아니면 0. 실패는 건수 0. `context`: `db_user`·`sql_normalized`·`tables`·`columns`(반환 컬럼 + UPDATE/INSERT 대상 컬럼)·`row_count`·`token_id`·`raw_ref`·`raw_fingerprint`. 원문 레코드: SQL 원문·매개변수·타입·프로토콜·문장 이름·결과·오류 코드·건수·전송 여부·제외 사유
- **platform-db 호스트 포트(15432) 제거** — DB 툴 입구는 게이트웨이 하나(architecture 3-4·7-2). `.env.example`의 `PLATFORM_DB_PORT` 삭제, README 서비스 표·2-2 갱신
- **E2E `e2e/test_db_gateway.py`**: 관리자 화면 API로 토큰 발급 → 비TLS 거부 → 게이트웨이 접속(공용 계정 확인) → 리터럴 조회·목록 조회 → 원장에 LOGIN + READ 2건(정규화 SQL `… = $1`, `MEMBER_BASIC`, 테이블·컬럼, 건수 3, 미특정, 지문) 도착, 리터럴·토큰 값은 원장에 없음 → 담당자 접속기록 검색에서 접근 경로 "DB"로 보임
- 게이트웨이 테스트 46개(신규 `test_statements.py` 18개: 정규화·원문 분리, 확장 질의 매개변수, 데이터 유형 5종, 업무 외 테이블, COPY=DOWNLOAD, 기록 제외 3종 원문 보관, 사용자 함수·CALL·DO, 실패 기록, 한 문자열 여러 문장, 오류 뒤 미실행 문장 무기록, 이름 있는 문장 재사용, 기록 실패 시 오류 변환·연결 종료). 테스트 하네스 종료 순서 수정(남은 연결 작업 취소 후 루프 정지)

**결정사항**
- 구현 중 정한 세부(설계에 없던 것, 다른 문서 영향 없음): COPY FROM=CREATE, SQL 수준 `EXECUTE`(준비된 문장 실행)=안을 볼 수 없는 실행과 같게, 파서 실패 문장=보수적 전송, fastpath 함수 호출=연결 종료, EXPLAIN=안의 문장 기준

**설계 변경**
- 없음 (위 세부는 architecture 3-4 "수행업무 매핑"의 구현 세부 — Cowork 동기화 때 표에 덧붙일지 판단 요청)

**미결·이슈**
- **②(회원번호 추출) 전이라 모든 DB 개인정보 처리가 "정보주체 미특정"** — 설계상 ① 범위(게이트 B 기준에 회원번호 없음)
- 파서·정규화는 PG18 문법 기준 — PG16에서 실행되는 SQL은 모두 읽을 수 있다(10/06 실측)
- 원문 저장소가 DB 툴 메타데이터 조회까지 모두 쌓는다(DBeaver 접속 1회 = 수십 건) — 열람 도구·보관 기간·파기는 v0.2(architecture 8-6)
- 기존 로컬 스택: 병합 후 `docker compose up -d --build` 하면 platform-db가 포트 없이 다시 만들어진다(데이터 볼륨 유지). 호스트에서 15432로 붙던 DBeaver 설정은 더 이상 동작하지 않음 — 게이트웨이(16432)로

**다음 할 일**
- 게이트 B 판단(사용자): 3b 병합으로 구현 순서 ① 완료 → ②(회원번호 추출) 진행 여부
- Cowork 동기화: 10/06 설계 변경(사용자 함수 매핑) + 위 세부

---

## 2026-10-06 (2) — 기능 레이어 8(2티어) 구현 순서 ① PR 3a: db-gateway 중계·인증

**한 일**
- 레포 정리: Cowork 사본 갱신 PR #41(10/05·10/06 질의·설계 변경 원본 반영) 병합
- **새 앱 `apps/db-gateway`** (Python asyncio, 전용 이미지 — 보안 경계라 플랫폼 이미지와 분리, 비root 계정 `gateway`)
  - `server.py`: SSLRequest → TLS 종단(비TLS는 인증 전 거부) → 평문 비밀번호 요청으로 토큰 수신 → 토큰·계정 확인 → 공용 계정으로 platform-db 연결(연결 1:1) → **LOGIN 기록 성공 후에야** DB 툴에 AuthenticationOk(fail-closed) → 양방향 중계 → 토큰 만료 시각에 연결 종료. CancelRequest는 게이트웨이가 연 연결의 키일 때만 전달. 시작 매개변수는 허용 목록만 넘김(`options`·`replication` 차단), 데이터베이스는 플랫폼 DB만
  - `auth.py`: 토큰은 서명 먼저 검증(알고리즘 고정) → 용도(`aud`)·주인(`sub` = 입력 아이디)·만료를 따로 확인 — 서명이 유효할 때만 실패 기록에 `token_id`. 계정 상태는 `operator` 조회(별도 연결, 실패 시 거부). **없는 아이디는 Argus·원문 저장소 어디에도 남기지 않음**, 응답은 토큰 오류와 같게(계정 열거 방지)
  - `upstream.py`: platform-db 쪽 SCRAM-SHA-256 로그인(`scramp`) — 중계 연결은 프로토콜을 직접 다뤄야 해서 드라이버 대신 메시지 수준 구현
  - `store.py`: `gateway-data` 볼륨의 SQLite 하나에 **원문(raw_record) + 전송 버퍼(outbox)를 같은 트랜잭션**으로. 원문은 트리거로 UPDATE·DELETE 금지(append-only), 지문 = SHA-256(정렬 JSON), `raw_ref` = event_id. WAL + `synchronous=FULL`
  - `sender.py`: platform relay 규칙 복사(출처 PLATFORM, 200 건별 판정·400 DEAD·413 분할·그 밖 PENDING 백오프 1분→1시간 무기한). 이미지를 나눠 import 대신 복사 — 규칙 변경 시 두 곳 함께 수정
  - `tls.py`: 운영 인증서 경로가 없으면 첫 기동 때 자체 서명(EC P-256, 키 0600)을 볼륨에 생성
  - LOGIN 원문 = 접속 정보만(아이디·IP·TLS 버전·`database`·`application_name`·결과·실패 사유·서명 유효 시 `token_id`), 토큰 값 없음 (architecture 3-4, api-spec 2-2 보완 #5)
- compose: `db-gateway`(`127.0.0.1:${DB_GATEWAY_PORT:-16432}:6432`, platform-net + argus-net, `gateway-data` 볼륨) + `db-gateway-test`. `.env.example`에 `DB_GATEWAY_PORT`
- CI python matrix·docker-build(runtime·dev)·Dependabot(pip·docker)에 db-gateway 추가
- README 2-2 갱신(DBeaver 연결 표, 15432는 문장 기록 단계에서 닫힘), 서비스 표·테스트 명령. 관리자 화면 접속 주소 `localhost:16432` 표시
- 테스트 28개(`db-gateway-test`): 실제 PostgreSQL을 플랫폼 DB로 두고 psycopg(libpq)로 접속 — 공용 계정 중계·확장 쿼리 매개변수 중계·LOGIN 기록 필드(Argus v0.5 형식)·원문 지문 대조·토큰 값 미기록 / 비TLS 거부(기록 없음) / 실패 6종(위조·용도·남의 토큰·만료·퇴직·잠금) 기록과 `token_id` 유무 / 없는 아이디 무기록 / 다른 DB 거부 / 기록 실패·계정 조회 실패 시 접속 거부 / 토큰 만료 시 연결 종료 / 버퍼 원자성·append-only·전송 응답별 처리
- **로컬 스택 확인**: 토큰으로 게이트웨이 접속 → `current_user = platform_owner`(공용 계정), Argus 원장에 `access_path=DB, action=LOGIN, actor=ops_park, db_user=platform_owner` + 지문 도착

**결정사항**
- 게이트웨이 포트 `16432`(사용자 결정 — 15432 직통 포트와 구분해 예전 DBeaver 설정이 게이트웨이로 잘못 들어가는 혼동 방지)
- PR 3을 3a(중계·인증·LOGIN)·3b(문장 기록·직통 포트 닫기)로 나눔(사용자 동의). 게이트 B 판단 = 3b 병합
- **사용자 함수·프로시저·`DO`·`CALL` 매핑(3b에서 구현)**: 수행업무 — `SELECT 사용자함수()` = `READ`, `CALL`·`DO` = `UPDATE`(내부에서 변경 가능) / 데이터 유형 = `MEMBER_BASIC`(게이트웨이가 내부를 볼 수 없어 개인정보 처리로 가정 — `NONE`이면 미특정 룰에도 안 걸리고, `PAYMENT`면 결제수단 룰 오탐) / `subject_unresolved = true` (사용자 결정, 추천안)

**설계 변경**
- **사용자 함수·`DO`·`CALL`의 수행업무·데이터 유형 매핑 확정**(위 결정) — architecture 3-4 "기록 제외"의 "구현 시 확정" 항목. 영향 문서: architecture 3-4(Cowork 반영)

**미결·이슈**
- 로컬 확인 중 **argus-api가 10/02 이미지로 떠 있어** 첫 LOGIN 1건이 `UNKNOWN_CONTEXT_KEY`로 거부 → 게이트웨이 버퍼에 DEAD로 남음(로컬 볼륨, 재처리 도구 없음 — api-spec 7절 미결 "DEAD 재처리 도구"와 같은 과제). 기존 스택은 PR 병합 후 `docker compose up -d --build`로 갱신해야 함 — README에 이미 안내됨
- 게이트웨이 원문 저장소 열람 도구·파기는 v0.2(architecture 8-6)

**다음 할 일**
- PR 3b: 문장 기록 — `pglast` 정규화·테이블·컬럼(RowDescription + 카탈로그 매핑)·건수·수행업무/데이터 유형·기록 제외(사용자 함수·`DO`·`CALL` 예외)·이름 있는 문장 캐시·바이너리 매개변수 해석·완료 신호 전 기록(fail-closed), platform-db 호스트 포트 닫기, E2E에 게이트웨이 시나리오

---

## 2026-10-06 — 기능 레이어 8(2티어) 착수 확인 3가지 실측 (CLAUDE.md 6절)

**한 일**
- 레포 밖 임시 폴더에 실측용 최소 게이트웨이(Python asyncio, 버리는 코드)를 만들어 PG16(실측 전용, trust) 앞에 두고 확인. 클라이언트: pgjdbc 42.7.13(Java 21), psql(libpq 16), **사용자 PC의 DBeaver 26.2.1**
- **① 평문 비밀번호 요청 + TLS — 통과**
  - pgjdbc·psql·DBeaver 모두 SSLRequest → TLS 1.3 → `AuthenticationCleartextPassword`에 토큰을 보냄. 비TLS 시작은 인증 전 거부(`sslmode=disable` 실패 확인), 틀린 토큰 거부
  - `sslmode=prefer`(pgjdbc·libpq 기본값)도 TLS로 붙음
  - DBeaver는 연결 설정의 Driver properties에 `sslmode=require`로 설정(버전에 따라 SSL 탭 위치가 다름 — 사용자 안내용)
- **② 확장 쿼리 프로토콜 — 통과**: Parse(SQL·매개변수 타입 OID) / Bind(매개변수 값) / RowDescription(컬럼별 테이블 OID·컬럼 번호) / CommandComplete(`SELECT 3`·`UPDATE 1`)로 필요한 정보가 다 나옴. 구현 시 반영할 점:
  1. pgjdbc는 같은 PreparedStatement를 5회 넘게 실행하면 이름 있는 문장(`S_1`)으로 바꾸고, 이후엔 **Parse·Describe 없이 Bind만** 보냄(RowDescription도 안 옴) → 게이트웨이가 연결별로 "문장 이름 → SQL·RowDescription"을 캐시해야 함
  2. `int8` 매개변수는 **바이너리 형식**으로 옴 → 타입 OID로 해석해야 회원번호 추출(②)·원문 저장 가능
  3. 계산 컬럼(`count(*)`)은 테이블 OID 0 / 테이블 OID → 이름은 카탈로그 조회로 매핑
  4. pgjdbc는 시작 직후 `SET application_name …`을 단순 쿼리로 보냄 → 설계의 `SET` 제외 규칙으로 걸러짐
- **DBeaver 실제 동작**: 한 번 열면 **연결 4개**(연결 테스트·Main·Metadata·SQLEditor, `application_name`으로 구분)를 각각 토큰으로 인증 → 연결 1:1 원칙과 문제없음, 다만 `LOGIN` 기록이 세션당 여러 건 생김. 메타데이터 조회는 대부분 `pg_catalog`만 참조(기록 제외 대상), `SELECT version()`·`current_schema()`처럼 **테이블을 전혀 참조하지 않는 문장**과 `pg_get_keywords()` 같은 함수 FROM도 있음. 사용자 쿼리는 Windows 줄바꿈(`\r`) 포함(`select *\r from member`)
- **③ `pglast` — 통과**: 최신 v8.5(내장 파서 PostgreSQL 18.6)가 PG16 신문법(숫자 밑줄·SQL/JSON 생성자·`IS JSON`·`SYSTEM_USER`·`GRANT … WITH INHERIT`·`any_value`)과 MERGE·COPY·`$n`·다중 문장을 파싱. **정규화 함수는 없음** → `scan()` 토큰(SCONST·ICONST·FCONST·BCONST·XCONST, 달러 인용 포함)으로 리터럴을 `$n` 치환(오프셋은 문자 단위라 한글 SQL도 정확). `fingerprint()`는 리터럴 값을 무시(쿼리 패턴 ID 후보). 테이블 추출은 RangeVar 방문 — **CTE 이름이 테이블로 잡혀 걸러야 함**
- 실측 컨테이너 정리(`docker compose down -v`), 레포 변경 없음
- **구현 순서 ① PR 1 — Argus 수집 검증 v0.5** (`apps/argus-api/app/ingest/validation.py`)
  - `context.query`(SQL 원문) 삭제 → 어느 경로로 와도 `UNKNOWN_CONTEXT_KEY`
  - DB 키 9종(`db_user`·`sql_normalized`·`tables`·`columns`·`row_count`·`raw_ref`·`raw_fingerprint`·`subject_unresolved`·`token_id`)은 `access_path=DB`일 때만 허용. 필수: `db_user`·`raw_ref`·`raw_fingerprint`, `LOGIN` 외에는 `sql_normalized`·`row_count`도 (`MISSING_FIELD`). 형식 검증은 api-spec 2-2 표 그대로
  - 원문 SQL 유입 차단: `sql_normalized`에 작은따옴표 **또는 달러 인용(`$$…$$`·`$tag$…$tag$`)**이 있으면 `INVALID_FIELD`, 메시지에 값을 되풀이하지 않음
  - `row_count`는 원래 APP에도 허용됐으나 v0.5에서 DB 키로 분류 → APP 기록에 오면 거부(보내는 곳 없음 확인)
  - 테스트: DB 정상 수집·`LOGIN` 최소 필드·거부 코드 18건·원문 리터럴 3형태 거부(응답에 값 미노출, 원장 미저장) — argus-api pytest 378개 통과, ruff 통과
- **구현 순서 ① PR 2 — 플랫폼 DB 접속 토큰**
  - 마이그레이션 0005 `db_access_token`(db-schema 4절 DDL 그대로 — `expires_at = issued_at + 1시간` CHECK)
  - `POST /admin/db-tokens`(`app/dbtoken/router.py`): JWT HS256 `sub`=플랫폼 아이디·`jti`=토큰 ID·`aud`=`db-gateway`·`exp`=발급+1시간(연장 없음). 발급 기록에는 토큰 값을 넣지 않고 응답으로 한 번만 반환. 발급 IP는 관리자 접속기록과 같은 신뢰 프록시 규칙, 정할 수 없으면 발급 거부. `@access_log_exempt`(사용자 결정)
  - JWT 시각이 초 단위라 발급 시각을 초로 잘라 발급 기록의 `expires_at`과 토큰 `exp`가 정확히 같게 함
  - 서명 키 `DB_GATEWAY_TOKEN_KEY`(`.env.example`, compose는 platform-api에만 — 시드 컨테이너엔 주지 않음): 없음·32자 미만·**관리자 로그인 키와 같음**이면 기동 거부(키 분리를 설정 실수로 깨지 않게)
  - 관리자 화면 `/admin/db-token`(메뉴 "DB 접속"): 발급 버튼, 사용자 이름·토큰(복사)·만료 표시, "이 화면을 벗어나면 다시 볼 수 없음" 안내, DB 툴 설정(SSL 필수). 토큰은 React 상태에만 두고 브라우저 저장소에 쓰지 않음
  - README 2-2 "DB 직접 접속 (2티어) — 구현 중" 추가(게이트웨이 전까지 15432 직통 포트는 기록되지 않는 경로임을 명시)
  - 테스트(`tests/test_db_tokens.py`): 1시간 서명 토큰·발급 기록(토큰 값 미저장·발급 IP)·발급마다 새 토큰·접속기록 미적재·로그인 필요·퇴직자 차단·관리자 세션 키로 검증 불가 + DB 토큰을 관리자 쿠키로 못 씀·키 없음/짧음/관리자 키와 같음 기동 거부 — platform-api pytest 197개 통과, ruff 통과, platform-web eslint·typecheck·build 통과(컨테이너)
  - 로컬 `.env`에 `DB_GATEWAY_TOKEN_KEY` 무작위 값 추가(값 미출력)

**결정사항**
- `pglast`는 v8.x(PG18 파서 — PG16 문법의 상위 집합)로 진행. 설계의 "기본안 `pglast`, 구현 시 확정" 범위 안이라 설계 변경 아님
- 실측용 코드는 레포에 넣지 않음(구현은 ① PR에서 테스트와 함께 새로 작성)
- **DB 접속 토큰 발급 라우트는 3티어 접속기록에서 제외**(`@access_log_exempt`) — 사용자 결정. 기각: `CREATE` + `NONE`으로 기록(발급 사실을 변조 불가능한 Argus 원장에 한 번 더 남기는 이점이 있으나, 개인정보 처리가 아니고 범위를 키우지 않기로). 발급 사실은 플랫폼 `db_access_token`과 게이트웨이 `LOGIN` 기록의 `token_id`로 추적

**설계 변경**
- **api-spec 2-4 표에 한 줄 추가 필요**: "DB 접속 토큰 발급 — **기록하지 않음**(명시적 제외, 로그아웃과 같은 처리)". 영향 문서: api-spec 2-4 → ✅ **원본 반영됨**(2026-10-06, api-spec v0.5 #6)
- **api-spec 2-2 "원문 SQL 유입 차단" 보완**: 작은따옴표뿐 아니라 **달러 인용**(`$$…$$`, `$tag$…$tag$`)도 거부 — PostgreSQL은 달러 인용으로도 문자열 리터럴을 쓸 수 있어 따옴표 검사만으로는 원문이 통과함. 정규화 SQL에는 `$1` 같은 자리표시만 남으므로 정상 기록은 영향 없음. 영향 문서: api-spec 2-2 검증 표 → ✅ **원본 반영됨**(2026-10-06, api-spec v0.5 #7)

**미결·이슈**
- **기록 제외 범위 해석**: architecture 3-4 "시스템 카탈로그만 읽는 문장" — DBeaver는 테이블을 전혀 참조하지 않는 문장(`SELECT version()`)·함수 FROM(`pg_get_keywords()`)도 보냄. **해석: 사용자 테이블을 하나도 참조하지 않는 문장은 제외**(원문 저장소에는 남김). → ✅ **원본 반영됨**(2026-10-06, architecture 3-4 "기록 제외"·api-spec v0.5 #8). Cowork가 예외 추가: **사용자 스키마 함수·프로시저 호출과 `DO`·`CALL`은 제외하지 않고 전송**(`subject_unresolved = true`, 수행업무·데이터 유형 매핑은 구현 시 확정해 "설계 변경"으로 보고) — 게이트웨이 PR에 반영
- **연결당 `LOGIN` 기록 다건**: DBeaver 1회 접속 = `LOGIN` 4건 — 설계대로 모두 기록(원장 노이즈지만 연결 단위 귀속의 증거). 묶을지는 v0.2 후보 → ✅ **원본 반영됨**(2026-10-06, architecture 8-6 "2티어 한계")
- **게이트웨이 우회 경로(보안성 검토 후보)**: 호스트에서 `docker compose exec platform-db psql`은 게이트웨이를 거치지 않음 — 서버 관리자 권한 경로라 앱 설계로 막을 수 없음. architecture 3-4 "게이트웨이 우회가 구조적으로 불가"는 **네트워크 경로 기준**이라는 한정 필요 → ✅ **원본 반영됨**(2026-10-06, architecture 3-4 "구조" 한정 + 8-6 "호스트 관리자 경로 우회" — 대응은 인프라 통제)

**다음 할 일**
- 구현 순서 ① PR 3개: (1) Argus 수집 검증 v0.5 → (2) 플랫폼 DB 접속 토큰(발급 기록·API·관리자 화면) → (3) `db-gateway`(TLS·토큰·SCRAM 백엔드·1:1 중계·정규화·원문 저장·자체 버퍼·전송 루프, platform-db 호스트 포트 닫기). 게이트 B = (3) 병합. (1)·(2)는 PR #39·#40으로 병합 완료. (3)에 **사용자 함수·프로시저·`DO`·`CALL` 전송**(architecture 3-4 "기록 제외" 예외, 2026-10-06 Cowork) 포함

---

## 2026-10-05 — Cowork 사본 갱신 대조 (기능 레이어 8(2티어) 설계 확정분)

**한 일**
- 레포 정리: 병합이 끝난 `docs/sync-1002`에서 `main`으로 전환, Dependabot PR #36(fastapi·sqlalchemy·ruff)·#37(`@types/node`) 병합(CI·E2E 통과 확인), 원격에 남은 브랜치 없음 확인
- Cowork가 갱신한 사본 8개(CLAUDE.md, docs/README·requirements·policy·actor-flows·architecture·api-spec·db-schema — 2티어 설계 확정: pgaudit안 폐기 → DB 접근 게이트웨이 중계 + DB 접속 토큰)를 구현과 대조 → docs PR
- **대조 결과: 이미 구현된 동작과 충돌하는 기술은 없음.** 새 설계가 요구하는 구현 변경은 아래처럼 구현 순서에 배정:
  1. **①에서 수정** — 수집 검증 `CONTEXT_KEYS`(`apps/argus-api/app/ingest/validation.py`)가 `query`(≤10,000자)를 허용 중 → api-spec v0.5에서 폐기(원문 SQL 유입 차단). `query` 제거 + DB 키 9종·`'` 포함 거부·`access_path=DB` 전용 규칙 추가
  2. **①에서 수정** — `infra/docker-compose.yml`이 platform-db를 `127.0.0.1:15432`로 호스트에 공개 중 → 설계(CLAUDE.md 2절, architecture 7-2)는 게이트웨이만 DB 툴 입구. 닫으면 DBeaver 직접 접속이 불가해지므로 README 포트 표·`.env.example`의 `PLATFORM_DB_PORT`도 함께 정리
  3. **③에서 수정** — `detection.access_path` 마이그레이션(기존 행 `APP`)·진행 중 유니크 인덱스·그룹 키에 경로, 퇴직자 룰 적용 경로 APP → 전체
- **해석이 필요한 곳**(구현은 아래 해석으로 진행, 다음 동기화 때 문구 확인 요청):
  1. architecture 3-4 "토큰 검증 방식": "게이트웨이는 … DB를 조회하지 않는다" ↔ 같은 절 "연결 시 계정 상태(퇴직·잠금) 재확인", api-spec 2-2 "연결 인증 실패는 존재하는 아이디일 때만 기록" — 후자 둘은 `operator` 조회가 필요. **해석: 조회하지 않는 것은 토큰 발급 기록(`db_access_token`)이고, `operator`는 조회한다**
  2. api-spec 2-2: DB 기록은 `raw_ref`·`raw_fingerprint` 필수 — `LOGIN`도 포함. **해석: 연결 인증도 원문 저장소에 레코드를 남긴다**(시작 메시지의 사용자·DB·앱 이름, 토큰은 제외). 서명이 깨진 토큰은 `jti`를 믿을 수 없으므로 실패 기록에서 `token_id` 생략
- 작은 차이(배치·문구만): policy 3-3 "경로별 소명 기준"도 3-2 뒤 기존 설명 목록("반려는 독립 상태로 둔다" 등) 앞에 끼어 들어감(10/02 대조 #3과 같은 문제) / architecture 7-2 "platform-db 포트는 db-gateway·platform-api·relay만" — 일회성 컨테이너(platform-migrate·platform-seed)도 내부망으로 접근

**결정사항**
- 설계 문서 사본 갱신은 지난번처럼 **별도 `docs:` PR**(코드와 섞지 않음)
- 사본은 Cowork가 로컬 레포 폴더에 직접 쓴다(사용자가 내려받아 덮어쓰지 않음)

**설계 변경**
- 없음 (Cowork 확정분의 사본 반영)

**미결·이슈**
- 위 "해석이 필요한 곳" 2건 → ✅ **원본 반영됨**(2026-10-05, architecture 3-4 "계정 상태"·"토큰 검증 방식", api-spec 2-2 "`LOGIN`의 원문 범위") — 해석과 같음

**다음 할 일**
- CLAUDE.md 6절 "2티어 착수 시 가장 먼저 확인할 것" 3가지 실측: ①DBeaver가 TLS 위에서 평문 비밀번호 요청에 토큰을 보내는지 ②확장 쿼리 프로토콜에서 SQL·매개변수·결과 컬럼 정보 획득 ③`pglast`의 PG16 문법 처리
- 실측 통과 시 구현 순서 ①(게이트웨이 중계 + 토큰 + TLS + 원장 도착 + 원문 저장 + 자체 버퍼) PR 단위 계획 공유 — 게이트 B 기준

---

## 2026-10-02 (저녁) — Cowork 사본 갱신 대조 + 기능 레이어 8(2티어) 논의 시작

**한 일**
- Cowork가 갱신한 사본 8개(CLAUDE.md, docs/README·requirements·policy·actor-flows·architecture·api-spec·db-schema — 10/01 기능 레이어 1~6, 10/02 기능 레이어 7·관련 티켓 반영)를 구현과 대조 → docs PR
- **대조 결과: 구현과 어긋나는 동작 기술 없음.** 다음 동기화 때 다듬을 작은 차이(문서 수정은 Cowork에서 — CLAUDE.md 4절):
  1. db-schema 4절 `refund_account` DDL(설계안 블록)에 `account_holder`·`updated_at`·`UNIQUE(member_id)`가 없음, `payment` DDL 블록 없음 — 표에 "레포 마이그레이션 기준"이라 적혀 있어 동작 오해는 없지만 DDL 원본으로 쓰려면 0003과 맞추기
  2. db-schema 4절 `member` 주석 "컬럼명은 구현 시 확정" → 확정됨(`failed_login_count`·`locked_until`). `orders.status` CHECK(PAID)·`product.price` CHECK·`inquiry` 인덱스 2개·`explanation_attachment` 인덱스도 마이그레이션에만 있음
  3. policy 3절: 3-3(소명 근거)이 3-2 뒤 기존 설명 목록("반려는 독립 상태로 둔다" 등) 앞에 끼어 그 목록이 3-3 아래로 들어감 / 4-4가 4-3보다 앞에 있음 — 배치만의 문제
  4. actor-flows F-01 #7 "분리보관은 주문 기능과 함께" → 구현 완료(주문 PAYMENT_5Y + 문의 DISPUTE_3Y, db-schema 4-1과 같게)
  5. architecture 8-6 이월 목록의 "보안 관련은 보안성 검토 단계로" → 사용자 정정(2026-10-02): **기본 보안은 바로 논의·반영, 논쟁 여지가 있거나 구현이 복잡한 것만 이월**

**다음 할 일**
- 기능 레이어 8(2티어) 계획 논의 — 상용 솔루션 방식 비교 → 범위·식별자·정보주체 기록 방식 결정 후 착수

---

## 2026-10-02 (오후) — 소명의 관련 업무 티켓 + 플랫폼 아이디 = Argus 아이디

> PR #29~#33 머지 후 사용자 테스트 중 "소명 화면에 관련 1:1 문의 티켓을 등록하는 부분이 없다"는 지적에서 시작.
> 사용자와 논의해 범위를 좁힘(아래 결정사항).

**한 일**
- **Argus 마이그레이션 0011**: `explanation.ticket_ids varchar(32)[]`(기본 빈 배열, 최대 3개 CHECK)
- **API**: 제출 본문에 `ticket_ids`(선택, 최대 3개) — 공백·대소문자 정리 후 **`INQ-[1-9][0-9]{0,9}` 형식만**(링크 주소에 그대로 들어가므로 엄격히 — 경로 조작·`javascript:` 거부 테스트), 중복은 한 번만. 제출과 함께 저장되고 제출 뒤엔 바꾸는 API가 없음. 탐지건 상세의 차수별 소명에 `tickets: [{ticket_id, url}]` — url = 설정 `ARGUS_PLATFORM_ADMIN_URL`(http(s)만, 아니면 기동 거부) + `/inquiries/{번호}`
- **화면** argus-web: 소명 작성에 **소명 내용과 별도의 "관련 업무 티켓" 입력칸**(칩 형태, 형식 검사, 최대 3개), 차수별 소명에 티켓 번호 + **[플랫폼에서 보기 ↗]**(새 탭, `noopener noreferrer`)
- **플랫폼 로그인 후 돌아가기**: 관리자 화면에서 401이면 `/admin/login?next=<현재 경로>`, 로그인 성공 시 `next`로 이동. `next`는 **같은 출처의 `/admin/` 경로만**(로그인 화면 자신·`//`·역슬래시·외부 출처는 회원 목록으로) — 오픈 리다이렉트 방지
- **같은 아이디 원칙**: 플랫폼 시드에 담당자 계정 **`officer`**(가상 인물 윤서진, OPS/ADMIN, 시드 취급자 공용 비밀번호) — `ensure_officer_operator`로 매번 확인(이미 시드된 스택에도 생김), 다른 취급자처럼 Argus 명부로 동기화. 기준선 시드에는 넣지 않음
- **`create-officer` 전환**: 새 스택에선 플랫폼 시드 → 동기화가 먼저 돌아 Argus에 `officer` 취급자 계정(로그인 불가)이 생겨 README의 `create-officer officer`가 "이미 있는 계정"으로 실패하던 순서 문제 → **로그인 이력이 없는 취급자 계정만 담당자로 전환**(명부 연결 유지 — 플랫폼 퇴직 시 Argus 담당자 계정도 막힘, DISABLED는 되살리지 않음). 실제로 쓰던 취급자 계정은 거부
- 테스트: argus-api **355 passed**(+10 — 티켓 저장·정리·링크, 형식 위반 5종 거부·미제출 유지, 선택 사항, 담당자 전환·사용 중 계정 거부·DISABLED 유지), platform-api **187 passed**(+1 담당자 계정 생성·멱등·로그인·동기화 1건), E2E — 첨부 시나리오에 `INQ-1` 제출·링크 확인, **같은 아이디 시나리오**(동기화된 `officer` → create-officer 전환 → Argus 담당자 로그인 → 같은 아이디로 플랫폼 로그인 → 링크 대상 문의 조회 200). 두 화면 lint·typecheck·build 통과

**결정사항** (사용자와 논의)
- **티켓 내용은 Argus에 두지 않는다 — 링크로 플랫폼에서 확인** (사용자 제안). Argus가 문의 제목·본문을 받으면 절대 규칙 #3(원본 개인정보 금지)과 두 시스템 분리 원칙이 깨짐. 담당자의 플랫폼 열람은 플랫폼 접속기록으로 Argus에 남음(감시자도 감시)
  - 기각: ① 플랫폼 → Argus 티켓 동기화(③ 업무 티켓 API) 후 Argus에서 검색·선택 — 새 시스템 간 API·메타데이터 동기화로 복잡 ② Argus가 플랫폼에 실시간 조회해 본문 표시 — 규칙 #3·네트워크 분리 위반 ③ 원장 기반 후보 목록·대조 표시("이 취급자가 연 티켓", "같은 고객") — 사용자 판단: 직접 조회하지 않은 티켓을 처리하는 경우도 있고(업무 이관), 원장엔 내용이 없어 결국 링크가 필요 → 단순한 직접 입력으로
- **취급자는 티켓 번호를 직접 입력, 검증은 취급자·담당자가** (사용자 결정). 서버는 형식만
- **플랫폼 백오피스 아이디 = Argus 아이디** (사용자 원칙) — 담당자 플랫폼 계정도 같은 아이디로
- 담당자 플랫폼 계정의 소속·권한은 기존 값(OPS/ADMIN)으로 — 플랫폼 소속 목록(CS·마케팅·운영)과 Argus 명부 검증(api-spec 3-1 TEAMS)을 바꾸지 않으려고. 정보보호팀 소속 추가는 필요해지면

**설계 변경** (Cowork 반영 요청)
1. **db-schema 3-4 `explanation.ticket_ids`**, **api-spec**(화면용 API) 소명 제출에 `ticket_ids`, 탐지건 상세에 `tickets`
2. **결정 9 범위 변경**: "문의 번호 언급(소명 텍스트)" → **별도 입력칸 + 플랫폼 링크**. 자동 대조는 계속 v0.2 후보
3. **원칙 추가 — 플랫폼 백오피스 아이디 = Argus 아이디** (actor-flows A4·A5, policy 4절), 담당자도 플랫폼 계정 보유
4. **api-spec 3-1 A5 초기 계정**: login_id 충돌 규칙에 "동기화로 먼저 생긴, 로그인 이력 없는 A5 계정은 담당자 생성 시 담당자로 전환" 추가

**미결·이슈**
- **보안성 검토 이월**: 담당자의 플랫폼 계정이 일반 관리자와 같은 권한(CSV 다운로드 포함 — 플랫폼에 권한 차등 없음, PLT-14 범위 밖) / 담당자 본인의 플랫폼 행위가 탐지되면 자동 소명 요청이 가지 않아(A5 아님) 담당자가 자기 건을 검토하게 됨 — 직무 분리
- v0.2 후보: 티켓 자동 대조(원장의 열람 기록·같은 고객 표시), 접속기록 검색 결과에 티켓 표시

**다음 할 일**
- PR #34 리뷰·머지 → Cowork 동기화(기능 레이어 7 전체 + 이번 변경). 사용자 브라우저 테스트 완료("생각처럼 구현됨")

---

## 2026-10-02 — PR A 확인(관리자 화면 /admin) + 고객 화면 최소판 + 기능 레이어 7 ①②③

> 사용자가 자리를 비운 동안(새벽 요가) "추천대로 끝까지 진행, 논의할 것은 모아서 나중에" 지시로 진행.
> PR은 단계마다 따로, 앞 PR 위에 쌓음(A → B → ① → ② → ③). 논의 항목은 맨 아래 "미결·이슈"에 모음.

**한 일 — PR A (관리자 화면 `/admin` 이동)**
- 개발 스택 재빌드 → 브라우저 확인(점검용 임시 취급자 `qa_web`, 확인 후 TERMINATED): `/` → `/admin/members` → 미로그인이라 `/admin/login`, 로그인 → 회원 목록 500명, CSV `GET /api/admin/members/export` 200·`text/csv`·첨부 파일명, 로그아웃 후 재접근 시 로그인 화면
- **E2E가 버그를 잡음**: platform-web 이미지의 헬스체크가 옮기기 전 주소 `/login`(404)을 보고 있어 컨테이너가 unhealthy → E2E 기동 단계 실패. `/admin/login`으로 수정 → **E2E 4 passed**
- 참고: 확인 시각이 자정 이후라 `qa_web` 로그인이 개발 스택 Argus에 야간 접속으로 탐지될 수 있음(개발 데이터)

**한 일 — PR B (고객 화면 최소판, 결정 2~6)**
- **마이그레이션 0002**: `consent_item`·`member_consent`(db-schema 4절 DDL 그대로), `member`에 `failed_login_count`·`locked_until`. 동의 항목 4개 시드 — 이용약관·개인정보 수집이용(필수)·**만 14세 이상 확인**·마케팅(선택), 버전 `v1`, 문안은 자리표시
- **고객 API** `/shop/*` (`app/shop/`): 동의 항목 조회, 회원가입(필수 동의 없으면 400, 선택 미동의도 이력에 `agreed=false`, 항목·버전·시각·IP), 로그인·로그아웃, 마이페이지(열람·정정, 비밀번호 변경, 선택 동의 철회·재동의 — 덮어쓰지 않고 이력 추가, 필수 동의 철회는 400), **탈퇴 = 즉시 파기**(비밀번호 재확인 → 동의 이력·회원 행 실제 삭제, `shop/withdrawal.py` — ①②가 여기에 덧붙임)
- **고객·관리자 토큰 분리**: `auth/tokens.py`에 `SessionKind`(쿠키 이름 + audience) — 관리자 `platform_session`/`platform-admin`, 고객 `customer_session`/`platform-shop`. aud 검증 필수(aud 없는 토큰도 거부). 요청마다 연장하는 쿠키도 종류별로(`request.state.session_cookie`)
- **고객 로그인 실패**: 5회 → `locked_until = now + 15분`, 실패 횟수 0으로. 잠긴 동안의 실패는 세지 않음(잠금이 끝없이 연장되지 않게), 잠김은 비밀번호가 맞을 때만 알림(403), 없는 이메일·틀린 비밀번호 같은 응답 + 더미 해시
- 입력 규칙: 이메일 소문자 정규화(대소문자 중복 가입 방지), 비밀번호 10자 이상·2종류 이상(KISA 기준), 휴대전화 형식 정규화(`010-0000-1234`)
- **시드**: `backfill_consents` — 동의 이력이 없는 회원에게 가입 시점의 필수 동의 + 마케팅 40%. seed()와 별도로 매번 실행(이미 시드된 개발 스택의 500명에도 생김), IP는 비움(지어내지 않음)
- `/shop` 응답도 `Cache-Control: no-store`
- **화면** platform-web: 고객 `/`(홈)·`/login`·`/signup`·`/mypage`·`/privacy`·`/terms` (`app/(shop)` 라우트 그룹 — URL엔 안 보임), 관리자 레이아웃 분리. 가입 화면은 항목마다 목적·항목·보유 기간, 필수/선택 배지, **"전체 동의" 버튼 없음**. 하단에 처리방침 링크 굵게
- 테스트: platform-api **145 passed**(+24 — 동의 이력·필수 거부·중복 이메일·입력 오류 6종·15분 잠금·자동 해제·잠긴 동안 실패 미집계·**토큰 바꿔치기 2종**·aud 없는 토큰·**고객 행위 outbox 0건**·정정·비밀번호·동의 철회 이력·탈퇴 즉시 파기·백필 멱등), E2E에 고객 시나리오 추가 → **E2E 5 passed**. platform-web lint·typecheck·build 통과
- 브라우저 확인(개발 스택): 가상 계정으로 가입 → 마이페이지 동의 내역(v1·시각) → 마케팅 동의 → 로그아웃 → 마이페이지 접근 시 로그인 화면

**결정사항 — PR B**
- **만 14세 이상 확인을 동의 항목(`AGE_OVER_14`, 필수)으로 저장** — 확인 증적(버전·시각·IP)이 동의 이력과 같은 구조로 남음. 기각: 요청에서만 검사하고 저장 안 함 — 확인했다는 증적이 없음 / `member`에 컬럼 — 스키마 변경. (논의 항목 참고)
- 가입 시 받는 항목은 이메일·비밀번호·이름만, 휴대전화·주소는 마이페이지에서 선택 입력 — 최소수집(§16①)
- 이메일은 로그인 아이디라 정정 대상에서 뺌(이메일 인증이 없어 바꾸면 소유 확인 수단이 없음)
- 가입 직후 자동 로그인 — 이메일 인증이 없어 따로 막을 이유가 없음
- 고객 로그아웃·로그인·가입은 접속기록 대상 아님 — Agent 미들웨어가 `/admin`에만 걸림(테스트로 고정)

**한 일 — PR ① (주문·결제 PG 목업, 환불계좌 암호화, 결제수단 조회 룰)**
- **플랫폼 마이그레이션 0003**: `product`(가상 상품 6개)·`orders`(설계대로 상품 1개, 상태 PAID)·**`payment`**(PG 승인 결과 — 결제수단 종류·카드사·PG 거래번호·승인 시각·금액, **카드번호 컬럼 없음**)·**`refund_account`**(은행·예금주·계좌번호 암호문·끝 4자리, 회원당 1개)·`retained_member_record`·`destruction_history`(설계 DDL 그대로)
- **암호화** `app/crypto.py`: AES-256-GCM, 버전 1바이트 + 무작위 nonce 12바이트 + 암호문·태그, **AAD = `refund_account:{회원번호}`**(다른 회원 행으로 옮겨 붙이면 복호화 실패). 키는 `.env`의 `PAYMENT_ENCRYPTION_KEY`(16진수 64자) → compose가 platform-api·platform-seed에만 `PLATFORM_PAYMENT_ENCRYPTION_KEY`로 전달, 없거나 형식이 틀리면 **기동 거부**. 의존성 `cryptography==50.0.2`
- **고객 API**: 상품 목록(공개), 주문 = 가상 PG 승인(카드사만 받음 — **카드번호 받는 칸 자체가 없음**, 금액은 서버가 상품 가격으로, 거래번호 `MOCKPG-…` 서버 생성), 내 주문, 환불계좌 등록·변경(upsert)·삭제(고객 본인 화면도 끝 4자리만)
- **관리자 API**: `GET /admin/orders`(READ·**ORDER**, 정보주체 = 표시된 주문의 회원, 중복 제거), `GET /admin/members/{id}`(READ·MEMBER_BASIC, 환불계좌 끝 4자리 + 최근 주문 20건), **`GET /admin/members/{id}/refund-account`**(READ·**PAYMENT** — 복호화한 전체 번호). 대상이 정해진 조회는 업무보다 먼저 `record_subjects` → 없는 회원(404)이어도 FAILURE + 정보주체로 남음
- **Argus 마이그레이션 0009**: 룰 **"결제수단 조회"**(EVENT, `data_category=PAYMENT ∧ action=READ`, HIGH) + 룰 변경 이력 CREATE(시스템). 기본 룰 7개
- **탈퇴 확장**(`shop/withdrawal.py`): 주문이 있으면 `retained_member_record`에 **PAYMENT_5Y**(전자상거래법 시행령 §6①3호, 5년) — 주문번호·상품·금액·일시·PG 거래번호·카드사 + 분쟁 시 본인 확인용 이름·이메일·전화. 비밀번호·주소·환불계좌는 담지 않음. 환불계좌·동의 이력·회원 행 삭제(`orders.member_id`는 SET NULL), `destruction_history`에 MEMBER 1건(개인정보 미기록)
- **시드** `seed_commerce`: 주문이 없을 때만 가상 주문 300건(지난 180일, 카드사 무작위, 거래번호 `MOCKPG-SEED…`) + 환불계좌 60개(번호 `0000…`, **실제 저장 경로와 같이 암호화**). 시드 회원(`user%@example.com`)에게만 — 화면에서 가입한 계정에 가짜 주문이 붙던 문제를 브라우저 확인에서 발견해 수정
- **화면**: 고객 홈 = 상품 목록 → `/checkout/[id]` → **가상 PG 결제창**(점선 테두리의 "외부 창", 카드사 선택만, 안내 문구) → `/orders`. 마이페이지에 환불계좌 카드. 관리자 상단 메뉴(회원·주문), 회원 이름 → `/admin/members/[id]`(환불계좌 끝 4자리 + **전체 보기** — 누르기 전 "결제수단 조회로 기록·소명 요청" 확인창), `/admin/orders`. 처리방침에 주문·환불계좌 항목, **처리위탁(가상 PG — 카드번호는 PG사가 받고 회사는 저장 안 함)**, 암호화 조치
- 테스트: platform-api **173 passed**(+28 — 암호화 왕복·AAD 바꿔치기·변조 탐지, 키 없음/형식 오류 4종 기동 거부, 카드번호·금액을 보내도 무시, `payment` 컬럼 집합 고정, 내 주문만, 계좌 암호화·끝 4자리, 계좌 형식 3종, 고객 주문·계좌 outbox 0건, ORDER·MEMBER_BASIC·PAYMENT 기록과 정보주체, 끝 4자리는 PAYMENT 아님, 404도 FAILURE+정보주체, 접속기록에 계좌번호 없음, 탈퇴 분리보관 5년·최소 항목·파기 이력, 시드 계좌 복호화, 화면 가입 계정 시드 제외), argus-api **319 passed**(+1 결제수단 조회 탐지 — 끝 4자리·주문 조회는 미탐지), **E2E 6 passed**(고객 구매·계좌 등록 → mkt_lee 전체 보기 → Argus "결제수단 조회" HIGH 탐지, 원장·응답에 계좌번호·이름 없음). platform-web lint·typecheck·build 통과
- 브라우저 확인(개발 스택, 화면이 꺼져 스크린샷 대신 페이지 텍스트): 가상 결제 → 주문 내역(MOCKPG 거래번호), 관리자 주문 목록 301건·회원 상세·전체 보기 버튼. 점검용 `qa_web`은 확인 후 다시 TERMINATED

**결정사항 — PR ①**
- `payment_method` 대신 **`payment`(PG 결과) + `refund_account`(환불계좌)** 두 테이블 — 카드는 저장할 번호가 없어 한 테이블에 두면 컬럼 절반이 항상 비고, 성격(거래 기록 / 고객 정보)과 보존 규칙(5년 분리보관 / 탈퇴 즉시 삭제)이 다름. 기각: 설계 `payment_method` 유지하며 카드 컬럼만 제거 — 이름과 내용이 어긋남
- 환불계좌 끝 4자리는 **평문 컬럼**으로 — 목록·상세마다 복호화하지 않게(복호화 지점을 "전체 보기" 하나로 좁힘). 끝 4자리만으로는 계좌를 특정할 수 없음
- 고객 본인 화면도 끝 4자리만 — 재인증 수단이 비밀번호뿐이라 세션 탈취 시 노출 최소화. 다시 쓰려면 새로 입력
- AAD로 행 바인딩 — DB 쓰기 권한이 있는 공격자가 암호문을 자기 행으로 복사해 화면에서 읽는 것을 막음
- 회원 상세 조회는 MEMBER_BASIC 1건, 전체 보기는 별도 요청 PAYMENT 1건 — "결제수단을 봤다"를 기록 단위로 분리해야 룰이 정확히 걸림
- 주문 상태는 PAID만, 수량 없음 — 취소·환불 처리는 범위 밖(환불계좌는 등록·조회만). v0.2 후보
- 결제수단 조회 룰은 `auto_request` 기본값(켜짐) 그대로 — 볼 때마다 소명

**한 일 — PR ② (1:1 문의, 고객 → CS)**
- **플랫폼 마이그레이션 0004**: `inquiry`(db-schema 4절 DDL 그대로) + 상태·회원별 인덱스
- **고객 API** `/shop/inquiries`: 작성(제목 200자·본문 5,000자, 공백만 거부), 내 문의(답변 포함, 답변 직원은 안 보여 줌)
- **관리자 API**(접속기록, 데이터 유형 INQUIRY): 목록 READ(본문 제외, 정보주체 = 작성자 중복 제거, 상태 필터), 상세 READ(**`context.ticket_id = "INQ-{번호}"`** — api-spec 2-4 "문의 상세 확인" 그대로), 답변 UPDATE(한 번만 — 이미 답변이면 409, 실패도 정보주체·티켓과 함께 FAILURE로 남음)
- **Agent `record_context()`** 신설 — 플랫폼이 쓰는 context 키(`ticket_id`)만 허용, 그 밖의 키는 보내기 전에 `ValueError`(Argus가 이벤트째 거부 `UNKNOWN_CONTEXT_KEY` → relay가 DEAD 처리하는 것보다 개발 중에 터지는 게 낫다). 지금까지 `context: {}` 고정이던 것을 기록지 값으로
- **탈퇴 확장**: 문의가 있으면 `retained_member_record`에 **DISPUTE_3Y**(전자상거래법 시행령 §6①4호, 3년) — 제목·본문·답변·시각 + 연락처를 **옮기고**, 운영 테이블 `inquiry`의 제목·본문·답변은 "(탈퇴 회원 문의 — 분리보관됨)"으로 지움(번호·상태·시각·답변자만 남음)
- **시드** `seed_inquiries`: 문의가 없을 때만 40건(고정 문구 5종 — 개인정보 없음), ⅔는 cs_kim·cs_choi가 답변
- **Argus**: 탐지건 상세의 기록에 **`ticket_id`**(context에서 그 키만 꺼냄 — 다른 키는 응답에 싣지 않음), 화면에 "연계 티켓" 칸. 소명할 때 "INQ-12 처리 중 조회"를 담당자가 바로 대조할 수 있게(결정 9의 "문의 번호 언급")
- **화면**: 고객 `/inquiries`(작성 + 내 문의·답변), 상단에 "1:1 문의". 관리자 메뉴 "1:1 문의" → `/admin/inquiries`(답변 대기·완료·전체 탭, 티켓 번호) → `/admin/inquiries/[id]`(본문, 작성자 → 회원 상세 링크, 답변 등록). 처리방침 수집 항목에 문의
- 테스트: platform-api **186 passed**(+13 — 본인 문의만, 공백 거부, 고객 문의 outbox 0건, 목록 본문 제외·작성자 1명, 상세 ticket_id·내용 미전송, 답변 1회·409도 FAILURE+티켓, 고객이 답변 확인, 없는 문의도 티켓 기록, 정의되지 않은 context 키 거부, 탈퇴 시 문의 내용 이동·3년, 시드 상태 분포), argus-api **320 passed**(+1 연계 티켓 표시), E2E에 문의 시나리오(고객 문의 → cs_choi 상세·회원 조회·답변 → 담당자 검색으로 원장 도착 확인, 내용·이름 미전송)

**결정사항 — PR ②**
- 탈퇴 시 문의 **내용을 분리보관 테이블로 옮기고 운영 테이블에서는 지움** — 설계 4-1은 "member_id SET NULL로 끊겨 통계 데이터가 된다"였지만, 문의 본문에는 고객이 쓴 개인정보(주소·전화 등)가 있을 수 있어 연결만 끊으면 사실상 보관 기간 없는 보관. 기각: 그대로 둠 — §21 위반 소지 / 문의 행 삭제 — CS 처리 통계·답변자 이력이 사라짐 (**논의 항목**)
- 답변은 한 번만 — 수정·재답변은 범위 밖(v0.2 후보). 답변 수정이 생기면 이력 테이블이 필요
- 문의 목록의 정보주체는 화면에 보인 작성자 — actor-flows F-02 #2 "문의 작성자 목록"
- Argus 접속기록 검색 결과에는 티켓을 아직 싣지 않음 — 탐지건 상세(소명 대조 지점)에만. 검색 화면 표시는 v0.2 후보

**한 일 — PR ③ (소명 근거자료 첨부, 결정 9·10)**
- **Argus 마이그레이션 0010**: `explanation_attachment`(db-schema 3-4 DDL 그대로) + 인덱스, 앱 계정 **SELECT·INSERT·DELETE**(UPDATE 없음 — 해시·경로를 바꿔 근거를 갈아치울 수 없음, 테스트로 고정)
- **API** (`app/detections/attachments.py`): 올리기(취급자 본인 + REQUESTED일 때만), 지우기(같은 조건 — 제출 전 정정), 내려받기(담당자는 **제출된 차수만**, 취급자 본인)
  - 형식: **매직 바이트**로 PNG·JPG·PDF 판정(이름·Content-Type 무시 — `.png` 이름의 HTML 거부 415), 5MB 초과 413, 빈 파일 400, 차수당 3개 초과 409
  - **파일 이름 불신**: 저장 경로 = `년/월/uuid`(서버 생성), `O_CREAT|O_EXCL`·0600, 저장 경로를 볼륨 기준으로 해석해 밖이면 거부. 보낸 이름은 경로·제어문자·따옴표·꺾쇠를 걷어 200자로 — 표시용만
  - **SHA-256**: 올릴 때 계산·저장, **내려받을 때마다 다시 계산해 다르면 500 `ATTACHMENT_TAMPERED`로 내주지 않음**
  - 내려줄 때 `attachment` + `filename*`(UTF-8) + `nosniff` + `CSP: sandbox` + `no-store` — PDF 안의 스크립트 등이 화면 출처에서 실행되지 않게
  - DB 기록에 실패하면 파일을 지움(고아 파일 방지), 지우기는 커밋 뒤 파일 삭제
  - **다운로드 = Argus 자체 접속기록 READ(ACCESS_LOG)**, 정보주체 건수 0, **`context.target = {"detection_id": N}`**(경로 변수 값은 원장에 안 남기므로). Argus Agent에 `record_target()`·기록지 context 추가. 올리기·지우기는 취급자 본인 자료 제출이라 제외(사유 명시)
  - 탐지건 상세의 차수별 소명에 `attachments`(메타데이터·해시, 저장 경로는 안 내보냄)
- **인프라**: argus-api 이미지에 `/data/attachments`(argus 소유, 700), compose에 **`argus-attachments` 볼륨 — argus-api에만**(worker·플랫폼은 안 붙음). 설정 `ARGUS_ATTACHMENT_DIR`. 의존성 `python-multipart==0.0.32`
- **화면** argus-web: 소명 작성 칸에 "근거자료 첨부"(취급자·요청 중 — 올린 목록·삭제), 차수별 소명에 첨부 목록(이름 누르면 내려받기, 크기·SHA-256 앞자리). 오류 문구 7종
- 테스트: argus-api **345 passed**(+25 — 3형식 허용, 위장 3종 거부, 5MB·빈 파일·4개째, 이름 불신·저장 경로, 담당자·남의 취급자 업로드 불가, 제출 뒤 409, 지우면 파일도 삭제, 업로드 미기록, 차수별 목록, 담당자에게 초안 숨김, 다운로드 헤더·자체 기록(READ·target), 남의 첨부·다른 탐지건 번호로 우회 불가, **파일 변조 시 거부**, 앱 계정 UPDATE 불가, 이름 정리 5종), **E2E 8 passed**(admin_han 대량 다운로드 → 자동 요청 → 화면 경유 PDF 첨부·위장 HTML 거부 → 제출 → 담당자 내려받기 해시 일치 → 제출 뒤 삭제 409 → 다운로드가 ARGUS 출처 원장에). argus-web lint·typecheck·build 통과. 개발 스택에서 볼륨 소유권(argus)·쓰기 확인
- E2E에서 겪은 것: 처음엔 첨부 시나리오를 mkt_lee로 짰다가 기존 "요청 취소" 시나리오와 **같은 날·같은 룰·같은 취급자 = 탐지건 하나**로 묶여 서로 상태를 바꿈 → admin_han으로 분리

**결정사항 — PR ③**
- **개별 업로드 + 제출 전 삭제** — 제출(JSON)과 분리. 기각: 제출과 multipart 한 번에 — 원자적이지만 제출 화면·API를 바꾸고, 잘못 올린 파일을 고칠 수 없음. 대가: 요청이 취소(DISMISS)된 차수의 미제출 첨부가 남을 수 있음 (논의 항목)
- SHA-256은 결정 10의 "제출 시" 대신 **업로드 시** 계산 — 제출 뒤엔 바꿀 수 없어(삭제 불가·UPDATE 권한 없음) 같은 값이고, 업로드 직후 응답으로 취급자가 확인할 수 있음
- 첨부 다운로드 기록은 **READ** — Argus 자체 기록 코드는 api-spec 2-7의 LOGIN·READ(·UNMASK·EXPORT)로 두고 플랫폼 코드 DOWNLOAD를 섞지 않는다(기존 테스트가 고정한 결정). 기각: DOWNLOAD 추가
- 담당자에게 미제출 초안은 숨김 — 취급자가 제출을 결심하기 전 자료는 소명이 아님
- 바이러스·악성 PDF 검사는 하지 않음 — 형식 판정 + 내려받기 강제(sandbox)까지. 운영이면 백신 연동 필요(v0.2 후보)

---

### 이 세션 정리 (PR 5개)

| PR | 브랜치 | base | 내용 |
|---|---|---|---|
| [#29](https://github.com/Salgoo26/argus/pull/29) | feat/admin-path | main | 관리자 화면 `/admin` 이동 + 헬스체크 수정 |
| [#30](https://github.com/Salgoo26/argus/pull/30) | feat/customer-shop | #29 | 고객 화면 최소판 |
| [#31](https://github.com/Salgoo26/argus/pull/31) | feat/order-payment | #30 | ① 주문·결제·환불계좌·결제수단 조회 룰 |
| [#32](https://github.com/Salgoo26/argus/pull/32) | feat/inquiry | #31 | ② 1:1 문의 |
| #33 | feat/explanation-attachment | #32 | ③ 소명 첨부 |

머지는 #29 → #30 → … 순서로(앞 PR이 머지되면 다음 PR의 base를 main으로 바꾸거나, GitHub가 브랜치 삭제 시 자동으로 바꿈).

**설계 변경** (Cowork 반영 요청 — 이전 세션분과 함께)
1. **db-schema 4절 `member`**: `failed_login_count`·`locked_until` (고객 5회 실패 → 15분 자동 해제) / **policy 4-3**에 고객 정책·토큰 audience 분리
2. **동의 항목 `AGE_OVER_14`(만 14세 이상 확인, 필수)** 를 consent_item에 — 확인 증적을 동의 이력과 같은 구조로
3. **db-schema 4절 `payment_method` → `payment`(PG 결과, 카드번호 없음) + `refund_account`(AES-256-GCM 암호문 + 끝 4자리, AAD 행 바인딩)** / **requirements PLT-05** PG 목업 / `orders`는 상태 PAID만
4. **db-schema 4-1 탈퇴 절차**: 즉시 파기 + 주문 기록 PAYMENT_5Y(최소 항목 + 연락처) + **문의 내용 DISPUTE_3Y로 이동·운영 테이블 내용 삭제** + destruction_history
5. **api-spec 2-7 표에 행 추가**: "소명 첨부 다운로드 | `READ` | count 0 + `context.target`"
6. **db-schema 3-4** `explanation_attachment` 권한: 앱 계정 SELECT·INSERT·DELETE(제출 전 정정만, 앱에서 강제), UPDATE 없음 / **architecture**: Argus 전용 첨부 볼륨(백업 대상에 추가 필요)
7. Argus 탐지건 상세 응답에 `ticket_id`(context에서 그 키만) — 소명 대조용
8. CLAUDE.md 6절 기능 레이어 7 순서·범위(지난 세션 결정 1) — 구현 완료

**미결·이슈 — 논의 결과** (사용자 결정 2026-10-02: 보안 관련은 보안성 검토 단계로 미루고, 그 외는 추천안대로)
1. **탈퇴 시 문의 내용을 운영 테이블에서 지우고 분리보관으로 옮김** → **추천안대로 유지**(구현 그대로). 설계 4-1 개정 요청(위 설계 변경 4)
2. **분리보관 데이터의 연락처 범위(이름·이메일·전화)** → **보안성 검토로 이월** — 최소수집 관점 점검 항목. 그때까지 구현 그대로
3. **분리보관 테이블의 접근 분리 미이행**(플랫폼 DB 계정 하나) → **보안성 검토로 이월** — 발견사항 후보("분리보관 = 별도 테이블 + 접근 권한 분리" 중 권한 분리 미이행)
4. **만 14세 확인을 동의 항목(`AGE_OVER_14`)으로 저장** → **추천안대로 유지**. 설계 변경 2로 Cowork 반영
5. **요청 취소된 차수의 미제출 첨부** → **추천안대로 그대로 둠** — 취급자가 올린 자료도 그 시점 소명 과정의 기록이고, 첨부는 차수당 3개라 쌓이는 양이 작음. 보관 기간은 파기 배치(v0.2)에서 접속기록·탐지건 보관 정책(1년)과 함께 정함
6. **처리방침·이용약관·동의서 문안** — Cowork 기획 방에서 작성 필요(지금 자리표시). 처리방침에 적은 항목(주문·환불계좌·문의·PG 위탁)과 실제 저장이 일치하는지가 실습 포인트
7. 알려진 한계(보안성 검토 후보): 회원가입 409로 이메일 존재 여부 노출(이메일 인증 없음), 고객 로그아웃 후 토큰 유효(관리자와 같음), 첨부 백신 검사 없음, 개발 스택에선 `qa_web`·`qa-buyer@example.com`이 점검용으로 남아 있음(qa_web은 TERMINATED)
- **v0.2 후보**: 주문 취소·환불 처리, 문의 답변 수정(이력), 접속기록 검색 결과에 티켓 표시, 첨부 백신 연동, 고객 비밀번호 찾기·이메일 인증, 첨부 볼륨 백업

**다음 할 일**
- PR #29~#33 리뷰·머지(순서대로) → **Cowork 동기화**(위 설계 변경 + 이전 세션 누적분)
- **보안성 검토 단계 이월 목록**(이 세션분): 분리보관 연락처 범위, 분리보관 접근 권한 분리, 회원가입 409 이메일 존재 노출, 고객 로그아웃 후 토큰 유효, 첨부 백신 검사
- 기능 레이어 8(2티어) 착수 전 계획 설명 — 지난 세션 메모(내부 담당자 요청에 따른 대량 조회·수정) 포함

---

## 2026-10-01 — 기능 레이어 7 재설계 논의 + 관리자 화면 /admin 이동(진행 중) — **세션 인계**

> 이 세션은 대화가 길어져 **구현은 새 세션에서** 이어가기로 함(사용자 결정). 아래 "결정사항"이 다음 작업의 기준이다.

**한 일**
- PR #28(룰 빌더) 머지 확인, main 최신화. 사용자가 개발 스택에서 룰 변경을 직접 테스트(대량 다운로드 50 → 10건): 같은 날 진행 중인 탐지건에 붙는 동작·종결 뒤 새 건 생성·5분 순찰 대기를 확인 → v0.2 후보 2건 기록(PR #28에 포함)
- 기능 레이어 7(플랫폼 무대장치) 설계 논의 — 아래 결정사항으로 **범위·순서 재구성**
- **PR A 진행 중** (브랜치 `feat/admin-path`): 플랫폼 관리자 화면을 `/admin` 아래로 이동(`app/login` → `app/admin/login`, `app/members` → `app/admin/members`), 경로 참조 4곳·README 주소 수정. platform-web lint·typecheck·build 통과. **브라우저 확인·E2E는 아직** → 다음 세션에서 확인 후 PR

**결정사항** (사용자와 논의해 확정 — 다음 세션의 기준)

1. **기능 레이어 7 순서 재구성**: **고객 화면 최소판(신설) → ① 주문·결제 → ② 1:1 문의 → ③ 소명 첨부**. PR은 각각 따로(고객 화면은 PR A 화면 구조 이동 + PR B 고객 기능)
   - 고객 화면을 먼저 하는 이유: 고객이 결제·문의를 남기는 흐름이 생겨야 ①②가 "실제 흐름에서 생긴 데이터"를 다룸. 시연용 양(회원 500명 등)은 계속 시드

2. **고객 화면 최소판 — 넣는다** (사용자: 처음부터 원했음. 보안성 검토·ISMS-P 3번 영역(처리 단계별 요구사항) 실습과 개인정보 처리방침·동의서 포트폴리오에 필요. 요구사항 PLT-01~03도 원래 Must)
   - 넣을 것: **회원가입**(필수·선택 동의 분리 — 이용약관·개인정보 수집이용 필수 / 마케팅 수신 선택, **만 14세 이상 확인**, 동의 이력은 `member_consent`에 항목·버전·시각·IP), **로그인·로그아웃**, **마이페이지**(내 정보 열람·정정, 비밀번호 변경, 선택 동의 철회·재동의, **탈퇴**), **개인정보 처리방침·이용약관 페이지**, 주문·환불계좌 등록(①), 1:1 문의 작성(②)
   - 뺄 것(v0.2 이후): 비밀번호 찾기, 이메일·휴대폰 인증, 실제 PG 결제, 소셜 로그인. 필요한 것은 추후 덧붙임
   - 처리방침·이용약관·동의서 **문안은 Cowork(기획 방)에서 작성**해 넘겨받아 화면에 붙임. 그전까지 자리표시 문안
   - 기존 가상 회원 500명에게도 동의 이력 시드(필수 동의 + 일부 마케팅)
   - 고객 행위는 Argus 접속기록 대상 아님(절대 규칙 #4) — Argus 시연용이 아니라 **개인정보보호 실습 무대**

3. **화면 구조**: platform-web 한 앱에서 **고객 `/`**, **관리자 `/admin`** (Caddy가 `/admin` 경로를 허용 IP로 제한 — architecture 7-2와 일치). 기각: 고객용 앱 분리 — 컨테이너 증가

4. **고객·관리자 인증 분리** (사용자 질문에서 정리): 권한(role)으로 나누는 게 아니라 **계정 체계 자체가 다름** — 고객 `member` / 관리자 `operator`, 로그인 입구(`/api/shop/auth/login` 예정 / `/api/admin/auth/login`)와 **쿠키**(`customer_session` 신설 / `platform_session`)가 다름. 판단은 항상 서버
   - **토큰 바꿔치기 방지**: 같은 키로 서명하고 id만 넣으면 고객 3번 토큰이 관리자 3번으로 통과할 수 있음 → 토큰에 **audience(고객용/관리자용)** 를 넣고 검증. 테스트로 고정할 것
   - 관리자 내부의 세부 권한 차등(PLT-14)은 이번 범위 아님

5. **고객 로그인 실패**: **5회 실패 → 15분 잠금(자동 해제)** — 비밀번호 찾기가 없어 영구 잠금은 불가. `member`에 실패 횟수·잠금 해제 시각 컬럼 2개 추가(작은 설계 변경). 보안성 검토 때 재확인

6. **탈퇴 = 즉시 파기** — 탈퇴 처리 트랜잭션에서 `member` 행·동의 이력을 **실제 삭제**. 법정 보존 대상(주문 기록 5년 등)의 분리보관(`retained_member_record`)은 ①에서 주문이 생길 때 붙임. 기각: 상태만 바꾸고 파기 배치(v0.2)를 기다림 — v0.1에서 탈퇴 회원 정보가 계속 남아 PIPA §21(지체 없이 파기) 위반 상태. 보안성 검토 때 재확인

7. **카드·계좌 (① 주문·결제)**
   - **카드: PG 목업 화면만** — 결제 기능은 구현하지 않고 흉내 화면. 플랫폼은 **카드번호를 받지도 저장하지도 않음**, **끝 4자리도 저장 안 함**(카드 등록·간편결제 기능이 없어 쓸 목적이 없음 — 최소수집). 저장: 결제수단 종류, 카드사, **PG 거래번호**, 승인 시각, 금액
   - 근거(사용자 질문에서 정리): 실무 커머스는 PCI DSS·여신전문금융업법 때문에 카드번호를 직접 저장하지 않고 PG에 맡김. SSG·무신사 처리방침에 카드번호가 나오는 이유는 ① 자체 간편결제(SSGPAY·무신사페이) 운영 ② 처리방침의 수집 항목은 "저장"이 아니라 "처리" 기준(PG 수탁) ③ 보수적 기재
   - **계좌: 환불계좌** — 마이페이지에서 등록, **AES-GCM 앱 레벨 암호화** 저장(절대 규칙 #11, 키 `PAYMENT_ENCRYPTION_KEY`). 관리자 화면은 기본 끝 4자리, **"전체 보기" 별도 동작 → 데이터 유형 "결제수단"으로 기록 → 결제수단 조회 룰(상)로 항상 탐지**
   - 처리방침 실습 포인트: 수집 항목에 "결제정보 — PG 결제창에서 입력·PG사 처리, 회사는 카드번호 저장 안 함", 처리위탁에 "(가상) PG사 — 결제 처리". **처리방침 기재와 실제 저장의 일치**를 보여 줌
   - 기각: 설계 PLT-05 그대로(카드번호 직접 저장·암호화) — 보안성 검토에서 수집 최소화 위반으로 지적받을 구조

8. **1:1 문의 (②)**: **고객 → CS 문의**(설계 그대로, A안). 3티어에서는 CS의 고객 조회(야간·주말 등)의 근거로 문의가 바로 연결됨(문의에 회원번호). 기각: 내부 업무 요청 게시판으로 변경 — CS 일상 업무 맥락(PLT-17) 상실 / 둘 다 — 범위 과다

9. **소명 근거 = 텍스트(필수) + 첨부(선택) + 문의 번호 언급**
   - 내부 업무 요청이 근거면 **결재 문서 PDF·지라 티켓 캡처·요청 메일**을 첨부 — 별도 요청 시스템 없이 외부 근거로 소명
   - 소명과 티켓 **자동 대조**는 스트레치 — v0.2에서 **내부 업무 요청 티켓**과 함께 설계

10. **첨부 규칙 (③)**: PNG·JPG·PDF만, 파일당 5MB, 소명 차수당 최대 3개, Argus 전용 볼륨, 파일 이름 불신(경로 조작 방지), 제출 시 SHA-256, 다운로드는 담당자·본인만 + **다운로드도 Argus 자체 접속기록**(캡처에 개인정보가 있을 수 있음)

11. **2티어 메모** (사용자 실무 경험): DB 직접 접근에서는 대량 다운로드 외에도 **내부 담당자 요청에 따른 대량 조회·수정**이 많았음 — 3티어(지금)와 소명 근거의 성격이 다름. **기능 레이어 8(2티어) 착수 때 논의**

**설계 변경** (Cowork 반영 요청 — 이전 세션분과 함께 동기화)
1. **CLAUDE.md 6절 기능 레이어 순서**: 7을 "고객 화면 최소판 → 주문·결제 → 1:1 문의 → 소명 첨부"로, 고객 화면 범위(위 2) 명시. (기존 반영 대기: 2 언마스킹 보류, 5 해체)
2. **requirements PLT-05**: "PG 분리 없이 카드번호 직접 저장" → **카드는 PG 목업(번호·끝 4자리 미저장), 환불계좌만 직접 수집·암호화**
3. **db-schema 4절**: `payment_method` 재설계(카드 번호 컬럼 제거 → PG 거래 정보 / 환불계좌 암호화), `member`에 로그인 실패 횟수·잠금 해제 시각
4. **db-schema 4-1 탈퇴 절차**: 파기 배치 대신 **탈퇴 즉시 파기**(v0.1). 법정 보존 분리보관은 주문 기능과 함께
5. **policy 4-3**: 고객 로그인 실패 정책(5회 → 15분 자동 해제)과 고객·관리자 토큰 audience 분리

**미결·이슈**
- PR A 브라우저 확인·E2E 미실시(빌드만 통과) — 다음 세션 첫 작업
- 처리방침·이용약관·동의서 문안 — Cowork에서 작성 필요(고객 화면 구현 중 자리표시로 시작 가능)
- 언어: 이 세션에서 응답이 여러 번 영어로 바뀌어 사용자가 지적 — 모든 응답·진행 메시지·문서를 한국어로

**다음 할 일 (새 세션)**
1. 이 로그와 CLAUDE.md를 읽고 시작. `feat/admin-path` 브랜치 이어서: 개발 스택 재빌드 → 브라우저로 `/admin/login`·`/admin/members`·CSV 다운로드 확인 → E2E → PR A
2. PR B: 고객 화면 최소판(위 결정 2~6)
3. 기능 레이어 7 ①②③ 순서대로
4. 적당한 시점에 Cowork 동기화(설계 변경 누적분 — 기능 레이어 1·3·4·5·6·7, 언마스킹 보류)

---

## 2026-10-01 — 기능 레이어 6 (룰 빌더) + 기능 레이어 5 범위 조정

**한 일**
- PR #27 머지 확인. **기능 레이어 4 설계 변경 1·2(집계 윈도우 1회 판단, `min_baseline`) 사용자 확인 완료** — 세부 수치는 룰 빌더에서 조정
- **기능 레이어 5 범위 조정** (사용자 결정, 아래 설계 변경 1)
- **룰 빌더 논의 → 확정**(사용자와): 생성·수정·켜기/끄기·변경 이력, UI 폼(JSON 편집 없음), 복제·미리보기·사유 입력 없음, 담당자 전원, **삭제 없음 — 끄기 + 켜짐/꺼짐 필터**
- **API** `/api/rules` (`app/rules/router.py`, 담당자 전용)
  - 목록(켜짐/꺼짐 필터, 룰별 탐지건 수, 최근 변경자), 상세 + 변경 이력, 생성(201), 수정(유형은 고정), 켜기/끄기
  - 저장 시 `validate_rule`(탐지 배치와 같은 검사기) — 해석 불가 룰은 **400 INVALID_RULE**로 저장 거부(켜진 룰 하나가 순찰을 멈추는 일을 입구에서 차단). 켤 때도 다시 검사
  - 동시 수정 방지: `expected_version` — 행 잠금 후 다르면 **409 VERSION_CONFLICT**. 같은 이름 409. 바뀐 것이 없으면 버전·이력 그대로
  - 매 변경마다 version +1, `detection_rule_history`에 변경 후 룰 전체 + 누가·언제
  - 룰 화면은 정보주체가 없어 Argus 자체 접속기록 대상 아님(`access_log_exempt`) — 변경은 룰 이력에
- **마이그레이션 0008**: `detection_rule_history`(앱 계정 **추가·조회만**, `(rule_id, version)` 유일), 시드 룰 6개 CREATE 이력 소급(변경자 NULL = 시스템). 앱 계정엔 원래 `detection_rule` 삭제 권한 없음(0002) — "삭제 없음"이 DB 권한과 일치
- `aggregate` 컬럼을 `JSONB(none_as_null=True)`로 — None이 JSON `null`로 저장돼 CHECK(EVENT ↔ aggregate IS NULL)에 걸리던 문제(기능 레이어 4 테스트에서 먼저 겪음)를 모델에서 해결
- **화면** argus-web: 담당자 메뉴 "룰", `/rules`(켜짐 기본·꺼짐·전체 탭, 조건 요약 문구, 심각도 색, 버전, 탐지건 수, 최근 변경), `/rules/new`, `/rules/[id]`(수정 폼·켜기/끄기·변경 이력)
  - 조건 입력: `[필드 ▼]` + 필드별 입력칸 — 수행업무·데이터 유형·요일은 체크박스(하나면 `eq`, 여럿이면 `in`으로 저장 — 담당자에게 연산자를 드러내지 않음), 처리 건수는 숫자 + 이상/이하/같음, 시각은 시작·끝(자정 넘김 안내), 퇴직 여부는 고정 문구. "모두 만족 / 하나라도 만족"
  - 집계: 기준(정해진 값 / 전월 같은 기간 대비 배율 — 고르면 구간이 한 달로 고정), 구간, 무엇을 셀지, 기준값, 기준선 최소 건수
  - 화면은 한 묶음 조건만 다룸(엔진의 1단계 중첩은 화면 편집 대상 아님 — 해당 룰은 안내 문구)
- **심각도 색**(LOG-11 일부): 상 빨강·중 노랑·하 회색 — 탐지건 목록·룰 화면
- 테스트 22개(목록·필터, 이력 최신순, 생성·형식 오류 4종 거부·중복 이름, 수정·버전·이력, 두 담당자 동시 수정 409, 유형 변경 불가, 변경 없는 저장, 켜기/끄기 이력, 취급자 5개 경로 403, 자체 접속기록 미기록, 미인증 401, **고친 기준(50 → 30)이 다음 순찰에 적용되고 탐지건에 새 버전·사본**) → argus-api **318 passed**. argus-web lint·typecheck·build 통과
- 브라우저 확인(개발 스택, 점검용 임시 담당자 — 확인 후 DISABLED): 목록 6개·심각도 색·대량 다운로드 탐지건 4, **화면으로 "쪼개기 다운로드"(다운로드 × 하루 × 처리 건수 합 200, 상) 생성 → 기준값 150 수정 → 끄기**, 이력 v1 생성 / v2 수정 / v3 끄기(누가·언제). 개발 DB에 룰 #7로 **꺼진 상태**로 남음(삭제 없음 원칙)

**결정사항**
- **사유 입력 없음**(사용자) — 이력의 변경 후 스냅숏 + 누가·언제로 추적. 기각: 사유 필수 — 통제 약화 증적엔 좋지만 운영 부담, 단순함 우선
- **복제·미리보기 없음**(사용자, "꼭 필요하지 않으면 붙이지 않는다") — 미리보기는 v0.2 후보
- **권한은 담당자 전원**(사용자) — 룰 관리 전용 권한은 담당자가 여럿일 때. 기각: 별도 역할 — 역할 체계 변경
- **삭제 없음 — 끄기 + 필터**(사용자와 논의) — 탐지건이 룰을 참조하고 "그 시점에 운영한 룰"이 점검 근거. 기각: 보관(아카이브) 상태 — 상태 하나 증가, 미사용 룰만 실제 삭제 — 분기·이력 처리 복잡. 룰이 수십 개로 늘면 보관 상태 검토
- 켜기/끄기도 version +1 — 이력의 (rule_id, version)을 하나의 축으로. 탐지건의 `rule_version`은 탐지 당시 버전이라 켜기/끄기로도 늘지만 판단 근거는 사본(`rule_snapshot`)에 있음
- 룰 유형(단건/집계)은 만든 뒤 고정 — 바꾸려면 새 룰. 유형이 바뀌면 같은 룰의 과거 탐지건과 의미가 달라짐
- 고친 룰은 다음 순찰부터, 과거 기록 재판정 없음(화면에 안내) — 기준을 낮췄다고 1년치가 쏟아지지 않게
- "민감 개인정보 조회" 룰(사용자 예시)은 플랫폼 Agent가 그 데이터를 별도 데이터 유형으로 기록해야 가능 — 기능 레이어 7에서 연결

**설계 변경** (Cowork 반영 요청)
1. **기능 레이어 5 해체** (사용자 결정): 룰 변경 이력(LOG-13)은 **6(룰 빌더)으로 합침** — 룰을 바꾸는 기능이 생길 때 이력이 의미 있음 / **화이트리스트(LOG-12)는 v0.2** — 예외가 필요한 대상(시스템 계정·배치)이 아직 없음(파기 배치 v0.2). 다시 꺼낼 때의 안: 만료일 필수(최대 1년), 삭제 대신 조기 종료 + 종료자 기록, 행위 시점 기준 적용 / **심각도 표시(LOG-11)는 색 구분만** 6에서. 영향 문서: CLAUDE.md 6절 기능 레이어 순서, requirements 5-2
2. **룰 삭제 없음 — 끄기와 목록 필터로 관리**: 영향 문서: actor-flows F-05 #10("룰 관리"), db-schema 3-3(룰 변경 이력 — 사유 없음, `(rule_id, version)` 유일, 앱 계정 추가·조회만)
3. **사유 없는 변경 이력**: db-schema 3-3 `detection_rule_history` 그대로(사유 컬럼 추가 안 함) — 기록용

**미결·이슈**
- **v0.2 후보** — 룰 미리보기("지난 7일에 적용했다면 몇 건"), 보관(아카이브) 상태, 룰 관리 전용 권한, 화면에서 중첩 조건 편집, `actor_team` 조건
- **v0.2 후보 (사용자 테스트에서 발견, 2026-10-01)** — **진행 중 탐지건에 룰 버전이 섞임**: 대량 다운로드를 50 → 10건으로 낮춘 뒤 mkt_lee의 11건 다운로드가 그날 이미 열려 있던 탐지건 #4(v1 "50명 이상"으로 생성)에 붙음 — 상세의 룰 설명은 50명인데 근거엔 10명짜리 기록. 선택지: A 그대로(하루 1건 원칙 유지, 현재) / B 룰 버전이 바뀌면 새 건(그룹 키·부분 유니크 인덱스 변경). 사용자 판단: 크리티컬하지 않음 → 개선 건. 최소 개선안: 상세에서 기록별 "판정한 룰 버전" 표시
- **v0.2 후보 (사용자 테스트)** — 룰을 고친 뒤 확인하려면 순찰(5분)을 기다리거나 `worker --once`를 실행해야 함 — 담당자 화면에 "지금 순찰" 버튼 검토(순찰 동시 실행은 advisory lock이 이미 막음)
- 탐지 배치는 순찰마다 켜진 룰을 다시 읽음 — 저장 직후 진행 중이던 순찰은 이전 룰로 끝남(다음 순찰부터라는 안내와 일치)

**다음 할 일**
- PR 리뷰·머지 → **Cowork 동기화**(기능 레이어 1·3·4·5·6 설계 변경, 언마스킹 보류)
- 기능 레이어 7: 플랫폼 무대장치(결제·주문·1:1 문의) + 결제수단 조회 룰 + 소명 근거자료 첨부 — **큰 항목: 착수 전 계획 설명**

---

## 2026-10-01 — 기능 레이어 4 (AGGREGATE 룰: 대량 조회·전월 대비 급증, 기준선 시드)

> 사용자가 자리를 비운 동안 "확인 후 구현 진행" 지시로 진행. 설계에 답이 없던 2건은 되돌리기 쉬운 쪽으로
> 정하고 **확인 대기**로 표시했다(아래 설계 변경 1·2) — PR 리뷰에서 확인받는다.

**한 일**
- PR #26 머지 확인, main 최신화·브랜치 정리 (Cowork 사본은 아직 없음 — 지금 문서 기준으로 진행)
- **룰 엔진** (`rules.py`): AGGREGATE 집계 스펙 형식 검사 — window(1h·1d·1mo), measure(LOG_COUNT·SUBJECT_COUNT·DISTINCT_SUBJECT), compare(ABSOLUTE: 양의 정수 기준 / RATIO_TO_BASELINE: 기준선 PREV_MONTH_SAME_PERIOD·월 윈도우·양수 배율), 키 집합 정확히 일치, EVENT 룰에 집계 스펙 금지
- **윈도우·기준선** (`aggregate.py` 신설): 한국 시각 **고정 구간**(매시 정각·0시·1일), 그룹 키 = 윈도우 시작 시각(`2026-09-15T14:00+09:00`), 전월 동기 = 전월 1일부터 같은 경과 시간(전월이 짧으면 전월 말에서 자름), 집계값 3종
- **탐지 배치**: 범위의 기록으로 "다시 볼 (취급자, 윈도우)"를 고르고, 원장에서 **윈도우를 통째로** 다시 집계(순찰 경계에서 윈도우가 쪼개지지 않게, 늦게 도착한 기록 포함, 이번 순찰이 본 id까지만). 기준 초과 시 탐지건 생성·하위 기록 추가·`aggregate_value` 갱신, 탐지 이력에 근거("집계 100 ≥ 기준 100", "당월 63 / 전월 동기 23 = 2.74배 ≥ 2.0배")
- **마이그레이션 0007**: 대량 조회(1h, LOG_COUNT ≥ 100, HIGH), 전월 대비 급증(1mo, 전월 동기 × 2.0, `min_baseline` 20, MEDIUM) — 조건은 둘 다 `action = READ`
- **API·화면**: 탐지건에 `aggregate_value`, 상세의 룰에 `rule_type`·`aggregate`. 상세 요약에 "집계 구간 시작"·"집계값(142건 (기준 100건) / 전월 같은 기간의 2.74배 (기준 2배))", 목록의 윈도우 시각 표기 정리
- **기준선 시드** (platform-api `app/scripts/baseline.py`): 플랫폼 시드가 **지난달 1일 ~ 지금**의 가상 회원 목록 조회 기록을 **outbox(ACCESS_LOG)** 에 넣음 → relay → 수집 API(서명·검증·해시체인 append) — 절대 규칙 #5. 평일 09:00~17:59만, 하루 횟수 고정(지난달 5건 / 이번 달 3건, `cs_kim`만 15건 — 급증 시연), 취급자 동기화(HANDLER)가 먼저 나감
- **테스트**: argus-api 296 passed(집계 스펙 형식 18, 윈도우·기준선 8, 배치 — 정각 경계 99/100, 두 시간에 걸친 120건 미탐지, 순찰 경계 재집계, 진행 중 건에 늦은 기록·값 갱신, 종결 윈도우 재탐지 없음, 7·8월 2.74배 탐지/1.83배 미탐지, 기준선 19건 미판정), platform-api 110 passed(기준선 모양·월별 건수·시간당 100건 미만, **2026년 매일 08·13·19시에 시드해도 평소 취급자는 2배 미만**, 급증 취급자는 2배 이상, HANDLER 먼저), argus-web lint·typecheck·build 통과
- **E2E**: 기준선 기록이 수집 API를 거쳐 원장에 실제 도착했는지 확인하는 테스트 추가(담당자 검색 API로 지난달 ops_park 조회 건수 = 평일 × 5) — relay가 형식 오류로 DEAD 처리하면 조용히 사라지기 때문
- **로컬 E2E 4 passed** + 남긴 스택 점검: 기준선 577건(9/1 09:12 ~ 10/1 17:50 KST)이 수집 API로 원장 도착, outbox 비어 있음(전부 전송), **시드로 생긴 탐지건 0건**(탐지건은 E2E 시나리오의 2건뿐 — 야간·주말·대량 조회·급증 어디에도 안 걸림, 10/1이라 급증은 기준선 20건 미만으로 미판정)
- README: 기본 룰 6개, 기준선 시드와 cs_kim 급증 시연 안내

**결정사항**
- 윈도우는 **고정 구간**(매시 정각 등) — 정책 예시 "cs_kim의 9/15 14:00~15:00 대량조회 = 1건"과 그룹 키 설계(윈도우 시작 시각)를 따름. 기각: 슬라이딩 윈도우 — 14:30~15:29의 120건도 잡지만 탐지건 단위가 정해지지 않고 같은 행위가 여러 건으로 겹침(대신 정각 경계에 걸친 몰아치기는 놓침 — 테스트로 명시)
- 전월 대비 급증의 기준 시각은 **지금**(이번 달) / **윈도우 끝**(지난 달 이전) — "당월 누적 ≥ 전월 동기 × 2"를 그대로
- 대량 조회의 집계 대상은 설계대로 **조회 기록 수(LOG_COUNT)** — 목록 한 화면이 기록 1건(20명)이라 "100번 조회"를 뜻함. 처리 건수 합(SUBJECT_COUNT)으로 바꿀지는 룰 빌더 때 담당자 판단
- 기준선 시드는 **고정 패턴** — 무작위 횟수·시각이면 시드 자체가 야간·주말·대량 조회 룰에 걸리거나, 월초 비율이 우연히 2배를 넘음
- 시드 기록에는 "시드" 표시를 남기지 않음 — 수집 API의 `context` 키가 정해져 있고(api-spec 2-4), 모든 데이터가 가상임은 README에 명시

**설계 변경** (확인 대기 — 사용자 부재 중 결정, Cowork 반영 요청)
1. **AGGREGATE 윈도우는 한 번만 판단** — 무엇을: 같은 (룰, 취급자, 윈도우)의 탐지건이 **종결됐으면** 그 윈도우에 기록이 더 쌓여도 새 탐지건을 만들지 않음(진행 중이면 기록 추가·집계값 갱신). 왜: policy 2-3("종결 후엔 새 건")을 그대로 적용하면 집계는 누적이라 승인 뒤에도 같은 윈도우가 계속 기준을 넘어 **순찰(5분)마다 새 건**이 생김 — 특히 월 윈도우는 한 달 내내. 기각: ① 2-3 그대로 — 위 문제, ② 종결 뒤 새 기록만으로 다시 기준을 넘을 때 새 건 — 정확하지만 비율 룰에서 뜻이 모호. 한계: 승인 뒤 같은 시간·같은 달에 더 한 행위는 탐지되지 않음(접속기록 검색으로는 보임, 대량 다운로드 등 EVENT 룰은 그대로). 영향 문서: **policy 2-3**(AGGREGATE 예외), db-schema 3-7 #4
2. **전월 대비 비율 룰에 `min_baseline`(기준선 최소 건수) 추가, 기본 룰 값 20** — 무엇을: 집계 스펙에 선택 키 `min_baseline`, 전월 같은 기간 집계값이 이보다 작으면(0 포함) 판정하지 않음. 왜: 시드 1년치 검사에서 **9/3 13시** 시드 시 평범한 취급자가 2.33배로 탐지됨 — 전월 같은 기간(8/1 토·8/2 일·8/3 월 오전)의 기준선이 3건뿐이라 비율이 튐. 시드만의 문제가 아니라 **월초·휴일 직후의 실제 오탐** 원인이라 룰에 둠. 기준선 0은 비율 자체를 정할 수 없음(신규 취급자·첫 달). 기각: 시드 패턴만 조정 — 실제 데이터의 같은 문제가 남음, 기준선 0을 "판정 불가 → 탐지"(기능 레이어 1의 명부 미등록처럼) — 신규 취급자마다 첫 달 매번 탐지. 영향 문서: **db-schema 3-6 aggregate 키 표**·기본 룰 시드 표, policy 1-2 AGGREGATE 필드, policy 1-3 기본 룰 표

**미결·이슈**
- 월초에는 `cs_kim` 급증 탐지가 나오지 않음(기준선 20건 미만) — 시연은 월초 며칠이 지난 뒤. README에 안내
- 집계 윈도우를 원장에서 다시 읽으므로, 월 윈도우는 한 취급자의 한 달 조회 기록을 순찰마다 읽음 — 데이터가 커지면 윈도우별 누적값 테이블 검토(v0.2 후보)
- 기존 개발 스택은 시드가 이미 돼 있어 기준선이 들어가지 않음 — 보려면 `docker compose down -v` 후 재기동(README)

**다음 할 일**
- PR 리뷰 — **설계 변경 1·2 확인** → 머지 → Cowork 동기화(기능 레이어 1·3·4 설계 변경, 언마스킹 보류)
- 기능 레이어 5: 화이트리스트·심각도 표시·룰 변경 이력(LOG-11~13)

---

## 2026-10-01 — 기능 레이어 3 (접속기록 조회·검색) + 기능 레이어 2(언마스킹) 보류 결정

**한 일**
- PR #24·#25 머지 확인, main 최신화·브랜치 정리
- **기능 레이어 2(식별값 언마스킹) 논의 → Argus 쪽 해제는 v0.2 이후로 보류** (아래 설계 변경 1)
- **API** `POST /api/access-logs/search` (`app/access_logs/router.py`) — 담당자 전용
  - 조건: 계정·기간(한국 날짜, 양 끝 포함, 비우면 최근 7일, 최대 366일)·수행업무·정보주체(회원번호, `10293`·`member_10293` 둘 다)·접근 경로·출처(기본 PLATFORM)
  - 정보주체 검색은 `subject_ids @> text[]` — GIN 인덱스(ix_access_log_subject). 모델 컬럼이 `varchar[]`로 선언돼 있어 `text[]`로 맞춰 캐스팅(그대로면 연산자 없음 오류)
  - 결과: 기록별 마스킹 정보주체 **앞 5개만** + 처리 건수, 연결된 탐지건 번호, 취급자 이름(명부 조인). 최신순, 페이지당 최대 100
  - 자체 접속기록: READ(ACCESS_LOG), 정보주체는 화면에 보여 준 마스킹 값의 **수**만, 검색 조건은 **키 이름만**(`record_query_keys` 신설 — 본문 검색이라 미들웨어의 쿼리스트링 키 추출이 적용되지 않음). 취급자의 시도도 FAILURE로 남음
- **화면** argus-web `/access-logs`: 조건 입력 → 결과 표(발생 시각·출처·계정·접속지·수행업무·데이터·결과·기능·처리 건수·정보주체·탐지건 링크), 1,000명 초과 기록 안내 문구. 상단에 담당자 전용 메뉴(탐지건 / 접속기록) 추가. 조건은 화면 상태에만 두고 URL에 싣지 않음
- 테스트 15개(필터별, KST 날짜 경계, 출처 선택, 잘못된 조건 400 6종, 마스킹·앞 5개·원본 미노출, 탐지건 연결, 취급자 403 + 시도 기록, 조건 이름만 기록·검색어 값 미기록, 미인증 401) → argus-api **260 passed**, ruff 통과. argus-web eslint·typecheck·build 통과
- 브라우저 확인(개발 스택, 점검용 임시 담당자 — 확인 후 DISABLED): 메뉴, 기본 검색, `member_10050` 검색 → 7건 모두 마스킹 + 탐지건 링크. 원장의 검색 기록 = `{page,size,source,subject}` + 건수 35, `10050`은 Argus 자체 기록 어디에도 없음. 체인 정상(81건)

**결정사항**
- **탐지건 화면과 별도 메뉴**(사용자 확인) — 탐지건은 결재함, 접속기록은 원장 점검 도구로 하는 일이 다름. 기록 → 탐지건 링크로 연결
- **검색 조건은 POST 본문으로** — URL 쿼리에 회원번호를 넣으면 서버 접근 로그(uvicorn)·브라우저 방문 기록에 남음. 기각: GET + 접근 로그 쿼리 제거 — 브라우저 기록·프록시 로그는 막지 못함
- 결과의 정보주체는 기록당 앞 5개만 — 50행 × 최대 1,000개를 내려보내지 않음(최소 노출, 탐지건 상세와 같은 표시 규칙)
- 기록하는 조건 키는 화면이 항상 보내는 `page·size·source`도 포함 — "어떤 조건으로 찾았는가"의 일부라 그대로 둠
- **Argus 자체 기록 점검은 출처 필터로만**(사용자 결정, 추천안 A) — 전용 점검 기능·룰·보고서 항목은 만들지 않음. 기각: 검색에서도 완전 제외(B) — §8② 점검 수단이 아예 없어짐. 배경(사용자 실무 경험): 실무에서 점검 시스템 자체 접근 기록까지 정기 검토하지 않는 경우가 많음 — 법 요건(§8②, Argus도 개인정보처리시스템)과 관행 사이에서 "볼 수는 있다"만 남김

**설계 변경** (Cowork 반영 요청 — 이 작업 완료 후 동기화)
1. **기능 레이어 2(Argus 식별값 언마스킹) 보류 → v0.2 이후**, **플랫폼 개인정보 표시제한은 보안성 검토 후 조치 항목으로** (사용자 결정)
   - 왜: Argus는 절대 규칙 #3에 따라 회원 PK만 보유 — 해제해도 회원번호뿐이라 실무 가치가 제한적. 실제 개인정보(이름·이메일·전화·주소)는 플랫폼에 있고, 지금 플랫폼 회원 목록·CSV는 **마스킹 없이 표시** → 표시제한 통제는 플랫폼에 두는 게 맞음
   - 진행: v0.1 이후 보안성 검토·ISMS-P 점검에서 발견사항으로 다루고, 조치로 플랫폼 관리자 화면에 마스킹 + 사유 입력 해제를 구현 → 해제 행위가 Argus 원장에 남는 것으로 이행 증적
   - 그때 함께 바꿀 것: api-spec 2-3의 `UNMASK`는 **Argus 전용**(외부 출처 거부) — 플랫폼 출처 허용 또는 `READ` + 사유 기록으로 명세 변경 필요
   - Argus의 회원번호 **마스킹은 유지**. 기능 레이어 9(점검 보고서)는 v0.1에서 **마스킹 보고서만**(언마스킹 export·`EXPORT` 사유는 함께 보류)
   - 기각: Argus 해제 그대로 구현(2번 유지) — 작고 9번의 토대지만 실무 가치 낮음 / 3번과 순서만 교체 / 9번에 합치기 — 화면 해제 설계(F-05 3a)와 어긋남
   - 영향 문서: **CLAUDE.md 6절 기능 레이어 순서**(2 제거·v0.2 이후로), **requirements LOG-10**(해제 부분 보류), actor-flows F-05 3a, policy 6절·6-1, api-spec 2-3·2-7
2. (기록) Argus 자체 기록 점검 범위 — 출처 필터만 (위 결정사항). 영향 문서: 기능 레이어 1의 설계 변경 1("자체 기록 점검은 조회 화면·보고서에서")을 "조회 화면의 출처 필터로만"으로 좁힘

**미결·이슈**
- **알려진 미흡 사항 / 위험 수용** (보안성 검토에서 다룰 것): 플랫폼 관리자 화면(회원 목록)과 CSV 다운로드에 이름·이메일·전화·주소가 **마스킹 없이** 노출됨 — 개인정보 표시제한 미적용. 수용 사유: v0.1 범위는 Argus 기능 우선, 표시제한은 실제 데이터가 있는 플랫폼에 두는 것이 맞고 점검 → 조치 사이클로 진행하기로 함(사용자 결정). 조치 시점: v0.1 이후 보안성 검토 직후
- 검색 결과 건수(`count(*)`)는 1년치 원장에서 느려질 수 있음 — 데이터가 커지면 추정치·상한 검토(v0.2 후보)

**다음 할 일**
- PR 리뷰·머지 → **Cowork 동기화**(기능 레이어 1·3의 설계 변경, 언마스킹 보류)
- 기능 레이어 4: AGGREGATE 룰 2개(대량 조회·전월 대비 급증) + 기준선용 과거 접속기록 시드(수집 API 경유)

---

## 2026-10-01 — 기능 레이어 1 (EVENT 룰 3개: 야간·주말·퇴직자 계정 접속)

**한 일**
- PR #20·#21·#22·#23 머지 확인, main 최신화·머지된 브랜치 정리
- Cowork 사본 3개(CLAUDE.md 6절 기능 레이어 순서·게이트 A 통과, architecture 8-1·8-5 e2e job, docs/README) 구현 대조 → 불일치 없음 → **PR #24**. 6절의 README "구현 현황표"는 아직 없음 — 기능 동결 시 작성
- 사용자 제안("Must 남은 것보다 v0.1 제외 기능을 순차 구현")에 의견 제시(심사자용 README는 먼저, 시연 자료는 동결 직전) → Cowork에서 **기능 레이어 순서 1~9** 확정, 파기·배포·알림은 v0.2 이후
- **룰 엔진** (`app/detection/rules.py`)
  - 조건식 필드 3개: `occurred_time`(`between`, `["22:00","06:00"]`), `occurred_weekday`(`in`, MON~SUN), `actor_terminated_at_or_before`(`eq: true`)
  - `evaluation_facts(log, roster)`: 접속기록 컬럼 + 계산한 값(KST 시:분·요일, 행위 시점 퇴직 여부, 명부 등록 여부). 판정(`matches`)은 이 값만 본다 — 순수 함수라 시각 고정 단위 테스트 가능
  - 형식 검사: 시각은 `HH:MM` 0 채움·두 값·서로 달라야 함, 요일은 정해진 7개, 퇴직 조건 값은 `true`만
- **탐지 배치** (`app/detection/batch.py`): 범위의 행위자 명부를 순찰마다 한 번 읽음(`(출처, 계정) IN (...)`), Argus 자체 기록은 평가에서 제외(순찰 건수에는 포함 — 밀림 판단·실행 이력), 명부에 없는 계정을 퇴직자 룰로 탐지하면 상태 이력에 사유 기록
- **마이그레이션 0006**: 룰 3개 시드(야간 MEDIUM·주말 LOW·퇴직자 HIGH, 모두 APP, auto_request 기본 켜짐 — 퇴직자는 받을 계정이 없어 DETECTED로 남음)
- **테스트**: 단위(KST 변환, 시작 포함·끝 미포함, 자정 넘김, 요일, 퇴직 시각 경계, 명부 미등록, 형식 검사 9종) + 배치(야간 경계 6종, UTC↔KST 날짜·요일 차이, 퇴직 전 정상 접속 소급 탐지 없음, 명부 미등록 사유, Argus 자체 기록 제외) + 마이그레이션(시드 4개·해석 가능). argus-api **245 passed**, ruff 통과
  - 기존 테스트가 **실행 시각에 따라 깨지지 않게**: `reset_rules`(conftest) — 시드 상태로 되돌리고 테스트가 다루는 룰만 켬. 기존 배치·API 테스트는 대량 다운로드만 켠 상태로 시작
- **E2E**: 탐지건을 룰 이름("대량 다운로드")으로 고르도록 수정 — CI가 밤·주말에 돌면 같은 다운로드가 야간·주말 룰로도 탐지됨. 로컬 3 passed
- README: 기본 룰 4개 안내, 밤·주말에 따라 하면 탐지건이 더 생김, `detected=1 이상`
- 로컬 개발 스택에 0006 적용 → 룰 4개 확인, 체인 정상(75건)

**결정사항**
- 야간 구간은 **시작 포함·끝 미포함, 분 단위**(22:00:00 ~ 05:59:59) — 설계에 경계가 없어 정함. `["09:00","18:00"]`처럼 자정을 넘지 않는 구간도 같은 연산자로 표현
- 퇴직 조건의 값은 `true`만 허용 — `false`("퇴직하지 않았음")는 명부 미등록을 어떻게 볼지 모호해 받지 않음
- `actor_team`(db-schema 3-6)은 쓰는 룰이 없어 이번에 넣지 않음 — 룰 빌더(기능 레이어 6) 때 추가
- 심각도 표시 개선(중·하 배지 색)은 기능 레이어 5(심각도 표시)에서 — 지금은 "상"만 강조

**설계 변경** (사용자 결정 2건 — Cowork 반영 요청)
1. **Argus 자체 접속기록은 룰 평가 대상에서 제외** — 무엇을: 탐지 룰은 감시 대상 시스템(플랫폼) 기록에만 적용, 출처 ARGUS 기록은 원장에 남기기만(LOG-17). 왜: 그대로 두면 담당자가 밤에 검토할 때 담당자 본인 탐지건이 생기고(자기 점검·이해충돌), 취급자의 주말 소명 제출이 다시 탐지됨. 자체 기록 점검은 기능 레이어 3(접속기록 조회 화면)·9(점검 보고서)에서. 기각: 자체 기록에도 적용 — 위 문제, 룰에 출처 컬럼 추가 — 스키마 변경이 필요하고 지금은 쓸 곳이 없음. 영향 문서: **db-schema 3-7 #2**(평가 대상), **policy 1-2 또는 1-3**(룰 적용 대상)
2. **명부에 없는 계정 = 퇴직 여부 판정 불가 → 탐지(fail-closed)** — 무엇을: db-schema 3-6의 "미동기화면 평가 보류(다음 배치 재평가)" 대신, 퇴직자 계정 접속 룰로 **탐지**하고 상태 이력에 "취급자 명부에 없는 계정 — 퇴직 여부를 판정할 수 없어 탐지"를 남김. 왜: 배치는 `id` 커서로 지나간 기록을 다시 보지 않아 "보류"를 구현하려면 순찰 정지나 보류 테이블이 필요. relay가 명부를 기록보다 먼저 보내므로 정상이면 생기지 않고, 생겼다면 동기화 실패·명부 밖 계정이라 그 자체가 점검 대상. 오탐이면 담당자가 처리(받을 계정이 없어 DETECTED). 기각: A 순찰 정지 — 계정 하나가 끝내 동기화되지 않으면 **모든 룰의 탐지가 멈춤**, B 보류 목록 테이블 — 정확하지만 스키마 변경·작업량. 영향 문서: **db-schema 3-6 "취급자 속성 조건의 미동기화 처리"**, policy 1-3 퇴직자 룰 판정 기준. `actor_team` 등 이후 명부 조건도 같은 원칙을 따를지는 룰 빌더 때 확인

**미결·이슈**
- E2E는 실행 시각의 다운로드로 돌기 때문에 **야간·주말 룰은 E2E에서 실행 시각에 따라서만** 확인됨 — 판정 자체는 단위·배치 테스트가 고정 시각으로 검증
- 마이그레이션 0004·0006의 downgrade는 그 룰로 만든 탐지건이 있으면 외래키로 실패 — 시드 룰 되돌리기는 운영에서 쓰지 않는 경로라 그대로 둠

**다음 할 일**
- PR #24(설계 사본) 머지, 이 PR 리뷰·머지 → Cowork 동기화(설계 변경 2건)
- 기능 레이어 2: 식별값 언마스킹(LOG-10, policy 6절)

---

## 2026-10-01 — 마일스톤 M6 PR ② (README 로컬 실행·시나리오 따라하기, .env 생성 스크립트) — M6 완료

**한 일**
- PR #21 CI 통과 확인(e2e job 약 2분, 시나리오 테스트 8초), 러너 Compose 버전(v2.38.2) 확인 → PR ① 항목에 기록
- `scripts/init-env.sh`: `.env.example` → `.env`, change-me 칸만 `openssl rand -hex 32`로 채움. 값은 출력하지 않고, 이미 `.env`가 있으면 덮어쓰지 않음(기존 DB 비밀번호와 어긋나지 않게). **CI e2e job도 이 스크립트로 일회용 `.env` 생성** — README 경로가 CI에서 그대로 검증됨
- README 재구성 (Windows·Git Bash 기준)
  1. **처음 실행**: clone → `bash scripts/init-env.sh` → `docker compose up -d --build` → `docker compose ps`로 화면 healthy 확인 (`--wait` 제거 — PR ① CI 실패와 같은 이유)
  2. **화면으로 시나리오 따라하기**: ① Argus 계정 준비(담당자 생성, 취급자 비밀번호 — `winpty`) ② 플랫폼 CSV 다운로드(시드 비밀번호는 `grep`으로 확인) ③ 탐지 배치 즉시 1회 ④ 결재 흐름 표(일반 창 = 담당자, 시크릿 창 = 취급자, 5단계와 상태) + 오탐 요청 취소 갈림길, 다른 취급자로는 안 보임 ⑤ 해시체인 검증
  3. **자동 검증(E2E)**: `bash scripts/e2e.sh`, 확인 항목, `E2E_KEEP=1`
  - 서비스 표(화면 2개를 맨 위로), 계정 잠금 해제(플랫폼·Argus 모두), 단위·통합 테스트, 중지
- **README대로 새로 clone해 실측**: 임시 폴더에 브랜치를 clone → `init-env.sh`(Windows Git Bash에서 8칸 채움, 두 번째 실행은 거부) → 프로젝트 이름·포트만 바꿔(`argus-readme`, 2만번대) 기동 → 계정 준비 → 화면 서버 경유 120건 다운로드 → `worker --once` "detected=1" → **내장 브라우저로 Argus 화면에서 1~5단계 수행**(마스킹 `member_10***`·1차 요청자 "시스템(자동 요청)" 확인, 제출 → 반려 → 재요청(2차) → 재제출 → 승인, 상태 이력 7줄이 README 표와 일치) → 체인 검증 OK(24건) → 볼륨까지 삭제

**M6 완료 기준 대조** (CLAUDE.md 6절): 시나리오 자동 테스트 스크립트 ✅(PR ①) / 해시체인 검증 스크립트 ✅(PR ①) / README 로컬 실행법(Windows) ✅(이 PR)
**Skeleton 전체 완료 기준**: 새로 clone → README대로 기동 → 시나리오를 화면으로 끝까지 ✅(위 실측) / M6 테스트 CI 통과 ✅(PR #21) → **Walking Skeleton 완료** (PR #21·#22·이 PR 머지 시점)

**결정사항**
- `.env` 생성을 스크립트로 — 기각: README에 "change-me 8칸을 직접 바꾸기" 유지 — 처음 받은 사람이 가장 막히기 쉬운 단계이고, 손으로 넣으면 짧은 값·URL 특수문자(`@ : /`)로 기동 거부가 나기 쉬움. hex만 쓰므로 그 문제도 없음
- 실측에서 CSV 다운로드는 **버튼 대신 같은 요청을 화면 서버로 직접 보냄** — 브라우저 파일 다운로드는 사용자 허락이 필요한 동작이라. 버튼 자체는 M5에서 사용자가 브라우저로 확인함
- 실측에서 비밀번호 입력창(`winpty` 대화형) 대신 같은 명령의 `--password-env`를 사용 — 대화형 경로는 사용자가 직접 확인 필요(M4·M5에서 사용자가 이미 사용한 경로)

**설계 변경**
- 없음

**미결·이슈**
- **v0.1 Must 남은 항목**: 심사자용 README(프로젝트 소개·설계 포인트·보안 판단 근거 — 이번 README는 "실행법" 중심), 시연 자료(시나리오 GIF 또는 스크린샷)
- **게이트 A(Skeleton 완성 확인)**: 이 PR 머지로 완료 기준 충족 — 사용자 확인 후 여유 항목 ① EVENT 룰 3개로 진행 여부 판단
- relay·worker에 healthcheck가 없어 `up --wait`를 쓸 수 없음 — 하트비트 파일 등으로 healthcheck를 주는 방안은 **v0.2 후보**(운영 모니터링에도 도움)

**다음 할 일**
- PR #22(설계 사본) → #21(M6 ①) → 이 PR 머지 → **M6 완료 → Cowork 동기화**(architecture CI 표에 e2e job, Skeleton 완료)
- 게이트 A 확인 → v0.1 Must(심사자용 README·시연 자료)와 여유 항목 순서 결정

---

## 2026-10-01 — 마일스톤 M6 PR ① (해시체인 검증 명령어, 시나리오 E2E, CI e2e job)

**한 일**
- 새 세션 시작. Cowork 동기화로 받은 설계 사본 8개(M4·M5 반영: policy 3절 자동 소명 요청, architecture 화면 서버 비신뢰, CLAUDE.md 5·6절 등)를 **구현과 대조 → 별도 PR #22**로 분리. 대조 결과 불일치 없음(auto_request·requested_by NULL = 마이그레이션 0005 / 자동 요청 조건·메시지·이력 2줄 = batch.py / 요청 취소·404 → 403 → 409 = 전이 표·`_transition` / 취급자 round ≥ 1 = `_visible` / 퇴직 → DISABLED = handlers/sync.py / 화면 서버 비신뢰 = compose)
  - 처음에는 사본을 M6 브랜치에 커밋만 하고 대조를 빠뜨림 — 사용자가 Cowork 지시("구현과 대조한 뒤 docs: PR")를 다시 확인해 줘서 바로잡음. M6 브랜치에서는 해당 커밋을 rebase로 빼고 force-with-lease로 push(이 세션에서 만든 브랜치)
- Dependabot 확인: #17은 닫혔고, 같은 메이저 안 갱신(react·react-dom 19.2.8 → 19.3.0, 웹 2개)만 담은 **#20**이 새로 열림(CI 전부 통과) — 사용자 머지 대기
- **해시체인 검증 명령어** `python -m app.scripts.verify_chain` (argus-api): M1의 `verify_chain`에 실행 입구만 붙임. 종료 코드 0 정상 / 1 끊김(위치·사유, 그 앞 정상 건수) / 2 실행 오류. 앱 계정(원장 SELECT만)으로 실행, 서버 측 커서(1,000건씩)로 1년치 원장도 메모리에 한 번에 올리지 않음. 테스트 4개(정상·빈 원장·변조 위치·DB 오류 시 비밀번호 미출력). 로컬 원장 75건 OK
- **시나리오 E2E** (`e2e/`, pytest)
  - `test_bulk_download_to_approval`: ops_park 120건 CSV → relay → 수집 → 탐지 + 자동 소명 요청(요청자 시스템) → 담당자 상세(120명 모두 `member_10***`) → 취급자 본인 건 → 제출 → 반려 → 재요청(2차) → 재제출 → 승인, 최종 차수별 소명·상태 이력 7단계 전체 대조
  - 시나리오 중간의 보안 확인: 응답에 원본 회원 PK 없음(#8), CSV의 이름·이메일·전화가 Argus 응답 어디에도 없음(#3), 다른 취급자(cs_kim)는 목록에 없고 상세·제출 **404**, 취급자 승인 시도 **403**, 중복 제출·종결 후 반려 **409**, 사유 없는 반려 400
  - `test_false_positive_request_is_cancelled`: 시나리오 갈림길 — mkt_lee 60건 → 담당자 요청 취소(사유 필수) → DISMISSED → 취급자 제출 409
  - `test_ledger_hash_chain_is_intact`: 시나리오 뒤 원장(플랫폼 + Argus 자체 접속기록) 체인 검증
- `infra/docker-compose.e2e.yml`(덧붙임 파일) + `scripts/e2e.sh`: 별도 프로젝트 `argus-e2e`로 새로 기동 → e2e 컨테이너에서 pytest → 볼륨까지 정리(실패 시 컨테이너 로그 출력, `E2E_KEEP=1`이면 남김)
- CI `e2e` job: 실행마다 `.env.example`의 change-me 칸을 무작위 값으로 채운 일회용 `.env` → 같은 스크립트 실행. `e2e/` ruff 린트
- 검증: 로컬 `bash scripts/e2e.sh` 3 passed(시나리오 자체 약 11초), 실행 후 argus-e2e 컨테이너·볼륨 없음, 개발 스택(argus) 영향 없음

**결정사항**
- **E2E는 API 수준**(사용자 확인): 화면 버튼이 부르는 API를 같은 경로(**화면 서버 Next.js rewrite 경유**)로 호출 — 기각: 브라우저 자동화(Playwright) — 무겁고 화면 문구·배치 변경에 자주 깨짐, 화면 자체는 M5에서 사람이 확인했고 v0.1 시연 자료 제작 때 다시 확인
- **사람 역할 = HTTP, 운영자 역할 = README의 관리 명령 그대로**(담당자 계정 발급 `users create-officer`, 취급자 비밀번호 `users set-password`, 탐지 배치 `app.worker --once`, `verify_chain`) — 그래서 e2e 컨테이너는 argus-api dev 이미지(관리 명령 + pytest)를 쓰고 argus-api와 같은 앱 계정을 받는다. 두 화면을 모두 부르므로 두 네트워크에 붙음(테스트 전용 profile)
- **별도 compose 프로젝트로 격리** — 기각: 개발 스택에 그대로 실행 — M5 때 사용자가 만든 ops_park 비밀번호를 덮어쓰고, 진행 중인 ops_park 탐지건이 있으면 새 다운로드가 그 건에 합쳐져(그룹 키 = 룰·출처·행위자·날짜) 시나리오가 재현되지 않음. 매번 빈 DB에서 출발하므로 로컬·CI 결과가 같음. 호스트 포트는 `!reset`으로 모두 닫아 개발 스택과 충돌 없음
- 담당자 계정·취급자 비밀번호는 매 실행 무작위 생성(레포·CI 설정에 비밀번호 없음). CI의 `.env`도 실행마다 생성·폐기
- 세션 쿠키가 `Secure`라 HTTP 클라이언트의 쿠키 저장소는 `http://`로 보내지 않음(브라우저는 localhost만 예외) → E2E가 쿠키를 직접 들고 다니며 매 응답에서 갱신(30분 슬라이딩 재발급)
- relay(2초 주기)·탐지 배치는 비동기 → "배치 1회 실행 후 목록 확인"을 최대 90초 반복. 다른 순찰과 겹쳐 건너뛴 경우(skipped)는 실패로 보지 않음

**설계 변경**
- 없음 — CI에 e2e job 추가는 CLAUDE.md 6절 M6 완료 기준("M6 테스트가 CI에서 통과")의 구현. 단 architecture 8-1·8-5의 CI 구성 표에는 e2e job이 없음 → **Cowork 동기화 시 반영 요청**(PR 검사 ①에 "Skeleton 시나리오 E2E — 전체 스택 기동 + 해시체인 검증")

**미결·이슈**
- **CI 첫 실행 실패와 조치**: `docker compose up --wait`가 healthcheck를 일부러 끈 `argus-worker`·`platform-relay`에서 "no healthcheck configured"로 중단. 로컬 Compose(v5.5.1)는 통과시켜 로컬에선 재현되지 않았음(**확인: 러너 Compose v2.38.2 vs 로컬 v5.5.1** — CI에 버전 출력을 넣어 확인) → `--wait` 제거. 화면 서버 healthy 대기는 e2e 서비스의 `depends_on`이 이미 맡고, relay·worker는 실행만 되면 테스트가 최대 90초 기다림
  - README의 로컬 실행법도 `--wait`를 쓴다 — 사용자 PC(Docker Desktop 최신)에선 동작하지만 Compose 버전에 따라 같은 오류가 날 수 있음 → **PR ② README에서 함께 정리**
- e2e job은 이미지 4개를 매번 새로 빌드해 CI 시간이 가장 긴 job — 느려지면 빌드 캐시(GitHub Actions cache) 도입 검토
- e2e 린트의 ruff 버전(0.16.9)을 CI에 직접 적어 Dependabot이 갱신하지 않음 — argus-api의 ruff를 올릴 때 함께 수정
- E2E 실행이 `argus-api:local` 등 공용 이미지 태그를 현재 소스로 다시 빌드 → 다른 브랜치의 개발 스택은 다음 `up --build` 때 자기 소스로 돌아감(영향 작음, README에 적을 필요는 없음)

**다음 할 일**
- PR ② README(심사자용 로컬 실행법 Windows, 화면으로 시나리오 따라하기 — 일반 창/시크릿 창, E2E·체인 검증 실행법) → M6 완료 → Cowork 동기화

---

## 2026-10-01 — 마일스톤 M5 PR ② (argus-web: Argus 화면) — M5 완료

**한 일**
- PR #16(M5 PR ①) 머지 확인, main 최신화
- `apps/argus-web`: platform-web 뼈대를 복사해 같은 구조·버전·디자인 체계, 강조색만 남색(Argus)
  - `next.config.ts`: rewrite `/api/*` → argus-api `/api/*`(화면용 API만, `/ingest`는 화면에서 부르지 않음)
  - 화면: `/login`, `/detections`(상태 탭 필터·페이지, 담당자 "탐지건" / 취급자 "내 소명 요청"), `/detections/[id]`(요약·처리 버튼·하위 접속기록·차수별 소명·상태 이력), 상단 바(이름·역할·로그아웃)
  - 처리 버튼은 서버 상태 전이 표를 화면용으로 옮긴 `lib/labels.ts`의 `actionsFor(역할, 상태)`로 표시 — 담당자: 소명 요청·불요 / 요청 취소(오탐) / 승인·반려 / 재요청·에스컬레이션, 취급자: 소명 제출. **숨김은 편의, 허용 판단은 서버**
  - 하위 기록의 정보주체는 서버가 마스킹한 값 앞 5개 + "외 N명", 고유 정보주체가 잘림 하한값이면 "이상" 표시
  - 소명 내용·사유는 텍스트로만 표시(`white-space: pre-wrap`, HTML 해석 없음 — XSS 방지)
- compose `argus-web`(127.0.0.1:3001, argus-api healthy 후 기동), argus-api의 `ARGUS_TRUSTED_PROXIES` 설명 갱신(화면 서버 비신뢰), `.env.example`(`ARGUS_WEB_PORT`), README 서비스 표
- CI `web` matrix·docker-build에 argus-web, Dependabot npm·docker에 argus-web
- 검증
  - eslint·typecheck·build 통과(컨테이너)
  - 화면 서버 경유 실제 요청(점검용 임시 담당자, 무작위 비밀번호): 목록 4건 → 상세 #4(120명 모두 `member_10***`, 원본 미노출, 1차 요청자 시스템) → 대리 제출 403 → Argus 원장 LOGIN·READ(0)·READ(120, ids 없음). 모든 요청에 `X-Forwarded-For: 8.8.8.8`을 붙였으나 원장에는 argus-web 실제 IP(`172.18.0.6`) 기록(위조 무시). 점검 계정 DISABLED

**M5 완료 기준 대조** (CLAUDE.md 6절): platform-web 관리자 로그인·회원 목록·다운로드 버튼 ✅(PR ①, 사용자 브라우저 확인) / argus-web 로그인·탐지 목록·상세·요청/제출/승인 버튼 ✅(+ 반려·재요청·불요·요청 취소·에스컬레이션) — 사용자의 브라우저 시나리오 확인은 이 PR 리뷰 중 진행

**결정사항**
- argus-web은 platform-web과 같은 구조·버전·디자인 체계를 복사해 쓰고 공유 패키지로 묶지 않음 — 두 시스템은 별개 제품(API 쪽과 같은 원칙)
- 화면의 버튼 표시 규칙은 서버 전이 표를 그대로 옮긴 사본 — 둘이 어긋나도 서버가 거부하므로 안전하지만, 어긋나면 버튼이 눌러도 409가 나므로 전이 표를 바꿀 때 함께 수정
- 같은 브라우저에서 담당자·취급자를 동시에 쓰면 `argus_session` 쿠키가 덮어써짐 — 시연 시 일반 창과 시크릿 창을 나눠 쓰도록 안내(README 반영은 M6 실행 안내와 함께)

**설계 변경**
- 없음 (M5 PR ①의 "화면 서버는 신뢰 프록시가 아님"을 argus-web에도 동일 적용)

**미결·이슈**
- **v0.2 후보** — 화면용 버튼 규칙과 서버 전이 표의 이중 관리: 서버가 상세 응답에 "지금 가능한 행동" 목록을 내려주면 화면 사본이 필요 없어짐
- **v0.2 후보 (사용자 의견, 브라우저 시나리오 확인 중)** — **하위 접속기록의 정보주체 표시 개선**: 현재 마스킹 값 앞 5개 + "외 N명". 전체 목록 펼치기, 마스킹 해제(v0.1 여유 ②)와의 연계 등 필요한 모습은 추후 결정
- **v0.2 후보 (사용자 의견)** — **세부 결재 과정 다듬기**: 사용자 평가 "세부적인 결재 과정은 손볼 필요가 있지만 중요하진 않음, 이 정도면 훌륭" — 구체 항목은 추후 결정
- 사용자가 브라우저에서 시나리오(담당자 소명 요청 → 취급자 제출 → 반려 → 재요청·에스컬레이션 확인)를 수행하며 화면 동작 확인
- **Dependabot #17 (npm, platform-web)** — react·react-dom 19.2.8 → 19.3.0(같은 메이저, 수용 가능)에 더해 **typescript 5 → 7, eslint 9 → 10, @types/node 24 → 26 메이저 업**을 묶어 올림. 메이저 업은 create-next-app이 고른 검증 조합 밖이고 @types/node 26은 런타임(Node 24)과 불일치 → **이 PR(#18)에서 npm 메이저 업 제외 규칙 추가**(Python 3.14 PR #6과 같은 처리). #18 머지 후 Dependabot이 #17을 같은 메이저 안 갱신(react 19.3)만으로 다시 만들거나 닫음 — 다음 세션에서 확인, 남아 있으면 사유 코멘트와 함께 닫기. TypeScript 7·ESLint 10 전환은 별도 작업(v0.2 후보)
- **세션 인계**: 이 세션에서 M2~M5를 진행해 대화가 길어짐 → **M6는 Cowork 동기화 후 새 세션에서 시작**하기로 함. 새 세션은 CLAUDE.md와 이 구현 로그(M2~M5 결정·설계 변경·미결)로 이어간다
- 로컬 한계(PR ①과 동일): 쿠키가 포트를 구분하지 않음, Argus 자체 접속기록의 접속지가 로컬에선 argus-web IP

**다음 할 일**
- PR ② 머지 → **M5 완료 → Cowork 동기화**(v0.1 기본 디자인 포함 = CLAUDE.md 5·6절 개정, 화면 서버 비신뢰)
- M6: 시나리오 자동 테스트 스크립트(E2E), 해시체인 검증 스크립트, README 로컬 실행법(Windows) — Skeleton 완료 기준 "새로 clone → README대로 `docker compose up` → 시나리오를 화면으로 끝까지 + M6 테스트 CI 통과"

---

## 2026-10-01 — 마일스톤 M5 PR ① (platform-web: 플랫폼 관리자 화면)

**한 일**
- PR #15(M4 PR ②) 머지 확인 → **M4 완료**. main 최신화
- M5 계획 설명·결정(아래), `create-next-app@16.3.8`로 뼈대 생성(Tailwind·AI 안내 파일·git 초기화 제외) 후 정리 — 생성기가 고른 검증된 조합(Next 16.3.8, React 19.2.8, TypeScript 5.9.3, ESLint 9.39.5)을 **정확한 버전으로 고정**, `@types/node`는 런타임(Node 24)에 맞춰 24.19.0
- `apps/platform-web`
  - `next.config.ts`: `output: "standalone"`, `poweredByHeader: false`, **rewrite `/api/*` → platform-api**(같은 출처라 HttpOnly·SameSite=Strict 세션 쿠키가 그대로 동작, CORS 불필요)
  - 화면: `/login`(로그인), `/members`(회원 목록 20명씩·페이지 이동, CSV 다운로드 — 건수·가입일 기간), 상단 바(로그인한 취급자·로그아웃), 전 화면 상단에 "모든 데이터는 가상" 안내
  - CSV는 링크 이동이 아니라 요청으로 받아 파일 저장 — 401·접속기록 실패(500 `ACCESS_LOG_UNAVAILABLE`)를 화면에 보여주기 위해
  - `lib/api.ts`: 같은 출처 `/api` 호출, 오류 코드 → 한국어 문구(없는 ID와 틀린 비밀번호는 서버가 같은 코드로 답함)
  - **기본 디자인**(`app/globals.css`, CSS 한 장): 여백·글꼴·표·폼·버튼·배지·카드, 외부 글꼴 CDN 미사용, 강조색 변수로 argus-web과 구분
  - Dockerfile: deps(`npm ci`) → build → runtime(standalone, non-root `web` uid 10001, HEALTHCHECK), `NEXT_TELEMETRY_DISABLED=1`. `typecheck` 스크립트는 `next typegen && tsc --noEmit`(라우트 타입 자동 생성이 먼저 필요)
- platform-api: `GET /admin/auth/me`(본인 계정 정보, 접속기록 명시 제외) + 테스트 1
- compose `platform-web`(127.0.0.1:3000, platform-api healthy 후 기동), `.env.example`(`PLATFORM_WEB_PORT`), README 서비스 표
- CI: `web` job(Node 24, `npm ci` → eslint → typecheck → build), docker-build를 `include` 목록으로 바꿔 platform-web runtime 추가. Dependabot: npm(platform-web), docker에 platform-web·`node` 메이저 업 제외
- 검증
  - eslint·typecheck·build 통과(컨테이너), platform-api pytest 102개 통과
  - **화면 서버를 거친 실제 요청**: 로그인 전 401 → 틀린 비밀번호 401 → 로그인 200(쿠키 4속성) → 목록 500명 → CSV 120행 → 로그아웃 후 401 — 모두 `platform-web:3000/api/...` 경유
  - 브라우저(앱 내장): `localhost:3000` → 세션 없음 → `/login`으로 이동, 로그인 폼 렌더링 확인(스크린샷은 창 가림으로 실패, 텍스트로 확인)

**결정사항** (사용자 동의)
- 브라우저 ↔ API는 **Next.js rewrite(같은 출처 프록시)** — 운영의 Caddy 구조(architecture 7-2)와 같은 모양, CORS를 열지 않음
- compose는 **운영용 빌드**(`next build` → `next start`)로 실행 — 로컬 = 운영
- 화면 방식은 클라이언트 컴포넌트 + fetch(표·버튼 수준이라 서버 렌더링 이점 없음)
- M5는 PR 2개: ① platform-web(+ CI·Dependabot 프론트 추가) ② argus-web
- 소명 내용 등 사용자 입력은 React 기본 이스케이프만 사용, `dangerouslySetInnerHTML` 금지(XSS)
- Next.js 텔레메트리 끔(빌드·실행 정보 외부 전송 방지)

**⚠ 구현 중 발견: 접속지 위조 구멍을 만들 뻔함 → 설계 수정**
- 계획: platform-web에 고정 IP를 주고 platform-api가 그 IP만 신뢰 프록시로 믿어, Next.js가 붙이는 `X-Forwarded-For`로 원래 요청자 IP를 기록
- 실측: 화면 경유 요청의 접속지가 원래 요청자(`172.30.10.5`)가 아니라 **화면 서버(`172.30.10.10`)**로 기록됨
- 원인(Next.js 소스 `server/lib/router-utils/proxy-request.js` 확인): rewrite 프록시(httpxy)는 `xfwd` 옵션 없이 `x-forwarded-host`만 붙임 → **원래 요청자 IP를 넣지 않으면서, 클라이언트가 보낸 `X-Forwarded-For`는 그대로 전달**
- 위험: 화면 서버를 신뢰하면 누구든 `X-Forwarded-For: 8.8.8.8`을 붙여 **접속지를 위조**할 수 있음(절대 규칙 #10 위반) — `X-Forwarded-For`는 브라우저 fetch로도 보낼 수 있는 헤더
- 조치: **화면 서버를 신뢰 프록시에서 제외**(`PLATFORM_TRUSTED_PROXIES` 기본 빈 값), 고정 IP·대역 설정 제거. 확인: 화면 경유로 `X-Forwarded-For: 8.8.8.8`을 붙인 로그인 → 원장에 화면 서버 실제 IP 기록(위조 무시)
- 대가(로컬 한계): 로컬에서는 모든 사용자의 접속지가 화면 서버 IP로 기록됨. 다만 로컬은 Docker 포트 포워딩 때문에 원래 브라우저 IP가 컨테이너에 보이지 않는 환경이라 실질 손실 없음
- 운영: Caddy가 `/api`를 화면을 거치지 않고 **API로 직접** 보내고 Caddy만 신뢰 → 실제 IP 기록. **배포 시 확인 항목**: Caddy가 클라이언트가 보낸 `X-Forwarded-For`를 덮어쓰는지(신뢰 프록시 설정), API의 `*_TRUSTED_PROXIES`가 Caddy 주소만인지
- 기각한 대안: Next.js 커스텀 서버로 소켓 주소를 직접 헤더에 넣기(standalone 서버를 대체 — 복잡·업그레이드 부담), 로컬에도 Caddy 두기(v0.1 제외 항목)

**설계 변경** (Cowork에서 원본 반영 필요)
1. **v0.1에 기본 디자인 포함** (사용자 요청, 2026-10-01) — "보여줘야 하는 프로젝트라 기본 수준의 디자인은 v0.1에서 완성". M5에서 CSS 한 장(외부 의존 없음)으로 기본 정돈을 함께 적용 / 영향: **CLAUDE.md 5절**("Skeleton에서는 디자인 없이 표·버튼 수준"), **6절 v0.1 범위**(Must 또는 여유 항목에 "기본 디자인" 추가)
2. **로컬 화면 서버는 신뢰 프록시가 아님** — 화면 경유 API 요청의 접속지는 로컬에서 화면 서버 IP로 기록, 운영은 Caddy 직접 라우팅 / 영향: architecture 3-2 #1·7-2(Caddy 신뢰 프록시 설정을 배포 체크리스트로)
3. `platform-api GET /admin/auth/me` 추가(화면용, 접속기록 명시 제외) — 화면용 내부 API라 api-spec 범위 밖, 기록만

**미결·이슈**
- **(배포 체크리스트)** Caddy의 `X-Forwarded-For` 처리·API 신뢰 프록시 설정 확인 — 위 발견 사항
- 로컬 한계: 쿠키는 포트를 구분하지 않아 `localhost:3000`의 플랫폼 쿠키가 `localhost:3001`(argus-web) 요청에도 실림(이름이 달라 서로 무시). 운영은 서브도메인이 달라 해당 없음
- 보안 헤더(CSP·X-Frame-Options 등)는 운영 Caddy 담당(architecture 7-2) — 화면에 직접 넣지 않음

**다음 할 일**
- PR ① 머지 → PR ②(argus-web: 로그인·탐지 목록·상세·결재 버튼, 같은 디자인 체계), CI·Dependabot에 argus-web 추가

---

## 2026-10-01 — 마일스톤 M4 PR ② (탐지건 조회·마스킹·상태 전이·자동 소명 요청) — M4 완료

**한 일**
- PR #13(M4 PR ①)·#14(M3 설계 사본) 머지 확인. #14로 M3 설계 변경 1~5와 api-spec 401 문구 정정이 반영됨(2026-10-01 미결 "401 문구" 해소)
- 마이그레이션 `0005`: `detection_rule.auto_request boolean NOT NULL DEFAULT true`, `explanation.requested_by` NULL 허용(NULL = 시스템). 새 테이블 없어 권한 변경 없음
- 자동 소명 요청(`app/detection/batch.py`): 탐지건을 **새로 만들 때**, 룰의 `auto_request`가 켜져 있고 행위자에게 재직 중 + 비활성 아닌 A5 계정이 있으면 같은 트랜잭션에서 `REQUESTED`(1차, 요청자 NULL, "자동 소명 요청 — 룰 이름"), 상태 이력 2줄(DETECTED·REQUESTED 모두 시스템). 룰 사본에 `auto_request` 포함
- 탐지건 API(`app/detections/`)
  - `GET /api/detections`(상태 필터·페이지), `GET /api/detections/{id}`(룰 요약, `log_summary`, 하위 기록, 차수별 소명, 상태 이력)
  - **마스킹**(`masking.py`): `member_` + 뒤 3자리 `***`, 3자리 이하는 전부 가림. 서버에서 가려서 보냄
  - **상태 전이 표**(`transitions.py`) 하나로 처리: request(DETECTED·REJECTED → REQUESTED, 차수 +1), dismiss(DETECTED·**REQUESTED** → DISMISSED, 사유 필수), submit(REQUESTED → SUBMITTED, 취급자 본인, 내용 필수), approve(SUBMITTED → APPROVED), reject(SUBMITTED → REJECTED, 사유 필수), escalate(REJECTED → ESCALATED). 표에 없으면 409, 역할이 다르면 403, 볼 수 없는 건은 404. 행 잠금(`FOR UPDATE`)으로 배치와 경합 방지. 소명은 차수별 한 줄(요청 시 생성·제출 시 채움·검토 시 채움)
  - 노출 범위: 담당자 전체 / 취급자는 본인 건 중 **요청받은 건(round ≥ 1)**
  - 자체 접속기록: 목록·상세는 READ(ACCESS_LOG, **건수만** — 상세는 보여준 마스킹 식별값 수), 전이 6개는 제외 표시(상태 이력이 기록)
- 검증
  - pytest 207개 통과(PR ①까지 174 + 신규 33: API 26, 배치 자동 요청 7), ruff 통과
    - 전체 흐름(자동 요청 → 제출 → 반려 → 재요청 2차 → 재제출 → 승인, 소명 2줄·이력 7줄), 담당자 요청 취소, 계정 없는 건 수동 처리, 에스컬레이션, 잘못된 전이 409·종결 후 불변, 역할 403(담당자 대리 제출·**취급자 취소 경로 없음**·취급자 승인), 필수 입력 400, 노출 범위(담당자 전체·취급자 본인 요청 건만·남의 건 404·요청 전 404 → 요청 후 200), 필터·페이지, **담당자·취급자 모두 원본 식별값 미노출**, 목록엔 건수만, 조회는 READ(건수만)·전이는 미기록
    - 배치: 자동 요청(ACTIVE·LOCKED 계정), 같은 날 반복 시 요청 1번, 계정 없음·비활성·퇴직 시 DETECTED, 룰 `auto_request` 꺼짐 시 DETECTED
  - **실제 컨테이너**: `0005` 적용 → ops_park 다운로드는 오늘 진행 중인 #3(자동 요청 기능 전 생성, DETECTED)에 붙음(하루 1건 규칙) → mkt_lee 다운로드 → 순찰 `detected=1` → **#4 자동 REQUESTED 1차**(요청자 NULL, 이력 2줄) → 점검용 임시 담당자로 목록(4건)·상세(120명 모두 `member_10***`, 원본 미노출)·대리 제출 403 → Argus 원장 LOGIN·READ(0)·READ(120, ids 없음) → `verify_chain` ok(29건). 점검 계정 DISABLED

**M4 완료 기준 대조** (CLAUDE.md 6절): Argus 로그인(OFFICER/HANDLER) ✅ / 탐지건 목록·상세(마스킹) ✅ / 소명 요청·제출·승인·반려·재요청(round+1)·DISMISS ✅(+ 에스컬레이션, 담당자 요청 취소, 자동 요청) / 허용되지 않은 전이 거부 ✅ / HANDLER는 본인 건만 ✅ / Argus 자체 접속기록(LOGIN·READ, READ는 건수만) ✅
- 취급자 로그인 → 조회 → 제출의 **실제 서버 확인은 API 테스트로 대신**함(사용자 계정 비밀번호를 쓰지 않음). M5 화면에서 사용자가 직접 확인

**결정사항**
- 상태 전이를 **표(데이터) 하나**로 정의하고 전이 함수 하나가 해석 — 허용 목록 방식이라 표에 없는 조합은 자동으로 거부, 상태도와 코드가 1:1로 대조됨
- 검사 순서: 볼 수 있는가(404) → 역할(403) → 전이 가능(409). 볼 수 없는 건에는 역할·상태 정보도 주지 않음
- 필수 입력 문자열은 앞뒤 공백을 제거한 뒤 1자 이상 — 공백만 넣은 사유 방지. 사유·코멘트 1,000자, 소명 내용 5,000자
- 자동 요청은 **탐지건 생성 시에만** — 이미 진행 중인 DETECTED 건(자동 요청 기능 이전에 생성됐거나 당시 계정이 없던 건)에 기록이 보태져도 자동 요청하지 않음. 담당자가 목록에서 수동 요청
- 잠김(LOCKED)·비밀번호 미설정 A5에게도 자동 요청은 감(풀면 되는 일시 상태), 비활성(DISABLED)·퇴직·미동기화는 DETECTED
- `explanation (detection_id, round)` 유일 제약이 이중 요청을 DB에서 막음(테스트 중 인위적으로 만든 모순 상태에서 실제로 작동 확인)

**기각한 대안**
- 전이마다 별도 함수에 조건문: 허용 목록이 코드 곳곳에 흩어져 상태도와 대조하기 어려움 → 전이 표
- 남의 탐지건에 403: 그 번호의 건이 존재한다는 사실이 드러남 → 404
- `explanation.requested_by`에 "시스템 계정" 행을 만들어 채움: 로그인 불가 계정을 따로 관리해야 하고 상태 이력의 "NULL = 시스템" 규칙과 어긋남 → NULL 허용

**설계 변경** (Cowork에서 원본 반영 필요 — M4 착수 항목의 "자동 소명 요청 사용자 확정" 포함)
1. 자동 소명 요청: `detection_rule.auto_request`, `explanation.requested_by` NULL 허용, 탐지 배치의 자동 요청, 시나리오 변경(CLAUDE.md 6절) — M4 착수 항목 참고
2. 상태도에 `REQUESTED → DISMISSED`(담당자 요청 취소) 추가 — policy 3-1·3-2
3. 취급자 노출 범위 = 본인 건 중 요청받은 건(round ≥ 1) — db-schema 3-1 "A5의 조회 범위", actor-flows F-06 #2
4. 상태 전이 오류 응답: 볼 수 없음 404 / 역할 불일치 403 `FORBIDDEN` / 허용되지 않은 전이 409 `INVALID_TRANSITION` — 화면용 API라 OpenAPI 자동 문서화 대상(api-spec 범위 밖)이지만 정책(policy 3-2 "허용되지 않은 전이 거부")의 구체화로 기록
5. 자동 요청은 탐지건 생성 시에만 — db-schema 3-7 #3

**미결·이슈**
- **v0.2 후보** — 기존 DETECTED 건에 새 기록이 붙을 때(또는 나중에 A5 계정이 생겼을 때) 자동 요청을 보낼지: 현재는 담당자 수동. 대시보드의 미조치 표시(LOG-05, 이미 v0.2 후보)와 함께 검토
- **v0.2 후보** — Argus 계정 생성·비밀번호 설정·잠금 해제 이력: 관리 스크립트(CLI)라 Argus 접속기록·상태 이력 어디에도 남지 않음. §5③(권한 부여·변경·말소 내역 보관) 관점의 보안성 검토 후보이기도 함
- 로컬 원장에 점검용 임시 계정(`smoke_*`) 기록이 남아 있음 — append-only라 지우지 않음(사실 기록), 계정은 DISABLED

**다음 할 일**
- PR ② 머지 → **M4 완료 → Cowork 동기화**(자동 소명 요청 설계 변경 1~5, CLAUDE.md 6절 시나리오 개정 포함)
- M5 계획 설명: platform-web(관리자 로그인·회원 목록·다운로드 버튼), argus-web(로그인·탐지 목록·상세·요청/제출/승인 버튼), Next.js, CI에 eslint·tsc, Dependabot npm

---

## 2026-10-01 — 마일스톤 M4 착수 (계획 확정, 자동 소명 요청 → Cowork 논의 대기)

**한 일**
- PR #12(M3) 머지 확인, main 최신화
- M4 계획 설명·결정. **소명 요청 방식을 사용자가 "자동 요청"으로 원함** → Skeleton 시나리오·상태도가 바뀌는 설계 변경이라 Cowork에서 먼저 정하기로 함(아래). 그동안 결정과 무관한 PR ①(Argus 로그인·계정 관리·자체 접속기록)을 먼저 진행

**결정사항** (사용자 동의)
- **필수 룰 범위는 Skeleton 완료 후 Cowork에서 결정, CLAUDE.md 6절은 그대로** (M3 로그의 "기본 룰 필수화" 미결을 대체)
- Argus 인증 = 플랫폼과 같은 방식(policy 4-3): JWT(HS256) HttpOnly·SameSite=Strict 쿠키, 30분 미사용 만료, 매 요청 재확인, 5회 연속 실패 시 `argus_user.status=LOCKED`. 두 시스템은 별개 제품이라 코드는 공유하지 않음
- **계정 준비는 관리 스크립트**(`app.scripts.users`: 담당자 생성·비밀번호 설정·잠금 해제), 비밀번호는 실행 후 직접 입력(셸 기록에 남지 않게). A5 계정은 동기화 뒤에야 생기므로 `.env` 시드 방식은 순서가 꼬임 — api-spec 3-1 "M4에서 관리 스크립트로 설정"의 구현
- **에스컬레이션(REJECTED → ESCALATED) 포함** — M4 목록엔 없지만 policy 3-2 상태도의 정식 전이
- M4는 PR 2개: ① 인증·계정·자체 접속기록 기반 / ② 탐지건 조회·마스킹·상태 전이
- 상태 변경(요청·제출·승인 등)은 Argus 접속기록이 아니라 **상태 이력**(`detection_status_history`)으로 남김 — api-spec 2-7의 Argus 기록 대상은 LOGIN·READ·UNMASK·EXPORT, 상태 이력이 누가·언제·사유를 담음. 해당 라우트는 제외 사유를 명시
- 남의 탐지건은 403이 아니라 404 — 존재 여부가 드러나지 않게

**PR ① 구현 — Argus 로그인·계정 관리·자체 접속기록 기반**
- 인증(`app/auth/`): `POST /api/auth/login`·`/logout`, `GET /api/auth/me`. JWT(HS256) `argus_session` 쿠키, 30분 연장, 매 요청 계정 상태(+ HANDLER는 명부 재직 상태) 재확인, 5회 실패 시 `status=LOCKED`(증가·잠금을 한 UPDATE 문으로), 없는 ID는 더미 해시 검증, 잠김·비활성은 비밀번호가 맞을 때만 403, 입력 상한 64/256자, 서명 키 32자 미만이면 기동 거부. 의존성 `pyjwt==2.15.1`
- 계정 관리 `app.scripts.users`: `create-officer`·`set-password`·`unlock`. 비밀번호는 `getpass` 두 번 입력(자동화용 `--password-env`), 12자 이상, 앱 계정으로 접속. `set-password`는 상태를 바꾸지 않음(비활성 A5가 되살아나지 않게), `unlock`은 LOCKED만(DISABLED 제외)
- 자체 접속기록 Agent(`app/agent/`): `/api` 경로만, 문패·제외 표시 없으면 기동 거부, 응답 헤더 직전에 **수집 API와 같은 `validate_event(internal=True)`를 거쳐 같은 `append_access_logs`로 원장에 직접**(source=ARGUS), 실패 시 500(fail-closed), 경로는 라우트 템플릿, 검색조건은 키 이름만, READ는 **건수만**(ids 없음), 신뢰 프록시일 때만 X-Forwarded-For
- compose: argus-api에 `ARGUS_AUTH_SECRET`(필수)·`ARGUS_TRUSTED_PROXIES`, `.env.example`·README(계정 준비 절차)
- 검증
  - pytest 174개 통과(M3까지 140 + 신규 34: 로그인 17, 자체 접속기록 10, 계정 스크립트 7), ruff 통과
  - 실제 서버: 점검용 임시 담당자 계정(무작위 비밀번호, 출력 안 함)으로 로그인 전 401 → 틀린 비밀번호 401 → 로그인 200(쿠키 속성 4종) → `/me` no-store → 5회 실패 후 403 LOCKED → ARGUS 출처 LOGIN 8건 원장 기록 → `verify_chain` ok(18건, 플랫폼·Argus 한 체인). 점검 계정은 DISABLED(앱 계정엔 DELETE 권한 없음, 원장 기록은 사실이므로 유지)

**→ 자동 소명 요청: 사용자 확정 (같은 날, 아래 논의안을 단순화)**
- **자동 소명 요청으로 간다.** 탐지건 생성 시 즉시 `REQUESTED`(1차, 요청자 = 시스템)
- **취급자의 "오탐 취소 요청" 경로는 두지 않는다** — 흐름이 너무 복잡해지고, 취급자가 오탐 여부를 판단할 기준도 애매함(아래 (가)·(나) 모두 기각)
- **담당자는 탐지건 목록을 보고 오탐으로 판단되면 요청을 취소**할 수 있다: `REQUESTED → DISMISSED`(사유 필수)
- 담당자는 기존대로 전체 탐지건 목록을 본다
- 구현 범위(PR ②): `detection_rule.auto_request`(기본 켜짐) / `explanation.requested_by` NULL 허용(NULL = 시스템) / 상태도에 `REQUESTED → DISMISSED`만 추가 / 탐지 배치가 탐지건 생성 시 같은 트랜잭션에서 자동 요청(행위자에게 로그인 가능한 A5 계정이 없으면 `DETECTED`로 남겨 담당자가 수동 처리)
- `REQUESTED → ESCALATED`는 추가하지 않음. 요청 후 제출 전 퇴직으로 멈추는 모순은 담당자가 `REQUESTED → DISMISSED`로 닫을 수 있어 해소
- **Cowork에서 원본 반영 필요**: CLAUDE.md 6절 Skeleton 시나리오("담당자 소명 요청" → "자동 소명 요청"), policy 3-1·3-2, actor-flows F-04·F-05·F-06, db-schema 3-3·3-4·3-7

**(참고) Cowork 논의용으로 정리했던 원안 — 사용자 요구, 2026-10-01**
- 배경: 현재 설계(F-05 #4)는 탐지 → `DETECTED` → **담당자가 검토 후 소명 요청 또는 불요**. 사용자는 **탐지 시 자동 요청**을 의도했음 — 수동이면 담당자 업무 부하(병목)·방치 위험이 큼
- "하루 1건" 억제는 이미 충족 — M3 그룹핑이 `(취급자, 룰, KST 날짜)`라 하루 여러 번 걸려도 탐지건·소명 요청은 1개
- 사용자 요구사항
  1. **자동 소명 요청** — 탐지건 생성 시 즉시 `REQUESTED`(1차, 요청자 = 시스템)
  2. **취급자의 오탐 숨구멍** — 취급자가 오탐이라 판단하면 사유와 함께 **취소를 요청**하고, **담당자가 받아들이면 요청 취소(`DISMISSED`)**, 아니면 계속 소명
  3. **담당자는 기존대로 전체 탐지건 목록 확인 가능**
- Claude Code 제안안
  - 자동 요청 조건: 룰의 `auto_request`가 켜짐(기본 켜짐, 룰 빌더에서 조정) **그리고** 행위자에게 로그인 가능한 A5 계정이 있음. 아니면 `DETECTED`로 남겨 담당자가 수동 처리(미동기화·퇴직자 등 — 퇴직자 접속은 원래 담당자 사안)
  - 오탐 숨구멍 구현 후보: **(가·추천)** 소명 제출 시 "오탐 주장" 표시 → 담당자가 `SUBMITTED`에서 승인·반려 외에 "오탐 인정(`DISMISSED`)" 선택 — 상태를 늘리지 않음 / (나) "취소 요청됨" 새 상태 — 흐름은 명확하나 상태도 복잡
  - 담당자는 `REQUESTED`에서도 요청 취소(`DISMISSED`, 사유 필수) 가능 — 취급자 소명 전에 오탐을 걷어냄
- 필요한 설계 변경
  | 무엇 | 변경 | 영향 문서 |
  |---|---|---|
  | 룰 | `detection_rule.auto_request boolean NOT NULL DEFAULT true` | db-schema 3-3 |
  | 소명 | `explanation.requested_by` NULL 허용(NULL = 시스템 요청) — 현재 NOT NULL이라 사람만 요청 가능. (가)면 `explanation.false_positive_claim boolean` 추가 | db-schema 3-4 |
  | 상태도 | `REQUESTED → DISMISSED`(요청 취소), `REQUESTED → ESCALATED`, (가)면 `SUBMITTED → DISMISSED`(오탐 인정) | policy 3-1·3-2 |
  | 탐지 배치 | 탐지건 생성 시 조건이 맞으면 같은 트랜잭션에서 자동 요청(상태 이력 2줄: DETECTED, REQUESTED — 모두 시스템) | db-schema 3-7, actor-flows F-04 |
  | 시나리오 | "담당자 소명 요청" → "**자동 소명 요청** → 취급자 제출 → 담당자 반려·재요청·승인" | **CLAUDE.md 6절 Skeleton 시나리오**, actor-flows F-05·F-06 |
- 함께 해소되는 모순: api-spec 3-1("퇴직 시 진행 중 소명 건은 담당자가 DISMISS 또는 ESCALATE") vs policy 3-2 상태도(`REQUESTED`에서 DISMISS·ESCALATE 불가) — 현재는 소명 요청 후 제출 전에 퇴직하면(A5 DISABLED) 건이 `REQUESTED`에 멈춤. 위 상태도 변경으로 해소
- 취급자 노출 범위: "**소명 요청을 받은 건(round ≥ 1)만**" — 자동 요청이면 대부분 보이고, 자동 요청이 안 간 `DETECTED`(담당자 검토 대기)는 보이지 않음. 수동·자동 어느 쪽이든 일관
- 부작용: 오탐도 일단 취급자에게 감 → 룰의 세밀한 설정과 담당자의 빠른 요청 취소가 중요해짐

**미결·이슈**
- **v0.2 후보** — 소명 제출 **후** 같은 탐지건에 새 기록이 붙으면 제출한 소명이 그 추가분을 설명하지 않을 수 있음 → 담당자 검토 화면에 "제출 후 추가된 기록" 표시
- **v0.2 후보** — 더 세밀한 억제(예: 같은 룰은 N일에 한 번만 요청), 룰별 자동 요청 여부 화면 조정 — 룰 빌더와 함께
- **v0.2 후보** — 담당자 대시보드의 "미조치 건수·경과 시간"(LOG-05) — 수동 처리 건의 방치 방지

**다음 할 일**
- PR ①(Argus 로그인·계정 관리 스크립트·자체 접속기록 기반) 구현
- Cowork에서 자동 소명 요청 설계 확정 → PR ②(탐지건 조회·마스킹·상태 전이)에 반영

---

## 2026-10-01 — 마일스톤 M3 (탐지 배치: argus-worker, 대량 다운로드 룰)

**한 일**
- 마이그레이션 `0004`: 탐지 룰 시드 "대량 다운로드"(EVENT, APP, HIGH, `action=DOWNLOAD ∧ subject_count≥50`) — M1 `0003`에 "M3에서 추가"로 남겨 둔 것
- 룰 검사·판정 `app/detection/rules.py`: `validate_rule`(해석 가능 여부) / `matches`(조건식 판정, `all`·`any` 1단계 중첩, `eq`·`in`·`gte`·`lte`), 필드 4개(`action`·`data_category`·`result`·`subject_count`)
- 탐지 배치 `app/detection/batch.py`: id 커서, 실행 이력 RUNNING → 판정·탐지건·하위 로그·SUCCESS를 한 트랜잭션, 실패 시 FAILED(책갈피 유지), KST 날짜 그룹핑, 진행 중 건에만 추가(`FOR UPDATE`), `rule_snapshot`, `log_summary`, 상태 이력(시스템), 남은 RUNNING 정리, advisory lock으로 한 번에 하나만
- worker `app/worker.py`: 주기를 매 순찰 `setting`에서 다시 읽음, 밀려 있으면 쉬지 않고 다음 순찰, `--once` 즉시 1회(시연·E2E용), compose `argus-worker`(argus-api 이미지·앱 계정, HEALTHCHECK 끔)
- README 서비스 표에 argus-worker
- 검증
  - pytest 140개 통과(M2까지 93 + 신규 47: 룰 검사·판정 23, 배치 23, 시드 1), ruff 통과. 배치 테스트는 **앱 계정**으로 실행(운영 권한으로 충분한지 확인)
    - 120건 → 탐지건(DETECTED·HIGH·룰 사본·상태 이력), 경계 49/50, READ 미탐지, 같은 날 두 번 → 한 건(고유 인원 180 = 겹침 제외), 다른 사람·날짜 분리, **KST 날짜 경계**, 종결(APPROVED·DISMISSED·ESCALATED) 후 새 건, 진행 중(REQUESTED·SUBMITTED·REJECTED)에 추가, 커서 전진, 빈 순찰 기록, 10,000건 끊기, **해석 불가 룰 → FAILED·책갈피 유지 → 고치면 같은 범위 재처리**, 재처리 중복 없음(종결 후 재처리 포함), 남은 RUNNING 정리, 동시 실행 방지, 꺼진 룰·다른 경로 무시, 요약에 회원 PK 없음·잘림 표시
  - **실제 컨테이너 끝-끝**: `0004` 적용 → worker 첫 순찰이 기존 원장 6건에서 탐지건 2개(M1·M2 점검 때의 120건 다운로드, 날짜가 달라 분리) → ops_park 로그인·CSV 120건 → relay 전송 → `--once` 순찰 #2 `(6..10] processed=4 detected=1` → 탐지건 #3 DETECTED. 이 다운로드는 **KST 10-01 00:42(UTC 9-30 15:42)**라 `2026-10-01` 서류철로 분리됨 — UTC 기준이었다면 #2에 섞였을 것

**결정사항** (사용자 동의, 2026-10-01)
1. **탐지 그룹핑 날짜는 한국 시각(KST, +09:00 고정)** — UTC 날짜면 KST 새벽 0~9시 행위가 전날 서류철로 감. 야간·주말 룰(시각·요일)도 KST여야 맞음
2. **켜진 룰 중 해석할 수 없는 룰이 있으면 순찰 전체 FAILED** — 그 룰만 건너뛰면 책갈피가 넘어가 그 사이 기록이 그 룰로 영영 평가되지 않음. "잘못된 룰" = 코드가 해석할 수 없는 룰(모르는 필드·연산자, 값 타입, 구조, 미지원 유형) — 기준값의 적절성은 담당자 판단. v0.1은 룰 빌더가 없어 잘못된 룰 = 배포 실수
3. **새 기록이 없어도 순찰 이력 기록** — "탐지가 주기적으로 수행됐다"는 점검 증적(§8②), worker 중단 기간과 기록 없는 기간을 구분
4. **순찰 1회 최대 10,000건**, 밀려 있으면 쉬지 않고 다음 순찰
5. **`log_summary.subject_ids_truncated`** — 회원 PK가 1,000개에서 잘린 기록이 있으면 `distinct_subject_count`가 하한값임을 표시. 건수 합(행위의 양, 탐지 판정)과 고유 인원(피해 범위, 유출 통지·신고 판단)은 서로 다른 질문의 답이라 둘 다 유지
- 실행 이력 RUNNING은 별도 트랜잭션으로 먼저 남기고, 결과·SUCCESS는 한 트랜잭션 — 진행 중 순찰이 밖에서 보이면서도 결과는 원자적
- 같은 룰로 이미 어떤 탐지건에 붙은 기록은 다시 붙이지 않음 — 책갈피를 되돌려 재처리해도(종결 후 포함) 증거가 복제되지 않음
- `log_summary`는 연결된 기록 전체에서 다시 계산(재처리해도 같은 값), 회원 PK는 담지 않고 숫자만
- 실패 기록에는 예외 메시지 **첫 줄만** — DB 오류의 DETAIL(둘째 줄 이후)에 값이 실릴 수 있음
- 룰 검사기를 하나로 두어, 나중의 룰 빌더 API(저장 시 거부)와 배치(순찰 전 이중 확인)가 함께 쓰게 함
- 왜 배치·5분인가(사용자 질문, 설계 근거 정리): requirements LOG-03 "준실시간 배치(5분~1시간)", 실시간 스트리밍은 "인프라 부담"으로 스트레치 제외. Argus는 막는 접근제어가 아니라 이미 일어난 행위를 보고 소명을 받는 탐지 통제 — 소명은 사람 속도, §8②는 월 1회 이상 점검. 배치가 집계형 룰·늦게 도착한 기록·실패 재처리·수집과의 분리·단일 VM 인프라에 모두 유리. 5분은 당일 대응이 가능하면서 부담 없는 간격이고 설정값으로 조정 가능

**기각한 대안**
- 해석 불가 룰만 건너뛰고 계속: 책갈피가 넘어가 탐지 누락 → 위 결정 2
- `log_count`·요약을 증분으로 갱신: 재처리 시 중복 누적 위험 → 연결된 기록에서 재계산
- 날짜 버킷을 SQL `AT TIME ZONE 'Asia/Seoul'`로: DB 이미지의 시간대 데이터에 의존 → Python 고정 오프셋

**설계 변경** (Cowork에서 원본 반영 필요)
1. **탐지 그룹핑의 날짜 = KST(+09:00) 기준** — db-schema 3-4 `group_bucket` 설명·3-7 #3, policy 2-2 "날짜" / 영향: 야간·주말 룰 시각·요일 판정도 KST
2. **해석할 수 없는 룰이 켜져 있으면 순찰 전체 FAILED(책갈피 유지)** — db-schema 3-7 #2·#5
3. **빈 순찰도 `detection_batch_run`에 기록** — db-schema 3-7 #1
4. **순찰 1회 최대 10,000건** — db-schema 3-7 #1
5. **`log_summary`에 `subject_ids_truncated` 추가** — db-schema 3-4 `log_summary` 내용 정의

**미결·이슈**
- **(사용자 요청) policy 1-3 기본 룰의 필수화** — 사용자가 Skeleton 이후 단계에서 기본 룰을 "반드시 반영"하길 원함. 현재 CLAUDE.md 6절은 EVENT 3개(야간·주말·퇴직자)가 "여유 시 ①", 결제수단 조회·AGGREGATE 2개는 v0.1 제외 → **Cowork에서 CLAUDE.md 6절 개정 필요, 필수 범위(EVENT 3개 / 7개 전부) 결정 필요**. 코드 쪽 준비: 룰은 마이그레이션 한 줄 + `rules.py`의 `FIELDS`에 필드 추가로 붙음(`occurred_time`·`occurred_weekday`·`actor_terminated_at_or_before` 등, KST 기준). 퇴직자 룰은 `handler` 조인 + 미동기화 시 평가 보류(db-schema 3-6) 구현 필요
- **v0.2 후보** — 룰 빌더(웹 룰 설정) 도입 시 재검토: 담당자 한 명의 잘못된 룰이 전체 탐지를 멈추지 않도록 "해당 룰만 격리(자동 비활성화 + 알림) + 룰별 책갈피" 방식 검토. 저장 시 검사는 `validate_rule` 재사용
- **v0.2 후보** — 원장 끝부분 삭제 교차 확인: 순찰 때 직전 SUCCESS의 `to_access_log_id` > 원장 최대 id면 경고(db-schema 3-5 알려진 한계의 대응 후보). M3 범위 밖이라 미구현
- **v0.2 후보** — Argus 자체 접속기록(M4부터, source=ARGUS)에 룰을 적용할지: 현재는 출처 구분 없이 평가(대량 다운로드는 Argus의 `EXPORT`와 무관해 영향 없음). 야간 룰 등이 붙으면 담당자의 야간 근무도 탐지되므로 M4 이후 결정
- `log_summary` 재계산은 연결된 기록 기준 — 1년 넘게 진행 중인 탐지건에 파기 후 새 기록이 붙으면 요약이 줄어들 수 있음(실무상 드묾)

**다음 할 일**
- M3 PR 머지 → M4 계획 설명: Argus 로그인(OFFICER/HANDLER, M2와 같은 인증 방식), 탐지건 목록·상세(마스킹), 소명 요청·제출·승인·반려·재요청·DISMISS, 허용되지 않은 전이 거부, HANDLER는 본인 건만, Argus 자체 접속기록(LOGIN·READ)
- Cowork: M3 설계 변경 1~5와 기본 룰 필수화 반영 (M4 착수 전이든 M4 후든 사용자 판단)

---

## 2026-10-01 — M2 마무리 (PR #10 머지, 설계 사본 M2 반영)

**한 일**
- PR #10(M2 PR ③) 머지 확인: CI 전부 통과, main CI(`ee5daec`)도 정상 실행. 로컬 main 최신화, 병합된 브랜치 삭제
- Cowork "구현" 방에서 M2 설계 변경 1~9를 원본에 반영 → 사본 7개 갱신분(CLAUDE.md, docs/README.md, actor-flows, api-spec v0.4, architecture, db-schema, policy 4-3 신설)을 구현과 대조, 별도 `docs:` PR로 올림
- 대조 결과: 설계 변경 9건 모두 반영, 1건 불일치(아래 미결), 1건 원문 확인 권장(아래 미결)
- Cowork에서 정한 **CLAUDE.md 6절 "Skeleton 이후: v0.1 범위"** 추가분을 같은 `docs:` PR에 반영
  - Must: Skeleton 완성(M3~M6), 심사자용 README, 시연 자료
  - 여유 시(순서 고정): ① EVENT 룰 3개 → ② 언마스킹(LOG-10) → ③ DB 직접 접근(2티어) 최소판
  - 게이트 A(Skeleton 완성 확인)·B(2티어 진행 여부), 기능 동결, 범위 밖 아이디어는 "v0.2 후보"로 기록만

**결정사항**
- 아래 미결 2건은 **보안성 검토 단계에서 정리**(사용자 판단) — 둘 다 문서 표현·인용 문제로 동작·M3에 영향 없음. 사본은 받은 그대로 반영

**설계 변경**
- 없음 (Cowork 반영분 확인만)

**미결·이슈**
- **api-spec 1-5(401 행)·v0.4 개정 내역 #1의 문구가 구현과 다름 — 구현이 의도된 동작**
  - 문서: "백오프 **최대 간격(1시간)으로만** 재확인" → 401이면 처음부터 1시간 간격으로 읽힘
  - 구현(`apps/platform-api/app/relay.py`): 401도 다른 실패와 같은 **지수 백오프 1분 → 2 → 4 … 최대 1시간** + 매번 ERROR 로그
  - 원인: 2026-09-29 로그의 설계 변경 5 문구 "백오프 간격(최대 1시간)"이 모호했음
  - **구현을 문서에 맞춰 바꾸지 말 것.** 키를 고친 직후 빨리 재개되는 쪽이 낫고, 로그 폭주는 백오프로 억제됨(첫 1시간 약 6회). 보안성 검토 때 Cowork에서 문구를 "다른 실패와 같은 지수 백오프(1분→…최대 1시간)로 재확인"으로 정정
- **법 조항 번호 원문 대조 필요** — architecture 1절(관리자 인증 행)·policy 4-3의 "안전조치기준 §5⑥(인증 실패 시 접근 제한)", "§6④(일정 시간 미사용 시 접속 차단)". 온라인에서 현행 고시 조문 전문을 열람하지 못해 확인하지 못함(§6④는 개정 전후 항 번호가 달랐을 가능성). 보안성 검토·ISMS-P 점검 때 법제처 국가법령정보센터 현행 고시 원문으로 대조

**다음 할 일**
- `docs:` PR 머지 → M3 계획 설명 후 착수: argus-worker 탐지 배치(`setting.detection_interval_min` 주기, `id` 커서, EVENT 룰 평가, 진행 중 탐지건에만 하위 로그 추가, `log_summary`·`rule_snapshot`, 상태 이력)

---

## 2026-09-30 — 마일스톤 M2 PR ③ (접속기록 Agent·relay) — M2 완료

**한 일**
- 접속기록 Agent (`apps/platform-api/app/agent/`)
  - `@access_log(action, data_category)` 문패 / `@access_log_exempt("사유")` 명시적 제외
  - 기록지 `AccessRecord`를 contextvars에 두고 `record_actor`·`record_subjects`로 채움
  - 순수 ASGI 미들웨어 `AccessLogMiddleware`: `/admin` 경로만, 응답 헤더를 보내기 직전에 outbox 적재(업무와 별도 트랜잭션), 적재 실패 시 500 `ACCESS_LOG_UNAVAILABLE`(fail-closed), 처리되지 않은 예외는 FAILURE로 적재 후 재발생
  - `check_admin_routes`: 문패·제외 표시 없는 `/admin` 라우트가 있으면 앱 기동 거부
  - 접속 IP: `PLATFORM_TRUSTED_PROXIES`에 등록된 프록시에서 온 요청만 `X-Forwarded-For`를 오른쪽부터 읽음
- 기존 라우트 연결: 로그인 `LOGIN`/`NONE`(존재하는 계정일 때만 식별자 기록), 로그아웃 제외, 목록 `READ`(표시된 회원 PK), CSV `DOWNLOAD`(파일에 담긴 회원 PK), 인증 의존성이 식별자 기록
- relay (`app/relay.py`, compose `platform-relay`): 2초 주기, topic별 100건, `FOR UPDATE SKIP LOCKED`, HMAC 서명, api-spec 1-5 응답별 처리, 백오프 1분→최대 1시간. platform-net + argus-net
- compose `PLATFORM_TRUSTED_PROXIES`(기본 빈 값), `.env.example`·README 갱신. `.env`에 추가할 필수 값 없음(relay는 기존 `ARGUS_INGEST_SECRET_PLATFORM` 공유)
- 검증
  - pytest 101개 통과(PR ② 44 + 신규 57), ruff 통과
    - Agent: 120건 다운로드 기록(식별자·접속지·PK 120·건수·키 이름만), payload에 이름·이메일·전화·주소 없음, 검색값 미기록, 목록 READ, 로그인 성공·실패 기록, 없는 ID·미인증 미기록, 로그아웃 제외, **고객 라우트 미기록**, **업무 예외에도 FAILURE 기록**(대상 PK 포함), 400도 FAILURE(건수 0), **적재 실패 시 CSV 차단**, 경로 변수 값 미기록(템플릿), 1,000개 초과 잘림, 문패 누락 시 기동 거부, 잘못된 문패 거부, 위조 XFF 무시, 요청 간 기록지 격리
    - Agent 26개(위), 접속 IP 9개, relay 22개(삭제·서명·DEAD·topic 경로·100건 배치·400·401/503/429/404/네트워크/해석 불가 200 → PENDING 백오프·413 분할·1건 413·백오프 상한·대기·DEAD 제외·잠긴 행 건너뜀)
  - **실제 컨테이너 끝-끝**: relay가 argus-api보다 먼저 떠 HANDLER 5건 첫 전송이 NETWORK_ERROR → PENDING·1분 백오프(설계대로 유실 없음). ops_park 로그인 실패·성공 → 목록 → CSV 120건 → Argus `access_log`에 LOGIN FAILURE·LOGIN SUCCESS·READ(20)·DOWNLOAD(120) 도착. 1분 뒤 HANDLER 5건 accepted → Argus `handler` 5명 + A5 계정 5개. 플랫폼 outbox 0건, `verify_chain` ok(6건)

**M2 완료 기준 대조** (CLAUDE.md 6절): ② 취급자 동기화 수신 ✅ / `operator`·`member` + 시드(회원 500·취급자 5) ✅ / 관리자 로그인 ✅ / `GET /admin/members/export` ✅ / 미들웨어 + 데코레이터(contextvars) ✅ / outbox 별도 트랜잭션 ✅ / relay(100건·지수 백오프·401 PENDING) ✅ / 취급자 동기화 호출 ✅ / 테스트: 고객 라우트 미기록 ✅, 업무 실패 FAILURE ✅

**결정사항**
- **`request.path`는 실제 URL이 아니라 라우트 템플릿**(`/admin/members/{member_id}`) — 경로 변수에 실린 값(검색어·이름)이 원장에 남지 않게. 대상 회원은 `subject.ids`로 이미 기록됨
- 검색조건 키가 키 이름 형식(`[A-Za-z0-9_.\[\]-]{1,64}`)이 아니면 버림 — Argus가 이벤트 전체를 거부하지 않게, 값이 키 모양으로 새지 않게
- 직접 연결 주소가 IP가 아니면 접속지를 지어내지 않고 요청을 실패시킴(fail-closed). 테스트 클라이언트 주소는 RFC 5737 문서용 대역 `203.0.113.10`
- `subject.count`는 `max(기록된 건수, ids 개수)` — Argus의 "count ≥ ids 개수" 검증과 어긋나지 않게
- 미들웨어는 가장 바깥(쿠키 연장 미들웨어보다 바깥) — 최종 응답 상태로 결과를 판정하고, 적재 실패 시 쿠키까지 포함한 응답 전체를 막음
- **순수 ASGI 미들웨어**: 응답 시작 메시지를 가로채 적재 성공 후에만 내보내야 함
- contextvars는 **가변 객체 하나를 공유**하는 방식 — 동기 핸들러·의존성이 스레드풀의 컨텍스트 복사본에서 실행되므로 `set()`은 미들웨어에 보이지 않음
- relay
  - topic 순서: HANDLER 먼저, 그다음 ACCESS_LOG — 명부가 기록보다 앞서 있게(순서가 뒤집혀도 Argus는 수락 후 조인으로 연결, api-spec 2-6)
  - 전송하는 동안 `FOR UPDATE SKIP LOCKED` 락을 유지(트랜잭션 안에서 전송·결과 반영) — 여러 relay가 떠도 중복 전송 없음. HTTP 타임아웃 10초
  - 200인데 본문 해석 불가·404 등 예상 밖 응답·1건짜리 413은 **DEAD가 아니라 PENDING + 백오프**(절대 규칙 #7). 일시 장애(네트워크·429·5xx)는 WARNING, 설정 문제(401·404 등)는 ERROR 로그
  - HTTP 클라이언트는 표준 라이브러리 `urllib`(런타임 의존성 추가 없음), URL 스킴을 http(s)로 제한(urllib이 `file://`도 열기 때문)
  - `depends_on`에 argus-api를 걸지 않음 — Argus가 꺼져 있어도 relay는 떠서 대기, 살아나면 전송. 이미지 HEALTHCHECK(8000번 웹 서버용)는 끔
  - 로그에는 건수·오류 코드만, payload(회원 PK) 미기록

**기각한 대안**
- `BaseHTTPMiddleware`로 Agent 작성: 응답이 이미 만들어진 뒤에만 개입 가능 → 적재 실패 시 본문 차단이 어려움
- 데코레이터가 핸들러를 감싸서 기록: FastAPI가 보는 시그니처·의존성 주입이 깨지기 쉬움 → 표시만 붙이고 미들웨어가 매칭된 라우트에서 읽음
- 기록지를 `request.state`에 두기: 서비스 계층 깊은 곳에서 request 없이 기록하려면 contextvars가 맞음(설계 3-1 ThreadLocal 대응)
- relay가 행을 "선점" 표시(lease 컬럼)하고 락 없이 전송: outbox 스키마 변경이 필요 → 락 유지로 충분(Skeleton은 relay 1개)
- relay에 `httpx` 런타임 의존성 추가: 기능상 이점 대비 공급망 표면 증가 → `urllib` + 전송 함수 주입(테스트는 가짜 Argus)

**설계 변경** (Cowork에서 원본 반영 필요 — 전날 1~5와 함께)
6. **`request.path`에는 라우트 템플릿을 보낸다** — api-spec 2-2 `request.path` 설명("호출된 관리자 기능")을 구체화 / 영향: api-spec 2-1·2-2
7. **로그아웃은 접속기록 대상에서 명시적으로 제외**(`@access_log_exempt`) — 개인정보 처리가 없고 api-spec 수행업무 코드에 로그아웃이 없음 / 영향: api-spec 2-4 표, actor-flows
8. **정보주체를 기록하기 전에 실패한 요청은 `subject.count = 0`**, 대상이 정해진 요청은 핸들러가 업무 로직보다 먼저 대상 PK를 기록하는 것을 규칙으로 함 / 영향: api-spec 2-2·2-4, architecture 3-2 #3
9. **문패 없는 관리자 라우트가 있으면 앱 기동 거부** — 기록 누락을 조용한 사고가 아닌 즉시 드러나는 실패로 / 영향: architecture 3-1(URL → 업무 매핑)

**미결·이슈**
- (보안성 검토 후보) **취급자 명부는 현재 상태만** 보유 — 부서 이동 후 과거 기록을 조회하면 현재 소속으로 보임. 탐지는 수 분 내 판정·`log_summary`·`rule_snapshot`으로 보완. 소속 이력이 필요하면 명부 이력화 검토
- (보안성 검토 후보) relay ↔ argus-api 구간은 도커 내부망 http — api-spec 1-1은 HTTPS 전제(HMAC으로 무결성·인증은 확보, 기밀성은 내부망 의존)
- (보안성 검토 후보) DB 직접 접속(DBeaver 등)은 Agent가 기록하지 못함 — architecture 6절의 2단계(pgaudit) 범위. 사용자가 DBeaver로 시드 데이터를 조회하며 확인
- DEAD 건 운영 알림은 ERROR 로그뿐(api-spec 7절 미결), DEAD 재처리 도구 없음
- relay는 전송 중 락을 잡으므로 Argus 응답이 느리면 처리량이 떨어짐 — Skeleton 규모에선 무관

**다음 할 일**
- PR ③ 머지 → **Cowork "구현" 방에서 진행기록·설계 변경(1~9) 동기화** → `docs:` PR로 사본 갱신
- M3: argus-worker 탐지 배치(대량 다운로드 룰, `id` 커서, 진행 중 탐지건에만 하위 로그 추가)

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
  - **해소 (2026-09-30)**: PR #9 머지(10:32:59 UTC) **2초 뒤 PR #8이 자동으로 Merged 처리**(10:33:01). 새 push에서 GitHub가 열린 PR을 재점검해 커밋 포함을 감지. main CI도 `afbd789`로 정상 실행. Close 없이 "Merged" 기록으로 남음

**보안 점검 결과 트리아지** (report-only 스캔 결과를 검토·판정한 기록 — architecture 8-2 "결과를 근거로 조치")
- **CodeQL `py/clear-text-logging-sensitive-data` (High)** — `apps/platform-api/app/scripts/seed.py:108` (PR #9에서 신규)
  - 판정: **오탐**. 로그 인자는 최소 길이 상수 `MIN_PASSWORD_LENGTH`(=12)와 고정 문구이며 비밀번호 값(`password` 변수)은 전달되지 않음. 변수명의 `password`에 반응하는 휴리스틱 탐지
  - 조치: 코드 수정 없이 GitHub에서 False positive로 해제, 사유 기록 (2026-09-30, 사용자). PR 상태는 `UNSTABLE`(필수 아닌 검사 실패)로 머지 차단은 아니었음
  - 기각한 대안: 상수명 변경 등으로 경고 회피 — 동작 차이 없이 "검토 후 판정했다"는 기록만 사라짐

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
