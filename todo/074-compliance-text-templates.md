# 074 — GDPR/CAN-SPAM/정통망법 컴플라이언스 텍스트 통합

## Why

법적 안전망. 각국 법규 명시한 표준 텍스트 + 발신자 정보 강제.

## What to do

1. `src/integrations/senders/compliance.py` 신규:
   - `build_footer(language: str, country_code: str | None) -> str` — 국가/언어별 footer 구성.
   - 한국: "이 메일은 [회사명](사업자번호 [...])의 광고성 정보..."
   - EU 거주자: GDPR 데이터처리 근거 명시.
   - US: CAN-SPAM 물리주소 명시.
   - 그 외: 일반 unsubscribe 안내.
2. `.env.example` 에 회사 정보 변수:
   - `COMPANY_NAME`, `COMPANY_REGISTRATION_NUMBER`, `COMPANY_ADDRESS`, `COMPANY_PRIVACY_POLICY_URL`.
3. 영문 메일 제목 앞에 `[AD]` 같은 광고 표시 옵션 (한국 정통망법 보수적 준수): `KOREA_AD_PREFIX_ENABLED=true` 시.

## Acceptance criteria

- 한국어 메일 footer: 회사명/사업자번호/연락처/주소/광고문구.
- 영문 (US 도메인): physical address + unsubscribe.
- 영문 (EU 도메인): GDPR notice + privacy policy 링크.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_compliance_footer.py -q
```

## Risks

- 법무 검토 필수 — 코드는 안전망일 뿐, 최종 책임은 운영자.
