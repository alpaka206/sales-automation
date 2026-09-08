import type { Dispatch, ReactNode, SetStateAction } from "react";
import { Link } from "react-router-dom";
import { Icon } from "./Icon";
import { RecordValueRow, type HubSpotRecord } from "./PlanCard";
import { kst } from "../lib/format";

/** 티켓 화면 오른쪽의 **한 상자** — 티켓 정보 · 플랜 · 연락처 (2026-09-07 운영자 지시로
 *  카드 셋을 합쳤습니다).
 *
 *  화면(`MessageDetail`)에서 떼어냈습니다 (2026-09-08). 초안 편집기와 같은 이유입니다:
 *  188줄이 **하나의 일**이고(이 문의가 누구의 무엇인지 보여 주고 고치게 한다) 경계가
 *  분명해서 넘길 값이 열둘로 끝납니다.
 *
 *  **상태를 안 갖습니다.** 연필을 눌렀는지(`editing`)도, 저장도 부르는 쪽이 들고 있습니다 —
 *  이 카드의 저장은 **두 곳으로 갈라져 나가고**(티켓의 플랜 스냅샷 · 연락처의 회사·메모)
 *  그 순서가 이 화면의 규칙이라, 여기서 흉내 내면 규칙이 두 벌이 됩니다.
 */
/** 이 카드가 그리는 두 덩어리. **화면의 타입을 import 하지 않습니다** — `ui/` 가
 *  `screens/` 를 가리키면 화면을 고칠 때마다 이 컴포넌트를 같이 봐야 합니다. 대신 같은
 *  모양을 여기 적고, 어긋나면 호출부에서 TypeScript 가 잡습니다. */
export type TicketContact = {
  id: number; name: string; email: string | null; company: string | null;
  domain: string | null; role_description: string | null; website: string | null;
  qualification: string; lifecycle: string;
};
export type TicketInfo = {
  id: number | null; ticket_id: string | null; stage: string | null;
  deal_detail: string | null; inquiry_subject: string | null;
  inquiry_language: string | null; client_id: number | null;
  created_at: string | null;
};

