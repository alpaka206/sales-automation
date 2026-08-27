import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { Icon } from "../ui/Icon";
import { DataTable, type Column } from "../ui/DataTable";
import { RevisionHistoryButton } from "../ui/RevisionHistory";
import { ActionButton } from "../ui/ActionButton";
import { kst } from "../lib/format";
import { Loading } from "../ui/Loading";
import { DeleteDialog } from "../ui/DeleteDialog";

type Mode = { key: string; label: string };
type Row = {
  id: number; label: string; title: string | null; mode: string; slug: string;
  body: string | null; chars: number;
  subject: string; usage_note: string; effective_on: string | null; edited_at: string | null;
  version: number;
};
type Data = { modes: Mode[]; rows: Row[] };

/** 만들기·고치기·지우기가 모두 form-encoded 로 갑니다 — 화면이 쓰는 라우트 계열입니다. */
async function send(path: string, method: string, fields: Record<string, string> = {}) {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(fields),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail ?? `${response.status}`);
  }
}

/** 같은 columns 객체를 두 묶음이 씁니다 — 표 둘이 각자 폭을 재면 같은 열이 다른 자리에
 *  섭니다. 되돌리기 버튼 하나 때문에 함수가 되었을 뿐, 여전히 한 벌입니다. */
const COLUMNS: Column<Row>[] = [
  { label: "문서", width: "66%", cell: (row) => (
      <>
        <strong>{row.title || row.label}</strong>
        {/* 라우터가 "이 문서를 보고 답해라" 라고 할 때 부르는 이름. 고르는 근거는 제목과
            「언제 쓰는가」이지 이 글자가 아니지만, 로그에 남는 것이 이것이라 화면과 로그를
            맞춰 보려면 여기 있어야 합니다. 「항상 적용」 문서에는 없습니다 — 고르는 대상이
            아니라 모든 프롬프트에 통째로 들어갑니다. */}
        {row.slug && <div className="t-xs mono t-subtle">{row.slug}</div>}
      </>
    ) },
  // **기준일과 수정일은 다른 사실입니다.** 기준일은 「이 정책이 언제 기준인가」라 오늘
  // 붙여넣은 넉 달 된 정책이 최신으로 보이지 않게 하고, 수정일은 「내가 언제 저장했나」
  // 입니다. 한 칸에서 기준일이 이기게 해 두었더니, 콘솔에서 고쳐도 이 칸이 안 움직여
  // 「저장이 안 됐나」로 읽혔습니다 (2026-08-27 운영자 지적). 둘 다 적습니다.
  { label: "기준 · 수정", width: "22%", className: "tnum td-subtle",
    cell: (row) => (
      <>
        <div>{row.effective_on || "—"}</div>
        <div className="t-xs t-subtle">수정 {kst(row.edited_at || "") || "—"}</div>
      </>
    ) },
];

/** 문서 하나 — 만들 때도 고칠 때도 이 화면입니다.
 *
 *  전에는 만드는 폼(목록 위에 펼쳐지는 카드)과 고치는 폼(상세 안의 또 다른 카드)이 따로
 *  있었습니다. 같은 것을 두 가지 모양으로 물으면 어느 칸이 어디 있는지 매번 다시 찾아야 하고,
 *  칸을 하나 더할 때 고칠 곳이 둘이 됩니다. 이메일 템플릿과 같은 배치로 맞췄습니다. */
