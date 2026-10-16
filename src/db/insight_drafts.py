"""영업 인사이트 메일 초안의 기본 문구 — 화면(「메일 초안」 창의 「기본 문구 수정」)에서 고친다 (2026-10-16 운영자 지시:
「메일 초안 문구를 영업 인사이트 화면에서 고칠 수 있게 바꿔줘」 — 그 전에는 화면 코드 ``insights/mail.ts`` 에 박혀
있었고 고치려면 배포가 필요했다).

**칸** = 카드(업셀링 · 이탈 방지 · 추가 계약) × 언어(한국어 · English) × 부분:

- ``subject`` 제목 — 한 줄.
- ``body`` 공통 문단 — 인사 · ``{근거}`` · 맺음말로 된 본문 틀. ``{근거}`` 는 **한 번만** 들어가야 한다 — 없으면 그 곳의
  근거가 빠진 메일이 되고, 두 번이면 같은 문단이 두 번 선다.
- ``reason.<근거>`` 근거별 문단 — 그 곳의 상황에 맞춰 화면이 하나를 골라 ``{근거}`` 자리에 넣는다(``REASONS``).

자리표시는 둘뿐이다(``PLACEHOLDERS``) — ``{고객명}`` · ``{근거}``. 모르는 자리표시(``{이름}`` 같은)는 저장을 거절한다:
그대로 두면 그 글자가 고객에게 가는 메일에 복사된다.

**저장은 ``insight_drafts`` 표에, 고친 칸만** — 행이 없으면 아래 ``DEFAULTS`` 이고, 기본 문구와 같은 글로 저장하면 행을
지운다(「기본 문구로 되돌리기」가 그렇게 된다). 고친 칸마다 기록(``insight_log``)에 한 줄 — 무엇을 바꿨는지와 **바꾸기
전 글**을 남긴다(화면에는 안 보이고, 실수를 되돌릴 때 DB 에서 찾는다).

**이메일 템플릿 표와 따로다** — 인바운드 답장 AI 의 문서 정리기는 ``email_templates`` 의 행을 전부 읽는다
(``llm/organizer``). 이 모듈과 그 표는 ``src/llm`` · ``src/agents`` 가 이름조차 모른다(``tests/test_insight_drafts.py``).
**메일을 보내지도 않는다** — 화면이 이 문구로 초안을 만들고, 사람이 복사해 자기 메일 프로그램에서 보낸다.

기본 문구는 2026-10-15 목업(``영업인사이트.html``)의 문구이고, 회사 이름만 바꿨다 — 「Perso AI」 · 「Perso AI 더빙 팀」 ·
「Perso AI Dubbing」 → 「Perso Dubbing」 · 「Perso Dubbing 팀」(2026-10-16 운영자 지시). 확인 못 한 것 하나: 업셀링 문구의
Business 플랜 혜택(월 크레딧이 더 크고 · 우선 처리 · 전담 매니저)이 지금 요금제와 맞는지.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Mapping

from sqlalchemy.orm import Session

from .insights import RuleError
from .models import InsightDraft, InsightLog

TAB_LABELS = {"up": "업셀링", "churn": "이탈 방지", "grow": "추가 계약 기회"}
LANG_LABELS = {"ko": "한국어", "en": "English"}

NAME = "{고객명}"
REASON = "{근거}"
PLACEHOLDERS: tuple[tuple[str, str], ...] = (
    (NAME, "이탈 방지 · 추가 계약은 회사 이름, 업셀링은 「Space 번호」. 여러 곳 초안에서는 복사할 때 곳마다 바뀌고, "
           "카드 공통 초안에서는 글자 그대로 남습니다."),
    (REASON, "공통 문단에만, 한 번. 그 곳의 상황에 맞는 근거별 문단이 이 자리에 들어갑니다."),
)
_TOKEN = re.compile(r"\{[^{}\n]*\}")

# 근거별 문단 — 화면이 고르는 규칙은 ``insights/mail.ts`` 이고, 여기 이름은 편집 창에 「언제 쓰이는지」로 선다.
REASONS: dict[str, tuple[tuple[str, str], ...]] = {
    "up": (
        ("near", "한 곳의 초안 — PRO 플랜의 월 한도에 가깝게 쓰는 스페이스"),
        ("gen", "카드 공통 · 여러 곳 초안"),
    ),
    "churn": (
        ("idle", "무활동이 기준 이상이거나 활동 기록이 없는 곳"),
        ("under", "그 밖에 계약 크레딧이 남을 전망인 곳"),
        ("gen", "카드 공통 · 여러 곳 초안"),
    ),
    "grow": (
        ("short", "부족 예상 또는 초과사용인 곳"),
        ("renew", "계약 종료가 기준 이내인 곳"),
        ("gen", "카드 공통 · 여러 곳 초안"),
    ),
}

MAX_SUBJECT = 200
MAX_BODY = 4_000
MAX_REASON = 2_000

DEFAULTS: dict[tuple[str, str], dict[str, str]] = {
    ("up", "ko"): {
        "subject": "[Perso Dubbing] Business 플랜 안내드립니다",
        "body": (
            "안녕하세요,\nPerso Dubbing 팀입니다.\n\n{근거}\n\n이 흐름이 계속된다면 Business 플랜이 더 잘 맞을 수 있습니다. "
            "월 크레딧이 더 크고, 우선 처리와 전담 매니저가 제공됩니다. 간단한 온라인 미팅으로 업무 흐름에 맞는지 함께 살펴볼 "
            "수 있을까요?\n\n감사합니다.\nPerso Dubbing 팀 드림"
        ),
        "reason.near": "최근 PRO 플랜의 월 한도에 가깝게 이용하고 계신 것 같아 연락드립니다.",
        "reason.gen": "최근 Perso Dubbing을 활발히 이용하고 계신 것 같아 연락드립니다.",
    },
    ("up", "en"): {
        "subject": "Getting more out of Perso Dubbing",
        "body": (
            "Hi,\n\nThank you for using Perso Dubbing. {근거}\n\nIf you expect this pace to continue, our Business plan may "
            "be a better fit: larger monthly credits, priority processing and a dedicated manager. Would you be open to a "
            "short call to see whether it suits your workflow?\n\nBest regards,\nPerso Dubbing Team"
        ),
        "reason.near": "It looks like your workspace has been running close to the monthly limit of the PRO plan recently.",
        "reason.gen": "It looks like your workspace has been using Perso Dubbing heavily recently.",
    },
    ("churn", "ko"): {
        "subject": "[Perso Dubbing] 더빙 이용 현황 안내드립니다",
        "body": (
            "안녕하세요, {고객명} 담당자님.\nPerso Dubbing 팀입니다.\n\n{근거}\n\n필요하시면 활용 방법 안내나 간단한 온라인 "
            "미팅도 가능합니다. 편하신 시간을 알려주시면 연락드리겠습니다.\n\n감사합니다.\nPerso Dubbing 팀 드림"
        ),
        "reason.idle": (
            "최근 한 달 넘게 더빙 이용이 없으신 것 같아 연락드립니다. 사용 중 불편한 점이나 막히는 부분이 있으셨는지 "
            "궁금합니다."
        ),
        "reason.under": (
            "계약 기간에 비해 크레딧 사용이 여유 있으신 것 같아 연락드립니다. 남은 크레딧을 잘 활용하실 수 있도록 "
            "도와드리고 싶습니다."
        ),
        "reason.gen": (
            "최근 이용 현황을 확인하다가 연락드립니다. 사용 중 불편한 점이나 도움이 필요한 부분이 있으시면 편하게 "
            "말씀해 주세요."
        ),
    },
    ("churn", "en"): {
        "subject": "[Perso Dubbing] Checking in on your dubbing usage",
        "body": (
            "Hi {고객명} team,\n\nThis is the Perso Dubbing team.\n\n{근거}\n\nWe're happy to walk you through best "
            "practices or set up a short call. Just let us know a time that works for you.\n\nBest regards,\n"
            "Perso Dubbing Team"
        ),
        "reason.idle": (
            "We noticed there hasn't been any dubbing activity in your workspace for over a month. We'd love to know "
            "if anything got in the way or if there's something we can help with."
        ),
        "reason.under": (
            "It looks like you still have a good amount of contract credits available relative to the contract "
            "period. We'd be glad to help you make the most of them."
        ),
        "reason.gen": (
            "We were reviewing recent usage and wanted to check in. If there's anything we can help with, please let "
            "us know."
        ),
    },
    ("grow", "ko"): {
        "subject": "[Perso Dubbing] 크레딧 추가·계약 갱신 안내",
        "body": (
            "안녕하세요, {고객명} 담당자님.\nPerso Dubbing 팀입니다.\n\n{근거}\n\n편하신 시간을 알려주시면 "
            "연락드리겠습니다.\n\n감사합니다.\nPerso Dubbing 팀 드림"
        ),
        "reason.short": (
            "최근 이용량이 늘어 계약 크레딧이 예정보다 일찍 소진될 것으로 보입니다. 작업이 중단되지 않도록 추가 "
            "크레딧이나 계약 조정 방안을 미리 안내드리고자 합니다."
        ),
        "reason.renew": (
            "계약 종료일이 다가오고 있어 갱신 조건을 미리 안내드리고자 합니다. 다음 기간에 필요한 분량이 달라졌다면 "
            "함께 조정해 드리겠습니다."
        ),
        "reason.gen": "계약 현황을 확인하다가 연락드립니다. 추가 크레딧이나 갱신 조건을 안내드리고자 합니다.",
    },
    ("grow", "en"): {
        "subject": "[Perso Dubbing] Additional credits and renewal options",
        "body": (
            "Hi {고객명} team,\n\nThis is the Perso Dubbing team.\n\n{근거}\n\nPlease let us know a time that works for "
            "you and we'll follow up.\n\nBest regards,\nPerso Dubbing Team"
        ),
        "reason.short": (
            "Your usage has grown recently, and it looks like your contract credits may run out earlier than "
            "planned. To avoid any interruption, we'd like to share options for additional credits or a contract "
            "adjustment."
        ),
        "reason.renew": (
            "Your contract end date is approaching, so we'd like to share renewal options ahead of time. If your "
            "needs for the next period have changed, we can adjust accordingly."
        ),
        "reason.gen": (
            "We were reviewing your contract and wanted to share options for additional credits or renewal."
        ),
    },
}


def parts(tab: str) -> list[tuple[str, str]]:
    """그 카드의 부분과 그 이름 — 제목 · 공통 문단 · 근거별 문단(편집 창의 칸 순서)."""
    return ([("subject", "제목"), ("body", "공통 문단")]
            + [(f"reason.{key}", f"근거 — {label}") for key, label in REASONS[tab]])


def _limit(part: str) -> int:
    return MAX_SUBJECT if part == "subject" else MAX_BODY if part == "body" else MAX_REASON


def check_text(tab: str, lang: str, part: str, raw: str) -> str:
    """편집 창이 보낸 글 → 저장할 글. 거절할 때는 화면에 그대로 뜨는 이유를 던집니다(``RuleError``).

    줄바꿈은 ``\\n`` 으로 맞추고 앞뒤 공백만 걷습니다 — 문단 안의 글자는 사람이 쓴 그대로입니다.
    """
    where = f"{LANG_LABELS[lang]} {dict(parts(tab))[part]}"
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise RuleError(f"{where}: 비어 있습니다")
    if part == "subject" and "\n" in text:
        raise RuleError(f"{where}: 제목은 한 줄이어야 합니다")
    if len(text) > _limit(part):
        raise RuleError(f"{where}: {_limit(part):,}자까지 적을 수 있습니다")
    allowed = ", ".join(token for token, _ in PLACEHOLDERS)
    for token in _TOKEN.findall(text):
        if token not in (NAME, REASON):
            raise RuleError(f"{where}: 모르는 자리표시 {token} — 쓸 수 있는 것은 {allowed} 입니다")
    count = text.count(REASON)
    if part == "body" and count != 1:
        raise RuleError(f"{where}: {REASON} 는 공통 문단에 한 번 들어가야 합니다(지금 {count}번)")
    if part != "body" and count:
        raise RuleError(f"{where}: {REASON} 는 공통 문단에만 쓸 수 있습니다")
    return text


def drafts_payload(session: Session) -> dict:
    """편집 창과 초안이 읽는 것 — 자리표시 안내와, 칸마다 지금 글 · 기본 문구 · 누가 언제."""
    stored = {(row.tab, row.lang, row.part): row for row in session.query(InsightDraft)}
    out = []
    for tab in REASONS:
        for lang in LANG_LABELS:
            for part, label in parts(tab):
                row = stored.get((tab, lang, part))
                out.append({
                    "tab": tab, "lang": lang, "part": part, "label": label,
                    "default": DEFAULTS[(tab, lang)][part],
                    "value": row.content if row else DEFAULTS[(tab, lang)][part],
                    "updated_by": row.updated_by if row else None,
                    "updated_at": row.updated_at if row else None,
                })
    return {
        "placeholders": [{"token": token, "help": help_} for token, help_ in PLACEHOLDERS],
        "parts": out,
    }


def save_drafts(session: Session, sent: Mapping[str, str], actor: str) -> list[str]:
    """한 카드의 기본 문구를 저장하고, 바뀐 칸마다 기록을 한 줄씩 남깁니다. 바뀐 칸(``ko.subject`` …)을 돌려줍니다.

    칸 이름은 ``<언어>.<부분>`` 이고(``ko.subject`` · ``en.reason.idle``), ``tab`` 이 어느 카드인지 말합니다. **전부 검사한
    뒤에 씁니다** — 하나라도 거절이면 아무것도 안 바뀝니다(확인창이 말한 것과 실제가 갈리면 안 됩니다). 모르는 칸도
    거절합니다. 기본 문구와 같은 글은 행을 지웁니다. commit 은 부르는 쪽이 합니다.
    """
    tab = (sent.get("tab") or "").strip()
    if tab not in REASONS:
        raise RuleError("카드가 올바르지 않습니다")
    known = {f"{lang}.{part}" for lang in LANG_LABELS for part, _ in parts(tab)}
    fields = {key: value for key, value in sent.items() if key != "tab"}
    unknown = sorted(key for key in fields if key not in known)
    if unknown:
        raise RuleError(f"모르는 칸입니다: {', '.join(unknown)}")
    if not fields:
        raise RuleError("바꿀 문구가 없습니다")
    texts = {}
    for key, raw in fields.items():
        lang, part = key.split(".", 1)
        texts[(lang, part)] = check_text(tab, lang, part, raw)

    stored = {(row.lang, row.part): row for row in session.query(InsightDraft).filter(InsightDraft.tab == tab)}
    now = datetime.now(timezone.utc)
    changed: list[str] = []
    labels = dict(parts(tab))
    for lang in LANG_LABELS:  # 기록의 순서를 편집 창의 칸 순서로
        for part, _ in parts(tab):
            if (lang, part) not in texts:
                continue
            text = texts[(lang, part)]
            row = stored.get((lang, part))
            default = DEFAULTS[(tab, lang)][part]
            before = row.content if row else default
            if text == before:
                continue
            if text == default:
                session.delete(row)  # 기본 문구로 돌아간다 — 행이 없으면 기본 문구
            else:
                if row is None:
                    row = InsightDraft(tab=tab, lang=lang, part=part)
                    session.add(row)
                row.content = text
                row.updated_by = actor
                row.updated_at = now
            back = " (기본 문구로)" if text == default else ""
            session.add(InsightLog(
                tab=tab, action="draft", actor=actor, created_at=now,
                detail=f"초안 문구 변경{back}: {TAB_LABELS[tab]} · {LANG_LABELS[lang]} · {labels[part]}\n바꾸기 전:\n{before}",
            ))
            changed.append(f"{lang}.{part}")
    return changed