export function TicketInfoCard({
  data, contact, ticket, msg, hubspot, hubspotPending,
  editing, setEditing, onSave, dealOptions, onSaveDealDetail, confirm,
}: {
  /** 이 카드가 실제로 읽는 칸만 적습니다 — 화면의 큰 타입을 통째로 끌어오면 안 쓰는
   *  값까지 넘겨야 하고, 그러면 이 카드가 그 화면에 묶입니다. */
  data: { stage_labels: Record<string, string> };
  contact: TicketContact | null;
  ticket: TicketInfo;
  msg: { to_address: string; sent_at: string | null } | null;
  hubspot?: HubSpotRecord;
  hubspotPending: boolean;
  editing: boolean;
  /** 세터 그대로 받습니다 — 연필 버튼이 `(on) => !on` 로 뒤집습니다. */
  setEditing: Dispatch<SetStateAction<boolean>>;
  onSave: (fields: Record<string, string>) => Promise<void>;
  dealOptions?: string[];
  onSaveDealDetail: (detail: string) => Promise<void>;
  /** 확인 창을 띄우는 쪽. **이 카드가 직접 안 띄웁니다** — 이 화면의 확인 창은 하나이고,
   *  둘이 되면 어느 쪽이 떠 있는지 화면이 스스로 모릅니다. */
  confirm: (ask: { description: ReactNode; run: () => Promise<void> }) => void;
}) {
  return (
      <div className="card">
        <div className="row-between" style={{ marginBottom: 12 }}>
          <div className="section-label">문의 정보</div>
          {contact && (
            <button type="button" className="btn btn--subtle btn--sm"
                    onClick={() => setEditing((on) => !on)}
                    aria-pressed={editing}
                    aria-label={editing ? "수정 취소" : "수정"}
                    title={editing ? "수정 취소" : "수정"}>
              <Icon name={editing ? "x" : "edit"} size={14} />
            </button>
          )}
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            // FormData 는 이 시점의 스냅숏입니다 — 확인 창을 지나면
            // event.currentTarget 은 이미 없습니다.
            const fields = Object.fromEntries(
              new FormData(event.currentTarget) as never,
            ) as Record<string, string>;
            confirm({
              description: (
                <>
                  이 문의의 플랜 정보와 이 고객의 회사·메모를 저장합니다. 회사:{" "}
                  <strong>{fields.company?.trim() || "—"}</strong>
                </>
              ),
              run: () => onSave(fields),
            });
          }}
        >
          <dl className="info-list">
            <div className="info-row"><dt>티켓</dt><dd className="mono">{ticket.ticket_id ? `#${ticket.ticket_id}` : "— (없음)"}</dd></div>
            <div className="info-row"><dt>Client ID</dt><dd className="tnum">{ticket.client_id ?? "미동기화"}</dd></div>
            {/* 티켓이 만들어진 날 (2026-09-03 운영자 요청). 백필이 허브스팟의 생성일을
                그대로 복사해 두므로 우리 값이 곧 허브스팟 값입니다 — 이 화면을 열 때마다
                허브스팟에 물으러 가지 않습니다. */}
            {ticket.created_at && (
              <div className="info-row"><dt>생성</dt><dd className="tnum">{kst(ticket.created_at)}</dd></div>
            )}
            {/* 「발송 정보」 카드를 지우면서(2026-09-03 운영자 지시) **수신자 한 줄만**
                여기로 옮겼습니다. 나머지(채널·발송 언어·생성)는 다른 데서도 볼 수 있는데
                수신 주소는 이 콘솔에서 볼 곳이 여기와 발송 확인 창뿐이었습니다 — 확인
                창은 초안이 열려 있을 때만 잠깐 뜨므로, 이미 나간 메일의 수신 주소를 볼
                자리가 통째로 사라질 뻔했습니다. */}
            {/* 아래 연락처 칸의 「이메일」 줄은 지웠습니다 (2026-09-07 운영자 지시) —
                같은 주소를 한 상자에서 두 번 적고 있었습니다. **메일이 없는 티켓에서도
                주소는 남아야** 하므로(백필로 들여온 건은 `msg` 가 없습니다) 연락처
                주소로 떨어집니다. */}
            {(msg?.to_address || contact?.email) && (
              <div className="info-row"><dt>수신자</dt>
                <dd className="mono truncate" style={{ maxWidth: 170 }}>
                  {msg?.to_address || contact?.email}
                </dd>
              </div>
            )}
            {msg?.sent_at && (
              <div className="info-row"><dt>발송</dt><dd className="tnum">{kst(msg.sent_at)}</dd></div>
            )}
            {ticket.stage && <div className="info-row"><dt>Stage</dt><dd>{data.stage_labels[ticket.stage] ?? ticket.stage}</dd></div>}
            {/* Won 과 Lost 일 때만 나옵니다 — 왜 이겼나 / 왜 졌나는 결말이 난 건에만
                있는 정보입니다. 보드 카드에도 같은 고르개가 있고, 값 목록과 「지금
                단계의 값인가」 판단은 둘 다 서버에서 옵니다. 여기 둔 이유: 이 화면에서
                대화를 다 읽고 결론을 내리는데, 그걸 적으려고 대시보드로 나가 카드를
                찾아야 했습니다. */}
            {dealOptions && (
              <div className="info-row"><dt>Deal Detail</dt>
                <dd>
                  <select className="select select--inline" value={ticket.deal_detail ?? ""}
                          aria-label={ticket.stage === "won" ? "Won Type" : "Lost Reason"}
                          onChange={(event) => {
                            const detail = event.target.value;
                            confirm({
                              description: (
                                <>
                                  Deal Detail 을 <strong>{detail || "선택 안 함"}</strong> 로
                                  바꿉니다.
                                </>
                              ),
                              run: () => onSaveDealDetail(detail),
                            });
                          }}>
                    <option value="">선택 안 함</option>
                    {dealOptions.map((option) => (
                      <option key={option} value={option}>{option}</option>
                    ))}
                  </select>
                </dd></div>
            )}

            {/* ── 플랜 ─────────────────────────────────────────────────
                **이 티켓이 들고 있는 문의 시점 값입니다** (0110). 같은 값이 리드
                히스토리에서는 지금 값이고, 한쪽을 고쳐도 다른 쪽은 안 바뀝니다.

                **머리글 옆에 「이 문의 시점 / 현재 값」을 적던 자리입니다**
                (2026-09-07 운영자 지시로 뺐습니다). 이관 0110 뒤에 들어온 문의는
                전부 얼린 값을 들고 있어서 그 글자는 모든 티켓에 같은 말을 하나씩 더
                얹을 뿐이고, 「현재 값」이 뜨는 옛 티켓에서는 오히려 **틀린 값을 보고
                있나** 하고 읽혔습니다. 그 300여 건은 문의 시점 값이 어디에도 안
                남아 있어 만들어 낼 수 없습니다 — 한 번 고쳐 저장하면 그때부터 자기
                값을 갖습니다. */}
            <div className="info-row info-row--head"><dt>플랜</dt><dd /></div>
            {hubspotPending && (
              <div className="info-row"><dt>&nbsp;</dt>
                <dd className="t-xs t-subtle"><span className="spinner" role="status" /> 읽는 중</dd>
              </div>
            )}
            {hubspot?.groups
              ?.filter((group) => group.key !== "contact")
              .flatMap((group) => group.rows)
              .filter((row) => row.on_ticket)
              .map((row) => (
                <RecordValueRow key={row.key} row={row} editing={editing} />
              ))}

            {contact && (<>
              <div className="info-row info-row--head"><dt>연락처</dt><dd /></div>
              {/* **머리글이 적은 것을 여기서 또 적지 않습니다** (2026-09-07 운영자
                  지시). 왼쪽 위 제목은 `회사 || 이름` 이라, 회사가 없으면 거기 이름이
                  서 있습니다 — 그때 이 줄은 같은 말을 두 번 하는 것입니다. 회사가
                  있을 때만 남기는 이유는 그때 이름이 화면 어디에도 없어서입니다. */}
              {contact.company && (
                <div className="info-row"><dt>이름</dt><dd>{contact.name}</dd></div>
              )}
              {contact.domain && (
                <div className="info-row"><dt>도메인</dt>
                  <dd><Link className="mono" to={`/companies/${contact.domain}`}>{contact.domain}</Link></dd></div>
              )}
              {hubspot?.groups
                ?.find((group) => group.key === "contact")
                ?.rows.map((row) => <RecordValueRow key={row.label} row={row} />)}
              {/* 이 사람이 리드인지 제품을 쓰는 고객인지 — 구독 플랜이 정합니다
                  (2026-09-02 운영자 지시). 바로 위 플랜 줄이 그 플랜이라, 같은 사실의
                  두 면이 한 상자 안에 나란히 섭니다. **플랜 묶음의 줄로 넣지는
                  않습니다**: 허브스팟에 대응 속성이 없어서, 저기 서면 「허브스팟이 아는
                  값」으로 읽히고 고칠 수 있는 칸처럼 보입니다. */}
              <div className="info-row"><dt>Lead Type</dt><dd>{contact.qualification}</dd></div>
              {/* 같은 사실의 셋째 면 — **이 티켓의 단계**를 영업이 부르는 이름입니다
                  (2026-09-07 운영자 지시). New 면 바로 위 줄과 같은 말을 합니다:
                  아직 아무도 안 만난 리드라 그 자리에 설 다른 이름이 없습니다. */}
              <div className="info-row"><dt>Lifecycle Stage</dt><dd>{contact.lifecycle}</dd></div>
                {!editing && (
                  <div className="info-row"><dt>회사</dt>
                    <dd className="truncate">{contact.company || "—"}</dd></div>
                )}
                {/* 허브스팟 「Company Record」의 Website URL (0111). **빈 줄을 세우지
                    않습니다** — 플랜 칸들과 달리 이건 허브스팟 사이드바를 옮겨 놓은
                    묶음이 아니라 우리가 한 줄 얹은 것이고, 실측상 회사 열에 아홉 중
                    여덟이 비어 있어 언제나 「—」인 줄이 하나 더 서게 됩니다. */}
                {contact.website && (
                  <div className="info-row"><dt>웹사이트</dt>
                    <dd className="truncate">
                      <a href={contact.website} target="_blank" rel="noreferrer noopener">
                        {contact.website}
                      </a>
                    </dd></div>
                )}
            </>)}
          </dl>
          {!editing && contact?.role_description && (
            <div style={{ marginTop: 12 }}>
              <div className="field-label">하는 일 / 메모</div>
              <p className="t-xs" style={{ margin: 0, whiteSpace: "pre-line" }}>
                {contact.role_description}
              </p>
            </div>
          )}

          {/* 대화하며 알게 되는 값들. **gmail·미확인 고객이 회사 이름을 갖는
              유일한 자리입니다.** 위의 플랜 칸과 같은 폼 안에 있어서 저장은 한 번입니다. */}
          {editing && (
            <div style={{ marginTop: 12 }}>
              <label className="field-label" htmlFor="c-company">회사</label>
              <input className="input" id="c-company" name="company"
                     defaultValue={contact?.company ?? ""} style={{ marginBottom: 10 }} />
              <label className="field-label" htmlFor="c-role">하는 일 / 메모</label>
              <textarea className="textarea" id="c-role" name="role_description" rows={3}
                        defaultValue={contact?.role_description ?? ""}
                        placeholder="이 고객·회사가 어떤 일을 하는지 (대화하며 알게 된 내용 포함). gmail·미확인이어도 입력해 저장됩니다." />
              <button className="btn btn--subtle btn--sm" type="submit"
                      style={{ marginTop: 10, width: "100%" }}>
                <Icon name="check" size={14} /> 저장
              </button>
            </div>
          )}
        </form>
      </div>
  );
}
