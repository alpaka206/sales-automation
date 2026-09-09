"""메일함 연결 — 여러 개, 골라 쓰기, 그리고 토큰이 죽었을 때 (2026-09-07 운영자 지시).

「`perso.ai@estsoft.com`, `untae@estsoft.com` 이런 식으로 여러 개 넣을 거고 추가될 수도
있다」 · 「토큰은 안정적으로 계속 쓸 수 있도록」 · 「이메일을 골라서 쓸 수도 있게」.
"""

from __future__ import annotations

import time
from datetime import timezone

import httpx
import pytest
import respx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.common.config import settings
from src.db.base import Base
from src.db.models import Conversation, MailboxAccount
from src.integrations import gmail


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(gmail, "SessionLocal", factory)
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setattr(settings, "GOOGLE_TOKEN_ENCRYPTION_KEY",
                        "b3BlbnNlc2FtZW9wZW5zZXNhbWVvcGVuc2VzYW1lMTI=")
    return factory


def _connect(factory, email, *, refresh="r-1", expires_at=0):
    with factory() as session:
        session.add(MailboxAccount(
            email=email,
            encrypted_payload=gmail._encrypt(
                {"refresh_token": refresh, "access_token": "old", "expires_at": expires_at}
            ),
        ))
        session.commit()


def test_more_than_one_mailbox_lives_side_by_side(db):
    """**한 줄에 한 사서함**입니다. `integration_credentials` 는 `provider` 가 기본키라
    provider 당 한 줄뿐이고, 그 표를 쓰면 두 번째 사서함이 첫 번째를 덮어씁니다."""
    _connect(db, "perso.ai@estsoft.com")
    _connect(db, "untae@estsoft.com")

    assert [a.email for a in gmail.list_accounts()] == [
        "perso.ai@estsoft.com", "untae@estsoft.com"
    ]


def test_reconnecting_the_same_mailbox_replaces_the_row(db):
    """같은 사서함을 다시 연결하면 **덮어씁니다.**

    줄이 둘이면 어느 토큰이 살아 있는지 화면만 봐서는 모릅니다. 그리고 구글은 계정당
    클라이언트당 refresh token 을 100개까지만 살려 두고 넘으면 오래된 것부터 **경고 없이**
    무효로 만듭니다 — 줄이 쌓이면 그중 다수가 이미 죽은 토큰입니다.
    """
    _connect(db, "untae@estsoft.com", refresh="old")
    gmail._save("untae@estsoft.com", {"refresh_token": "new", "expires_at": 0}, "admin")

    rows = gmail.list_accounts()
    assert len(rows) == 1
    assert gmail._decrypt(rows[0].encrypted_payload)["refresh_token"] == "new"


def test_the_operator_picks_which_mailboxes_are_collected(db):
    """「이메일을 골라서 쓸 수도 있게」 — 끄면 수집에서 빠지되 **연결은 살아 있습니다.**
    다시 켜는 데 재동의가 필요하면 그건 고르기가 아니라 해제입니다."""
    _connect(db, "a@estsoft.com")
    _connect(db, "b@estsoft.com")

    gmail.set_enabled("b@estsoft.com", False)
    assert gmail.enabled_accounts() == ["a@estsoft.com"]
    assert len(gmail.list_accounts()) == 2, "연결은 남습니다"

    gmail.set_enabled("b@estsoft.com", True)
    assert gmail.enabled_accounts() == ["a@estsoft.com", "b@estsoft.com"]


@respx.mock
def test_a_live_token_is_refreshed_and_kept(db):
    """만료가 가까우면 미리 갱신하고 **저장합니다.** 저장 안 하면 3시간마다 갱신 요청이
    한 번씩 더 나가고, 구글이 그걸 셉니다."""
    respx.post(gmail.TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "fresh", "expires_in": 3600})
    )
    _connect(db, "untae@estsoft.com", expires_at=0)

    assert gmail.access_token("untae@estsoft.com") == "fresh"
    with db() as session:
        row = session.get(MailboxAccount, "untae@estsoft.com")
        payload = gmail._decrypt(row.encrypted_payload)
    assert payload["access_token"] == "fresh"
    assert payload["expires_at"] > time.time()
    # 아직 안 만료됐으면 두 번째 호출은 왕복이 없습니다.
    assert gmail.access_token("untae@estsoft.com") == "fresh"
    assert respx.calls.call_count == 1


