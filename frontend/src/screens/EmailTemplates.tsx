import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON, postForm } from "../lib/api";
import { Icon } from "../ui/Icon";
import { DataTable } from "../ui/DataTable";
import { kst } from "../lib/format";
import { Loading } from "../ui/Loading";
import { PolicyDocs } from "./PolicyDocs";

type Kind = { key: string; label: string; count: number; can_create: boolean; read_only: boolean };
type Item = {
  id: number; key: string; base_key: string; name: string; language: string;
  updated_at: string; kind: string; chars: number; is_default: boolean;
};
// 한 줄 = 한 템플릿. 언어가 여럿이면 그 안에서 고릅니다.
type Group = { base: string; rows: Item[] };

const LANGUAGE_LABELS: Record<string, string> = { all: "전체", ko: "한국어", en: "영어" };

/** 이름에서 언어 꼬리표를 뗍니다 — "자동 접수확인 (영어)" 는 목록에서 "자동 접수확인". */
const withoutLanguage = (name: string) => name.replace(/\s*[（(](전체|한국어|영어)[）)]\s*$/, "");
type List = { kinds: Kind[]; items: Item[] };
type Detail = {
  id: number; key: string; name: string; language: string; body: string; kind: string;
  is_default: boolean;
};

// Three rows hold a single value and nothing else: the booking calendar, the WhatsApp
// number, and the name the Korean template introduces the writer by. A language, an HTML
// preview and a 240px textarea are the wrong questions to ask about any of them, so they
// get one field. The label and the placeholder say what goes in it — no sentence under it:
// what the value does is visible in the draft, which is where it is read.
const ONE_LINE_FIELDS: Record<string, { label: string; type: string; placeholder: string }> = {
  meeting_link: { label: "링크 주소", type: "url", placeholder: "https://…" },
  whatsapp_link: { label: "링크 주소", type: "url", placeholder: "https://…" },
  // Two spellings of one person, not a translation. Empty is a real state, not a mistake:
  // the token then stays visible in the draft so the operator sees it before 발송.
  sender_name: { label: "담당자 이름 (한국어)", type: "text", placeholder: "예: 배운태" },
  sender_name_en: { label: "담당자 이름 (영문)", type: "text", placeholder: "예: Untae Bae" },
};

