import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { getJSON, postForm } from "../../lib/api";
import { ActionButton } from "../../ui/ActionButton";
import {
  type Contract, type Row,
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
  const [pickedSeq, setPickedSeq] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [commFilter, setCommFilter] = useState<"all" | "nego" | number>("all");

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
            <div className="dh-sub">ID {data.client_id} · {data.customer_type} · {data.owner || "담당 미지정"}</div>
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
            <button key={id} type="button"
                    onClick={() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" })}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="detail-body">
        <Section id="sec-basic" title="고객 기본 정보">
          <div className="field-grid c3">
            <KV k="고객사" v={data.company} />
            <KV k="산업 분야" v={data.industry} />
            <KV k="국가" v={data.country} />
            <KV k="담당부서" v={data.department} />
            <KV k="Client ID" v={String(data.client_id)} />
            <KV k="고객 종류" v={data.customer_type} />
            <KV k="고객 담당자" v={data.contact_name} />
            <KV k="고객 연락처" v={data.contact_info} />
            <KV k="최초 수주일" v={fmt(data.first_won_on)} />
            <KV k="플랜 상태" v={data.plan_status} />
            <KV k="담당" v={data.owner} />
            <KV k="연동 티켓 (계약별)"
                v={contracts.filter((c) => c.ticket_id).map((c) => `${c.seq}차 ${c.ticket_id}`).join(" · ") || "연동 없음"} />
          </div>
        </Section>

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

function Section({ id, title, right, children }: {
  id: string; title: string; right?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <section className="sec" id={id}>
      <div className="sec-head">
        <h2 className="sec-title">{title}</h2>
        <div className="sec-actions">{right}</div>
      </div>
      {children}
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

  async function toggle(id: number, next: boolean) {
    await postForm(`/won-customers/credits/${id}`, { done: String(next) });
    onDone();
  }

  return (
    <Section id="sec-credit" title="크레딧 지급 현황">
      <div className="stat-row">
        <Stat label="계약 크레딧" value={num(contract.credits)} />
        <Stat label="누적 지급 크레딧" value={num(contract.granted_credits)} />
        <Stat label="다음 지급일" value={contract.next_credit_on ? dueText(contract.next_credit_on, today) : "완료"} />
        <Stat label="지급 진행률" value={`${percent}%`} />
        <Stat label="잔여 지급 회차" value={`${pending.length}회`} />
      </div>
      <div className="form-sec">지급 예정</div>
      {pending.map((grant) => (
        <div key={grant.id} className="form-row">
          <span className="no">{grant.no}/{grant.total}</span>
          <span className={`when ${dueClass(grant.grant_on, today)}`}>{fmt(grant.grant_on)}</span>
          <span className="amt">{num(grant.amount)} 크레딧</span>
          {grant.memo && <span className="muted">{grant.memo}</span>}
          <ActionButton className="btn btn-sm btn-primary" pending="처리 중"
                        onClick={() => toggle(grant.id, true)}>지급 완료</ActionButton>
        </div>
      ))}
      {!pending.length && <div className="board-empty">지급 예정 회차가 없습니다.</div>}
      <div className="form-sec">지급 완료</div>
      {done.map((grant) => (
        <div key={grant.id} className="form-row">
          <span className="no">{grant.no}/{grant.total}</span>
          <span className="when">{fmt(grant.grant_on)}</span>
          <span className="amt">{num(grant.amount)} 크레딧</span>
          <span className="muted">{grant.granted_by || "—"}</span>
          {grant.memo && <span className="muted">{grant.memo}</span>}
          <ActionButton className="btn btn-sm" pending="처리 중"
                        onClick={() => toggle(grant.id, false)}>지급 취소</ActionButton>
        </div>
      ))}
      {!done.length && <div className="board-empty">아직 지급 내역이 없습니다.</div>}
    </Section>
  );
}

function PaySection({ contract, today, onDone }: {
  contract: Contract; today: string; onDone: () => void;
}) {
  const paid = contract.payments.filter((p) => p.done);
  const total = n(contract.amount_incl_vat);
  // 수금율은 **항상 계약 통화 기준**입니다. 환율 환산은 대시보드의 예상 MRR 에서만 씁니다.
  const percent = total ? Math.min(100, Math.round((n(contract.collected) / total) * 100)) : 0;

  async function toggle(id: number, next: boolean) {
    await postForm(`/won-customers/payments/${id}`, { done: String(next) });
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
                  <ActionButton className="btn btn-sm" pending="처리 중"
                                onClick={() => toggle(payment.id, !payment.done)}>
                    {payment.done ? "입금 전으로" : "입금 완료"}
                  </ActionButton>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function CareSection({ contract, onDone }: { contract: Contract; onDone: () => void }) {
  const [adding, setAdding] = useState(false);
  const [kind, setKind] = useState("");
  const [when, setWhen] = useState("");

  async function add() {
    if (!kind.trim()) return;
    await postForm(`/won-customers/contracts/${contract.id}/claims`, {
      kind, happened_on: when, progress: "접수",
    });
    setKind(""); setWhen(""); setAdding(false);
    onDone();
  }
  async function setProgress(id: number, progress: string) {
    await postForm(`/won-customers/claims/${id}`, { progress });
    onDone();
  }
  async function remove(id: number) {
    await postForm(`/won-customers/claims/${id}/delete`, {});
    onDone();
  }

  return (
    <Section id="sec-care" title="고객 클레임 / 히스토리"
             right={<button className="btn btn-sm" type="button" onClick={() => setAdding(!adding)}>+ 등록</button>}>
      {adding && (
        <div className="form-row">
          <input className="select" placeholder="클레임/히스토리 종류"
                 value={kind} onChange={(e) => setKind(e.target.value)} />
          <input className="select" type="date" value={when} onChange={(e) => setWhen(e.target.value)} />
          <ActionButton className="btn btn-sm btn-primary" pending="등록 중" onClick={add}>등록</ActionButton>
        </div>
      )}
      {contract.claims.map((claim) => (
        <div key={claim.id} className="form-row">
          <span className="when">{fmt(claim.happened_on)}</span>
          <span className="amt">{claim.kind}</span>
          {claim.compensation && <span className="muted">{claim.compensation}</span>}
          <span className={`tag ${claim.progress === "조치 완료" ? "d-mrr" : "risk"}`}>{claim.progress}</span>
          {claim.progress !== "조치 완료" && (
            <ActionButton className="btn btn-sm" pending="처리 중"
                          onClick={() => setProgress(claim.id, "조치 완료")}>조치 완료</ActionButton>
          )}
          <ActionButton className="btn btn-sm btn-ghost" pending="삭제 중"
                        onClick={() => remove(claim.id)}>삭제</ActionButton>
        </div>
      ))}
      {!contract.claims.length && <div className="board-empty">등록된 클레임·히스토리가 없습니다.</div>}
      <div className="field-grid" style={{ marginTop: 14 }}>
        <KV k="갱신 계획" v={contract.renewal_plan} />
        <KV k="사용 중단 이유" v={contract.stop_reason} />
        <KV k="비고" v={contract.memo} />
      </div>
    </Section>
  );
}
