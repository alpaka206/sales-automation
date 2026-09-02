# Project Context

PERSO Inbound is a FastAPI workflow for inbound inquiry handling and customer operations.

## Invariants

- **External-write safety (대전제, top priority).** `LIVE_EXTERNAL_WRITES` defaults to `false` (SAFE). While safe: HubSpot writes, Google Sheets writes, and outbound email delivery are blocked. The application never substitutes an internal test recipient. Reads stay on. Enforced in `src/common/safe_mode.py`; guaranteed behavior is pinned by `tests/test_safe_mode.py`. Any new external-write/send path must use the same gate and add a safety test.
- **Human-approved email delivery is live.** `EMAIL_SENDING_ENABLED = True`; only an operator-approved draft is claimable. Immediate inbound acknowledgements were removed structurally. A draft whose body is still Korean when the thread's target language is not cannot be approved or sent until the operator presses 번역하기 (`approval.translation_required`). Delivery replies on the ticket's existing HubSpot Conversations thread. The actor is `HUBSPOT_SENDER_ACTOR_ID`; the actual From address comes from the thread's email `channelAccountId` (or the same-Inbox fallback account for a form-only thread). SMTP and CRM email-activity logging are not delivery paths.
  - **발송 payload 는 문서가 아니라 이 포털이 정한다** (2026-08-26, 첫 실전 발송 msg 62 가
    이것으로 실패했다). HubSpot 문서의 예시에는 수신자에
    `"actorId": "E-user@hubspot.com"` 이 있는데 **발송 엔드포인트가 그것을 거부한다** —
    `Actor type EMAIL is not supported for receiving`. 그래서 수신자는 `recipientField` 와
    `deliveryIdentifiers` **주소로만** 적는다. **읽기 검증으로는 절대 못 잡는다**: actor 조회
    (`GET /conversations/v3/conversations/actors/E-<메일>`)는 그 ID 를 200 으로 돌려주고
    `{"type":"EMAIL"}` 이라고 답한다. 고칠 때 기준으로 삼을 것은 문서도 actor 조회도 아니고
    **그 포털에서 실제로 나간 메시지**다 — `GET .../threads/{id}/messages` 로 성공한
    OUTGOING 한 건을 열어 `senders`·`recipients` 모양을 그대로 베껴라. `tests/
    test_hubspot_conversations.py::test_the_recipient_is_an_address_not_an_email_actor`
    가 고정한다(기존 발송 테스트는 `senderActorId`·`channelAccountId`·`deliveryIdentifiers`
    만 봐서 `actorId` 가 있든 없든 통과했다 — 그게 뚫린 구멍이었다).
  - **HubSpot 400 의 이유는 `message` 가 아니라 `errors[]` 에 있다.** `message` 는 원인이
    무엇이든 언제나 `"Multiple errors validating request."` 한 문장이라, 그것만 로그에 남기면
    「무언가 틀렸다」까지만 말하고 무엇이 틀렸는지는 어디에도 안 남는다. `_lookup_error` 가
    둘 다 싣는다 — 요약은 모양을, 배열은 필드를 말한다. **이 한 줄이 없으면 발송 실패는
    로그만으로 진단이 불가능하고, 알아내는 데 실제 발송을 한 번 태워야 한다.**
  - **실패는 이유를 행에 남긴다** (`messages.send_error`, 0093). 로그는 30분이면 스크롤 밖이고,
    그때 화면에 남는 것은 빨간 「발송 실패」 배지 하나뿐이었다. `post_send_sync_error` 를
    쓰면 안 된다 — 그 칸은 「메일은 나갔고 기록만 실패했다」는 뜻이고, 복구 화면이 그 둘을
    다른 목록으로 갈라 다르게 처리한다. 사유는 티켓 화면 배너와 복구 화면 두 곳에 뜨는데,
    출처는 이 한 칸이다.
  - **폼 스레드 폴백은 실전에서 동작한다 — 채널 계정을 의심하지 마라.** 2026-08-26 에 폼
    스레드(`originalChannelId: 1003`)로 온 문의에 `HUBSPOT_DEFAULT_EMAIL_CHANNEL_ACCOUNT_ID`
    (support@perso.ai)로 회신이 나갔고 `status: SENT` 로 남았다. 그 계정이 이 포털에서 오간
    적 없는 계정처럼 보여 한 번 의심했는데 **아니었다**: HubSpot 은 검증 오류를 한 번에 다
    돌려주므로(그래서 "Multiple errors") 채널 계정 얘기가 없으면 통과했다는 뜻이다.
  - **「티켓 → Email → Create an email」은 API 로 못 한다.** 운영자가 아는 그 동작은 UI
    전용이다. CRM Emails engagement API 는 **기록만** 하고 발송하지 않으며(공식 문서 원문:
    "log and manage emails"), 우리 토큰의 `sales-email-read` 는 읽기 전용이라 기록조차 못
    만든다. Transactional Single-Send 는 `transactional-email` 스코프 + Marketing Hub
    Pro/Ent + 부가상품이 필요하고 티켓 스레드에 회신도 안 된다. **API 가 실제로 메일을 보내는
    길은 Conversations 스레드 회신 하나뿐이고**, 그 회신은 티켓 스레드에 그대로 붙으므로
    고객이 받는 메일도 티켓에 남는 기록도 같은 자리에 선다.
- **연락처 링크는 언어가 정하고, 정하는 곳은 `canonicalize_contact_links` 한 곳이다.**
  국문 회신에는 WhatsApp 을 붙이지 않고 링크 글자는 「미팅 링크」, 영문에는 둘 다 붙이고
  `Calendly` · `WhatsApp` 이다. **서식(`reply_format`)만 고치면 안 된다**: 0069 가 국문
  서식에서 `{{WHATSAPP}}` 을 뺐는데도 국문 메일에 WhatsApp 이 계속 나갔다 — 이 함수가
  모델이 쓴 링크 줄을 전부 지우고 푸터를 **다시 만드는데**, `language` 를 「어느 행에서 URL 을
  읽을지」 고르는 데만 쓰고 「그 줄이 붙어야 하는지」는 보지 않았기 때문이다. 발송 경로가 이
  함수를 마지막에 부르므로 **여기서 붙인 것이 곧 고객이 받는 것**이고, 서식·정책 문서에 무엇이
  적혀 있든 이 함수가 이긴다.
- **The CRM/workbook are LIVE.** `LIVE_EXTERNAL_WRITES=true` with `LIVE_HUBSPOT_WRITES` / `LIVE_SHEETS_WRITES` both on, so a stage moved in the console moves the HubSpot ticket and updates the Inbound DB row. Every screen write goes through the same routes the Jinja forms used, which is why that stayed true through the React port.
- **Per-destination switches are subordinate.** `LIVE_HUBSPOT_WRITES` / `LIVE_SHEETS_WRITES` (both default `true`) select which destinations go live *after* the master is on; neither can permit a write while `LIVE_EXTERNAL_WRITES` is `false`. `guard_external_write("<channel>:<action>")` picks the gate from the label prefix, and an unregistered channel falls back to the master — so a new write path is blocked by default.
- HubSpot tickets are accepted only from `HUBSPOT_TICKET_STAGE_NEW` when a ticket ID exists.
- **파이프라인 단계는 키와 이름이 따로 움직인다.** HubSpot 에서 이름이 바뀌어도 stage id 는 그대로다 — Meeting link sent 는 **Qualified** 로, Closed 는 **Not a Fit** 으로 이름만 바뀌었다. 그래서 로컬 키(`meeting_link_sent` · `closed`)도 그대로 두고 `customer_ops.PIPELINE_STAGES` 의 표시 이름만 고친다: 키를 따라 바꾸면 `conversations.stage` 와 `customer_profiles.pipeline_stage` 두 열을 옮기는 마이그레이션이 필요하고, 다음에 이름이 또 바뀌면 그걸 또 한다. 이름의 출처는 그 튜플 **한 곳**이다(화면·칩·목록이 전부 거기를 읽는다). 새 이름을 `.env` 에 새 변수명으로 적어도 되게 옛 이름을 `AliasChoices` 에 남긴다 — 그리고 **새 별칭을 넣을 때마다 `tests/conftest.py` 의 blanking 목록에도 넣는다**: 한 철자만 비우면 개발자 `.env` 의 다른 철자가 pytest 로 실제 티켓을 옮길 수 있다(`tests/test_safe_mode.py` 가 잡는다).
  - **Deal Detail 은 Won 과 Lost 에만 있다** (`DEAL_DETAILS`). 왜 이겼나(PoC/Contract/Renewal)와 왜 졌나(여섯 가지)는 결말이 난 건에만 있는 정보라 다른 단계에는 채울 답이 없다. **열은 하나**(`conversations.deal_detail`)다 — 한 문의가 동시에 이기고 지지 않으므로 어느 목록의 값인지는 그때의 단계가 정하고, 단계가 바뀌면 값은 남되 화면에는 안 나온다(되돌아오면 다시 뜬다). 우리 DB 에만 쓴다: 그 파이프라인에 대응하는 HubSpot 속성이 있는지 확인되지 않았고, 없는 속성에 쓰면 요청마다 400 이다.
