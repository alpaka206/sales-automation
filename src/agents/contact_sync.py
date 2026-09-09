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
from datetime import datetime, timezone

from ..db.models import Contact, Conversation, CustomerProfile
from ..db.session import SessionLocal
from ..integrations.hubspot_record import stamp_plan_snapshot

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
        # **아직 답이 안 나간 문의에는 지금 플랜을 박아 둡니다** (0110). 접수 시점에
        # 찍으려 해도 처음 보는 고객은 그때 프로필이 비어 있어 찍을 것이 없습니다 — 그
        # 값을 채우는 것이 바로 이 함수라, 여기가 「알게 된 첫 순간」입니다.
        # 한 번 찍힌 티켓과 New 를 지난 티켓은 그냥 지나갑니다(`stamp_plan_snapshot`).
        for conv in (
            session.query(Conversation)
            .filter(Conversation.contact_id == contact_id, Conversation.stage == "new")
            .all()
        ):
            stamp_plan_snapshot(session, conv)
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


# **2분 연락처 스윕은 2026-09-09 에 없앴습니다** (운영자 지시: 「이제 우리 사이트에서만
# 변경할 거라 연락처 변경은 감지 안 해도 됨」).
#
# 그 루프는 「마지막 스윕 이후 허브스팟에서 바뀐 연락처」를 찾아, 걸린 사람마다 최근 메일
# 10건·통화·미팅·노트·Deal 을 **다시 읽었습니다** — 사람당 왕복 약 14회, 회차당 최대 15명.
# 이미 우리 DB 에 있는 것들이고 `external_id` 로 중복만 걸러 버렸습니다. 운영 로그가 그
# 낭비를 그대로 적고 있었습니다: 「연락처 스윕: **플랜 칸 0명**, 기록 2명 가져옴」 —
# 우리가 보는 값은 하나도 안 바뀌었는데 두 사람분을 다시 읽은 것입니다.
#
# 왜 자꾸 「바뀜」으로 잡혔나: 허브스팟은 아주 사소한 변경에도 `lastmodifieddate` 를
# 올립니다 — **우리가 티켓 단계를 옮기거나 플랜 칸을 되쓰는 것 포함.** 즉 우리 쓰기가
# 다음 스윕의 읽기를 부르고 있었습니다.
#
# 남은 길은 **웹훅**입니다(`contact.propertyChange` → `sync_contact_from_hubspot`).
# 그쪽은 연락처 한 번 읽고 행 하나 쓰는 게 전부라, 이 루프가 하던 일 중 실제로 필요한
# 것은 거기서 다 합니다.
#
# 같이 나간 것: `_retire_drafts_for_replies_seen_in_hubspot` — 영업이 허브스팟에서 직접
# 답장했을 때 우리 초안을 지우던 장치입니다. 개발 중 불안정성 때문에 「허브스팟에서
# 답장하라」고 두었던 임시 장치이고, 이제 회신은 콘솔에서만 나갑니다(2026-09-09 운영자).

# 한 회차에 회사 주소를 물어볼 사람 수 (0111). 왕복은 인원수와 무관하게 **둘**이라
# (연결 배치 + 회사 배치) 100명이 1명보다 비싸지 않고, 허브스팟 배치 상한도 100입니다.
_WEBSITES_PER_SWEEP = 100


def _safe_url(raw: str) -> str:
    """저장할 모양으로 다듬습니다 — 화면이 이 값을 `<a href>` 에 그대로 넣습니다 (0111).

    허브스팟의 회사 주소 칸은 자유 입력이라 `perso.ai` 처럼 스킴 없이 적힌 것이 흔합니다.
    그대로 두면 링크가 **콘솔 안의 상대 경로**가 되어 엉뚱한 화면으로 갑니다. 그리고
    `javascript:` 로 시작하는 값은 통째로 버립니다 — 우리가 쓴 글자가 아니라 저쪽에서 온
    글자이고, 링크로 그리는 순간 그것이 실행 경로가 됩니다.

    **들어올 때 한 번만 다듬습니다.** 그리는 곳마다 다듬으면 한 곳을 빼먹고, 그 한 곳이
    하필 링크입니다.
    """
    value = (raw or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered.startswith(("http://", "https://")):
        return value
    return "" if "://" in lowered or ":" in lowered.split("/")[0] else f"https://{value}"


def fill_missing_websites(client, limit: int = _WEBSITES_PER_SWEEP) -> int:
    """아직 안 물어본 연락처의 회사 주소를 채웁니다. 채운 사람 수를 돌려줍니다 (0111).

    **대기열은 `website IS NULL` 그 자체입니다.** 표식 열도, 한 번 훑고 마는 스크립트도
    없습니다 — 모든 연락처가 NULL 로 시작하므로 이 스윕 하나가 옛 행을 메우는 일과 새로
    들어온 사람을 따라잡는 일을 같이 합니다(티켓 대화 수집기가 `history_synced_at` 로 하는
    것과 같은 규칙입니다). 10분마다 100명이라 몇 천 명이어도 반나절이면 한 바퀴입니다.

    **답이 없어도 빈 문자열을 적습니다.** 회사가 없거나 회사에 주소가 없는 사람이 다수인데
    (실측: 회사에 `website` 가 있는 것은 100건 중 9건) 그들을 NULL 로 두면 대기열이 그
    사람들로 영영 막혀 뒤에 있는 사람 차례가 안 옵니다.

    **한 번 채운 값은 다시 안 봅니다.** 회사 쪽에서 주소가 바뀌어도 연락처의
    `lastmodifieddate` 는 안 움직여서 이 스윕이 알아챌 길이 없고, 사이드바에 한 줄 그리는
    값을 위해 전수 재조회를 매일 돌 이유는 없습니다.
    ponytail: 갱신 없음. 주소가 바뀌어 문제가 되면 그때 `website` 를 비우는 자리를 만든다.
    """
    with SessionLocal() as session:
        pending = {
            str(row.hubspot_contact_id): row.id
            for row in session.query(Contact.id, Contact.hubspot_contact_id)
            .filter(Contact.hubspot_contact_id.is_not(None), Contact.website.is_(None))
            .limit(limit)
            .all()
        }
    if not pending:
        return 0

    try:
        found = client.company_websites_sync(list(pending))
    except Exception:
        logger.warning("회사 주소 조회 실패 (%d명)", len(pending), exc_info=True)
        return 0

    filled = 0
    with SessionLocal() as session:
        for hubspot_id, contact_id in pending.items():
            contact = session.get(Contact, contact_id)
            if contact is None:
                continue
            contact.website = _safe_url(found.get(hubspot_id, ""))
            filled += 1 if contact.website else 0
        session.commit()
    if filled:
        logger.info("회사 주소: %d명 채웠습니다 (%d명 조회).", filled, len(pending))
    return filled
