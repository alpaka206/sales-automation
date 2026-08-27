import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON, postForm } from "../lib/api";
import { Icon } from "../ui/Icon";
import { DataTable, type Column } from "../ui/DataTable";
import { RevisionHistoryButton } from "../ui/RevisionHistory";
import { ActionButton } from "../ui/ActionButton";
import { kst } from "../lib/format";
import { Loading, LoadingBlock } from "../ui/Loading";
import { DeleteDialog } from "../ui/DeleteDialog";
import { PolicyDocs } from "./PolicyDocs";

type Kind = { key: string; label: string; count: number; can_create: boolean; read_only: boolean };
type Item = {
  id: number; key: string; name: string; language: string;
  updated_at: string;
  version: number; kind: string; body: string;
  /** 마지막으로 저장한 사람. */
  author: string | null;
  chars: number;
  // 발송 경로가 이 이름으로 찾는 행인가. 아무 키나 만들 수 있게 된 뒤로, 이것이 「실제로
  // 쓰이는 행」과 「목록에만 있는 행」을 가르는 유일한 표시입니다.
  code_resolved: boolean;
};

/** 언어 칸에 들어갈 수 있는 값. **「영어」가 아니라 「외국어」입니다** — `_en` 행을 고르는
 *  조건이 「영어인가」가 아니라 「한국어가 **아닌가**」라서, 일본어 문의도 베트남어 문의도
 *  그 행을 읽습니다(`prompts.get_reply_format`). 화면에 「영어」라고 적으면 그 행을 영어
 *  전용으로 읽게 되고, 무엇이 실제로 그 행을 읽는지와 어긋납니다 (2026-08-27 이관 0099).
 *
 *  키의 `_en` 접미사는 그대로입니다 — 그건 발송 경로가 이름으로 꺼내 가는 코드 참조입니다. */
const LANGUAGES: { value: string; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "ko", label: "한국어" },
  { value: "foreign", label: "외국어" },
];
const LANGUAGE_LABELS: Record<string, string> = Object.fromEntries(
  LANGUAGES.map((entry) => [entry.value, entry.label]),
);

type List = { kinds: Kind[]; items: Item[] };

// These rows hold a single value and nothing else: the booking calendar, the WhatsApp
// number, and the name the templates introduce the writer by. A language, an HTML
// preview and a 240px textarea are the wrong questions to ask about any of them, so they
// get one field. The label and the placeholder say what goes in it — no sentence under it:
// what the value does is visible in the draft, which is where it is read.
//
// 링크 네 줄은 **주소가 아니라 표기가 붙은 링크**입니다 (0069): 렌더러가 `[글자](주소)` 를
// 앵커로 만들고, 국문은 「미팅 링크」 에 영문은 `Calendly` 에 겁니다. type="url" 로 두면
// 브라우저가 바로 그 형태를 잘못된 값이라고 표시합니다.
const ONE_LINE_FIELDS: Record<string, { label: string; placeholder: string; mono?: boolean }> = {
  meeting_link: { label: "링크", placeholder: "[미팅 링크](https://…)", mono: true },
  meeting_link_en: { label: "링크", placeholder: "[Calendly](https://…)", mono: true },
  whatsapp_link: { label: "링크", placeholder: "[WhatsApp](https://…)", mono: true },
  whatsapp_link_en: { label: "링크", placeholder: "[WhatsApp](https://…)", mono: true },
  // Two spellings of one person, not a translation. Empty is a real state, not a mistake:
  // the token then stays visible in the draft so the operator sees it before 발송.
  sender_name: { label: "담당자 이름 (한국어)", placeholder: "예: 배운태" },
  sender_name_en: { label: "담당자 이름 (영문)", placeholder: "예: Untae Bae" },
};

