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
  /** 다섯 칸 중 하나. `model_access`·`mode`·`scope` 셋을 합친 값이고 매핑은 서버
   *  (`policy_docs.PLACEMENTS`) 한 곳입니다 — 화면이 자기 사전을 들면 서버가 안 받는
   *  값이 생깁니다. */
  placement: string;
  body: string | null; chars: number;
  usage_note: string; updated_at: string;
  version: number;
};
type Data = { modes: Mode[]; scopes: Mode[]; placements: Mode[]; rows: Row[] };

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
function DocEditor({ doc, placements, onDone }: {
  doc: Row | null;
  placements: Mode[];
  onDone: () => void;
}) {
  const [label, setLabel] = useState(doc?.title || doc?.label || "");
  const [placement, setPlacement] = useState(doc?.placement || "knowledge");
  const [body, setBody] = useState(doc?.body || "");
  const [note, setNote] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  // 이메일 템플릿 편집기와 같은 규칙입니다 — 바꾼 것이 있을 때만 저장이 뜨고, 판 번호는
  // 화면에서만 앞서 보입니다. 실제로 올라가는 것은 저장을 눌렀을 때뿐입니다.
  const dirty = doc
    ? label !== (doc.title || doc.label) || placement !== doc.placement
      || body !== (doc.body || "")
    : Boolean(label.trim() || body.trim());
  const shownVersion = (doc?.version ?? 1) + (dirty && doc ? 1 : 0);

  async function save() {
    setNote(null);
    try {
      // **안 바꿨으면 안 보냅니다.** 보내면 서버가 그 값을 적고, 다섯으로 표현이 안 되는
      // 조합(`knowledge/first`·`knowledge/followup`)은 그때 조용히 「모두」가 됩니다 —
      // 제목만 고친 저장이 후속 전용 문서를 첫 회신 후보로 만들었습니다.
      const fields: Record<string, string> = { label, body };
      if (!doc || placement !== doc.placement) fields.placement = placement;
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

        {/* **묻는 것은 둘뿐입니다 — 이름과 어느 회신에** (2026-09-10 운영자 지시:
            「어느 회신에 이거 하나만 고르면 되도록, 그리고 문서 이름이랑 같은 줄에」).

            예전에는 「쓰임」과 「범위」 두 고르개였습니다. 저장되는 칸은 지금도 셋이지만
            (`model_access`·`mode`·`scope`) 뜻이 있는 조합은 다섯뿐이고, 셋을 따로
            맞추게 하면 운영자가 조합을 틀립니다. */}
        <div className="row" style={{ gap: 12, alignItems: "flex-end", marginBottom: 12 }}>
          <div style={{ flex: 1 }}>
            <label className="field-label" htmlFor="pd-label">문서 이름</label>
            <input className="input" id="pd-label" value={label}
                   onChange={(e) => setLabel(e.target.value)}
                   placeholder="예: CS 문의 대응 가이드" />
          </div>
          <div style={{ width: 200 }}>
            <label className="field-label" htmlFor="pd-placement">어느 회신에</label>
            {/* 서버가 빈 값을 주면 이 다섯으로 표현이 안 되는 조합입니다. **고르개를
                비워 두고**, 운영자가 직접 고르지 않는 한 아무것도 안 보냅니다 — 골라야
                바뀌고, 안 고르면 지금 범위가 그대로 남습니다. */}
            <select className="select" id="pd-placement" value={placement}
                    onChange={(e) => setPlacement(e.target.value)}>
              {!placement && <option value="">— 지금 설정 유지 —</option>}
              {placements.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
          </div>
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
        {/* **화면 안에서 끝납니다** — 높이를 고정값(420px)으로 두었더니 운영자 화면
            (세로 695px)에서 편집기가 화면 밖으로 나가 스크롤을 만들었습니다. 이제 위아래
            요소가 쓰고 남은 만큼 차지합니다. 짧은 창에서는 `min-height` 가 받칩니다. */}
        <textarea className="draft-textarea mono doc-body" id="pd-body" value={body}
                  onChange={(e) => setBody(e.target.value)}
                  style={{ fontSize: 12.5, lineHeight: 1.7 }}
                  placeholder="노션에서 복사해 그대로 붙여넣으세요. 표와 목록은 그대로 읽힙니다." />

        {/* 저장은 왼쪽, 삭제는 오른쪽 끝에 휴지통 하나 — 이메일 템플릿과 같은 배치입니다.
            나란히 두면 둘이 같은 무게로 보입니다. */}
        <div className="action-bar row-between">
          <div className="row" style={{ gap: 8 }}>
            {/* **버튼은 항상 자리에 있습니다** (2026-09-10 운영자 지시). 바뀐 것이
                있을 때만 뜨게 해 두었더니 버튼이 나타났다 사라져 자리가 흔들렸고,
                「저장이 어디 갔지」가 됩니다. 지금은 **비활성으로 보입니다** — 있는데
                지금은 못 누른다는 것이 없는 것보다 알려 주는 것이 많습니다.

                「만들기」도 「저장하기」로 통일했습니다. 만드는 것과 고치는 것이 같은
                화면인데 버튼 글자만 다르면 같은 자리를 두 이름으로 부르게 됩니다. */}
            <ActionButton className="btn btn--primary btn--editor" disabled={!dirty}
                          pending="저장 중" onClick={save}>
              <Icon name="check" size={15} /> 저장하기
            </ActionButton>
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
    return <DocEditor doc={doc} placements={data.placements} onDone={backToList} />;
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

      {/* **칸이 곧 섹션입니다.**

          머리에 편수와 글자수를 적어 두었다가 뺐습니다(2026-09-10). 편수는 표에 행이
          보이니 같은 말을 두 번 하는 것이고, 글자수는 **재배치하는 동안에만** 쓸모가
          있었습니다 — 상시로는 아무도 안 보는 숫자가 매 줄 위에 한 자리씩 앉습니다.
          「이 문서가 프롬프트를 얼마나 차지하나」를 말해야 할 자리는 문서 점검입니다.

          **빈 칸은 안 그립니다.** 「0편」 한 줄을 위해 머리와 빈 표가 세 줄을 먹고,
          어느 칸이 비었는지는 고르개에서 이미 보입니다. 운영자 화면이 세로 695px 라
          안 쓰는 줄 하나가 곧 스크롤입니다. */}
      {/* **고르개에 없는 조합은 「분류 안 됨」에 모입니다.** 안 그리면 그 문서가 목록
          어디에도 안 뜨고, 화면에서 사라진 문서는 고칠 수도 옮길 수도 없습니다.
          지금은 `mode='knowledge'` 행이 여기 옵니다 — 「문의별 참고」를 고르개에서
          뺐기 때문입니다(라우터가 잠든 동안 그 칸은 「모든 회신에 적용」과 동작이 같습니다). */}
      {[...data.placements, { key: "", label: "분류 안 됨 — 옛 설정" }].map((placement) => {
        const rows = data.rows.filter((row) => (row.placement || "") === placement.key);
        if (rows.length === 0) return null;
        return (
          <section key={placement.key} className="mb-gap">
            <div className="section-header table-heading">
              <div className="section-header__l">
                <span className="section-header__icon"><Icon name="file" size={16} /></span>
                <div className="section-header__title">{placement.label}</div>
              </div>
            </div>
            <div className="card card--flush">
              {/* The SAME columns object for every group. Two tables measuring their own
                  widths put the same column in two different places. */}
              <DataTable
                columns={COLUMNS}
                rows={rows}
                rowKey={(row) => row.id}
                empty="등록된 문서가 없습니다."
                // Pushed: opening a document must leave the list in history so the
                // browser's back button returns to it instead of leaving the screen.
                onRowClick={(row) => setParams({ kind: "policy", doc: String(row.id) })}
              />
            </div>
          </section>
        );
      })}
    </>
  );
}
