# 060 — 다국어 메일 프롬프트 강화 (recipient 언어 자동 작성)

## Why

글로벌 영업이라 한국어 외 영어/일본어/스페인어/포르투갈어 등 자동 작성 필요. 현재 프롬프트가 `{{language}}` 변수만 받고 실제 강제력 약함.

## What to do

1. 모든 outbound 메일 프롬프트 (`email_*.md`) 에 강제 절 추가:
   ```
   ## Language enforcement
   - You MUST write the entire email in {{language}}.
   - If your draft is in a different language, translate it before responding.
   - Subject + body + signature all in {{language}}.
   ```
2. `language` 결정 로직 강화 — `outbound/icp_score.md` 의 `language_guess` 가 ISO 639-1 코드 (ko/en/ja/es/pt/zh/...) 로 응답하도록 명시.
3. 국가 코드 → 언어 매핑 fallback:
   - KR/JP → ko/ja
   - US/GB/AU → en
   - DE → de, FR → fr, ES → es, MX/AR/CL → es, BR → pt
   - SG/HK/TW/MY/ID/VN/TH → en (영문이 안전)
4. 시그니처 (perso/김규원/Intern) 는 언어별로 자연스럽게 — LLM 이 자동으로 "Kyuwon Kim / perso / Intern" 식 변환.

## Acceptance criteria

- 영어 단위 테스트 케이스: 영어 답장 초안에 한국어 없음, 시그니처 영문화.
- 일본어 케이스도 같이.

## Verify

```powershell
.venv\Scripts\python.exe -m pytest tests/test_multilingual_drafts.py -q
```
