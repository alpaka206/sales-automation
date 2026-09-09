"""메모리를 재고, 남는 것을 운영체제에 돌려줍니다.

**왜 있나 (2026-09-09 실측).** 이 서비스는 Render 무료 인스턴스(512Mi)에서 **실제로 두 번
OOM 으로 죽었습니다** — 2026-09-07 12:57 과 2026-09-08 14:24, Render 이벤트에
``oomKilled: {memoryLimit: "512Mi"}`` 로 남아 있습니다. 「메모리 경고 메일」이 아니라
프로세스가 죽고 재시작한 것입니다.

곡선이 원인을 말해 줍니다(60초 해상도):

    13:40 ~ 14:22   350.0 MB   45분 동안 소수점까지 평평, CPU 0
    14:22:23        Vertex(Gemini) 호출 한 건
    14:23           487.4 MB   ← 1분 만에 +137 MB
    14:24:49        OOM
    14:26            344 MB    재시작

즉 **새는 것이 아닙니다** — 평평하니까요. 일하는 순간 크게 부풀고, 그 봉우리가 **안
내려옵니다.** 갓 뜬 인스턴스는 120~135 MB 인데 하루 지나면 350 MB 에서 평평해지고, 그
위에 초안 한 건(약 +137 MB)이 얹히면 512 를 넘습니다.

여기서 하는 일은 셋입니다.

1. **재기**(`rss_bytes`) — 지금까지 이 앱은 자기 메모리를 한 번도 안 적었습니다. 위
   숫자는 전부 Render API 로 밖에서 본 것이고, 그건 사고가 난 **뒤에** 보는 값입니다.
2. **돌려주기**(`release`) — `gc.collect()` 뒤 glibc `malloc_trim(0)`. 파이썬이 객체를
   놓아도 glibc 는 그 페이지를 자기 힙에 쥐고 있습니다. 그래서 「평평한 350 MB」의
   상당 부분이 **이미 비어 있는데 반납 안 된 자리**입니다.
3. **일하는 실 수 줄이기**(`cap_thread_pool`) — 아래.

`MALLOC_ARENA_MAX` 는 코드가 아니라 환경변수라 `render.yaml` 에 있습니다. glibc 는 실마다
따로 아레나를 만들고(64비트에서 하나가 최대 64 MB), 그 아레나들은 서로 반납을 안 도와
줍니다 — 실이 여덟이면 빈 자리가 여덟 군데로 흩어집니다.
"""

from __future__ import annotations

import ctypes
import gc
import logging
import os
import sys

logger = logging.getLogger(__name__)

_malloc_trim = None
if sys.platform.startswith("linux"):
    try:  # glibc 에만 있습니다 — musl(Alpine)에는 없고, 없으면 그냥 안 부릅니다.
        _malloc_trim = ctypes.CDLL("libc.so.6").malloc_trim
        _malloc_trim.argtypes = [ctypes.c_size_t]
        _malloc_trim.restype = ctypes.c_int
    except (OSError, AttributeError):
        _malloc_trim = None


def rss_bytes() -> int:
    """지금 이 프로세스가 잡고 있는 물리 메모리. 모르면 0.

    리눅스는 `/proc/self/statm` 두 번째 값(페이지 수)입니다 — `psutil` 을 의존성에
    더하지 않으려고 직접 읽습니다. 개발 기계(Windows)에서는 psapi 를 부릅니다.
    """
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/self/statm", encoding="ascii") as handle:
                pages = int(handle.read().split()[1])
            return pages * os.sysconf("SC_PAGE_SIZE")
        if sys.platform == "win32":
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            probe = ctypes.windll.kernel32.K32GetProcessMemoryInfo
            probe.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
            probe.restype = wintypes.BOOL
            counters = _PMC()
            counters.cb = ctypes.sizeof(_PMC)
            if not probe(ctypes.windll.kernel32.GetCurrentProcess(),
                         ctypes.byref(counters), counters.cb):
                return 0
            return int(counters.WorkingSetSize)
    except Exception:  # 재는 일이 앱을 멈추면 안 됩니다
        pass
    return 0


def release(label: str = "") -> int:
    """놓아준 메모리를 운영체제에 돌려주고, 줄어든 바이트를 돌려줍니다 (음수면 늘었음).

    **부르는 자리는 「회차가 끝난 곳」입니다** — 초안 한 건이 끝난 뒤, 폴러 한 바퀴가
    끝난 뒤. 요청 처리 중간에 부르면 안 됩니다: `malloc_trim` 은 힙 잠금을 잡습니다.

    큰 회수가 있을 때만 로그에 적습니다. 평소에 0에 가까운 줄을 10분마다 남기면 그게
    또 하나의 `/healthz` 가 됩니다.
    """
    before = rss_bytes()
    gc.collect()
    if _malloc_trim is not None:
        try:
            _malloc_trim(0)
        except Exception:
            pass
    after = rss_bytes()
    freed = before - after
    if freed >= 32 * 1024 * 1024:
        logger.info(
            "메모리 %d MB 반납 (%d → %d MB)%s",
            freed // (1024 * 1024), before // (1024 * 1024), after // (1024 * 1024),
            f" — {label}" if label else "",
        )
    return freed


def cap_thread_pool(max_workers: int = 4) -> None:
    """`asyncio.to_thread` 가 쓰는 기본 실 묶음의 상한을 정합니다.

    기본값은 ``min(32, os.cpu_count() + 4)`` 인데, **컨테이너 안에서 `cpu_count()` 는 그
    컨테이너의 몫이 아니라 호스트의 코어 수**를 돌려줍니다. 0.1 CPU 짜리 무료 인스턴스가
    실 열두 개까지 자랄 수 있다는 뜻이고, 그렇게 자라면 ① 빨라지지 않으면서(CPU 가
    없으니) ② glibc 아레나가 실마다 하나씩 생기고 ③ DB 커넥션을 그만큼 동시에 잡습니다.

    이 앱의 `to_thread` 는 전부 **기다리는 일**(DB·HTTP)이라 넷이면 넉넉합니다.

    **동기 라우트 핸들러는 여기 안 옵니다** — Starlette 은 `anyio` 의 자체 실 묶음을
    씁니다(기본 상한 40). 그쪽은 동시 요청 수만큼만 자라고 이 콘솔은 사람이 한둘이라
    상한에 안 닿습니다. 아레나 쪽은 `MALLOC_ARENA_MAX` 가 이미 막습니다.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="io")
    )
    logger.info(
        "시작: RSS %d MB · CPU %s개로 보임 · 작업 실 %d개로 제한",
        rss_bytes() // (1024 * 1024), os.cpu_count(), max_workers,
    )