function Editor({ id, data, onDone }: {
  id: number | "new";
  // 목록이 이미 들고 있는 그 행입니다. 다시 받지 않습니다 — 서울에서 이 서비스까지 왕복이
  // 200~370ms 이고(측정), 그게 바닥입니다: DB를 찌르는 /healthz 가 정적 파일과 같은 시간이
  // 걸립니다. 거리가 값이지 쿼리가 값이 아닙니다. 행 몇 개에 가장 큰 본문이 1.1KB 라
  // 목록에 실어 보내는 편이 싸고, 여는 순간 그려집니다.
  data: Item | undefined;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [language, setLanguage] = useState("all");
  const [body, setBody] = useState("");
  const [note, setNote] = useState<string | null>("");
  // 미리보기는 늘 켜져 있고 타자가 멎으면 따라옵니다. 그래도 **스냅샷**입니다: srcDoc 을
  // body 에 직접 묶으면 글자 하나에 iframe 이 문서를 통째로 다시 싣습니다 — 타자 한 번에
  // 리로드 한 번. 누르는 대신 250ms 를 기다릴 뿐, 끊어 두는 것은 그대로입니다.
  const [preview, setPreview] = useState("");
  const [loadedId, setLoadedId] = useState<number | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setPreview(body), 250);
    return () => clearTimeout(timer);
  }, [body]);

  if (data && loadedId !== data.id) {
    setLoadedId(data.id);
    setName(data.name);
    setLanguage(data.language);
    setBody(data.body);
  }

  // **바뀐 것이 있나.** 목록이 이미 들고 있는 서버 행과 비교합니다 — 상태를 하나 더 두면
  // 그 사본이 언젠가 어긋납니다. 저장 버튼이 뜨는 조건이자, 아래 판 번호가 한 칸 앞서
  // 보이는 조건입니다.
  const dirty =
    id === "new"
      ? Boolean(name.trim() || body.trim())
      : !!data &&
        (name !== data.name ||
          language !== data.language ||
          body !== data.body);
  // **화면에서만 올라갑니다.** 타자를 치는 동안 서버에는 아무것도 안 갑니다 — 판이 실제로
  // 올라가고 이력이 남는 것은 저장을 눌렀을 때뿐입니다 (2026-08-27 운영자 지시).
  const shownVersion = (data?.version ?? 1) + (dirty && id !== "new" ? 1 : 0);

  async function save() {
    setNote(null);
    // 한 칸짜리 값은 앞뒤 공백이 의미 없습니다 — 붙여넣기로 딸려 온 것이 대부분입니다.
    // 치는 동안이 아니라 여기서 한 번 다듬습니다.
    const value = oneLine ? body.trim() : body;
    try {
      // Same routes the Jinja form uses: key derivation and the revision snapshot stay
      // on the server, in one place.
      if (id === "new") {
        // 키를 비우면 서버가 서명으로 만듭니다 — 여기서 만드는 것의 거의 전부입니다.
        await postForm("/email-templates", { name, body: value, key, language });
      } else {
        const response = await fetch(`/email-templates/${id}`, {
          method: "PUT",
          credentials: "same-origin",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({ name, language, body: value }),
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
      // 서버는 거절하지 않습니다 — 무엇이든 지워집니다(운영자 결정). 그래도 본문을 읽는
      // 이유는 404 처럼 진짜 실패했을 때 상태 코드 대신 문장을 보여 주기 위해서입니다.
      if (!response.ok) throw new Error((await response.text()).replace(/<[^>]*>/g, ""));
      await queryClient.invalidateQueries();
      onDone();
    } catch (error) {
      setNote(`${error instanceof Error ? error.message : String(error)}`);
    }
  }

  // 이제 무엇이든 만들 수 있으므로 새 행이 서명이라는 보장이 없습니다 — 키가 정합니다.
  const isSignature = data
    ? data.kind === "signature"
    : !key.trim() || key.trim().startsWith("signature_");
  const oneLine = data ? ONE_LINE_FIELDS[data.key] : undefined;
  // 미리보기는 태그가 있으면 나옵니다. 서명만 볼 수 있게 해 두었더니 접수확인 하단 로고를
  // 고치는 사람은 테스트 메일을 보내 보는 수밖에 없었습니다 — HTML 인 행이 서명만은 아닙니다.
  const canPreview = isSignature || /<\w/.test(body);
  // 지우면 무엇이 없어지는지 — 확인 창이 이 행에서만 다른 문장을 띄웁니다.
  const codeResolved = data?.code_resolved ?? false;

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
        <div className="card"><LoadingBlock lines={6} /></div>
      </>
    );
  }

  return (
    <>
      {/* Left, with the chevron — the same back affordance as every other screen. It sat
          on the right of the header here alone, which read as an action, not a way out. */}
      {backChip}
      <div className="card">
        <div className="page-header">
          <div>
            <h1 className="page-title">
              {id === "new" ? "새 템플릿 작성" : data?.name || "편집"}
            </h1>
          </div>
          {/* 오른쪽 위에 언어 태그가 있었습니다. 바로 아래 칸이 같은 것을 고를 수 있는
              모양으로 말하므로 지웠습니다 — 같은 사실을 두 자리에서 말하면 어느 쪽이
              진짜인지 화면만 봐서는 모릅니다 (2026-08-27 운영자 지시). */}
        </div>

        {oneLine ? (
          <>
            <label className="field-label" htmlFor="et-link">{oneLine.label}</label>
            <input className={`input${oneLine.mono ? " mono" : ""}`} id="et-link"
                   type="text" value={body}
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

            {/* 키는 만들 때만 묻습니다. 고칠 때는 못 바꿉니다 — 발송 경로가 그 이름으로
                꺼내 가므로, 옮기는 순간 조회의 답이 없어집니다. 비우면 서명이 됩니다:
                여기서 만드는 것의 거의 전부라 기본값이 그쪽입니다. */}
            {id === "new" && (
              <>
                <label className="field-label" htmlFor="et-key">키 (비우면 서명으로 만듭니다)</label>
                <input className="input mono" id="et-key" value={key}
                       onChange={(e) => setKey(e.target.value)}
                       placeholder="예: custom_followup" style={{ marginBottom: 14 }} />
              </>
            )}

            {/* **언제든 고칠 수 있습니다** (2026-08-27 운영자 지시). 만들 때만 물어보고
                그 뒤로 못 바꾸면, 잘못 고른 행을 지우고 다시 만드는 수밖에 없습니다 —
                키가 코드 참조라 그럴 수도 없는 행이 있습니다. 자유 입력이 아니라 목록인
                이유: 이 값은 화면에 보여 주는 글자이고, 아무 코드도 임의의 언어 코드로
                행을 고르지 않습니다. `ja` 라고 적어 봐야 읽는 곳이 없습니다. */}
            {/* 언어와 판 번호를 한 줄에 나눠 놓습니다. 언어 고르개가 카드 폭만큼 뻗어
                있었는데, 고를 것이 셋뿐이라 그 폭이 아무 의미도 없었습니다. */}
            <div className="row" style={{ gap: 12, marginBottom: 14, alignItems: "flex-end" }}>
              {!isSignature && (
                <div style={{ flex: 1, minWidth: 0 }}>
                  <label className="field-label" htmlFor="et-lang">언어</label>
                  <select className="select" id="et-lang" value={language}
                          onChange={(e) => setLanguage(e.target.value)}>
                    {LANGUAGES.map((entry) => (
                      <option key={entry.value} value={entry.value}>{entry.label}</option>
                    ))}
                  </select>
                </div>
              )}
              {id !== "new" && (
                <div style={{ flex: 1, minWidth: 0 }}>
                  <label className="field-label" htmlFor="et-version">버전</label>
                  {/* 못 고칩니다 — 사람이 정하는 값이 아니라 저장한 횟수입니다. 고친 것이
                      있으면 저장했을 때 될 번호를 미리 보여 주고, 그게 아직 저장 전이라는
                      것을 옆에 적습니다. */}
                  <input className="input tnum" id="et-version" value={`v${shownVersion}`}
                         readOnly disabled />
                  {dirty && <div className="t-xs t-subtle" style={{ marginTop: 4 }}>저장하면 이 번호가 됩니다</div>}
                </div>
              )}
            </div>

            {/* 서명에는 언어 칸이 없습니다. 어떤 코드도 언어로 서명을 고르지 않고 —
                고르는 것은 사람입니다 — 그래서 그 칸은 아무 데도 가 닿지 않는 질문이었습니다. */}

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

        {/* 저장은 왼쪽, 삭제는 **오른쪽 끝에 휴지통 하나**. 나란히 두면 둘이 같은 무게로
            보이고, 실제로 저장을 누르려다 삭제를 누른 사람이 있었습니다. */}
        <div className="action-bar row-between" style={{ marginTop: 14 }}>
          <div className="row" style={{ gap: 8 }}>
            {/* **바꾼 것이 있을 때만 뜹니다** (2026-08-27 운영자 지시). 늘 떠 있으면 아무것도
                안 고치고 누른 저장이 판을 올리고 이력에 같은 본문을 한 줄 더 남깁니다. */}
            {dirty && (
              <ActionButton className="btn btn--primary btn--editor"
                            pending={id === "new" ? "만드는 중" : "저장 중"} onClick={save}>
                <Icon name="check" size={15} /> {id === "new" ? "생성" : "저장"}
              </ActionButton>
            )}
            {/* 저장 옆이고 **크기가 같습니다** — 나란히 선 둘의 높이·폭이 다르면 한쪽이
                더 중요한 것처럼 읽힙니다. */}
            {data && (
              <RevisionHistoryButton kind="email_template" documentId={data.id}
                                     title={data.name} />
            )}
          </div>
          {/* 무엇이든 지웁니다. 발송 경로가 찾는 행이면 확인 창이 그렇게 말합니다. */}
          {data && (
            <button type="button" className="btn btn--icon btn--danger-ghost"
                    title="삭제" aria-label="삭제" onClick={() => setConfirming(true)}>
              <Icon name="trash" size={16} />
            </button>
          )}
        </div>
        {note && <div className="t-sm" style={{ marginTop: 14 }} role="status">{note}</div>}
      </div>
      {confirming && data && (
        <DeleteDialog
          name={data.name}
          onCancel={() => setConfirming(false)}
          onConfirm={remove}
          warning={codeResolved
            ? `발송 경로가 이 행을 「${data.key}」 라는 이름으로 찾습니다. 지워도 그 조회는 ` +
              "계속 일어나고, 답만 없어집니다 — 접수확인이 하드코딩된 문장으로 떨어지거나, " +
              "회신이 {{MEETING_LINK}} 같은 토큰으로 끝납니다. 7일 동안은 목록에서 되돌릴 " +
              "수 있지만 그 사이 같은 키로 새로 만들 수는 없고, 7일이 지나면 본문이 개정 " +
              "이력까지 사라집니다. 쓰지 않을 생각이라면 내용을 비우는 쪽이 되돌리기 쉽습니다."
            : undefined}
        />
      )}
    </>
  );
}

export function EmailTemplates() {
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind");

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
  // 한 줄 = 한 행입니다. 언어별로 묶지 않습니다: 발송 경로는 키로 찾고 `_en` 행은 영문
  // 문의만 읽으므로, 묶어 두면 국문 행을 고쳐 놓고 영문이 그대로인 이유가 화면에
  // 없습니다. 키 순서로 세워 같은 템플릿의 두 언어가 나란히 붙습니다.
  const items = data.items.filter((item) => item.kind === kind);
  if (kind === "template") items.sort((a, b) => a.key.localeCompare(b.key));
  const columns: Column<Item>[] = [
    // "기본" 표시는 없앴습니다: 어느 서명을 쓸지는 초안마다 고르는 것이고, 목록에 미리
    // 정해 둔 하나를 표시하면 그게 강제인 것처럼 읽힙니다.
    { label: "템플릿 이름", width: kind === "signature" ? "44%" : "34%",
      cell: (row) => (
        <>
          <strong>{row.name}</strong>
          {/* 발송 경로가 이 행을 찾을 때 부르는 이름입니다. 열을 하나 더 내는 대신 이름
              아래에 둡니다 — 폭을 다시 나눌 필요가 없고, 이름과 신원은 같은 자리에서
              읽히는 편이 낫습니다. 「답변 메일 형식」이 둘인 이유가 여기 적혀 있습니다. */}
          <div className="t-xs mono t-subtle">{row.key}</div>
        </>
      ) },
    // 이 행이 어느 언어의 행인지. 서명에는 언어라는 것이 없으므로 (고르는 것은
    // 사람입니다) 서명 목록에서는 이 칸 자체를 뺍니다.
    // 이름을 반만 쓰고 남은 폭을 언어·버전에 줍니다 (2026-08-27 운영자 지시) — 이름은
    // 한 줄이면 읽히는데 그 뒤가 비어 있었습니다.
    { label: "버전", width: "10%", className: "tnum td-subtle",
      cell: (row) => `v${row.version}` },
    // 「발송 경로 사용」 열이 여기 있었습니다. 남아 있는 행이 전부 「사용」이라 아무것도
    // 구별해 주지 않았습니다 (2026-08-27 운영자 지적). 그 사실이 정말 필요한 순간은
    // **지울 때**이고, 삭제 확인 창이 그때 같은 `is_code_resolved` 로 경고합니다.
    ...(kind === "signature" ? [] : [{ label: "언어", width: "14%",
      cell: (row: Item) => (
        <span className="t-subtle t-xs">{LANGUAGE_LABELS[row.language] ?? row.language}</span>
      ) },
    ]),
    // 연도까지. 이 열은 "얼마나 오래됐나" 를 보는 자리이고, 월·일만 있으면 작년 것과 올해
    // 것이 같은 글자로 보입니다.
    // 언제 · 누가. `author` 는 **만든 사람이 아니라 마지막으로 저장한 사람**입니다(0100) —
    // 만든 사람은 이 행이 처음 생긴 뒤로 아무 질문에도 답하지 않았습니다.
    { label: "수정", width: "22%", className: "td-subtle tnum",
      cell: (row) => (
        <>
          <div>{kst(row.updated_at) || "—"}</div>
          {row.author && <div className="t-xs t-subtle">{row.author}</div>}
        </>
      ) },
    {
      // 정책 문서 목록과 같은 자리. 줄을 클릭해도 열리지만, 지우려고 들어갔다 나오는
      // 왕복이 없어야 합니다.
      width: "12%",
      cell: (row) => (
        <div className="row" style={{ gap: 6 }} onClick={(e) => e.stopPropagation()}>
          {/* 삭제 버튼은 여기 없습니다. 열어서 지웁니다 — 확인 창이 문장을 옮겨 적으라고
              하는데, 무엇을 지우는지 안 보고 옮겨 적는 것은 확인이 아닙니다. */}
          <button type="button" className="btn btn--subtle btn--sm"
                  onClick={() => setParams({ kind, edit: String(row.id) })}>수정</button>
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
            rows={items}
            rowKey={(row) => String(row.id)}
            empty="템플릿이 없습니다"
            onRowClick={(row) => setParams({ kind, edit: String(row.id) })}
          />
      </div>
    </>
  );
}
