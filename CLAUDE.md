# Project Context

PERSO Inbound is a FastAPI workflow for inbound inquiry handling and customer operations.

## Invariants

- **External-write safety (대전제, top priority).** `LIVE_EXTERNAL_WRITES` defaults to `false` (SAFE). While safe: HubSpot writes, Google Sheets writes, and outbound email delivery are blocked. The application never substitutes an internal test recipient. Reads stay on. Enforced in `src/common/safe_mode.py`; guaranteed behavior is pinned by `tests/test_safe_mode.py`. Any new external-write/send path must use the same gate and add a safety test.
- **Human-approved email delivery is live.** `EMAIL_SENDING_ENABLED = True`; only an operator-approved draft is claimable. Immediate inbound acknowledgements were removed structurally. Foreign-language drafts cannot be approved or sent until the operator completes the explicit translation step. The current delivery transport is SMTP, followed by a best-effort HubSpot CRM timeline log; that CRM email activity API does not deliver the message.
- **The CRM/workbook are LIVE.** `LIVE_EXTERNAL_WRITES=true` with `LIVE_HUBSPOT_WRITES` / `LIVE_SHEETS_WRITES` both on, so a stage moved in the console moves the HubSpot ticket and updates the Inbound DB row. Every screen write goes through the same routes the Jinja forms used, which is why that stayed true through the React port.
- **Per-destination switches are subordinate.** `LIVE_HUBSPOT_WRITES` / `LIVE_SHEETS_WRITES` (both default `true`) select which destinations go live *after* the master is on; neither can permit a write while `LIVE_EXTERNAL_WRITES` is `false`. `guard_external_write("<channel>:<action>")` picks the gate from the label prefix, and an unregistered channel falls back to the master — so a new write path is blocked by default.
- HubSpot tickets are accepted only from `HUBSPOT_TICKET_STAGE_NEW` when a ticket ID exists.
- **파이프라인 단계는 키와 이름이 따로 움직인다.** HubSpot 에서 이름이 바뀌어도 stage id 는 그대로다 — Meeting link sent 는 **Qualified** 로, Closed 는 **Not a Fit** 으로 이름만 바뀌었다. 그래서 로컬 키(`meeting_link_sent` · `closed`)도 그대로 두고 `customer_ops.PIPELINE_STAGES` 의 표시 이름만 고친다: 키를 따라 바꾸면 `conversations.stage` 와 `customer_profiles.pipeline_stage` 두 열을 옮기는 마이그레이션이 필요하고, 다음에 이름이 또 바뀌면 그걸 또 한다. 이름의 출처는 그 튜플 **한 곳**이다(화면·칩·목록이 전부 거기를 읽는다). 새 이름을 `.env` 에 새 변수명으로 적어도 되게 옛 이름을 `AliasChoices` 에 남긴다 — 그리고 **새 별칭을 넣을 때마다 `tests/conftest.py` 의 blanking 목록에도 넣는다**: 한 철자만 비우면 개발자 `.env` 의 다른 철자가 pytest 로 실제 티켓을 옮길 수 있다(`tests/test_safe_mode.py` 가 잡는다).
  - **Deal Detail 은 Won 과 Lost 에만 있다** (`DEAL_DETAILS`). 왜 이겼나(PoC/Contract/Renewal)와 왜 졌나(여섯 가지)는 결말이 난 건에만 있는 정보라 다른 단계에는 채울 답이 없다. **열은 하나**(`conversations.deal_detail`)다 — 한 문의가 동시에 이기고 지지 않으므로 어느 목록의 값인지는 그때의 단계가 정하고, 단계가 바뀌면 값은 남되 화면에는 안 나온다(되돌아오면 다시 뜬다). 우리 DB 에만 쓴다: 그 파이프라인에 대응하는 HubSpot 속성이 있는지 확인되지 않았고, 없는 속성에 쓰면 요청마다 400 이다.