- **서명은 사람이 고르는 것이고, 코드는 본문에 넣지 않는다.** 예전에는 두 벌이었다 — 회사 규칙 안의 `{{__signature__}}` 가 `signature_ko` 행을 프롬프트에 끼워 모델이 **본문에** 서명을 쓰게 했고, 그래서 발송 경로에는 운영자가 다른 서명을 고르면 그 텍스트를 도로 찾아 떼어내는 기계(`strip_known_signature`, 번역된 메일에서는 메일 주소를 앵커로 잘랐다)까지 있었다. 지금은 한 벌이다: 운영자가 초안에서 고르고 `발송` 을 누르면 그때 본문 아래로 붙는다(`messages.signature_key` → `branded_signature_html` → `to_html_email`). 되살리기 전에 왜 두 벌이면 안 되는지부터 읽어라 — 0061 의 docstring 에 적혀 있다.
  - **서명은 데이터다.** 접두사 `signature_` **하나**가 목록의 서명 묶음과 검토 화면 고르개를 가른다. 둘이 같은 집합을 가리켜야 한다 — 예전에 `signature_html_`(고르개)과 `signature_`(목록)로 갈라져 있었고, 그래서 화면에는 서명으로 보이는데 고를 수 없는 행이 있었다. 추가·수정·삭제 전부 콘솔에서 되고, 코드가 이름으로 찾는 서명은 하나도 없다. 글로 써도 되고 HTML 로 써도 된다.
  - **지우면 행이 사라지고, 그때 내용은 판본 이력에 남는다** (2026-08-27, 이관 0100).
    `email_templates.key` 와 `policy_sources.doc_key` 는 **unique** 이고 만들기 라우트가
    상태를 안 보고 중복을 막는다. 소프트 삭제로 두면 **지운 행이 그 이름을 영원히 붙들고**
    있다 — `reply_format` 을 한 번 지우면 다시는 그 이름으로 못 만들고, 그 이름은 발송
    경로가 찾는 이름이다. 7일 청소가 있던 동안에는 결국 풀렸지만, 그 청소를 없애면서
    영구 잠금이 됐다. 그래서 하드 삭제다: 스냅샷을 **먼저** 남기고(`change_note='deleted'`)
    행을 지운다. 「DB 에서 볼 수 있게 영원히 지우지 않는다」는 그 이력이 지킨다.
  - **지운 것은 화면에서 바로 사라진다** (2026-08-27 운영자 지시).
    삭제 확인 창(이름을 옮겨 적어야 한다)을 지나면 그 자리에서 없어진다. **7일 휴지통은
    없다**: `soft_delete` 모듈과 `purge_expired`, 되돌리기 버튼과 라우트가 다 나갔다.
    되살릴 재료는 히스토리에 있다.
    - **그래서 「Gemini 는 안 본다」가 더 중요해진다.** 읽는 쪽 셋이 전부 이미
      `status='active'` 만 본다 — 서명·링크 조회(`db/email_templates`), 항상 적용 규칙
      (`llm/prompts._rules_from_db`), 문의별 참고 문서(`llm/knowledge._is_active`). 정책
      문서를 지우면 초안이 읽는 **사본**도 같이 재운다(`_set_knowledge_status`). 그리고
      `document_revisions` 는 `src/llm` · `src/agents` 어디에서도 **이름으로조차** 등장하지
      않는다 — `tests/test_email_template_form.py::test_the_revision_history_is_out_of_
      gemini_reach` 가 두 폴더를 훑어 고정한다.
  - **읽는 코드가 있는 칸만 남는다** (0101). `policy_sources` 에서 `order_index`(읽히기만
    하고 **정할 방법이 없어** 늘 100 이었다 — 순서는 결국 `id` 였고 이제 그렇게 적는다) ·
    `status`(0100 이 하드 삭제로 바꾸면서 `deleted` 가 되는 행이 없어졌다 — 표에 있는 행이
    곧 살아 있는 행) · `summary`(읽는 코드 0, 채워진 행 0 — 라우터가 읽는 요약은
    `usage_note`) · `effective_on`·`edited_at`(「언제 기준인가」를 세 칸이 서로 다르게
    말했다. 답은 `updated_at` 하나 — 저장할 때마다 자동으로 움직인다)를 지웠다.
    `email_templates.status` 와 `document_revisions.status` 도 같은 이유로 나갔다.
    - 그래서 **초안이 읽는 곳에 상태 필터가 없다**: `_rules_from_db` 도 `router_docs` 도
      `get_email_template` 도 그냥 가져온다. 「항상 쓰는 것이니 항상 가져온다.」
  - **안 쓰는 칸은 남기지 않는다** (0100). `email_templates` 에서 `subject`(운영 7행 중
    채워진 행 0개, 읽는 코드 0) · `channel`(행마다 `'email'`) · `deleted_at` 을, 그리고
    `policy_sources.deleted_at` 을 지웠다. **`author` 는 남되 뜻이 바뀌었다** — 만든 사람이
    아니라 **마지막으로 저장한 사람**이고 목록의 수정일 옆에 뜬다.
    - `channel`·`subject` 는 **0019 가 만들고 0100 이 지운 칸**이다. 0019~0056 의 씨앗들이
      그 이름으로 INSERT 하는데 새 DB 는 0001 의 `create_all` 이 지금 모델로 표를 만든다 —
      여덟 개 넘는 옛 마이그레이션의 SQL 을 고쳐 역사를 다시 쓰는 대신, 0019 가 그때의
      모양을 세워 두고 0100 이 그때처럼 지운다. 테스트도 같은 헬퍼를 쓴다
      (`tests/conftest.legacy_template_columns`).
  - **판본 이력은 표 하나이고, 두 종류가 같이 쓴다** (2026-08-27 운영자 지시, 이관 0096).
    `document_revisions` 에 이메일 템플릿과 정책 문서의 이전 판본이 같이 산다. 남기는 곳도
    읽는 곳도 `src/db/revisions.py` **한 곳**이고, 화면은 두 편집기 바닥의 「판본 기록」
    버튼(`ui/RevisionHistory.tsx`) — 같은 컴포넌트다. **표를 종류마다 두면 안 되는 이유는
    이미 겪었다**: `email_template_revisions` 는 쌓이는데 읽는 라우트도 화면도 없었고(0069
    주석은 「이력 화면에 예전 본문이 남는다」고 적어 두었지만 그 화면은 존재한 적이 없다),
    정책 문서는 이력이 아예 없었으며, 그 몫이라던 `knowledge_document_revisions` 는 0016 이
    만들고 아무도 쓰지 않았다(0095 가 지웠다). 남는 시점은 **고치기 직전**이라 맨 위 행은
    「지금 본문」이 아니라 「직전 본문」이고, **만들 때는 안 남긴다** — 갓 만든 행에는
    이전이 없다(남기면 첫 수정 스냅샷과 같은 버전·같은 본문이 두 줄로 선다). 지운 문서는
    7일 뒤 본문과 이력이 **같이** 사라진다(`soft_delete.purge_expired`) — 「7일 뒤
    사라진다」가 사실이어야 하니까. 그 청소는 **종류별로** 고아를 세야 한다: 한 표에 둘이
    사는데 템플릿 id 로만 재면 정책 문서 이력이 전부 고아로 잡혀 사라진다.
  - **LLM 사용량은 기록하지 않는다** (2026-08-27 운영자 지시, 이관 0095). `llm_usage` 는
    호출마다 한 줄씩 쌓였는데 읽는 곳은 `POST /run/report` 하나였고 콘솔에 버튼도 스케줄도
    없었다. 되살리기 전에 **어느 화면이 그것을 읽는지부터 정해라** — 그게 없어서 이렇게
    됐다. 같은 숫자는 Vertex 콘솔에 있다.
  - **이메일 템플릿은 정책 문서처럼 콘솔에서 추가·수정·삭제한다.** 현재 실행 코드가 직접 찾는 키만 「발송 경로 사용」으로 표시하며, 제거된 `auto_ack*` 키는 다시 만들 수 없다. 즉시 접수확인 기능과 그 템플릿은 0087에서 함께 제거되었다.
