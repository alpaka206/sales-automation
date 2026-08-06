"""그 날짜의 USD→KRW 환율.

**왜 날짜별로 저장하는가.** 입금 한 건은 그날의 환율로 원화가 정해집니다. 오늘 환율로 과거
입금을 다시 환산하면 지난달 매출이 이번 달에 바뀌고, 마감한 숫자가 흔들립니다. 그래서 결제
행에 ``fx_rate`` 와 ``fx_on`` 을 박아 두고, 이 모듈은 **그 값을 처음 채울 때만** 씁니다.

출처는 한국수출입은행 OpenAPI 의 매매기준율(최초 고시)입니다. 인증키가 필요해서
``KOREAEXIM_API_KEY`` 가 없으면 조회하지 않고 None 을 돌려줍니다 — 그러면 화면이 입력칸을
비워 두고 운영자가 직접 넣습니다. 키가 생기면 그때부터 자동으로 채워지고, **어느 쪽이든 값은
행에 남습니다.** 조회에 실패했다고 저장이 막히면 안 됩니다.

주말·공휴일에는 고시가 없습니다. 주말은 직전 금요일로 물러나고(``previous_business_day``),
공휴일은 응답이 비므로 최대 5영업일까지 하루씩 더 물러납니다.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from ..common.config import settings
from ..common.won import previous_business_day

logger = logging.getLogger(__name__)

_ENDPOINT = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"
_TIMEOUT = 8.0
# 공휴일은 달력 없이 응답이 비는 것으로 알아냅니다. 연휴가 길어야 닷새입니다.
_MAX_FALLBACK_DAYS = 5


def is_configured() -> bool:
    return bool(getattr(settings, "KOREAEXIM_API_KEY", "").strip())


def _parse(payload: list[dict]) -> Decimal | None:
    """USD 행의 매매기준율. 숫자에 천 단위 쉼표가 들어 있습니다."""
    for row in payload:
        if str(row.get("cur_unit", "")).upper().startswith("USD"):
            raw = str(row.get("deal_bas_r", "")).replace(",", "").strip()
            try:
                rate = Decimal(raw)
            except (InvalidOperation, ValueError):
                return None
            return rate if rate > 0 else None
    return None


def usd_krw_on(day: date | str) -> tuple[Decimal, str] | None:
    """``(환율, 실제 고시일)``. 조회할 수 없으면 None — 운영자가 직접 넣습니다.

    돌려주는 날짜가 요청한 날짜와 다를 수 있습니다(주말·공휴일). 실제로 어느 날 고시가를
    썼는지가 나중에 숫자를 설명하는 유일한 단서라, 행에 같이 저장합니다.
    """
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day[:10])
        except ValueError:
            return None
    if not is_configured():
        return None

    import httpx

    target = previous_business_day(day)
    for _ in range(_MAX_FALLBACK_DAYS):
        try:
            response = httpx.get(
                _ENDPOINT,
                params={
                    "authkey": settings.KOREAEXIM_API_KEY.strip(),
                    "searchdate": target.strftime("%Y%m%d"),
                    "data": "AP01",
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            logger.warning("환율 조회 실패 (%s).", target, exc_info=True)
            return None
        if isinstance(payload, list) and payload:
            rate = _parse(payload)
            if rate is not None:
                return rate, target.isoformat()
        # 빈 응답 = 그날 고시가 없음(공휴일). 하루 물러나 다시 영업일로 맞춥니다.
        target = previous_business_day(target - timedelta(days=1))
    logger.info("환율 고시를 %d영업일 안에서 찾지 못했습니다 (%s).", _MAX_FALLBACK_DAYS, day)
    return None