- **서명은 사람이 고르는 것이고, 코드는 본문에 넣지 않는다.** 예전에는 두 벌이었다 — 회사 규칙 안의 `{{__signature__}}` 가 `signature_ko` 행을 프롬프트에 끼워 모델이 **본문에** 서명을 쓰게 했고, 그래서 발송 경로에는 운영자가 다른 서명을 고르면 그 텍스트를 도로 찾아 떼어내는 기계(`strip_known_signature`, 번역된 메일에서는 메일 주소를 앵커로 잘랐다)까지 있었다. 지금은 한 벌이다: 운영자가 초안에서 고르고 `발송` 을 누르면 그때 본문 아래로 붙는다(`messages.signature_key` → `branded_signature_html` → `to_html_email`). 되살리기 전에 왜 두 벌이면 안 되는지부터 읽어라 — 0061 의 docstring 에 적혀 있다.
  - **서명은 데이터다.** 접두사 `signature_` **하나**가 목록의 서명 묶음과 검토 화면 고르개를 가른다. 둘이 같은 집합을 가리켜야 한다 — 예전에 `signature_html_`(고르개)과 `signature_`(목록)로 갈라져 있었고, 그래서 화면에는 서명으로 보이는데 고를 수 없는 행이 있었다. 추가·수정·삭제 전부 콘솔에서 되고, 코드가 이름으로 찾는 서명은 하나도 없다. 글로 써도 되고 HTML 로 써도 된다.
  - **이메일 템플릿은 이제 정책 문서처럼 자유롭게 추가·삭제된다** (2026-08-18, 운영자 결정). 키를 운영자가 직접 적고, 무엇이든 지워진다. 대가는 알고 쓴다: 발송 경로는 템플릿을 **이름으로** 찾으므로 아무 이름이나 만든 행은 읽는 코드가 없고, 지운 행은 조회만 남기고 사라진다(접수확인이 하드코딩 문장으로 떨어지거나 회신이 `{{MEETING_LINK}}` 로 끝난다 — 7일 뒤에는 콘솔로 다시 만들 수도 없다). 그래서 **막는 대신 보이게** 한다: `db/email_templates.is_code_resolved` 한 곳이 목록의 「발송 경로 사용」 표와 삭제 확인 창의 빨간 문장을 동시에 만든다. 이름 목록만이 아니라 **모양**도 센다 — `signature_*` 는 고르개가 훑고, `auto_ack_<두 글자>` 는 그 언어 문의의 접수확인이라 콘솔에서 만들면 실제로 읽힌다(`auto_ack_footer` 는 두 글자 규칙에 안 걸린다).
  - **목록은 한 줄에 한 행이다 — 언어별로 묶지 않는다.** `auto_ack_en` 을 `auto_ack` 아래로 접어 두었더니 11개 행이 6줄로 그려지고 카드 숫자만 11이었다. 그리고 접힌 다섯 줄이 하필 **영문 문의가 읽는 유일한 행들**이라, 운영자가 「전체」라고 적힌 국문 행을 고치면 영문 회신은 손대지 않은 `_en` 행을 계속 읽었다 — 화면에 그럴 이유가 하나도 없이. `reply_format`·`meeting_link`·`whatsapp_link` 의 언어도 `all` → `ko` 로 바로잡았다(0074): 「전체」인데 영문에 한 글자도 안 닿는 행이었다. 저장이 그 값을 되돌리지 못하게 `PUT /email-templates/{id}` 의 `language` 기본값도 없앴다 — 화면에 언어 칸이 없으므로 빈 값은 "전체로 바꿔라" 가 아니라 "이 폼은 언어를 모른다" 이다.
  - **서명에 언어는 없다** (0063). 어떤 코드도 언어로 서명을 고르지 않는다 — 고르는 것은 사람이다 — 그래서 묻지도, 보여주지도, 저장하지도 않는다. `email_templates.language` 열 자체는 남는다: `auto_ack` / `auto_ack_en` 은 정말로 한 메일의 두 언어다.
  - **접수확인 아래에는 서명이 아니라 로고가 붙는다** — `auto_ack_footer` 행 (0062). 붙는 통로는 서명과 같은 `signature_key` 지만 키가 접두사 **밖에** 있어서 검토 화면 고르개에는 안 나온다. 아직 아무도 읽지 않은 메일에 담당자 서명을 붙이면 그 사람이 쓴 메일로 읽히는데, 정작 답은 며칠 뒤 다른 사람이 쓸 수도 있다. 서명은 사람이 검토하고 누르는 **첫 답변**부터다.