- **수주 고객은 Client ID 로 묶인다.** `clients` 아래 `client_contracts`(차수별), 그 아래 크레딧 지급 회차·분납 회차. 소통 히스토리만 예외로 고객 단위라 새 테이블을 만들지 않고 `customer_interactions` 에 `contract_seq` 만 붙였다 — 협상 단계 대화가 계약보다 먼저 쌓이고 그대로 이어져야 한다(0065).
  - **고객을 `contacts` 로 대신할 수 없다.** Contact 는 이메일 신원이라 한 회사에 담당자가 셋이면 셋이고, Outbound·Interactive·AX 고객은 문의를 보낸 적이 없어 아예 없다. 인바운드 고객만 `clients.contact_id` 로 연결된다.
  - **Client ID 는 고객사 하나에 하나.** 전에는 문의 하나에 하나였다(`suggest_inbound_client_id` 가 대화마다 새 번호). 그러면 같은 회사가 두 번 문의할 때 계약·크레딧·소통 히스토리가 두 갈래로 갈라진다. `agents/client_ids.py` 가 ① 그 연락처의 번호 ② 같은 회사 도메인의 다른 담당자 번호(**개인 메일 도메인 제외** — gmail 둘을 한 회사로 묶으면 남의 계약이 보인다) ③ 새 발급 순으로 정한다.
  - **고객 종류는 저장하지 않는다.** 번호대가 곧 종류다(1000 Inbound / 2000 GTM Outbound / 3000 Interactive / 4000 AX / 9000 레거시). 둘 다 저장하면 서로 다른 행이 반드시 생긴다.
  - **Won 감지는 `stage_sync.sync_stage_from_hubspot` 한 곳.** 웹훅·10분 폴러·수동 최신화가 전부 이 함수를 지난다. 감지를 세 군데 두면 하나가 조용히 빠진다. Won 티켓은 `pending_won` 에 쌓이고 계약 정보는 사람이 채운다 — 금액도 기간도 없는 고객이 활성 고객 수와 예상 MRR 을 오염시키면 안 된다.
    - **대기의 반대말은 `client_contracts.ticket_id` 다.** 그 티켓으로 등록된 계약이 있으면
      대기가 아니다 — 계약 정보를 이미 받았다. `done` 만 보던 시절에는 부족했다: 계약이
      시트에서 들어왔거나(`sheet_to_db`) 운영자가 계약에 티켓을 손으로 적은 건은 대기 카드를
      지난 적이 없어 `done` 행 자체가 없고, 그래서 백필과 10분 스윕이 그 티켓을 훑을 때마다
      **이미 등록된 고객이** 「계약 정보를 입력해야 합니다」로 돌아왔다(운영자 실측 3건).
      막는 곳은 둘이다 — 들어오는 쪽은 `_enqueue_pending_won`, 나가는 쪽은
      `won_customers._claim_ticket`(계약에 티켓을 적으면 그 대기가 닫힌다 — 계약 **수정**에서도).
      **후자는 Client ID 가 달라도 막지 않는다**: 어느 고객의 티켓인지는 방금 운영자가 적어서
      정한 것이다. 다만 **옮겨지는 것은 대기 행의 번호뿐이다** — 문의·연락처의
      `sheet_client_id` 는 비어 있을 때만 채우므로(`_close_pending`) 「한 회사에 번호가 둘」인
      상태 자체는 남고, 그 둘을 합치는 것은 `client_merge.merge_client_ids` 다(콘솔에 버튼은
      아직 없다). 쌓여 있던 행은 이관 0090 이 치운다.
    - **Won 에서 벗어나면 대기에서 내려간다** (`stage_sync._retire_pending_won`). Won 이
      아니라는 것은 계약 정보를 받을 일이 없어졌다는 뜻인데, 그대로 두면 카드가 영영 남고
      운영자는 그것이 살아 있는 일감인지 되돌려진 건인지 화면만 봐서는 모른다. 다는 곳은
      `_retire_superseded_drafts` **안**이다 — 단계를 옮기는 여덟 경로(HubSpot 동기화 ·
      콘솔 보드 · 고객 상세 폼 · 워크북 · 백필 · 리컨사일)가 전부 그 함수를 지나므로, 옮기는
      곳마다 따로 달면 하나가 조용히 빠지고 그 경로로 옮긴 건만 카드가 남는다. **그 함수가
      돌려주는 수는 초안만 센다**: `_finalize_draft` 가 그 값으로 「내 초안이 밀렸나」를
      판단하므로 대기를 내린 것까지 더하면 멀쩡한 초안이 밀린 것이 된다.
      - **`dismissed` 이지 `done` 이 아니다.** `done` 은 「계약을 받았다」라서, 그것으로
        닫으면 그 티켓이 다시 Won 이 되어도 카드가 안 돌아온다. `dismissed` 는 「지금 Won 이
        아니다」이고 `_enqueue_pending_won` 이 `pending` 으로 되살린다. 그리고 「벗어났다」는
        **매핑된 단계**로만 잰다(`_MAPPED_STAGES`) — `!= "won"` 으로 세면 모델 기본값
        `initial` 인 티켓, 즉 아직 아무도 손대지 않은 건의 카드까지 내려간다.
      - **딸려 있던 계약 0건짜리 고객은 지우지 않고 「장부에서 내린다」**
        (`_retire_empty_client` → `clients.retired_on`, 2026-08-25 운영자 지시). 안 그러면 그
        고객이 「세팅중」으로 목록과 워크북에 남아 활성 고객 수를 부풀린다. **지우면 안 되는
        이유는 번호다**: Client ID 를 문의·연락처가 들고 있고, 워크북의 계약·회차 탭과
        Inbound DB 가 그 행을 조회해 회사명을 가져온다 — 한 건이 Won 에서 물러났다고 그
        연결을 끊을 이유가 없다. 계약이 하나라도 있으면 손대지 않고, 그 외에는 조건이 없다:
        되돌릴 수 있고(`POST /won-customers/{id}/retire`, `retire=0` 이 해제 — **빈 문자열이
        아니다**, 빈 폼 값은 중간에서 사라져 해제가 조용히 내리기가 된다), 계약이 들어오면
        `_add_contract` 가 그 칸을 비워 저절로 되돌아온다.
      - **내린 고객은 목록에서 숨는다.** 플랜 상태 고르개에서 「내림」을 골랐을 때만 보인다 —
        안 보이는 행을 다시 볼 길이 하나는 있어야 한다. 「내림」은 `PLAN_STATUSES` 에 **없다**:
        그 셋은 워크북 드롭다운의 값이고, 거기 없는 말을 시트에 쓰면 그 행이 영업팀의 어느
        필터에도 안 걸린다. 시트에는 빈칸이 가고 **행 자체는 그대로 나간다**(조회 대상이라).
        `retired_on` 은 플랜 상태 열이 아니다 — 그 값은 계약 기간에서 나오는 파생값이고
        (이관 0067), 이 칸은 「사람이 내렸다」는 사실 하나만 들고 있다.
      - 오래전에 벗어나 그 뒤로 안 건드려진 티켓은 10분 스윕(최근 변경분만 훑는다)에 안
        걸린다. 그건 이관 0091(대기)·0092(고객)가 한 번 치운다.
    - **고객은 계약과 함께 만들어진다 — 고객만 있는 순간은 없다.** `POST /won-customers` 가
      고객과 첫 계약을 한 트랜잭션에서 만들고, 계약 정보 없이 부르면 400 이다. 화면(대기
      카드 · 「수주 고객 추가」)은 아무것도 저장하지 않고 계약 폼으로 넘기기만 한다.
      예전에는 클릭 시점에 고객을 먼저 만들었는데, 폼을 채우지 않고 나가면 계약 0건짜리
      고객이 남았고 `won.plan_status` 가 그런 고객을 「세팅중」으로 읽어 워크북 「고객 기본
      정보」에 행을 얹었다 — 지울 길이 없어 누를 때마다 한 줄씩 쌓였다(2026-08-25 운영자
      보고). 고친 곳이 폼이 아니라 라우트인 이유: **고객만 만드는 길이 남아 있으면 다른
      화면이 언젠가 또 그리로 간다.** 이미 생긴 빈 고객은 `POST /won-customers/{id}/delete`
      가 치운다(계약이 있으면 거부, 그 번호를 들고 있던 문의·연락처·대기의 Client ID 도
      같이 비운다 — 없는 번호가 남으면 다음 Won 때 `find_existing_client_id` 가 그것을 도로
      찾아 준다). **워크북의 그 행은 건드리지 않는다**: 동기화는 지금 있는 고객의 행만
      정리하므로 지워진 번호의 행은 「손으로 쓴 행」으로 보이고, 콘솔이 모르는 행을 지우기
      시작하면 운영자가 먼저 채워 둔 회사가 사라진다.
  - **부가세가 붙는지는 통화가 아니라 고객이 정한다** (2026-08-18, 이관 0075).
    `client_contracts.vat_applicable` — 국내 법인이면 해당, 그 외는 미해당. 한동안 `is_krw`
    가 이 판단을 대신했는데(원화면 부가세가 있다) 늘 맞지는 않았다. **NULL 은 「아직 안
    고름」이라 옛 규칙으로 떨어진다** — 이 칸이 생기기 전의 계약 수백 건에 고른 값이 없고,
    없는 것을 「미해당」으로 읽으면 그 원화 계약들의 총액이 한꺼번에 10% 내려앉는다.
    폼의 순서도 그래서 **해당 여부 → 통화 → 환율 → 금액 → 공급가**다: 앞의 것이 뒤의 것을
    정한다. 해당이면 금액 칸이 **둘**이고 한쪽을 적으면 다른 쪽이 10%로 따라온다(계약서가
    어느 쪽으로 적혀 있든 그 숫자를 그대로 넣을 수 있어야 한다). 저장할 때 서버가 **공급가로
    고른 쪽에서 다시 계산**하므로 두 값이 어긋난 채 저장되는 일은 없다 — 예전의 「채우는 칸은
    하나」를 대신하는 규칙이 이것이다. 미해당이면 금액은 하나이고 `amount_incl_vat` 에 산다
    (「포함」이라는 이름은 부가세가 없는 계약에서 그냥 「그 금액」이라는 뜻이다).
  - **환율은 계약 행에 박힌다** (`client_contracts.fx_rate`/`fx_on`, 이관 0075). 비워 두면
    저장할 때 계약일 고시가로 채운다(`fx.usd_krw_on`). 결제 회차에도 같은 이름의 칸이 있는데
    뜻이 다르다: 저쪽은 **입금액을 환산한** 환율, 이쪽은 **계약 금액을 환산할** 환율이다.
  - **중도 해지일과 크레딧 사용량** (`terminated_on`, `credits_used`, 이관 0075). 크레딧
    사용량은 **수동 입력**이다 — 제품 쪽에서 가져오는 경로가 없고, 없는 값을 0으로 두면
    「하나도 안 썼으니 전액 환불」이 되어 해지월 매출이 통째로 음수가 된다. 비어 있으면
    환불액을 계산하지 않는다.
  - **분당 단가의 기준은 「계약서에 적힌 금액」이다.** 통화만으로는 못 정한다: 국내 계약서는 공급가로 적히기도 하고 총액으로 적히기도 하는데, 총액으로 적힌 계약을 공급가 칸에 넣으면 총액이 10% 부풀고 단가가 10% 낮아지며 **화면 어디에도 그게 안 보인다.** 그래서 원화 계약만 `client_contracts.vat_included` 로 어느 쪽인지 행에 박는다 — 켜면 받은 금액이 곧 총액이자 단가의 기준이고(10% 계산 없음), 끄면 공급가를 받아 총액을 +10% 로 계산한다. **채우는 칸은 어느 쪽이든 하나다**(`_one_amount_per_currency`). 그 외 통화는 부가세가 없어 총액만 받고 이 값을 보지 않는다 — `won.vat_included()` 가 통화까지 확인하는 이유다. **예상 MRR 은 기준과 무관하게 언제나 VAT 포함 총액**(`won.total_amount`)을 더한다. 공급가 열은 총액으로 적힌 계약에서도 채운다(`won.supply_amount`, 총액 ÷ 1.1) — 워크북의 그 열은 회계가 합계를 내는 칸이라 비면 그 행만 조용히 빠지고, 화면은 같은 값 옆에 「총액에서 역산」이라고 적는다.
  - **플랜 기간은 계약 기간과 다른 것이고, 폼이 따로 묻는다** (2026-08-31 운영자 지시).
    계약은 먼저 맺고 실제 사용은 늦게 시작하는 일이 흔하다. 한동안 `client_contracts` 에
    `plan_starts_on`/`plan_ends_on` 칸은 있었는데 **폼이 묻지 않아** 저장 경로가 계약
    날짜를 그대로 복사했고, 그래서 MRR 도 「사용중」도 사실은 계약 기간으로 계산되고
    있었다. 이제 「Perso 계정 및 플랜」 절이 두 날짜를 받는다.
    - **비우면 계약 기간과 같다.** 대부분의 계약이 그렇고, 옛 행과 워크북에서 온 행도 그
      기본값으로 떨어진다(`_fill_contract`). 그러니 이 두 줄은 규칙이 아니라 기본값이다.
    - **「사용중」도 플랜 기간이 정한다** (`won.plan_status`). 계약서에 도장을 찍은 날부터
      사용중이라고 적으면, 아직 아무것도 안 쓰는 고객이 활성 고객 수와 예상 MRR 에
      들어간다 — 그 두 숫자를 보려고 만든 화면인데. 중도 해지도 따라온다: `plan_period` 의
      끝이 만료일과 해지일 중 빠른 쪽이라, 해지한 고객은 그날부터 「사용 중단」이다.
    - **계약 한 건의 상태(`contract_state`, 진행 중/세팅중/종료)도 플랜 기간이 정한다.**
      고객 단위와 같은 기간을 봐야, 한 화면에서 고객은 「세팅중」인데 그 밑의 계약 줄은
      「진행 중」이라고 적히는 일이 없다. 중도 해지도 따라온다 — 전에는 만료일이 올 때까지
      「진행 중」이었고, 그건 매출 인식이 이미 멈춘 계약이었다.
      `plan_status` 와 다른 점은 하나: **날짜가 없으면 「진행 중」이다.** 계약 하나를 놓고
      보는 자리라 그게 맞고, 고객 단위에서는 같은 상태가 「세팅중」(아직 채울 것이 있다)이다.
  - **MRR 은 플랜 기간으로 나누고 플랜 기간에 인식한다** (2026-08-18, 운영자 확인).
    계약 기간이 아니다 — 계약은 먼저 맺고 실제 사용은 늦게 시작하는 일이 흔해서, 계약
    기간으로 나누면 아직 쓰지도 않는 달에 매출이 잡히고 정작 쓰는 달에는 덜 잡힌다.
    분모와 인식 창이 **둘 다** 플랜 기간이라 월별 합계가 총 계약금액과 정확히 맞는다 —
    한쪽만 바꾸면 마지막 달이 잘리고, 잘렸다는 표시는 화면 어디에도 없다.
    `revenue_start_month` 도 플랜 시작월이다(`revenue_from` 이 있으면 그것이 이긴다).
  - **중도 해지는 그 달에 한 번에 정산한다.** `해지월 = 총액 − 예상 환불 − 이미 인식한
    MRR`, 그 뒤로는 0. **음수일 수 있다** — 이미 인식한 것이 실제로 번 돈보다 많으면 그
    달에 마이너스로 찍힌다. 지난달들을 소급해 고치지 않는 이유: 마감한 달의 숫자가 나중에
    바뀌면 그 달 보고서가 전부 거짓이 된다. 예상 환불 = **VAT 포함 총액** × (남은 크레딧 ÷
    계약 크레딧). **크레딧 사용량이 비면 정산하지 않고 인식만 멈춘다** — 0 으로 두면
    「하나도 안 썼으니 전액 환불」이 되어 해지월 매출이 통째로 음수가 된다. 분모(월 요금)는
    해지일로 줄이지 않는다: 해지는 「얼마인가」가 아니라 「언제까지인가」를 바꾸는 사건이라,
    분모까지 줄이면 해지한 계약의 월 요금이 갑자기 오른다.
  - **월별 MRR 과 월 매출은 다른 것을 센다.** 카드가 옆으로 넓어진 자리에 최근 6개월을
    펼치고, 두 지표를 나란히 둔다: `mrr_months` 는 플랜 기간에 균등 배분한 **인식** 매출,
    `cash_months` 는 결제 회차가 잡힌 달에 통째로 얹는 **현금흐름**이다(일시불이면 한 달에
    전액, 할부면 회차마다). 둘이 갈릴 때가 그 계약을 봐야 할 때라 같이 보인다.
    차트는 직접 그린다(`won/MonthlyArea.tsx`) — 라이브러리를 써도 된다는 허락을
    받고도 안 쓴 이유는 `docs/암묵지/07` 에 있다. **색은 눈으로 고르지 않았다**: 양수
    `#2A9D8F`(--teal-500)와 음수 `#B42318`(--red-fg)은 색맹 분리도·명도대·채도·대비
    검사를 통과한 쌍이고, 음수는 색만으로 말하지 않는다(0선 아래로 자라고, 툴팁이
    「중도 해지 정산」이라고 쓴다).
    - **막대가 아니라 면이다** (2026-09-02 운영자 지시). 막대는 달마다 따로 선 값이라
      「달과 달 사이」를 말하지 않는데 MRR 은 추세로 읽는 값이고, 무엇보다 **면은 쌓을 수
      있다** — New 를 위에 얹는 것이 이번 요구사항이었다.
    - **New 는 같은 색의 진한 쪽이다**(`--teal-700`). 전체의 일부이지 다른 종류가 아니므로
      색상을 바꾸면 「두 지표」로 읽힌다 — 부분·전체는 명도로 가르는 것이 맞고, 명도차는
      색각과 무관하게 남는다.
    - **New = 그 달에 처음 잡힌 고객의 몫** (`mrr_new_months` · `cash_new_months`).
      **계열마다 자가 다르다** — 한 자로 재면 신규 고객이 어느 달의 New 에도 안 잡히고,
      화면에는 큰 면 옆에 「New ₩0」 이 설 뿐 틀렸다는 표시가 없다:
      `won.first_revenue_month` 는 MRR 이 처음 인식되는 달(PoC 는 **첫 회차의 달** —
      균등 배분할 기간이 없어 `revenue_in_month` 가 그 달에 전액을 잡는다),
      `won.first_cash_month` 는 **처음 입금된 달**이다(월 매출 칸이 그 날짜로 선다).
      `clients.first_won_on` 은 쓰지 않는다 — 그 칸을 채우는 곳이 워크북 임포트 하나뿐이라
      콘솔에서 만든 고객은 전부 비어 있다.
    - **New 는 총액의 부분집합이 아니다.** 총액은 중도 해지 정산까지 반영한 **순액**이고
      New 는 신규 고객이 번 돈이라, 신규가 온 달에 누가 해지하면 New 가 더 크다. 한동안
      총액을 넘지 못하게 눌러 두었는데, 그러면 신규 1,000만 + 해지 −700만인 달이
      「New ₩300만」으로 나왔다 — 틀린 숫자다. **큰 숫자는 자르지 않고**, 0선 위로만 쌓는
      그림에서 자리가 없는 만큼만 그리는 쪽이 바닥을 친다(그 달은 양수 면이 통째로
      진해진다 — 「이 달 매출은 다 신규분이다」이고, 실제로 그렇다).
    - **큰 숫자는 짚은 달을 따라간다.** 「이번 달」과 New 를 나란히 두면 그 다음 질문이
      언제나 「지난 달은?」이라, 차트가 짚은 달을 카드에 돌려주고 라벨이 그 달로 바뀐다.
    - **「VAT 포함」·「입금 기준」은 카드 왼쪽 아래**, 범례와 한 줄이다. 제목 옆 괄호에
      담당부서까지 들어가 있어서 무엇이 지표 이름이고 무엇이 단서인지 흐렸다.
    - **담당부서는 칩이다** (셀렉트가 아니라). 접힌 목록은 지금 어느 팀을 보고 있는지 열어
      봐야 알고, 옆 필터 셋과 생김새가 같아 「목록 필터겠거니」로 읽혔다. 넷뿐이라 다 펼쳐
      둘 수 있다. 그래서 카드 제목에서 담당부서가 빠졌다 — 한 화면에 네 번 적히던 값이다.
    **환산은 서버가 계약마다 그 계약의 환율로 한 번만 한다** — 두 통화를 다 채워 보내고
    화면은 고르기만 한다. 화면이 다시 환산하면 같은 숫자가 화면마다 달라지고, 오늘 고시가로
    과거를 환산하면 마감한 달의 숫자가 오늘 환율에 따라 움직인다. 계약에 환율이 없는 옛
    행만 오늘 고시가로 떨어진다. 「전체」 묶음도 서버가 만든다 — 화면이 부서별 값을 다시
    더하면 그 덧셈이 두 곳에 생긴다. 갱신 임박 고객은 **보드 줄**(크레딧 지급 예정 · 결제 예정)의
    비어 있던 세 번째 칸으로 옮겼다(운영자 지시). 셋 다 날짜가 다가와 손이 가야 하는
    목록이라 한자리에 모인다. **제목이 곧 필터인 것도 같이 옮겼다** — 예전 KPI 카드는
    누르면 목록이 갱신 임박만 남았고, 그 기능이 사라지면 옮긴 것이 아니라 지운 것이다.
  - **환율은 통화와 무관하게 모든 계약에 박는다** (2026-08-31 운영자 지시, 이관 0102).
    예전에는 원화 계약을 「환산할 것이 없다」며 건너뛰었는데, 그건 한쪽 방향만 본 이야기다:
    예상 MRR 카드는 원화 계약을 **USD 로도** 보여 주고, 계약에 환율이 없으면 그 환산이
    매일 오늘 고시가로 다시 일어난다 — 마감한 달의 숫자가 오늘 환율에 따라 움직인다.
    USD 계약에 대해 고친 문제가 원화 계약에는 그대로 남아 있었다(운영 34건 중 **33건**이
    빈 채였다). 폼도 통화로 감싸지 않고 언제나 묻는다.
    - **이 칸은 비어 있으면 안 된다.** `_fill_contract_fx` 가 계약일 고시가로 채우고, 그
      날짜를 못 가져오거나 날짜 자체가 없으면 **오늘 고시가**로 떨어진다. 비어 있는 계약은
      화면이 매일 다른 환율로 환산하기 때문이다.
    - 조회가 전부 실패해도 저장을 막지 않는다 — 계약 하나 저장하자고 외부 API 에 매달릴
      이유가 없고, 운영자가 손으로 적을 수 있다.
    - **정책은 `fx.fill_contract_rate` 한 곳이다.** 계약이 들어오는 길이 셋이고(콘솔 저장 ·
      워크북 임포트 · 이관 0102), 세 군데가 각자 규칙을 들고 있으면 **어느 길로 들어온
      계약이냐에 따라 환율이 달라진다** — 그리고 그건 화면에 안 보인다. 워크북 임포트는
      한동안 아예 안 채우고 있었다.
    - **카드에 「적용 환율」 한 줄을 두지 않는다.** 환산이 계약마다 일어나므로 카드 전체에
      적용되는 환율이라는 것이 없고, 그 줄은 아무 숫자도 설명하지 않았다(2026-08-31 지적).
      대신 **환율이 비어 있는 계약 수**를 센다(`contracts_without_rate`) — 그 계약들만
      오늘 고시가로 환산되고 그 USD 숫자는 매일 달라진다. **0건이면 화면은 아무 말도 안
      한다**: 조용히 떨어지는 것이 문제이지 떨어지는 것 자체가 아니다.
  - **환율은 쓴 시점의 값을 행에 박는다.** 크레딧(`공급가 ÷ 분당 단가 × 60`)에 쓴 환율은 계약에, 입금액에 쓴 환율은 결제 회차에. 오늘 환율로 과거를 다시 환산하면 지난달 매출이 이번 달에 바뀐다. 예상 MRR 카드는 **오늘 고시가를 가져와** 쓴다(인증 불필요, ECB. 수출입은행 키가 있으면 그쪽이 우선). 손으로 적는 칸이었는데, 그러면 두 사람이 다른 환율로 다른 MRR 을 보고 그 값이 언제 것인지 아무도 모른다. `MRR_FX_RATE` 는 조회가 실패했을 때의 바닥값이다. **한국에서 낮에 보면 거의 항상 전일자 고시가 나온다 — 정상이다**: ECB 는 유럽 오후에 하루 한 번 낸다(KST 밤). 그래서 화면은 "오늘" 이라 쓰지 않고 실제 고시일을 적는다.
  - **지급 예정 목록은 「총 지급 회차 · 첫 지급 예정일 · 계약 크레딧」에서 나오는 계산값이다**
    (2026-09-02 운영자 지시). 그래서 그 두 칸을 고치면 목록도 다시 계산된다 — 계약 수정 폼과
    상세 4번 「크레딧 지급」이 **같은 라우트**(`POST /won-customers/contracts/{id}`)로 가고,
    다시 까는 곳은 `_reseed_credit_grants` 한 곳이다. 손으로 추가·수정한 회차와 지급 완료
    표시는 그때 사라지므로 두 화면 다 누르기 전에 그렇게 적는다.
    - **「바뀌었나」는 화면이 판단하고, 서버는 표(`credit_reseed`)만 본다**
      (`_resync_credit_grants`). 서버가 폼 값과 행을 비교해 알아내게 두었다가 당했다: 폼은
      첫 지급일이 비어 있으면 계약 시작일을 대신 넣어 보내는데(빈 칸을 보여 줄 수는 없으니까)
      서버는 그것을 행의 NULL 과 비교해 「바뀌었다」로 읽었고, **워크북에서 날짜 없이 들어온
      계약은 비고 한 줄 고치는 저장마다 일정이 갈아엎혔다** — 화면 눈에는 아무것도 안 바뀌어서
      경고도 안 떴다(`_settle_amounts` 가 겪은 그 일이고 되돌릴 방법이 없다). 화면은 자기가
      불러온 값과 지금 칸의 값을 비교하므로 그런 어긋남이 없고, **경고를 띄우는 조건과 실제로
      일어나는 일이 같은 조건**이 된다. 상세 4번의 버튼은 그 표를 **언제나** 붙인다 — 「다시
      깔기」라고 적힌 버튼이 아무 일도 안 하면 안 되고, 계약 크레딧만 고친 뒤 회차 금액을
      다시 나누는 길도 그것뿐이다.
    - **회차 수는 1~120 이다.** `max(1, …)` 로 받아 주면 「12회차로 다시 깝니다」라고 적힌
      확인 창을 지나고도 1회차가 되고, 그 차이는 화면 어디에도 안 보인다.
    - **한 줄 삭제는 다시 깔기가 아니다.** 지운 회차의 크레딧은 다른 회차로 옮겨 가지 않고,
      남은 회차만 1부터 번호를 다시 단다 — 안 그러면 「1/2」와 「3/2」가 나란히 선다.
  - **워크북의 화살표는 한 방향뿐이다: 「고객 기본 정보」 → 나머지 전부.** 고객사 이름이
    적히는 곳은 그 탭 하나이고, 계약·회차 탭과 **Inbound DB** 가
    Client ID 로 거기를 조회한다 — 거기서 한 번 고치면 전부 따라 바뀐다. **그래서 고객 기본
    정보는 아무 데도 조회하지 않는다**: 조회하는 순간 반대 방향이 순환 참조가 되어 양쪽 다
    `#REF!` 가 된다. Inbound DB 쪽은 append 경로가 그 세 칸(고객사·기업 종류·국가)에 행마다
    조회 수식을 쓰고(`_write_registry_formulas`), 조회 대상이 없으면 값이 비므로 **문의 행을
    쓰기 전에 회사 행을 먼저 만든다**(`_ensure_registry_row`). 그 탭에는 ARRAYFORMULA 를 쓸
    수 없다 — 앱이 행을 append 하는데 배열이 채울 자리에 값이 들어오면 열 전체가 깨진다.
    Website URL 과 최초 연락일은 콘솔에 없는 칸이라 시트가 원본이고 동기화가 건드리지 않는다.
    사람 이름·연락처는 그 탭에 없다: 담당자는 바뀌고 회사는 안 바뀐다.
  - **파생 열은 열당 수식 한 칸(ARRAYFORMULA)이다.** 예전에는 같은 수식을 수백 줄 복사해
    깔았는데, 그러면 줄 수가 곧 입력 한계가 되고, 한 줄만 지우면 그 행만 조용히 계산을
    멈춘다. 대신 그 열에 값을 쓰면 배열 전체가 깨지므로 콘솔이 쓰는 열(`owned`)과 절대
    겹치지 않고, 경고용 보호를 걸어 둔다. 동기화가 시트를 읽을 때 **자연키 열만 한 열씩**
    읽는 이유도 이것이다 — 행으로 읽으면 배열이 채운 빈 문자열까지 딸려 와 마지막 행이
    시트 끝이 되고 새 행 자리를 못 찾는다.
  - **행은 자연키로 찾는다 — 표식 열은 없다.** Client ID(+계약 차수, +회차) 셋 다 사람이
    고치지 않는 값이라 수정이 제자리 덮어쓰기가 된다. 한동안 「동기화 키」 열을 뒀지만 하는
    일이 없어서 지웠다. 콘솔이 아는 Client ID 의 행 중 콘솔이 안 들고 온 것은 지워진 항목이라
    비우고, 모르는 Client ID 의 행은 손으로 쓴 것이라 두었다가 그 고객이 생기면 이어받는다.
    수식 칸(고객사·계약 개월수·월간 매출·잔여일수·고객 종류·담당부서·전체 회차)에는 쓰지 않는다:
    값으로 덮으면 시트가 스스로 계산하던 것이 그 행에서만 멈춘다. 글자는 RAW, 숫자·날짜는
    USER_ENTERED 로 나눠 보낸다 — 한 벌로 보내면 `+82 10-…` 이 수식이 되거나 날짜가 글자가
    된다. 부르는 곳은 `main.publish_changes_middleware` **한 곳**이다(쓰기 라우트가 열한 개다).
  - **화면은 운영자가 준 HTML 목업 그대로**다. `static/won.css` 는 그 목업의 스타일을 전부 `.won` 아래로 스코프해 옮긴 것이고, `:root` 변수도 `.won` 에 건다 — `:root` 에 두면 콘솔 전체 색이 바뀐다. 파일 끝의 되돌림 블록은 **목업이 값을 주지 않아 console.css 가 흘러 들어온 속성**만 다룬다.