function DocEditor({ doc, modes, onDone }: {
  doc: Row | null;
  modes: Mode[];
  onDone: () => void;
}) {
  const [label, setLabel] = useState(doc?.title || doc?.label || "");
  const [mode, setMode] = useState(doc?.mode || "knowledge");
  const [subject, setSubject] = useState(doc?.subject || "");
  const [usageNote, setUsageNote] = useState(doc?.usage_note || "");
  const [effectiveOn, setEffectiveOn] = useState(doc?.effective_on || "");
  const [body, setBody] = useState(doc?.body || "");
  const [note, setNote] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  // 이메일 템플릿 편집기와 같은 규칙입니다 — 바꾼 것이 있을 때만 저장이 뜨고, 판 번호는
  // 화면에서만 앞서 보입니다. 실제로 올라가는 것은 저장을 눌렀을 때뿐입니다.
  const dirty = doc
    ? label !== (doc.title || doc.label) || mode !== doc.mode || subject !== (doc.subject || "")
      || usageNote !== (doc.usage_note || "") || effectiveOn !== (doc.effective_on || "")
      || body !== (doc.body || "")
    : Boolean(label.trim() || body.trim());
  const shownVersion = (doc?.version ?? 1) + (dirty && doc ? 1 : 0);

  async function save() {
    setNote(null);
    try {
      const fields = { label, mode, subject, usage_note: usageNote, effective_on: effectiveOn, body };
      if (doc) await send(`/policy-docs/${doc.id}`, "PUT", fields);
      else await send("/policy-docs", "POST", fields);
      onDone();
    } catch (error) {
      setNote(`실패: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function remove() {
    if (!doc) return;
    setNote(null);
    try {
      await send(`/policy-docs/${doc.id}/delete`, "POST");
      onDone();
    } catch (error) {
      setNote(`실패: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  return (
    <>
      <div style={{ marginBottom: 14 }}>
        <button type="button" className="chip" onClick={onDone}>
          <Icon name="chevron" size={14} /> 정책 문서
        </button>
      </div>
      <div className="card">
        <div className="page-header">
          <div><h1 className="page-title">{doc ? label || "편집" : "새 문서"}</h1></div>
        </div>

        <label className="field-label" htmlFor="pd-label">문서 이름</label>
        <input className="input" id="pd-label" value={label}
               onChange={(e) => setLabel(e.target.value)}
               placeholder="예: CS 문의 대응 가이드" style={{ marginBottom: 12 }} />

        <div className="grid grid-2" style={{ marginBottom: 12 }}>
          <div>
            <label className="field-label" htmlFor="pd-mode">쓰임</label>
            <select className="select" id="pd-mode" value={mode}
                    onChange={(e) => setMode(e.target.value)}>
              {modes.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
            </select>
          </div>
          <div>
            <label className="field-label" htmlFor="pd-eff">
              기준일 <span className="t-subtle">(비우면 저장한 날짜)</span>
            </label>
            <input className="input" id="pd-eff" type="date" value={effectiveOn}
                   onChange={(e) => setEffectiveOn(e.target.value)} />
          </div>
        </div>

        {/* 이 문서를 근거로 회신할 때의 메일 제목. 본문 안에 "Subject: ..." 로 적으면 모델이
            그 줄을 본문에 옮겨 적어 첫 줄이 "Subject: ..." 인 메일이 나갑니다.

            아래 "언제 쓰는가" 와 함께 문의별 참고에만 묻습니다: 둘 다 라우터에 넘어가는 사본에만
            실리는 값이라, 항상 적용 문서에서는 채워도 아무 데도 가 닿지 않습니다. */}
        {mode === "knowledge" && (
          <>
            <label className="field-label" htmlFor="pd-subject">
              메일 제목 <span className="t-subtle">(비우면 RE: 고객이 쓴 제목)</span>
            </label>
            <input className="input" id="pd-subject" value={subject}
                   onChange={(e) => setSubject(e.target.value)}
                   placeholder="예: Next Steps on Your custom Perso Dubbing plan"
                   style={{ marginBottom: 12 }} />
          </>
        )}

        {/* 문의별 참고에만 묻습니다. 항상 적용 문서는 라우터를 거치지 않고 무조건 들어가므로,
            "언제 쓰는가" 는 그쪽에 답이 없는 질문입니다.

            문서를 고르는 것은 모델이고, 모델이 보는 것은 본문이 아니라 인덱스 한 줄입니다.
            비우면 본문 앞 400자가 그 자리에 들어갑니다 — 바로 표로 시작하는 문서는 그래서
            안 골라졌습니다. 본문 맨 위에 적어 두던 방식은 노션에서 다시 붙여넣을 때마다
            날아갔습니다. */}
        {mode === "knowledge" && (
          <>
            <label className="field-label" htmlFor="pd-usage">
              언제 쓰는가 <span className="t-subtle">(AI가 이 문서를 고를 때 읽는 설명. 비우면 본문 앞부분)</span>
            </label>
            <textarea className="draft-textarea" id="pd-usage" value={usageNote}
                      onChange={(e) => setUsageNote(e.target.value)}
                      style={{ minHeight: 72, marginBottom: 12 }}
                      placeholder="예: Quote, Price, pricing, cost, estimate, 추천 플랜 등 가격·견적·플랜 추천을 직접 묻는 문의에 씁니다." />
          </>
        )}

        <label className="field-label" htmlFor="pd-body">본문</label>
        <textarea className="draft-textarea mono" id="pd-body" value={body}
                  onChange={(e) => setBody(e.target.value)}
                  style={{ minHeight: 420, fontSize: 12.5, lineHeight: 1.7 }}
                  placeholder="노션에서 복사해 그대로 붙여넣으세요. 표와 목록은 그대로 읽힙니다." />

        {/* 저장은 왼쪽, 삭제는 오른쪽 끝에 휴지통 하나 — 이메일 템플릿과 같은 배치입니다.
            나란히 두면 둘이 같은 무게로 보입니다. */}
        <div className="action-bar row-between">
          <div className="row" style={{ gap: 8 }}>
            {dirty && (
              <ActionButton className="btn btn--primary btn--editor"
                            pending={doc ? "저장 중" : "만드는 중"} onClick={save}>
                <Icon name="check" size={15} /> {doc ? "저장" : "만들기"}
              </ActionButton>
            )}
            {doc && (
              <RevisionHistoryButton kind="policy_source" documentId={doc.id}
                                     title={doc.title || doc.label} />
            )}
            {doc && (
              <span className="t-xs t-subtle tnum" style={{ marginLeft: 4 }}>
                v{shownVersion}{dirty ? " (저장 전)" : ""}
              </span>
            )}
          </div>
          {doc && (
            <button type="button" className="btn btn--icon btn--danger-ghost"
                    title="삭제" aria-label="삭제" onClick={() => setConfirming(true)}>
              <Icon name="trash" size={16} />
            </button>
          )}
        </div>
        {note && <div className="t-sm" style={{ marginTop: 12 }} role="status">{note}</div>}
      </div>
      {confirming && doc && (
        <DeleteDialog name={doc.title || doc.label}
                      onCancel={() => setConfirming(false)} onConfirm={remove} />
      )}
    </>
  );
}

export function PolicyDocs({ onBack }: { onBack?: () => void }) {
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const open = params.get("doc");
  const { data, isPending } = useQuery({
    queryKey: ["policy-docs"],
    queryFn: () => getJSON<Data>("/api/ui/policy-docs"),
  });

  if (isPending || !data) return <Loading columns={2} />;

  const backToList = () => {
    void queryClient.invalidateQueries({ queryKey: ["policy-docs"] });
    setParams({ kind: "policy" }, { replace: true });
  };

  if (open) {
    const doc = open === "new" ? null : data.rows.find((row) => String(row.id) === open) ?? null;
    return <DocEditor doc={doc} modes={data.modes} onDone={backToList} />;
  }

  return (
    <>
      {/* 나가는 버튼과 만드는 버튼을 한 줄에 — 이메일 템플릿 목록과 같은 배치입니다. */}
      <div className="row-between" style={{ marginBottom: 14 }}>
        <button type="button" className="chip"
                onClick={() => (onBack ? onBack() : setParams({}, { replace: true }))}>
          <Icon name="chevron" size={14} /> 이메일 템플릿
        </button>
        {/* 문서가 들어오는 유일한 길입니다. 노션에서 받아 오는 경로는 전부 없앴습니다 —
            토큰을 못 만들고, 쿠키는 403 이고, Export zip 은 부모 한 장만 실어 옵니다. */}
        <button type="button" className="btn btn--primary btn--sm"
                onClick={() => setParams({ kind: "policy", doc: "new" })}>
          <Icon name="plus" size={14} /> 새로 만들기
        </button>
      </div>
      <div className="page-header">
        <div><h1 className="page-title">정책 문서</h1></div>
      </div>

      {data.modes.map((mode) => (
        <section key={mode.key} className="mb-gap">
          <div className="section-header table-heading">
            <div className="section-header__l">
              <span className="section-header__icon"><Icon name="file" size={16} /></span>
              <div className="section-header__title">{mode.label}</div>
            </div>
          </div>
          <div className="card card--flush">
            {/* The SAME columns object for both groups. Two tables measuring their own
                widths put the same column in two different places. */}
            <DataTable
              columns={COLUMNS}
              rows={data.rows.filter((row) => row.mode === mode.key)}
              rowKey={(row) => row.id}
              empty="등록된 문서가 없습니다."
              // Pushed: opening a document must leave the list in history so the
              // browser's back button returns to it instead of leaving the screen.
              onRowClick={(row) => setParams({ kind: "policy", doc: String(row.id) })}
            />
          </div>
        </section>
      ))}
    </>
  );
}