- **수주 고객은 Client ID 로 묶인다.** `clients` 아래 `client_contracts`(차수별), 그 아래 크레딧 지급 회차·분납 회차. 소통 히스토리만 예외로 고객 단위라 새 테이블을 만들지 않고 `customer_interactions` 에 `contract_seq` 만 붙였다 — 협상 단계 대화가 계약보다 먼저 쌓이고 그대로 이어져야 한다(0065).
  - **고객을 `contacts` 로 대신할 수 없다.** Contact 는 이메일 신원이라 한 회사에 담당자가 셋이면 셋이고, Outbound·Interactive·AX 고객은 문의를 보낸 적이 없어 아예 없다. 인바운드 고객만 `clients.contact_id` 로 연결된다.
  - **Client ID 는 고객사 하나에 하나.** 전에는 문의 하나에 하나였다(`suggest_inbound_client_id` 가 대화마다 새 번호). 그러면 같은 회사가 두 번 문의할 때 계약·크레딧·소통 히스토리가 두 갈래로 갈라진다. `agents/client_ids.py` 가 ① 그 연락처의 번호 ② 같은 회사 도메인의 다른 담당자 번호(**개인 메일 도메인 제외** — gmail 둘을 한 회사로 묶으면 남의 계약이 보인다) ③ 새 발급 순으로 정한다.
  - **고객 종류는 저장하지 않는다.** 번호대가 곧 종류다(1000 Inbound / 2000 GTM Outbound / 3000 Interactive / 4000 AX / 9000 레거시). 둘 다 저장하면 서로 다른 행이 반드시 생긴다.
  - **Won 감지는 `stage_sync.sync_stage_from_hubspot` 한 곳.** 웹훅·10분 폴러·수동 최신화가 전부 이 함수를 지난다. 감지를 세 군데 두면 하나가 조용히 빠진다. Won 티켓은 `pending_won` 에 쌓이고 계약 정보는 사람이 채운다 — 금액도 기간도 없는 고객이 활성 고객 수와 예상 MRR 을 오염시키면 안 된다.
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
  - **월별 MRR 과 월 매출은 다른 것을 센다.** 카드가 옆으로 넓어진 자리에 최근 12개월을
    펼치고, 두 지표를 나란히 둔다: `mrr_months` 는 플랜 기간에 균등 배분한 **인식** 매출,
    `cash_months` 는 결제 회차가 잡힌 달에 통째로 얹는 **현금흐름**이다(일시불이면 한 달에
    전액, 할부면 회차마다). 둘이 갈릴 때가 그 계약을 봐야 할 때라 같이 보인다.
    차트는 직접 그린다(`won/MonthlyBars.tsx`) — 라이브러리를 써도 된다는 허락을
    받고도 안 쓴 이유는 `docs/암묵지/07` 에 있다. **색은 눈으로 고르지 않았다**: 양수
    `#2A9D8F`(--teal-500)와 음수 `#B42318`(--red-fg)은 색맹 분리도·명도대·채도·대비
    검사를 통과한 쌍이고, 음수는 색만으로 말하지 않는다(0선 아래로 자라고, 값이 직접
    적히고, 툴팁이 「중도 해지 정산」이라고 쓴다).
    **환산은 서버가 계약마다 그 계약의 환율로 한 번만 한다** — 두 통화를 다 채워 보내고
    화면은 고르기만 한다. 화면이 다시 환산하면 같은 숫자가 화면마다 달라지고, 오늘 고시가로
    과거를 환산하면 마감한 달의 숫자가 오늘 환율에 따라 움직인다. 계약에 환율이 없는 옛
    행만 오늘 고시가로 떨어진다. 「전체」 묶음도 서버가 만든다 — 화면이 부서별 값을 다시
    더하면 그 덧셈이 두 곳에 생긴다. 갱신 임박 고객은 **보드 줄**(크레딧 지급 예정 · 결제 예정)의
    비어 있던 세 번째 칸으로 옮겼다(운영자 지시). 셋 다 날짜가 다가와 손이 가야 하는
    목록이라 한자리에 모인다. **제목이 곧 필터인 것도 같이 옮겼다** — 예전 KPI 카드는
    누르면 목록이 갱신 임박만 남았고, 그 기능이 사라지면 옮긴 것이 아니라 지운 것이다.
  - **환율은 쓴 시점의 값을 행에 박는다.** 크레딧(`공급가 ÷ 분당 단가 × 60`)에 쓴 환율은 계약에, 입금액에 쓴 환율은 결제 회차에. 오늘 환율로 과거를 다시 환산하면 지난달 매출이 이번 달에 바뀐다. 예상 MRR 카드는 **오늘 고시가를 가져와** 쓴다(인증 불필요, ECB. 수출입은행 키가 있으면 그쪽이 우선). 손으로 적는 칸이었는데, 그러면 두 사람이 다른 환율로 다른 MRR 을 보고 그 값이 언제 것인지 아무도 모른다. `MRR_FX_RATE` 는 조회가 실패했을 때의 바닥값이다. **한국에서 낮에 보면 거의 항상 전일자 고시가 나온다 — 정상이다**: ECB 는 유럽 오후에 하루 한 번 낸다(KST 밤). 그래서 화면은 "오늘" 이라 쓰지 않고 실제 고시일을 적는다.
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
  - **접수확인은 초안이 아니다.** `prompt_variant='auto_ack'` 는 `approved` 로 발송 큐에 앉아 있어서, 걸러 내지 않으면 단계 한 번 옮기는 것이 아직 안 나간 고객 접수확인을 취소한다. 반대로 `approved` 인 **회신**은 종료 대상이다 — 발송 워커는 status 만 보고 집어 간다.
  - **단계가 안 바뀌어도 훑는 이유**: 초안 작성은 몇 분이 걸린다. 그 사이에 단계가 옮겨지면 그 대화에는 다시 아무 이벤트도 오지 않는다(10분 폴러의 stage reconcile 은 HubSpot 에서 **최근에 바뀐** 티켓만 훑는다). `tests/test_stage_sync.py` · `tests/test_inbound_flow.py` 가 고정한다.
