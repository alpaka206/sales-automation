"""그 날짜의 USD→KRW 환율. **인증 없이** 동작합니다.

**왜 날짜별로 저장하는가.** 입금 한 건은 그날의 환율로 원화가 정해집니다. 오늘 환율로 과거
입금을 다시 환산하면 지난달 매출이 이번 달에 바뀌고, 마감한 숫자가 흔들립니다. 그래서 결제
행에 ``fx_rate`` 와 ``fx_on`` 을 박아 두고, 이 모듈은 **그 값을 처음 채울 때만** 씁니다.

출처가 둘입니다:

1. **한국수출입은행** 매매기준율 — ``KOREAEXIM_API_KEY`` 가 있을 때만. 국내 계약의 기준이
   되는 최초 고시가라 이쪽이 우선입니다.
2. **Frankfurter** (ECB 기준환율) — 키가 필요 없습니다. 기본값이고, 키를 발급받지 않아도
   환율이 자동으로 채워집니다.

두 값은 조금 다릅니다(ECB 기준환율 ≠ 국내 은행 최초고시). 그래서 어느 쪽에서 왔는지와
실제 고시일을 함께 돌려주고, 화면에서 손으로 고칠 수 있습니다 — 조회는 편의이지 진실이
아닙니다.

주말·공휴일에는 고시가 없습니다. 두 출처 모두 직전 영업일로 물러나고, 실제로 어느 날짜의
값을 썼는지를 함께 알려줍니다.

**한국에서 낮에 보면 거의 항상 "어제 값" 입니다. 정상입니다.** ECB 는 유럽 시간 오후 4시경
하루 한 번 내는데, 그때가 KST 로는 밤 11시~자정입니다. 그래서 8월 6일 낮에 물어도 8월 5일
고시가가 돌아옵니다(``latest`` 를 물어도 같습니다). 화면이 고시일을 같이 적는 이유가 이것이고,
오늘 값인 척하지 않는 편이 맞습니다 — 하루 차이로 예상 MRR 의 판단이 바뀌지는 않습니다.

국내 최초고시(아침에 나옵니다)가 필요하면 ``KOREAEXIM_API_KEY`` 를 넣으면 됩니다. 그러면
아래 우선순위에 따라 그쪽을 먼저 씁니다.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from time import monotonic

from ..common.config import settings
from ..common.won import previous_business_day

logger = logging.getLogger(__name__)

_KOREAEXIM = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"
# 인증 없이 과거 날짜를 조회할 수 있고, 요청한 날에 고시가 없으면 직전 영업일 값과 그 날짜를
# 함께 돌려줍니다 — 주말·공휴일 처리를 우리가 다시 짤 필요가 없습니다.
_FRANKFURTER = "https://api.frankfurter.dev/v1/{day}"
_TIMEOUT = 8.0
_MAX_FALLBACK_DAYS = 5


def _to_date(day: date | str) -> date | None:
    if isinstance(day, date):
        return day
    try:
        return date.fromisoformat(str(day)[:10])
    except ValueError:
        return None


def _decimal(value) -> Decimal | None:
    try:
        rate = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return rate if rate > 0 else None


def _remember_error(source: str, day: date, exc: Exception) -> None:
    """왜 실패했는지 한 줄로 남깁니다. 화면이 「설정값」 옆에 이걸 적습니다."""
    global _last_error
    _last_error = f"{source} {day.isoformat()}: {type(exc).__name__} {exc}"[:200]
    logger.warning("환율 조회 실패 — %s", _last_error, exc_info=True)


def _koreaexim(day: date) -> tuple[Decimal, str, str] | None:
    """매매기준율. 응답이 비면 그날 고시가 없다는 뜻이라 하루씩 물러납니다."""
    import httpx

    target = previous_business_day(day)
    for _ in range(_MAX_FALLBACK_DAYS):
        try:
            response = httpx.get(
                _KOREAEXIM,
                params={
                    "authkey": settings.KOREAEXIM_API_KEY.strip(),
                    "searchdate": target.strftime("%Y%m%d"),
                    "data": "AP01",
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            _remember_error("수출입은행", target, exc)
            return None
        if isinstance(payload, list) and payload:
            for row in payload:
                if str(row.get("cur_unit", "")).upper().startswith("USD"):
                    rate = _decimal(row.get("deal_bas_r"))
                    if rate is not None:
                        return rate, target.isoformat(), "koreaexim"
        target = previous_business_day(target - timedelta(days=1))
    return None


def _frankfurter(day: date) -> tuple[Decimal, str, str] | None:
    """ECB 기준환율. 인증이 필요 없고, 고시가 없는 날은 직전 영업일 값을 돌려줍니다."""
    import httpx

    try:
        response = httpx.get(
            _FRANKFURTER.format(day=day.isoformat()),
            params={"from": "USD", "to": "KRW"},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        _remember_error("Frankfurter", day, exc)
        return None
    rate = _decimal((payload.get("rates") or {}).get("KRW"))
    if rate is None:
        # 응답은 왔는데 KRW 가 없는 경우. 망 문제와 전혀 다른 원인이라 따로 적습니다.
        _remember_error("Frankfurter", day, ValueError(f"KRW 없음: {str(payload)[:120]}"))
        return None
    # 요청한 날이 아니라 **실제 고시일**을 돌려줍니다. 나중에 숫자를 설명하는 단서입니다.
    return rate, str(payload.get("date") or day.isoformat()), "ecb"


def usd_krw_on(day: date | str) -> tuple[Decimal, str, str] | None:
    """``(환율, 실제 고시일, 출처)``. 못 가져오면 None — 운영자가 직접 넣습니다.

    수출입은행 키가 있으면 그쪽을 먼저 씁니다. 실패하거나 키가 없으면 ECB 로 떨어집니다:
    조회가 아예 안 되는 것보다 **조금 다른 기준의 값이라도 채워 두고 고칠 수 있게** 하는
    편이 낫습니다. 비어 있으면 수금율이 통화별로 갈린 채 남습니다.
    """
    target = _to_date(day)
    if target is None:
        return None
    if getattr(settings, "KOREAEXIM_API_KEY", "").strip():
        found = _koreaexim(target)
        if found:
            return found
    return _frankfurter(target)


# 오늘 환율은 화면을 열 때마다 필요합니다. 목록 한 번에 외부 호출 한 번이면 목록이 그
# 응답만큼 느려지므로, **성공한 값만** 날짜별로 담아 두고 재사용합니다.
_today_cache: dict[str, tuple[Decimal, str, str]] = {}

# **실패는 하루치로 굳히지 않습니다.** 예전에는 결과를 그대로 담았는데, 그러면 아침에
# 한 번 삐끗한 것이 그날 내내 「설정값」이 되고 프로세스를 재시작하기 전까지 안 풀렸습니다.
# 조회 한 번이 실패하는 이유는 대개 그때뿐인 것(콜드 스타트, 타임아웃)이라, 조금 있다 다시
# 물어보면 됩니다. 그렇다고 화면을 열 때마다 부르면 안 되는 날에는 매 요청이 8초씩 밀립니다.
_RETRY_AFTER_SECONDS = 600.0
_last_attempt: float = 0.0
_last_error: str | None = None


def last_error() -> str | None:
    """직전 조회가 **왜** 실패했는지 한 줄. 화면이 「설정값」 옆에 적습니다.

    이유를 안 남기면 「설정값」이 막다른 길이 됩니다 — 망 문제인지, 응답이 바뀐 것인지,
    키가 죽은 것인지 아무도 모르고, 매번 사람이 코드를 열어 봐야 합니다.
    """
    return _last_error


def usd_krw_today() -> tuple[Decimal, str, str] | None:
    """예상 MRR 이 USD 계약을 원화로 환산할 때 쓰는 값.

    운영자가 손으로 적던 칸을 대신합니다. 손으로 적으면 두 사람이 다른 숫자를 보고 회의에
    들어가고, 아무도 그 값이 언제 것인지 모릅니다. 실제 고시일을 같이 돌려주는 이유입니다.

    **과거 입금액에는 쓰지 않습니다** — 그건 결제 행에 그날 환율이 박혀 있습니다.
    """
    global _last_attempt, _last_error

    key = date.today().isoformat()
    cached = _today_cache.get(key)
    if cached is not None:
        return cached
    if _last_attempt and monotonic() - _last_attempt < _RETRY_AFTER_SECONDS:
        return None
    _last_attempt = monotonic()
    found = usd_krw_on(date.today())
    if found is None:
        return None
    _last_error = None
    _today_cache[key] = found
    return found
