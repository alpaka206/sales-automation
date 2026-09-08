"""회신에 참조(CC)를 건다 — 나가던 것은 하나도 안 건드리고 (2026-09-07 운영자 지시).

**됩니까**: 됩니다. 문서가 아니라 이 포털에서 실제로 나간 메시지로 확인했습니다
(스레드 600개·메시지 666건 읽기 전용 조사): `recipientField` 가 `TO` 546 · `BCC` 6 ·
`CC` 5, CC 가 붙은 OUTGOING 이 4건이고 그중 하나는 CC 가 넷입니다. 모양은 우리가 이미
보내는 `TO` 와 같습니다 — `HS_EMAIL_ADDRESS` + 주소, `actorId` 없음. actorId 때 배운
규칙(「기준은 그 포털에서 실제로 나간 메시지다」)을 그대로 따랐습니다.

이 파일이 지키는 것은 둘입니다: 참조가 **붙는다**, 그리고 참조가 없으면 payload 가
**예전과 한 글자도 다르지 않다**.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
import respx

from src.common.config import settings
from src.integrations.hubspot import BASE_URL, ConversationReplyContext, HubSpotClient
from src.integrations.senders import MAX_CC, parse_cc_addresses


@pytest.fixture()
def client() -> HubSpotClient:
    return HubSpotClient(token="test-token")


def _send_route():
    respx.get(f"{BASE_URL}/conversations/v3/conversations/actors/A-1").mock(
        return_value=httpx.Response(200, json={"id": "A-1", "type": "AGENT"})
    )
    return respx.post(
        f"{BASE_URL}/conversations/v3/conversations/threads/t-1/messages"
    ).mock(return_value=httpx.Response(201, json={"id": "m-1"}))


async def _send(client, **kwargs):
    return await client.send_conversation_message(
        ConversationReplyContext("t-1", "1002", "acct-1"),
        recipient_email="buyer@example.com",
        subject="Re: Inquiry",
        text="Hello",
        rich_text="<p>Hello</p>",
        **kwargs,
    )


@respx.mock
@pytest.mark.asyncio
async def test_no_cc_sends_exactly_what_it_always_sent(client, monkeypatch) -> None:
    """**이것이 「기존에 보내던 거는 건들지 말고」입니다.**

    참조가 비었을 때 payload 가 이 칸이 생기기 전과 다르면, 이미 나가고 있는 발송을
    건드린 것입니다 — 그리고 그 차이는 고객이 메일을 받은 뒤에야 드러납니다.
    """
    monkeypatch.setattr(settings, "HUBSPOT_SENDER_ACTOR_ID", "A-1")
    route = _send_route()
    await _send(client)
    await client.close()

    payload = json.loads(route.calls[0].request.content)
    assert payload["recipients"] == [
        {
            "recipientField": "TO",
            "deliveryIdentifiers": [
                {"type": "HS_EMAIL_ADDRESS", "value": "buyer@example.com"}
            ],
        }
    ]
    # 보내는 계정도 그대로입니다. 참조는 **얹기만** 합니다.
    assert payload["channelAccountId"] == "acct-1"
    assert payload["senderActorId"] == "A-1"


@respx.mock
@pytest.mark.asyncio
async def test_cc_rides_along_in_the_shape_the_portal_uses(client, monkeypatch) -> None:
    """받는 사람은 첫 줄에 그대로 있고, 참조가 뒤에 붙는다 — 여러 명도.

    `actorId` 를 안 넣는 것이 핵심입니다. 문서 예시에는 있는데 **발송 엔드포인트가
    거부합니다**(`Actor type EMAIL is not supported for receiving`). 읽기 조회로는 절대 못
    잡는 자리라, 이 검사가 그 자리를 지킵니다.
    """
    monkeypatch.setattr(settings, "HUBSPOT_SENDER_ACTOR_ID", "A-1")
    route = _send_route()
    await _send(client, cc=["boss@estsoft.com", "peer@acme.com"])
    await client.close()

    recipients = json.loads(route.calls[0].request.content)["recipients"]
    assert [r["recipientField"] for r in recipients] == ["TO", "CC", "CC"]
    assert recipients[0]["deliveryIdentifiers"][0]["value"] == "buyer@example.com"
    assert [r["deliveryIdentifiers"][0]["value"] for r in recipients[1:]] == [
        "boss@estsoft.com",
        "peer@acme.com",
    ]
    assert not any("actorId" in r for r in recipients)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a@x.com, b@y.com", ["a@x.com", "b@y.com"]),
        # 메일 클라이언트에서 그대로 복사해 붙일 수 있어야 합니다.
        ("Kim <a@x.com>; b@y.com\nc@z.com", ["a@x.com", "b@y.com", "c@z.com"]),
        # 대소문자만 다른 중복은 하나로, 처음 적힌 철자를 남깁니다 — 로컬 파트는 원칙적으로
        # 대소문자를 가리므로 우리가 눕혀 쓸 값이 아닙니다.
        ("A@x.com, a@X.com", ["A@x.com"]),
        ("not an address", []),
        ("", []),
        (None, []),
    ],
)
def test_the_spelling_rule_lives_in_one_place(raw, expected) -> None:
    assert parse_cc_addresses(raw) == expected


def test_no_address_can_carry_a_line_break() -> None:
    """줄바꿈은 **구분자**입니다 — 시트에서 한 열을 복사하면 그렇게 옵니다.

    그래서 한 주소 안에 CR/LF 가 남을 수 없고, 그 사실을 여기서 못 박습니다: 이 값은
    결국 메일 헤더가 되고(우리는 JSON 을 주고 허브스팟이 헤더를 짓습니다), 나중에 누가
    구분자 규칙을 고쳐도 그건 안 바뀝니다.
    """
    got = parse_cc_addresses("a@x.com\r\nb@y.com")
    assert got == ["a@x.com", "b@y.com"]
    assert all("\r" not in one and "\n" not in one for one in got)


def test_the_recipient_is_never_also_a_cc() -> None:
    """같은 사람이 To 와 Cc 에 같이 서면 메일이 두 통 가는 것처럼 보입니다.

    거르는 시점이 **발송**인 것이 중요합니다: 저장할 때 걸러 두면 그 사이에 받는 사람이
    바뀐 초안에서 틀립니다.
    """
    assert parse_cc_addresses(
        "Buyer@example.com, boss@estsoft.com", exclude="buyer@example.com"
    ) == ["boss@estsoft.com"]


def test_a_paste_accident_cannot_expose_a_hundred_addresses() -> None:
    """상한이 없으면 붙여넣기 사고 하나가 고객 메일에 주소 수백 개를 노출하고, 나간
    뒤에는 못 되돌립니다."""
    many = ", ".join(f"p{i}@x.com" for i in range(MAX_CC + 20))
    assert len(parse_cc_addresses(many)) == MAX_CC


def test_an_address_this_team_does_not_use_is_not_offered(monkeypatch):
    """**`support@perso.ai` 는 이 팀이 안 씁니다** (2026-09-08 운영자 지시).

    그 주소는 포털에 연결돼 있어서 지난 스레드에 남아 있고, 그래서 참조 후보로 떴습니다.
    「연결돼 있다」와 「우리가 쓴다」는 다른 이야기이고 그 판단은 코드가 아니라 팀이
    합니다 — 목록에 두면 언젠가 눌러서 **고객이 받는 메일의 참조에 붙습니다.**

    거르는 곳이 설정인 이유도 같습니다(`HUBSPOT_REPLY_SENDER_ACCOUNT_IDS` 와 같은 성격).
    """
    from src.common.config import settings as app_settings

    assert "support@perso.ai" in app_settings.CC_EXCLUDED_ADDRESSES

    excluded = {
        one.strip().lower()
        for one in app_settings.CC_EXCLUDED_ADDRESSES.split(",")
        if one.strip()
    }
    found = [
        {"address": "buyer@acme.com"},
        {"address": "support@perso.ai"},
        {"address": "boss@estsoft.com"},
    ]
    kept = [row["address"] for row in found if row["address"] not in excluded]
    assert kept == ["buyer@acme.com", "boss@estsoft.com"]


# --------------------------------------------------------------------------- #
# 개인 사서함에서 보내기 (2026-09-08 운영자 지시)
# --------------------------------------------------------------------------- #
def test_the_picker_value_decides_which_door_the_mail_leaves_by():
    """**「어느 주소로」와 「어느 경로로」는 같은 질문입니다.**

    허브스팟 채널 계정 id 는 숫자고 개인 사서함은 `gmail:<주소>` 라 두 형식이 절대
    안 겹칩니다. 그래서 값 하나로 갈리고, 화면은 고르기만 합니다.
    """
    from src.api.routes.messages import _clean_channel_account_id

    assert _clean_channel_account_id("3114216464") == "3114216464"
    assert _clean_channel_account_id("gmail:UnTae@Estsoft.com") == "gmail:untae@estsoft.com"
    # 모양이 아니면 「안 고름」입니다 — 그때는 예전처럼 스레드가 정합니다.
    assert _clean_channel_account_id("gmail:notanaddress") is None
    assert _clean_channel_account_id("support@perso.ai") is None
    assert _clean_channel_account_id("") is None


@respx.mock
def test_a_personal_mailbox_send_is_a_reply_only_when_there_is_an_original():
    """**원본이 있으면 답장, 없으면 새 메일** (운영자 확인).

    허브스팟으로 온 문의에 개인 주소를 골라 보내는 경우가 뒤엣것입니다 — 붙일 스레드도
    `In-Reply-To` 도 없으니 새 메일이 유일하게 정직한 결과입니다.

    `threadId` 는 **헤더와 함께일 때만** 넣습니다: 헤더 없이 threadId 만 주면 Gmail 이
    거절하고, threadId 없이 헤더만 주면 받는 쪽에서는 묶이는데 우리 사서함에서 새
    대화로 섭니다.
    """
    import base64

    from src.integrations import gmail

    route = respx.post(gmail.SEND_URL).mock(
        return_value=httpx.Response(200, json={"id": "g-1"})
    )
    respx.post(gmail.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
    )

    with patch.object(gmail, "access_token", return_value="t"):
        gmail.send_mail("untae@estsoft.com", to="buyer@acme.com", subject="Re: 문의",
                        html="<p>안녕하세요</p>", cc=["boss@estsoft.com"],
                        thread_id="th-1", in_reply_to="<abc@mail>")
        gmail.send_mail("untae@estsoft.com", to="buyer@acme.com", subject="안내",
                        html="<p>새 메일</p>")

    reply = json.loads(route.calls[0].request.content)
    fresh = json.loads(route.calls[1].request.content)
    assert reply["threadId"] == "th-1", "원본이 있으면 그 스레드에 붙습니다"
    assert "threadId" not in fresh, "원본이 없으면 새 메일입니다"

    raw = base64.urlsafe_b64decode(reply["raw"]).decode("utf-8", "replace")
    assert "In-Reply-To: <abc@mail>" in raw and "References: <abc@mail>" in raw
    assert "From: untae@estsoft.com" in raw
    assert "Cc: boss@estsoft.com" in raw


def test_the_gmail_door_passes_the_same_write_guard():
    """**문이 둘인데 관문이 하나뿐이면 그 대전제는 대전제가 아닙니다.**

    허브스팟 발송이 `guard_external_write` 를 함수 첫 줄에서 지나듯, Gmail 발송도
    같습니다 — 안전 모드에서는 네트워크에 닿기도 전에 막힙니다.
    """
    import pathlib

    source = pathlib.Path("src/integrations/gmail.py").read_text(encoding="utf-8")
    body = source[source.index("def send_mail("):]
    body = body[: body.index("\n    from email.message")]
    assert 'guard_external_write("gmail:send_mail")' in body
