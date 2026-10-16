"""영업 인사이트(안쪽 겹 `frontend/src/screens/insights/`) — 무엇을 후보로 세우나, 숫자를 어떻게 적나.

값은 화면이 브라우저에서 가공 파일(`sales.json` · `summary.json`)로 판정하므로(서버는 그 데이터를 모른다) 여기서는
화면 소스를 읽는다. 판정 자체의 숫자는 `frontend/test/insights.test.ts` 가, 기준값의 저장은 `tests/test_insights.py`
가 본다.

2026-10-15 운영자 지시로 목업(업셀링 · 이탈 방지 · 추가 계약 기회)대로 바뀌었다. 예전 「주목할 스페이스」의 신호
여섯 중 「셀프서브 대량 사용」만 업셀링 카드로 남았다(2026-10-16 운영자 확인 ②).
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
FOLDER = ROOT / "frontend" / "src" / "screens" / "insights"
JUDGE = FOLDER / "judge.ts"


def _code(path: pathlib.Path) -> str:
    """주석을 걷어낸 소스 — 바꾼 이유를 적은 주석이 옛 값을 들고 있어도 걸리지 않게."""
    source = path.read_text(encoding="utf-8")
    return re.sub(r"//.*", "", re.sub(r"/\*.*?\*/", "", source, flags=re.S))


def _all_code() -> str:
    return "\n".join(_code(path) for path in sorted(FOLDER.glob("*.ts*")))


def test_upsell_is_a_pro_space_over_the_stored_threshold_that_is_not_ours():
    """업셀링 후보 = 엔터프라이즈가 아닌 **Pro 플랜** 스페이스가 30일 크레딧 **기준 이상** + 우리 수주 고객이 아님.

    기준(기본 9,720 = PRO 월 10,800 크레딧의 90%, 2026-10-14 운영자)은 이제 **서버에 저장된 값**이라 화면에 숫자가
    없다 — 화면에 남아 있으면 「기준 수정」으로 바꿔도 그 숫자로 판정하는 자리가 생긴다.
    """
    judge = _code(JUDGE)
    assert 'export const PRO_TIER = "pro";' in judge
    assert "!s.ent && s.tier === PRO_TIER && s.credits_30d >= rules.upsellCredits30d && !ours.has(s.space_seq)" in judge
    everything = _all_code()
    for stale in ("9720", "9_720", "9,720", "HEAVY_CREDITS_30D"):
        assert stale not in everything, f"업셀링 문턱이 화면에 박혀 있습니다: {stale}"


def test_the_old_signals_are_gone():
    """목업에 없는 신호 다섯은 지웠다(2026-10-16 운영자 확인 ②) — 「여럿이 쓴다」는 그 전에(10-14) 지웠다."""
    everything = _all_code()
    for label in ("엔터프라이즈 · 우리 기록 없음", "트라이얼인데 많이 쓴다", "구독 연체", "무료인데 활발", "실패가 잦다",
                  "여럿이 쓴다", "주목할 스페이스"):
        assert label not in everything, label


def test_the_candidate_cap_is_the_exporters_limit():
    """가공기는 후보를 300곳까지만 싣는다(`agent/sales.go` 의 picked `LIMIT 300`). 화면이 그 상한에 닿았을 때 「빠졌을 수
    있음」을 다는데, 두 숫자가 어긋나면 그 경고가 엉뚱한 때에 뜨거나 안 뜬다."""
    sales = (ROOT / "agent" / "sales.go").read_text(encoding="utf-8")
    picked = re.search(r"picked AS \((.*?)\)\s*\nSELECT", sales, re.S)
    assert picked, "agent/sales.go 의 picked 가 바뀌었습니다 — 이 테스트도 고쳐야 합니다"
    limit = re.search(r"LIMIT (\d+)", picked.group(1))
    assert limit and f"export const CANDIDATE_CAP = {limit.group(1)};" in _code(JUDGE)


def test_contracts_are_judged_by_the_same_function_as_the_won_screen():
    """이탈 방지 · 추가 계약의 사용 판정은 수주 고객 「사용 진단」과 **같은 함수**(`usageFor` → `diagnose`)다 — 운영자
    조건 1. 이 폴더가 판정을 따로 세우기 시작하면(소진율 · 경과율을 스스로 나누면) 두 화면이 같은 고객을 다르게
    부른다."""
    judge = _code(JUDGE)
    assert 'import { usageFor, type UsageIndex } from "../won/useUsage";' in judge
    assert "usageFor(c, index, rules)" in judge
    for own in ("used_total /", "monthsLeft", "30.4"):
        assert own not in _all_code(), f"이 폴더가 판정을 스스로 셉니다: {own}"


def test_the_screen_shows_credits_and_never_converts_them_to_minutes():
    """크레딧을 분으로 바꿔 적지 않는다 — 크레딧과 분의 비율은 더빙 모델마다 달라 「60크레딧 = 1분」은
    정확하지 않다 (2026-10-14 운영자: 「분 환산은 더빙 모델마다 달라서 정확하지 않아」)."""
    code = _all_code()
    assert "mins(" not in code
    assert not re.search(r"/\s*60\b", code), "크레딧을 60 으로 나누는 곳이 있습니다"
    assert not re.search(r"\d\s*분|\}분", code), "화면 글자에 「N분」이 있습니다"
