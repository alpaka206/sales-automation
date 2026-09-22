# 코드 기반 발견과 최종 처분

severity는 이번 검토의 고객 영향 추정이며 운영 발생률 측정은 아니다. 코드 위치는 변경 후 함수명으로 연결한다.

| ID | 심각도 | 증상 → 위치 → 재현 → 영향 | 처분 |
|---|---|---|---|
| F01 | HIGH | 긴 파생 요약 → InboundAgent._build_conversation_context → 긴 summary 뒤 최신 정정 → 잘못된 요청에 답할 수 있음 | MUST_FIX, 최근 원문 예산 우선 |
| F02 | HIGH | 리마인더가 응답 기준선 이동 → latest_customer_message → 정정 뒤 reminder → 미답변 정정 유실 | MUST_FIX, reminder 제외 |
| F03 | MEDIUM | test_sent를 실제 답변으로 취급 → thread_events → 테스트 발송 행 → first/followup 정책 오선택 | MUST_FIX, sent만 인정 |
| F04 | HIGH | DB 장애를 빈 정책/이력으로 반환 → _rules_from_db/thread_events → 읽기 예외 → 필수 맥락 없는 정상 초안 | MUST_FIX, 오류 전파 |
| F05 | HIGH | rules/knowledge가 다른 시점 → _draft_reply/LLMClient.complete → body/access/version/scope 변경 → 서로 다른 정책 적용 | MUST_FIX, snapshot·hash 검사 |
| F06 | HIGH | 승인 이후 수정 → approve/senders.send → 본문·수신자·정책·정정 mutation → 미검토 내용 전송 | MUST_FIX 범위 구현, legacy/동적 HTML은 PARTIAL |
| F07 | MEDIUM | 과거 요약만 표시 → 티켓/리드/수주 화면 → 이전 티켓 조회 → 실제 근거 확인 어려움 | MUST_FIX 사용자 요청, shared records box |
| F08 | MEDIUM | 첫 답변 전 정정/동기화 요청 추출 누락 → _draft_reply/_extract_requests → interaction 정정/철회 → 과거 요청 재사용 | MUST_FIX, 동일 원문 및 빈 철회 허용 |
| F09 | MEDIUM | 파싱 오류/라우터 사유에 원문 → LLMClient/knowledge logger → sentinel 응답 → 로그 노출 가능 | MUST_FIX, 개수/ID 위주 로그 |

F01~F04는 수정 전 5개 테스트 실패로 직접 재현했다(F04 두 테스트). F05/F06/F08/F09는 코드 경로 분석과 변경 후 regression/mutation 시험이다. 실제 운영 사고를 관측했다는 주장은 아니다. F07은 사용자가 변경한 UI 요구사항이며 과거 요구사항 자체를 결함으로 취급하지 않는다.

## 미해결

- HIGH: 정책 권위·시행일·계약 예외 원천 미확정, 의미 validator 없음. 담당자 판단 전 정책 자동화 확대 제한.
- HIGH: legacy/manual/template의 생성 trace 부재, dynamic signature/settings, 원격 동기화/송신 경쟁. 새 trace가 있는 초안의 로컬 변경만 이번 경계가 검출한다.
- MEDIUM: 라우터 400자 인덱스가 예외를 누락할 가능성. 현재 오류율 NOT_RUN, 전체문서 paired 비교 필요.
- MEDIUM: Event JSON 검색/전체 문서 hash와 history 전체 로딩의 데이터량별 p95 미측정. 실제 크기에 따라 pagination/index 검토.
최종 대조에서 첫 회신 가격 send guard도 같은 실제 대화 판정을 쓰도록 통일했다. 리마인더/test_sent와 HubSpot에서 직접 보낸 답변을 별도 회귀로 확인한다. 이 가격 가드를 의미 정책 전체 validator로 일반화하지 않는다.
- LOW: 기존 Starlette deprecation, Vite 정적 CSS/번들 경고.
