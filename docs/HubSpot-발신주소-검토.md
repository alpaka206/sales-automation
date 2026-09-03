# 인박스와 발신 주소 — 담당자 확인 요청

작성 2026-09-02 · **갱신 2026-09-03(실발송으로 결론이 바뀌었습니다)**
근거: HubSpot API 직접 조회 · 포털 화면 확인 · HubSpot 공식 문서 · 저장소 코드 · **실발송 1건**

---

## 결론 먼저 — 부탁드릴 것이 없어졌습니다

이 문서는 원래 **「`perso.ai@estsoft.com` 을 Inbox 인박스로 옮겨 주세요」**를 부탁드리려고
쓴 것입니다. **그 요청은 취소합니다.** 옮기지 않아도 됩니다.

**왜 바뀌었나.** 「회신은 그 대화가 속한 인박스의 주소로만 나간다」가 저희 전제였습니다.
근거는 HubSpot 답장창의 보내는사람 드롭다운이 그 대화의 인박스 계정만 보여 준다는
**화면 관찰**이었는데 — **화면이 안 보여 주는 것과 API 가 거절하는 것은 다른 이야기였습니다.**
2026-09-03 에 폼으로 들어온(=`Inbox` 인박스) 티켓에 `perso.ai@estsoft.com`(GTM Marketing)
으로 실제로 보냈고 **그대로 나갔습니다.**

콘솔은 그 제한을 걷어냈고, 이제 **모든 B2B 티켓의 회신이 `perso.ai@estsoft.com` 에서
나갑니다**(운영 티켓 42건 실측 42/42). 예전 규칙에서는 폼으로만 들어온 티켓 93건이
**원천적으로 불가능**했습니다.

아래 1~3장의 인박스 구조와 측정치는 **왜 그런 전제를 갖게 됐는지**의 기록으로 남깁니다.
2장의 「주소 이관 후」 열은 이제 의미가 없습니다.

---

## 그래도 알아 두실 것 — `untae@estsoft.com` 자동 발송은 **불가능**합니다

권한이나 설정 문제가 아니라 **HubSpot 에 그 API 가 없습니다.**
가이드 4단계(담당자 메일로 전환)는 지금처럼 사람이 HubSpot 화면에서 하는 것이 맞습니다.
자세한 근거는 4장에 있습니다.

**참고로 새 메일도 API 로는 못 씁니다.** Conversations API 에는 대화를 새로 여는
엔드포인트가 없습니다(POST 는 `/threads/{id}/messages` 와 `/threads/{id}/assignee` 둘뿐).
콘솔이 할 수 있는 것은 **이미 있는 대화에 답장하는 것**뿐입니다.

---

## 0. 포털 화면에서 직접 확인한 것 (2026-09-03)

아래 문서의 근거는 API 조회만이 아닙니다. **HubSpot 화면을 하나씩 눌러** 확인했고,
두 결과가 값까지 일치합니다.

**① 대화 답장창의 보내는사람 목록 = 그 대화가 속한 인박스의 이메일 채널뿐**

`[Perso Dubbing] B2B 1:1 문의 form` 으로 들어온 티켓의 답장창에는 3개가 뜨고,
`perso.ai@estsoft.com` 도 `untae@estsoft.com` 도 **없습니다.**
반대로 GTM Marketing 인박스의 대화에서는 `perso.ai@estsoft.com` **하나만** 뜹니다.
→ 화면이 콘솔과 **같은 규칙**을 씁니다.

**② 티켓 상단 [Email] 버튼은 다른 기능입니다**

거기서는 5개가 뜨고 `perso.ai@estsoft.com`(기본값)과 `untae@estsoft.com` 이 있습니다.
하지만 화면이 배너로 명시합니다 — *"You're currently composing a **new email**.
To reply to an existing thread, find the email in the timeline and click 'reply' there instead."*
**스레드 회신이 아니라 새 메일**이고, 이 경로에는 발송 API 가 없습니다(4장).

**③ 같은 주소를 두 인박스에 붙일 수 없습니다** (직접 시도해 받은 문구)

> This email address is already connected to another inbox.

**④ 폼 채널을 GTM Marketing 으로 옮길 수 없습니다**

폼 채널의 이동 선택지는 **Help Desk 하나뿐**입니다. GTM Marketing · Interactive 는 아예 없습니다.
그래서 「폼을 옮긴다」가 아니라 「주소를 옮긴다」가 요청 1입니다.

**⑤ 다만 「대화」 단위 이동은 됩니다**

