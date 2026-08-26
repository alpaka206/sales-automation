"""처리 경과 — what the operator needs to read back, and what is only machine noise.

The log is the answer to "what happened with this customer": the mail went out, then we
met them and this is what they asked for. Entries about what the app did to itself —
a draft finished, an acknowledgement dispatched — restate what the same screen already
shows and push the two real events apart.
"""

from __future__ import annotations

# ---- 처리 경과 -----------------------------------------------------------------------


def test_the_routine_machine_entries_are_not_written_any_more():
    """"AI 회신 초안 작성 완료. 검토 대기." is what the pending_approval status already
    says on the same screen, and a successful auto-acknowledgement IS the first outgoing
    message in the thread above it."""
    import pathlib

    inbound = pathlib.Path("src/agents/inbound.py").read_text(encoding="utf-8")
    assert "AI 회신 초안 작성 완료" not in inbound
    assert "자동 접수확인 메일 발송됨" not in inbound
    assert "auto_ack_failed" not in inbound


def test_a_stage_move_leaves_no_progress_row():
    """단계 이동은 기록으로 남기지 않습니다 (2026-08-20 운영자 지시).

    옮겨지면 우리 DB·워크북·허브스팟의 **상태만** 바뀌면 됩니다. 예전에는 단계 이동과
    「대기 중이던 초안을 종료했습니다」를 진행 기록에 한 줄씩 남겼는데, 앞엣것은 화면에서
    이미 숨기고 있었고(지금 단계는 Stage 칸이 보여 줍니다) 뒤엣것은 이 고객과 오간 일이
    아니라 우리 안의 사정입니다. 히스토리는 「무엇이 오갔나」를 보는 자리입니다.

    이 검사는 그 두 줄이 되살아나는 것을 막습니다 — 지우는 것보다 다시 쓰기 시작하는
    쪽이 쉽고, 그러면 대화마다 아무도 안 읽는 줄이 다시 쌓입니다.
    """
    import pathlib

    stage_sync = pathlib.Path("src/agents/stage_sync.py").read_text(encoding="utf-8")
    assert "add_progress" not in stage_sync
    assert '"draft_retired"' not in stage_sync


def test_hiding_is_a_read_filter_so_the_rows_survive():
    """Progress rows are append-only (CLAUDE.md). Hiding is what the screen does, not
    what the database does — the rows still explain a support question later."""
    import pathlib

    source = pathlib.Path("src/api/routes/messages.py").read_text(encoding="utf-8")
    assert "kind.not_in(ROUTINE_PROGRESS_KINDS)" in source
    # Nothing anywhere removes a progress row.
    assert "delete(ConversationProgress" not in source
    assert "session.delete" not in source


def test_every_screen_that_reads_progress_uses_the_same_filter():
    """목록이 한 곳에 있고, 읽는 화면이 전부 그것을 쓴다.

    예전에는 목록이 ``messages.py`` 안에만 있어서 티켓 세부 내역만 걸러졌고, 고객 상세는
    같은 행을 ``kind`` 문자열까지 그대로 찍었다. 화면마다 목록을 따로 들면 다음에 종류가
    하나 늘 때 한 화면만 조용히 빠진다.
    """
    import pathlib

    from src.db.conversation_history import ROUTINE_PROGRESS_KINDS

    for path in ("src/api/routes/messages.py", "src/api/routes/customer_ops.py"):
        source = pathlib.Path(path).read_text(encoding="utf-8")
        assert "select(ConversationProgress)" in source, path
        assert "kind.not_in(ROUTINE_PROGRESS_KINDS)" in source, path

    # 「답변 발송 완료: <제목>」은 그 메일 줄이 바로 옆에 있을 때만 그려지는 카드에 산다.
    assert "reply" in ROUTINE_PROGRESS_KINDS
