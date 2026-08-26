"""허브스팟 Contact 레코드의 「기본 그룹」을 티켓 세부 내역 오른쪽에 그대로 옮겨 놓는다.

여기까지 오게 된 이유: 플랜·좌석·테넌트 정보는 허브스팟에 다 적혀 있는데 이 콘솔에는 한
줄도 없었다. 답을 쓰는 사람이 "이 고객이 무슨 플랜인지" 를 보려면 콘솔을 떠나 허브스팟을
열어야 했다 — 답을 쓰는 화면에서 답에 필요한 정보만 없었던 셈이다.

**Company 가 아니라 Contact 다.** 처음에는 Company 를 읽었다. 운영자가 준 매핑 표의 왼쪽
칸이 `Company` 였기 때문인데, 실제 포털 화면을 보니 `Plan` · `IP Country` · `user seq` ·
`space seq` · `plan tier` · `plan seq` 가 전부 **Contact 레코드의 「기본 그룹」**에 있었다
(같은 그룹에 `Contact owner` 가 서 있는 것이 결정적이었다 — 그건 Company 에 없는 속성이다).
덕분에 연결(association) 조회가 통째로 없어졌고, 「주 회사가 어느 쪽이냐」는 물음도 사라졌다.
`crm.objects.companies.read` 도 필요 없다 — 이 앱은 이미 연락처를 읽고 있다.

**속성 이름을 코드에 박지 않는다.** 운영자가 아는 것은 허브스팟 화면에 보이는 **라벨**
(`IP Country`, `user seq`)이고, 내부 이름(`hs_ip_country`, `user_seq_c` …)은 포털마다
다르며 이름을 바꿔도 라벨만 바뀐다. 그래서 속성 카탈로그(`/crm/v3/properties/contacts`)를
먼저 읽어 **라벨로 찾고, 못 찾으면 내부 이름으로 한 번 더** 찾는다. 순서가 중요하다:
은퇴한 `user_seq` 와 지금 쓰는 `user_seq_c`(라벨 `user seq`)가 같이 있을 때 이름을 먼저
보면 빈 옛 속성이 이긴다.

**빈 값도 줄을 만든다.** 허브스팟 자신의 사이드바가 값 없는 필드에 `--` 를 그린다. 운영자가
그 화면을 보고 이 화면을 열기 때문에, 우리가 빈 줄을 숨기면 「같은 레코드인데 줄 수가 다른」
화면이 된다 — 그리고 실제로 이 포털의 플랜 필드는 대부분 비어 있어서, 숨기면 카드가 통째로
사라진다. 「Plan: —」은 소음이 아니라 "돈 내는 고객이 아니다" 라는 정보다.

**못 찾은 필드는 못 찾았다고 말한다**(`found=False`). 값이 빈 것(`—`)과는 다른 이야기다.
앞엣것은 그 고객 이야기이고 뒤엣것은 설정 이야기다 — 라벨이 한국어이거나 단어가 하나 더
붙으면 정규화로는 못 잡는데, 조용히 빼면 운영자가 진짜 라벨을 알려줄 기회가 없다.

**어느 카드에 들어가는지도 여기 한 곳에서 정한다**(`RECORD_FIELDS`). 화면은 서버가 준
그룹을 그리기만 한다 — 「화면이 아는 목록은 서버가 준다」와 같은 이유로, 필드가 늘거나
카드가 갈리는 일이 화면과 서버 두 곳에 적히면 반드시 어긋난다.

**플랜 필드는 되쓸 수 있다**(`update_record_fields`). 연동이 100% 가 아니라 사람이 채워야
할 때가 있다는 운영자 판단이다. 쓰기이므로 `guard_external_write` 를 **가장 먼저** 지난다 —
안전 모드에서는 네트워크에 닿기도 전에 막힌다.

쓰기의 울타리는 `RECORD_FIELDS` 그 자체다. 화면이 보내는 것은 우리 `key`(`user_seq`)이고
허브스팟 속성 이름은 서버가 카탈로그에서 다시 찾는다. 그래서 브라우저가 무슨 이름을 보내든
`email` 이나 `lifecyclestage` 같은 남의 속성에는 닿지 않는다 — 목록에 있고 `editable=True`
인 필드만 통과한다. 화면을 믿고 이름을 그대로 넘겼다면, 콘솔에 닿은 누구든 연락처의 아무
속성이나 덮어쓸 수 있었다.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import NamedTuple

import httpx

from ..common.safe_mode import guard_external_write
from .hubspot import BASE_URL, _require_token, _sync_request_with_retries

logger = logging.getLogger(__name__)

# 사이드바 패널이라 사람이 기다려 줄 만큼만 기다린다. 라우트가 sync 라 이 시간 동안
# threadpool 자리 하나를 붙잡고 있고, 허브스팟이 응답 없이 매달리는 장애에서는 그 자리들이
# 게시판·검토 목록 같은 다른 화면의 요청을 뒤에 줄 세운다.
_TIMEOUT = 8.0

# 카드 하나가 곧 운영자 표의 「레코드」다. 키와 제목이 따로인 이유: 제목은 사람이 읽는
# 말이라 언제든 바뀌고, 화면은 「이 묶음이 이미 있는 연락처 정보 카드에 얹히는 것인가」를
# 키로 판단해야 한다 — 제목 글자로 판단하면 제목을 고치는 순간 조용히 카드가 둘이 된다.
# 키를 바꾸면 `MessageDetail.tsx` 의 `"contact"` 도 같이 바뀌어야 한다
# (`tests/test_hubspot_record.py` 가 그 짝을 붙잡는다).
GROUPS: tuple[tuple[str, str], ...] = (
    ("plan", "플랜 정보"),
    ("contact", "연락처 정보"),
)

class Field(NamedTuple):
    """한 줄. 네 이름이 각자 다른 일을 한다 — 합치면 하나를 고칠 때 나머지가 끊긴다.

    - ``group``      : 어느 카드에 서는가 (`GROUPS` 의 키)
    - ``key``        : 폼과 API 가 주고받는 **안정된 이름**. 화면 글자를 다듬어도 안 바뀐다.
    - ``label``      : 화면에 적는 말. 운영자가 정한다.
    - ``candidates`` : 허브스팟에서 집어낼 열쇠. 첫 번째가 포털 라벨이고 뒤는 대비책.
    - ``editable``   : 콘솔에서 되쓸 수 있는가. **쓰기의 울타리가 이 칸이다.**
    - ``column``     : 우리 DB 의 자리(`profile.<칸>` 또는 `contact.<칸>`). **읽기는 여기서**
      한다 — 0094 이후 이 패널은 허브스팟이 아니라 우리 행을 읽는다. `candidates` 는 그
      반대편, 즉 **되쓸 때** 허브스팟 속성 이름을 찾는 데만 쓰인다.
    """

    group: str
    key: str
    label: str
    candidates: tuple[str, ...]
    editable: bool = False
    column: str = ""


# 순서가 곧 화면 순서다 — 운영자가 정한 대로 위에서 아래로 그려진다.
#
# 플랜 다섯은 `editable=True`: 연동이 100% 가 아니라 사람이 채워야 할 때가 있다(운영자 판단).
# 「국가」는 허브스팟이 접속 IP 로 스스로 뽑는 값이라 손으로 고칠 것이 아니고, 「전화번호」는
# 연락처 정보 카드의 다른 칸들과 함께 다뤄야 해서 여기서는 읽기만 한다.
#
# 회사 이름은 아예 넣지 않는다: 연락처 정보 카드에 이미 **고칠 수 있는** 회사 칸이 있고, 그건
# gmail·미확인 고객이 회사 이름을 갖는 유일한 자리다. 옆에 사본을 세우면 둘 중 어느 것이
# 진짜인지 화면만 봐서는 알 수 없다.
RECORD_FIELDS: tuple[Field, ...] = (
    Field("plan", "plan", "플랜 (Plan)", ("plan",), True, "profile.current_plan"),
    Field("plan", "plan_tier", "플랜 티어 (plan tier)", ("plan tier",), True, "profile.plan_tier"),
    Field("plan", "user_seq", "user seq", ("user seq",), True, "profile.user_seq"),
    Field("plan", "space_seq", "space seq", ("space seq",), True, "profile.space_seq"),
    Field("plan", "plan_seq", "plan seq", ("plan seq",), True, "profile.plan_seq"),
    Field(
        "contact", "ip_country", "국가 (IP Country)",
        ("ip country", "ip_country", "hs_ip_country", "ip"), False, "contact.ip_country",
    ),
    Field("contact", "phone", "전화번호", ("전화번호", "phone number", "phone"), False, "contact.phone"),
)


def _norm(value: str) -> str:
    """라벨과 내부 이름을 같은 자로 재기 위한 정규화.

    `IP Country` · `ip_country` · `IP-COUNTRY` · `User Seq.` · `userSeq` 가 전부 한 키가
    된다. 한글도 남긴다(`전화번호`). 허브스팟 접두사 `hs_` 는 **떼지 않는다** — 뗐다가는
    `hs_plan` 과 `plan` 이 같은 것이 되어, 서로 다른 속성 둘 중 아무거나 잡힌다.

    번역과 덧붙은 단어는 이걸로 못 잡는다. 철자를 더 넣는 것은 지는 싸움이라 안 한다 —
    대신 못 찾았다고 말한다(`build_groups` 의 `found`).
    """
    return re.sub(r"[^0-9a-z가-힣]", "", value.lower())


def _index(labels: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """카탈로그를 `(라벨→이름, 이름→이름)` 두 색인으로 가른다.

    한 색인에 섞으면 먼저 들어온 쪽이 이기는데, 그 순서는 허브스팟이 정한다. 라벨을 따로
    두어야 「라벨 우선」이 순서와 무관하게 지켜진다.
    """
    by_label: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for name, label in labels.items():
        by_label.setdefault(_norm(label), name)
        by_name.setdefault(_norm(name), name)
    return by_label, by_name


def resolve_property_names(labels: dict[str, str]) -> dict[str, str]:
    """`RECORD_FIELDS` 의 각 필드 → 그 필드가 가리키는 **내부 이름**. 못 찾으면 빠진다.

    허브스팟은 달라는 속성을 이름으로 적어야 준다. 연락처 속성은 수백 개라 전부 달라고 하면
    시스템 속성이 딸려 오고, 그중 무엇이 운영자가 말한 그 필드인지는 여전히 라벨로 골라야
    한다.
    """
    by_label, by_name = _index(labels)
    resolved: dict[str, str] = {}
    for field in RECORD_FIELDS:
        for candidate in field.candidates:
            found = by_label.get(_norm(candidate)) or by_name.get(_norm(candidate))
            if found:
                resolved[field.key] = found
                break
    return resolved


def build_groups(properties: dict[str, object], resolved: dict[str, str]) -> list[dict]:
    """받아 온 속성값을 화면이 그릴 카드 목록으로 접는다 — 순수 함수.

    줄은 셋 중 하나다:

    - 값이 있다 → `value` 가 찬다.
    - 속성은 찾았는데 이 고객에게 값이 없다 → `value=None, found=True` → 화면에 `—`.
      허브스팟 사이드바가 `--` 를 그리는 것과 같은 자리에 같은 뜻으로 선다.
    - 속성 자체를 못 찾았다 → `found=False`. 이건 이 고객 이야기가 아니라 설정 이야기라서,
      조용히 빠지면 아무도 못 고친다.
    """
    rows_by_group: dict[str, list[dict]] = {}
    for field in RECORD_FIELDS:
        name = resolved.get(field.key)
        if name is None:
            rows_by_group.setdefault(field.group, []).append(
                {"key": field.key, "label": field.label, "value": None,
                 "found": False, "editable": False}
            )
            continue
        raw = properties.get(name)
        value = "" if raw is None else str(raw).strip()
        rows_by_group.setdefault(field.group, []).append(
            {"key": field.key, "label": field.label, "value": value or None,
             "found": True, "editable": field.editable}
        )
    # 카드의 `editable` 은 **못 찾은 필드를 뺀** 값이다: 허브스팟에 없는 속성은 쓸 수도 없으니
    # 연필만 달아 두면 저장이 아무 일도 안 하고 성공한 척한다.
    return [
        {
            "key": key,
            "title": title,
            "rows": rows_by_group[key],
            "editable": any(row["editable"] for row in rows_by_group[key]),
        }
        for key, title in GROUPS
        if rows_by_group.get(key)
    ]


@lru_cache(maxsize=1)
def _property_labels(token: str) -> dict[str, str]:
    """`{내부 이름: 라벨}` — 포털의 Contact 속성 카탈로그.

    프로세스당 한 번만 읽는다. 속성 정의는 사람이 허브스팟 설정에서 바꿀 때만 바뀌므로
    티켓을 열 때마다 물어볼 이유가 없다. 캐시 키가 토큰인 것은 그래야 `lru_cache` 가 되기
    때문이고, 토큰이 바뀌면 캐시도 저절로 갈린다.

    **빈 결과는 부르는 쪽이 캐시에서 지운다**(`fetch_record_groups`). 여기서 한 번 비어
    돌아온 것을 그대로 물고 있으면, 허브스팟에서 속성을 고친 뒤 배포를 다시 할 때까지
    패널이 영영 비어 있는다 — 그리고 그건 화면에서 「값이 없다」와 구별되지 않는다.
    """
    with httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=_TIMEOUT) as client:
        response = _sync_request_with_retries(
            client, "GET", f"{BASE_URL}/crm/v3/properties/contacts"
        )
    response.raise_for_status()
    return {
        str(item["name"]): str(item.get("label") or item["name"])
        for item in response.json().get("results", [])
        if item.get("name")
    }


def _blank(error: str | None = None) -> dict:
    return {"groups": [], "error": error}


def _friendly_error(exc: Exception) -> str:
    """403 은 뭉뚱그리지 않는다.

    연락처 **객체**는 이 앱이 늘 읽지만 속성 **카탈로그**(`crm.schemas.contacts.read`)는
    이 패널이 처음 읽는다. 그걸 「허브스팟을 읽지 못했습니다」로 적으면 설정 2분짜리 문제가
    일주일짜리 수수께끼가 된다.
    """
    response = getattr(exc, "response", None)
    if response is not None and response.status_code == 403:
        return (
            "허브스팟 앱에 연락처 속성 읽기 권한이 없습니다. "
            "비공개 앱 스코프에 crm.objects.contacts.read 와 crm.schemas.contacts.read 를 "
            "추가해 주세요."
        )
    return "허브스팟을 읽지 못했습니다."


def update_record_fields(hubspot_contact_id: str, values: dict[str, str]) -> None:
    """콘솔에서 고친 플랜 값을 허브스팟 연락처에 되쓴다.

    **가장 먼저 `guard_external_write` 를 지난다.** 안전 모드에서는 네트워크에 닿기도 전에
    `ExternalWriteBlocked` 로 끝난다. 막는 자리를 HTTP 호출 옆이 아니라 함수 입구에 두는
    이유는, 다음에 재시도나 배치가 붙어도 그 앞을 지나게 하기 위해서다.

    ``values`` 의 키는 우리 `Field.key` 다. 허브스팟 속성 이름은 여기서 카탈로그로 다시
    찾는다 — 브라우저가 보낸 이름을 그대로 쓰면 콘솔에 닿은 누구든 `email` 이든
    `lifecyclestage` 든 덮어쓸 수 있다. 목록에 있고 `editable` 인 것만 통과한다.

    빈 문자열은 지우라는 뜻이라 그대로 보낸다(허브스팟에서 값이 비워진다). 쓸 것이 하나도
    없으면 요청을 아예 내지 않는다.
    """
    guard_external_write("hubspot:update_contact_record")

    writable = {field.key for field in RECORD_FIELDS if field.editable}
    token = _require_token()
    resolved = resolve_property_names(_property_labels(token))
    properties = {
        resolved[key]: (value or "").strip()
        for key, value in values.items()
        if key in writable and key in resolved
    }
    if not properties:
        return

    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(headers=headers, timeout=_TIMEOUT) as client:
        response = _sync_request_with_retries(
            client,
            "PATCH",
            f"{BASE_URL}/crm/v3/objects/contacts/{hubspot_contact_id}",
            json={"properties": properties},
        )
    response.raise_for_status()


def fetch_record_groups(contact_id: int) -> dict:
    """**우리 행**을 읽어 화면이 그릴 그룹으로 돌려준다.

    돌려주는 모양은 예전 그대로다: `{"groups": [...], "error": str|None}`. 바뀐 것은 값이
    어디서 오느냐 하나다.

    **예전에는 티켓을 열 때마다 허브스팟 연락처를 한 번씩 읽었다.** 그래서 화면 값이 언제나
    지금 허브스팟 값이었지만, 답을 읽는 일이 매번 외부 왕복을 기다렸고 허브스팟이 느린 날에는
    그 패널 때문에 티켓이 늦게 열렸다. 운영자 지시(2026-08-26): 화면은 우리 DB 를 보고, 저쪽이
    바뀌면 그때 이쪽으로 들어오게 한다 — 그 들어오는 문이 `agents/contact_sync` 의 셋이다
    (웹훅 · 10분 스윕 · 고객 상세의 수동 동기화).

    그래서 이 함수는 이제 **네트워크에 닿지 않는다.** 예외를 올리지 않는 계약은 그대로 두되
    (이 패널 하나 때문에 티켓 세부 내역이 안 열리면 답을 못 쓴다) 실패할 거리가 거의 없다.

    ``found`` 는 언제나 참이다. 예전에 거짓일 수 있었던 것은 「그 포털에 그 속성이 없다」는
    뜻이었고, 그건 허브스팟 카탈로그를 뒤질 때만 알 수 있는 사실이다. 지금 그 정보가 필요한
    곳은 되쓰기(`update_record_fields`) 하나뿐이고, 거기서는 여전히 카탈로그를 본다.
    """
    from ..db.models import Contact, CustomerProfile
    from ..db.session import SessionLocal

    try:
        with SessionLocal() as session:
            contact = session.get(Contact, int(contact_id))
            if contact is None:
                return _blank("연락처를 찾을 수 없습니다.")
            profile = session.get(CustomerProfile, int(contact_id))
            sources = {"contact": contact, "profile": profile}
            rows_by_group: dict[str, list[dict]] = {}
            for field in RECORD_FIELDS:
                table, _, column = field.column.partition(".")
                row = sources.get(table)
                raw = getattr(row, column, None) if row is not None else None
                value = "" if raw is None else str(raw).strip()
                rows_by_group.setdefault(field.group, []).append(
                    {
                        "key": field.key,
                        "label": field.label,
                        "value": value or None,
                        "found": True,
                        "editable": field.editable,
                    }
                )
            synced_at = getattr(profile, "last_synced_at", None) if profile else None
    except Exception as exc:  # 계약이 「절대 안 터진다」라서 종류를 나열하지 않는다.
        logger.warning("Plan panel read failed for contact %s: %s", contact_id, exc)
        return _blank("플랜 정보를 읽지 못했습니다.")

    groups = [
        {
            "key": key,
            "title": title,
            "rows": rows_by_group[key],
            "editable": any(row["editable"] for row in rows_by_group[key]),
        }
        for key, title in GROUPS
        if rows_by_group.get(key)
    ]
    # **언제 것인지가 곧 믿어도 되느냐다.** 저쪽을 그때그때 읽던 시절에는 물어볼 필요가
    # 없던 질문이고, 지금은 화면이 답할 수 있어야 한다.
    return {"groups": groups, "error": None, "synced_at": synced_at}