@respx.mock
def test_a_dead_token_says_so_on_the_row(db):
    """**Gmail 토큰은 비밀번호를 바꾸면 죽습니다** — 다른 스코프에는 없는 조건이라 시트
    연결은 안 겪던 일입니다(구글 문서: "The user changed passwords and the refresh token
    contains Gmail scopes").

    그때 로그만 남기면 운영자는 수집이 멈춘 줄 모릅니다. 행에 적어야 화면이 「재연결
    필요」를 띄우고, **수집 대상에서도 빠집니다** — 죽은 사서함을 3시간마다 두드려 봐야
    같은 실패가 쌓일 뿐입니다.
    """
    respx.post(gmail.TOKEN_URL).mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    _connect(db, "untae@estsoft.com", expires_at=0)

    with pytest.raises(gmail.MailboxTokenError):
        gmail.access_token("untae@estsoft.com")

    with db() as session:
        assert "invalid_grant" in session.get(MailboxAccount, "untae@estsoft.com").last_error
    assert gmail.enabled_accounts() == [], "죽은 사서함은 수집에서 빠집니다"


@respx.mock
def test_a_hiccup_does_not_look_like_a_dead_token(db):
    """5xx·네트워크는 다음 회차에 저절로 낫습니다. 그걸 「재연결 필요」로 적으면 멀쩡한
    연결을 사람이 다시 잇게 만듭니다."""
    respx.post(gmail.TOKEN_URL).mock(return_value=httpx.Response(503))
    _connect(db, "untae@estsoft.com", expires_at=0)

    with pytest.raises(gmail.MailboxTokenError):
        gmail.access_token("untae@estsoft.com")

    with db() as session:
        assert session.get(MailboxAccount, "untae@estsoft.com").last_error is None
    assert gmail.enabled_accounts() == ["untae@estsoft.com"]


def test_the_consent_link_asks_which_account_and_asks_offline(db):
    """**계정을 묻게 합니다.** 관리자가 자기 계정으로 로그인해 둔 브라우저에서 링크를 열면
    구글이 계정을 안 묻고 조용히 그 계정으로 동의해 버립니다 — 「untae 연결」을 눌렀는데
    관리자 사서함이 붙습니다.

    `consent` 도 함께여야 합니다: 이미 동의한 계정에는 **refresh token 이 다시 안 나오고**,
    그러면 `exchange_code` 가 「오프라인 권한이 발급되지 않았습니다」로 끝납니다.
    """
    url = gmail.authorization_url("https://c/cb", "state", login_hint="untae@estsoft.com")

    assert "prompt=select_account+consent" in url
    assert "access_type=offline" in url
    assert "login_hint=untae%40estsoft.com" in url
    # 요청하는 것은 읽기와 발송뿐입니다. `gmail.modify` 는 **남의 사서함을 고칠 수**
    # 있어서 넣지 않습니다 — 개인함 메일을 읽고 그 자리에서 답하는 데 필요 없습니다.
    assert "gmail.readonly" in url and "gmail.send" in url
    assert "gmail.modify" not in url


def test_the_stored_token_is_encrypted_at_rest(db):
    """DB 를 열어 본 사람이 그 사람의 메일을 읽을 수 있으면 안 됩니다."""
    gmail._save("untae@estsoft.com", {"refresh_token": "super-secret"}, None)

    with db() as session:
        stored = session.get(MailboxAccount, "untae@estsoft.com").encrypted_payload
    assert "super-secret" not in stored
    assert gmail._decrypt(stored)["refresh_token"] == "super-secret"