대화 화면 ⋯ → Move conversation → Help Desk / Interactive / GTM Marketing.
안내: *"Any associated tickets will remain associated to this conversation and in their
current pipelines."* — 티켓은 그대로 두고 대화만 옮깁니다. **한 건씩 손으로** 하는 일이고
API 에는 없습니다(스레드 PATCH 는 status · archived 만 받습니다).

**⑥ 아직 확인되지 않은 것 하나 — 정직하게 적어 둡니다**

발송 **API** 가 다른 인박스의 채널 계정을 거절하는지는 **모릅니다.** 화면이 안 보여 주는 것과
API 가 거절하는 것은 다른 이야기이고, 실제 발송을 한 번 해 보는 것 말고는 판별할 방법이
없습니다(→ 3장 「대안」, 5장 ②). 지금 콘솔은 화면과 **같은 규칙**으로 막아 두었습니다.

---

## 1. 지금 포털의 인박스 구조

HubSpot API 로 직접 조회한 이메일 채널 계정 전체입니다. 회신은 **여기 있는 주소로만** 나갈 수 있습니다.

| 인박스 | 연결된 이메일 주소 | B2B 티켓 대화 |
|---|---|---|
| **Inbox** | **`support@perso.ai`** ← 지금 대부분의 회신<br>`support@45169260.hubspot-inbox.com`<br>`support@perso.co.kr.hs-inbox.com` | **100 / 100** |
| **GTM Marketing** | **`perso.ai@estsoft.com`** ← 우리가 쓰고 싶은 주소<br>`support-3@estsoft.com.hs-inbox.com` | 63 / 100 |
| Help Desk | `support-1@estsoft.com.hs-inbox.com`<br>`support@interactive.perso.ai` *(인증 만료 — 발송 불가)* | 1 / 100 |
| Interactive | `support-2@estsoft.com.hs-inbox.com` | 0 / 100 |

> `*.hs-inbox.com` 은 HubSpot 내부 전달 주소입니다.

**핵심: 회신은 그 대화가 속한 인박스의 주소로만 나갑니다.**
B2B 티켓의 대화는 **전부 Inbox 인박스에 있는데** `perso.ai@estsoft.com` 은 **GTM Marketing** 에
있어서, 지금 구조로는 그 주소를 쓸 수 없는 티켓이 생깁니다.

그리고 팀 주소는 **한 인박스에만** 연결됩니다 — 두 곳에 같이 두는 방법이 없어 어디에 둘지를 골라야 합니다.

> “a team email address can only be connected to **one** of the inboxes in your account.”
> — HubSpot 고객센터, *Connect a personal or team email to the conversations inbox*

---

## 2. 측정 — 지금 어느 주소로 나가는가

콘솔이 실제로 회신하는 단계(New · Qualified · Negotiating)의 티켓 전부를 대상으로,
발송 직전 로직을 그대로 실행해 계산했습니다.

**2026-09-03 재측정** — B2B 파이프라인에서 연락처 메일이 있는 티켓 **103건**에 발송 직전
로직을 그대로 실행했습니다(읽기만, 메일은 나가지 않았습니다).

| 발신 주소 | 지금 | **주소 이관 후(예상)** |
|---|---:|---:|
| `perso.ai@estsoft.com` | **85** (83%) | **102** (99%) |
| `support@perso.ai` | 18 | 0 |
| 기계 주소 (그 티켓엔 다른 길이 없어서) | 0 | 1 |
| **합계** | **103** | **103** |

- **코드로 할 수 있는 것은 이미 다 했습니다.** 고른 주소를 쓸 수 있는 대화를 먼저 찾도록
  바꾼 뒤 83%가 되었습니다(그 전에는 32%였습니다).
- 남은 **18건**은 그 티켓에 GTM Marketing 인박스의 대화가 없어서입니다. 코드로는 못 넘습니다 —
  **없는 대화에 답장을 붙일 수는 없습니다.**
- 그중 2건은 한때 **허브스팟이 자동 발급한 기계 주소**(`support@45169260.hubspot-inbox.com`)로
  나갔습니다 — 폼 채널에 「Customer agent reply email」이 설정돼 있지 않아 폼 문의의 첫 메일이
  그 주소로 기록되기 때문입니다. **콘솔 쪽은 고쳤습니다**(기계 주소를 맨 뒤로 미룹니다).
  다만 폼 채널의 그 설정을 채워 두시면 허브스팟 화면에서 사람이 답할 때도 같이 정리됩니다.
### 왜 그런가 — 문의가 들어오는 문이 둘입니다