- **MQL / PQL 은 구독 플랜이 정하고, 저장하지 않는다** (2026-09-02 운영자 지시).
  플랜 없음 · Free · N/A 는 MQL(아직 아무것도 안 샀다), 그 외 플랜은 PQL. 규칙은
  `sheet_values.qualification_for_plan` **한 곳**이고 「없음」의 철자는 `normalise_plan` 이
  이미 들고 있는 목록을 그대로 쓴다 — 목록이 둘이면 콘솔과 시트가 같은 고객을 다르게 부른다.
  화면 셋(리드 히스토리 · 티켓 상세의 연락처 정보 · 고객 상세)이 전부 그 값을 그린다.
  - **`customer_profiles.qualification` 열을 읽지 않는다.** 그것은 워크북에서 읽어 온
    **거울**이고(`sheet_sync`) 콘솔에서 채우는 길이 없어 운영 데이터에서 늘 비어 있었다 —
    그래서 고객 상세의 「MQL / PQL」은 언제나 「-」였고, 그게 이번 요청의 발단이다. 저장하기
    시작하면 플랜을 고친 뒤 이 값을 안 고친 행이 반드시 생기는데 그건 화면에 안 보인다
    (고객 종류를 번호대에서 되짚는 것과 같은 이유, 0065).
  - **워크북의 Pipeline 칸에는 값을 쓰지 않는다 — 수식을 되돌려 놓는다** (2026-09-02
    운영자 지시). 그 칸은 구독 플랜을 읽는 수식이고(`_pipeline_formula`), 콘솔이 하는 판단을
    시트도 스스로 한다. `update_inbound_stage` 에 있던 `pipeline` 인자는 **없앴다** — 안
    넘기는 것으로 두면 다음 호출자가 언젠가 또 그리로 간다.
    - **왜 문제였나.** 그 인자가 받던 것은 `customer_profiles.qualification` 인데, 그 열은
      워크북 전체 동기화가 **시트의 계산 결과를 글자로 베껴 온 사본**이다(`sheet_sync`).
      되돌려 쓰는 순간 그 행의 수식이 죽은 글자가 되고, 그 뒤로는 구독 플랜을 아무리 고쳐도
      그 행만 옛 값을 들고 있다 — 수식이 없다는 것은 시트를 봐도 안 보인다.
    - **단계를 옮기는 김에 그 칸의 수식을 다시 깐다.** 이미 덮인 행이 다음 단계 이동에서
      스스로 낫는다. 값을 안 쓰는 데서 그치면 시트는 그대로라, 고쳐 놓고도 고쳐진 것이
      화면에 없다. `valueInputOption` 이 달라(단계는 RAW, 수식은 USER_ENTERED) 요청은 둘이다.
    - **「아직 아무것도 안 샀다」의 철자는 `sheet_values.PLAN_AS_NOT_APPLICABLE` 한 곳에서
      온다.** 콘솔의 `qualification_for_plan` 도, 시트 수식도 그 목록으로 만든다. 수식에
      **빈칸 가지가 생긴 이유**: 「이 앱이 플랜 칸을 늘 채우므로 빈칸은 없다」가 사실이
      아니었다 — `record_inbound` 만 `normalise_plan` 을 지나고, 허브스팟 연락처 동기화와
      콘솔의 플랜 폼은 값을 그대로 써서 `Free` 가 `Free` 로 들어간다(그러면 PQL 로 읽혔다).
    - 시트 수식에는 `엔터프라이즈 → 재계약` 가지가 남아 있다. 콘솔은 그 말을 안 쓴다 —
      운영자가 준 구분이 둘뿐이고, 재계약인지는 이 콘솔에서 **계약 수**가 안다(수주 고객).
  - 시트 수식에는 `엔터프라이즈 → 재계약` 가지가 하나 더 있다. 콘솔은 그 말을 안 쓴다 —
    운영자가 준 구분이 둘뿐이고, 재계약인지는 이 콘솔에서 **계약 수**가 안다(수주 고객).
