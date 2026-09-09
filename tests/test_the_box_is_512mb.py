"""무료 인스턴스(512Mi) 안에서 살기 위한 장치들.

**이 파일은 추측이 아니라 사고 기록입니다.** 2026-09-09 에 Render API 로 실제 지표와
이벤트를 읽었고, 이 서비스는 **두 번 OOM 으로 죽어 있었습니다** —
2026-09-07 12:57 · 2026-09-08 14:24, 둘 다 ``oomKilled: {memoryLimit: "512Mi"}``.
"""

from __future__ import annotations

import logging

import pytest

from src.common.logging import QuietHealthChecks
from src.common.memory import cap_thread_pool, release, rss_bytes


def _access_record(path: str, status: int) -> logging.LogRecord:
    """uvicorn 의 액세스 로그 레코드 모양 그대로 — 인자 다섯 개 튜플."""
    return logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname=__file__, lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("10.0.0.1:1234", "GET", path, "1.1", status), exc_info=None,
    )


def test_a_successful_health_check_does_not_reach_the_log():
    """**실측: 한 시간치 로그 1,265줄 중 861줄(68%)이 `/healthz` 였습니다.**

    플랫폼이 약 4.2초마다 두드리고(하루 약 17,000번) 외부 업타임 모니터 여덟 곳이 더
    붙습니다. 그 줄들이 로그를 채우면 정작 무슨 일이 났는지는 세 페이지를 넘겨야
    보이고, 이 앱의 운영 진단은 사실상 그 로그뿐입니다.
    """
    quiet = QuietHealthChecks()

    assert quiet.filter(_access_record("/healthz", 200)) is False
    assert quiet.filter(_access_record("/healthz", 204)) is False


def test_a_failing_health_check_still_reaches_the_log():
    """**끄는 것은 200 뿐입니다.** 헬스 체크가 실패하는 줄이야말로 보고 싶은 줄입니다 —
    그게 안 보이면 「배포가 왜 안 올라가나」를 로그로는 영영 알 수 없습니다."""
    quiet = QuietHealthChecks()

    assert quiet.filter(_access_record("/healthz", 503)) is True
    # 다른 경로는 상태와 무관하게 그대로 남습니다.
    assert quiet.filter(_access_record("/api/ui/tickets/1", 200)) is True
    # 모양이 다른 레코드(우리 앱 로그 등)는 건드리지 않습니다.
    other = logging.LogRecord("src.agents.poller", logging.INFO, __file__, 1,
                              "/healthz 를 언급하는 평범한 줄", None, None)
    assert quiet.filter(other) is True


def test_the_process_can_measure_and_hand_back_its_own_memory():
    """**이 앱은 자기 메모리를 한 번도 안 적었습니다.** 위의 숫자들은 전부 Render API 로
    밖에서 본 값이고, 그건 죽고 **난 뒤에** 보는 값입니다.

    `release()` 는 놓아준 자리를 운영체제에 돌려줍니다(glibc `malloc_trim`). 실측 곡선이
    이유입니다: 45분 동안 350.0 MB 로 소수점까지 평평하다가 Vertex 호출 한 건에 487 MB,
    2분 뒤 OOM. 평평했다는 것은 **새는 게 아니라는 뜻**이고, 그러면 남는 것은 「부푼
    자리가 안 내려온다」 하나입니다.
    """
    assert rss_bytes() > 0, "이 플랫폼에서 RSS 를 못 재면 진단 자체가 불가능합니다"
    # 어느 OS 에서도 예외 없이 정수를 돌려줍니다 — glibc 가 아니면 `gc.collect()` 만 합니다.
    assert isinstance(release("테스트"), int)


@pytest.mark.asyncio
async def test_the_worker_threads_are_capped():
    """기본값 ``min(32, cpu_count()+4)`` 는 **컨테이너 밖의 코어 수**를 셉니다.

    0.1 CPU 인스턴스에서 실 열둘을 만들어 두면 빨라지지도 않으면서 glibc 아레나와 DB
    커넥션만 그만큼 잡습니다. 이 앱의 `to_thread` 는 전부 기다리는 일입니다.
    """
    import asyncio

    cap_thread_pool(4)
    executor = asyncio.get_running_loop()._default_executor
    assert executor is not None
    assert executor._max_workers == 4


def test_a_google_failure_never_locks_the_operator_out_of_the_console():
    """**로그인 뒤 착지점은 우리 화면이어야 합니다** (2026-09-09 실측 사고).

    로그인이 끝나면 메일함 동의를 아직 안 한 사람을 그 동의로 보냅니다. 예전에는 구글
    동의 URL 로 **곧장** 튕겼는데, 그 요청이 구글에서 거절되면(그날은
    `redirect_uri_mismatch` — 콜백 주소가 구글 콘솔에 등록돼 있지 않았습니다) 화면에는
    구글의 오류만 남고 돌아올 링크가 없습니다. **다시 로그인해도 같은 자리로 튕깁니다** —
    운영 로그에 로그인 성공 4회가 1분 안에 찍혀 있고 전부 그 오류였습니다.

    이제 착지점은 콘솔의 메일함 화면이고, 동의는 거기서 버튼으로 시작합니다. 저쪽이
    거절해도 사람은 콘솔 안에 남습니다.
    """
    import pathlib

    source = pathlib.Path("src/api/auth.py").read_text(encoding="utf-8")
    landing = source[source.index('next_url = "/"'):]
    landing = landing[: landing.index("resp = RedirectResponse")]

    assert '"/app/settings/mailboxes"' in landing
    assert "self-connect" not in landing, "로그인 착지점이 구글이면 저쪽 실패가 곧 잠금입니다"
