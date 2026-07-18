---
# ── 필수 ──────────────────────────────────────────────
title: <문서 제목>                       # DB title 컬럼
categories: [pricing_question]           # 인바운드 분류 매칭 (아래 목록 참고). 전부면 [all]
# ── LLM 라우터용 (강력 권장) ──────────────────────────
summary: <한 줄 요약>                     # 라우터가 이 한 줄을 보고 관련성 판단. 핵심만.
tags: [키워드1, 키워드2]                  # 검색/라우팅 보조 키워드
# ── 메타데이터 ───────────────────────────────────────
scope: inbound                           # 인바운드 답변 정책
author: <작성자>
status: active                           # active | draft | archived  (active만 LLM에 노출)
created_at: 2026-01-01                    # YYYY-MM-DD
updated_at: 2026-01-01                    # 수정 시 갱신
---

# <문서 제목>

> 작성 가이드: 본문은 사실(가격, 정책, FAQ, 제품 정보)만 담습니다.
> 메일에 그대로 인용하면 안 되는 내용(구체 금액 등)은 "운영자·LLM 참고용"임을 명시하세요.

## 섹션 1

내용...

## 섹션 2

내용...

<!--
이 파일(_ 로 시작)은 import_knowledge_base.py 가 건너뜁니다 (템플릿/스크래치 용도).
새 문서는 _ 없는 파일명으로 복사해서 작성하세요. 예: cp _TEMPLATE.md perso_faq.md
categories 가능 값: purchase_inquiry, partnership, pricing_question, support, recruiting, other, all
-->