- **연락처가 안 붙은 티켓도 파이프라인의 한 건이다.** 백필은 오래 「붙일 사람이 없으니
  지어내지 않는다」며 그런 티켓을 건너뛰었다. 대가는 **화면 건수가 허브스팟보다 적은 것**
  이었고, 운영자가 세어 보고 알아챘다(2026-08-18 실측: Lost 3건 · Not a Fit 1건이 전부 이
  경우였다). 숫자가 안 맞으면 그 화면의 어떤 숫자도 못 믿는다. 이제 `_placeholder_contact`
  가 자리 표시 연락처를 만든다 — 키는 **티켓** 번호다(연락처 번호가 아니라: 그러면 연락처
  없는 티켓 둘이 한 사람으로 합쳐진다). 메일이 갈 길은 없다(이메일 None, 메시지·초안 없음).
- **접수는 New 만 보지만, 유입은 그러면 안 된다.** `poll_tickets_once` 와 웹훅은 New 에
  도착한 티켓만 접수 처리한다(그래야 초안이 엉뚱한 단계에 안 생긴다). 그런데 영업이 다른
  파이프라인에서 끌어오거나 처음부터 Negotiating·Lost·Not a Fit 으로 만든 티켓은 그래서
  **행 자체가 안 생겼다** — 단계 동기화는 고칠 대상이 없어 조용히 지나갔고, 화면 건수가
  허브스팟보다 적었다(2026-08-18 운영자 보고: negotiating −1, lost −3, not a fit −1).
  이제 10분 스윕이 모르는 티켓을 `hubspot_backfill.adopt_ticket` 으로 주워 온다. **일감이
  아니라 보이기 위한 행이다**: 메시지도 초안도 접수 큐도 만들지 않고 `last_incoming_at` 을
  NULL 로 둔다(차면 워크북 append 대기에 올라간다) — 백필과 같은 규칙이고, 그래서 Lost
  티켓을 주워 와도 고객에게 메일이 갈 길이 없다.
  그리고 스윕 검색을 **우리 파이프라인으로 좁혔다**. 전에는 포털 전체를 훑어도 무해했지만
  (다른 파이프라인의 단계 id 는 매핑에 없어 버려졌다) 주워 오기 시작하면 무해하지 않다 —
  CS·지원 파이프라인 수백 건이 이 콘솔로 들어온다.
  스윕의 검색 창은 「마지막 스윕 이후 변경분」이라 **오래전에 만들어져 그 뒤로 안 건드려진
  티켓은 안 걸린다.** 그건 `POST /pipeline/backfill` 이 파이프라인 전체를 훑어 채운다.
  **콘솔에 버튼은 없다.** 2026-08-18 에 하루 있었다가 운영자 지시로 지웠다 — 321건을 훑는
  데 몇 분이 걸려서 「누른 직후」에는 아무 일도 안 일어난 것처럼 보이고, 기다리면 알아서
  맞는다. 되살릴 거면 **진행 중인지 끝났는지가 화면에 보여야 한다**(`hubspot_backfill_status()`
  가 그 값을 이미 기록하고 있다). 그게 없으면 「받아온 게 없는 것 같다」와 구별되지 않는다.
