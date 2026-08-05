"""Engine and SessionLocal factory."""

from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from ..common.config import settings


def _normalize_url(url: str) -> str:
    """
    Normalize DATABASE_URL for SQLAlchemy 2.x.

    Render/Supabase/Heroku hand out `postgres://...` URLs but SQLAlchemy 2.x
    rejects them — it expects the explicit `postgresql://` scheme.
    """
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


_url = _normalize_url(settings.DATABASE_URL)
_is_sqlite = _url.startswith("sqlite")

# 이 서비스와 데이터베이스는 **다른 대륙에 있습니다** — 앱은 Oregon(render.yaml 에 region 이
# 없어 Render 기본값), DB 는 ap-northeast-1(도쿄). 그래서 쿼리 한 번이 태평양을 건넙니다.
# 2026-08-05 실측(서울에서 배포본으로, TTFB):
#
#     정적 파일 (DB 없음)              230 ~ 270 ms
#     /healthz (SELECT 1 한 번)        630 ~ 880 ms
#
# 차이가 400ms 이고 그게 왕복 두 번입니다 — 하나는 아래 pre_ping, 하나는 진짜 쿼리. 즉 DB
# 왕복 하나에 약 200ms 입니다. 화면이 느린 진짜 이유는 쿼리가 무거워서가 아니라 이것이고,
# 제대로 고치려면 앱과 DB 를 같은 리전에 두어야 합니다(둘 다 생성 후 리전 변경 불가 →
# 서비스나 프로젝트를 새로 만들어야 하는 운영 결정입니다).
#
# 그때까지 코드로 줄일 수 있는 절반이 이것입니다. ``pool_pre_ping`` 은 커넥션을 꺼낼 때마다
# "SELECT 1" 을 한 번 더 보냅니다 — 옆방 DB 라면 공짜지만 태평양 건너에서는 매 요청에
# 200ms 입니다. 유휴 커넥션이 끊겨 있을 때 그 확인이 하던 일은 ``pool_recycle`` 이 대신
#합니다: 일정 시간 지난 커넥션은 확인하지 않고 그냥 버립니다(왕복 0회).
#
# 되돌리려면 pool_pre_ping=True 로 두면 됩니다. 그 대가가 위의 200ms 입니다.
_POOL_RECYCLE_SECONDS = 180

engine = create_engine(
    _url,
    future=True,
    echo=False,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_recycle=-1 if _is_sqlite else _POOL_RECYCLE_SECONDS,
)


def _sqlite_pragmas(dbapi_conn, _connection_record):
    """Set performance and safety PRAGMAs for SQLite connections."""
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
    finally:
        cur.close()


if _is_sqlite:
    event.listens_for(engine, "connect")(_sqlite_pragmas)


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