- **자동 회신은 대화당 한 번뿐이다.** 첫 문의에 접수확인이 나가고 초안이 하나 만들어진다. 그 대화에 이미 회신(접수확인 제외)이 있으면, 이후 고객 메시지는 **기록만 되고 초안을 만들지 않는다** — 이후 회신은 사람이 직접 등록한다. `_persist_placeholder` 가 `reply_message_id=None` 을 돌려주고 `handle()` 이 `skipped_reply_exists` 로 끝낸다. 조건이 "두 번째 inbound" 가 아니라 "이미 회신이 있다" 인 이유: 티켓 하나에 이벤트가 여러 번 온다(웹훅 + 10분 폴러 + 티켓 변경). `tests/test_inbound_flow.py` 가 고정한다.
- **답변의 형식·톤 규칙은 콘솔에 한 벌만 둔다.** `policy_sources(mode='rules')` 의 「공통 원칙 및 가드레일」이 그 한 벌이고, `draft_reply.md` 는 그것을 따르라고 가리키기만 한다. 양쪽에 적으면 운영자가 콘솔에서 고친 쪽과 배포해야 바뀌는 파일이 조용히 어긋난다. `tests/test_reply_style.py::test_the_layout_rules_live_in_exactly_one_place` 가 고정한다.
  - **가격은 문서와 코드가 같은 말을 해야 한다.** 문서의 가드레일이 "구체적 가격 숫자를 쓰지 않는다" 이므로 `_PRICING_RULE_NORMAL` 도 그렇게 말한다. 예전에는 정반대였고(코드는 "금액을 명시하라"), 그때 이기는 쪽은 코드였다. `enforce_first_reply_no_price` 는 첫 회신에만 도는 하드 가드로 남는다 — 모든 회신에 걸면 운영자가 일부러 적은 금액을 조용히 지운다.
  - **어떤 문서를 쓸지는 모델이 고른다.** 매핑을 코드에 박으면 문서 이름이 바뀌거나 지워질 때마다 흔적 없이 끊긴다. 모델이 보는 것은 본문이 아니라 인덱스 한 줄(`slug·title·categories·tags·summary`)이고, `summary` 는 정책 문서의 **「언제 쓰는가」 칸**(0064)이다 — 비면 본문 앞 400자. 사본의 `categories` 는 `["all"]` 이어야 한다: 라우터가 실패해 유형 매칭으로 떨어질 때 후보가 0개가 되면 **문서 없이** 답을 쓴다.
- The first receipt acknowledgement may send automatically; the detailed reply always requires human approval. This is now structural, not configured: the score-vs-`AUTO_SEND_THRESHOLD` branch that could set `approved` on its own is gone, along with the setting, so `_finalize_draft` always writes `pending_approval`. Pinned by `tests/test_safe_mode.py` and `tests/test_inbound_auto_ack.py`.
- **The inquiry category is stored and shown; which document answers it is NOT.** `Conversation.inquiry_category` (0049) is what the 회신 및 검토 list shows where 채널 used to be — channel was `email` on every row. `support` / `spam` / `recruiting` render as **UnQualified**, which means "not a sales lead", not "do not reply": those still get an answer, from the CS guide or the intro document. It also replaced the 검토 필요 flag (0047, dropped in 0049) — "CS 문의" says which one to open first far better than "확인이 필요합니다" did. `Conversation.inquiry_subject` (renamed from `topic` in 0041) still holds the customer's own subject line.
  - **The category→document mapping is deliberately not in code.** The model reads the document index (title · summary · tags) and picks; the category and the inquiry language are hints in the prompt, not a lookup table. Policy changes and Notion titles change — a mapping frozen in Python breaks on both, with nothing on screen to show it broke. `spam` no longer short-circuits to "no documents" for the same reason.
- SMTP performs real delivery. HubSpot's CRM email object is used only to log a successful delivery.
- Slack approval notifications are emitted only after a detailed draft is ready.
- `Message.direction` uses `inbound` for received messages and `outgoing` for our replies.
- Personal email domains are never grouped as one company.
- Existing conversation progress rows are append-only.

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
- SMTP delivery, HubSpot CRM synchronization, optional Slack

## Data flow

`HubSpot webhook / 10-minute poll → immediate acknowledgement → Gemini + policy docs → review queue + Slack → SMTP → HubSpot timeline + ticket stage`

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