def test_a_mailbox_can_be_added_without_its_owner_logging_in(db, monkeypatch):
    """**본인이 로그인하지 않아도 됩니다** (2026-09-07 운영자 요구: 「운태가 로그인 하지
    않아도 운태의 기록을 불러올 수 있도록」).

    그 길은 도메인 전체 위임 하나뿐입니다 — 서비스 계정이 그 사람을 가장하고, 사람마다
    받던 동의를 Workspace 슈퍼관리자가 한 번 대신 해 둡니다.

    **토큰을 안 담는 것이 이 방식의 표시입니다.** 열 때마다 새로 받으므로 보관할 refresh
    token 이 없고, 그래서 비밀번호 변경으로 죽지도 6개월 미사용으로 만료되지도 않습니다.
    """
    monkeypatch.setattr(settings, "GOOGLE_CREDENTIALS_JSON", '{"type": "service_account"}')
    gmail.add_delegated_mailbox("untae@estsoft.com", "ronald")

    assert gmail.enabled_accounts() == ["untae@estsoft.com"]
    with db() as session:
        payload = gmail._decrypt(
            session.get(MailboxAccount, "untae@estsoft.com").encrypted_payload
        )
    assert "refresh_token" not in payload, "위임 방식은 토큰을 보관하지 않습니다"
    # 주소는 눕혀서 담습니다 — 같은 사서함이 대소문자만 달리 두 줄로 서면 안 됩니다.
    gmail.add_delegated_mailbox("UnTae@Estsoft.com")
    assert len(gmail.list_accounts()) == 1


def test_an_unregistered_delegation_says_which_console_to_open(db, monkeypatch):
    """위임이 등록 안 된 것과 토큰이 죽은 것은 **고치는 사람도 고치는 곳도 다릅니다.**

    앞엣것은 슈퍼관리자가 Admin console 에서 client ID 를 등록할 일이고, 뒤엣것은 본인이
    다시 동의할 일입니다. 「열지 못했습니다」 한 문장으로 뭉개면 둘 중 어느 쪽인지 알 수
    없고, 이건 **읽기로는 못 가립니다** — 토큰을 실제로 받아 봐야 압니다(허브스팟 actorId
    때와 같은 자리).
    """
    from google.oauth2 import service_account

    monkeypatch.setattr(settings, "GOOGLE_CREDENTIALS_JSON", '{"type": "service_account"}')
    gmail.add_delegated_mailbox("untae@estsoft.com")

    def _refuse(*_args, **_kwargs):
        raise RuntimeError("('unauthorized_client: Client is unauthorized', ...)")

    monkeypatch.setattr(
        service_account.Credentials, "from_service_account_info", staticmethod(_refuse)
    )

    with pytest.raises(gmail.MailboxTokenError):
        gmail.access_token("untae@estsoft.com")

    with db() as session:
        reason = session.get(MailboxAccount, "untae@estsoft.com").last_error
    assert "도메인 전체 위임" in reason and "Admin console" in reason
    assert gmail.enabled_accounts() == [], "못 여는 사서함은 수집에서 빠집니다"


def test_without_a_service_account_the_address_only_path_is_closed(db, monkeypatch):
    """서비스 계정이 없으면 주소만 든 줄은 열 방법이 없습니다 — 조용히 빈손으로 돌아가는
    대신 그렇게 말합니다."""
    monkeypatch.setattr(settings, "GOOGLE_CREDENTIALS_JSON", "")
    with db() as session:
        session.add(MailboxAccount(email="x@estsoft.com", encrypted_payload=gmail._encrypt({})))
        session.commit()

    assert gmail.delegation_configured() is False
    with pytest.raises(gmail.MailboxTokenError, match="서비스 계정"):
        gmail.access_token("x@estsoft.com")


