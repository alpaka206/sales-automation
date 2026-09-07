"""회사 주소는 연락처 스윕이 채우고, 대기열은 NULL 그 자체다 (0111, 2026-09-07 운영자 지시).

운영자가 허브스팟에서 본 「Company Record」의 `Website URL` 은 **연락처의 속성이
아닙니다** — 연결된 회사의 값입니다(실측: 연락처 `website` 100건 중 0건, 회사 `website`
100건 중 9건). 그래서 이 값이 화면에 서려면 연결을 한 번 더 읽어야 하는데, 그리는 자리인
티켓 카드는 0094 가 일부러 네트워크를 끊은 자리입니다. 이 파일이 그 「어디서 채우는가」를
고정합니다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.agents import contact_sync
from src.db.base import Base
from src.db.models import Contact


class _Client:
    """물어본 사람은 전부 답에 담아 돌려줍니다 — 진짜 클라이언트의 계약입니다."""

    def __init__(self, sites: dict[str, str]):
        self.sites = sites
        self.asked: list[list[str]] = []

    def company_websites_sync(self, contact_ids):
        self.asked.append(list(contact_ids))
        return {cid: self.sites.get(cid, "") for cid in contact_ids}


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(contact_sync, "SessionLocal", factory)
    with factory() as session:
        session.add_all([
            Contact(normalized_email="a@acme.com", email="a@acme.com",
                    full_name="A", hubspot_contact_id="101"),
            # 회사가 없거나 회사에 주소가 없는 사람 — 실측상 이쪽이 다수입니다.
            Contact(normalized_email="b@none.com", email="b@none.com",
                    full_name="B", hubspot_contact_id="102"),
            # 허브스팟 id 가 없으면 물어볼 방법이 없습니다.
            Contact(normalized_email="c@local.com", email="c@local.com", full_name="C"),
        ])
        session.commit()
    return factory


def test_the_sweep_fills_it_and_never_asks_twice(db):
    """**대기열은 `website IS NULL` 입니다.** 답이 없는 사람에게도 빈 문자열을 적는 이유가
    이것입니다 — NULL 로 두면 회사 주소가 없는 다수가 대기열 앞을 영영 막고, 뒤에 있는
    사람은 한 번도 차례가 오지 않습니다."""
    client = _Client({"101": "acme.com"})

    assert contact_sync.fill_missing_websites(client) == 1
    assert client.asked == [["101", "102"]], "허브스팟 id 가 없는 사람은 안 묻습니다"

    with db() as session:
        rows = {c.normalized_email: c.website for c in session.query(Contact).all()}
    # 스킴 없이 적힌 값은 저장할 때 한 번 다듬습니다 — 그대로 두면 링크가 콘솔 안의
    # 상대 경로가 됩니다.
    assert rows["a@acme.com"] == "https://acme.com"
    assert rows["b@none.com"] == "", "물어봤고 없었다"
    assert rows["c@local.com"] is None

    client.asked.clear()
    assert contact_sync.fill_missing_websites(client) == 0
    assert client.asked == [], "한 번 물어본 사람은 다시 안 묻습니다"


def test_a_failed_lookup_leaves_the_queue_alone(db):
    """조회가 터졌을 때 빈 문자열을 적으면 그 사람들은 「물어봤고 없었다」가 되어 영영
    비어 있습니다. 그건 스윕 한 회차의 사고이지 그 고객의 사실이 아닙니다."""

    class _Broken:
        def company_websites_sync(self, contact_ids):
            raise RuntimeError("boom")

    assert contact_sync.fill_missing_websites(_Broken()) == 0
    with db() as session:
        assert session.query(Contact).filter(Contact.website.is_(None)).count() == 3


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://perso.ai", "https://perso.ai"),
        ("  perso.ai/ko  ", "https://perso.ai/ko"),
        ("www.youtube.com/@Banheiristas", "https://www.youtube.com/@Banheiristas"),
        # 화면이 이 값을 `<a href>` 에 그대로 넣습니다. 저쪽에서 온 글자라 스킴을 고릅니다.
        ("javascript:alert(1)", ""),
        ("data:text/html,<script>", ""),
        ("", ""),
    ],
)
def test_the_stored_value_is_safe_to_put_in_an_href(raw, expected):
    assert contact_sync._safe_url(raw) == expected
