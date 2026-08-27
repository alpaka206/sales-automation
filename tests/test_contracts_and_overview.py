"""화면 없는 통로 둘은 없다 — 「전체 대시보드」와 「계약 장부」.

이 파일은 그 두 화면의 산술을 지키던 곳이었습니다. 둘 다 없어졌습니다:

- **전체 대시보드** — 각 화면의 숫자를 모아 보여 주기만 하는 자리라 아무도 안 봤습니다
  (2026-08-13 운영자 지시).
- **계약 장부** (`GET /api/ui/contracts`) — 「수주 고객」이 `clients`/`client_contracts` 로
  옮겨 가면서 **화면만 갈아탔고**, 이 엔드포인트와 그 뒤의 `_contract_rows` ·
  `_contract_summary` 가 `contract_records` 를 읽은 채로 남았습니다. 부르는 화면은 하나도
  없었습니다 (2026-08-27 운영자 지시).

**`contract_records` 표는 그대로입니다.** 고객 상세의 「계약 · 결제」 폼이 여전히 그 표에
쓰고, 워크북 동기화가 읽습니다 — 그 경로는 ``tests/test_customer_ops.py`` 가 지킵니다.
지운 것은 표가 아니라 아무도 안 부르는 통로입니다.

되살릴 일이 생기면 git 이력에서 그대로 꺼내세요. 다만 되살리기 전에 **어느 화면이 그것을
읽는지** 부터 정하십시오 — 그게 없어서 이렇게 됐습니다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app


def test_the_screenless_endpoints_are_gone():
    with TestClient(app) as client:
        assert client.get("/api/ui/contracts").status_code == 404
        assert client.get("/api/ui/overview").status_code == 404


def test_nothing_builds_those_numbers_any_more():
    """화면만 지우면 매 요청마다 아무도 안 읽는 집계가 계속 돕니다. 빌더까지 같이 갑니다."""
    from src.api.routes import customer_ops, dashboard

    assert not hasattr(dashboard, "_overview_context")
    for name in ("_contract_rows", "_contract_summary", "CONTRACT_STATUS_LABELS"):
        assert not hasattr(customer_ops, name), name


def test_the_status_vocabulary_the_write_route_accepts_survives():
    """계약 폼은 남았고, 그 폼이 받는 값의 목록도 남아야 합니다."""
    from src.api.routes.customer_ops import CONTRACT_STATUSES

    assert CONTRACT_STATUSES == {"draft", "sent", "contracted", "active", "expired", "cancelled"}