- **단계 값이 사는 열은 둘이고, 둘 다 맞춰야 한다.** `Conversation.stage`(문의별)와
  `CustomerProfile.pipeline_stage`(연락처별)다. 화면이 자리마다 다른 쪽을 읽는다 — 보드는
  앞엣것, 리드 히스토리·고객 상세는 뒤엣것. 한동안 `sync_stage_from_hubspot` 이 `conv.stage`
  하나만 보고 「바뀐 것 없음」이라 되돌아갔는데, 그러면 둘이 **한 번** 어긋난 뒤로는 영영
  안 맞는다: 이후 모든 스윕이 같은 자리에서 되돌아가 프로필을 못 고친다. 그리고 실제로
  어긋났다 — 발송 워커는 `conv.stage` 만, 고객 상세 폼은 프로필만 썼다. 그래서 허브스팟에서
  단계를 옮겨도 화면이 안 바뀌는 일이 생겼다. 지금은 ① 어긋남을 만들던 두 쓰기를 고쳤고
  (고객 상세 폼이 대화도 옮기고, 발송 워커는 **앞으로만** 간다 — 협상·수주 건을 Qualified 로
  되돌리지 않는다) ② 허브스팟 동기화가 둘 다 확인해 어느 쪽이 뒤처져 있어도 고친다.
  프로필은 그 연락처의 **최신 문의**일 때만 쓴다 — 연락처당 한 행이라 옛 티켓이 움직일
  때마다 화면 값이 그 옛 티켓으로 끌려갔다.
- **단계 이동은 조용히 실패하지 않는다.** 설정에 없는 stage id 로 옮겨진 티켓은 그 단계만
  안 따라오는데 화면에서는 「아직 안 바뀌었다」와 똑같이 보였고, 로그도 진행 기록도 없었다.
  이제 `sync_stage_from_hubspot` 이 경고를 남긴다 — 다만 **id 를 적지 않는다**: `/logs` 의
  스크러버가 9자리 이상 숫자를 전화번호로 지우는데(`common/log_buffer.py`) HubSpot stage id
  가 딱 거기 걸린다. 대신 바로 행동이 되는 사실을 적는다 — **id 가 설정되지 않은 로컬 단계
  목록**. 여덟 개가 다 있어야 하고, 하나라도 비면 그 단계로의 이동이 전부 사라진다.
- **워크북은 허브스팟이 아는 문의의 단계를 덮어쓰지 않는다.** 티켓이 있으면 허브스팟이
  기준이고 시트는 그 거울이다. 예전에는 시간 비교 없이 마지막에 쓴 쪽이 이겨서, 허브스팟에서
  옮긴 단계가 다음 전체 동기화 때 시트의 옛 값으로 되돌아갔다. 티켓이 없는 행(워크북에서만
  사는 문의)은 여전히 시트가 원본이다. 새 행을 붙일 때도 단계를 `New` 로 박지 않는다 —
  몇 주째 협상 중인 문의가 New 로 들어가고 그 값이 되돌아왔다. 시트 표기는
  `google_sheets._STAGE_VALUES` **한 곳**이고, 거기 말이 없는 단계(`reminder_sent` ·
  `no_response` · `closed`)는 갱신을 건너뛰며 경고를 남긴다 — 그 열은 영업팀이 필터로 쓰는
  값 목록이라 없는 말을 지어 넣으면 그 행이 어느 필터에도 안 걸린다. **넣을 말은 영업팀이
  정할 일이다.**
