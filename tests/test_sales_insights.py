"""영업 인사이트(안쪽 겹 `frontend/src/screens/insights/SalesInsightsView.tsx`) — 신호의 문턱과 숫자의 표기.

값은 화면이 브라우저에서 `sales.json` 으로 판정하므로(서버는 그 데이터를 모른다) 여기서는 화면 소스를 읽는다.
"""
from __future__ import annotations

import pathlib
import re

SCREEN = pathlib.Path("frontend/src/screens/insights/SalesInsightsView.tsx")


def _code() -> str:
    """주석을 걷어낸 화면 소스 — 바꾼 이유를 적은 주석이 옛 값을 들고 있어도 걸리지 않게."""
    source = SCREEN.read_text(encoding="utf-8")
    return re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", source, flags=re.S))


def _signal(code: str, key: str) -> str:
    """`SIGNALS` 의 한 항목 — `key` 부터 그 항목을 닫는 `},` 까지."""
    body = code[code.index(f'key: "{key}"') :]
    return body[: body.index("},")]


def test_heavy_self_serve_means_ninety_percent_of_a_pro_month():
    """「셀프서브 대량 사용」은 엔터프라이즈가 아닌 **Pro 플랜** 스페이스가 30일 크레딧 **9,720 이상** — PRO 월
    10,800 크레딧(perso.ai/pricing)의 90% (2026-10-14 운영자). 그 전의 3,000 은 스냅샷 상위 1% 에서 잡은 값이었고,
    등급 조건이 없어 Free 스페이스에도 붙었다.

    문턱은 상수 하나이고 신호의 판정과 툴팁이 같이 읽는다 — 둘에 따로 적으면 툴팁만 옛 숫자를 말한다.
    """
    code = _code()
    assert "const HEAVY_CREDITS_30D = 9_720;" in code
    heavy = _signal(code, "heavy")
    assert "!s.ent && s.tier === PRO_TIER && s.credits_30d >= HEAVY_CREDITS_30D" in heavy
    assert "${num(HEAVY_CREDITS_30D)} 크레딧 이상" in heavy and "Pro 플랜" in heavy
    assert "3000" not in heavy and "3,000" not in heavy


def test_the_team_signal_is_gone_and_only_heavy_use_looks_at_the_pro_tier():
    """「여럿이 쓴다」는 지웠다 (2026-10-14 운영자: 「여럿이 쓴다 빼자」). 같은 날 Pro 플랜에만 붙게 좁히자 그날
    가공 기준 0곳이었다 — 그 전에 붙던 25곳은 엔터프라이즈 24 · Team 1 이었다.

    Pro 플랜을 보는 신호는 「셀프서브 대량 사용」 하나다. `tier` 는 가공 결과의 글자 그대로라(구독 플랜의
    `priority_tier`) 소문자 `pro` 와 맞대고, 비교하는 값은 상수 하나다.
    """
    code = _code()
    assert 'key: "team"' not in code and "여럿이 쓴다" not in code
    assert 'const PRO_TIER = "pro";' in code
    assert "s.tier === PRO_TIER" in _signal(code, "heavy")
    # 나머지 다섯 신호는 등급을 Pro 로 묶지 않는다 — 「무료인데 활발」은 따로 Free 를 본다.
    for key in ("ent-unknown", "trial-heavy", "past-due", "free-active", "failing"):
        assert "PRO_TIER" not in _signal(code, key), key


def test_the_screen_shows_credits_and_never_converts_them_to_minutes():
    """크레딧을 분으로 바꿔 적지 않는다 — 크레딧과 분의 비율은 더빙 모델마다 달라 「60크레딧 = 1분」은
    정확하지 않다 (2026-10-14 운영자: 「분 환산은 더빙 모델마다 달라서 정확하지 않아」)."""
    code = _code()
    assert "mins(" not in code
    assert not re.search(r"/\s*60\b", code), "크레딧을 60 으로 나누는 곳이 있습니다"
    assert not re.search(r"\d\s*분|\}분", code), "화면 글자에 「N분」이 있습니다"
