"""개인함 수집이 **보낸 메일로 들여온 지메일 초안**을 지웁니다 — 한 번 돌리는 스크립트.

개인함 수집(2026-09-08, f2ea2d8)은 처음에 임시보관함을 거르지 않았습니다. 초안은 저장할 때마다 새 메시지 id
로 오므로, 쓰다 만 글이 「우리가 보낸 메일」로 티켓 기록에 서고 허브스팟 노트까지 남았습니다. 거르기는
a6b7879 배포(2026-09-22 03:04 UTC)부터입니다 — 그 **전에** 들어온 `gmail:` 발신 줄만 봅니다.

**DB 만으로는 못 가립니다** (운영 실측): 같은 날 같은 제목으로 선 줄들 중에도 진짜로 나간 회신이 있습니다
(2820 · 2822 · 2827). 그래서 줄마다 지메일에 그 메시지를 다시 묻습니다:

- 라벨에 `DRAFT` 가 있으면 초안 — 지우고 묘비를 남깁니다(`customer_ops.interaction_delete` 와 같은 표 — 안
  남기면 다음 수집이 되살립니다).
- 404 면 **사람이 봅니다** — 초안이 지워졌을 수도, 진짜 메일이 영구 삭제됐을 수도 있습니다.
- 사서함 토큰이 죽었으면 그 사서함은 건너뜁니다 — 확인할 수 없는 것은 지우지 않습니다.

**기본은 세기만 합니다.** 목록을 읽어 보고 `--apply` 로 다시 돌리세요. 허브스팟에 그 초안이 남긴 노트는
지우지 않습니다(저쪽 기록입니다 — `docs/HubSpot-쓰기-감사-2026-09-22.md`).

    .venv\\Scripts\\python.exe -m scripts.cleanup_gmail_drafts            # 세기만
    .venv\\Scripts\\python.exe -m scripts.cleanup_gmail_drafts --apply    # 지우기

사내망에서는 DB(5432/6543)가 막혀 있습니다. 서버 셸이나 망 밖에서 실행하세요.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from src.db.models import CustomerInteraction, MailboxLinkDecision  # noqa: E402

# a6b7879 배포 시각(UTC) — 그 뒤로는 수집이 초안을 거릅니다(`mailbox_sync._NOT_A_MAIL`).
DRAFTS_FILTERED_SINCE = datetime(2026, 9, 22, 3, 4)
_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _mailbox(row: CustomerInteraction) -> str | None:
    """그 줄을 들여온 사서함 — 수집기가 `context` 에 적은 「<사서함> 개인 메일함」, 없으면 보낸 주소."""
    from src.agents.followup_sequence import _base_mailbox

    found = _base_mailbox(row)
    return found.removeprefix("gmail:") if found else None


def review(session, labels_of: Callable[[str, str], set[str] | None], *, apply: bool = False,
           before: datetime = DRAFTS_FILTERED_SINCE) -> dict[str, list[int]]:
    """줄마다 지메일에 묻고 가릅니다. {"draft": 초안(지울 것), "missing": 404(사람이 볼 것), "kept": 진짜 메일,
    "unreadable": 사서함이 죽었거나 못 물은 것} — 값은 접점 기록 id 들. `apply` 면 초안을 지우고 커밋합니다.

    `labels_of(사서함, 지메일 id)` 가 라벨들을(그 메시지가 없으면 None) 돌려줍니다 — 네트워크는 그 함수
    하나만 지납니다(`_gmail_labels`). 테스트는 가짜를 넘깁니다."""
    from src.integrations.gmail import MailboxTokenError

    report: dict[str, list[int]] = {"draft": [], "missing": [], "kept": [], "unreadable": []}
    dead: set[str] = set()
    rows = session.scalars(
        select(CustomerInteraction).where(
            CustomerInteraction.external_id.like("gmail:%"),
            CustomerInteraction.direction == "outgoing",
            CustomerInteraction.created_at < before,
        ).order_by(CustomerInteraction.id)
    ).all()
    for row in rows:
        mailbox = _mailbox(row)
        if not mailbox or mailbox in dead:
            report["unreadable"].append(row.id)
            continue
        try:
            labels = labels_of(mailbox, row.external_id.removeprefix("gmail:"))
        except MailboxTokenError:
            dead.add(mailbox)  # 재연결 전에는 그 사서함의 어느 줄도 확인할 수 없습니다
            report["unreadable"].append(row.id)
            continue
        except Exception as exc:  # 한 줄을 못 물어도 나머지는 봅니다
            print(f"  ! {row.external_id}: {type(exc).__name__}: {exc}", file=sys.stderr)
            report["unreadable"].append(row.id)
            continue
        if labels is None:
            report["missing"].append(row.id)
        elif "DRAFT" in labels:
            report["draft"].append(row.id)
            if apply:
                session.delete(row)
                session.merge(MailboxLinkDecision(
                    external_id=row.external_id, conversation_id=None, decided_by="gmail_draft",
                    decided_at=datetime.now(timezone.utc),
                ))
        else:
            report["kept"].append(row.id)
    if apply:
        session.commit()
    return report


def _gmail_labels(mailbox: str, gmail_id: str) -> set[str] | None:
    """지메일에 그 메시지의 라벨을 묻습니다(읽기만). 토큰이 죽었으면 `MailboxTokenError` 가 그대로 납니다."""
    import httpx

    from src.integrations.gmail import access_token

    token = access_token(mailbox)
    with httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=30.0) as client:
        response = client.get(f"{_API}/messages/{gmail_id}", params={"format": "minimal"})
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return set(response.json().get("labelIds") or ())


def main(argv: list[str] | None = None) -> int:
    from src.common.tls import use_os_trust_store
    from src.db.session import SessionLocal

    # 사내망은 TLS 를 가로챕니다 — 이 줄이 없으면 지메일 조회가 전부 인증서 오류로 떨어집니다.
    use_os_trust_store()
    apply = "--apply" in (sys.argv[1:] if argv is None else argv)
    with SessionLocal() as session:
        report = review(session, _gmail_labels, apply=apply)
    # 출력은 ASCII 기호만 — 윈도 콘솔(cp949)이 — 를 못 쓴다.
    labels = {"draft": "초안" + (": 지웠습니다" if apply else ": 지울 것 (--apply)"),
              "missing": "지메일에 없음(404): 사람이 확인", "kept": "진짜 메일: 그대로",
              "unreadable": "확인 못 함(사서함 끊김 등): 그대로"}
    for key, label in labels.items():
        print(f"{label}: {len(report[key])}건 {report[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
