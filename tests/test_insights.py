"""영업 인사이트 — 판정 기준은 서버에 살고, 누가 언제 바꿨는지 남는다 (2026-10-15 운영자 지시).

운영자의 조건 그대로:
- 기준값은 서버에 저장하고 화면에서 바꿀 수 있게. 누가 언제 바꿨는지 기록. 업셀링 하한은 1,500.
- 85% · 115% · 15%p 는 수주 고객 「사용 진단」과 값을 공유한다(그 셋은 ``shared``).
- 쓰기용 새 JSON API 는 만들지 않는다 — 다른 쓰기와 같은 폼 POST.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.base import Base
from src.db.insights import RULES, SPEC_BY_KEY
from src.db.models import InsightLog, InsightRule

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
    """콘솔이 부르는 대로 — 읽기는 /api/ui, 쓰기는 /operations 아래 폼 POST."""
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.api.routes import insights

    with patch.object(insights, "SessionLocal", factory), \
         patch("src.db.session.SessionLocal", factory), TestClient(app) as client:
        yield client


def _rules(console) -> dict[str, dict]:
    answer = console.get("/api/ui/insight-rules")
    assert answer.status_code == 200, answer.text
    return {rule["key"]: rule for rule in answer.json()["rules"]}


def test_the_six_rules_start_at_the_mockup_values_and_nobody_has_changed_them(console):
    """목업의 RULE(9,720 / 85 / 30 / 30 / 115 / 15)이자 지금 수주 고객 화면의 값(±15)이다."""
    rules = _rules(console)
    assert {key: rule["value"] for key, rule in rules.items()} == {
        "upsell_credits_30d": 9720,
        "forecast_left_pct": 85,
        "churn_idle_days": 30,
        "renewal_days": 30,
        "forecast_short_pct": 115,
        "level_gap_pp": 15,
    }
    assert [rule["tab"] for rule in rules.values()] == ["up", "churn", "churn", "grow", "grow", "grow"]
    # 행이 없으면 기본값 — 화면은 「기본값」이라 적는다.
    assert all(rule["updated_by"] is None and rule["updated_at"] is None for rule in rules.values())
    # 수주 고객 「사용 진단」과 같이 쓰는 셋.
    assert {key for key, rule in rules.items() if rule["shared"]} == {
        "forecast_left_pct", "forecast_short_pct", "level_gap_pp",
    }


def test_saving_records_who_and_when_and_appends_one_log_line_per_change(console, factory):
    answer = console.post("/operations/insights/rules", data={
        "forecast_left_pct": "80", "churn_idle_days": "45",
    })
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"ok": True, "changed": ["forecast_left_pct", "churn_idle_days"]}

    rules = _rules(console)
    assert rules["forecast_left_pct"]["value"] == 80
    assert rules["churn_idle_days"]["value"] == 45
    # 로그인 없는 로컬(basic)에서는 「web」 — 운영(google_oauth)에서는 로그인한 사람의 이름이다.
    assert rules["forecast_left_pct"]["updated_by"] == "web"
    assert rules["forecast_left_pct"]["updated_at"]
    # 안 보낸 기준은 그대로이고 기본값이다.
    assert rules["upsell_credits_30d"]["value"] == 9720 and rules["upsell_credits_30d"]["updated_by"] is None

    with factory() as session:
        lines = session.query(InsightLog).order_by(InsightLog.id).all()
        assert [(line.tab, line.action, line.detail, line.actor) for line in lines] == [
            ("churn", "rule", "기준 변경: 잔여 예상 = 예상 소진이 계약 크레딧의 85 → 80 % 이하", "web"),
            ("churn", "rule", "기준 변경: 위험 높음 = 무활동 30 → 45 일 이상", "web"),
        ]
        # 기준 줄은 무엇의 기록도 아니다 — 스페이스 · 계약 칸이 비어 있다.
        assert all(line.space_seq is None and line.contract_id is None for line in lines)

    # 한 번 더 바꾸면 그 앞 값에서 잰다.
    console.post("/operations/insights/rules", data={"forecast_left_pct": "75"})
    with factory() as session:
        last = session.query(InsightLog).order_by(InsightLog.id.desc()).first()
        assert last.detail == "기준 변경: 잔여 예상 = 예상 소진이 계약 크레딧의 80 → 75 % 이하"


def test_the_upsell_floor_is_1500(console, factory):
    refused = console.post("/operations/insights/rules", data={"upsell_credits_30d": "1,490"})
    assert refused.status_code == 400
    assert refused.json()["detail"] == "30일 크레딧: 1,500~1,000,000 범위만 가능합니다 (가공 후보 하한)"
    with factory() as session:
        assert session.query(InsightRule).count() == 0
        assert session.query(InsightLog).count() == 0

    # 쉼표는 받아 준다 — 화면이 「9,720」처럼 보여 준다.
    assert console.post("/operations/insights/rules", data={"upsell_credits_30d": "1,500"}).status_code == 200
    assert _rules(console)["upsell_credits_30d"]["value"] == 1500


@pytest.mark.parametrize(("key", "raw", "reason"), [
    ("forecast_left_pct", "101", "1~100 범위만"),
    ("forecast_left_pct", "0", "1~100 범위만"),
    ("forecast_short_pct", "99", "100~1,000 범위만"),
    ("level_gap_pp", "0", "1~100 범위만"),
    ("churn_idle_days", "-3", "1~365 범위만"),
    ("renewal_days", "12.5", "정수만"),
    ("renewal_days", "", "정수만"),
    ("renewal_days", "삼십", "정수만"),
])
def test_out_of_range_or_not_a_whole_number_is_refused(console, factory, key, raw, reason):
    answer = console.post("/operations/insights/rules", data={key: raw})
    assert answer.status_code == 400
    assert reason in answer.json()["detail"]
    with factory() as session:
        assert session.query(InsightRule).count() == 0


def test_one_bad_value_saves_none_of_the_others(console, factory):
    """「저장」 한 번에 일부만 바뀌면 확인 창이 말한 것과 실제가 갈린다."""
    answer = console.post("/operations/insights/rules", data={
        "renewal_days": "45", "forecast_short_pct": "120", "level_gap_pp": "500",
    })
    assert answer.status_code == 400
    with factory() as session:
        assert session.query(InsightRule).count() == 0
        assert session.query(InsightLog).count() == 0


def test_an_unknown_field_is_refused_not_silently_dropped(console, factory):
    """이름이 틀린 칸을 버리면 200 이 돌아오고 아무것도 안 바뀐다 — 화면은 저장됐다고 믿는다."""
    answer = console.post("/operations/insights/rules", data={"gapPct": "10"})
    assert answer.status_code == 400
    assert "gapPct" in answer.json()["detail"]
    assert console.post("/operations/insights/rules", data={}).status_code == 400


def test_saving_the_same_value_writes_nothing(console, factory):
    answer = console.post("/operations/insights/rules", data={"forecast_short_pct": "115"})
    assert answer.status_code == 200
    assert answer.json()["changed"] == []
    with factory() as session:
        assert session.query(InsightRule).count() == 0
        assert session.query(InsightLog).count() == 0


def test_the_write_is_a_form_post_under_a_registered_browser_prefix():
    """새 JSON 쓰기 API 를 안 만든다 — `/api/ui` 는 읽기만, 쓰기는 브라우저 경로의 폼 POST.

    (그 라우트가 실제로 앱에 붙어 있다는 것은 위 테스트들이 그 주소로 저장해 보며 확인한다.)
    """
    from src.api.routes import insights, ui_api
    from src.api.security import is_web_ui_path

    assert [(route.path, sorted(route.methods)) for route in insights.router.routes] == [
        ("/operations/insights/rules", ["POST"]),
        ("/operations/insights/marks", ["POST"]),
        ("/operations/insights/copies", ["POST"]),
        ("/operations/insights/drafts", ["POST"]),
    ]
    assert all(is_web_ui_path(route.path) for route in insights.router.routes)
    assert not [route.path for route in ui_api.router.routes if route.methods - {"GET", "HEAD"}]


def test_the_upsell_floor_is_the_exporters_candidate_floor():
    """업셀링 하한 = 가공기가 후보를 뽑는 문턱(`agent/sales.go` 의 picked).

    더 낮추면 화면은 후보가 늘었다고 믿지만 가공 파일에는 그 스페이스가 처음부터 없다. 가공기의 문턱을
    바꾸면(가공 레포의 EXPORTER_COMMIT 까지 올려야 실제로 바뀐다) 이 하한도 같이 바꾼다.
    """
    sales = (ROOT / "agent" / "sales.go").read_text(encoding="utf-8")
    picked = re.search(r"picked AS \((.*?)\)\s*\nSELECT", sales, re.S)
    assert picked, "agent/sales.go 의 picked 가 바뀌었습니다 — 이 테스트도 고쳐야 합니다"
    floor = re.search(r"credits_30d >= (\d+)", picked.group(1))
    assert floor and int(floor.group(1)) == SPEC_BY_KEY["upsell_credits_30d"].min


def test_every_default_is_inside_its_own_range():
    for spec in RULES:
        assert spec.min <= spec.default <= spec.max, spec.key
        assert spec.tab in ("up", "churn", "grow"), spec.key


# --------------------------------------------------------------------------- #
# 상태 · 메모 — 업셀링 줄의 번호가 서버에 남는 것이 세 번째 예외다 (2026-10-15 A안)
# --------------------------------------------------------------------------- #
def _marks(console) -> dict:
    answer = console.get("/api/ui/insight-marks")
    assert answer.status_code == 200, answer.text
    return answer.json()


def _contract(factory, contract_id: int = 501) -> int:
    from src.db.models import Client, ClientContract

    with factory() as session:
        session.add(Client(client_id=1101, company="가나 스튜디오"))
        session.add(ClientContract(id=contract_id, client_id=1101, seq=1))
        session.commit()
    return contract_id


def test_nothing_is_marked_until_a_person_presses_something(console):
    data = _marks(console)
    assert data["marks"] == [] and data["log"] == []
    # 카드마다의 상태 이름 — 목업 그대로이고 화면은 이 목록을 그린다.
    assert {tab: [s["label"] for s in pairs] for tab, pairs in data["statuses"].items()} == {
        "up": ["미컨택", "컨택 완료", "전환", "제외"],
        "churn": ["미조치", "조치 완료", "정상화", "제외"],
        "grow": ["미컨택", "컨택 완료", "계약", "제외"],
    }


def test_an_upsell_row_keeps_its_status_memo_and_who_by_space_seq(console, factory):
    assert console.post("/operations/insights/marks", data={
        "tab": "up", "space_seq": "1001", "status": "done",
    }).json() == {"ok": True, "changed": ["status"]}
    assert console.post("/operations/insights/marks", data={
        "tab": "up", "space_seq": "1001", "memo": "  담당자와 통화 — 다음 주 미팅  ",
    }).json()["changed"] == ["memo"]
    # 같은 상태를 또 눌러도 아무것도 안 바뀌고 기록도 없다.
    assert console.post("/operations/insights/marks", data={
        "tab": "up", "space_seq": "1001", "status": "done",
    }).json()["changed"] == []

    data = _marks(console)
    [mark] = data["marks"]
    assert (mark["tab"], mark["space_seq"], mark["contract_id"], mark["status"], mark["memo"], mark["updated_by"]) == (
        "up", 1001, None, "done", "담당자와 통화 — 다음 주 미팅", "web")
    assert [(line["action"], line["detail"], line["actor"]) for line in data["log"]] == [
        ("memo", "메모 저장", "web"),
        ("status", "상태 변경: 컨택 완료", "web"),
    ]

    console.post("/operations/insights/marks", data={"tab": "up", "space_seq": "1001", "memo": ""})
    data = _marks(console)
    assert data["marks"][0]["memo"] is None
    assert data["log"][0]["detail"] == "메모 지움"


def test_the_exception_carries_only_number_status_and_memo(console, factory):
    """세 번째 예외의 울타리 — 받는 칸은 tab · space_seq/contract_id · status · memo 다섯뿐이다. 업셀링 줄은 스냅샷에서
    나온 줄이라, 이 길로 사용량 숫자가 실려 오기 시작하면 「사용 데이터는 서버로 안 간다」가 조용히 깨진다."""
    for extra in ({"credits_30d": "12000"}, {"exports_30d": "40"}, {"plan_name": "Pro"}):
        answer = console.post("/operations/insights/marks", data={
            "tab": "up", "space_seq": "1001", "status": "done", **extra,
        })
        assert answer.status_code == 400, extra
        assert "모르는 칸" in answer.json()["detail"]
    with factory() as session:
        assert session.query(InsightLog).count() == 0


@pytest.mark.parametrize(("data", "status"), [
    ({"tab": "up", "space_seq": "1001", "status": "ok"}, 400),            # 정상화는 이탈 방지의 상태
    ({"tab": "up", "space_seq": "0", "status": "done"}, 400),
    ({"tab": "up", "space_seq": "abc", "status": "done"}, 400),
    ({"tab": "up", "contract_id": "501", "status": "done"}, 400),          # 업셀링은 스페이스로 찾는다
    ({"tab": "churn", "space_seq": "1001", "status": "done"}, 400),        # 이탈 방지는 계약으로 찾는다
    ({"tab": "churn", "contract_id": "999", "status": "done"}, 404),       # 없는 계약
    ({"tab": "other", "space_seq": "1001", "status": "done"}, 400),
    ({"tab": "up", "space_seq": "1001"}, 400),                             # 바꿀 것이 없다
    ({"tab": "up", "space_seq": "1001", "memo": "가" * 2001}, 400),
])
def test_a_wrong_mark_is_refused(console, factory, data, status):
    _contract(factory)
    assert console.post("/operations/insights/marks", data=data).status_code == status
    with factory() as session:
        assert session.query(InsightLog).count() == 0


def test_churn_and_grow_keep_separate_states_for_the_same_contract(console, factory):
    contract_id = _contract(factory)
    console.post("/operations/insights/marks", data={"tab": "churn", "contract_id": str(contract_id), "status": "ok"})
    console.post("/operations/insights/marks", data={"tab": "grow", "contract_id": str(contract_id), "status": "won"})
    data = _marks(console)
    assert sorted((m["tab"], m["contract_id"], m["status"], m["space_seq"]) for m in data["marks"]) == [
        ("churn", 501, "ok", None), ("grow", 501, "won", None),
    ]
    assert sorted(line["detail"] for line in data["log"]) == ["상태 변경: 계약", "상태 변경: 정상화"]


def test_rule_changes_stay_out_of_the_row_log(console):
    console.post("/operations/insights/rules", data={"renewal_days": "45"})
    assert _marks(console)["log"] == []


# --------------------------------------------------------------------------- #
# 메일 초안 — 복사만, 보내지 않는다 (운영자 조건 3)
# --------------------------------------------------------------------------- #
def test_copying_a_draft_leaves_one_log_line_and_sends_nothing(console, factory):
    """「복사」는 기록 한 줄이다 — 받는 칸은 tab · 그 줄의 열쇠 · subject 넷뿐(본문은 받지 않는다), 상태도 안 바꾼다."""
    contract_id = _contract(factory)
    assert console.post("/operations/insights/copies", data={
        "tab": "up", "space_seq": "1001", "subject": "[Perso AI] Business 플랜 안내드립니다",
    }).json() == {"ok": True}
    assert console.post("/operations/insights/copies", data={
        "tab": "grow", "contract_id": str(contract_id), "subject": "  [Perso AI]   크레딧 추가·계약 갱신 안내 ",
    }).status_code == 200
    data = _marks(console)
    assert data["marks"] == []
    assert sorted((line["tab"], line["action"], line["detail"]) for line in data["log"]) == [
        ("grow", "copy", "메일 초안 복사 · [Perso AI] 크레딧 추가·계약 갱신 안내"),
        ("up", "copy", "메일 초안 복사 · [Perso AI] Business 플랜 안내드립니다"),
    ]


@pytest.mark.parametrize("data", [
    {"tab": "up", "space_seq": "1001", "subject": "제목", "body": "본문"},       # 본문은 받지 않는다
    {"tab": "up", "space_seq": "1001", "subject": "제목", "credits_30d": "1"},  # 사용량 숫자는 실릴 칸이 없다
    {"tab": "up", "space_seq": "1001", "subject": "   "},
    {"tab": "up", "space_seq": "1001", "status": "done"},
])
def test_a_wrong_copy_log_is_refused(console, factory, data):
    assert console.post("/operations/insights/copies", data=data).status_code == 400
    with factory() as session:
        assert session.query(InsightLog).count() == 0


def test_the_insights_server_code_has_no_way_to_send_mail():
    """영업 인사이트의 서버 코드는 메일을 보내는 어떤 것도 부르지 않는다 — 발송 경로(`integrations/senders`) ·
    메일 함수 · 허브스팟을 import 하지 않는다. 메일은 사람이 복사해 자기 메일 프로그램에서 보낸다."""
    for path in (ROOT / "src" / "db" / "insights.py", ROOT / "src" / "api" / "routes" / "insights.py"):
        code = path.read_text(encoding="utf-8")
        for needle in ("senders", "send_", "smtp", "gmail", "hubspot", "integrations"):
            assert needle not in code.lower(), f"{path.name} 가 메일 · 외부 연동을 건드립니다: {needle}"
