"""`public` 스키마의 표에 RLS 를 켭니다 — Supabase 의 공개 API 를 막는 자물쇠.

**왜 (2026-09-09, Supabase 보안 경고 `rls_disabled_in_public`).** 이 DB 는 Supabase 이고,
Supabase 프로젝트는 `public` 스키마를 **REST/GraphQL API 로 자동 공개**합니다. 우리는 그
API 를 한 줄도 안 쓰지만 **안 쓰는 것과 안 열려 있는 것은 다른 이야기**입니다: 그 API 의
`anon` 역할은 RLS 가 없는 표를 전부 읽고 쓰고 지울 수 있고, 그 표에는 고객 이메일 본문과
계약 금액이 들어 있습니다. `anon` 키는 원래 클라이언트에 박아 배포하라고 만든 값이라
**공개된 것으로 간주해야 합니다.**

**앱은 영향을 안 받습니다.** RLS 를 우회하는 것은 슈퍼유저 · `BYPASSRLS` · **그리고 표의
소유자**인데(`FORCE ROW LEVEL SECURITY` 를 걸지 않는 한), 이 앱의 `DATABASE_URL` 역할이
바로 그 표들을 만든 역할입니다 — 이관이 `CREATE TABLE` 을 그 접속으로 실행합니다.

**그래도 확인하고 켭니다.** 만약 소유자가 아닌 역할로 붙는 배포가 있다면, RLS 를 켜는
순간 그 배포는 **모든 조회가 0행**이 됩니다 — 에러도 없이 콘솔이 통째로 빈 화면이 되고,
되돌리려면 DB 에 직접 붙어야 합니다. 그래서 표마다
`pg_has_role(current_user, relowner, 'USAGE')`(= Postgres 가 실제로 소유자 우회를 판단할 때
쓰는 그 검사)를 물어보고, 참인 표에만 켭니다.

**이관 파일이 아니라 여기 있는 이유**: 이관은 한 번만 돕니다. 내일 누가 표를 하나 더
만들면 그 표는 다시 열린 채로 서고, 아무도 안 알려 줍니다(Supabase 가 메일을 보낼
뿐입니다). 이 함수는 배포마다 도므로 **새 표가 생긴 다음 배포에서 저절로 닫힙니다.**

정책(policy)은 하나도 안 만듭니다. 「아무도 못 본다」가 우리가 원하는 정책이고, 정책이
없는 RLS 가 정확히 그 뜻입니다.
"""

from __future__ import annotations

import logging

from sqlalchemy import Engine, text

logger = logging.getLogger(__name__)

# 소유자 우회가 확실한, RLS 가 아직 꺼진 `public` 의 보통 표들.
# `relkind='r'` — 뷰·시퀀스·인덱스는 RLS 대상이 아닙니다.
_UNPROTECTED = text("""
    SELECT c.relname,
           pg_has_role(current_user, c.relowner, 'USAGE') AS ours
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind = 'r'
      AND NOT c.relrowsecurity
    ORDER BY c.relname
""")


# 카나리아로 쓸 표. **행이 반드시 있습니다** — 이 함수는 이관이 끝난 직후에 도는데,
# 그때 이 표에는 방금 적용한 이관 이름들이 들어 있습니다. 그래서 「켠 뒤에 읽어 보니
# 0행」이면 그것은 빈 표가 아니라 **우리가 RLS 를 우회하지 못한다**는 뜻입니다.
_CANARY = "_migrations"


def _rls_would_lock_us_out(engine: Engine) -> bool:
    """표 하나에 먼저 걸어 보고, 우리가 계속 읽을 수 있는지 확인합니다.

    **왜 이게 있나.** 소유자 우회는 Postgres 의 확정된 동작이고 위 `pg_has_role` 검사도
    그것을 그대로 묻습니다. 그래도 틀렸을 때의 대가가 「콘솔이 에러 없이 통째로 빈 화면」
    이고, **복구하려면 DB 에 직접 붙어야 하는데 사무실 망은 Postgres 를 막고 있습니다**
    (`docs` 와 운영 메모에 적힌 그 사정). 되돌릴 수 없는 자리에서는 확인이 싸게 먹힙니다.

    켜 보고 못 읽으면 **그 자리에서 다시 끕니다.** 그 되돌리기는 같은 연결에서 즉시
    일어나므로 사무실 망과 무관합니다.
    """
    count = text(f'SELECT count(*) FROM public."{_CANARY}"')
    with engine.begin() as conn:
        before = conn.scalar(count) or 0
        if before == 0:
            # 셀 것이 없으면 카나리아가 아무 말도 못 합니다 — 그냥 진행합니다.
            return False
        conn.execute(text(f'ALTER TABLE public."{_CANARY}" ENABLE ROW LEVEL SECURITY'))
        after = conn.scalar(count) or 0
        if after == before:
            return False
        conn.execute(text(f'ALTER TABLE public."{_CANARY}" DISABLE ROW LEVEL SECURITY'))
    logger.error(
        "RLS 를 켜면 이 접속이 자기 표를 못 읽습니다 (%s: %d행 → %d행). 아무 표에도 켜지 "
        "않고 그대로 둡니다 — 켰다면 콘솔이 에러 없이 빈 화면이 됐을 것입니다.",
        _CANARY, before, after,
    )
    return True


def enable_rls_on_public_tables(engine: Engine) -> list[str]:
    """RLS 를 새로 켠 표 이름들을 돌려줍니다. Postgres 가 아니면 아무 일도 안 합니다."""
    if engine.dialect.name != "postgresql":
        return []

    try:
        if _rls_would_lock_us_out(engine):
            return []
    except Exception:
        # 확인 자체가 실패하면 켜지 않습니다. 모르는 채로 잠그는 것보다 열어 두는 편이
        # 낫습니다 — 열려 있는 것은 다음 배포에서 다시 시도할 수 있지만, 잠긴 것은
        # 이 망에서 못 풉니다.
        logger.warning("RLS 사전 확인 실패 — 이번 배포에서는 켜지 않습니다.", exc_info=True)
        return []

    with engine.connect() as conn:
        rows = list(conn.execute(_UNPROTECTED))

    ours = [name for name, mine in rows if mine]
    theirs = [name for name, mine in rows if not mine]
    if theirs:
        # 켜면 우리가 못 읽게 되는 표입니다 — 사람이 판단할 일이라 이름만 알립니다.
        logger.warning(
            "RLS 를 못 켠 표 %d개 (이 접속이 소유자가 아닙니다): %s",
            len(theirs), ", ".join(theirs),
        )
    if not ours:
        return []

    done: list[str] = []
    for name in ours:
        try:
            with engine.begin() as conn:
                # 표 이름은 pg_class 에서 방금 읽은 값이라 사용자 입력이 아닙니다.
                # 그래도 큰따옴표로 감싸 대소문자·예약어 이름을 안전하게 다룹니다.
                conn.execute(text(f'ALTER TABLE public."{name}" ENABLE ROW LEVEL SECURITY'))
            done.append(name)
        except Exception:
            # 한 표가 실패해도 나머지는 닫아야 합니다.
            logger.warning("RLS 켜기 실패: %s", name, exc_info=True)
    if done:
        logger.info("RLS 를 켰습니다 (%d개): %s", len(done), ", ".join(done))
    return done