- **폴러는 한 회차에 여러 일을 하고, 단계 맞추기는 두 번째다.** 예전에는 `try` 하나가 일곱
  단계를 감싸서 앞 단계가 터지면 뒤 단계가 그 회차를 통째로 굶었다. 지금은 단계마다 따로
  잡는다. 그리고 페이지가 꽉 차면 워터마크를 `now` 로 밀지 않는다 — 정렬이 오름차순이라
  **안 읽은 쪽이 더 최신**이고, 밀어 버리면 그 티켓들은 다음 창 밖으로 나가 영영 안 돌아온다.
- **초안은 New 티켓에만 있다 — 단계가 넘어가면 종료된다.** 미팅 링크가 나갔거나 협상·수주·종료로 옮겨졌다는 것은 답이 이미 다른 경로로 나갔다는 뜻이고, 그 초안을 발송 대기에 두면 운영자에게 고객이 이미 받은 답을 한 번 더 보내라고 청하는 셈이다. 종료하는 곳은 `stage_sync._retire_superseded_drafts` **한 곳**이고, 단계를 옮기는 쪽(HubSpot 동기화 · 콘솔 보드 · 고객 상세 폼 · 워크북 · 백필)과 초안을 완성하는 쪽(`inbound._finalize_draft`)이 전부 여기를 지난다. 화면·집계·발송이 모두 `Message.status` 하나만 보므로, 여기서 한 번 `superseded` 로 닫으면 목록에서 빠지고 검토 화면이 읽기 전용이 되고 `approve()` 가 거부하는 것까지 따라온다 — 라우트마다 단계를 확인하지 않는 이유다.
  - **`!= "new"` 가 아니라 매핑된 단계인지로 가른다**(`_PAST_NEW`). 모델 기본값 `initial` 이나 뜻을 모르는 값은 단계가 움직인 것이 아니라서, `!= "new"` 로 세면 아직 아무도 손대지 않은 티켓의 초안까지 지운다.
  - 과거 데이터의 `prompt_variant='auto_ack'`는 호환을 위해 일반 회신 집계와 발송 큐에서 제외한다. 새 자동 접수확인은 생성되지 않는다.
  - **단계가 안 바뀌어도 훑는 이유**: 초안 작성은 몇 분이 걸린다. 그 사이에 단계가 옮겨지면 그 대화에는 다시 아무 이벤트도 오지 않는다(10분 폴러의 stage reconcile 은 HubSpot 에서 **최근에 바뀐** 티켓만 훑는다). `tests/test_stage_sync.py` · `tests/test_inbound_flow.py` 가 고정한다.
- **자동 회신은 없다.** 첫 문의는 검토용 초안만 만들며, 고객에게 바로 나가는 메일은 없다. 그 대화에 이미 사람이 승인해 보낸 회신이 있으면 이후 고객 메시지는 기록만 되고 새 초안을 자동 생성하지 않는다.
- **초안은 나갈 언어로 쓴다. 한국어는 옆에 저장해 둔다** (2026-08-27 운영자 지시).
  예전에는 초안이 늘 한국어였고 승인 때 flash 가 고객 언어로 되돌렸다. 그러면 **정책 문서에
  운영자가 영어로 써 둔 완성된 메일이 고객에게 그대로 갈 길이 없다** — 모델이 그 문장을
  한국어로 다시 쓰고, 번역기가 그 한국어를 영어로 되돌린다. 실제로 `Hi [Name], Thanks for
  reaching out to Perso Dubbing…` 이 `Hello, Ivan. Thank you for your inquiry about Perso
  Dubbing.` 으로, `Looking forward to helping you get started! Cheers, Untae Bae` 가
  `Thank you.` 로 나갔다 (2026-08-26, msg 64 — 로그의 `Doc router selected 1/9` 가 고른 것은
  견적 문서였고, 그 문서의 **내용**은 살아남고 **문장**은 못 살아남았다). 지금은
  `reply.ensure_language` 가 초안을 문의 언어에 두고, `reply.korean_reading` 이 한국어 대역을
  **한 번** 만들어 `messages.body_ko` 에 넣는다 — 화면을 열 때마다 모델을 부르지 않는다
  (본문이 안 바뀌면 번역도 안 바뀐다, 0045 가 고객 문의에 같은 이유로 만든 칸이다).
  - **언어 라벨은 결과를 보고 붙인다.** 번역이 실패하면 본문은 한국어로 남는데 라벨만 `en`
    으로 찍으면 발송 관문이 통과시켜 한국어 메일이 영어 고객에게 간다. `_draft_reply` 가
    `is_mostly_korean(draft.body)` 로 다시 재고, `enforce_send_language` 가 한 번 더 막는다.
  - **대역은 맨 마지막에 만든다.** 링크 치환·정규화와 금액 가드가 끝난 뒤라야 두 벌이 같은
    문장, 같은 링크를 들고 대조가 된다.
  - **`번역하기` 버튼은 남는다 — 다만 대개 안 보인다.** 모델이 지시를 어겼거나 운영자가 본문을
    한국어로 고쳐 놓았을 때만 뜬다(`approval.translation_required`).
- **답변의 형식·톤 규칙은 콘솔에 한 벌만 둔다.** `policy_sources(mode='rules')` 의 「공통 원칙 및 가드레일」이 그 한 벌이고, `draft_reply.md` 는 그것을 따르라고 가리키기만 한다. 양쪽에 적으면 운영자가 콘솔에서 고친 쪽과 배포해야 바뀌는 파일이 조용히 어긋난다. `tests/test_reply_style.py::test_the_layout_rules_live_in_exactly_one_place` 가 고정한다.
  - **가격은 문서와 코드가 같은 말을 해야 한다.** 문서의 가드레일이 "구체적 가격 숫자를 쓰지 않는다" 이므로 `_PRICING_RULE_NORMAL` 도 그렇게 말한다. 예전에는 정반대였고(코드는 "금액을 명시하라"), 그때 이기는 쪽은 코드였다. `enforce_first_reply_no_price` 는 첫 회신에만 도는 하드 가드로 남는다 — 모든 회신에 걸면 운영자가 일부러 적은 금액을 조용히 지운다.
  - **어떤 문서를 쓸지는 모델이 고른다.** 매핑을 코드에 박으면 문서 이름이 바뀌거나 지워질 때마다 흔적 없이 끊긴다. 모델이 보는 것은 본문이 아니라 인덱스 한 줄(`slug·title·categories·tags·summary`)이고, `summary` 는 정책 문서의 **「언제 쓰는가」 칸**(0064)이다 — 비면 본문 앞 400자. 사본의 `categories` 는 `["all"]` 이어야 한다: 라우터가 실패해 유형 매칭으로 떨어질 때 후보가 0개가 되면 **문서 없이** 답을 쓴다.
- Every outbound reply requires human approval. `_finalize_draft` always writes `pending_approval`, and migration 0087 retires any legacy queued acknowledgement.
- **The inquiry category is stored and shown; which document answers it is NOT.** `Conversation.inquiry_category` (0049) is what the 회신 및 검토 list shows where 채널 used to be — channel was `email` on every row. `support` / `spam` / `recruiting` render as **UnQualified**, which means "not a sales lead", not "do not reply": those still get an answer, from the CS guide or the intro document. It also replaced the 검토 필요 flag (0047, dropped in 0049) — "CS 문의" says which one to open first far better than "확인이 필요합니다" did. `Conversation.inquiry_subject` (renamed from `topic` in 0041) still holds the customer's own subject line.
  - **The category→document mapping is deliberately not in code.** The model reads the document index (title · summary · tags) and picks; the category and the inquiry language are hints in the prompt, not a lookup table. Policy changes and Notion titles change — a mapping frozen in Python breaks on both, with nothing on screen to show it broke. `spam` no longer short-circuits to "no documents" for the same reason.
- HubSpot Conversations performs real delivery on an existing ticket thread. A ticket with no usable thread fails closed for manual handling.
- Slack approval notifications are emitted only after a detailed draft is ready.
- `Message.direction` uses `inbound` for received messages and `outgoing` for our replies.
- Personal email domains are never grouped as one company.
- Existing conversation progress rows are append-only.

- **정책 문서의 표는 하나다 — 라우터가 `policy_sources` 를 직접 읽는다** (2026-08-27, 이관 0098).
  한동안 사본 표(`knowledge_documents`)가 있었고 초안은 그것을 읽었다. **그 표의 칸은 하나도
  자기 것이 아니었다**: slug 은 `doc_key` 에서, 요약은 `usage_note` 에서, 메일 제목은
  `subject` 를 `tags` 에 `"subject:…"` 로 실어서, `scope`·`categories`·`author` 는 행마다
  똑같은 상수. 파생물이라 **어긋날 수 있었고 어긋났다** — 상태를 따로 재워야 했고
  (`_set_knowledge_status`), 저장 직후 따로 밀어야 했고(`refresh_knowledge_copy`), 재우다 만
  행 하나가 콘솔에 안 보이는 채로 초안에 인용될 뻔했다(0097 의 `perso_refund_policy`).
  - **모델이 읽는 본문은 한 글자도 안 바뀌었다.** 운영 데이터로 전/후를 대조했다: 문서 8편,
    본문 9534자 바이트 단위 동일. 라우터 인덱스만 2083 → 1633자로 줄었고, 빠진 것은 행마다
    똑같던 `categories: all` 과 `tags: notion` 두 줄이다.
  - **라우터에게 보이는 이름은 `doc_key` 다.** 제목이 아니다 — 제목을 바꿔도 같은 문서여야
    하고, 이름으로 만들면 바꾼 순간 옛 것이 남아 한 정책을 두 번 인용한다.
  - `policy_sync` 는 `knowledge_slug` 하나만 남았다. 만드는 곳은 없고, 0097 이 「어느 행이
    사본인가」를 그 규칙으로 갈랐기 때문에 읽을 수 있게 남긴다.
