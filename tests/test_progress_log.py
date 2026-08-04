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
    # The failure is a different sentence and needs a person.
    assert "auto_ack_failed" in inbound


def test_a_failed_acknowledgement_is_still_on_the_log():
    from src.api.routes.messages import _ROUTINE_PROGRESS_KINDS

    assert "auto_ack_failed" not in _ROUTINE_PROGRESS_KINDS
    assert "draft_retired" not in _ROUTINE_PROGRESS_KINDS


def test_a_draft_retired_by_hubspot_is_not_hidden_with_the_routine_ones():
    """It used to share the "draft" kind with the routine entry. A draft cancelled out
    from under the operator is the opposite of routine."""
    import pathlib

    stage_sync = pathlib.Path("src/agents/stage_sync.py").read_text(encoding="utf-8")
    assert '"draft_retired"' in stage_sync


def test_hiding_is_a_read_filter_so_the_rows_survive():
    """Progress rows are append-only (CLAUDE.md). Hiding is what the screen does, not
    what the database does — the rows still explain a support question later."""
    import pathlib

    source = pathlib.Path("src/api/routes/messages.py").read_text(encoding="utf-8")
    assert "kind.not_in(_ROUTINE_PROGRESS_KINDS)" in source
    # Nothing anywhere removes a progress row.
    assert "delete(ConversationProgress" not in source
    assert "session.delete" not in source
