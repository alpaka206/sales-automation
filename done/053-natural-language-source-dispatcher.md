# 053 — 자연어 입력 → 소스 디스패처 (intent router)

## Why

사용자가 웹에서 "구독자 10만+ 의료기기 유튜브 채널" / "성형외과 마케팅 채용 공고" 같은 자연어 입력 → BE 가 적절한 소스 + 파라미터 결정 → 발굴 실행.

## What to do

1. `src/agents/outbound/dispatcher.py` 신규:
   ```python
   class IntentRouterResult(BaseModel):
       source: Literal["youtube", "linkedin_comments", "google_search",
                       "job_board", "manual_csv"]
       filters: dict
       confidence: float        # 0.0–1.0
       rationale: str
       requires_user_input: list[str] = []  # 추가 정보 필요 시 (예: LinkedIn post URLs)
   ```
2. `src/llm/prompts/outbound/intent_router.md` 신규 — 자연어를 받아서 위 schema 로 응답하는 프롬프트. 예시 few-shot 포함.
3. `OutboundAgent` 에 `run_from_natural_query(user_query: str)` 메서드 추가:
   - intent_router 호출
   - `requires_user_input` 비어있으면 즉시 `run(source, filters)`
   - 비어있지 않으면 `outbound_intents` 테이블에 저장하고 사용자에게 추가 입력 요청 (UI 가 후속 처리)
4. `outbound_intents` 테이블 신규 (마이그레이션 0008):
   - id, user_query, routed_source, routed_filters (JSON), status (`pending_user_input` / `dispatched` / `failed`), created_at.

## Acceptance criteria

- "구독자 10만+ 의료기기 유튜브 채널" → `{source: "youtube", filters: {query: "의료기기", min_subscribers: 100000}}`
- "성형외과 마케팅 채용 공고" → `{source: "job_board", filters: {keyword: "성형외과 마케팅"}}`
- 모호한 입력 (예: "사람 찾아줘") → `confidence < 0.5` + 라우팅 거부.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_intent_router.py -q
```
