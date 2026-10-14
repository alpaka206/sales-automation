"""영업 인사이트(`frontend/src/screens/SalesInsights.tsx`) — 신호의 문턱과 숫자의 표기.

값은 화면이 브라우저에서 `sales.json` 으로 판정하므로(서버는 그 데이터를 모른다) 여기서는 화면 소스를 읽는다.
"""
from __future__ import annotations

import pathlib
import re

SCREEN = pathlib.Path("frontend/src/screens/SalesInsights.tsx")


def _code() -> str:
    """주석을 걷어낸 화면 소스 — 바꾼 이유를 적은 주석이 옛 값을 들고 있어도 걸리지 않게."""
    source = SCREEN.read_text(encoding="utf-8")
    return re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", source, flags=re.S))


def test_heavy_self_serve_means_ninety_percent_of_a_pro_month():
    """「셀프서브 대량 사용」은 엔터프라이즈가 아닌데 30일 크레딧이 **9,720 이상** — PRO 월 10,800 크레딧
    (perso.ai/pricing)의 90% (2026-10-14 운영자). 그 전의 3,000 은 스냅샷 상위 1% 에서 잡은 값이었다.

    문턱은 상수 하나이고 신호의 판정과 툴팁이 같이 읽는다 — 둘에 따로 적으면 툴팁만 옛 숫자를 말한다.
    """
    code = _code()
    assert "const HEAVY_CREDITS_30D = 9_720;" in code
    heavy = code[code.index('key: "heavy"') :]
    heavy = heavy[: heavy.index("},")]
    assert "!s.ent && s.credits_30d >= HEAVY_CREDITS_30D" in heavy
    assert "${num(HEAVY_CREDITS_30D)} 크레딧 이상" in heavy
    assert "3000" not in heavy and "3,000" not in heavy


def test_the_screen_shows_credits_and_never_converts_them_to_minutes():
    """크레딧을 분으로 바꿔 적지 않는다 — 크레딧과 분의 비율은 더빙 모델마다 달라 「60크레딧 = 1분」은
    정확하지 않다 (2026-10-14 운영자: 「분 환산은 더빙 모델마다 달라서 정확하지 않아」)."""
    code = _code()
    assert "mins(" not in code
    assert not re.search(r"/\s*60\b", code), "크레딧을 60 으로 나누는 곳이 있습니다"
    assert not re.search(r"\d\s*분|\}분", code), "화면 글자에 「N분」이 있습니다"
