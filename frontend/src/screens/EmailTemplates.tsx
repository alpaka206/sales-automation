import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON, postForm } from "../lib/api";
import { Icon } from "../ui/Icon";
import { DataTable, type Column } from "../ui/DataTable";
import { kst } from "../lib/format";
import { Loading } from "../ui/Loading";
import { PolicyDocs } from "./PolicyDocs";

type Kind = { key: string; label: string; count: number; can_create: boolean; read_only: boolean };
type Item = { id: number; key: string; name: string; language: string; updated_at: string; kind: string; chars: number; is_default: boolean };
type List = { kinds: Kind[]; items: Item[] };
type Detail = { id: number; key: string; name: string; language: string; body: string; kind: string };

// Three rows hold a single value and nothing else: the booking calendar, the WhatsApp
// number, and the name the Korean template introduces the writer by. A language, an HTML
// preview and a 240px textarea are the wrong questions to ask about any of them, so they
// get one field — the label says what goes in it.
const ONE_LINE_FIELDS: Record<string, { label: string; type: string; hint: string }> = {
  meeting_link: { label: "링크 주소", type: "url", hint: "답변 본문의 토큰이 이 주소로 치환됩니다." },
  whatsapp_link: { label: "링크 주소", type: "url", hint: "답변 본문의 토큰이 이 주소로 치환됩니다." },
  sender_name: {
    label: "담당자 이름",
    type: "text",
    // Empty is a real state, not a mistake: the token then stays visible in the draft so
    // the operator sees it before 발송 rather than after.
    hint: "한국어 템플릿의 \"이스트소프트 OOO입니다\" 자리에 들어갑니다. 비워 두면 초안에 토큰이 그대로 남습니다.",
  },
};

function Editor({ id, onDone }: { id: number | "new"; onDone: () => void }) {
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

  // 새로 만들기 is only offered for signatures, so a new row is one.
  const isSignature = id === "new" || data?.kind === "signature";
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
          <div><h1 className="page-title">{id === "new" ? "새 서명 작성" : data?.name || "편집"}</h1></div>
        </div>

        {oneLine ? (
          <>
            <label className="field-label" htmlFor="et-link">{oneLine.label}</label>
            <input className={`input${oneLine.type === "url" ? " mono" : ""}`} id="et-link"
                   type={oneLine.type} value={body}
                   onChange={(e) => setBody(e.target.value.trim())}
                   placeholder={oneLine.type === "url" ? "https://…" : "예: 배운태"} />
            <p className="t-xs t-subtle" style={{ marginTop: 6 }}>{oneLine.hint}</p>
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
                    <option value="all">all</option>
                    <option value="ko">ko</option>
                    <option value="en">en</option>
                  </select>
                </div>
              </div>
            )}

            <label className="field-label" htmlFor="et-body">
              본문 <span className="t-subtle">({"{{변수}}"} 허용{isSignature ? " · 서명은 HTML 그대로 붙여넣기" : ""})</span>
            </label>
            <textarea className="draft-textarea" id="et-body" value={body}
                      onChange={(e) => setBody(e.target.value)} style={{ minHeight: 240 }} />

            {isSignature && (
              <>
                <div className="row" style={{ gap: 10, marginTop: 8, alignItems: "center" }}>
                  <button type="button" className="btn btn--subtle btn--sm" onClick={() => setPreview((p) => !p)}>
                    <Icon name="file" size={14} /> 미리보기
                  </button>
                  <span className="t-xs t-subtle">HTML 서명은 미리보기로 실제 모양을 확인하세요.</span>
                </div>
                {preview && (
                  <iframe title="템플릿 미리보기" sandbox=""
                          srcDoc={`<body style="margin:0;padding:24px;background:#fff;font-family:'Pretendard Variable',Pretendard">${body}</body>`}
                          style={{ width: "100%", height: 380, marginTop: 10, border: "1px solid var(--border)", borderRadius: 8, background: "#fff" }} />
                )}
              </>
            )}
          </>
        )}

        <div className="action-bar">
          <button type="button" className="btn btn--primary" onClick={() => void save()}>
            <Icon name="check" size={15} /> {id === "new" ? "생성" : "저장"}
          </button>
        </div>
        {note && <div className="t-sm" style={{ marginTop: 14 }} role="status">{note}</div>}
      </div>
    </>
  );
}

export function EmailTemplates() {
  const [params, setParams] = useSearchParams();
  const queryClient = useQueryClient();
  const kind = params.get("kind");
  const edit = params.get("edit");
  const { data, isPending } = useQuery({
    queryKey: ["email-templates"],
    queryFn: () => getJSON<List>("/api/ui/email-templates"),
  });

  async function setDefault(id: number) {
    await postForm(`/email-templates/${id}/default`, {});
    await queryClient.invalidateQueries({ queryKey: ["email-templates"] });
  }

  if (edit) {
    return (
      <Editor
        id={edit === "new" ? "new" : Number(edit)}
        onDone={() => setParams(kind ? { kind } : {}, { replace: true })}
      />
    );
  }

  if (isPending || !data) return <Loading columns={3} />;

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
  return (
    <>
      <div style={{ marginBottom: 14 }}>
        <button type="button" className="chip" onClick={() => setParams({}, { replace: true })}>
          <Icon name="chevron" size={14} /> 이메일 템플릿
        </button>
      </div>
      <div className="page-header">
        <div>
          <h1 className="page-title">{group?.label}</h1>
        </div>
        {group?.can_create && (
          <button type="button" className="btn btn--primary"
                  onClick={() => setParams({ kind, edit: "new" })}>
            <Icon name="plus" size={15} /> 새로 만들기
          </button>
        )}
      </div>
      <div className="card card--flush">
          <DataTable
            columns={[
              { label: "템플릿 이름", width: kind === "signature" ? "40%" : "56%",
                cell: (item) => <strong>{item.name}</strong> },
              // Signatures only. It answers "which one gets used?", which is the question
              // the list could not answer before — every row looked equally in play.
              ...(kind === "signature"
                ? [{
                    label: "기본", width: "16%",
                    cell: (item) => item.is_default ? (
                      <span className="pill pill--ok pill--sm"><span className="pill__dot" />기본</span>
                    ) : (
                      <button type="button" className="btn btn--subtle btn--sm"
                              onClick={(event) => { event.stopPropagation(); void setDefault(item.id); }}>
                        기본으로
                      </button>
                    ),
                  } as Column<Item>]
                : []),
              { label: "언어", width: "16%", cell: (item) => <span className="tag">{item.language}</span> },
              { label: "수정일", width: "28%", className: "td-subtle tnum",
                cell: (item) => kst(item.updated_at, "md-hm") || "—" },
            ]}
            rows={items}
            rowKey={(item) => item.id}
            empty="템플릿이 없습니다"
            onRowClick={(item) => setParams({ kind, edit: String(item.id) })}
          />
      </div>
    </>
  );
}
