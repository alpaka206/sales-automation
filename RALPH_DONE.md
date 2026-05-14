# 폴리시 완료 — Sales Automation

- 완료 일시: 2026-05-15
- 최종 커밋: 5f5115d6d30d0d1666dad6a259df69626f9b6a93

## 시스템 요약

인바운드 문의 자동 분류·답장, 아웃바운드 리드 발굴·이메일 초안 작성, 일간/주간 리포트 자동 생성을 수행하는 세일즈 자동화 시스템입니다. LLM(Claude CLI 또는 Anthropic API)이 판단이 필요한 단계(분류, 점수화, 초안 작성)를 처리하고, n8n이 스케줄·이벤트 트리거를 담당하며, FastAPI 서버가 비즈니스 로직을 실행합니다. 모든 발송은 Slack/Teams 승인 카드를 통한 사람 확인 후에만 이루어집니다. YouTube·LinkedIn·CSV 등 다양한 소스에서 잠재 고객을 수집하며, 중복 방지·ICP 점수 필터링·팔로업 자동화가 포함됩니다. 비개발자도 `scripts\setup.bat` 한 번 실행으로 설치하고 `scripts\run.bat`으로 서버를 시작할 수 있습니다.

## 다음 단계

- 사용자: [README.md](README.md) → 빠른 시작 섹션
- 운영자: [docs/사용법.md](docs/사용법.md), [docs/문제해결.md](docs/문제해결.md)
