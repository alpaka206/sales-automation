import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON, postForm } from "../lib/api";
import { Icon } from "../ui/Icon";
import { DataTable, type Column } from "../ui/DataTable";
import { ActionButton } from "../ui/ActionButton";
import { kst } from "../lib/format";
import { Loading, LoadingBlock } from "../ui/Loading";
import { PolicyDocs } from "./PolicyDocs";

type Kind = { key: string; label: string; count: number; can_create: boolean; read_only: boolean };
type Item = {
  id: number; key: string; base_key: string; name: string; language: string;
  updated_at: string; kind: string; body: string; subject: string;
  chars: number;
};
// 한 줄 = 한 템플릿. 언어가 여럿이면 그 안에서 고릅니다.
type Group = { base: string; rows: Item[] };

const LANGUAGE_LABELS: Record<string, string> = { all: "전체", ko: "한국어", en: "영어" };

type List = { kinds: Kind[]; items: Item[] };

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

// 제목이 있는 메일. 서명·링크·담당자 이름에는 제목이라는 것이 없고, 답변 메일 형식은
// 뼈대일 뿐 메일이 아닙니다.
const HAS_SUBJECT = new Set(["auto_ack", "auto_ack_en"]);

function Editor({ id, data, siblings, onOpen, onDone }: {
  id: number | "new";
  // 목록이 이미 들고 있는 그 행입니다. 다시 받지 않습니다 — 서울에서 이 서비스까지 왕복이
  // 200~370ms 이고(측정), 그게 바닥입니다: DB를 찌르는 /healthz 가 정적 파일과 같은 시간이
  // 걸립니다. 거리가 값이지 쿼리가 값이 아닙니다. 행 몇 개에 가장 큰 본문이 1.1KB 라
  // 목록에 실어 보내는 편이 싸고, 여는 순간 그려집니다.
  data: Item | undefined;
  // 같은 템플릿의 다른 언어들. 목록에서 언어별로 줄을 나누는 대신 여기서 고릅니다.
  siblings: Item[];
  onOpen: (id: number) => void;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [language, setLanguage] = useState("all");
  const [body, setBody] = useState("");
  const [subject, setSubject] = useState("");
  const [note, setNote] = useState<string | null>("");
  // 미리보기는 늘 켜져 있고 타자가 멎으면 따라옵니다. 그래도 **스냅샷**입니다: srcDoc 을
  // body 에 직접 묶으면 글자 하나에 iframe 이 문서를 통째로 다시 싣습니다 — 타자 한 번에
  // 리로드 한 번. 누르는 대신 250ms 를 기다릴 뿐, 끊어 두는 것은 그대로입니다.
  const [preview, setPreview] = useState("");
  const [loadedId, setLoadedId] = useState<number | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setPreview(body), 250);
    return () => clearTimeout(timer);
  }, [body]);

  if (data && loadedId !== data.id) {
    setLoadedId(data.id);
    setName(data.name);
    setLanguage(data.language);
    setBody(data.body);
    setSubject(data.subject);
  }

  async function save() {
    setNote(null);
    // 한 칸짜리 값은 앞뒤 공백이 의미 없습니다 — 붙여넣기로 딸려 온 것이 대부분입니다.
    // 치는 동안이 아니라 여기서 한 번 다듬습니다.
    const value = oneLine ? body.trim() : body;
    try {
      // Same routes the Jinja form uses: key derivation and the revision snapshot stay
      // on the server, in one place.
      if (id === "new") {
        // 새로 만들 수 있는 것은 서명뿐이고, 서명에는 언어가 없습니다.
        await postForm("/email-templates", { name, body: value });
      } else {
        const response = await fetch(`/email-templates/${id}`, {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({ name, language, body: value, subject }),
        });
        if (!response.ok) throw new Error(String(response.status));
      }
      await queryClient.invalidateQueries();
      onDone();
    } catch (error) {
      setNote(`실패: ${String(error)}`);
    }
  }

  async function remove() {
    setNote(null);
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
  const oneLine = data ? ONE_LINE_FIELDS[data.key] : undefined;
  // 미리보기는 태그가 있으면 나옵니다. 서명만 볼 수 있게 해 두었더니 접수확인 하단 로고를
  // 고치는 사람은 테스트 메일을 보내 보는 수밖에 없었습니다 — HTML 인 행이 서명만은 아닙니다.
  const canPreview = isSignature || /<\w/.test(body);

  // 목록을 거쳐 들어오면 data 가 이미 있으므로 이 스켈레톤은 보이지 않습니다. 주소를 직접
  // 열어 목록이 아직 없을 때만 나옵니다 — 그때도 틀린 폼을 그리는 것보다는 낫습니다:
  // 어떤 폼인지는 key 가 정하고, key 는 행과 함께 오기 때문입니다.
  const backChip = (
    <div style={{ marginBottom: 14 }}>
      <button type="button" className="chip" onClick={onDone}>
        <Icon name="chevron" size={14} /> 목록으로
      </button>
    </div>
  );
  if (id !== "new" && !data) {
    return (
      <>
        {backChip}
        <div className="card" style={{ maxWidth: 860 }}><LoadingBlock lines={6} /></div>
      </>
    );
  }

  return (
    <>
      {/* Left, with the chevron — the same back affordance as every other screen. It sat
          on the right of the header here alone, which read as an action, not a way out. */}
      {backChip}
      <div className="card" style={{ maxWidth: 860 }}>
        <div className="page-header">
          <div>
            <h1 className="page-title">
              {id === "new" ? "새 서명 작성" : data?.name || "편집"}
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
                   // 타자마다 trim 하면 안 됩니다. "Untae Bae" 를 치는 도중 "Untae " 가
                   // "Untae" 로 잘려 스페이스가 영영 안 들어갑니다. 다듬는 것은 저장할 때.
                   onChange={(e) => setBody(e.target.value)}
                   placeholder={oneLine.placeholder} />
          </>
        ) : (
          <>
            {/* No 키 · 설명 · 상태 · 버전 field: none of them is a decision the operator
                makes, and the key is a code reference the send path resolves. The
                placeholder names a person, not a language — 서명에 언어라는 것이 없습니다. */}
            <label className="field-label" htmlFor="et-name">템플릿 이름</label>
            <input className="input" id="et-name" value={name} onChange={(e) => setName(e.target.value)}
                   placeholder="예: 배운태 (Perso Dubbing)" required style={{ marginBottom: 14 }} />

            {/* 서명에는 언어 칸이 없습니다. 어떤 코드도 언어로 서명을 고르지 않고 —
                고르는 것은 사람입니다 — 그래서 그 칸은 아무 데도 가 닿지 않는 질문이었습니다.
                다른 행들은 'all' 이거나 auto_ack 처럼 처음부터 언어별로 존재합니다. */}

            {/* 제목과 본문은 한 메일의 두 부분입니다. 따로 두면 한 메일을 고치는 데 두
                화면을 오가게 됩니다. */}
            {data && HAS_SUBJECT.has(data.key) && (
              <>
                <label className="field-label" htmlFor="et-subject">메일 제목</label>
                <input className="input" id="et-subject" value={subject}
                       onChange={(e) => setSubject(e.target.value)}
                       placeholder="비우면 RE: 고객이 쓴 제목"
                       style={{ marginBottom: 14 }} />
              </>
            )}
            {/* 나란히. 미리보기가 본문 **아래**에 붙으면 그만큼 세로가 밀리는데, 이 화면을
                보는 노트북은 세로가 640px 남짓입니다. 옆에 두면 늘 켜 두어도 높이가 그대로고,
                고치는 곳과 결과가 한눈에 들어옵니다. */}
            <div className={canPreview ? "grid grid-2" : undefined}>
              <div>
                <label className="field-label" htmlFor="et-body">본문</label>
                <textarea className="draft-textarea" id="et-body" value={body}
                          onChange={(e) => setBody(e.target.value)} style={{ minHeight: 260 }} />
              </div>
              {canPreview && (
                <div>
                  <span className="field-label">미리보기</span>
                  <iframe title="템플릿 미리보기" sandbox=""
                          srcDoc={`<body style="margin:0;padding:16px;background:#fff;font-family:'Pretendard Variable',Pretendard">${preview}</body>`}
                          style={{ width: "100%", height: 260, border: "1px solid var(--border)", borderRadius: 8, background: "#fff" }} />
                </div>
              )}
            </div>
          </>
        )}

        {/* 이 화면에서 할 수 있는 일은 전부 이 한 줄입니다. 미리보기 버튼은 없앴습니다 —
            켜고 끄는 것이 아니라 옆에 늘 있습니다. */}
        <div className="action-bar" style={{ marginTop: 14 }}>
          <ActionButton className="btn btn--primary" pending={id === "new" ? "만드는 중" : "저장 중"}
                        onClick={save}>
            <Icon name="check" size={15} /> {id === "new" ? "생성" : "저장"}
          </ActionButton>
          {data && isSignature && (
            <ActionButton className="btn btn--ghost" pending="삭제 중" onClick={remove}>
              <Icon name="x" size={15} /> 삭제
            </ActionButton>
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
        data={editing}
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
  const columns: Column<Group>[] = [
    // "기본" 표시는 없앴습니다: 어느 서명을 쓸지는 초안마다 고르는 것이고, 목록에 미리
    // 정해 둔 하나를 표시하면 그게 강제인 것처럼 읽힙니다.
    { label: "템플릿 이름", width: kind === "signature" ? "68%" : "52%",
      cell: (g) => <strong>{g.rows[0].name}</strong> },
    // 무엇이 있는지만. 어느 것을 고칠지는 열어서 정합니다. 서명에는 언어라는 것이 없으므로
    // (고르는 것은 사람입니다) 그 묶음에서는 이 칸 자체를 뺍니다.
    ...(kind === "signature" ? [] : [{ label: "언어", width: "16%",
      cell: (g: Group) => (
        <span className="t-subtle t-xs">
          {g.rows.map((r) => LANGUAGE_LABELS[r.language] ?? r.language).join(" · ")}
        </span>
      ) }]),
    // 연도까지. 이 열은 "얼마나 오래됐나" 를 보는 자리이고, 월·일만 있으면 작년 것과 올해
    // 것이 같은 글자로 보입니다.
    { label: "수정일", width: "20%", className: "td-subtle tnum",
      cell: (g) => kst(g.rows[0].updated_at) || "—" },
    {
      // 정책 문서 목록과 같은 자리. 줄을 클릭해도 열리지만, 지우려고 들어갔다 나오는
      // 왕복이 없어야 합니다.
      width: "12%",
      cell: (g) => (
        <div className="row" style={{ gap: 6 }} onClick={(e) => e.stopPropagation()}>
          <button type="button" className="btn btn--subtle btn--sm"
                  onClick={() => setParams({ kind, edit: String(g.rows[0].id) })}>수정</button>
          {/* 서명만. 나머지는 코드가 이름으로 찾는 행이라 지우면 되돌릴 방법이 없습니다 —
              콘솔은 서명을 만들지 코드 참조를 만들지 못합니다. */}
          {kind === "signature" && (
            <ActionButton className="btn btn--ghost btn--sm" pending="삭제 중"
                          onClick={() => removeTemplate(g.rows[0].id)}>삭제</ActionButton>
          )}
        </div>
      ),
    },
  ];

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
            columns={columns}
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
