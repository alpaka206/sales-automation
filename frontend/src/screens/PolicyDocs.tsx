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
  id: number; label: string; title: string | null; mode: string;
  /** 어느 회신에 붙는가 (0108). `scope_label` 은 기본값(`all`)과 「항상 적용」에서 빕니다 —
   *  모든 줄에 「모두」가 하나씩 붙으면 아무것도 안 알려 줍니다. */
  scope: string; scope_label: string;
  body: string | null; chars: number;
  usage_note: string; updated_at: string;
  version: number;
};
type Data = { modes: Mode[]; scopes: Mode[]; rows: Row[] };

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
        {row.scope_label && <span className="tag" style={{ marginLeft: 6 }}>{row.scope_label}</span>}
        {/* **`notion-…` 줄은 없앴습니다** (2026-09-10 운영자 지적). 그 값은
            `notion-<doc_key 앞 12자>` 였고, 함수 docstring 이 직접 「아무것도 가리키지
            않습니다」라고 적고 있었습니다 — 노션에서 받아오던 시절의 사본 표 slug 이고
            그 표는 2026-08-27 에 없어졌습니다. 라우터가 부르는 이름은 `doc_key` 입니다. */}
      </>
    ) },
  // **날짜는 하나입니다** (0101). 「기준일」이라는 칸이 따로 있었는데 마이그레이션이 심은
  // 한 행 말고는 아무도 안 채웠고, 「언제 기준인가」는 결국 마지막으로 저장한 시각입니다.
  { label: "수정", width: "22%", className: "tnum td-subtle",
    cell: (row) => kst(row.updated_at) || "—" },
];

/** 문서 하나 — 만들 때도 고칠 때도 이 화면입니다.
 *
 *  전에는 만드는 폼(목록 위에 펼쳐지는 카드)과 고치는 폼(상세 안의 또 다른 카드)이 따로
 *  있었습니다. 같은 것을 두 가지 모양으로 물으면 어느 칸이 어디 있는지 매번 다시 찾아야 하고,
 *  칸을 하나 더할 때 고칠 곳이 둘이 됩니다. 이메일 템플릿과 같은 배치로 맞췄습니다. */
function DocEditor({ doc, modes, scopes, onDone }: {
  doc: Row | null;
  modes: Mode[];
  scopes: Mode[];
  onDone: () => void;
}) {
  const [label, setLabel] = useState(doc?.title || doc?.label || "");
  const [mode, setMode] = useState(doc?.mode || "knowledge");
  const [scope, setScope] = useState(doc?.scope || "all");
  const [body, setBody] = useState(doc?.body || "");
  const [note, setNote] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  // 이메일 템플릿 편집기와 같은 규칙입니다 — 바꾼 것이 있을 때만 저장이 뜨고, 판 번호는
  // 화면에서만 앞서 보입니다. 실제로 올라가는 것은 저장을 눌렀을 때뿐입니다.
  const dirty = doc
    ? label !== (doc.title || doc.label) || mode !== doc.mode || scope !== (doc.scope || "all")
      || body !== (doc.body || "")
    : Boolean(label.trim() || body.trim());
  const shownVersion = (doc?.version ?? 1) + (dirty && doc ? 1 : 0);

  async function save() {
    setNote(null);
    try {
      const fields = { label, mode, scope, body };
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
          {/* **어느 회신에 붙는가** (0108). 첫 회신에는 간단히 답하고, 고객이 더 물어오면
              깊은 문서를 붙여 자세히 씁니다 — 그 깊은 문서가 「후속 회신에만」입니다.

              「항상 적용」에는 안 묻습니다: 그 문서는 고르는 대상이 아니라 모든 프롬프트에
              통째로 들어가므로 답이 없는 질문입니다. 안 물을 때는 **칸을 안 그립니다** —
              숨겨 두면 그 값이 그대로 전송됩니다.

              「기준일」 칸이 여기 있었습니다 (0101). 「언제 기준인가」는 결국 마지막으로
              저장한 시각이고, 그건 저장할 때마다 자동으로 움직입니다 — 사람이 채워야 하는
              칸으로 두었더니 마이그레이션이 심은 한 행 말고는 아무도 안 채웠습니다. */}
          {mode === "knowledge" && (
            <div>
              <label className="field-label" htmlFor="pd-scope">어느 회신에</label>
              <select className="select" id="pd-scope" value={scope}
                      onChange={(e) => setScope(e.target.value)}>
                {scopes.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
              </select>
            </div>
          )}
        </div>

        {/* **「메일 제목」과 「언제 쓰는가」 칸은 없앴습니다** (2026-09-10 운영자 지시).

            메일 제목은 DB 에서도 나갔습니다(이관 0118) — 그 고정 제목이 두 번 사고를 냈고,
            지금 제목은 「RE: <고객이 쓴 제목>」 하나입니다.

            「언제 쓰는가」는 **저장할 때 본문에서 만듭니다**(`knowledge.usage_note_from_body`).
            사람이 적게 두면 안 적힌 행이 반드시 생기고, 바로 표로 시작하는 문서에서는 본문
            앞 400자가 아무것도 안 말해서 라우터가 그 문서를 못 골랐습니다. 만들어진 줄은
            목록에서 볼 수 있습니다 — 고칠 수는 없습니다: 본문에서 나온 값이라 손으로 고쳐
            두면 다음 저장에 덮입니다. */}

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
    return <DocEditor doc={doc} modes={data.modes} scopes={data.scopes} onDone={backToList} />;
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
