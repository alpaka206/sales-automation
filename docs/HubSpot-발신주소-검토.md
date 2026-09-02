# 인박스와 발신 주소 — 담당자 확인 요청

작성 2026-09-02 · 근거: HubSpot API 직접 조회 · HubSpot 공식 문서 · 저장소 코드
(모두 읽기 조회이며 **메일은 한 통도 발송하지 않았습니다**)

콘솔이 보내는 회신이 **어느 주소에서 나가는지**는 HubSpot 의 인박스 구조가 정합니다.
코드로는 넘을 수 없는 벽이 두 개 있어 설정 쪽 확인이 필요합니다.

---

## 요청 두 가지

### 요청 1 — `perso.ai@estsoft.com` 을 **Inbox** 인박스로 옮겨 주세요

지금은 GTM Marketing 인박스에 있습니다. 그대로면 콘솔이 처리하는 티켓 5건 중 **2건만**
이 주소로 나가고 나머지는 `support@perso.ai` 로 나갑니다. 옮기면 **5건 전부** 이 주소가 됩니다.

### 요청 2 — `untae@estsoft.com` 자동 발송은 **불가능**합니다

권한이나 설정 문제가 아니라 **HubSpot 에 그 API 가 없습니다.**
가이드 4단계(담당자 메일로 전환)는 지금처럼 사람이 HubSpot 화면에서 하는 것이 맞습니다.

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

| 발신 주소 | 현재 | 코드 개선 후 | **주소 이관 후** |
|---|---:|---:|---:|
| `perso.ai@estsoft.com` | 1 | 2 | **5** |
| `support@perso.ai` | 2 | 2 | 0 |
| `support@45169260.hubspot-inbox.com` | 1 | 1 | 0 |
| 발송 실패 | 1 | 0 | 0 |
| **합계** | **5** | **5** | **5** |

- **「코드 개선 후」는 이미 반영했습니다** — 고른 주소를 쓸 수 있는 대화를 먼저 찾도록 바꿨고,
  덤으로 **지금 발송이 아예 실패하는 티켓 1건이 살아납니다.**
- 다만 코드만으로는 **5건 중 2건이 한계**입니다. 나머지는 그 주소가 그 인박스에 없기 때문입니다.
- 전체 B2B 티켓 100건으로 넓혀도 결론은 같습니다: **100건 모두 Inbox 인박스에 대화가 있고**,
  GTM Marketing 에만 있는 티켓은 **0건**입니다. 그래서 옮길 방향은 Inbox 쪽입니다.

---

## 3. 옮기면 달라지는 것

- ✅ **모든 B2B 티켓에서 그 주소로 회신할 수 있게 됩니다.** — 이번 요청의 목적입니다.
- ⚠️ **그 주소로 오는 메일이 Inbox 인박스로 들어옵니다.** 지금은 GTM Marketing 으로 들어옵니다.
  보는 사람·알림·자동화가 달라지므로 **이 부분이 확인이 필요합니다.**
- ➖ **GTM Marketing 대화에서는 그 주소를 못 쓰게 됩니다.** 다만 모든 티켓이 Inbox 대화를 함께
  갖고 있고 콘솔이 그쪽을 고르므로 **실질적인 손실은 없습니다.**

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
