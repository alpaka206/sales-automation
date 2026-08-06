import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { getJSON, postForm } from "../../lib/api";
import { useAction } from "../../ui/ActionButton";
import { Confirm } from "./Confirm";
import {
  type Claim, type Contract, type Grant, type ListData, type Options, type Payment, type Row,
  dueClass, dueText, fmt, initials, money, n, num,
} from "./shared";

/** 수주 고객 상세 — 목업의 8개 섹션.
 *
 * 계약 선택 드롭다운이 이 화면의 축입니다. 고객은 하나이고 계약이 여럿이라, 3~7번 섹션은
 * 전부 "지금 고른 계약" 의 내용입니다. 8번 소통 히스토리만 고객 단위로 쌓입니다 — 협상
 * 단계 대화가 계약보다 먼저 있기 때문입니다.
 */
const SECTIONS: [string, string][] = [
  ["sec-basic", "고객 기본 정보"],
  ["sec-contract", "계약 · 결제 정보"],
  ["sec-plan", "Perso 계정 · 플랜"],
  ["sec-credit", "크레딧 지급"],
  ["sec-pay", "결제 현황"],
  ["sec-care", "클레임 / 히스토리"],
  ["sec-revenue", "매출 관리"],
  ["sec-comm", "소통 히스토리"],
];

const AVATAR_COLORS = ["#0F766E", "#B45309", "#3730A3", "#B42318", "#026AA2", "#4B5563"];

