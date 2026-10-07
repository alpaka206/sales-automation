"""같은 메일의 두 벌(옛 CRM 줄 · 개인함 줄)을 허브스팟 스레드 줄에 접습니다 — 한 번 돌리는 스크립트.

수집기는 티켓을 **다시 수집할 때만** 접고(대기열은 순환이 아닙니다), 접는 열쇠가 「같은 초」였던 동안은 한
쌍도 안 접혔습니다 — 수집이 끝난 대화 176건에 CRM 사본 190줄이 남아 같은 메일이 두 번 섰습니다(2026-10-06
운영 실측). 규칙은 수집기와 같은 함수입니다(`agents.ticket_history.fold_history_twins` → `_merge_crm_twins`).

**기본은 세기만 합니다** — 아무것도 안 지우고, 무엇이 무엇에 접힐지 적습니다. 읽어 보고 `--apply` 로 다시
돌리세요. 허브스팟에는 아무것도 안 씁니다: 지우는 것은 우리 DB 의 사본 줄이고, 지운 줄은 묘비를 남겨
수집기가 되살리지 않습니다.

    .venv\\Scripts\\python.exe -m scripts.fold_history_twins            # 세기만
    .venv\\Scripts\\python.exe -m scripts.fold_history_twins --apply    # 접기

사내망에서는 DB(5432/6543)가 막혀 있습니다. 서버 셸이나 망 밖에서 실행하세요.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    from src.agents.ticket_history import fold_history_twins

    apply = "--apply" in (sys.argv[1:] if argv is None else argv)
    report = fold_history_twins(apply=apply)
    for conversation_id, gone, kept in report["folded"]:
        print(f"conv {conversation_id}: {gone} -> {kept}")  # 출력은 ASCII 기호만 — 윈도 콘솔(cp949)이 —·→ 를 못 쓴다
    verb = "접었습니다" if apply else "접을 수 있습니다 (세기만 했습니다. --apply 로 실행)"
    print(f"대화 {report['conversations']}건을 보고, 사본 {len(report['folded'])}줄을 {verb}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
