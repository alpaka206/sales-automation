"""허브스팟 연락처의 플랜 칸이 바뀌면 우리 DB와 워크북으로 흘러들어온다.

**화면은 우리 DB를 봅니다.** 티켓을 열 때마다 저쪽에 물어보지 않습니다 — 그러면 답을 읽는
일이 매번 외부 왕복을 기다리고, 허브스팟이 느린 날에는 콘솔 전체가 느려집니다. 대신 저쪽이
바뀔 때 이쪽으로 밀어 넣습니다 (2026-08-26 운영자 지시: "항상 연동할 필요는 없고, 허브스팟에서
변화가 생기면 우리 DB에 실시간으로 업데이트되어야지, 시트에도").

들어오는 문이 둘입니다. 티켓 쪽과 **같은 이중 구조**입니다:

- **웹훅** (`contact.propertyChange`) — 실시간. 허브스팟 비공개 앱에서 그 구독을 켜야
  옵니다. 켜져 있지 않으면 이 문으로는 아무것도 안 들어옵니다.
- **10분 폴러** — 마지막 스윕 이후 바뀐 연락처를 훑습니다. 구독이 꺼져 있어도, 웹훅 한
  건이 유실돼도 늦어도 10분 안에 맞습니다. 티켓 폴러의 docstring 이 말하는 그 역할입니다
  ("Discover and enqueue tickets missed by webhooks").

**세 칸뿐인 이유**는 그 셋만 양쪽에 자리가 있어서입니다. 허브스팟 연락처 속성 549개를 훑어
확인했습니다(2026-08-26): 리드 온도·다음 액션은 대응 속성이 아예 없고, MQL/PQL 과 산업군은
워크북 쪽이 수식 칸이라 값으로 덮으면 그 행만 계산이 멈춥니다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..db.models import Contact, CustomerProfile
from ..db.session import SessionLocal

logger = logging.getLogger(__name__)

# 허브스팟 속성 이름 → 우리 `CustomerProfile` 칸.
FIELDS: dict[str, str] = {
    "plan": "current_plan",
    "plan_tier": "plan_tier",
    "plan_seq": "plan_seq",
    "user_seq": "user_seq",
    "space_seq": "space_seq",
    "industry": "industry",
}

# 같은 일을 하는데 사는 표가 다른 것: 국가와 전화번호는 프로필이 아니라 **연락처**의
# 값입니다. 둘 다 플랜 패널이 그리는 줄이고, 그 패널은 0094 이후 허브스팟이 아니라 우리
# 행을 읽습니다 — 여기 없으면 저쪽에서 바뀐 값이 그 줄에 영영 안 옵니다.
CONTACT_FIELDS: dict[str, str] = {"ip_country": "ip_country", "phone": "phone"}

# 그중 워크북에 자리가 있는 것. **산업군은 없습니다** — 시트에서 「기업 종류」인데 그 칸이
# 「고객 기본 정보」를 Client ID 로 조회하는 수식이라, 값으로 덮으면 그 행만 조회를 멈춥니다.
# plan tier·plan seq 도 시트에 열이 없습니다.
SHEET_FIELDS: dict[str, str] = {
    "current_plan": "plan",
    "user_seq": "user_seq",
    "space_seq": "space_seq",
}

# 웹훅이 이 이름으로 올 때만 일합니다. 연락처의 속성은 549개고, 그중 하나가 바뀔 때마다
# 허브스팟을 다시 읽으면 이메일 한 글자 고친 것에도 왕복이 납니다.
WATCHED_PROPERTIES = frozenset(FIELDS) | frozenset(CONTACT_FIELDS)


def values_from(dto) -> dict[str, str | None]:
    """``ContactDTO`` 에서 우리가 보는 칸만 뽑습니다 — **한 곳에서**.

    세 문(수동 동기화 · 웹훅 · 스윕)이 각자 dict 를 짜면, 칸이 하나 늘 때 한 문만 빠집니다.
    """
    return {prop: getattr(dto, prop, None) for prop in WATCHED_PROPERTIES}


def apply_contact_fields(contact_id: int, incoming: dict[str, str | None]) -> dict[str, str]:
    """우리 DB에 반영하고 바뀐 것만 워크북으로. 바뀐 칸(우리 이름 → 값)을 돌려줍니다.

    ``incoming`` 은 **허브스팟 속성 이름**으로 옵니다(`plan` · `user_seq` · `industry`).

    **빈 값은 덮어쓰지 않습니다.** 허브스팟의 플랜 칸은 대부분 비어 있고(제품 쪽 연동이
    100%가 아닙니다), 빈 것을 「지워라」로 읽으면 사람이 콘솔에서 채워 넣은 값이 스윕 한
    번에 사라집니다. 지우는 것은 티켓 세부 내역의 플랜 정보 폼이 합니다 — 거기 빈 칸은
    사람이 일부러 비운 것입니다.

    **바뀐 것만** 워크북으로 보냅니다. 안 바뀐 값을 매번 다시 쓰면 10분마다 시트에 쓰기가
    나가고, 영업팀이 손으로 고쳐 둔 칸을 같은 값으로 계속 덮습니다.
    """
    changed: dict[str, str] = {}
    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if not contact:
            return {}
        profile = session.get(CustomerProfile, contact_id) or CustomerProfile(
            contact_id=contact_id
        )
        for prop, column in FIELDS.items():
            value = (incoming.get(prop) or "").strip()
            if value and value != (getattr(profile, column) or ""):
                setattr(profile, column, value)
                changed[column] = value
        for prop, column in CONTACT_FIELDS.items():
            value = (incoming.get(prop) or "").strip()
            if value and value != (getattr(contact, column) or ""):
                setattr(contact, column, value)
                changed[column] = value
        if not changed:
            return {}
        # **언제 것인지가 곧 믿어도 되느냐입니다.** 플랜 패널은 이제 허브스팟이 아니라 이
        # 행을 읽으므로, 마지막으로 받아온 시각이 화면에 설 수 있어야 합니다.
        profile.last_synced_at = datetime.now(timezone.utc)
        session.add(profile)
        sheet_client_id = contact.sheet_client_id
        session.commit()

    sheet_values = {
        key: changed[column] for column, key in SHEET_FIELDS.items() if column in changed
    }
    if sheet_values and sheet_client_id:
        from ..integrations.google_sheets import update_inbound_fields

        # 시트가 안 되는 것이 우리 DB 반영을 되돌릴 이유는 아닙니다. 이유는 로그에 남습니다.
        update_inbound_fields(sheet_client_id, sheet_values)
    return changed


def sync_contact_from_hubspot(hubspot_contact_id: str) -> dict[str, str]:
    """그 연락처를 허브스팟에서 한 번 읽어 반영합니다. 우리가 모르는 사람이면 아무 일도 안 합니다.

    웹훅이 값을 payload 에 실어 보내지만(`propertyValue`) 그것을 쓰지 않습니다. 한 번에 여러
    속성이 바뀌면 이벤트도 여러 개로 오고, 그때 각자가 자기 한 칸만 아는 채로 우리 행을
    건드립니다 — 읽어 오면 세 칸이 언제나 같은 순간의 값입니다.
    """
    from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured

    with SessionLocal() as session:
        contact = (
            session.query(Contact)
            .filter(Contact.hubspot_contact_id == str(hubspot_contact_id))
            .first()
        )
        contact_id = contact.id if contact else None
    if contact_id is None:
        return {}

    try:
        dto = HubSpotClient().get_contact_sync(str(hubspot_contact_id))
    except HubSpotNotConfigured:
        return {}
    except Exception:
        logger.warning("HubSpot contact read failed (contact=%s)", contact_id, exc_info=True)
        return {}

    changed = apply_contact_fields(contact_id, values_from(dto))
    if changed:
        logger.info("연락처 %d: 허브스팟에서 %s 를 받았습니다.", contact_id, sorted(changed))
    return changed


# 폴러가 훑는 창. 티켓 스윕과 같은 이유로 조금 겹칩니다 — 경계에 걸친 변경을 놓치지 않게.
_SWEEP_OVERLAP = timedelta(minutes=2)
_SWEEP_MARKER_KIND = "contact_field_poll"
_SWEEP_LIMIT = 200

# 한 회차에 **기록까지** 당겨올 사람 수. 필드 반영은 검색 결과에 이미 값이 들어 있어 공짜지만,
# 기록(메일·통화·미팅·노트·Deal)은 사람당 왕복 다섯 번입니다.
#
# **웹훅을 안 쓰는 이유가 여기 있습니다.** 허브스팟에 Note/Call/Meeting/Email 구독이 있긴
# 한데(expanded object support), 그걸 켜면 한 엔드포인트가 두 가지 payload 스키마를 받게
# 되고 스코프도 늘어납니다. 얻는 것은 「10분 → 즉시」뿐인데, 통화 기록이 10분 늦게 보이는
# 것은 아무 문제가 아닙니다.
#
# 그리고 **활동을 남기면 그 연락처의 `lastmodifieddate` 가 같이 밀립니다** — 2026-08-26 실측:
# 활동 10:39:33 → 연락처 수정 10:40:13. 그래서 이 스윕이 이미 그 사람들을 보고 있고, 구독을
# 하나도 안 만들어도 됩니다.
_HISTORY_PER_SWEEP = 15


def _changed_at(dto) -> datetime | None:
    """그 연락처가 마지막으로 바뀐 시각. 몫에서 끊겼을 때 워터마크를 여기까지만 옮깁니다."""
    raw = getattr(dto, "updated_at", None)
    return raw if isinstance(raw, datetime) else None


def _last_sweep_at() -> datetime:
    from ..db.models import Event

    with SessionLocal() as session:
        row = (
            session.query(Event)
            .filter(Event.kind == _SWEEP_MARKER_KIND)
            .order_by(Event.created_at.desc())
            .first()
        )
    if row and row.payload and "poll_at" in row.payload:
        return datetime.fromisoformat(row.payload["poll_at"])
    # 첫 회차는 한 시간만 봅니다. 포털 전체를 훑는 자리가 아닙니다 — 그건 사람이 고객
    # 상세에서 「HubSpot 동기화」를 누를 때 할 일입니다.
    return datetime.now(timezone.utc) - timedelta(hours=1)


def sync_changed_contacts_once() -> int:
    """마지막 스윕 이후 바뀐 연락처를 훑어 반영합니다. 고친 연락처 수를 돌려줍니다.

    **웹훅이 있어도 이것이 필요합니다.** 구독이 꺼져 있을 수도 있고, 한 건이 유실될 수도
    있고, 우리가 배포 중일 수도 있습니다. 티켓 쪽이 이미 같은 이유로 폴러를 둡니다.

    우리가 아는 연락처만 고칩니다 — 포털의 연락처는 수만 개이고, 그중 이 콘솔에 행이 있는
    것만이 화면에 그려질 수 있습니다.
    """
    from ..db.models import Event
    from ..integrations.hubspot import HubSpotClient, HubSpotNotConfigured

    try:
        client = HubSpotClient()
    except HubSpotNotConfigured:
        return 0

    since = _last_sweep_at() - _SWEEP_OVERLAP
    now = datetime.now(timezone.utc)
    try:
        rows = client.search_contacts_changed_since(since, limit=_SWEEP_LIMIT)
    except Exception:
        logger.warning("HubSpot 연락처 스윕 검색 실패", exc_info=True)
        return 0

    with SessionLocal() as session:
        known = {
            str(row.hubspot_contact_id): row.id
            for row in session.query(Contact.id, Contact.hubspot_contact_id)
            .filter(Contact.hubspot_contact_id.is_not(None))
            .all()
        }

    touched = 0
    pulled = 0
    reached: datetime | None = None
    for dto in rows:
        contact_id = known.get(str(dto.id))
        if contact_id is None:
            continue
        try:
            if apply_contact_fields(contact_id, values_from(dto)):
                touched += 1
        except Exception:
            logger.warning("연락처 %d 반영 실패", contact_id, exc_info=True)

        # 기록까지 당겨옵니다 — 메일·통화·미팅·노트·Deal. 손으로 누르던 「HubSpot 동기화」가
        # 하던 일이고, 그 버튼은 이제 「지금 당장」이 필요할 때만 씁니다.
        #
        # **한 회차의 몫이 있습니다.** 대량 임포트가 아는 연락처 이백 명을 건드리면 왕복이
        # 천 번이 넘고, 허브스팟 한도(10초 100회)에 걸려 스윕 한 회차가 몇 분이 됩니다.
        # 몫을 넘기면 워터마크를 **여기까지**로 두어 다음 회차가 이어서 훑습니다 — 티켓
        # 스윕이 페이지가 꽉 찼을 때 하는 것과 같은 규칙입니다.
        if pulled >= _HISTORY_PER_SWEEP:
            break
        try:
            from ..api.routes.customer_ops import _sync_hubspot

            _sync_hubspot(contact_id, per_type=10)
        except Exception:
            logger.warning("연락처 %d 기록 가져오기 실패", contact_id, exc_info=True)
        pulled += 1
        reached = _changed_at(dto) or reached

    # **워터마크는 끝에 한 번만.** 중간에 터지면 안 밀리고, 다음 회차가 같은 창을 다시
    # 훑습니다 — 같은 값을 다시 넣는 것은 무해합니다(바뀐 것이 없으면 아무 데도 안 씁니다).
    #
    # **못 읽은 것이 남았으면 워터마크를 끝까지 밀지 않습니다.** 두 가지로 남습니다:
    #
    #   ① 검색 페이지가 꽉 찼다 — 허브스팟에 더 있다는 뜻이고, 정렬이 오름차순이라 **안 읽은
    #      쪽이 더 최신**입니다. `now` 로 밀면 그 사람들은 다음 창 밖으로 나가 영영 안
    #      돌아옵니다. 대량 임포트에서만 나는 일이라 평소에는 안 걸리지만, 나는 그날
    #      조용히 유실됩니다 (2026-08-26 지적).
    #   ② 기록 몫에서 끊겼다 — 같은 이유입니다.
    #
    # 티켓 스윕이 ①을 이미 그렇게 합니다. 그때 옮기는 자리는 **읽은 것 중 가장 최신**입니다.
    read_upto = [stamp for stamp in map(_changed_at, rows) if stamp]
    if len(rows) >= _SWEEP_LIMIT and read_upto:
        now = min(now, max(read_upto))
    if pulled >= _HISTORY_PER_SWEEP and reached:
        now = min(now, reached)
    with SessionLocal() as session:
        session.add(Event(kind=_SWEEP_MARKER_KIND, payload={"poll_at": now.isoformat()}))
        session.commit()
    if touched or pulled:
        logger.info(
            "연락처 스윕: 플랜 칸 %d명, 기록 %d명 가져옴.", touched, pulled
        )
    return touched


# 이 스윕만 따로, 더 자주 돕니다. 10분짜리 폴러에 얹혀 있던 것을 떼어낸 이유는 **이것이
# 사람이 읽기만 하는 값이 아니기 때문**입니다: 영업이 허브스팟에서 직접 회신하면
# `_retire_drafts_for_replies_seen_in_hubspot` 이 우리 대기 초안을 종료시킵니다. 그 사이가
# 곧 「고객이 같은 질문에 두 번째 답을 받는」 창이라, 10분과 2분은 체감이 다릅니다.
#
# **30초로는 안 내립니다.** 허브스팟 Search 는 새 레코드가 색인에 뜨기까지 5~10초가
# 걸립니다("It may take a few moments for newly created or updated CRM objects to appear
# in search results"). 주기가 30초면 그 지연이 주기의 1/3이라 창 설계가 예민해지는데,
# 2분 대비 얻는 것이 없습니다. 지금 창은 `주기 + _SWEEP_OVERLAP(2분)` 이라 색인 지연보다
# 한참 넉넉하고, 다시 읽는 것은 무해합니다(바뀐 것이 없으면 아무 데도 안 씁니다).
#
# Search 는 일반 한도(10초 100회)와 **별개로 초당 4회**가 걸립니다. 2분에 1~2회라 여유가
# 큽니다 — 페이지가 둘 이상이 되는 것은 2분 안에 100명 넘게 바뀔 때뿐입니다.
CONTACT_SWEEP_SECONDS = 120


async def run_contact_sweep() -> None:
    """연락처 스윕만 따로 도는 루프."""
    import asyncio

    logger.info("연락처 스윕 시작 (주기 %ds)", CONTACT_SWEEP_SECONDS)
    while True:
        try:
            await asyncio.to_thread(sync_changed_contacts_once)
        except Exception:
            logger.exception("연락처 스윕 회차 실패")
        await asyncio.sleep(CONTACT_SWEEP_SECONDS)