- **정책·지식 문서의 원본은 이 콘솔이다. 노션에서 받아오는 코드는 하나도 없다.**
  `이메일 템플릿 → 정책 문서 → 직접 추가`(제목+본문), 어떤 문서든 `수정` 가능. 다섯 가지를
  시도했고 전부 막혔다 — 통합 토큰 발급 불가, 쿠키는 `file.notion.com`에서 403, 로컬
  스크립트는 사내망이 5432/6543을 막아 DB에 못 닿고, URL만 등록하면 본문이 영원히 비고,
  **Export zip은 부모 페이지 하나만 실어 온다**(정책 페이지가 가리키는 문서 여덟 개는
  자식이 아니라 링크라서 `Include subpages`로도 안 따라온다 — 2026-08-05 실측).
  그래서 `notion.py` · `notion_export.py` · `notion_session.py` · `policy_push.py` ·
  `api/policy_api.py` · `scripts/sync_notion_local.py` · `sync_policy_sources` · `NOTION_*`
  설정을 전부 지웠고, `policy_sources`에서 `notion_url`/`last_synced_at`/`last_error`도
  뺐다(0050). 답할 수 없는 질문을 스키마에 남기면 다음 사람이 답을 찾으러 간다.
  **되살리기 전에 `docs/정책문서-동기화-설계.md` §5를 돌려라** — 조건이 바뀌었으면 되돌리는
  게 맞고, 안 바뀌었으면 그 문서에 왜 안 되는지가 실측과 함께 적혀 있다.
  - 본문 없는 등록은 만들 수 없다 — 문서를 만드는 화면이 본문을 같이 받는다. URL만 등록해
    두던 폼이 `body`가 영원히 빈 행을 만들었고, zip도 결과적으로 같은 일을 했다.
  - 콘솔 편집은 `refresh_knowledge_copy`로 라우터가 읽는 사본까지 즉시 간다. 안 그러면
    화면엔 새 내용, 회신은 옛 내용이 되고 눈치챌 방법이 없다.

## Stack

- Python 3.11+, FastAPI, SQLAlchemy, React (Vite + TypeScript + React Query)
- Gemini on Vertex AI (`flash` for routing/classification, `pro` for customer replies)
- SQLite locally; PostgreSQL-compatible migrations
- HubSpot Conversations delivery and CRM synchronization, optional Slack

## Data flow

`HubSpot webhook / 10-minute poll → Gemini + policy docs → review queue + Slack → operator approval/translation → HubSpot Conversations reply → ticket stage`

Customer operations reuse the same Contact and Conversation records. `CustomerProfile`, `CustomerInteraction`, and `ContractRecord` add manual pipeline fields, cross-channel history, contracts, payments, and renewal insights without duplicating the inbound pipeline.

## Development

```powershell
.\.venv\Scripts\python.exe -m src.db.migrate
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

Do not send live messages in tests. `tests/conftest.py` disables background integrations regardless of the developer `.env`.

## Front end

The console is React (`frontend/`, Vite + TypeScript + React Query), served by FastAPI at
`/app`; every old page URL 302s there. There are no HTML templates left and no template
engine — sign-in was the last holdout, and it serves the same SPA document (`/auth/*` is
exempt from the auth gate, so React can draw it before a session exists).

- **Build before packaging.** `npm --prefix frontend ci && npm --prefix frontend run build`
  writes `src/api/static/app/`, which is gitignored and shipped via
  `[tool.setuptools.package-data]`. Skip it and `/app` answers 503. The Dockerfile's node
  stage does this itself, so `docker build` needs no prior step.
- **견적 계산기·견적서·계약서, 그리고 전체 대시보드·리드 추이는 지웠습니다** (2026-08-13,
  운영자 지시 "앞으로 안 씀"). 화면·라우트·리다이렉트·`src/lib/quote.ts`·`quote_tiers.py`,
  그리고 그 계산을 지키던 1,512행짜리 golden 코퍼스(`frontend/test/quote.golden.json`)까지
  전부입니다. 되살릴 일이 생기면 **git 이력에서 그 파일들을 그대로 꺼내라** — 특히 golden
  은 React 포팅 전 계산기가 실제로 뽑은 값을 캡처한 것이라 다시 만들 수 없습니다.
- **사이드바의 `협상중 고객` 은 지웠습니다** (2026-08-18, 운영자 지시 "완전히 삭제").
  `/customers?stage=negotiation` 이라 리드 히스토리와 **같은 화면**이었고, 쿼리 하나로 갈라진
  이름 둘이었습니다. 단계로 좁혀 보는 일은 그 화면의 `Stage` 열이 그대로 합니다. 같이 나간 것:
  `flame` 아이콘(그 항목만 쓰던 것), `Customers.tsx` 의 `isFixedStage` 분기, `Shell.tsx` 활성
  판정의 `location.search` 가지. **뒤의 둘은 남겨 두면 버그입니다** — 항목이 없으면
  `?stage=negotiation` 에 닿는 유일한 길이 Stage 드롭다운인데, 거기서 Negotiating 을 고르는
  순간 사이드바가 강조를 잃고(`location.search` 가지) 드롭다운 자신이 정적 라벨로 바뀌어
  「전체」로 돌아갈 길이 사라집니다(`isFixedStage`). 백엔드의 `negotiation` 단계는 그대로입니다 —
  지운 것은 메뉴이지 파이프라인이 아닙니다.
- **티켓 세부 내역 오른쪽의 「플랜 정보」는 허브스팟 연락처를 그때그때 읽어 그립니다**
  (`src/integrations/hubspot_record.py`, `GET /api/ui/contacts/{id}/hubspot-record`).
  **Company 가 아니라 Contact 입니다.** 처음에 Company 로 만들었다가 옮겼습니다 — 운영자가
  준 매핑 표의 왼쪽 칸이 `Company` 였는데, 실제 포털 화면에서는 `Plan` · `IP Country` ·
  `user seq` · `space seq` · `plan tier` · `plan seq` 가 전부 **연락처 레코드의 「기본
  그룹」**에 있었습니다(같은 그룹의 `Contact owner` 가 결정적입니다 — Company 에 없는
  속성입니다). 덕분에 연결(association) 조회도, 「주 회사가 어느 쪽이냐」도 없습니다.
  네 가지가 일부러 그렇게 되어 있습니다. ① **속성 이름을 코드에 박지 않는다** — 운영자가
  아는 것은 라벨(`user seq`)이고 내부 이름(`user_seq_c`)은 포털마다 다릅니다. 카탈로그를
  읽어 **라벨 먼저, 내부 이름 나중**으로 찾습니다(순서가 중요합니다: 은퇴한 `user_seq` 와
  현역 `user_seq_c` 가 같이 있을 때 이름을 먼저 보면 빈 옛 속성이 이깁니다). ② **빈 값도
  줄을 만든다** — 허브스팟 사이드바가 그 자리에 `--` 를 그립니다. 우리가 숨기면 같은
  레코드인데 줄 수가 다른 화면이 되고, 이 포털은 플랜 필드가 대부분 비어 있어 카드가 통째로
  사라집니다. ③ **못 찾은 필드는 못 찾았다고 적는다** — ②와 다른 이야기입니다. 값이 빈 것은
  그 고객 이야기이고 속성을 못 찾은 것은 설정 이야기인데, 라벨이 한국어면 정규화로 못 잡고
  조용히 빼면 운영자가 진짜 라벨을 알려줄 기회가 없습니다. ④ **본문 payload 와 따로
  받는다** — 같이 받으면 답을 읽는 일이 허브스팟 응답을 기다립니다.
  회사 이름은 일부러 안 가져옵니다: 연락처 정보 카드에 이미 **고칠 수 있는** 회사 칸이 있고,
  옆에 읽기 전용 사본을 세우면 둘 중 어느 것이 진짜인지 화면만 봐서는 알 수 없습니다.
  - **플랜 다섯 칸은 콘솔에서 허브스팟으로 되씁니다** (2026-08-18, 운영자 지시 — 제품 쪽
    연동이 100% 가 아니라 사람이 채워야 할 때가 있습니다). `update_record_fields` 가
    `guard_external_write("hubspot:update_contact_record")` 를 **함수 첫 줄에서** 지납니다 —
    라우트가 아니라 여기인 이유는 다음 호출자(폴러·배치)도 그 앞을 지나야 하기 때문입니다.
    `tests/test_safe_mode.py::test_hubspot_record_write_blocked` 가 고정합니다.
    **쓰기의 울타리는 `RECORD_FIELDS.editable` 한 칸입니다.** 화면이 보내는 것은 우리
    `key`(`user_seq`)이고 허브스팟 속성 이름은 서버가 카탈로그에서 **다시** 찾습니다 —
    브라우저가 보낸 이름을 그대로 썼다면 콘솔에 닿은 누구든 `email` 이나 `lifecyclestage`
    를 덮어쓸 수 있었습니다. 「국가」는 허브스팟이 접속 IP 로 뽑는 값이라 안 열고, 빈 칸은
    「모르겠다」가 아니라 「지워라」입니다(잘못 들어간 값을 되돌릴 길이 있어야 합니다).
    저장 뒤에는 이 질의만 따로 무효화합니다 — 일괄 무효화에서는 일부러 빠져 있는데, 저쪽
    값이 **정말로** 바뀐 것은 이때뿐이기 때문입니다.
- **Styling is `static/console.css`**, linked rather than bundled — one copy of the design
  for the SPA and for the sign-in pages. There is no CSS framework.
- **Reads go through `/api/ui/*`**, which calls the SAME context builders the templates
  used, so a screen's data has one definition. **Writes go to the existing routes** — the
  send guard, stage sync and safe-mode block stay in one place.
- **`/api/ui/events` is SSE.** Writes publish a topic; every open console invalidates its
  cache. This is what makes a change visible in another tab or to another operator, and
  React state alone cannot do it. In-process fan-out: multi-worker needs Redis pub/sub.
