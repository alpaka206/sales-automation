# WhatsApp Cloud API 설정 가이드

이 문서는 WhatsApp Business Cloud API 를 사용해 인바운드 답장을 WhatsApp 으로 발송하기 위한 설정 절차를 안내합니다.

---

## 사전 준비

1. **Meta Business Suite 계정** — https://business.facebook.com 에서 비즈니스 계정 생성.
2. **WhatsApp Business 앱** — Meta for Developers (https://developers.facebook.com) 에서 앱 생성 후 WhatsApp 제품 추가.
3. **전화번호 등록** — WhatsApp Business 에 사용할 전화번호를 등록하고 인증.

---

## 1단계 — API 토큰 발급

1. https://developers.facebook.com → 앱 선택 → 좌측 **WhatsApp** → **API Setup**.
2. **Temporary access token** 을 복사 (개발 테스트용, 24시간 만료).
3. 프로덕션용은 **System User** 토큰을 사용하세요:
   - Business Settings → System Users → 토큰 생성 → `whatsapp_business_messaging` 퍼미션 부여.
4. **Phone number ID** 도 같은 페이지에서 확인 (`From` 번호 아래 표시).

`.env` 에 입력:

```
WHATSAPP_ENABLED=true
WHATSAPP_ACCESS_TOKEN=<위에서 발급한 토큰>
WHATSAPP_PHONE_NUMBER_ID=<Phone number ID>
```

---

## 2단계 — 메시지 템플릿 생성 및 승인

WhatsApp 정책상 첫 메시지(24시간 대화 창이 열리지 않은 상태)는 **사전 승인된 템플릿**으로만 보낼 수 있습니다.

### 템플릿 생성

1. Meta Business Suite → **WhatsApp Manager** → **Message Templates**.
2. **Create Template** 클릭.
3. 입력:
   - **Category**: Marketing 또는 Utility
   - **Name**: `sales_reply_intro` (코드와 동일하게)
   - **Language**: 한국어 (`ko`)
   - **Body**: 본문 텍스트. 변수를 사용하려면 `{{1}}` 형식:

   ```
   안녕하세요, perso의 김규원입니다. 문의해 주셔서 감사합니다.

   {{1}}

   궁금하신 점이 있으시면 이 채팅으로 답장해 주세요.
   ```

4. **Submit** → Meta 심사 (보통 1~3일, 빠르면 수 시간).

### 승인 확인

템플릿 상태가 **Approved** 로 바뀌면 사용 가능합니다. **Rejected** 이면 사유를 확인하고 수정 후 재제출하세요.

`.env` 에 입력:

```
WHATSAPP_TEMPLATE_NAME=sales_reply_intro
```

---

## 3단계 — 테스트

```powershell
# 서버 시작
scripts\run.bat

# 다른 터미널에서 테스트 (실제 WhatsApp 번호 필요)
curl -X POST http://localhost:8000/webhook/hubspot/inbound ^
  -H "X-Internal-Token: <your_token>" ^
  -H "Content-Type: application/json" ^
  -d "{\"event_type\": \"contact.creation\", \"object_id\": \"test\", \"phone\": \"+821012345678\", \"whatsapp_opt_in\": true}"
```

---

## 동작 방식

- `WHATSAPP_ENABLED=false` (기본값): WhatsApp 관련 코드가 조용히 스킵됩니다.
- `WHATSAPP_ENABLED=true`: 이메일 발송 시 contact 에 전화번호가 있으면 WhatsApp 템플릿도 함께 발송 시도합니다.
- WhatsApp 발송이 실패해도 이메일 발송에는 영향이 없습니다.
- 채널이 `whatsapp` 인 메시지는 WhatsApp 으로만 발송됩니다.

---

## 비용

- WhatsApp Business API 는 **대화 기반 과금**입니다.
- Marketing 대화: ~$0.04/건 (한국 기준)
- Utility 대화: ~$0.02/건
- 월 1,000건 대화까지 무료 (첫 사용 시 무료 크레딧 제공).

---

## 제한 사항

- 첫 메시지는 반드시 승인된 템플릿이어야 합니다 (Meta 정책).
- 상대방이 24시간 이내 회신하면 자유 형식 메시지 발송 가능합니다 (freeform).
- 템플릿 승인에 1~3일 소요됩니다.
