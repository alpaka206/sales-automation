"""저장소의 씨앗 파일에서 「항상 적용」 규칙을 다시 넣습니다.

**되살릴 데가 여기밖에 없는 경우는 이제 하나뿐입니다.** 2026-08-27 부터 콘솔에서 지운 것은
행이 남습니다 — 목록에서만 사라지고 DB 에서는 안 사라집니다(``db/soft_delete``). 그래서
이메일 템플릿이든 정책 문서든 되살리는 길은 그 행의 ``status`` 를 ``active`` 로 돌리는
것이고, 본문이 필요하면 「판본 기록」(``document_revisions``)에 있습니다.

남은 하나: **``mode='rules'`` 문서를 아예 처음부터 다시 넣어야 할 때.** 그 종류는 DB 에
사본이 없고, 마이그레이션 0043 이 처음 넣은 텍스트가 저장소에 있습니다 —
``src/db/seeds/policy/rule_*.md``. 되살아나는 것은 **원본**이라 그 뒤 콘솔에서 고친 내용은
돌아오지 않습니다. 화면에서 한 번 훑어야 합니다.

    python scripts/restore_deleted.py                      # 되살릴 수 있는 씨앗 목록
    python scripts/restore_deleted.py rule_01_tone.md      # 씨앗 파일에서 규칙 되살리기

여기 「사본에서 정책 문서 되짚기」가 있었습니다. 초안이 읽던 사본 표(``knowledge_documents``)
가 2026-08-27 에 없어졌습니다 — 라우터가 ``policy_sources`` 를 직접 읽습니다. 그리고 그
길이 존재하던 이유(등록부 행이 하드 삭제로 사라짐)도 같이 없어졌습니다.

사내망에서는 DB(5432/6543)가 막혀 있습니다. 서버 셸이나 망 밖에서 실행하세요.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.models import PolicySource  # noqa: E402
from src.db.session import SessionLocal  # noqa: E402

SEEDS_DIR = Path(__file__).resolve().parents[1] / "src" / "db" / "seeds" / "policy"


def _rule_seeds() -> dict[str, Path]:
    """씨앗 파일 이름 → 경로. 0043 이 ``file:<이름>`` 을 doc_key 로 넣은 그 파일들입니다."""
    return {path.name: path for path in sorted(SEEDS_DIR.glob("rule_*.md"))}


def _restore_rule(session, path: Path) -> str:
    """씨앗 파일을 「항상 적용」 규칙으로 다시 넣습니다. 0043 이 하던 일 그대로.

    label 은 파일 이름이 아니라 **본문 첫 제목**입니다. 0043 은 파일 이름(``path.stem``)을
    썼는데, 화면에 ``rule_01_common_principles`` 라고 뜨면 운영자가 그게 뭔지 모릅니다 —
    지운 그 문서인지도 모릅니다.
    """
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    label, body = path.stem, "\n".join(lines).strip()
    if lines and lines[0].startswith("# "):
        # 제목 줄은 label 로 올립니다. 놔두면 프롬프트에 제목이 두 번 들어갑니다 —
        # `_rules_from_db` 가 본문 앞에 `# {label}` 을 다시 붙입니다.
        label, body = lines[0][2:].strip(), "\n".join(lines[1:]).strip()
    key = f"file:{path.name}"
    if session.query(PolicySource).filter_by(doc_key=key).one_or_none() is not None:
        raise SystemExit(f"'{key}' 는 이미 등록되어 있습니다. 지운 것이 아닙니다.")
    # 순서 칸은 없습니다 (0101) — 「항상 적용」은 만든 순서(id)로 이어 붙습니다.
    session.add(PolicySource(label=label, title=label, doc_key=key, mode="rules", body=body))
    session.commit()
    return (
        f"항상 적용 규칙 복원: {label} ({key})\n"
        "  ※ 마이그레이션이 처음 넣은 원본입니다. 콘솔에서 고친 내용은 들어 있지 않으니,\n"
        "    정책 문서 화면에서 한 번 읽어 보세요."
    )


def main() -> None:
    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    with SessionLocal() as session:
        seeds = _rule_seeds()

        if wanted is None:
            # 지금 등록된 것과 씨앗 파일을 **나란히** 보여 줍니다. "없어진 것" 으로 계산해서
            # 보여 주지 않는 이유: 씨앗 파일은 이름이 바뀐 적이 있고(rule_01_tone.md →
            # rule_01_common_principles.md, 마이그레이션 없이) 콘솔에서 제목도 바뀝니다.
            # 그러면 멀쩡히 있는 문서가 매번 "지워졌다" 로 뜹니다.
            print("지금 등록된 「항상 적용」 규칙:")
            live_rules = session.query(PolicySource).filter_by(mode="rules").all()
            for source in live_rules or ():
                print(f"  {source.doc_key:40} {source.label}")
            if not live_rules:
                print("  없습니다.")

            print("")
            print("씨앗 파일에서 다시 넣을 수 있는 규칙(위와 비교해서 고르세요):")
            for name, path in seeds.items():
                first = path.read_text(encoding="utf-8").lstrip("# \n").splitlines()[0].strip()
                print(f"  {name:40} {first}")

            print("")
            print("지운 문서·템플릿은 여기 없습니다 — 그 행은 DB 에 그대로 남아 있습니다.")
            print("  status 를 'active' 로 돌리면 화면에 다시 뜨고, 예전 본문은 판본 기록에 있습니다.")
            return

        if wanted in seeds:
            print(_restore_rule(session, seeds[wanted]))
        else:
            raise SystemExit(f"'{wanted}' 를 씨앗 목록에서 못 찾았습니다. 인자 없이 실행해 보세요.")


if __name__ == "__main__":
    main()
