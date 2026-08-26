"""허브스팟 연락처 레코드가 티켓 세부 내역 오른쪽에 앉는 규칙.

**읽기는 우리 행에서, 쓰기는 허브스팟으로** (0094). 예전에는 읽기도 저쪽이었고, 그래서
티켓을 열 때마다 외부 왕복이 하나씩 났습니다.

고정하는 것 넷:

1. **읽기는 네트워크에 안 닿는다.** 화면 값의 출처는 `customer_profiles` 와 `contacts` 이고,
   저쪽에서 값이 들어오는 문은 `agents/contact_sync` 의 셋입니다(웹훅 · 10분 스윕 · 수동).
2. **빈 값도 줄을 만든다.** 허브스팟 사이드바가 `--` 를 그리는 자리를 우리가 숨기면, 같은
   레코드인데 줄 수가 다른 화면이 됩니다.
3. **쓸 때는 속성을 라벨로 찾는다.** 운영자가 아는 것은 허브스팟 화면의 라벨(`user seq`)이고
   내부 이름(`user_seq_c`)은 포털마다 다릅니다. **쓰기의 울타리가 `RECORD_FIELDS` 입니다** —
   화면이 보낸 이름을 그대로 썼다면 콘솔에 닿은 누구든 남의 속성을 덮어쓸 수 있었습니다.
4. **`fetch_record_groups` 는 절대 안 터진다.** 이 패널 하나 때문에 티켓 세부 내역이 안
   열리면 답을 못 씁니다.

그물은 아무 데도 안 닿습니다 — 순수 함수를 직접 부르거나, 요청 함수를 갈아 끼웁니다.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.integrations import hubspot_record as hr

# 운영자가 보낸 포털 화면 그대로: 내부 이름은 제각각이고 라벨이 사람이 아는 말입니다.
LABELS = {
    "email": "Email",
    "firstname": "Full Name",
    "company": "Company Name",
    "phone": "Phone Number",
    "hubspot_owner_id": "Contact owner",
    "plan": "Plan",
    "hs_ip_country": "IP Country",
    "lead_scoring_c": "[Lead] Scoring",
    "user_seq_c": "user seq",
    "space_seq_c": "space seq",
    "plan_tier_c": "plan tier",
    "plan_seq_c": "plan seq",
}


@pytest.fixture
def live_writes(monkeypatch):
    """쓰기 검사만 안전 모드를 엽니다.

    `tests/conftest.py` 가 pytest 를 SAFE 로 고정하는데(그래야 개발자 `.env` 로 진짜 티켓이
    움직이지 않습니다), 그러면 `update_record_fields` 가 첫 줄에서 막혀 울타리 자체를 못
    봅니다. 막히는 것은 `tests/test_safe_mode.py` 가 따로 고정합니다.
    """
    from src.common import safe_mode

    monkeypatch.setattr(safe_mode, "guard_external_write", lambda _label: None)
    monkeypatch.setattr(hr, "guard_external_write", lambda _label: None)


@pytest.fixture(autouse=True)
def _no_catalog_cache_between_tests():
    hr._property_labels.cache_clear()
    yield
    hr._property_labels.cache_clear()


def test_properties_are_found_by_their_hubspot_label():
    """`user_seq_c` 를 코드가 알 리 없습니다. 아는 것은 라벨 `user seq` 뿐입니다."""
    assert hr.resolve_property_names(LABELS) == {
        "plan": "plan",
        "plan_tier": "plan_tier_c",
        "user_seq": "user_seq_c",
        "space_seq": "space_seq_c",
        "plan_seq": "plan_seq_c",
        "ip_country": "hs_ip_country",
        "phone": "phone",
    }


def test_fields_the_operator_did_not_ask_for_are_not_fetched():
    """같은 그룹에 서 있어도 표에 없으면 안 가져옵니다. 회사 이름은 특히 그렇습니다 —
    연락처 정보 카드에 이미 **고칠 수 있는** 회사 칸이 있고, 옆에 읽기 전용 사본을 세우면
    둘 중 어느 것이 진짜인지 화면만 봐서는 알 수 없습니다."""
    fetched = set(hr.resolve_property_names(LABELS).values())

    assert "company" not in fetched
    assert "hubspot_owner_id" not in fetched
    assert "lead_scoring_c" not in fetched


def test_the_screen_label_is_not_the_search_key():
    """화면에 적는 말과 허브스팟에서 찾는 말은 다릅니다.

    운영자가 「플랜 (Plan)」이라고 읽고 싶어 해도 속성은 여전히 라벨 `Plan` 으로 찾고, 폼과
    API 는 또 다른 안정된 키 `plan` 으로 주고받습니다. 셋을 한 칸으로 합치면 화면 글자를
    다듬는 순간 조회가 끊기거나 저장이 끊깁니다.
    """
    assert hr.resolve_property_names({"plan": "Plan"}) == {"plan": "plan"}
    field = next(f for f in hr.RECORD_FIELDS if f.key == "plan")
    assert (field.label, field.candidates) == ("플랜 (Plan)", ("plan",))


def test_the_plan_card_is_drawn_in_the_order_the_operator_set():
    assert [f.label for f in hr.RECORD_FIELDS if f.group == "plan"] == [
        "플랜 (Plan)", "플랜 티어 (plan tier)", "user seq", "space seq", "plan seq",
    ]


def test_the_label_beats_a_retired_property_that_shares_the_name():
    """은퇴한 `user_seq` 와 현역 `user_seq_c`(라벨 `user seq`)가 같이 있는 포털.

    이름을 먼저 보면 빈 옛 속성이 이기고, 화면에는 「값 없음」으로 보입니다 — 값은 옆 속성에
    멀쩡히 있는데. 그래서 라벨 색인을 먼저 봅니다.
    """
    portal = dict(LABELS, user_seq="user seq (사용 안 함)")
    assert hr.resolve_property_names(portal)["user_seq"] == "user_seq_c"


def test_a_renamed_label_still_matches_by_internal_name():
    """라벨이 우리가 모르는 말로 바뀌어도 내부 이름이 그대로면 계속 잡힙니다."""
    assert hr.resolve_property_names({"plan_tier": "요금제 등급"}) == {"plan_tier": "plan_tier"}


def test_punctuation_and_casing_do_not_matter():
    assert hr._norm("User Seq.") == hr._norm("user_seq") == hr._norm("userSeq")
    assert hr._norm("IP-COUNTRY") == hr._norm("ip country")


def test_the_record_becomes_the_card_and_the_fields_become_its_rows():
    """운영자가 보낸 그 연락처 그대로 — 플랜은 비어 있고 IP·전화번호는 차 있습니다."""
    groups = hr.build_groups(
        {
            "plan": "",
            "user_seq_c": None,
            "hs_ip_country": "south korea",
            "phone": "01043391407",
        },
        hr.resolve_property_names(LABELS),
    )

    assert [group["key"] for group in groups] == ["plan", "contact"]
    assert groups[0]["title"] == "플랜 정보"
    # 운영자 표의 순서 그대로이고, 빈 값도 줄로 섭니다 — 허브스팟 사이드바의 `--` 자리.
    assert [row["label"] for row in groups[0]["rows"]] == [
        "플랜 (Plan)", "플랜 티어 (plan tier)", "user seq", "space seq", "plan seq",
    ]
    assert all(row["found"] and row["value"] is None for row in groups[0]["rows"])

    assert groups[1]["title"] == "연락처 정보"
    assert [(row["label"], row["value"]) for row in groups[1]["rows"]] == [
        ("국가 (IP Country)", "south korea"),
        ("전화번호", "01043391407"),
    ]
    # 플랜만 고칠 수 있습니다. 국가는 허브스팟이 IP 로 뽑는 값이고, 전화번호는 연락처 정보
    # 카드의 다른 칸들과 함께 다뤄야 합니다 — 그래서 그 카드에는 연필이 안 붙습니다.
    assert groups[0]["editable"] is True
    assert groups[1]["editable"] is False


def test_a_missing_property_says_so_instead_of_looking_empty():
    """라벨이 한국어인 포털. 정규화로는 못 잡고, 잡은 척해서도 안 됩니다 —
    운영자가 진짜 라벨을 알려줄 수 있어야 합니다."""
    korean_portal = {"plan_c": "플랜", "user_seq_c": "유저 시퀀스", "phone": "Phone Number"}
    groups = hr.build_groups({"phone": "01000000000"}, hr.resolve_property_names(korean_portal))

    plan_rows = {row["key"]: row for row in groups[0]["rows"]}
    assert plan_rows["plan"]["found"] is False
    assert plan_rows["plan"]["value"] is None
    assert plan_rows["user_seq"]["found"] is False
    # 못 찾은 필드는 쓸 수도 없습니다 — 연필만 달아 두면 저장이 아무 일도 안 하고 성공한
    # 척합니다.
    assert plan_rows["plan"]["editable"] is False
    assert groups[0]["editable"] is False
    # 찾은 것은 찾은 대로 섭니다 — 하나 못 찾았다고 카드가 통째로 죽지 않습니다.
    contact_rows = {row["key"]: row for row in groups[1]["rows"]}
    assert contact_rows["phone"]["value"] == "01000000000"
    assert contact_rows["ip_country"]["found"] is False


def _raise(exc):
    def _fake(*_args, **_kwargs):
        raise exc

    return _fake


def _forbidden() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.hubapi.com/x")
    return httpx.HTTPStatusError("403", request=request, response=httpx.Response(403, request=request))


class _Row:
    def __init__(self, **values):
        for key, value in values.items():
            setattr(self, key, value)


class _Session:
    """`SessionLocal()` 자리. 두 표를 흉내 냅니다 — 연락처와 프로필."""

    def __init__(self, contact=None, profile=None):
        self._rows = {"Contact": contact, "CustomerProfile": profile}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, model, _id):
        return self._rows.get(model.__name__)


def _patch_db(monkeypatch, contact, profile):
    from src.db import session as db_session

    monkeypatch.setattr(db_session, "SessionLocal", lambda: _Session(contact, profile))


def test_the_panel_reads_our_own_rows_and_never_touches_hubspot(monkeypatch):
    """**읽기는 네트워크에 안 닿습니다** (0094).

    예전에는 티켓을 열 때마다 허브스팟 연락처를 한 번씩 읽었습니다. 화면 값이 언제나 지금
    저쪽 값이었지만, 답을 읽는 일이 매번 외부 왕복을 기다렸습니다.
    """
    monkeypatch.setattr(hr, "_sync_request_with_retries", _raise(RuntimeError("네트워크에 닿았다")))
    _patch_db(
        monkeypatch,
        _Row(ip_country="south korea", phone="01043391407"),
        _Row(current_plan="Enterprise", plan_tier=None, user_seq="184920",
             space_seq=None, plan_seq=None, last_synced_at=None),
    )

    result = hr.fetch_record_groups(42)

    assert result["error"] is None
    assert [group["title"] for group in result["groups"]] == ["플랜 정보", "연락처 정보"]
    assert result["groups"][0]["rows"][0] == {
        "key": "plan", "label": "플랜 (Plan)", "value": "Enterprise",
        "found": True, "editable": True,
    }
    # 빈 값도 줄을 만듭니다 — 허브스팟 사이드바가 `--` 를 그리는 그 자리입니다.
    assert [row["value"] for row in result["groups"][0]["rows"]] == [
        "Enterprise", None, "184920", None, None,
    ]
    assert result["groups"][1]["rows"][0]["value"] == "south korea"


def test_the_panel_never_raises_whatever_the_database_does(monkeypatch):
    """티켓 세부 내역이 이 패널 때문에 안 열리면 안 됩니다."""
    from src.db import session as db_session

    def _boom():
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(db_session, "SessionLocal", _boom)

    result = hr.fetch_record_groups(42)

    assert result["groups"] == []
    assert "플랜 정보를 읽지 못했습니다." in result["error"]


def test_a_contact_we_have_never_synced_is_an_empty_panel_not_an_error(monkeypatch):
    """프로필 행이 아직 없어도 카드는 섭니다 — 줄 수가 고객마다 달라지면 안 됩니다."""
    _patch_db(monkeypatch, _Row(ip_country=None, phone=None), None)

    result = hr.fetch_record_groups(42)

    assert result["error"] is None
    assert [group["title"] for group in result["groups"]] == ["플랜 정보", "연락처 정보"]
    assert all(row["value"] is None for group in result["groups"] for row in group["rows"])


def test_only_the_plan_fields_can_be_written(monkeypatch, live_writes):
    """**쓰기의 울타리는 `RECORD_FIELDS` 다.**

    화면이 보내는 것은 우리 키(`user_seq`)이고 허브스팟 속성 이름은 서버가 카탈로그에서 다시
    찾습니다. 브라우저가 보낸 이름을 그대로 썼다면, 콘솔에 닿은 누구든 `email` 이나
    `lifecyclestage` 를 덮어쓸 수 있었습니다. 읽기 전용 필드(국가·전화번호)도 안 나갑니다.
    """
    sent: dict = {}

    def _fake(_client, method, url, **kwargs):
        request = httpx.Request(method, url)
        if url.endswith("/crm/v3/properties/contacts"):
            body = {"results": [{"name": n, "label": label} for n, label in LABELS.items()]}
            return httpx.Response(200, json=body, request=request)
        sent["method"] = method
        sent["properties"] = kwargs["json"]["properties"]
        return httpx.Response(200, json={"id": "42"}, request=request)

    monkeypatch.setattr(hr, "_sync_request_with_retries", _fake)

    hr.update_record_fields("42", {
        "user_seq": "184920",
        "plan": "Pro",
        "ip_country": "japan",        # 읽기 전용 — 허브스팟이 IP 로 뽑는 값입니다
        "phone": "01000000000",       # 읽기 전용
        "email": "attacker@example.com",   # 목록에 아예 없는 속성
        "lifecyclestage": "customer",      # 〃
    })

    assert sent["method"] == "PATCH"
    assert sent["properties"] == {"user_seq_c": "184920", "plan": "Pro"}


def test_writing_nothing_writes_nothing(monkeypatch, live_writes):
    """쓸 것이 하나도 없으면 요청을 아예 내지 않습니다 — 빈 PATCH 도 수정 시각을 바꿉니다."""
    calls: list[str] = []

    def _fake(_client, method, url, **_kw):
        calls.append(f"{method} {url}")
        request = httpx.Request(method, url)
        if url.endswith("/crm/v3/properties/contacts"):
            body = {"results": [{"name": n, "label": label} for n, label in LABELS.items()]}
            return httpx.Response(200, json=body, request=request)
        raise AssertionError("빈 저장인데 PATCH 가 나갔습니다")

    monkeypatch.setattr(hr, "_sync_request_with_retries", _fake)

    hr.update_record_fields("42", {"email": "nope@example.com"})

    assert not any(call.startswith("PATCH") for call in calls)


def test_an_empty_box_clears_the_value(monkeypatch, live_writes):
    """빈 칸은 「모르겠다」가 아니라 「지워라」입니다 — 잘못 들어간 값을 되돌릴 길이
    있어야 합니다."""
    sent: dict = {}

    def _fake(_client, method, url, **kwargs):
        request = httpx.Request(method, url)
        if url.endswith("/crm/v3/properties/contacts"):
            body = {"results": [{"name": n, "label": label} for n, label in LABELS.items()]}
            return httpx.Response(200, json=body, request=request)
        sent.update(kwargs["json"]["properties"])
        return httpx.Response(200, json={"id": "42"}, request=request)

    monkeypatch.setattr(hr, "_sync_request_with_retries", _fake)

    hr.update_record_fields("42", {"plan": "  "})

    assert sent == {"plan": ""}


def test_a_contact_without_a_hubspot_id_still_gets_the_panel():
    """워크북에서 온 행과 손으로 만든 행도 자기 칸을 갖습니다 (0094).

    예전에는 이 패널이 허브스팟 연락처 ID 로만 조회할 수 있어서, ID 가 없는 고객에게는 카드
    자체가 안 떴습니다. 이제 읽는 곳이 우리 행이라 ID 는 상관없습니다 — 비어 있으면 비어
    있는 채로 그려지고, 사람이 그 자리에 채워 넣을 수 있습니다.

    없는 연락처도 200 입니다. 이 패널의 계약은 「티켓 세부 내역을 절대 못 열게 하지 않는다」
    이고, 그러려면 실패가 상태 코드가 아니라 `error` 문자열로 와야 합니다.
    """
    from src.db.models import Contact
    from src.db.session import SessionLocal

    with SessionLocal() as session:
        contact = Contact(
            normalized_email="no-hubspot@example.com",
            email="no-hubspot@example.com",
            full_name="No HubSpot",
        )
        session.add(contact)
        session.commit()
        contact_id = contact.id

    with TestClient(app) as client:
        response = client.get(f"/api/ui/contacts/{contact_id}/hubspot-record")
        missing = client.get("/api/ui/contacts/999999/hubspot-record")

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] is None
    assert [group["title"] for group in payload["groups"]] == ["플랜 정보", "연락처 정보"]

    assert missing.status_code == 200
    assert missing.json()["groups"] == []
    assert "연락처를 찾을 수 없습니다." in missing.json()["error"]
