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

읽기 전용이라 `guard_external_write` 는 지나지 않는다. 안전 모드는 쓰기만 막는다.
**이 모듈에 쓰기를 추가한다면 그때는 반드시 `guard_external_write` 를 지나야 한다** —
지금 안 지나는 것은 면제가 아니라 쓰기가 없기 때문이다.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

import httpx

from .hubspot import BASE_URL, HubSpotNotConfigured, _require_token, _sync_request_with_retries

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

# (그룹 키, 화면에 적을 라벨, 허브스팟에서 찾을 이름 후보)
#
# **화면에 적는 말과 찾는 말은 다르다.** 가운데 칸은 운영자가 콘솔에서 읽고 싶어 하는
# 글자이고(`플랜 (Plan)`), 오른쪽 칸은 허브스팟에서 그 속성을 집어내는 열쇠다(`plan`).
# 둘을 한 칸으로 합치면 화면 글자를 다듬는 순간 조회가 끊긴다.
#
# 순서도 화면 순서다 — 운영자가 정한 대로 위에서 아래로 그려진다. 후보의 첫 번째는 허브스팟
# 라벨이고, 뒤따르는 것은 그 라벨이 안 잡혔을 때의 대비책이다(기본 속성의 내부 이름이거나
# 다른 철자). `user seq` 에 `user_seq` 를 덧붙이지 않는 이유는 `_norm` 이 둘을 이미 같은
# 자로 재기 때문이다.
#
# 회사 이름은 넣지 않는다: 연락처 정보 카드에 이미 **고칠 수 있는** 회사 칸이 있고, 그건
# gmail·미확인 고객이 회사 이름을 갖는 유일한 자리다. 옆에 읽기 전용 사본을 세우면 둘 중
# 어느 것이 진짜인지 화면만 봐서는 알 수 없다.
RECORD_FIELDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("plan", "플랜 (Plan)", ("plan",)),
    ("plan", "플랜 티어 (plan tier)", ("plan tier",)),
    ("plan", "user seq", ("user seq",)),
    ("plan", "space seq", ("space seq",)),
    ("plan", "plan seq", ("plan seq",)),
    ("contact", "국가 (IP Country)", ("ip country", "hs_ip_country", "ip")),
    ("contact", "전화번호", ("전화번호", "phone number", "phone")),
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
    for _key, field, candidates in RECORD_FIELDS:
        for candidate in candidates:
            found = by_label.get(_norm(candidate)) or by_name.get(_norm(candidate))
            if found:
                resolved[field] = found
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
    for key, field, _candidates in RECORD_FIELDS:
        name = resolved.get(field)
        if name is None:
            rows_by_group.setdefault(key, []).append(
                {"label": field, "value": None, "found": False}
            )
            continue
        raw = properties.get(name)
        value = "" if raw is None else str(raw).strip()
        rows_by_group.setdefault(key, []).append(
            {"label": field, "value": value or None, "found": True}
        )
    return [
        {"key": key, "title": title, "rows": rows_by_group[key]}
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


def fetch_record_groups(hubspot_contact_id: str) -> dict:
    """연락처 레코드를 읽어 화면이 그릴 그룹으로 돌려준다.

    돌려주는 모양은 **언제나 같다**: `{"groups": [...], "error": str|None}`. 무슨 일이 나든
    예외를 올리지 않는다 — 이 패널 하나 때문에 티켓 세부 내역 전체가 안 열리면 답을 못 쓴다.
    그래서 `except Exception` 이다: 잡을 예외를 나열하면 그 목록에 없는 것 하나가 곧 이
    함수의 계약을 깬다(허브스팟이 dict 가 아닌 JSON 을 돌려주면 `.get` 이 `AttributeError`
    다).

    ponytail: 캐시가 없다. 티켓을 열 때마다 연락처 1회다(카탈로그는 프로세스 캐시). 사람이
    여는 속도라 허브스팟 한도(10초 100회)에 한참 못 미치고, 대신 화면에 뜨는 값이 언제나
    지금 허브스팟의 값이다. 여는 속도가 한도를 넘기 시작하면 그때 테이블에 담고 폴러가
    갱신하게 바꾼다 — 그때부터는 「언제 것이냐」를 화면에 적어야 한다.
    """
    try:
        token = _require_token()
    except HubSpotNotConfigured:
        return _blank("HubSpot 토큰이 설정되지 않았습니다.")

    try:
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(headers=headers, timeout=_TIMEOUT) as client:
            labels = _property_labels(token)
            if not labels:
                # 빈 카탈로그를 프로세스 내내 물고 있지 않는다.
                _property_labels.cache_clear()
                return _blank("허브스팟 속성 목록을 읽지 못했습니다.")

            resolved = resolve_property_names(labels)
            if not resolved:
                return _blank("허브스팟 연락처에서 해당 속성을 하나도 찾지 못했습니다.")

            record = _sync_request_with_retries(
                client,
                "GET",
                f"{BASE_URL}/crm/v3/objects/contacts/{hubspot_contact_id}",
                params={"properties": ",".join(sorted(set(resolved.values())))},
            )
            record.raise_for_status()
            properties = record.json().get("properties") or {}
            groups = build_groups(properties, resolved)
    except Exception as exc:  # 계약이 「절대 안 터진다」라서 종류를 나열하지 않는다.
        logger.warning("HubSpot record lookup failed for contact %s: %s", hubspot_contact_id, exc)
        return _blank(_friendly_error(exc))

    return {"groups": groups, "error": None}