function Editor({ id, siblings, onOpen, onDone }: {
  id: number | "new";
  // 같은 템플릿의 다른 언어들. 목록에서 언어별로 줄을 나누는 대신 여기서 고릅니다.
  siblings: Item[];
  onOpen: (id: number) => void;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["email-template", id],
    queryFn: () => getJSON<Detail>(`/api/ui/email-templates/${id}`),
    enabled: id !== "new",
  });
  const [name, setName] = useState("");
  const [language, setLanguage] = useState("all");
  const [body, setBody] = useState("");
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState(false);
  const [loadedId, setLoadedId] = useState<number | null>(null);

  if (data && loadedId !== data.id) {
    setLoadedId(data.id);
    setName(data.name);
    setLanguage(data.language);
    setBody(data.body);
  }

  async function save() {
    setNote("저장 중…");
    try {
      // Same routes the Jinja form uses: key derivation and the revision snapshot stay
      // on the server, in one place.
      if (id === "new") {
        await postForm("/email-templates", { name, language, body });
      } else {
        const response = await fetch(`/email-templates/${id}`, {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({ name, language, body }),
        });
        if (!response.ok) throw new Error(String(response.status));
      }
      await queryClient.invalidateQueries();
      onDone();
    } catch (error) {
      setNote(`실패: ${String(error)}`);
    }
  }

  async function makeDefault() {
    setNote("변경 중…");
    try {
      await postForm(`/email-templates/${id}/default`, {});
      await queryClient.invalidateQueries();
      onDone();
    } catch (error) {
      setNote(`실패: ${String(error)}`);
    }
  }

  async function remove() {
    setNote("삭제 중…");
    try {
      const response = await fetch(`/email-templates/${id}`, {
        method: "DELETE", credentials: "same-origin",
      });
      // The server refuses to delete the last row for a key the send path resolves, and
      // says why — show that sentence rather than a status code.
      if (!response.ok) throw new Error((await response.text()).replace(/<[^>]*>/g, ""));
      await queryClient.invalidateQueries();
      onDone();
    } catch (error) {
      setNote(`${error instanceof Error ? error.message : String(error)}`);
    }
  }

  // 새로 만들기 is only offered for signatures, so a new row is one.
  const isSignature = id === "new" || data?.kind === "signature";
  const isDefault = Boolean(data?.is_default);
  const oneLine = data ? ONE_LINE_FIELDS[data.key] : undefined;

  return (
    <>
      {/* Left, with the chevron — the same back affordance as every other screen. It sat
          on the right of the header here alone, which read as an action, not a way out. */}
      <div style={{ marginBottom: 14 }}>
        <button type="button" className="chip" onClick={onDone}>
          <Icon name="chevron" size={14} /> 목록으로
        </button>
      </div>
      <div className="card" style={{ maxWidth: 860 }}>
        <div className="page-header">
          <div>
            <h1 className="page-title">
              {id === "new" ? "새 서명 작성" : withoutLanguage(data?.name || "") || "편집"}
            </h1>
          </div>
          {/* 언어가 하나뿐이면 고를 것이 없으므로 보이지 않습니다. */}
          {siblings.length > 1 && (
            <div className="chip-row">
              {siblings.map((row) => (
                <button key={row.id} type="button"
                        className={`chip${row.id === data?.id ? " is-active" : ""}`}
                        onClick={() => onOpen(row.id)}>
                  {LANGUAGE_LABELS[row.language] ?? row.language}
                </button>
              ))}
            </div>
          )}
        </div>

        {oneLine ? (
          <>
            <label className="field-label" htmlFor="et-link">{oneLine.label}</label>
            <input className={`input${oneLine.type === "url" ? " mono" : ""}`} id="et-link"
                   type={oneLine.type} value={body}
                   onChange={(e) => setBody(e.target.value.trim())}
                   placeholder={oneLine.placeholder} />
          </>
        ) : (
          <>
            {/* No 키 · 설명 · 상태 · 버전 field: none of them is a decision the operator
                makes, and the key is a code reference the send path resolves. */}
            <label className="field-label" htmlFor="et-name">템플릿 이름</label>
            <input className="input" id="et-name" value={name} onChange={(e) => setName(e.target.value)}
                   placeholder="예: 기본 서명 (한국어)" required style={{ marginBottom: 14 }} />

            {/* 언어 only for signatures: they are the only kind that exists once per
                language. The other rows are 'all' and always will be. */}
            {isSignature && (
              <div className="grid grid-2" style={{ marginBottom: 14 }}>
                <div>
                  <label className="field-label" htmlFor="et-language">언어</label>
                  <select className="select" id="et-language" value={language}
                          onChange={(e) => setLanguage(e.target.value)}>
                    <option value="all">전체</option>
                    <option value="ko">한국어</option>
                    <option value="en">영어</option>
                  </select>
                </div>
              </div>
            )}

            <label className="field-label" htmlFor="et-body">본문</label>
            <textarea className="draft-textarea" id="et-body" value={body}
                      onChange={(e) => setBody(e.target.value)} style={{ minHeight: 240 }} />

            {isSignature && preview && (
              <iframe title="템플릿 미리보기" sandbox=""
                      srcDoc={`<body style="margin:0;padding:24px;background:#fff;font-family:'Pretendard Variable',Pretendard">${body}</body>`}
                      style={{ width: "100%", height: 380, marginTop: 10, border: "1px solid var(--border)", borderRadius: 8, background: "#fff" }} />
            )}
          </>
        )}

        {/* 이 화면에서 할 수 있는 일은 전부 이 한 줄입니다. 미리보기가 본문 아래에 따로
            있으면 그만큼 세로가 밀리고, 이 화면을 보는 노트북은 세로가 640px 남짓입니다. */}
        <div className="action-bar">
          <button type="button" className="btn btn--primary" onClick={() => void save()}>
            <Icon name="check" size={15} /> {id === "new" ? "생성" : "저장"}
          </button>
          {isSignature && (
            <button type="button" className="btn btn--subtle" onClick={() => setPreview((p) => !p)}>
              <Icon name="file" size={15} /> 미리보기
            </button>
          )}
          {/* 목록의 `기본으로` 버튼 대신 여기. 서명마다 한 줄씩 버튼이 서 있을 일이 아니라
              그 서명을 열어 보고 정할 일입니다. */}
          {data && isSignature && !isDefault && (
            <button type="button" className="btn btn--subtle" onClick={() => void makeDefault()}>
              기본으로 지정
            </button>
          )}
          {data && isSignature && (
            <button type="button" className="btn btn--ghost" onClick={() => void remove()}>
              <Icon name="x" size={15} /> 삭제
            </button>
          )}
        </div>
        {note && <div className="t-sm" style={{ marginTop: 14 }} role="status">{note}</div>}
      </div>
    </>
  );
}

