"""영업 인사이트 메일 초안의 기본 문구 — 화면에서 고치고, 서버의 따로 둔 표에 산다 (2026-10-16 운영자 지시).

운영자의 조건 그대로:
- 저장은 insight_rules 처럼 별도 표에. 기존 이메일 템플릿 표와 분리해서 인바운드 답장 AI 가 영업 초안을 읽지 않게.
- {고객명} 같은 자리표시는 유지하고, 쓸 수 있는 자리표시를 안내.
- 누가 언제 고쳤는지 기록(기준 수정과 같은 방식). ① {근거} 가 없거나 두 번이면 · ② 모르는 자리표시면 저장 거절,
  ③ 기본 문구로 되돌릴 수 있고, ④ 기록에 바꾸기 전 글을 남긴다.
- 초기값은 목업 문구 그대로, 회사 이름만 「Perso Dubbing」 · 「Perso Dubbing 팀」으로.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.insight_drafts import DEFAULTS, NAME, REASON, REASONS, parts
from src.db.models import InsightDraft, InsightLog

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def factory():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def console(factory):
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.api.routes import insights

    with patch.object(insights, "SessionLocal", factory), \
         patch("src.db.session.SessionLocal", factory), TestClient(app) as client:
        yield client


def _parts(console) -> dict[tuple[str, str, str], dict]:
    answer = console.get("/api/ui/insight-drafts")
    assert answer.status_code == 200, answer.text
    return {(p["tab"], p["lang"], p["part"]): p for p in answer.json()["parts"]}


def _full(tab: str, lang: str) -> dict[str, str]:
    """그 카드 · 언어의 모든 칸을 기본 문구로 — 편집 창이 보내는 모양(``ko.subject`` …)."""
    return {f"{lang}.{part}": text for part, text in DEFAULTS[(tab, lang)].items()}


# --------------------------------------------------------------------------- #
# 기본 문구
# --------------------------------------------------------------------------- #
def test_every_tab_and_language_has_a_subject_a_frame_and_its_reasons():
    for tab, reasons in REASONS.items():
        for lang in ("ko", "en"):
            assert set(DEFAULTS[(tab, lang)]) == {part for part, _ in parts(tab)}, (tab, lang)
            body = DEFAULTS[(tab, lang)]["body"]
            assert body.count(REASON) == 1, (tab, lang)
            assert len(reasons) >= 2


def test_the_company_name_is_perso_dubbing_everywhere():
    """「Perso AI」 · 「Perso AI 더빙 팀」 · 「Perso AI Dubbing」 → 「Perso Dubbing」 · 「Perso Dubbing 팀」 (2026-10-16)."""
    every = "\n".join(text for texts in DEFAULTS.values() for text in texts.values())
    assert "Perso AI" not in every
    assert every.count("Perso Dubbing 팀입니다.") == 3 and every.count("Perso Dubbing 팀 드림") == 3
    assert every.count("Perso Dubbing Team") == 3 and every.count("This is the Perso Dubbing team.") == 2
    assert DEFAULTS[("up", "ko")]["reason.gen"] == "최근 Perso Dubbing을 활발히 이용하고 계신 것 같아 연락드립니다."
    assert {DEFAULTS[(tab, "ko")]["subject"] for tab in REASONS} == {
        "[Perso Dubbing] Business 플랜 안내드립니다", "[Perso Dubbing] 더빙 이용 현황 안내드립니다",
        "[Perso Dubbing] 크레딧 추가·계약 갱신 안내",
    }


def test_the_frame_keeps_the_mockup_wording_around_the_reason():
    """목업의 문구 그대로 — 근거 문단이 틀의 {근거} 자리에 들어가면 예전 초안과 같은 글이 된다(회사 이름만 다르다)."""
    churn = DEFAULTS[("churn", "ko")]
    assert churn["body"].replace(REASON, churn["reason.idle"]).replace(NAME, "가나 스튜디오") == (
        "안녕하세요, 가나 스튜디오 담당자님.\nPerso Dubbing 팀입니다.\n\n"
        "최근 한 달 넘게 더빙 이용이 없으신 것 같아 연락드립니다. 사용 중 불편한 점이나 막히는 부분이 있으셨는지 궁금합니다.\n\n"
        "필요하시면 활용 방법 안내나 간단한 온라인 미팅도 가능합니다. 편하신 시간을 알려주시면 연락드리겠습니다.\n\n"
        "감사합니다.\nPerso Dubbing 팀 드림"
    )
    up = DEFAULTS[("up", "en")]
    # 업셀링 영어는 근거 문장이 첫 문장 바로 뒤에 같은 줄로 붙는다(목업 그대로).
    assert up["body"].replace(REASON, up["reason.near"]).startswith(
        "Hi,\n\nThank you for using Perso Dubbing. It looks like your workspace has been running close to the "
        "monthly limit of the PRO plan recently.\n\nIf you expect")


def test_the_defaults_have_no_usage_numbers():
    every = "\n".join(text for texts in DEFAULTS.values() for text in texts.values())
    assert not any(ch.isdigit() for ch in every)


# --------------------------------------------------------------------------- #
# 읽기 · 저장 · 기록
# --------------------------------------------------------------------------- #
def test_nothing_is_edited_until_a_person_saves(console):
    data = console.get("/api/ui/insight-drafts").json()
    assert [p["token"] for p in data["placeholders"]] == ["{고객명}", "{근거}"]
    parts_ = _parts(console)
    assert len(parts_) == 28  # 업셀링 (2+2)×2 · 이탈 방지 (2+3)×2 · 추가 계약 (2+3)×2
    assert all(p["value"] == p["default"] and p["updated_by"] is None for p in parts_.values())
    assert parts_[("churn", "ko", "reason.idle")]["label"] == "근거 — 무활동이 기준 이상이거나 활동 기록이 없는 곳"


def test_saving_records_who_and_when_and_keeps_the_text_before(console, factory):
    sent = {**_full("churn", "ko"), "ko.subject": "[Perso Dubbing] 이용 현황을 여쭙니다"}
    answer = console.post("/operations/insights/drafts", data={"tab": "churn", **sent})
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "changed": ["ko.subject"]}  # 같은 글은 안 건드린다

    parts_ = _parts(console)
    assert parts_[("churn", "ko", "subject")]["value"] == "[Perso Dubbing] 이용 현황을 여쭙니다"
    assert parts_[("churn", "ko", "subject")]["updated_by"] == "web"
    assert parts_[("churn", "en", "subject")]["updated_by"] is None
    with factory() as session:
        [line] = session.query(InsightLog).all()
        assert (line.tab, line.action, line.actor, line.space_seq, line.contract_id) == ("churn", "draft", "web", None, None)
        assert line.detail == (
            "초안 문구 변경: 이탈 방지 · 한국어 · 제목\n바꾸기 전:\n[Perso Dubbing] 더빙 이용 현황 안내드립니다")


def test_saving_the_default_again_removes_the_row(console, factory):
    """「기본 문구로 되돌리기」 — 기본 문구와 같은 글로 저장하면 행이 사라지고 다시 「기본 문구」다(기록은 남는다)."""
    console.post("/operations/insights/drafts", data={"tab": "up", "en.subject": "More from Perso Dubbing"})
    console.post("/operations/insights/drafts", data={"tab": "up", **_full("up", "en")})
    with factory() as session:
        assert session.query(InsightDraft).count() == 0
        details = [line.detail for line in session.query(InsightLog).order_by(InsightLog.id)]
    assert details[1].startswith("초안 문구 변경 (기본 문구로): 업셀링 · English · 제목\n바꾸기 전:\nMore from Perso Dubbing")
    assert _parts(console)[("up", "en", "subject")]["updated_by"] is None


def test_line_breaks_are_kept_and_only_the_ends_are_trimmed(console):
    body = "안녕하세요, {고객명} 담당자님.\r\n\r\n{근거}\r\n\r\n감사합니다.  "
    console.post("/operations/insights/drafts", data={"tab": "grow", "ko.body": body})
    assert _parts(console)[("grow", "ko", "body")]["value"] == "안녕하세요, {고객명} 담당자님.\n\n{근거}\n\n감사합니다."


@pytest.mark.parametrize(("field", "text", "reason"), [
    ("ko.body", "안녕하세요.\n\n감사합니다.", "{근거} 는 공통 문단에 한 번 들어가야 합니다(지금 0번)"),     # ①
    ("ko.body", "{근거}\n\n{근거}", "(지금 2번)"),                                                    # ①
    ("ko.body", "안녕하세요, {이름} 님.\n{근거}", "모르는 자리표시 {이름} — 쓸 수 있는 것은 {고객명}, {근거}"),  # ②
    ("en.reason.idle", "Dear {customer}, hello.", "모르는 자리표시 {customer}"),                         # ②
    ("ko.subject", "[Perso Dubbing] {근거}", "{근거} 는 공통 문단에만"),
    ("ko.reason.under", "{근거} 입니다", "{근거} 는 공통 문단에만"),
    ("ko.subject", "첫 줄\n둘째 줄", "제목은 한 줄"),
    ("ko.subject", "   ", "비어 있습니다"),
    ("ko.subject", "가" * 201, "200자까지"),
    ("ko.reason.gen", "가" * 2001, "2,000자까지"),
])
def test_a_wrong_text_is_refused_and_nothing_is_saved(console, factory, field, text, reason):
    answer = console.post("/operations/insights/drafts", data={"tab": "churn", "ko.subject": "바뀐 제목", field: text})
    assert answer.status_code == 400
    assert reason in answer.json()["detail"]
    with factory() as session:  # 같이 보낸 멀쩡한 칸도 안 바뀐다
        assert session.query(InsightDraft).count() == 0
        assert session.query(InsightLog).count() == 0


@pytest.mark.parametrize("data", [
    {"tab": "churn", "ko.reason.short": "부족 예상 문단"},   # 추가 계약의 근거 — 이탈 방지에는 없다
    {"tab": "churn", "ja.subject": "件名"},
    {"tab": "other", "ko.subject": "제목"},
    {"tab": "churn"},
    {"tab": "churn", "ko.subject": "제목", "credits_30d": "1"},
])
def test_an_unknown_field_is_refused(console, factory, data):
    assert console.post("/operations/insights/drafts", data=data).status_code == 400
    with factory() as session:
        assert session.query(InsightDraft).count() == 0


def test_draft_changes_stay_out_of_the_row_logs(console):
    """문구 변경 줄은 어느 줄의 일도 아니고 바꾸기 전 글까지 들고 있어 길다 — 상세 패널이 읽는 기록에 안 섞인다."""
    console.post("/operations/insights/drafts", data={"tab": "up", "ko.subject": "새 제목"})
    assert console.get("/api/ui/insight-marks").json()["log"] == []


# --------------------------------------------------------------------------- #
# 인바운드 답장 AI 와 떨어져 있다
# --------------------------------------------------------------------------- #
def test_the_inbound_reply_ai_never_reads_the_sales_drafts():
    """영업 초안은 이메일 템플릿 표가 아니라 따로 둔 표에 산다 — 인바운드 답장의 문서 정리기는 이메일 템플릿 표의 행을
    전부 읽기 때문이다(`llm/organizer`). 모델을 부르는 코드(`src/llm`)도, 그것을 부르는 에이전트(`src/agents`)도
    영업 인사이트의 표와 모듈을 이름으로조차 모른다(판본 이력을 떼어 둔 것과 같은 방식)."""
    for folder in ("src/llm", "src/agents"):
        for path in (ROOT / folder).rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for needle in ("InsightDraft", "insight_drafts", "InsightRule", "InsightMark", "InsightLog",
                           "insight_rules", "insight_marks", "insight_log", "db.insight", "db import insight"):
                assert needle not in source, f"{path.relative_to(ROOT)} 가 영업 인사이트를 압니다: {needle}"
    # 그 표는 이메일 템플릿 표가 아니다.
    assert InsightDraft.__tablename__ != "email_templates"
    assert "email_templates" not in (ROOT / "src" / "db" / "insight_drafts.py").read_text(encoding="utf-8").split('"""', 2)[2]


def test_the_drafts_module_has_no_way_to_send_mail():
    code = (ROOT / "src" / "db" / "insight_drafts.py").read_text(encoding="utf-8").lower()
    for needle in ("senders", "smtp", "gmail", "hubspot", "integrations", "send_mail"):
        assert needle not in code, needle


def test_the_screen_holds_no_mail_wording_of_its_own():
    """문구는 서버 한 곳(기본 문구 `DEFAULTS` + 고친 칸 `insight_drafts`)에만 있다 — 화면 코드에 문구가 남아 있으면 「기본
    문구 수정」으로 고쳐도 그 문구로 초안을 만드는 자리가 생긴다. 화면(`insights/mail.ts`)은 근거 문단을 고르고 자리표시를
    채우기만 한다."""
    folder = ROOT / "frontend" / "src" / "screens" / "insights"
    for path in folder.glob("*.ts*"):
        code = path.read_text(encoding="utf-8")
        for needle in ("Perso", "담당자님", "Best regards", "팀 드림", "연락드립니다"):
            assert needle not in code, f"{path.name} 에 메일 문구가 남아 있습니다: {needle}"
