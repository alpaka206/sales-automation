"""영업 인사이트 — 판정 기준과 그 기록 (2026-10-15 운영자 지시).

화면(``frontend/src/screens/SalesInsights.tsx`` 와 그 안쪽 겹 ``screens/insights/``)은 매일 가공된 스냅샷을
브라우저가 GitHub 에서 직접 받아 판정합니다 — 이 서버는 그 숫자를 모릅니다. 여기 사는 것은 **사람이 적은 것**
뿐입니다:

- **기준** — 「30일 크레딧 9,720 이상이면 업셀링 후보」 같은 문턱 여섯 개. 화면의 「기준 수정」 줄이
  ``POST /operations/insights/rules`` 로 바꾸고, ``GET /api/ui/insight-rules`` 로 모든 콘솔이 같은 값을
  읽습니다. 그중 셋(``shared``)은 수주 고객 「사용 진단」이 같이 씁니다 — 두 화면이 같은 고객을 다르게
  부르면 안 되기 때문입니다.
- **상태 · 메모** — 줄마다 「미컨택 → 컨택 완료 → 전환 / 제외」와 메모(``insight_marks``). 화면의 상세 패널이
  ``POST /operations/insights/marks`` 로 바꾸고 ``GET /api/ui/insight-marks`` 로 팀 전체가 같은 상태를 봅니다.
  업셀링 줄의 번호(space_seq)가 여기 남는 것이 「사용 데이터는 서버로 안 간다」의 세 번째 예외입니다(2026-10-15
  A안) — 사람이 누를 때만, 번호 · 상태 · 메모 · 누가 언제만.
- **기록** — 누가 언제 무엇을 바꿨나(``insight_log``, 덧붙이기만).

**기준의 목록 · 기본값 · 범위는 ``RULES`` 한 곳입니다.** 화면은 이름 · 단위 · 범위를 여기서 받아
그립니다 — 입력 칸의 범위와 서버의 검사가 서로 다른 말을 하지 않게 하려는 것입니다. 기본값은 목업
(``영업인사이트.html`` 의 ``RULE``)과 지금 수주 고객 화면의 값입니다(85 · 115 · 15 = ±15).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from sqlalchemy.orm import Session

from .models import ClientContract, InsightLog, InsightMark, InsightRule

# 카드마다 줄이 갈 수 있는 상태 — 첫 칸(todo)이 행이 없을 때의 상태다. 이름은 목업(``영업인사이트.html`` 의
# ``P.*.status``) 그대로이고, 화면은 이 목록을 받아 그린다(``GET /api/ui/insight-marks``). 기록 한 줄
# (「상태 변경: 컨택 완료」)도 이 이름으로 서버가 적는다 — 화면이 보낸 글자를 기록에 넣지 않는다.
STATUSES: dict[str, tuple[tuple[str, str], ...]] = {
    "up": (("todo", "미컨택"), ("done", "컨택 완료"), ("won", "전환"), ("drop", "제외")),
    "churn": (("todo", "미조치"), ("done", "조치 완료"), ("ok", "정상화"), ("drop", "제외")),
    "grow": (("todo", "미컨택"), ("done", "컨택 완료"), ("won", "계약"), ("drop", "제외")),
}
MEMO_MAX = 2_000
SUBJECT_MAX = 200
# 화면이 한 번에 받는 기록의 상한 — 사람이 누른 것만 쌓이는 표라 넉넉하다.
LOG_LIMIT = 5_000


@dataclass(frozen=True)
class RuleSpec:
    key: str
    # 어느 카드의 「기준」 줄에서 고치나 — up(업셀링) · churn(이탈 방지) · grow(추가 계약 기회).
    tab: str
    label: str
    unit: str
    default: int
    min: int
    max: int
    step: int = 1
    # 수주 고객 「사용 진단」이 같이 쓰는 값 — 고치면 그 화면의 판정도 바뀝니다.
    shared: bool = False
    # 범위를 벗어났을 때 이유에 덧붙이는 말.
    why: str = ""


RULES: tuple[RuleSpec, ...] = (
    # 업셀링 하한 1,500 은 가공기가 후보를 뽑는 문턱이다(`agent/sales.go` 의 picked: 30일 1,500 이상).
    # 그보다 낮추면 화면은 「후보가 늘었다」고 믿지만 가공 파일에는 그 스페이스가 처음부터 없다 —
    # 그래서 막는다. 두 숫자가 같은지는 `tests/test_insights.py` 가 본다.
    RuleSpec("upsell_credits_30d", "up", "30일 크레딧", "이상", 9_720, 1_500, 1_000_000, step=10,
             why="가공 후보 하한"),
    RuleSpec("forecast_left_pct", "churn", "잔여 예상 = 예상 소진이 계약 크레딧의", "% 이하", 85, 1, 100,
             shared=True),
    RuleSpec("churn_idle_days", "churn", "위험 높음 = 무활동", "일 이상", 30, 1, 365),
    RuleSpec("renewal_days", "grow", "계약 종료", "일 이내", 30, 1, 365),
    RuleSpec("forecast_short_pct", "grow", "부족 예상 = 예상 소진이", "% 이상", 115, 100, 1_000, shared=True),
    # 한 값이 과소사용(소진율 − 경과율 ≤ −N)과 초과사용(≥ +N)을 같이 정한다 — 수주 고객 화면이 그렇게 잰다.
    RuleSpec("level_gap_pp", "grow", "초과사용 = 소진율이 경과율보다", "%p 이상", 15, 1, 100, shared=True),
)
SPEC_BY_KEY: dict[str, RuleSpec] = {spec.key: spec for spec in RULES}


class RuleError(ValueError):
    """기준 · 상태 · 메모를 거절할 때 화면에 그대로 뜨는 이유."""


def _num(value: int) -> str:
    return f"{value:,}"


def parse_rule_value(spec: RuleSpec, raw: str) -> int:
    """칸에 적힌 글자 → 정수. 쉼표는 받아 주고, 소수 · 빈칸 · 범위 밖은 거절합니다."""
    text = (raw or "").replace(",", "").strip()
    try:
        value = int(text)
    except ValueError:
        raise RuleError(f"{spec.label}: 정수만 적을 수 있습니다") from None
    if not spec.min <= value <= spec.max:
        why = f" ({spec.why})" if spec.why else ""
        raise RuleError(f"{spec.label}: {_num(spec.min)}~{_num(spec.max)} 범위만 가능합니다{why}")
    return value


def rules_payload(session: Session) -> list[dict]:
    """여섯 기준 — 지금 값과 누가 언제 바꿨나, 그리고 화면이 칸을 그릴 이름 · 단위 · 범위."""
    stored = {row.key: row for row in session.query(InsightRule)}
    out = []
    for spec in RULES:
        row = stored.get(spec.key)
        out.append({
            "key": spec.key, "tab": spec.tab, "label": spec.label, "unit": spec.unit,
            "min": spec.min, "max": spec.max, "step": spec.step, "shared": spec.shared, "why": spec.why,
            "default": spec.default,
            "value": row.value if row else spec.default,
            "updated_by": row.updated_by if row else None,
            "updated_at": row.updated_at if row else None,
        })
    return out


def save_rules(session: Session, sent: Mapping[str, str], actor: str) -> list[str]:
    """화면이 보낸 기준을 저장하고, 바뀐 것마다 기록을 한 줄씩 남깁니다. 바뀐 열쇠를 돌려줍니다.

    **전부 검사한 뒤에 씁니다** — 셋 중 하나가 범위 밖이면 나머지 둘도 안 바뀝니다. 「저장」을 한 번
    눌렀는데 일부만 바뀌면 화면의 확인 창이 말한 것과 실제가 갈립니다. 모르는 칸도 거절합니다: 이름이
    틀린 칸을 조용히 버리면 200 이 돌아오고 아무것도 안 바뀝니다. 값이 그대로인 칸은 건드리지도
    기록하지도 않습니다. commit 은 부르는 쪽이 합니다.
    """
    unknown = sorted(key for key in sent if key not in SPEC_BY_KEY)
    if unknown:
        raise RuleError(f"모르는 기준입니다: {', '.join(unknown)}")
    if not sent:
        raise RuleError("바꿀 기준이 없습니다")
    values = {key: parse_rule_value(SPEC_BY_KEY[key], raw) for key, raw in sent.items()}

    stored = {row.key: row for row in session.query(InsightRule).filter(InsightRule.key.in_(list(values)))}
    now = datetime.now(timezone.utc)
    changed: list[str] = []
    for spec in RULES:  # 기록의 순서를 화면의 칸 순서로
        if spec.key not in values:
            continue
        value = values[spec.key]
        row = stored.get(spec.key)
        before = row.value if row else spec.default
        if value == before:
            continue
        if row is None:
            row = InsightRule(key=spec.key)
            session.add(row)
        row.value = value
        row.updated_by = actor
        row.updated_at = now
        session.add(InsightLog(
            tab=spec.tab, action="rule", actor=actor, created_at=now,
            detail=f"기준 변경: {spec.label} {_num(before)} → {_num(value)} {spec.unit}",
        ))
        changed.append(spec.key)
    return changed


# --------------------------------------------------------------------------- #
# 상태 · 메모 (2026-10-15)
# --------------------------------------------------------------------------- #


def _positive(raw: str | None, what: str) -> int:
    text = (raw or "").strip()
    if not text.isdigit() or int(text) <= 0:
        raise RuleError(f"{what} 이(가) 올바르지 않습니다")
    return int(text)


def _row_key(session: Session, sent: Mapping[str, str], extra: set[str]) -> tuple[str, int | None, int | None]:
    """어느 카드의 어느 줄인가 — ``tab`` 과 그 카드의 열쇠(업셀링 ``space_seq``, 나머지 ``contract_id``).

    받는 칸은 그 셋과 ``extra`` 뿐이고 **그 밖의 칸은 거절합니다**: 업셀링 줄은 스냅샷에서 나온 줄이라, 이 길로
    사용량 숫자가 실려 오기 시작하면 「사용 데이터는 서버로 안 간다」가 조용히 깨집니다.
    """
    allowed = {"tab", "space_seq", "contract_id"} | extra
    unknown = sorted(key for key in sent if key not in allowed)
    if unknown:
        raise RuleError(f"모르는 칸입니다: {', '.join(unknown)}")
    tab = (sent.get("tab") or "").strip()
    if tab not in STATUSES:
        raise RuleError("카드가 올바르지 않습니다")
    if tab == "up":
        if sent.get("contract_id"):
            raise RuleError("업셀링 줄은 space_seq 로 찾습니다")
        return tab, _positive(sent.get("space_seq"), "space_seq"), None
    if sent.get("space_seq"):
        raise RuleError("이탈 방지 · 추가 계약 줄은 계약으로 찾습니다")
    contract_id = _positive(sent.get("contract_id"), "계약")
    # 계약은 우리 표에 있다 — 없는 번호로 줄을 세우지 않는다(지워진 계약이면 그 줄에 더 할 일이 없다).
    if session.get(ClientContract, contract_id) is None:
        raise LookupError("계약을 찾을 수 없습니다")
    return tab, None, contract_id


def save_mark(session: Session, sent: Mapping[str, str], actor: str) -> list[str]:
    """줄 하나의 상태 · 메모를 저장하고 바뀐 것마다 기록을 남깁니다. 바뀐 것(``status`` · ``memo``)을 돌려줍니다.

    받는 칸은 다섯뿐입니다 — ``tab`` · 그 카드의 열쇠(업셀링 ``space_seq``, 나머지 ``contract_id``) · ``status`` ·
    ``memo``. **그 밖의 칸은 거절합니다**: 업셀링 줄은 스냅샷에서 나온 줄이라, 이 길로 사용량 숫자가 실려 오기
    시작하면 「사용 데이터는 서버로 안 간다」가 조용히 깨집니다(세 번째 예외는 번호 · 상태 · 메모까지다).
    commit 은 부르는 쪽이 합니다.
    """
    tab, space_seq, contract_id = _row_key(session, sent, {"status", "memo"})
    labels = dict(STATUSES[tab])
    status = sent.get("status")
    if status is not None and status not in labels:
        raise RuleError("상태가 올바르지 않습니다")
    memo = sent.get("memo")
    if memo is not None:
        memo = memo.strip()
        if len(memo) > MEMO_MAX:
            raise RuleError(f"메모는 {MEMO_MAX:,}자까지 적을 수 있습니다")
    if status is None and memo is None:
        raise RuleError("바꿀 것이 없습니다")

    query = session.query(InsightMark).filter(InsightMark.tab == tab)
    query = query.filter(InsightMark.space_seq == space_seq) if tab == "up" else query.filter(
        InsightMark.contract_id == contract_id)
    mark = query.one_or_none()
    before_status = mark.status if mark else "todo"
    before_memo = (mark.memo or "") if mark else ""
    changed: list[str] = []
    if status is not None and status != before_status:
        changed.append("status")
    if memo is not None and memo != before_memo:
        changed.append("memo")
    if not changed:
        return []

    now = datetime.now(timezone.utc)
    if mark is None:
        mark = InsightMark(tab=tab, space_seq=space_seq, contract_id=contract_id, status="todo")
        session.add(mark)
    if "status" in changed:
        mark.status = status
        session.add(InsightLog(tab=tab, space_seq=space_seq, contract_id=contract_id, action="status",
                               detail=f"상태 변경: {labels[status]}", actor=actor, created_at=now))
    if "memo" in changed:
        mark.memo = memo or None
        session.add(InsightLog(tab=tab, space_seq=space_seq, contract_id=contract_id, action="memo",
                               detail="메모 저장" if memo else "메모 지움", actor=actor, created_at=now))
    mark.updated_by = actor
    mark.updated_at = now
    return changed


def marks_payload(session: Session) -> dict:
    """카드마다의 상태 이름 · 모든 줄의 상태와 메모 · 줄의 기록(최근 것부터, 기준 변경은 뺀다)."""
    marks = session.query(InsightMark).all()
    log = (session.query(InsightLog).filter(InsightLog.action != "rule")
           .order_by(InsightLog.created_at.desc(), InsightLog.id.desc()).limit(LOG_LIMIT).all())
    return {
        "statuses": {tab: [{"key": key, "label": label} for key, label in pairs] for tab, pairs in STATUSES.items()},
        "marks": [
            {"tab": m.tab, "space_seq": m.space_seq, "contract_id": m.contract_id, "status": m.status,
             "memo": m.memo, "updated_by": m.updated_by, "updated_at": m.updated_at}
            for m in marks
        ],
        "log": [
            {"tab": line.tab, "space_seq": line.space_seq, "contract_id": line.contract_id, "action": line.action,
             "detail": line.detail, "actor": line.actor, "created_at": line.created_at}
            for line in log
        ],
    }


def save_copy(session: Session, sent: Mapping[str, str], actor: str) -> None:
    """메일 초안을 복사했다는 기록 한 줄 — 「메일 초안 복사 · <제목>」. **메일은 보내지 않습니다** (운영자 조건 3:
    「메일은 절대 보내지 마. 초안 만들고 복사만」) — 이 함수는 기록만 남기고, 받는 사람에게 가는 길은 사람이 복사해
    자기 메일 프로그램에 붙여 넣는 것 하나입니다. 받는 칸은 ``tab`` · 그 줄의 열쇠 · ``subject``(초안의 제목) 넷뿐이고
    본문은 받지 않습니다. 상태는 안 바꿉니다. commit 은 부르는 쪽이 합니다.
    """
    tab, space_seq, contract_id = _row_key(session, sent, {"subject"})
    subject = " ".join((sent.get("subject") or "").split())
    if not subject:
        raise RuleError("초안의 제목이 없습니다")
    session.add(InsightLog(tab=tab, space_seq=space_seq, contract_id=contract_id, action="copy",
                           detail=f"메일 초안 복사 · {subject[:SUBJECT_MAX]}", actor=actor,
                           created_at=datetime.now(timezone.utc)))