export function EmailTemplates() {
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [listNote, setListNote] = useState<string | null>(null);
  const kind = params.get("kind");

  async function removeTemplate(id: number) {
    setListNote(null);
    const response = await fetch(`/email-templates/${id}`, {
      method: "DELETE", credentials: "same-origin",
    });
    // The server refuses the last row for a key the send path resolves, and says why.
    if (!response.ok) {
      setListNote((await response.text()).replace(/<[^>]*>/g, ""));
      return;
    }
    await queryClient.invalidateQueries({ queryKey: ["email-templates"] });
  }
  const edit = params.get("edit");
  const { data, isPending } = useQuery({
    queryKey: ["email-templates"],
    queryFn: () => getJSON<List>("/api/ui/email-templates"),
  });

  if (isPending || !data) return <Loading columns={3} />;

  if (edit) {
    const editing = data.items.find((item) => String(item.id) === edit);
    return (
      <Editor
        id={edit === "new" ? "new" : Number(edit)}
        siblings={
          editing ? data.items.filter((item) => item.base_key === editing.base_key) : []
        }
        // replace: 언어를 바꿔 보는 것은 들어간 것이 아니라 같은 화면을 다르게 보는 것이라,
        // 뒤로가기가 언어를 되짚는 대신 목록으로 나가야 합니다.
        onOpen={(next) => setParams({ kind: kind ?? "", edit: String(next) }, { replace: true })}
        onDone={() => setParams(kind ? { kind } : {}, { replace: true })}
      />
    );
  }

  // Top level: the kinds. Flat, the list mixed signatures with the bodies the send path
  // resolves by name, and nothing on screen said which was which.
  if (!kind) {
    return (
      <>
        <div className="page-header"><div><h1 className="page-title">이메일 템플릿</h1></div></div>
        <div className="stack" style={{ gap: 12 }}>
          {data.kinds.map((entry) => (
            // Pushed, not replaced: going INTO something has to leave the list behind in
            // history, or the browser's back button jumps out of the screen entirely.
            // Replace stays on the back buttons and the filters below.
            <button key={entry.key} type="button" className="card is-clickable"
                    style={{ textAlign: "left", width: "100%" }}
                    onClick={() => setParams({ kind: entry.key })}>
              <div className="row-between">
                <div className="section-header__l">
                  <span className="section-header__icon">
                    <Icon name={
                      entry.key === "signature" ? "edit" : entry.key === "policy" ? "file" : "mail"
                    } size={16} />
                  </span>
                  <div className="section-header__title">{entry.label}</div>
                </div>
                <span className="tag tnum">{entry.count}</span>
              </div>
            </button>
          ))}
        </div>
      </>
    );
  }

  // 정책 문서 is read-only and comes from a different table — the group opens its own
  // viewer rather than the template list.
  if (kind === "policy") {
    return <PolicyDocs onBack={() => setParams({}, { replace: true })} />;
  }

  const group = data.kinds.find((entry) => entry.key === kind);
  const items = data.items.filter((item) => item.kind === kind);
  // 언어가 다른 같은 템플릿은 한 줄로 모읍니다. 두 줄이면 두 가지로 읽힙니다.
  const groups: Group[] = [];
  for (const item of items) {
    const found = groups.find((g) => g.base === item.base_key);
    if (found) found.rows.push(item);
    else groups.push({ base: item.base_key, rows: [item] });
  }
  for (const g of groups) {
    g.rows.sort((a, b) => (a.key === g.base ? -1 : b.key === g.base ? 1 : 0));
  }
  return (
    <>
      {/* 나가는 버튼과 만드는 버튼을 한 줄에. 각자 줄을 차지하면 세로로 두 줄이 더 붙고,
          이 화면을 보는 노트북은 세로가 640px 남짓입니다. */}
      <div className="row-between" style={{ marginBottom: 14 }}>
        <button type="button" className="chip" onClick={() => setParams({}, { replace: true })}>
          <Icon name="chevron" size={14} /> 이메일 템플릿
        </button>
        {group?.can_create && (
          <button type="button" className="btn btn--primary btn--sm"
                  onClick={() => setParams({ kind, edit: "new" })}>
            <Icon name="plus" size={14} /> 새로 만들기
          </button>
        )}
      </div>
      <div className="page-header">
        <div>
          <h1 className="page-title">{group?.label}</h1>
        </div>
      </div>
      <div className="card card--flush">
          <DataTable
            columns={[
              // 어느 것이 기본인지는 이름 옆 태그로 말합니다. 열을 따로 두면 서명마다 한
              // 줄씩 버튼이 서 있게 되고, 정하는 일은 그 서명을 열어 보고 할 일입니다.
              { label: "템플릿 이름", width: "52%",
                cell: (g) => (
                  <>
                    <strong>{withoutLanguage(g.rows[0].name)}</strong>
                    {g.rows.some((r) => r.is_default) && (
                      <span className="tag" style={{ marginLeft: 8 }}>기본</span>
                    )}
                  </>
                ) },
              // 무엇이 있는지만. 어느 것을 고칠지는 열어서 정합니다.
              { label: "언어", width: "16%",
                cell: (g) => (
                  <span className="t-subtle t-xs">
                    {g.rows.map((r) => LANGUAGE_LABELS[r.language] ?? r.language).join(" · ")}
                  </span>
                ) },
              // 연도까지. 이 열은 "얼마나 오래됐나" 를 보는 자리이고, 월·일만 있으면
              // 작년 것과 올해 것이 같은 글자로 보입니다.
              { label: "수정일", width: "20%", className: "td-subtle tnum",
                cell: (g) => kst(g.rows[0].updated_at) || "—" },
              {
                // 정책 문서 목록과 같은 자리. 줄을 클릭해도 열리지만, 지우려고 들어갔다
                // 나오는 왕복이 없어야 합니다.
                width: "12%",
                cell: (g) => (
                  <div className="row" style={{ gap: 6 }} onClick={(e) => e.stopPropagation()}>
                    <button type="button" className="btn btn--subtle btn--sm"
                            onClick={() => setParams({ kind, edit: String(g.rows[0].id) })}>수정</button>
                    {/* 서명만. 나머지는 코드가 이름으로 찾는 행이라 지우면 되돌릴 방법이
                        없습니다 — 콘솔은 서명을 만들지 코드 참조를 만들지 못합니다. */}
                    {kind === "signature" && (
                      <button type="button" className="btn btn--ghost btn--sm"
                              onClick={() => void removeTemplate(g.rows[0].id)}>삭제</button>
                    )}
                  </div>
                ),
              },
            ]}
            rows={groups}
            rowKey={(g) => g.base}
            empty="템플릿이 없습니다"
            onRowClick={(g) => setParams({ kind, edit: String(g.rows[0].id) })}
          />
      </div>
      {listNote && <div className="t-sm" style={{ marginTop: 12 }} role="status">{listNote}</div>}
    </>
  );
}
