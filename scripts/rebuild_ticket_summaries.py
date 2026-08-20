"""티켓 요약을 **실제로 오간 것**으로 다시 만듭니다 — 한 번 돌리는 스크립트.

0081 이 초안으로 쓴 요약을 비웠습니다. 이 스크립트는 그 자리를 우리 DB 의 메시지와
허브스팟에서 끌어온 **그 티켓의** 메일로 채웁니다. 하는 일은 전부
`agents.summaries.rebuild_summary` 에 있고 — 콘솔의
`POST /internal/conversations/rebuild-summaries` 도 같은 함수를 부릅니다 — 여기서는
DB 에 있는 대화를 하나씩 넘겨 줄 뿐입니다.

허브스팟에 다시 묻지 않습니다: 이미 가지고 온 것으로 돕니다.

    .venv\Scripts\python.exe -m scripts.rebuild_ticket_summaries [--dry-run]
"""

from __future__ import annotations

import os
import sys

# 가져오기·다시 쓰기만 합니다. 아래 import 보다 먼저 꺼야 합니다 — 설정은 모듈을 읽을 때
# 한 번 굳습니다.
os.environ["LIVE_EXTERNAL_WRITES"] = "false"

from src.common.tls import use_os_trust_store  # noqa: E402

# 사내망은 TLS 를 가로챕니다. 이 한 줄이 없으면 모델 호출이 전부
# CERTIFICATE_VERIFY_FAILED 로 떨어지고, 줄이 하나도 안 만들어집니다.
use_os_trust_store()

from src.agents.summaries import rebuild_summary  # noqa: E402
from src.db.models import Conversation  # noqa: E402
from src.db.session import SessionLocal  # noqa: E402


def main() -> int:
    dry = "--dry-run" in sys.argv
    with SessionLocal() as session:
        conv_ids = [row[0] for row in session.query(Conversation.id).order_by(Conversation.id).all()]
        touched = lines = 0
        for conv_id in conv_ids:
            if dry:
                continue
            written = rebuild_summary(session, conv_id)
            if written:
                touched += 1
                lines += written
                session.commit()
                print(f"conv {conv_id}: {written}줄", flush=True)
        print(f"{'(dry-run) ' if dry else ''}대화 {len(conv_ids)}건 중 {touched}건 · 줄 {lines}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