def test_a_ticked_operator_is_asked_once_on_their_next_login(db, monkeypatch):
    """**접근 승인의 체크 한 칸이 전부입니다** (2026-09-07 운영자 지시: 「운영자가 접근
    승인에서 체크해서 해당 계정의 토큰 받아오도록 다음 로그인에」).

    그 사람이 평소처럼 콘솔에 로그인하면 구글 동의가 **한 번** 끼어들고, 받은 뒤로는
    안 뜹니다. 관리자 결재도 서비스 계정도 없습니다 — 콘솔 로그인에 쓰는 그 OAuth
    클라이언트를 그대로 씁니다.

    **끊긴 줄은 없는 것으로 칩니다.** 토큰이 죽었으면 다시 받아야 하고, 그 사람이 다음에
    로그인할 때가 가장 자연스러운 기회입니다.
    """
    assert gmail.has_token("untae@estsoft.com") is False, "아직 없으면 물어봅니다"

    _connect(db, "untae@estsoft.com")
    assert gmail.has_token("untae@estsoft.com") is True, "이미 있으면 안 묻습니다"
    # 주소 대소문자로 갈리면 안 됩니다 — 로그인 클레임과 저장된 값의 철자가 다를 수 있습니다.
    assert gmail.has_token("UnTae@Estsoft.com") is True

    gmail._mark_broken("untae@estsoft.com", "invalid_grant")
    assert gmail.has_token("untae@estsoft.com") is False, "끊겼으면 다시 받습니다"


def test_the_login_only_detours_for_someone_who_was_ticked():
    """로그인 요청 자체에는 Gmail 스코프를 안 얹습니다.

    누가 로그인할지는 **끝나 봐야** 알기 때문입니다 — 앞에서 얹으면 체크 안 된 사람까지
    전원이 매번 메일 권한을 요구받습니다. 그리고 refresh token 을 받으려면
    `prompt=consent` 가 필요한데, 그걸 로그인에 상시로 달면 **모든 로그인마다** 동의
    화면이 한 장 더 뜹니다.
    """
    import pathlib

    auth = pathlib.Path("src/api/auth.py").read_text(encoding="utf-8")
    login = auth[auth.index("async def auth_google"):auth.index("async def auth_callback")]
    assert "gmail" not in login, "로그인 요청에는 메일 스코프가 없습니다"
    assert '"scope": "openid email profile"' in login

    done = auth[auth.index("user, approved = _login_or_pending"):]
    assert 'user.get("collect_mailbox")' in done, "체크된 사람만 우회합니다"
    assert "has_token(email)" in done, "이미 받았으면 안 묻습니다"


def test_only_mail_that_arrives_after_consent_is_collected(db, monkeypatch):
    """**동의한 이후 메일만** (2026-09-07 운영자 지시).

    토큰 자체에는 시점 제한이 없어 몇 년 전 메일까지 읽힙니다 — 어디부터 볼지는 구글이
    아니라 우리가 정해야 하고, 그 값이 `collect_from` 입니다.

    **재연결에는 다시 안 찍습니다.** 비밀번호를 바꿔 다시 동의한 사람의 그 사이 메일이
    통째로 사라지면 안 되고, 그건 화면 어디에도 안 보입니다.
    """
    from datetime import datetime

    gmail._save("untae@estsoft.com", {"refresh_token": "r"}, None)
    with db() as session:
        first = session.get(MailboxAccount, "untae@estsoft.com").collect_from
    assert isinstance(first, datetime), "붙는 순간을 찍습니다"

    gmail._mark_broken("untae@estsoft.com", "invalid_grant")
    gmail._save("untae@estsoft.com", {"refresh_token": "r2"}, None)
    with db() as session:
        assert session.get(MailboxAccount, "untae@estsoft.com").collect_from == first


def test_the_consent_asks_for_sending_too(db):
    """개인함으로 온 메일에는 **개인함으로 답합니다** (2026-09-07 운영자 지시). 여기서 읽고
    Gmail 로 가서 답장을 쓰라고 하면 화면을 둘로 쪼개는 것입니다.

    `gmail.send` 는 **sensitive** 이고 `gmail.readonly` 는 restricted 인데, Internal 앱이라
    둘 다 검증이 없습니다(운영자 실측).
    """
    url = gmail.authorization_url("https://c/cb", "state")
    assert "gmail.readonly" in url and "gmail.send" in url
    # 읽기·발송이면 충분합니다 — `gmail.modify` 는 남의 사서함을 고칠 수 있습니다.
    assert "gmail.modify" not in url and "mail.google.com" not in url


