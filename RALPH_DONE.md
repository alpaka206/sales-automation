# Ralph Loop 폴리시 완료

**완료일:** 2026-05-27

## 시스템 요약

Sales Automation은 HubSpot CRM 기반의 영업 자동화 시스템입니다. 인바운드 에이전트가 HubSpot 문의를 분석하고 답변을 작성하며, 아웃바운드 에이전트가 YouTube/LinkedIn/CSV 소스에서 잠재 고객을 발굴하고 개인화된 이메일을 작성합니다. 모든 발송은 Slack/Teams/웹 UI를 통한 사람의 승인 후 진행됩니다. FastAPI 백엔드, SQLite/PostgreSQL 데이터베이스, Claude CLI 또는 Anthropic API 기반 LLM 레이어로 구성되며, Windows 노트북에서 비개발자가 `scripts/setup.bat`과 `scripts/run.bat`으로 설치·실행할 수 있습니다. 전체 테스트 591건 통과, 핵심 모듈 커버리지 70% 이상 달성.

## 참고 문서

- [README.md](README.md) — 프로젝트 개요 및 빠른 시작 가이드
- [docs/사용법.md](docs/사용법.md) — 비개발자용 상세 사용법
