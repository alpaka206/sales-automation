"""One request's authorized policy input, not an inferred policy/approval engine.

Read rules and routing candidates in one query. Fingerprints detect edits between
routing, generation and draft persistence. They do not assign contract precedence
or make an atomic transaction with a remote model or delivery provider.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json

from ..db.models import PolicySource


class PolicyContextError(RuntimeError):
    pass


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8")).hexdigest()


def read_sources(stage: str | None, *, mode: str | None = None, factory=None):
    from ..db.session import SessionLocal
    from .knowledge import scopes_for_stage

    allowed = scopes_for_stage(stage)
    try:
        with (factory or SessionLocal)() as session:
            query = session.query(PolicySource).filter(
                PolicySource.model_access == "customer_context",
                PolicySource.scope.in_(allowed),
                PolicySource.mode.in_((mode,) if mode else ("rules", "knowledge")),
            )
            return query.order_by(PolicySource.id).all()
    except Exception as exc:
        raise PolicyContextError("정책을 읽지 못했습니다. 초안을 다시 생성해 주세요.") from exc


@dataclass(frozen=True)
class PolicyDocument:
    id: int
    doc_key: str
    version: int
    mode: str
    scope: str
    model_access: str
    label: str
    title: str | None
    body: str
    usage_note: str | None

    @classmethod
    def from_row(cls, row):
        return cls(**{key: getattr(row, key) for key in cls.__dataclass_fields__})


def render_rules(rows) -> str:
    body = "\n\n".join(
        f"# {row.label}\n[policy source_id={row.id}; version={row.version}]\n{(row.body or '').strip()}"
        for row in rows if (row.body or "").strip()
    )
    return "## Company rules (must follow)\n\n" + body if body else ""


@dataclass(frozen=True)
class PolicySnapshot:
    stage: str
    documents: tuple[PolicyDocument, ...]

    @classmethod
    def capture(cls, stage: str) -> PolicySnapshot:
        return cls(stage, tuple(PolicyDocument.from_row(row) for row in read_sources(stage)))

    @property
    def digest(self) -> str:
        return fingerprint([asdict(doc) for doc in self.documents])

    @property
    def rules(self) -> str:
        return render_rules(doc for doc in self.documents if doc.mode == "rules")

    @property
    def candidates(self) -> list[PolicyDocument]:
        return sorted((doc for doc in self.documents if doc.mode == "knowledge"),
                      key=lambda doc: (doc.title or "", doc.label, doc.id))

    def assert_current(self) -> None:
        # 생성 중(몇 초)의 검사라 전체 digest 로 엄격하게 봅니다. 승인·발송 관문은
        # `evidence_changed` 로 초안이 본 문서만 봅니다 — 아래 이유.
        if self.capture(self.stage).digest != self.digest:
            raise PolicyContextError("초안 생성 중 정책이 변경되었습니다. 다시 생성해 주세요.")

    def evidence_changed(self, manifest: dict, selected_ids) -> bool:
        """초안이 **본** 문서가 바뀌었나 — 전체 digest 가 아닙니다.

        전체 digest(`assert_current`)로 승인·발송을 막으면 콘솔에서 정책 문서 **아무거나**
        저장하는 순간(CS 가이드 오타 하나, 저장이 「언제 쓰는가」를 다시 만드는 것까지)
        대기 중인 초안과 승인됐지만 아직 안 나간 회신이 **전부** 「정책이 변경되었습니다」로
        막히고, 빠져나갈 길은 다시 쓰기 — 운영자 편집이 사라지는 길 — 뿐입니다.

        그래서 재는 것은 모델이 실제로 읽은 것뿐입니다: 규칙 문서 전부(추가·삭제 포함 —
        초안이 못 본 규칙이 생긴 것도 정책 변경입니다)와 라우터가 고른 참고 문서. 안 고른
        참고 문서는 고쳐도 늘어도 지워도 안 셉니다.
        """
        current = {doc.id: doc for doc in self.documents}
        sources = manifest.get("sources", [])
        rules_then = {src["id"] for src in sources if src["mode"] == "rules"}
        if rules_then != {doc.id for doc in self.documents if doc.mode == "rules"}:
            return True
        watched = rules_then | set(selected_ids)
        return any(
            src["id"] not in current or fingerprint(asdict(current[src["id"]])) != src["sha256"]
            for src in sources if src["id"] in watched
        )

    def manifest(self) -> dict:
        # IDs/revisions/digests only: no titles, raw policy, or AI routing summaries.
        return {
            "stage": self.stage, "sha256": self.digest,
            "sources": [{"id": doc.id, "version": doc.version, "mode": doc.mode,
                         "scope": doc.scope, "sha256": fingerprint(asdict(doc))}
                        for doc in self.documents],
        }