# --------------------------------------------------------------------------- #
# 개인함 메일을 우리 기록에 잇는다 (2026-09-08 운영자 지시)
# --------------------------------------------------------------------------- #
@pytest.fixture()
def sync_db(db, monkeypatch):
    from src.agents import mailbox_sync

    monkeypatch.setattr(mailbox_sync, "SessionLocal", db)
    return db


def _seed(factory, *, with_ticket: bool):
    from datetime import datetime

    from src.db.models import Contact, Conversation

    with factory() as session:
        contact = Contact(normalized_email="buyer@acme.com", email="buyer@acme.com",
                          full_name="Acme Buyer")
        session.add(contact)
        session.flush()
        if with_ticket:
            session.add_all([
                Conversation(contact_id=contact.id, stage="negotiation",
                             hubspot_ticket_id="T-1", inquiry_subject="옛 문의",
                             created_at=datetime(2026, 1, 1)),
                Conversation(contact_id=contact.id, stage="new",
                             hubspot_ticket_id="T-2", inquiry_subject="최근 문의",
                             created_at=datetime(2026, 9, 1)),
            ])
        session.commit()
        return contact.id


def test_only_a_known_contact_is_kept(sync_db):
    """**다 긁어오지 않습니다.** 개인함에는 회사 공지도 광고도 옵니다 — 전부 넣으면 리드
    히스토리가 그 사람의 받은편지함이 됩니다."""
    from src.agents.mailbox_sync import _known_contact

    contact_id = _seed(sync_db, with_ticket=False)
    with sync_db() as session:
        assert _known_contact(session, ["noreply@newsletter.io"]) is None
        found = _known_contact(session, ["noreply@newsletter.io", "buyer@acme.com"])
        assert found is not None and found.id == contact_id


def test_the_newest_record_is_the_one_it_lands_on(sync_db):
    """후보가 여럿이면 **가장 최근 것** (운영자 지시). 답이 거의 언제나 「지금 진행 중인
    그 건」이라 고르개를 안 띄웁니다."""
    from src.agents.mailbox_sync import _newest_conversation

    contact_id = _seed(sync_db, with_ticket=True)
    with sync_db() as session:
        assert _newest_conversation(session, contact_id).hubspot_ticket_id == "T-2"


def test_a_conversation_without_a_hubspot_number_still_counts(sync_db):
    """**허브스팟 번호를 요구하던 것이 사고였습니다** (2026-09-09 운영자 보고: 「이메일
    들어는 왔는데 티켓에 연동된 게 아니라 무관한 연락으로 들어갔어」).

    예전 조건에는 `hubspot_ticket_id IS NOT NULL` 이 붙어 있었습니다. 워크북에서만 사는
    문의나 번호가 아직 안 달린 대화밖에 없는 고객은 후보가 **0개**가 되어, 개인함으로 온
    메일이 「티켓 외」로 떨어졌습니다 — 붙을 자리를 아는데도 그랬으니 그건 정보가 아니라
    손실입니다. 「티켓, 수주 등 아무거나 최신으로」가 그 지시입니다.
    """
    from datetime import datetime

    from src.db.models import Contact, Conversation
    from src.agents.mailbox_sync import _newest_conversation

    with sync_db() as session:
        contact = Contact(normalized_email="sheet@acme.com", email="sheet@acme.com",
                          full_name="Sheet Only")
        session.add(contact)
        session.flush()
        session.add(Conversation(contact_id=contact.id, stage="negotiation",
                                 hubspot_ticket_id=None, inquiry_subject="워크북 문의",
                                 created_at=datetime(2026, 5, 1)))
        session.commit()
        contact_id = contact.id

    with sync_db() as session:
        found = _newest_conversation(session, contact_id)
        assert found is not None, "번호가 없다고 붙을 자리가 없는 것은 아닙니다"
        assert found.inquiry_subject == "워크북 문의"