폼 19개가 **전부 `Inbox` 인박스**에 있고, `GTM Marketing` 에는 **폼이 하나도 없습니다.**

- 고객이 **폼**을 채우면 → 대화가 **`Inbox`** 에 생깁니다
- 고객이 **`perso.ai@estsoft.com` 으로 메일**을 보내면 → 대화가 **`GTM Marketing`** 에 생깁니다

한 티켓에 둘 다 있을 수 있습니다(폼으로 문의한 뒤 메일도 주고받은 경우). **티켓이 다른
인박스에서 옮겨 온 것이 아니라, 처음부터 들어온 문이 달랐던 것입니다.**

### 전수 측정 (B2B 파이프라인 328건 전부)

| 그 티켓의 대화가 있는 인박스 | 건수 | 옮긴 뒤 `perso.ai@estsoft.com` |
|---|---:|---|
| `Inbox` · `GTM Marketing` 둘 다 | 227 (69%) | ✅ 가능 |
| `Inbox` 만 (폼으로만 들어옴) | 94 (29%) | ✅ **새로 가능해짐** |
| `GTM Marketing` 만 | 6 (2%) | ❌ **못 쓰게 됨** |
| 대화 없음 | 1 | — |

- 옮기면 **321건(98%)** 이 그 주소로 나갑니다. 지금은 그 반대로 **94건이 원천적으로 불가능**합니다.
- **못 쓰게 되는 6건은 전부 `Lost` 이고 2025년 9~10월에 만들어진 건**입니다. 살아 있는 일감이
  아니라 실무 영향이 없습니다. (티켓 30423872602 · 30410704816 · 30765303874 · 31675491703 ·
  31735767315 · 31949554453)

---

## 3. 옮기면 달라지는 것

- ✅ **모든 B2B 티켓에서 그 주소로 회신할 수 있게 됩니다.** — 이번 요청의 목적입니다.
- ⚠️ **그 주소로 오는 메일이 Inbox 인박스로 들어옵니다.** 지금은 GTM Marketing 으로 들어옵니다.
  보는 사람·알림·자동화가 달라지므로 **이 부분이 확인이 필요합니다.**
- ➖ **GTM Marketing 대화에서는 그 주소를 못 쓰게 됩니다.** 다만 모든 티켓이 Inbox 대화를 함께
  갖고 있고 콘솔이 그쪽을 고르므로 **실질적인 손실은 없습니다.**

**순서가 중요합니다.** GTM Marketing 에서 **먼저 연결을 해제**한 뒤 Inbox 에 붙여야 합니다 —
붙어 있는 채로 추가하려 하면 화면이 그 자리에서 막습니다(0장 ③).

**같이 따라오는 것:** GTM Marketing 인박스에는 지금 대화가 **4,354건** 있고, 내용은 대부분
PERSO 회원 · 크레딧 통계 자동 메일과 벤더 메일(Futurepedia · Rewardful · OpenAI Ads)입니다.
주소를 옮기면 **그 메일들이 앞으로 Inbox 인박스로 들어옵니다** — Inbox 는 이미 열린 대화가
9,595건입니다. 이것이 이 결정의 실질적인 비용이고, 아래 ①의 판단 포인트입니다.

### 대안 — 옮기지 않고 확인하는 방법

다른 인박스의 주소를 써도 HubSpot 이 받아 주는지는 **문서에 없고 공개 사례도 없습니다.**
실제로 한 번 보내 봐야만 알 수 있습니다(읽기 조회로는 판별이 안 됩니다).
되는 것으로 확인되면 **주소를 옮기지 않고도 해결됩니다.**

담당자가 제어하는 주소로 문의를 하나 넣어 검증할 수 있습니다 — **실제 고객에게는 아무것도 나가지 않습니다.**

---

## 4. `untae@estsoft.com` 을 쓸 수 없는 이유

가이드 4단계의 「보내는 사람 드롭다운에서 변경」은 **HubSpot 화면 전용** 기능입니다.
가이드에 적힌 두 주소의 연결 경로가 서로 **다른 기능**이고, 콘솔이 쓸 수 있는 것은 한쪽뿐입니다.

| 주소 | 가이드의 설정 경로 | HubSpot 기능 | 콘솔 발송 |
|---|---|---|---|
| `perso.ai@estsoft.com` | 설정 › CRM › 받은 편지함 › 채널 연결 | 대화(Conversations) 채널 | ✅ 가능 |
| `untae@estsoft.com` | 설정 › 일반 › 이메일 › 계정 연결 | 개인 연결 이메일 | ❌ **불가** |