export function WonCustomerDetail() {
  const { clientId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["won-customer", clientId],
    queryFn: () => getJSON<Row & { comms: NonNullable<Row["comms"]> }>(`/api/ui/won-customers/${clientId}`),
  });
  // 선택지(산업·플랜 상태·담당부서)는 목록 payload 가 이미 들고 있습니다. 같은 쿼리 키라
  // 캐시에서 나오고, 이 화면 때문에 왕복이 하나 더 생기지 않습니다.
  const { data: list } = useQuery({
    queryKey: ["won-customers"],
    queryFn: () => getJSON<ListData>("/api/ui/won-customers"),
  });
  const [pickedSeq, setPickedSeq] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [commFilter, setCommFilter] = useState<"all" | "nego" | number>("all");

  // 액션 보드가 `/won-customers/2102#sec-care` 로 보냅니다. 브라우저의 기본 앵커 이동은
  // 소용이 없습니다 — 그 시점에 섹션이 아직 그려지지 않았습니다. 데이터가 온 **뒤에**
  // 한 번 내려갑니다. 훅은 아래 early return 보다 위에 있어야 합니다(#310).
  useEffect(() => {
    if (!data) return;
    const id = window.location.hash.slice(1);
    if (!id) return;
    // 렌더 직후에는 아직 레이아웃이 잡히기 전이라, 다음 프레임에 찾습니다.
    const timer = setTimeout(
      () => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" }),
      60,
    );
    return () => clearTimeout(timer);
  }, [data]);

  // 지금 보고 있는 섹션. 8개가 한 화면에 이어져 있어서 스크롤하다 보면 어디쯤인지 놓칩니다.
  //
  // IntersectionObserver 를 쓰는 이유: scroll 이벤트로 위치를 계산하면 스크롤할 때마다 8개
  // 섹션의 좌표를 다시 재게 됩니다. 관찰자는 화면에 들어오고 나갈 때만 부릅니다.
  //
  // `rootMargin` 위쪽이 큰 이유는 머리글이 sticky 라서입니다 — 그 아래로 들어온 섹션은
  // 가려져 있는데도 "보인다" 고 나옵니다. 화면 위쪽 1/4 을 감지선으로 씁니다.
  const [section, setSection] = useState<string>(SECTIONS[0][0]);
  useEffect(() => {
    if (!data) return;
    const seen = new Map<string, number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) seen.set(entry.target.id, entry.intersectionRatio);
        // 가장 많이 보이는 섹션. 동률이면 위쪽 섹션이 이깁니다(SECTIONS 순서).
        let best = "", ratio = 0;
        for (const [id] of SECTIONS) {
          const value = seen.get(id) ?? 0;
          if (value > ratio) { best = id; ratio = value; }
        }
        if (best) setSection(best);
      },
      { rootMargin: "-150px 0px -55% 0px", threshold: [0, 0.25, 0.5, 1] },
    );
    for (const [id] of SECTIONS) {
      const element = document.getElementById(id);
      if (element) observer.observe(element);
    }
    return () => observer.disconnect();
  }, [data]);

  const refresh = () => queryClient.invalidateQueries();

  if (!data) return <div className="won"><div className="page">불러오는 중…</div></div>;

  const today = new Date().toISOString().slice(0, 10);
  const contracts = data.contracts ?? [];
  const current =
    contracts.find((c) => c.seq === pickedSeq) ?? data.active ?? contracts[contracts.length - 1] ?? null;

  return (
    <div className="won">
      <div className="detail-head">
        <div className="dh-top">
          <div className="avatar" style={{
            width: 40, height: 40, borderRadius: 9, fontSize: 14,
            background: AVATAR_COLORS[data.client_id % AVATAR_COLORS.length],
          }}>{initials(data.company)}</div>
          <div>
            <h1 className="dh-title">{data.company}</h1>
            {/* 이 줄은 "이 고객이 누구인가" 를 한 줄로 말합니다. 담당자 이름은 뺐습니다 —
                바로 아래 기본 정보 섹션에 있고, 여기서는 고객을 분류하는 값이 먼저입니다. */}
            <div className="dh-sub">
              {["Client ID " + data.client_id, data.customer_type,
                data.industry, data.country, data.department]
                .map((part) => part || "—").join(" · ")}
            </div>
          </div>
          <div className="dh-right">
            <button className="btn btn-sm btn-primary" type="button"
                    onClick={() => navigate(`/won-customers/${data.client_id}/contracts/new`)}>+ 계약 추가</button>
            <button className="btn btn-sm" type="button" onClick={() => navigate("/won-customers")}>← 목록</button>
          </div>
        </div>
        <div className="dh-tags">
          <span className={`tag ${data.plan_status === "사용중" ? "st-live" : data.plan_status === "세팅중" ? "st-setup" : "st-stop"}`}>
            {data.plan_status}
          </span>
          {data.setup_count > 0 && <span className="tag st-setup">세팅중 계약 {data.setup_count}</span>}
          {data.open_claims > 0 && <span className="tag risk">미처리 {data.open_claims}</span>}
          {current && <span className={`tag ${current.deal_type === "MRR" ? "d-mrr" : "d-poc"}`}>{current.deal_type}</span>}
        </div>
        <div className="secnav">
          {SECTIONS.map(([id, label]) => (
            <button key={id} type="button" className={section === id ? "is-on" : undefined}
                    aria-current={section === id ? "true" : undefined}
                    onClick={() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" })}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="detail-body">
        <BasicSection client={data} contracts={contracts} options={list?.options}
                      onDone={refresh} />

        <Section id="sec-contract" title="계약 및 결제 정보"
                 right={
                   <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                     <select className="select" value={current?.seq ?? ""}
                             onChange={(event) => setPickedSeq(Number(event.target.value))}>
                       {contracts.map((c) => (
                         <option key={c.seq} value={c.seq}>{c.label} · {c.state}</option>
                       ))}
                     </select>
                     <button className="btn btn-sm" type="button" onClick={() => setShowAll(!showAll)}>
                       {showAll ? "접기" : "전체 계약 내역"}
                     </button>
                     {current && (
                       <button className="btn btn-sm" type="button"
                               onClick={() => navigate(`/won-customers/${data.client_id}/contracts/${current.id}`)}>
                         수정
                       </button>
                     )}
                   </div>
                 }>
          {!current ? (
            <div className="board-empty">등록된 계약이 없습니다. 위의 <b>+ 계약 추가</b>로 시작하세요.</div>
          ) : (
            <>
              <div className="field-grid c3">
                <KV k="수주 유형" v={current.deal_type} />
                <KV k="Ticket ID" v={current.ticket_id || "인바운드 연동 없음"} />
                <KV k="계약기간" v={`${fmt(current.starts_on)} – ${fmt(current.ends_on)} (${current.months}개월)`} />
                <KV k="계약서 유형" v={(current.doc_types || []).join(" + ") || "—"} />
                <KV k="계약 크레딧" v={`${num(current.credits)} 크레딧`} />
                <KV k="총 계약금액 (VAT 포함)" v={money(current.amount_incl_vat, current.currency)} />
                <KV k="공급가 (VAT 제외)" v={money(current.amount_excl_vat, current.currency)} />
                <KV k="분당 단가" v={current.unit_price ? money(current.unit_price, current.unit_currency || current.currency) : "—"} />
                <KV k="결제 수단" v={current.payment_method} />
                <KV k="결제 방식" v={`${current.payment_type || "—"}${current.installments ? ` · ${current.installments}회` : ""}`} />
                <KV k="최초 결제일" v={fmt(current.first_payment_on)} />
                <KV k="Billing Email" v={current.billing_email} />
              </div>
              {current.note && <p className="note-box">{current.note}</p>}
              {showAll && (
                <div className="table-wrap" style={{ marginTop: 14 }}>
                  <table>
                    <thead><tr>
                      <th>계약</th><th>상태</th><th>수주 유형</th><th>계약기간</th>
                      <th>플랜</th><th>총 계약금액</th><th>계약 크레딧</th>
                    </tr></thead>
                    <tbody>
                      {contracts.map((c) => (
                        <tr key={c.seq} onClick={() => { setPickedSeq(c.seq); setShowAll(false); }}>
                          <td>{c.label}</td>
                          <td><span className="tag neutral">{c.state}</span></td>
                          <td>{c.deal_type}</td>
                          <td className="nowrap">{fmt(c.starts_on)} – {fmt(c.ends_on)}</td>
                          <td>{c.plan || "—"}</td>
                          <td className="nowrap">{money(c.amount_incl_vat, c.currency)}</td>
                          <td className="nowrap">{num(c.credits)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </Section>

        {current && (
          <>
            <Section id="sec-plan" title="Perso 계정 및 플랜">
              <div className="field-grid c3">
                <KV k="플랜" v={current.plan} />
                <KV k="플랜명" v={current.plan_name} />
                <KV k="Perso Email" v={current.perso_email} />
                <KV k="플랜 시작일" v={fmt(current.plan_starts_on)} />
                <KV k="플랜 만료일" v={fmt(current.plan_ends_on)} />
                <KV k="잔여일수" v={current.plan_days_left === null ? "—" : `${current.plan_days_left}일`} />
                <KV k="Account Invitation Limit" v={current.invite_limit?.toString()} />
                <KV k="Queue limit" v={current.queue_limit?.toString()} />
                <KV k="Concurrent Jobs" v={current.concurrent_jobs?.toString()} />
                <KV k="Space 개수" v={current.space_count?.toString()} />
                <KV k="space_seq" v={current.space_seq} />
              </div>
            </Section>

            <CreditSection contract={current} today={today} onDone={refresh} />
            <PaySection contract={current} today={today} onDone={refresh} />
            <CareSection contract={current} onDone={refresh} />

            <Section id="sec-revenue" title="매출 관리">
              <div className="stat-row">
                <Stat label="계약 종류" value={current.deal_type} />
                <Stat label="총 계약 금액 (VAT 포함)" value={money(current.amount_incl_vat, current.currency)} />
                <Stat label="월간 매출 (VAT 포함)"
                      value={current.deal_type === "MRR"
                        ? `${money(current.monthly_revenue, current.currency)} / 월`
                        : "결제월에 일시 인식"} />
                <Stat label="매출 인식 시작 월"
                      value={`${current.revenue_from || "—"} ${current.revenue_from_set ? "(직접 지정)" : "(계약 시작월)"}`} />
              </div>
            </Section>
          </>
        )}

        <Section id="sec-comm" title="소통 히스토리"
                 right={<span className="muted">고객 단위 · 전체 계약 통합</span>}>
          {data.contact_id && (
            <CommForm contactId={data.contact_id} contracts={contracts} onDone={refresh} />
          )}
          <div className="chips" style={{ marginBottom: 12 }}>
            <button type="button" className={`chip${commFilter === "all" ? " is-on" : ""}`}
                    onClick={() => setCommFilter("all")}>전체</button>
            <button type="button" className={`chip${commFilter === "nego" ? " is-on" : ""}`}
                    onClick={() => setCommFilter("nego")}>협상 단계 (계약 전)</button>
            {contracts.map((c) => (
              <button key={c.seq} type="button" className={`chip${commFilter === c.seq ? " is-on" : ""}`}
                      onClick={() => setCommFilter(c.seq)}>{c.label}</button>
            ))}
          </div>
          {(data.comms ?? [])
            .filter((item) =>
              commFilter === "all" ? true
              : commFilter === "nego" ? !item.contract_seq
              : item.contract_seq === commFilter)
            .map((item) => (
              <div key={item.id} className="tl-item">
                <div className="tl-meta">
                  <span className="mono">{fmt(item.happened_at?.slice(0, 10))}</span>
                    <span className="tag neutral">{item.channel}</span>
                    {item.handler && <span className="muted">{item.handler}</span>}
                    {item.contract_seq
                      ? <span className="tag blue">{item.contract_seq}차 계약</span>
                      : <span className="tag neutral">협상 단계</span>}
                </div>
                <div className="tl-text">{item.subject ? `${item.subject} — ` : ""}{item.summary}</div>
              </div>
            ))}
          {!(data.comms ?? []).length && <div className="board-empty">기록이 없습니다.</div>}
        </Section>
      </div>
    </div>
  );
}

/** 고객 기본 정보 — 읽기와 편집이 같은 자리에서 바뀝니다.
 *
 * 편집할 수 없는 세 칸이 있습니다. **Client ID** 는 고객의 신원이라 바꾸면 계약·크레딧·
 * 소통 히스토리가 통째로 남의 것이 됩니다. **고객 종류** 는 그 번호대에서 파생되는 값이고,
 * **연동 티켓** 은 계약이 들고 있는 것이라 계약 폼에서 고칩니다. 세 칸은 편집 중에도 그대로
 * 보여 줍니다 — 사라지면 "왜 없지" 를 확인하러 나갔다 와야 합니다.
 */
function BasicSection({ client, contracts, options, onDone }: {
  client: Row;
  contracts: Contract[];
  options: Options | undefined;
  onDone: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    company: client.company,
    industry: client.industry ?? "",
    country: client.country ?? "",
    department: client.department ?? "",
    contact_name: client.contact_name ?? "",
    contact_info: client.contact_info ?? "",
    first_won_on: client.first_won_on ?? "",
    plan_status: client.plan_status,
    owner: client.owner ?? "",
  });
  const set = (key: keyof typeof form, value: string) =>
    setForm((current) => ({ ...current, [key]: value }));

  const [save, saving] = useAction(async () => {
    await postForm(`/won-customers/${client.client_id}`, form);
    setEditing(false);
    onDone();
  });

  const tickets =
    contracts.filter((c) => c.ticket_id).map((c) => `${c.seq}차 ${c.ticket_id}`).join(" · ")
    || "연동 없음";

  if (!editing) {
    return (
      <Section id="sec-basic" title="고객 기본 정보"
               right={<button className="btn btn-sm" type="button"
                              onClick={() => setEditing(true)}>편집</button>}>
        <div className="field-grid c3">
          <KV k="고객사" v={client.company} />
          <KV k="산업 분야" v={client.industry} />
          <KV k="국가" v={client.country} />
          <KV k="담당부서" v={client.department} />
          <KV k="Client ID" v={String(client.client_id)} />
          <KV k="고객 종류" v={client.customer_type} />
          <KV k="고객 담당자" v={client.contact_name} />
          <KV k="고객 연락처" v={client.contact_info} />
          <KV k="최초 수주일" v={fmt(client.first_won_on)} />
          <KV k="플랜 상태" v={client.plan_status} />
          <KV k="담당" v={client.owner} />
          <KV k="연동 티켓 (계약별)" v={tickets} />
        </div>
      </Section>
    );
  }

  return (
    <Section id="sec-basic" title="고객 기본 정보">
      <div className="form-grid3">
        <div>
          <label className="form-label">고객사</label>
          <input className="inp" value={form.company} onChange={(e) => set("company", e.target.value)} />
        </div>
        <div>
          <label className="form-label">산업 분야</label>
          {/* 목록에 없으면 직접 입력합니다 — 운영자가 시트에서 쓰던 방식 그대로. */}
          <input className="inp" list="won-industries" value={form.industry}
                 onChange={(e) => set("industry", e.target.value)} />
          <datalist id="won-industries">
            {(options?.industries ?? []).map((item) => <option key={item} value={item} />)}
          </datalist>
        </div>
        <div>
          <label className="form-label">국가</label>
          <input className="inp" value={form.country} onChange={(e) => set("country", e.target.value)} />
        </div>
        <div>
          <label className="form-label">담당부서</label>
          <select className="inp" value={form.department} onChange={(e) => set("department", e.target.value)}>
            {["", ...(options?.departments ?? [])].map((item) => (
              <option key={item} value={item}>{item || "—"}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="form-label">Client ID</label>
          <div className="field-value">{client.client_id} <span className="muted">(바꿀 수 없음)</span></div>
        </div>
        <div>
          <label className="form-label">고객 종류</label>
          <div className="field-value">{client.customer_type} <span className="muted">(번호대에서 파생)</span></div>
        </div>
        <div>
          <label className="form-label">고객 담당자</label>
          <input className="inp" value={form.contact_name} onChange={(e) => set("contact_name", e.target.value)} />
        </div>
        <div>
          <label className="form-label">고객 연락처</label>
          <input className="inp" value={form.contact_info} onChange={(e) => set("contact_info", e.target.value)} />
        </div>
        <div>
          <label className="form-label">최초 수주일</label>
          <input className="inp" type="date" value={form.first_won_on}
                 onChange={(e) => set("first_won_on", e.target.value)} />
        </div>
        <div>
          <label className="form-label">플랜 상태</label>
          <select className="inp" value={form.plan_status} onChange={(e) => set("plan_status", e.target.value)}>
            {(options?.plan_statuses ?? []).map((item) => <option key={item}>{item}</option>)}
          </select>
        </div>
        <div>
          <label className="form-label">담당</label>
          <input className="inp" value={form.owner} onChange={(e) => set("owner", e.target.value)} />
        </div>
        <div>
          <label className="form-label">연동 티켓 (계약별)</label>
          <div className="field-value">{tickets} <span className="muted">(계약에서 고침)</span></div>
        </div>
      </div>
      <div className="modal-foot" style={{ marginTop: 14 }}>
        <button className="btn btn-sm" type="button" onClick={() => setEditing(false)}>취소</button>
        <button className="btn btn-sm btn-primary" type="button" disabled={saving}
                onClick={() => save()}>{saving ? "저장 중" : "저장"}</button>
      </div>
    </Section>
  );
}

/** 섹션 하나. 내용은 `.panel` 안에 들어갑니다.
 *
 * 목업이 각 섹션 내용을 흰 카드(테두리 + 라운드 + 여백)로 감싸는데, 그 래퍼가 빠져 있어서
 * 값들이 배경 위에 그냥 떠 있었습니다. 8개 섹션이 한 화면에 이어지는 구조라 카드가 없으면
 * 어디서 어디까지가 한 섹션인지 눈으로 끊기지 않습니다 — 제목 글자 크기만으로는 부족합니다.
 */
function Section({ id, title, right, children }: {
  id: string; title: string; right?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <section className="sec" id={id}>
      <div className="sec-head">
        <h2 className="sec-title">{title}</h2>
        <div className="sec-actions">{right}</div>
      </div>
      <div className="panel">{children}</div>
    </section>
  );
}

function KV({ k, v }: { k: string; v: string | null | undefined }) {
  return (
    <div>
      <div className="field-label">{k}</div>
      <div className="field-value">{v || "—"}</div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

function CreditSection({ contract, today, onDone }: {
  contract: Contract; today: string; onDone: () => void;
}) {
  const done = contract.credit_grants.filter((g) => g.done);
  const pending = contract.credit_grants.filter((g) => !g.done);
  // 계약 크레딧 대비 지급 진행률. 100%를 넘을 수 있습니다 — 테스트·보상 지급은 계약분
  // 밖이라, 넘은 것이 곧 오류는 아닙니다. 그래서 자르지 않고 그대로 보여 줍니다.
  const percent = contract.credits
    ? Math.round((contract.granted_credits / contract.credits) * 100)
    : 0;

  const [ask, setAsk] = useState<Grant | null>(null);
  const [editing, setEditing] = useState<Grant | null>(null);
  const [adding, setAdding] = useState(false);

  async function save(id: number, fields: Record<string, string>) {
    await postForm(`/won-customers/credits/${id}`, fields);
    onDone();
  }

  return (
    <Section id="sec-credit" title="크레딧 지급 현황"
             right={<button className="btn btn-sm" type="button"
                            onClick={() => setAdding(!adding)}>+ 지급 회차</button>}>
      <div className="stat-row">
        <Stat label="계약 크레딧" value={num(contract.credits)} />
        <Stat label="누적 지급 크레딧" value={num(contract.granted_credits)} />
        <Stat label="다음 지급일" value={contract.next_credit_on ? dueText(contract.next_credit_on, today) : "완료"} />
        <Stat label="지급 진행률" value={`${percent}%`} />
        <Stat label="잔여 지급 회차" value={`${pending.length}회`} />
      </div>

      {adding && (
        <GrantForm contract={contract} onDone={() => { setAdding(false); onDone(); }} />
      )}

      <div className="form-sec">지급 예정</div>
      {pending.map((grant) => (
        editing?.id === grant.id
          ? <GrantEdit key={grant.id} grant={grant}
                       onCancel={() => setEditing(null)}
                       onSave={(fields) => save(grant.id, fields).then(() => setEditing(null))} />
          : (
            <div key={grant.id} className="list-row">
              <span className="mono">{grant.no}/{grant.total}</span>
              <span className={dueClass(grant.grant_on, today)}>{fmt(grant.grant_on)}</span>
              <span>{num(grant.amount)} 크레딧</span>
              {grant.memo && <span className="muted">{grant.memo}</span>}
              <button className="btn btn-sm" type="button" onClick={() => setEditing(grant)}>수정</button>
              <button className="btn btn-sm btn-primary" type="button" onClick={() => setAsk(grant)}>
                지급 완료
              </button>
            </div>
          )
      ))}
      {!pending.length && <div className="board-empty">지급 예정 회차가 없습니다.</div>}

      <div className="form-sec">지급 완료</div>
      {done.map((grant) => (
        <div key={grant.id} className="list-row">
          <span className="mono">{grant.no}/{grant.total}</span>
          <span>{fmt(grant.grant_on)}</span>
          <span>{num(grant.amount)} 크레딧</span>
          <span className="muted">{grant.granted_by || "—"}</span>
          {grant.memo && <span className="muted">{grant.memo}</span>}
          <button className="btn btn-sm" type="button" onClick={() => setAsk(grant)}>지급 취소</button>
        </div>
      ))}
      {!done.length && <div className="board-empty">아직 지급 내역이 없습니다.</div>}

      {ask && (
        <Confirm
          title={ask.done ? "크레딧 지급을 취소합니다" : "크레딧 지급을 완료로 표시합니다"}
          rows={[
            ["회차", `${ask.no}/${ask.total}`],
            ["지급 날짜", fmt(ask.grant_on)],
            ["크레딧", `${num(ask.amount)} 크레딧`],
            ["누적 지급", `${num(contract.granted_credits)} → ${num(
              contract.granted_credits + (ask.done ? -(ask.amount ?? 0) : (ask.amount ?? 0)))}`],
          ]}
          note={ask.done
            ? "지급자 이름도 함께 지워집니다. 누적 지급 크레딧과 다음 지급일이 바로 갱신됩니다."
            : "누적 지급 크레딧과 다음 지급일이 바로 갱신됩니다."}
          okLabel={ask.done ? "지급 취소" : "지급 완료"}
          danger={ask.done}
          onOk={() => save(ask.id, { done: String(!ask.done) })}
          onClose={() => setAsk(null)}
        />
      )}
    </Section>
  );
}

/** 지급 회차 추가. 전체 회차 수는 서버가 다시 셉니다 — 화면이 세면 두 값이 갈라집니다. */
function GrantForm({ contract, onDone }: { contract: Contract; onDone: () => void }) {
  const [when, setWhen] = useState("");
  const [amount, setAmount] = useState("");
  const [memo, setMemo] = useState("");
  const [add, adding] = useAction(async () => {
    if (!amount.trim()) return;
    await postForm(`/won-customers/contracts/${contract.id}/credits`, {
      grant_on: when, amount, memo,
    });
    onDone();
  });
  return (
    <div className="list-row">
      <input className="inp" type="date" value={when} onChange={(e) => setWhen(e.target.value)} />
      <input className="inp" type="number" placeholder="크레딧" value={amount}
             onChange={(e) => setAmount(e.target.value)} />
      <input className="inp" placeholder="메모 (space_seq별 지급량 등)" value={memo}
             onChange={(e) => setMemo(e.target.value)} />
      <button className="btn btn-sm btn-primary" type="button" disabled={adding}
              onClick={() => add()}>{adding ? "추가 중" : "추가"}</button>
    </div>
  );
}

function GrantEdit({ grant, onSave, onCancel }: {
  grant: Grant;
  onSave: (fields: Record<string, string>) => void;
  onCancel: () => void;
}) {
  const [when, setWhen] = useState(grant.grant_on ?? "");
  const [amount, setAmount] = useState(String(grant.amount ?? ""));
  const [by, setBy] = useState(grant.granted_by ?? "");
  const [memo, setMemo] = useState(grant.memo ?? "");
  return (
    <div className="list-row">
      <input className="inp" type="date" value={when} onChange={(e) => setWhen(e.target.value)} />
      <input className="inp" type="number" value={amount} onChange={(e) => setAmount(e.target.value)} />
      <input className="inp" placeholder="지급자" value={by} onChange={(e) => setBy(e.target.value)} />
      <input className="inp" placeholder="메모" value={memo} onChange={(e) => setMemo(e.target.value)} />
      <button className="btn btn-sm btn-primary" type="button"
              onClick={() => onSave({ grant_on: when, amount, granted_by: by, memo })}>저장</button>
      <button className="btn btn-sm" type="button" onClick={onCancel}>취소</button>
    </div>
  );
}

function PaySection({ contract, today, onDone }: {
  contract: Contract; today: string; onDone: () => void;
}) {
  const paid = contract.payments.filter((p) => p.done);
  const total = n(contract.amount_incl_vat);
  // 수금율은 **항상 계약 통화 기준**입니다. 환율 환산은 대시보드의 예상 MRR 에서만 씁니다.
  const percent = total ? Math.min(100, Math.round((n(contract.collected) / total) * 100)) : 0;

  const [ask, setAsk] = useState<Payment | null>(null);
  const [editing, setEditing] = useState<Payment | null>(null);

  async function save(id: number, fields: Record<string, string>) {
    await postForm(`/won-customers/payments/${id}`, fields);
    onDone();
  }

  return (
    <Section id="sec-pay" title="결제 현황">
      <div className="stat-row">
        <Stat label="수금율 (VAT 포함)" value={`${percent}%`} />
        <Stat label="총 계약 금액 (VAT 포함)" value={money(contract.amount_incl_vat, contract.currency)} />
        <Stat label="수금 완료 금액 (VAT 포함)" value={money(contract.collected, contract.currency)} />
        <Stat label="잔여 금액 (VAT 포함)" value={money(total - n(contract.collected), contract.currency)} />
        <Stat label="다음 결제일" value={contract.next_pay_on ? dueText(contract.next_pay_on, today) : "완료"} />
        <Stat label="분납 완료" value={`${paid.length} / ${contract.payments.length}`} />
      </div>
      <div className="form-sec">결제 히스토리</div>
      <div className="table-wrap">
        <table>
          <thead><tr>
            <th>분납 차수</th><th>입금 날짜</th><th>금액</th><th>적용 환율</th><th>상태</th><th />
          </tr></thead>
          <tbody>
            {contract.payments.map((payment) => (
              editing?.id === payment.id ? (
                <tr key={payment.id}>
                  <td>{payment.no}/{payment.total}</td>
                  <td colSpan={5}>
                    <PayEdit payment={payment}
                             onCancel={() => setEditing(null)}
                             onSave={(fields) => save(payment.id, fields).then(() => setEditing(null))} />
                  </td>
                </tr>
              ) : (
                <tr key={payment.id}>
                  <td>{payment.no}/{payment.total}</td>
                  <td className={dueClass(payment.paid_on, today)}>{fmt(payment.paid_on)}</td>
                  <td className="nowrap">{money(payment.amount, contract.currency)}</td>
                  <td className="nowrap">
                    {payment.fx_rate ? `${num(Number(payment.fx_rate))} (${fmt(payment.fx_on)})` : "—"}
                  </td>
                  <td>
                    <span className={`tag ${payment.done ? "d-mrr" : "neutral"}`}>
                      {payment.done ? "입금 완료" : "입금 전"}
                    </span>
                  </td>
                  <td>
                    <div style={{ display: "flex", gap: 6 }}>
                      <button className="btn btn-sm" type="button" onClick={() => setEditing(payment)}>수정</button>
                      <button className="btn btn-sm" type="button" onClick={() => setAsk(payment)}>
                        {payment.done ? "입금 전으로" : "입금 완료"}
                      </button>
                    </div>
                  </td>
                </tr>
              )
            ))}
          </tbody>
        </table>
      </div>

      {ask && (
        <Confirm
          title={ask.done ? "입금 전으로 되돌립니다" : "입금 완료로 표시합니다"}
          rows={[
            ["분납 차수", `${ask.no}/${ask.total}`],
            ["입금 날짜", fmt(ask.paid_on)],
            ["금액", money(ask.amount, contract.currency)],
            ["수금 완료", `${money(contract.collected, contract.currency)} → ${money(
              n(contract.collected) + (ask.done ? -n(ask.amount) : n(ask.amount)), contract.currency)}`],
          ]}
          note={ask.done
            ? "수금율과 다음 결제일이 바로 갱신됩니다."
            : "수금율과 다음 결제일이 바로 갱신됩니다. 그 날짜의 환율이 함께 저장됩니다 — 나중에 오늘 환율로 다시 환산하지 않기 위해서입니다."}
          okLabel={ask.done ? "입금 전으로" : "입금 완료"}
          danger={ask.done}
          onOk={() => save(ask.id, { done: String(!ask.done) })}
          onClose={() => setAsk(null)}
        />
      )}
    </Section>
  );
}

function PayEdit({ payment, onSave, onCancel }: {
  payment: Payment;
  onSave: (fields: Record<string, string>) => void;
  onCancel: () => void;
}) {
  const [when, setWhen] = useState(payment.paid_on ?? "");
  const [amount, setAmount] = useState(String(payment.amount ?? ""));
  const [rate, setRate] = useState(String(payment.fx_rate ?? ""));
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <input className="inp" type="date" value={when} onChange={(e) => setWhen(e.target.value)} />
      <input className="inp" type="number" value={amount} onChange={(e) => setAmount(e.target.value)} />
      {/* 비워 두면 입금 완료 처리할 때 그 날짜의 환율을 자동으로 채웁니다. */}
      <input className="inp" type="number" step="0.0001" placeholder="적용 환율 (비우면 자동)"
             value={rate} onChange={(e) => setRate(e.target.value)} />
      <button className="btn btn-sm btn-primary" type="button"
              onClick={() => onSave({ paid_on: when, amount, fx_rate: rate })}>저장</button>
      <button className="btn btn-sm" type="button" onClick={onCancel}>취소</button>
    </div>
  );
}

function CareSection({ contract, onDone }: { contract: Contract; onDone: () => void }) {
  const [adding, setAdding] = useState(false);
  const [kind, setKind] = useState("");
  const [when, setWhen] = useState("");
  const [comp, setComp] = useState("");
  const [removing, setRemoving] = useState<Claim | null>(null);
  const [editing, setEditing] = useState<Claim | null>(null);

  const [add, addBusy] = useAction(async () => {
    if (!kind.trim()) return;
    await postForm(`/won-customers/contracts/${contract.id}/claims`, {
      kind, happened_on: when, compensation: comp, progress: "접수",
    });
    setKind(""); setWhen(""); setComp(""); setAdding(false);
    onDone();
  });
  async function save(id: number, fields: Record<string, string>) {
    await postForm(`/won-customers/claims/${id}`, fields);
    onDone();
  }

  return (
    <Section id="sec-care" title="고객 클레임 / 히스토리"
             right={<button className="btn btn-sm" type="button"
                            onClick={() => setAdding(!adding)}>+ 등록</button>}>
      {adding && (
        <div className="list-row">
          <input className="inp" placeholder="클레임/히스토리 종류 (예: 품질 이슈, 신기능 TEST)"
                 value={kind} onChange={(e) => setKind(e.target.value)} />
          <input className="inp" type="date" value={when} onChange={(e) => setWhen(e.target.value)} />
          <input className="inp" placeholder="보상 종류 (예: 크레딧 보상 3,000)"
                 value={comp} onChange={(e) => setComp(e.target.value)} />
          <button className="btn btn-sm btn-primary" type="button" disabled={addBusy}
                  onClick={() => add()}>{addBusy ? "등록 중" : "등록"}</button>
        </div>
      )}
      {contract.claims.map((claim) => (
        editing?.id === claim.id ? (
          <ClaimEdit key={claim.id} claim={claim}
                     onCancel={() => setEditing(null)}
                     onSave={(fields) => save(claim.id, fields).then(() => setEditing(null))} />
        ) : (
          <div key={claim.id} className="list-row">
            <span className="mono">{fmt(claim.happened_on)}</span>
            <span>{claim.kind}</span>
            {claim.compensation && <span className="muted">{claim.compensation}</span>}
            <span className={`tag ${claim.progress === "조치 완료" ? "d-mrr" : "risk"}`}>
              {claim.progress}
            </span>
            {claim.action_on && <span className="muted">조치 {fmt(claim.action_on)}</span>}
            <button className="btn btn-sm" type="button" onClick={() => setEditing(claim)}>수정</button>
            <button className="btn btn-sm btn-ghost" type="button"
                    onClick={() => setRemoving(claim)}>삭제</button>
          </div>
        )
      ))}
      {!contract.claims.length && <div className="board-empty">등록된 클레임·히스토리가 없습니다.</div>}

      <ContractNotes contract={contract} onDone={onDone} />

      {removing && (
        <Confirm
          title="클레임 · 히스토리를 삭제합니다"
          rows={[
            ["종류", removing.kind],
            ["발생 날짜", fmt(removing.happened_on)],
            ["진행상황", removing.progress],
          ]}
          note="되돌릴 수 없습니다. 미처리 건이면 목록 상단의 카운트에서도 빠집니다."
          okLabel="삭제" danger
          onOk={async () => {
            await postForm(`/won-customers/claims/${removing.id}/delete`, {});
            onDone();
          }}
          onClose={() => setRemoving(null)}
        />
      )}
    </Section>
  );
}

function ClaimEdit({ claim, onSave, onCancel }: {
  claim: Claim;
  onSave: (fields: Record<string, string>) => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState(claim.kind);
  const [when, setWhen] = useState(claim.happened_on ?? "");
  const [comp, setComp] = useState(claim.compensation ?? "");
  const [progress, setProgress] = useState(claim.progress);
  const [actionOn, setActionOn] = useState(claim.action_on ?? "");
  return (
    <div className="list-row">
      <input className="inp" value={kind} onChange={(e) => setKind(e.target.value)} />
      <input className="inp" type="date" value={when} onChange={(e) => setWhen(e.target.value)} />
      <input className="inp" placeholder="보상 종류" value={comp} onChange={(e) => setComp(e.target.value)} />
      <select className="inp" value={progress} onChange={(e) => setProgress(e.target.value)}>
        {["접수", "조치 진행 중", "조치 완료"].map((item) => <option key={item}>{item}</option>)}
      </select>
      <input className="inp" type="date" value={actionOn} onChange={(e) => setActionOn(e.target.value)} />
      <button className="btn btn-sm btn-primary" type="button"
              onClick={() => onSave({ kind, happened_on: when, compensation: comp,
                                      progress, action_on: actionOn })}>저장</button>
      <button className="btn btn-sm" type="button" onClick={onCancel}>취소</button>
    </div>
  );
}

/** 갱신 계획 · 사용 중단 이유 · 비고. 다시 고치면 그만인 값이라 확인 창을 붙이지 않습니다.
 *
 * 확인 창이 흔해지면 아무도 안 읽습니다. 붙이는 자리는 파생 수치가 같이 움직이는 값뿐입니다. */
function ContractNotes({ contract, onDone }: { contract: Contract; onDone: () => void }) {
  const [renewal, setRenewal] = useState(contract.renewal_plan ?? "");
  const [stop, setStop] = useState(contract.stop_reason ?? "");
  const [memo, setMemo] = useState(contract.memo ?? "");
  const [save, saving] = useAction(async () => {
    await postForm(`/won-customers/contracts/${contract.id}`, {
      renewal_plan: renewal, stop_reason: stop, memo,
    });
    onDone();
  });
  return (
    <>
      <div className="form-sec">갱신 계획 · 비고</div>
      <div className="form-grid3">
        <div>
          <label className="form-label">갱신 계획</label>
          <select className="inp" value={renewal} onChange={(e) => setRenewal(e.target.value)}>
            {["", "갱신 예정", "협의 중", "미정", "본계약 검토 중", "갱신 안함", "갱신 완료"]
              .map((item) => <option key={item} value={item}>{item || "—"}</option>)}
          </select>
        </div>
        <div>
          <label className="form-label">사용 중단 이유</label>
          <input className="inp" value={stop} onChange={(e) => setStop(e.target.value)} />
        </div>
        <div>
          <label className="form-label">비고</label>
          <input className="inp" value={memo} onChange={(e) => setMemo(e.target.value)} />
        </div>
      </div>
      <div className="modal-foot" style={{ marginTop: 12 }}>
        <button className="btn btn-sm btn-primary" type="button" disabled={saving}
                onClick={() => save()}>{saving ? "저장 중" : "저장"}</button>
      </div>
    </>
  );
}

/** 소통 히스토리 등록 — **고객 단위**입니다. 계약 차수를 비우면 협상 단계(계약 전) 기록이고,
 *  그래서 계약이 하나도 없는 고객에게도 쓸 수 있습니다. */
export function CommForm({ contactId, contracts, onDone }: {
  contactId: number; contracts: Contract[]; onDone: () => void;
}) {
  const [channel, setChannel] = useState("email");
  const [handler, setHandler] = useState("");
  const [when, setWhen] = useState("");
  const [seq, setSeq] = useState("");
  const [summary, setSummary] = useState("");
  const [add, adding] = useAction(async () => {
    if (!summary.trim()) return;
    await postForm(`/customers/${contactId}/interactions`, {
      channel, handler, happened_at: when, contract_seq: seq, summary,
    });
    setSummary(""); setHandler(""); setWhen(""); setSeq("");
    onDone();
  });
  return (
    <div className="form-grid3" style={{ marginBottom: 14 }}>
      <div>
        <label className="form-label">소통 플랫폼</label>
        <select className="inp" value={channel} onChange={(e) => setChannel(e.target.value)}>
          {[["email", "메일"], ["phone", "전화"], ["whatsapp", "WhatsApp"],
            ["meeting", "미팅"], ["sms", "문자"], ["manual", "기타"]].map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
      </div>
      <div>
        <label className="form-label">담당자</label>
        <input className="inp" value={handler} onChange={(e) => setHandler(e.target.value)} />
      </div>
      <div>
        <label className="form-label">날짜</label>
        <input className="inp" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
      </div>
      <div>
        <label className="form-label">관련 계약</label>
        <select className="inp" value={seq} onChange={(e) => setSeq(e.target.value)}>
          <option value="">협상 단계 (계약 전)</option>
          {contracts.map((c) => <option key={c.seq} value={c.seq}>{c.label}</option>)}
        </select>
      </div>
      <div style={{ gridColumn: "span 2" }}>
        <label className="form-label">소통 내용 및 메모</label>
        <input className="inp" value={summary} onChange={(e) => setSummary(e.target.value)}
               placeholder="오간 내용을 한 번에 정리해 적어주세요." />
      </div>
      <div className="modal-foot" style={{ gridColumn: "span 3" }}>
        <button className="btn btn-sm btn-primary" type="button" disabled={adding}
                onClick={() => add()}>{adding ? "등록 중" : "등록"}</button>
      </div>
    </div>
  );
}