def test_a_mail_hubspot_already_has_is_skipped(sync_db):
    """**겹치면 허브스팟 것만** (운영자 지시). 자를 새로 만들지 않고 `_merge_crm_twins` 의
    규칙을 그대로 씁니다 — 같은 연락처 · 같은 초 · 같은 방향."""
    from datetime import datetime, timezone

    from src.agents.mailbox_sync import _hubspot_already_has_it
    from src.db.models import CustomerInteraction

    contact_id = _seed(sync_db, with_ticket=True)
    when = datetime(2026, 9, 7, 1, 2, 3, tzinfo=timezone.utc)
    with sync_db() as session:
        session.add(CustomerInteraction(
            contact_id=contact_id, channel="이메일", direction="inbound",
            summary="허브스팟이 들여온 같은 메일", external_id="hubspot:conv:x",
            happened_at=when.replace(tzinfo=None),
        ))
        session.commit()
        assert _hubspot_already_has_it(session, contact_id, when, "inbound") is True
        # 방향이 다르면 다른 메일입니다.
        assert _hubspot_already_has_it(session, contact_id, when, "outgoing") is False


@pytest.mark.asyncio
async def test_the_queue_asks_once_and_remembers_the_answer(sync_db, monkeypatch):
    """**거절도 저장합니다** — 안 적으면 그 메일이 회차마다 다시 물어봅니다.

    그리고 「아니요」를 눌러도 **기록은 남습니다**: 메일은 이미 리드 히스토리의 한 줄이고,
    연결은 그 줄에 티켓을 채우는 일일 뿐입니다.
    """
    from datetime import datetime

    from src.agents import mailbox_sync
    from src.db.models import CustomerInteraction

    contact_id = _seed(sync_db, with_ticket=True)
    with sync_db() as session:
        session.add(CustomerInteraction(
            contact_id=contact_id, channel="이메일", direction="inbound",
            summary="개인함으로 온 메일", external_id="gmail:m1",
            context="untae@estsoft.com 개인 메일함",
            happened_at=datetime(2026, 9, 7, 5, 0),
        ))
        session.commit()

    pending = mailbox_sync.pending_links()
    assert len(pending) == 1 and pending[0]["ticket_id"] == "T-2", "가장 최근 티켓을 제안"

    await mailbox_sync.decide_link("gmail:m1", None, "ronald")
    assert mailbox_sync.pending_links() == [], "거절한 것은 다시 안 묻습니다"
    with sync_db() as session:
        row = session.query(CustomerInteraction).one()
        assert row.conversation_id is None
        assert row.summary == "개인함으로 온 메일", "기록은 남습니다"


def test_a_contact_without_a_ticket_never_reaches_the_queue(sync_db):
    """연락처는 아는데 티켓이 없으면 **리드 히스토리에만** 남습니다 (운영자 지시).
    물어볼 티켓이 없는데 물어보면 답할 수 없는 질문이 화면에 섭니다."""
    from datetime import datetime

    from src.agents import mailbox_sync
    from src.db.models import CustomerInteraction

    contact_id = _seed(sync_db, with_ticket=False)
    with sync_db() as session:
        session.add(CustomerInteraction(
            contact_id=contact_id, channel="이메일", direction="inbound",
            summary="티켓 없는 고객 메일", external_id="gmail:m2",
            happened_at=datetime(2026, 9, 7, 5, 0),
        ))
        session.commit()

    assert mailbox_sync.pending_links() == []