**화면에서도 확인했습니다** (2026-09-03). `untae@estsoft.com` 은 Settings › General ›
Email 에 G Suite · Enabled 로 연결돼 있고, 티켓 [Email] 버튼의 보내는사람 목록에 **인박스
라벨 없이** 단독으로 뜹니다(다른 항목에는 `Inbox` · `GTM Marketing` 라벨이 붙습니다).
대화 답장창에는 **없습니다.**

발송 API 가 요구하는 값은 `channelAccountId` 인데 이 주소에는 그 id 가 **존재하지 않습니다**
(채널 계정 8개를 조회해 확인). 콘솔이 넣을 값 자체가 없습니다 — 이건 추론이 아니라
「없는 값을 못 넣는다」입니다.

개인 연결 이메일로 보내는 기능의 API 는 **기록만 하고 발송하지 않습니다.**

> “Use the email engagement API to **log and manage** emails on CRM records.”
> — HubSpot 개발자 문서, *Email engagement API*

**권한을 더 준다고 해결되지 않습니다.** 스코프가 모자란 것이 아니라 그 기능의 발송 API 자체가
없습니다. (참고로 현재 토큰은 그 API 를 **읽기는** 합니다 — 조회는 정상 응답합니다.)

### 그럼 팀 이메일로 바꾸면?

가능은 합니다. 대신 잃는 것이 크고, **가이드의 절반이 성립하지 않게 됩니다.**

- **그 사서함에 오는 모든 메일이 팀 전체에 보입니다.** 고객 메일만이 아니라 사내 메일까지입니다.
  HubSpot 문서도 *“do not connect an email account that you use to send personal emails”* 라고 명시합니다.
- **Gmail 답장 자동 로깅이 안 됩니다** — 가이드 3️⃣ 방법 B 가 성립하지 않습니다.
- 시퀀스 발송, HubSpot Sales 확장, 「로그 및 추적 › 티켓」 설정도 함께 사라집니다.
- 같은 주소를 개인·팀 양쪽에 연결할 수 없습니다 — 한쪽을 택해야 합니다.

그래서 **지금 구조(개인 이메일 유지)가 맞다**고 봅니다. 콘솔은 최초 응대를 팀 주소로 처리하고,
심층 상담 단계의 담당자 메일은 가이드대로 사람이 HubSpot 화면에서 보내는 역할 분담입니다.

> 참고로 콘솔이 보내는 회신의 **본문 서명은 이미 「배운태 / Untae Bae」** 입니다.
> 고객이 보는 이름은 담당자 개인 이름이고, 다른 것은 봉투의 발신 주소뿐입니다.

---

## 5. 내일 확인하고 싶은 것

**① [결정] `perso.ai@estsoft.com` 을 Inbox 인박스로 옮겨도 될까요?**
옮기면 그 주소로 오는 **수신 메일도** Inbox 인박스로 들어옵니다.
지금 GTM Marketing 인박스를 보고 계신 분들의 업무에 영향이 있는지가 판단 포인트입니다.

**② [확인] 테스트용으로 쓸 수 있는 외부 주소가 있을까요?**
다른 인박스의 주소로도 발송이 되는지 확인하려면 실제 발송 한 번이 필요합니다.
담당자가 제어하는 주소로 문의를 하나 넣어 주시면 고객에게는 아무것도 나가지 않는 상태로
검증할 수 있습니다. 되는 것으로 확인되면 **주소를 옮기지 않아도 됩니다.**

**③ [공유] 가이드 4단계는 계속 수동으로 갑니다.**
`untae@estsoft.com` 으로의 전환은 콘솔이 대신할 수 없어 지금처럼 HubSpot 화면에서 진행하시면 됩니다.
설정으로 풀리는 문제가 아니라는 점만 공유드립니다.

---

### 측정 방법

HubSpot Conversations API 로 이메일 채널 계정과 티켓별 대화를 직접 조회하고, 콘솔의 발송 경로
결정 로직(`find_conversation_reply_context`)을 그대로 실행해 계산했습니다.
**모두 읽기 조회이며 메일은 발송하지 않았습니다.**

인용한 HubSpot 문서
- [Email engagement API](https://developers.hubspot.com/docs/reference/api/crm/engagements/email)
- [Conversations API guide](https://developers.hubspot.com/docs/api-reference/legacy/conversations/guide)
- [Connect channels to the conversations inbox](https://knowledge.hubspot.com/inbox/connect-channels-to-the-conversations-inbox)
- [Connect a personal or team email to the conversations inbox](https://knowledge.hubspot.com/connected-email/choose-an-inbox-connection)