def test_the_window_is_since_we_last_looked_not_since_consent(sync_db, monkeypatch):
    """**한 회차에 넘친 메일이 영영 안 들어오면 안 됩니다** (2026-09-08).

    「동의 이후」로 물으면 창이 날마다 넓어지는데 한 회차에 받는 것은 50통뿐이라, 한 창에
    그보다 많이 오면 넘친 것을 다음 회차도 못 봅니다 — 같은 조건으로 물어 같은 쪽만
    돌려받기 때문입니다. 그리고 **빠졌다는 표시가 아무 데도 안 남습니다.**

    도장은 수집이 성공했을 때만 찍히므로 실패한 회차의 메일도 안 놓칩니다.
    """
    from datetime import datetime, timedelta

    from src.agents import mailbox_sync
    from src.db.models import MailboxAccount

    consent = datetime(2026, 9, 1, 0, 0)
    polled = datetime(2026, 9, 8, 12, 0)
    with sync_db() as session:
        session.add(MailboxAccount(email="untae@estsoft.com",
                                   encrypted_payload=gmail._encrypt({"refresh_token": "r"}),
                                   collect_from=consent))
        session.commit()

    asked: list[str] = []

    class _Response:
        is_error = False
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    class _Client:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def get(self, url, params=None):
            asked.append((params or {}).get("q", ""))
            return _Response({"messages": []})

    monkeypatch.setattr(mailbox_sync, "access_token", lambda email: "t")
    monkeypatch.setattr(mailbox_sync.httpx, "Client", lambda **_k: _Client())

    # 아직 한 번도 안 돌았으면 동의 시각이 바닥입니다.
    mailbox_sync._sync_one("untae@estsoft.com")
    assert asked[-1] == f"after:{int(consent.replace(tzinfo=timezone.utc).timestamp())}"

    with sync_db() as session:
        session.get(MailboxAccount, "untae@estsoft.com").last_polled_at = polled
        session.commit()

    mailbox_sync._sync_one("untae@estsoft.com")
    expected = (polled - timedelta(minutes=5)).replace(tzinfo=timezone.utc)
    assert asked[-1] == f"after:{int(expected.timestamp())}", "겹침을 두고 그때부터"


def test_a_collected_mail_lands_on_the_newest_record(sync_db, monkeypatch):
    """**붙일 자리가 있으면 수집하면서 바로 붙입니다** (2026-09-09 운영자 지시:
    「최신 티켓이 있으면 무조건 거기다가 넣도록」).

    예전에는 언제나 `conversation_id=None` 으로 넣고 화면에서 사람이 누르기를 기다렸는데,
    그 사이 그 메일은 「티켓 외」에 서서 **무관한 연락처럼** 보였습니다 — 붙을 자리를
    아는데도 그랬습니다.

    아직 아무 기록도 없는 고객은 그대로 비어 있고, 나중에 티켓이 생기면
    `pending_links` 가 물어봅니다. 그 순서도 실제로 있습니다.
    """
    from datetime import datetime

    from src.agents import mailbox_sync
    from src.db.models import CustomerInteraction, MailboxAccount

    contact_id = _seed(sync_db, with_ticket=True)   # T-1(2026-01) · T-2(2026-09)
    with sync_db() as session:
        session.add(MailboxAccount(
            email="untae@estsoft.com",
            encrypted_payload=gmail._encrypt({"refresh_token": "r"}),
            collect_from=datetime(2026, 9, 1)))
        session.commit()

    class _Response:
        is_error = False
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    mail = {
        "id": "m1",
        "internalDate": "1789000000000",
        "snippet": "견적 문의드립니다",
        "payload": {"headers": [
            {"name": "From", "value": "buyer@acme.com"},
            {"name": "To", "value": "untae@estsoft.com"},
            {"name": "Subject", "value": "재문의"},
            {"name": "Date", "value": "Tue, 9 Sep 2026 10:00:00 +0900"},
        ]},
    }

    class _Client:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def get(self, url, params=None):
            if url.endswith("/messages"):
                return _Response({"messages": [{"id": "m1"}]})
            return _Response(mail)

    monkeypatch.setattr(mailbox_sync, "access_token", lambda email: "t")
    monkeypatch.setattr(mailbox_sync.httpx, "Client", lambda **_k: _Client())

    assert mailbox_sync.sync_mailboxes_once() == {"added": 1}

    with sync_db() as session:
        row = session.scalar(
            select(CustomerInteraction).where(CustomerInteraction.external_id == "gmail:m1")
        )
        assert row is not None and row.contact_id == contact_id
        assert row.conversation_id is not None, "붙을 자리를 아는데 「티켓 외」로 두면 안 됩니다"
        landed = session.get(Conversation, row.conversation_id)
        assert landed.hubspot_ticket_id == "T-2", "가장 최근 기록에 붙습니다"

    # 이미 붙었으니 「연결할까요?」에는 안 뜹니다 — 물어볼 것이 없습니다.
    assert mailbox_sync.pending_links() == []
