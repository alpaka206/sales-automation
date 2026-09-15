import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { getJSON, postForm } from "../../lib/api";
import { useAgent, useSpaceMetric } from "../../lib/agent";
import { SubmitButton, useAction } from "../../ui/ActionButton";
// **타입 목록은 한 곳에서 옵니다** (2026-09-03 운영자 지시). 이 화면은 모달이 아니고
// 「관련 계약」 칸이 따로 있어 폼 자체는 합치지 않지만, 고르개 목록까지 따로 들고 있으면
// 같은 값을 두 화면이 다르게 부릅니다 — 여기는 「메일」·「왓츠앱」·「기타」였고 저쪽은
// 「이메일」·「WhatsApp」·「메모」였습니다.
import { CHANNELS, InteractionForm } from "../../ui/InteractionForm";
import { Modal } from "../../ui/Modal";
import { Confirm } from "./Confirm";
import { AlertTags } from "./UsageBits";
import { useAutoReconcile } from "./reconcile";
import { useEvidence, usageFor, useUsageIndex } from "./useUsage";
import { matchGrants, matchPayments, mergeEvidence, parseSpaceSeqs, type GrantEvidence, type PaymentEvidence } from "./usage";
import { WonContractForm } from "./WonContractForm";
import { ContractFields, PlanFields, scheduleLabel, useContractDraft } from "./ContractFields";
import { validate } from "./contractDraft";
import { CreditUsageSection, JobsSection, MixSection, UsageInsight, type CreditsData } from "./WonUsageSections";
import {
  RETIRED,
  type Comm, type Contract, type Grant, type History, type ListData, type Options, type Payment, type Row,
  addMonths, dday, dueClass, fmt, initials, money, n, num, planTone, statusTone,
} from "./shared";

/** 수주 고객 상세 — 목업(`수주관리목업_0806.html` 의 `detailHTML`)의 섹션 그대로. 목업의
 * 8개 중 「갱신 · 비고」가 빠져 일곱 개였고(이관 0073), 2026-09-15 에 사용 현황 셋이 붙어
 * 열 개입니다(`수주고객-사용현황-목업_26.html`). 그 셋은 **이 PC 의 데이터 에이전트**가
 * 답합니다 — 스냅샷 원본도 집계도 서버를 안 지납니다. 같은 날 상단 nav 가 앵커에서
 * **탭**이 됐습니다 — 고른 섹션 하나만 그립니다.
 *
 * **계약 선택 드롭다운이 이 화면의 축입니다.** 고객은 하나이고 계약이 여럿이라, 2~6번
 * 섹션은 전부 "지금 고른 계약" 의 내용이고 고르는 순간 함께 바뀝니다.
 *
 * 계약을 따라가지 **않는** 것은 **7번 소통 히스토리** 하나입니다 — 협상 단계 대화가 계약보다
 * 먼저 쌓이고 그대로 이어집니다(0065).
 */
const SECTIONS: [string, string][] = [
  ["sec-basic", "고객 정보"],
  // 계약과 플랜은 한 탭입니다 — 목업의 「계약 · 플랜」(2026-09-15 운영자 지시). 카드는 둘 그대로.
  ["sec-contract", "계약 · 플랜"],
  // 결제와 MRR 도 한 탭, 셋째 자리 — 목업의 「결제 · MRR」(2026-09-15 운영자 지시).
  ["sec-pay", "결제 · MRR"],
  // 지급과 사용 현황은 한 탭입니다 — 목업의 「크레딧」(2026-09-15 운영자 지시). 순서도 목업대로:
  // 사용 진단 줄 → 지급 현황 카드 → 사용 현황 카드. 지급과 소진은 한 화면에서 맞대 봐야 합니다.
  // 사용 쪽은 **이 PC 의 데이터 에이전트**가 답합니다(스냅샷 집계). 서버는 이 값을 모릅니다.
  ["sec-credit", "크레딧"],
  ["sec-jobs", "작업 성능"],
  ["sec-mix", "사용 구성"],
];

const AVATAR_COLORS = ["#0F766E", "#B45309", "#3730A3", "#B42318", "#026AA2", "#4B5563"];

/** 목업의 `statusTag` / `dealTag` / `planTag`. */
const Tag = ({ tone, children }: { tone: string; children: React.ReactNode }) =>
  <span className={`tag ${tone}`}>{children}</span>;


export function WonCustomerDetail() {
  const { clientId } = useParams();
  const navigate = useNavigate();
  // 계약 폼은 이 화면 위의 모달입니다. 주소로 판단하므로 새로고침해도 열려 있고,
  // 뒤로가기가 곧 닫기입니다 — 모달을 상태로만 들면 둘 다 안 됩니다.
  const contractRoute = useLocation().pathname.includes("/contracts");
  // 「계약 · 플랜」 탭의 제자리 편집. 버튼(편집 · 취소 · 저장)은 탭바 오른쪽에 서고 폼은 탭
  // 본문에 있어서, 상태는 둘을 다 보는 여기가 듭니다. 저장 버튼은 `form=` 으로 그 폼을
  // 가리킵니다 — 모달 푸터의 제출 버튼과 같은 방식입니다.
  const [editingContract, setEditingContract] = useState(false);
  const [savingContract, setSavingContract] = useState(false);
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
  const [removing, setRemoving] = useState(false);
  const [retiring, setRetiring] = useState(false);

  // 사용 현황은 이 PC 의 에이전트에서 옵니다. 지금 고른 계약의 Space ID 만 묻습니다.
  const agent = useAgent();

  // **상단 nav 는 탭입니다** (2026-09-15 운영자 지시 — 「이동이 아니라 그 요소만 보이도록」).
  // 한동안 열 섹션이 한 화면에 이어져 있었고 nav 는 거기로 내려가는 앵커였습니다. 이제 고른
  // 섹션 하나만 그립니다. 액션 보드가 보내는 `/won-customers/2102#sec-credit` 은 그 탭을
  // 엽니다 — 해시가 곧 탭 이름이라 주소를 나눠 줘도 같은 탭이 열립니다.
  const hash = useLocation().hash.slice(1);
  const [picked, setPicked] = useState<string | null>(null);
  // 예전 앵커 이름은 지금 탭으로 옮긴다 — `#sec-plan` 은 계약 탭 안에 있다.
  const alias: Record<string, string> = {
    "sec-plan": "sec-contract", "sec-comm": "sec-basic", "sec-usage": "sec-credit", "sec-revenue": "sec-pay",
  };
  const known = (id: string) => SECTIONS.some(([key]) => key === id);
  const wanted = alias[hash] ?? hash;
  const section = picked ?? (known(wanted) ? wanted : SECTIONS[0][0]);
  const select = (id: string) => {
    setPicked(id);
    // 긴 탭을 내려 보다 다른 탭을 누르면 그 탭의 중간에 서게 됩니다 — 위로 올립니다.
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  // 이 화면에 있는 채로 해시만 바뀌면(보드에서 또 누름) 그 탭으로.
  useEffect(() => { if (known(wanted)) setPicked(wanted); }, [wanted]);
  // 탭을 떠나면 편집도 끝납니다 — 폼이 내려가 초안이 사라지는데 버튼만 「저장」으로 남으면 안 됩니다.
  useEffect(() => setEditingContract(false), [section]);

  const refresh = () => queryClient.invalidateQueries();

  // 훅은 early return 위에 있어야 합니다. 고른 계약이 아직 없으면 빈 목록이라 안 부릅니다.
  const currentForUsage = (data?.contracts ?? []).find((c) => c.seq === pickedSeq)
    ?? data?.active ?? (data?.contracts ?? [])[(data?.contracts ?? []).length - 1] ?? null;
  const usageIndex = useUsageIndex(agent.pair, parseSpaceSeqs(currentForUsage?.space_seq));
  // 크레딧 소진은 한 번만 받아 4번(지급 회차마다 소진이 시작됐나)과 5번이 같이 씁니다.
  const creditSpaces = usageIndex.index
    ? parseSpaceSeqs(currentForUsage?.space_seq).filter((s) => usageIndex.index!.bySpace.get(s)?.known)
    : [];
  const credits = useSpaceMetric<CreditsData>(agent.pair, "credits", creditSpaces);
  // 이 고객의 **모든** 계약을 맞대어 자동 적용합니다(목록은 활성 계약만 봅니다).
  const allContractSpaces = useMemo(
    () => [...new Set((data?.contracts ?? []).flatMap((c) => parseSpaceSeqs(c.space_seq)))],
    [data?.contracts]);
  const evidence = useEvidence(agent.pair, allContractSpaces);
  useAutoReconcile(data?.contracts ?? [], evidence.index);

  if (!data) return <div className="won"><div className="page">불러오는 중…</div></div>;

  const today = new Date().toISOString().slice(0, 10);
  const contracts = data.contracts ?? [];
  const current =
    contracts.find((c) => c.seq === pickedSeq) ?? data.active ?? contracts[contracts.length - 1] ?? null;
  const comms = data.comms ?? [];
  const usage = usageFor(current, usageIndex.index);
  const alerts = usage.kind === "ok" ? usage.diagnosis.alerts : [];
  // 지급 회차 ↔ 스냅샷 소진 묶음. **표시만** — 우리 기록에는 쓰지 않습니다.
  const grantEvidence = current && credits.data && usageIndex.index
    ? matchGrants(current.credit_grants, credits.data.data.buckets ?? [], usageIndex.index.snapshotAt)
    : null;
  const payEvidence = (() => {
    if (!current || !evidence.index) return null;
    const rows = parseSpaceSeqs(current.space_seq).map((s) => evidence.index!.bySpace.get(s)).filter((x) => !!x);
    if (!rows.length) return null;
    return matchPayments(current.payments, current.currency, mergeEvidence(rows).payments, evidence.index.snapshotAt);
  })();

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
          <Tag tone={statusTone(data.plan_status)}>{data.plan_status}</Tag>
          {current && <Tag tone={current.deal_type === "MRR" ? "d-mrr" : "d-poc"}>{current.deal_type}</Tag>}
          {current?.plan && <Tag tone={`plan-${planTone(current.plan)}`}>{current.plan}</Tag>}
          <Tag tone="neutral">{current ? current.label : "계약 없음"}</Tag>
          {data.setup_count > 0 && <Tag tone="st-setup">세팅중 계약 {data.setup_count}건</Tag>}
          {/* 주의 배지 전부 — 하나도 없으면 안 뜹니다(요청 문서). 근거 펼침은 없습니다 — 규칙은 usage.ts 의 RULE. */}
          {usage.kind === "ok" && <AlertTags alerts={alerts} />}
        </div>
        <div className="secnav" role="tablist">
          {SECTIONS.map(([id, label]) => (
            <button key={id} type="button" role="tab" className={section === id ? "is-on" : undefined}
                    aria-selected={section === id}
                    onClick={() => select(id)}>
              {label}
            </button>
          ))}
          {/* **계약 고르개는 탭바 오른쪽입니다** (목업의 `ct-sel`, 2026-09-15 운영자 지시). 계약을
              고르면 계약·플랜부터 사용 구성까지 탭 전부가 그 계약의 값으로 바뀌므로, 어느 한 탭
              안이 아니라 탭들 옆에 서야 합니다. */}
          {current && (
            <div className="ct-sel">
              {/* 편집 중에는 계약을 못 바꿉니다 — 바꾸면 치던 초안이 다른 계약 위에 섭니다. */}
              <select className="sel-pill" value={current.seq} disabled={editingContract}
                      onChange={(event) => setPickedSeq(Number(event.target.value))}>
                {contracts.slice().reverse().map((c) => (
                  <option key={c.seq} value={c.seq}>
                    {c.label} · {fmt(c.starts_on)} ~ {fmt(c.ends_on)} · {c.state}
                  </option>
                ))}
              </select>
              {/* **편집 하나가 두 카드를 같이 입력칸으로 바꿉니다** (2026-09-15 운영자 확인).
                  「계약 및 결제 정보」와 「Perso 계정 및 플랜」은 한 계약 행이고 칸들이 서로
                  얽혀 있어서(VAT → 통화 → 금액 → 공급가, 크레딧 ↔ 단가, 플랜 기간 → MRR),
                  카드마다 따로 저장하면 반쪽짜리 계약이 남습니다. 저장·취소도 한 번입니다. */}
              {section === "sec-contract" && (editingContract ? (
                <>
                  <button className="btn btn-sm" type="button" disabled={savingContract}
                          onClick={() => setEditingContract(false)}>취소</button>
                  <SubmitButton form={CONTRACT_FORM_ID} busy={savingContract}
                                className="btn btn-sm btn-primary">저장</SubmitButton>
                </>
              ) : (
                <button className="btn btn-sm" type="button" onClick={() => setEditingContract(true)}>편집</button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="detail-body">
        {section === "sec-basic" && (
          <>
            <BasicSection client={data} contracts={contracts} options={list?.options} onDone={refresh} />
            <HistoryCard client={data} contracts={contracts} comms={comms} history={data.history} onDone={refresh} />
          </>
        )}

        {/* 계약이 있어야 그려지는 탭들. 계약이 없으면 어느 탭을 눌러도 같은 안내입니다. */}
        {!current && section !== "sec-basic" ? (
          <section className="sec" id="sec-contract">
            <div className="sec-head">
              <span className="sec-title">계약 및 결제 정보</span>
            </div>
            <div className="empty">
              <strong>등록된 계약이 없습니다</strong>
              계약 · 결제 · 플랜 · 크레딧 정보는 지금 추가할 수 있습니다.
              <div style={{ marginTop: 14, display: "flex", gap: 8, justifyContent: "center" }}>
                <button className="btn btn-primary" type="button"
                        onClick={() => navigate(`/won-customers/${data.client_id}/contracts/new`)}>
                  계약 정보 입력
                </button>
                {/* 둘 다 「계약 0건」일 때만 그려집니다. 하는 일이 다릅니다:
                    내리기는 **번호를 남기고** 목록에서만 뺍니다(Won 에 잘못 올라갔던 건),
                    삭제는 번호까지 걷어냅니다(같은 회사에 중복으로 발급된 번호). */}
                <button className="btn" type="button" onClick={() => setRetiring(true)}>
                  {data.plan_status === RETIRED ? "장부에 다시 올리기" : "장부에서 내리기"}
                </button>
                <button className="btn btn--danger" type="button" onClick={() => setRemoving(true)}>
                  이 고객 삭제
                </button>
              </div>
            </div>
          </section>
        ) : current ? (
          <>
            {section === "sec-contract" && (
              editingContract ? (
                <ContractEditor key={current.id} contract={current} options={list?.options}
                                onBusy={setSavingContract}
                                onDone={() => { setEditingContract(false); refresh(); }} />
              ) : (
                <>
                  <ContractSection current={current} today={today} />
                  <PlanSection contract={current} />
                </>
              )
            )}
            {section === "sec-credit" && (
              <>
                {/* 목업의 `dg-bar` — 사용 수준 · 마지막 작업 · 주의. 카드 밖, 탭 맨 위. */}
                {usage.kind === "ok" && <UsageInsight d={usage.diagnosis} />}
                <CreditSection contract={current} today={today} onDone={refresh} evidence={grantEvidence} />
                <CreditUsageSection contract={current} usage={usage} credits={credits}
                                    snapshotAt={usageIndex.index?.snapshotAt}
                                    creditsFrom={usageIndex.index?.creditsFrom} />
              </>
            )}
            {section === "sec-pay" && (
              <>
                <PaySection contract={current} today={today} onDone={refresh} evidence={payEvidence} />
                <RevenueSection contract={current} today={today} />
              </>
            )}
            {section === "sec-jobs" && (
              <JobsSection pair={agent.pair} contract={current} usage={usage}
                           failRateAll={usageIndex.index?.failRateAll ?? null} />
            )}
            {section === "sec-mix" && <MixSection pair={agent.pair} usage={usage} />}
          </>
        ) : null}

      </div>

      {contractRoute && <WonContractForm />}

      {retiring && (
        <Confirm
          title={data.plan_status === RETIRED
            ? "이 고객을 장부에 다시 올립니다" : "이 고객을 장부에서 내립니다"}
          rows={[["고객사", data.company], ["Client ID", String(data.client_id)], ["계약", "0건"]]}
          note={
            data.plan_status === RETIRED
              ? "수주 고객 목록과 활성 고객 수에 다시 들어갑니다."
              : "Client ID·고객 행·문의 연결은 그대로 두고 목록에서만 내립니다 — 그 번호를 "
                + "워크북의 계약·회차 탭과 Inbound DB 가 조회해 회사명을 가져오기 때문입니다. "
                + "계약을 넣으면 저절로 다시 올라옵니다."
          }
          okLabel={data.plan_status === RETIRED ? "다시 올리기" : "내리기"}
          // 끄는 것은 "0" 입니다 — 빈 문자열은 중간에서 사라지면 「해제」가 조용히
          // 「내리기」가 되기 때문입니다.
          onOk={() => postForm(`/won-customers/${data.client_id}/retire`, {
            retire: data.plan_status === RETIRED ? "0" : "1",
          }).then(() => queryClient.invalidateQueries())}
          onClose={() => setRetiring(false)}
        />
      )}

      {removing && (
        <Confirm
          title="이 고객을 지웁니다"
          rows={[["고객사", data.company], ["Client ID", String(data.client_id)], ["계약", "0건"]]}
          note={
            "이 번호를 들고 있던 문의·연락처·수주 전환 대기의 Client ID 도 함께 비웁니다 — " +
            "없는 번호가 남아 있으면 다음 Won 때 그 번호가 도로 찾아져 고객이 살아 돌아옵니다. " +
            "워크북 「고객 기본 정보」의 그 행은 시트가 원본이라 손으로 지웁니다."
          }
          okLabel="삭제"
          danger
          onOk={() => postForm(`/won-customers/${data.client_id}/delete`, {})
            .then(() => queryClient.invalidateQueries())
            .then(() => navigate("/won-customers", { replace: true }))}
          onClose={() => setRemoving(false)}
        />
      )}
    </div>
  );
}

/** 이전 히스토리 — **티켓 화면의 「이전 히스토리」와 같은 모양** (2026-09-15 운영자 지시).
 *
 *  계약 전의 이야기는 티켓마다 한 상자(제목 · 단계 · 요약 한 문단)이고 누르면 그 티켓으로
 *  갑니다. 지워진 티켓은 제목으로 묶여 같은 모양으로 서고, 티켓이 없던 기록은 「티켓 외
 *  n건」으로 셉니다 — 전부 `MessageDetail` 의 카드와 같은 값·같은 규칙입니다. 머리 오른쪽
 *  「전체보기」는 이 고객의 리드 히스토리로 갑니다.
 *
 *  **계약 단위 묶음** (1차·2차 …)이 위입니다 — 지금 진행 중인 이야기가 먼저. 계약이 생긴 뒤의
 *  소통은 여기 적고, 묶음마다
 *  「+ 소통 등록」이 그 계약을 고른 채로 폼을 엽니다. 빈 묶음도 그립니다 — 2차 계약에 아직
 *  기록이 없다는 것도 정보이고, 적을 자리가 있어야 합니다. */
function HistoryCard({ client, contracts, comms, history, onDone }: {
  client: Row; contracts: Contract[]; comms: Comm[]; history: History | undefined; onDone: () => void;
}) {
  const [adding, setAdding] = useState<number | null>(null);
  // 「+ 추가하기」 — 리드 히스토리 화면의 **그 모달 그대로** (2026-09-15 운영자 지시:
  // 「이전에 만든 모달 그대로 가져다가」). 폼이 한 벌이라 칸이 늘어도 두 화면이 같이 는다.
  const [logging, setLogging] = useState(false);
  const tickets = history?.tickets ?? [];
  const past = history?.past_tickets ?? [];
  const loose = history?.loose ?? [];
  const nothing = !tickets.length && !past.length && !loose.length;
  return (
    <section className="sec" id="sec-comm">
      <div className="sec-head">
        <span className="sec-title">이전 히스토리</span>
        <div className="sec-actions">
          {/* **전체보기는 언제나 뜹니다** (운영자 지시: 「있든 없든」). 연락처가 없는 고객은
              리드 히스토리 **목록**으로 — 갈 상세가 없어서입니다. */}
          <button className="btn btn-sm btn-primary" type="button" disabled={!client.contact_id}
                  title={client.contact_id ? undefined : "연락처가 없는 고객은 기록을 적을 수 없습니다"}
                  onClick={() => setLogging(true)}>+ 추가하기</button>
          <Link className="btn btn-sm" to={client.contact_id ? `/customers/${client.contact_id}` : "/customers"}>전체보기</Link>
        </div>
      </div>
      {logging && client.contact_id && (
        <Modal title="히스토리 추가" hideCancel wide onClose={() => setLogging(false)}>
          <div style={{ marginTop: 16 }}>
            <InteractionForm contactId={client.contact_id}
                             onCancel={() => setLogging(false)}
                             onSaved={() => { setLogging(false); onDone(); }} />
          </div>
        </Modal>
      )}
      {contracts.slice().reverse().map((c) => {
        const rows = comms.filter((x) => x.contract_seq === c.seq);
        return (
          <div className="panel" key={c.seq}>
            <div className="sub-head">
              <span className="sub-title">{c.seq}차 계약</span>
              <span className="sub-count">{fmt(c.starts_on)} – {fmt(c.ends_on)} · {rows.length}건</span>
              {client.contact_id && (
                <button className="btn btn-sm" type="button" style={{ marginLeft: "auto" }}
                        onClick={() => setAdding(adding === c.seq ? null : c.seq)}>
                  {adding === c.seq ? "닫기" : "+ 소통 등록"}
                </button>
              )}
            </div>
            {adding === c.seq && client.contact_id && (
              <CommForm contactId={client.contact_id} contracts={contracts} defaultSeq={c.seq}
                        onCancel={() => setAdding(null)}
                        onDone={() => { setAdding(null); onDone(); }} />
            )}
            {rows.length ? (
              <div className="timeline">
                {rows.map((item, index) => (
                  <div key={item.id} className={`tl-item${index === 0 ? " mark" : ""}`}>
                    <div className="tl-meta">
                      <Tag tone="blue">{item.channel}</Tag>
                      {fmt(item.happened_at?.slice(0, 10))}
                      <span>·</span>
                      {item.handler || "—"}
                    </div>
                    <div className="tl-text">{item.subject ? `${item.subject} — ` : ""}{item.summary}</div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="board-empty">아직 기록이 없습니다.</div>
            )}
          </div>
        );
      })}
      <div className="panel">
        {nothing ? (
          /* 티켓 화면과 같은 빈 상태 — **눈에 띄어야 합니다** (2026-09-04 · 09-15 운영자 지시).
             「없다」는 판단에 쓰는 사실이라 흐린 작은 글씨로 적으면 「아직 안 불러왔다」로 읽힙니다. */
          <div className="empty" style={{ padding: "40px 20px" }}>
            <div className="empty__text empty__text--lead">이전 히스토리가 존재하지 않습니다.</div>
          </div>
        ) : (
          <div className="stack" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {tickets.map((t) => (
              <Link key={t.conversation_id} className="link--plain history-box" to={`/tickets/${t.conversation_id}`}>
                <div className="row-between" style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <strong className="t-sm">{t.subject || "제목 없는 문의"}</strong>
                  <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <span className="tag neutral">{history?.stage_labels[t.stage] ?? t.stage}</span>
                    <span className="muted">›</span>
                  </span>
                </div>
                <div className="muted" style={{ fontSize: 12 }}>
                  {fmt(t.created_at?.slice(0, 10))}{t.ticket_id ? ` · #${t.ticket_id}` : ""}
                </div>
                {t.summary && <div style={{ marginTop: 4, fontSize: 13, whiteSpace: "pre-line" }}>{t.summary}</div>}
              </Link>
            ))}
            {past.map((t) => (
              <Link key={t.subject} className="link--plain history-box" to={`/customers/${client.contact_id}`}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <strong className="t-sm">{t.subject}</strong><span className="muted">›</span>
                </div>
                <div className="muted" style={{ fontSize: 12 }}>
                  {t.last_at ? fmt(t.last_at.slice(0, 10)) : ""} · {t.count}건 · 지난 티켓
                </div>
                {t.summary && <div style={{ marginTop: 4, fontSize: 13, whiteSpace: "pre-line" }}>{t.summary}</div>}
              </Link>
            ))}
            {loose.length > 0 && (
              <div>
                <div className="sub-head" style={{ marginTop: 6 }}>
                  <span className="sub-title">티켓 외 기록</span><span className="sub-count">{loose.length}건</span>
                </div>
                <div className="timeline">
                  {loose.map((item, index) => (
                    <div key={item.id} className={`tl-item${index === 0 ? " mark" : ""}`}>
                      <div className="tl-meta">
                        <Tag tone="blue">{item.channel}</Tag>
                        {fmt(item.happened_at?.slice(0, 10))}
                        <span>·</span>
                        {item.handler || "—"}
                      </div>
                      <div className="tl-text">{item.subject ? `${item.subject} — ` : ""}{item.summary}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

    </section>
  );
}

function Section({ id, title, right, children, plain }: {
  id: string; title: string;
  right?: React.ReactNode; children: React.ReactNode;
  /** 내용이 스스로 패널을 여러 개 그리는 섹션(크레딧·결제). */
  plain?: boolean;
}) {
  return (
    <section className="sec" id={id}>
      <div className="sec-head">
        <span className="sec-title">{title}</span>
        {right && <div className="sec-actions">{right}</div>}
      </div>
      {plain ? children : <div className="panel">{children}</div>}
    </section>
  );
}

function KV({ k, v, span }: { k: string; v: React.ReactNode; span?: number }) {
  return (
    <div style={span ? { gridColumn: `span ${span}` } : undefined}>
      <div className="field-label">{k}</div>
      <div className="field-value">{v || "—"}</div>
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
    first_won_on: client.first_won_on ?? "",
    owner: client.owner ?? "",
  });
  const set = (key: keyof typeof form, value: string) =>
    setForm((current) => ({ ...current, [key]: value }));
  const [confirming, setConfirming] = useState(false);

  const [save, saving] = useAction(async () => {
    await postForm(`/won-customers/${client.client_id}`, form);
    setEditing(false);
    setConfirming(false);
    onDone();
  });

  // 저장 전에 한 번 더 묻습니다 — **바뀐 칸만** 보여 주면서. 고객사 이름은 워크북의
  // 계약·회차 탭과 Inbound DB 가 Client ID 로 조회해 가는 값이고, 담당부서는
  // 요약 카드와 예상 MRR 이 GTM 만 더할 때 쓰는 값입니다 — 한 글자 잘못 고치면 이 화면
  // 밖의 숫자가 조용히 달라집니다. 바뀐 것이 없으면 물을 것도 없어 바로 닫습니다.
  const LABELS: Record<keyof typeof form, string> = {
    company: "고객사", industry: "산업 분야", country: "국가", department: "담당부서",
    first_won_on: "최초 수주일", owner: "담당",
  };
  const changed = (Object.keys(form) as (keyof typeof form)[])
    .filter((key) => form[key] !== ((client[key as keyof Row] as string | null) ?? ""))
    .map((key): [string, string] => [
      LABELS[key],
      `${(client[key as keyof Row] as string | null) || "—"} → ${form[key] || "—"}`,
    ]);

  // 티켓은 계약이 들고 있습니다 — 몇 차 계약의 티켓인지까지 적어야 쓸모가 있습니다.
  const tickets = contracts.filter((c) => c.ticket_id);

  if (!editing) {
    return (
      <Section id="sec-basic" title="고객 기본 정보"
               right={<button className="btn btn-sm" type="button"
                              onClick={() => setEditing(true)}>편집</button>}>
        <div className="field-grid">
          <KV k="고객사" v={client.company} />
          <KV k="산업 분야" v={client.industry} />
          <KV k="국가" v={client.country} />
          <KV k="담당부서" v={client.department} />
          <KV k="Client ID" v={<span className="mono">{client.client_id}</span>} />
          <KV k="고객 종류" v={<Tag tone="blue">{client.customer_type}</Tag>} />
          <KV k="연동 티켓 (계약별)" span={2} v={
            tickets.length
              ? tickets.map((c) => (
                  <span key={c.seq} style={{ marginRight: 5 }}>
                    <Tag tone="blue">{c.ticket_id} <span style={{ opacity: .7 }}>{c.seq}차</span></Tag>
                  </span>
                ))
              : <span className="muted">연동 없음</span>
          } />
          <KV k="담당" v={client.owner} />
          <KV k="최초 수주일" v={<span className="mono">{fmt(client.first_won_on)}</span>} />
          <KV k="플랜 상태" v={<Tag tone={statusTone(client.plan_status)}>{client.plan_status}</Tag>} />
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
          <label className="form-label">최초 수주일</label>
          <input className="inp" type="date" value={form.first_won_on}
                 onChange={(e) => set("first_won_on", e.target.value)} />
        </div>
        {/* 고르개가 아니라 읽기 전용입니다. 플랜 상태는 계약 기간이 정합니다 — 여기서
            손으로 바꿔 두면 계약이 끝난 뒤에도 「사용중」이 남습니다. 바꾸려면 계약의
            기간을 고쳐야 하고, 그게 사실과 맞는 유일한 방법입니다. */}
        <div>
          <label className="form-label">플랜 상태</label>
          <div className="inp" aria-readonly="true"
               style={{ background: "var(--bg-soft)", color: "var(--muted)" }}>
            {client.plan_status}
          </div>
          <div style={{ fontSize: 11.5, color: "var(--faint)", marginTop: 4 }}>
            계약 기간에서 자동으로 정해집니다.
          </div>
        </div>
        <div>
          <label className="form-label">담당</label>
          <input className="inp" value={form.owner} onChange={(e) => set("owner", e.target.value)} />
        </div>
        <div>
          <label className="form-label">연동 티켓 (계약별)</label>
          <div className="field-value">
            {tickets.map((c) => `${c.seq}차 ${c.ticket_id}`).join(" · ") || "연동 없음"}{" "}
            <span className="muted">(계약에서 고침)</span>
          </div>
        </div>
      </div>
      <div className="modal-foot" style={{ marginTop: 14 }}>
        <button className="btn btn-sm" type="button" onClick={() => setEditing(false)}>취소</button>
        <button className="btn btn-sm btn-primary" type="button" disabled={saving}
                onClick={() => (changed.length ? setConfirming(true) : setEditing(false))}>
          {saving ? "저장 중" : "저장"}
        </button>
      </div>

      {confirming && (
        <Confirm
          title="고객 정보를 이렇게 바꿉니다"
          rows={changed}
          note="고객사 이름과 담당부서는 워크북의 다른 탭과 요약 카드가 조회해 가는 값입니다."
          okLabel="저장"
          onOk={() => save()}
          onClose={() => setConfirming(false)}
        />
      )}
    </Section>
  );
}

/** 계약 및 결제 정보 — 이 화면의 축. 읽기 전용입니다.
 *
 * 카드 머리에 버튼이 없습니다(2026-09-15 운영자 확인): 「+ 계약 추가」는 이 고객의 동작이라
 * 페이지 머리에만 있고, 「편집」은 탭바 오른쪽에서 이 카드와 「Perso 계정 및 플랜」을 같이
 * 입력칸으로 바꿉니다(`ContractEditor`). 계약 고르개도 탭바 오른쪽입니다.
 */
function ContractSection({ current, today }: { current: Contract; today: string }) {
  const docs = current.doc_types || [];
  return (
    <section className="sec" id="sec-contract">
      <div className="sec-head">
        <span className="sec-title">계약 및 결제 정보</span>
      </div>

      <div className="panel">
        <div className="field-grid">
          <KV k="수주 유형" v={<Tag tone={current.deal_type === "MRR" ? "d-mrr" : "d-poc"}>{current.deal_type}</Tag>} />
          <KV k="Ticket ID" v={current.ticket_id
            ? <><Tag tone="blue">{current.ticket_id}</Tag> <span className="muted" style={{ fontSize: 12 }}>인바운드 연동</span></>
            : <span className="muted">연동 없음</span>} />
          <KV k="계약기간" v={<span className="mono">
            {current.starts_on} – {current.ends_on} <span className="muted">({current.months}개월)</span>
          </span>} />
          <KV k="총 계약금액 (VAT 포함)" v={<span className="mono">
            {money(current.amount_incl_vat, current.currency)} <span className="muted">{current.currency}</span>
          </span>} />
          <KV k="계약서 유형" v={docs.length
            ? docs.map((t) => <span key={t} style={{ marginRight: 4 }}><Tag tone="neutral">{t}</Tag></span>)
            : "—"} />
          <KV k="계약 크레딧" v={<span className="mono">
            {num(current.credits)}{" "}
            <span className="muted">
              = {num(Math.round((current.credits ?? 0) / 60))}분 ·{" "}
              {current.vat_included ? "VAT 포함 금액 기준" : "공급가 기준"}
            </span>
          </span>} />
          {/* 총액으로 적힌 계약도 숫자를 보여 주되(총액 ÷ 1.1) **역산이라고 적습니다** —
              계약서에 적힌 금액과 계산한 금액이 같은 얼굴이면 안 됩니다. 워크북의 공급가
              열과 같은 값입니다: 비워 두면 회계가 합계를 내는 칸에서 그 행만 빠집니다. */}
          <KV k="공급가 (VAT 제외)" v={<span className="mono">
            {current.currency !== "KRW" ? (
              <span className="muted">VAT 해당 없음</span>
            ) : (
              <>
                {money(current.amount_excl_vat, current.currency)}{" "}
                <span className="muted">
                  {current.vat_included ? "총액에서 역산" : "VAT 10% 제외"}
                </span>
              </>
            )}
          </span>} />
          {/* 계산값입니다 — 금액 ÷ (계약 크레딧 ÷ 60). 단가 통화 칸은 없어졌습니다:
              단가는 언제나 계약 통화입니다. */}
          <KV k="분당 단가" v={current.unit_price ? <span className="mono">
            {money(current.unit_price, current.currency, 2)}{" "}
            <span className="muted">{current.currency}</span>
          </span> : "—"} />
          <KV k="결제 수단" v={current.payment_method} />
          <KV k="결제 방식" v={<>
            {current.payment_type || "—"}
            {current.payment_type === "할부" && <span className="muted"> {current.payments.length}회</span>}
          </>} />
          <KV k="최초 결제일" v={<span className="mono">{fmt(current.first_payment_on)}</span>} />
          <KV k="Billing Email" v={current.billing_email} />
          {/* **계약마다 다를 수 있어 여기 있습니다**(2026-08-31 운영자 지시). 고객 기본
              정보에 한 벌만 두던 시절에는 두 번째 계약의 담당자가 첫 계약의 담당자를
              덮었습니다 — 그리고 그것이 화면에서는 「담당자가 바뀌었다」와 같아 보였습니다. */}
          <KV k="고객 담당자" v={current.contact_name} />
          <KV k="고객 연락처" v={current.contact_info} />
          <KV k="계약 비고" span={2} v={current.note} />
        </div>
      </div>
      {/* 오늘 기준 상태를 아래 섹션들이 함께 씁니다. */}
      <span hidden data-today={today} />
    </section>
  );
}

// 제자리 편집 폼의 id — 저장 버튼이 탭바에 있어 폼 밖입니다.
const CONTRACT_FORM_ID = "won-contract-inline";

/** 「계약 · 플랜」 탭의 제자리 편집 — 두 카드가 같은 자리에서 입력칸이 됩니다.
 *
 *  칸과 규칙은 모달과 **같은 한 벌**(`ContractFields` · `PlanFields` · `useContractDraft`)이고,
 *  저장도 모달과 같은 라우트에 같은 몸통입니다. 지급 일정(회차 수 · 첫 지급일)을 건드린
 *  저장은 되돌릴 수 없어 확인 창을 한 번 지납니다 — 저장 버튼이 탭바에 있어 칸 옆의 빨간
 *  안내와 멀기 때문입니다. `key={contract.id}` 로 마운트되므로 계약이 바뀌면 새로 섭니다. */
function ContractEditor({ contract, options, onBusy, onDone }: {
  contract: Contract; options: Options | undefined;
  onBusy: (busy: boolean) => void; onDone: () => void;
}) {
  const f = useContractDraft(contract);
  const [note, setNote] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [save, saving] = useAction(async () => {
    setNote(null);
    try {
      await postForm(`/won-customers/contracts/${contract.id}`, f.body()!);
      onDone();
    } catch (error) {
      setConfirming(false);
      setNote(error instanceof Error ? error.message : String(error));
    }
  });
  // 저장이 끝나면 부모가 이 폼을 내리므로 `saving → false` 를 볼 기회가 없습니다. 내려갈 때 끕니다.
  useEffect(() => { onBusy(saving); return () => onBusy(false); }, [saving, onBusy]);
  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!f.draft) return;
    const problem = validate(f.draft);
    if (problem) { setNote(problem); return; }
    if (f.creditChanged) setConfirming(true); else save();
  };
  if (!options) return null;
  return (
    <form id={CONTRACT_FORM_ID} onSubmit={submit}>
      <section className="sec">
        <div className="sec-head">
          <span className="sec-title">계약 및 결제 정보</span>
          {note && (
            <div className="sec-actions" role="status" style={{ color: "var(--danger)" }}>{note}</div>
          )}
        </div>
        <div className="panel"><ContractFields f={f} options={options} /></div>
      </section>
      <section className="sec">
        <div className="sec-head">
          <span className="sec-title">Perso 계정 및 플랜</span>
        </div>
        <div className="panel"><PlanFields f={f} options={options} heading={false} /></div>
      </section>
      {confirming && (
        <Confirm
          title="지급 예정 목록을 다시 깝니다"
          rows={[["지급 일정", scheduleLabel(f)]]}
          note="손으로 추가·수정한 회차와 지급 완료 표시가 모두 사라집니다. 되돌릴 수 없습니다."
          okLabel="저장"
          onOk={() => save()}
          onClose={() => setConfirming(false)}
        />
      )}
    </form>
  );
}

function PlanSection({ contract }: { contract: Contract }) {
  return (
    <Section id="sec-plan" title="Perso 계정 및 플랜">
      <div className="field-grid">
        <KV k="플랜" v={contract.plan ? <Tag tone={`plan-${planTone(contract.plan)}`}>{contract.plan}</Tag> : "—"} />
        <KV k="플랜명" v={contract.plan_name} />
        <KV k="Perso Email" v={contract.perso_email} />
        <KV k="잔여일수" v={<span className="mono">
          {contract.plan_days_left === null ? "—"
            : contract.plan_days_left > 0 ? `${contract.plan_days_left}일` : "만료"}
        </span>} />
        <KV k="플랜 시작일" v={<span className="mono">{contract.plan_starts_on || "—"}</span>} />
        <KV k="플랜 만료일" v={<span className="mono">{contract.plan_ends_on || "—"}</span>} />
        <KV k="Account Invitation Limit" v={<span className="mono">{contract.invite_limit ?? "—"}</span>} />
        <KV k="Queue limit" v={<span className="mono">{contract.queue_limit ?? "—"}</span>} />
        <KV k="Concurrent Jobs" v={<span className="mono">{contract.concurrent_jobs ?? "—"}</span>} />
        <KV k="Space 개수" v={<span className="mono">{contract.space_count ?? "—"}</span>} />
        <KV k="space_seq" v={<span className="mono">{contract.space_seq || "—"}</span>} />
      </div>
    </Section>
  );
}

/** 크레딧 지급 현황 — 목업(수주고객-사용현황-목업_26)의 카드 그대로 (2026-09-15 운영자 지시).
 *
 *  위는 「확인된 지급」 타일(진행 막대 + 다음 지급 · 잔여 회차 · 미지급), 아래는 **표 하나**
 *  (회차 · 지급 예정일 · 크레딧 · 상태 · 비고). 예정과 완료를 두 표로 가르지 않는다 — 회차는
 *  한 줄로 이어진 일정이고, 상태는 줄마다 알약 고르개가 말한다. 날짜·크레딧·비고는 결제 표처럼
 *  그 자리에서 고친다(blur 저장). 일정을 다시 까는 폼(회차 수 · 첫 지급일)은 여기 없다 —
 *  계약 편집의 「크레딧 지급」 절이 그 자리다(운영자: 「이런 거 필요 없어」).
 *
 *  스냅샷 근거는 상태 칸의 작은 줄이다: 지급 뒤 소진이 시작됐으면 그 날짜, 예정일이 지났는데
 *  기록이 없으면 「확인 불가」. 지급 원장은 스냅샷에 없어 「지급됐다」까지는 못 말한다 —
 *  「확인 불가」는 오류가 아니라 「증거 없음」이다. */
function CreditSection({ contract, today, onDone, evidence }: {
  contract: Contract; today: string; onDone: () => void;
  /** 회차별로 스냅샷에 소진 시작 기록이 있나. 에이전트가 없으면 null 이고 줄이 안 뜹니다. */
  evidence: Map<number, GrantEvidence> | null;
}) {
  const grants = contract.credit_grants;
  const done = grants.filter((g) => g.done);
  const pending = grants.filter((g) => !g.done);
  const total = grants.length;
  // 계약 크레딧 대비 지급 진행률. 100%를 넘을 수 있습니다 — 테스트·보상 지급은 계약분
  // 밖이라, 넘은 것이 곧 오류는 아닙니다. 막대만 100 에서 멈춥니다.
  const percent = contract.credits ? Math.round((contract.granted_credits / contract.credits) * 100) : 0;
  const left = (contract.credits ?? 0) - contract.granted_credits;
  const unknown = evidence
    ? grants.filter((g) => !g.done && evidence.get(g.id)?.kind === "unseen").length
    : 0;

  const [ask, setAsk] = useState<Grant | null>(null);
  const [removing, setRemoving] = useState<Grant | null>(null);
  const [adding, setAdding] = useState(false);

  async function save(id: number, fields: Record<string, string>) {
    await postForm(`/won-customers/credits/${id}`, fields);
    onDone();
  }

  return (
    <Section id="sec-credit" title="크레딧 지급 현황" plain
             right={<>
               {unknown > 0 && <span style={{ color: "var(--amber-fg)", fontWeight: 700 }}>확인 불가 {unknown}건</span>}
               <button className="btn btn-sm" type="button" onClick={() => setAdding(!adding)}>+ 지급 회차 추가</button>
             </>}>
      <div className="panel">
        <div className="tile-h">
          <span className="tile-k">확인된 지급</span>
          <span className="tile-s">{done.length}/{total}회차</span>
        </div>
        <div className="tile-v">{num(contract.granted_credits)}<small>/ {num(contract.credits)}</small></div>
        <div className="pbar" style={{ margin: "10px 0 14px" }}>
          <div className="fill" style={{ width: `${Math.min(percent, 100)}%` }} />
        </div>
        <div className="field-grid">
          <KV k="다음 지급 예정일" v={contract.next_credit_on ? fmt(contract.next_credit_on) : (pending.length ? "—" : "완료")} />
          <KV k="다음 지급 크레딧" v={contract.next_credit_amount ? num(contract.next_credit_amount) : "—"} />
          <KV k="잔여 지급 회차" v={`${pending.length}회`} />
          <KV k={left < 0 ? "계약 외 추가 지급" : "미지급 크레딧"}
              v={<span style={left < 0 ? { color: "var(--amber-fg)" } : undefined}>{num(Math.abs(left))}</span>} />
        </div>
      </div>

      {adding && (
        <div className="panel">
          <GrantForm contract={contract} onCancel={() => setAdding(false)}
                     onDone={() => { setAdding(false); onDone(); }} />
        </div>
      )}

      {grants.length ? (
        <div className="table-wrap">
          <table className="mini grant-table">
            <thead><tr>
              <th>회차</th><th>지급 예정일</th><th className="num">크레딧</th><th>상태</th><th>지급자</th><th>비고</th>
              <th />
            </tr></thead>
            <tbody>
              {grants.map((grant) => (
                <GrantRow key={grant.id} grant={grant} total={total} today={today}
                          evidence={evidence?.get(grant.id)}
                          onAsk={() => setAsk(grant)}
                          onRemove={grant.done ? undefined : () => setRemoving(grant)}
                          onSave={(fields) => save(grant.id, fields)} />
              ))}
            </tbody>
          </table>
        </div>
      ) : <div className="panel"><div className="board-empty">지급 회차가 없습니다 — 계약 편집의 「크레딧 지급」에서 회차 수 · 첫 지급일을 정해 저장하면 깔립니다.</div></div>}

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

      {removing && (
        <Confirm
          title="이 지급 회차를 지웁니다"
          rows={[
            ["회차", `${removing.no}/${removing.total}`],
            ["지급 예정일", fmt(removing.grant_on)],
            ["크레딧", `${num(removing.amount)} 크레딧`],
          ]}
          note="남은 회차는 1부터 다시 번호가 매겨집니다. 지운 회차의 크레딧은 다른 회차로 옮겨 가지 않습니다 — 나눠 담으려면 계약 편집에서 지급 일정을 다시 까세요."
          okLabel="삭제"
          danger
          onOk={() => postForm(`/won-customers/credits/${removing.id}/delete`, {}).then(onDone)}
          onClose={() => setRemoving(null)}
        />
      )}
    </Section>
  );
}

/** 지급 회차 한 줄 — 결제 표(`PayRow`)와 같은 방식: 날짜·크레딧·지급자·비고는 칸에서 바로
 *  고치고 blur 에 저장, 상태는 알약 고르개(목업 `st-sel`)이고 바꾸면 확인 창을 지납니다.
 *
 *  상태 칸 아래 줄은 예정 회차의 D-day 뿐입니다. 스냅샷이 말하는 것(예정일이 지났는데 소진
 *  기록이 없다)은 **비고 칸**에 섭니다 — 운영자가 적은 비고가 없을 때 그 자리에 흐리게
 *  (2026-09-15 운영자: 「이런 건 비고에 넣어 두는 게 맞는 것 같아」). 「소진 시작 날짜」는
 *  따로 안 적습니다 — 자동 대조가 완료 처리하며 비고에 이미 적습니다. */
function GrantRow({ grant, total, today, evidence, onAsk, onRemove, onSave }: {
  grant: Grant; total: number; today: string; evidence?: GrantEvidence;
  onAsk: () => void; onRemove?: () => void;
  onSave: (fields: Record<string, string>) => Promise<void>;
}) {
  // 세 칸 다 **서버 값이 바뀌면 따라옵니다** — 다른 사람의 저장이 SSE 로 오면 내 칸이
  // 옛 값을 들고 있다가 focus·blur 한 번에 그 값을 도로 저장해 남의 편집을 되돌립니다.
  const [when, setWhen] = useState(grant.grant_on ?? "");
  useEffect(() => setWhen(grant.grant_on ?? ""), [grant.grant_on]);
  // 읽을 때는 60,000, 고칠 때는 날숫자 — 결제 표의 금액 칸과 같은 방식입니다. NULL(워크북에서
  // 빈 칸으로 들어온 회차)은 빈 칸입니다 — 0 으로 보이면 focus·blur 만으로 0 이 저장됩니다.
  const shown = (value: number | null) => (value === null ? "" : num(value));
  const [amount, setAmount] = useState(shown(grant.amount));
  useEffect(() => setAmount(shown(grant.amount)), [grant.amount]);
  const raw = (text: string) => text.replace(/[^0-9]/g, "");
  // 자동 대조가 「소진 시작 <날짜> (스냅샷 <날짜>)」를 적는 칸이기도 해서, 저쪽이 채우면
  // 화면 값이 내가 친 것과 달라집니다 — 그때 따라옵니다(결제 표의 비고 칸과 같은 이유).
  const [memo, setMemo] = useState(grant.memo ?? "");
  useEffect(() => setMemo(grant.memo ?? ""), [grant.memo]);

  // 지급자 — 완료 회차에만 있습니다. 「스냅샷 자동」도 여기 적히고 고칠 수 있습니다.
  const [by, setBy] = useState(grant.granted_by ?? "");
  useEffect(() => setBy(grant.granted_by ?? ""), [grant.granted_by]);

  // 스냅샷이 아직 안 본 예정 회차(예정일이 지났는데 소진 기록 없음)만 「확인 불가」입니다.
  const unknown = !grant.done && evidence?.kind === "unseen";
  const tone = grant.done ? "st-ok" : unknown ? "st-warn" : "st-neutral";
  // 스냅샷이 말하는 것 — 비고가 비어 있을 때 그 자리에 흐리게 섭니다.
  const hint = unknown
    ? "사용 기록이 없어 지급 확인 불가"
    : grant.done && evidence?.kind === "unseen" ? "지급 뒤 소진 기록 없음" : "";

  return (
    <tr className={grant.done ? undefined : "pending"}>
      <td className="mono">{grant.no}/{total}회차</td>
      <td>
        <input type="date" className="cell-inp" value={when}
               onChange={(e) => setWhen(e.target.value)}
               onBlur={() => when && when !== grant.grant_on && void onSave({ grant_on: when })} />
      </td>
      <td className="num">
        <input className="cell-inp" inputMode="numeric" style={{ textAlign: "right" }} value={amount}
               onFocus={() => setAmount(raw(amount))}
               onChange={(e) => setAmount(e.target.value)}
               onBlur={() => {
                 const next = raw(amount);
                 setAmount(next ? num(Number(next)) : shown(grant.amount));
                 if (next && next !== String(grant.amount ?? "")) void onSave({ amount: next });
               }} />
      </td>
      <td>
        <select className={`st-sel ${tone}`} value={grant.done ? "done" : unknown ? "unknown" : "todo"}
                onChange={(e) => { if ((e.target.value === "done") !== grant.done) onAsk(); }}>
          <option value="todo">예정</option>
          {unknown && <option value="unknown" disabled>확인 불가</option>}
          <option value="done">지급 확인</option>
        </select>
        {!grant.done && !unknown && (
          <div className="st-note"><span className={dueClass(grant.grant_on, today) || undefined}>{dday(grant.grant_on, today)}</span></div>
        )}
      </td>
      <td>
        {grant.done ? (
          <input className="cell-inp" value={by} placeholder="담당자" title={by}
                 onChange={(e) => setBy(e.target.value)}
                 onBlur={() => by.trim() !== (grant.granted_by ?? "") && void onSave({ granted_by: by.trim() })} />
        ) : <span className="muted">—</span>}
      </td>
      <td>
        <input className={`memo-in${hint && !memo ? " memo-in--hint" : ""}`} value={memo} placeholder={hint || "비고 입력"} title={memo || hint}
               onChange={(e) => setMemo(e.target.value)}
               onBlur={() => memo !== (grant.memo ?? "") && void onSave({ memo })} />
      </td>
      <td style={{ textAlign: "right" }}>
        {onRemove && <button className="btn btn-sm btn-ghost" type="button" onClick={onRemove}>삭제</button>}
      </td>
    </tr>
  );
}

/** 지급 회차 추가. 전체 회차 수는 서버가 다시 셉니다 — 화면이 세면 두 값이 갈라집니다. */
function GrantForm({ contract, onDone, onCancel }: {
  contract: Contract; onDone: () => void; onCancel: () => void;
}) {
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
    <>
      <div className="form-row" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div>
          <label className="form-label">지급 날짜</label>
          <input className="inp" type="date" value={when} onChange={(e) => setWhen(e.target.value)} />
        </div>
        <div>
          <label className="form-label">지급 크레딧</label>
          <input className="inp" type="number" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </div>
      </div>
      <div style={{ marginTop: 8 }}>
        <label className="form-label">메모</label>
        <input className="inp" value={memo} onChange={(e) => setMemo(e.target.value)}
               placeholder="예: 471203 40,000 / 471204 30,000 / 471205 20,000" />
      </div>
      <div style={{ display: "flex", gap: 7, justifyContent: "flex-end", marginTop: 9 }}>
        <button className="btn btn-sm" type="button" onClick={onCancel}>취소</button>
        <button className="btn btn-sm btn-primary" type="button" disabled={adding}
                onClick={() => add()}>{adding ? "추가 중" : "추가"}</button>
      </div>
    </>
  );
}

/** 결제 내역 — 목업(수주고객-사용현황-목업_26)의 카드 그대로 (2026-09-15 운영자 지시).
 *
 *  위는 「수금률」 타일(수금액 / 총액 · 수금 % · 진행 막대), 아래는 표 하나(회차 · 입금 날짜 ·
 *  금액 · 적용 환율 · 상태 · 비고). 날짜·금액·환율·비고는 칸에서 바로 고치고 blur 에 저장,
 *  상태는 알약 고르개이고 바꾸면 확인 창을 지납니다 — 수금율과 다음 결제일이 그 자리에서
 *  달라지고, 입금 완료로 넘길 때는 그 날짜의 환율까지 함께 박히기 때문입니다.
 *
 *  스냅샷이 말하는 것(국내 카드 결제 실패 · 링크만 발급 · 금액 불일치)은 지급 표와 같이 **비고
 *  자리**에 흐리게 섭니다 — 저장하지는 않습니다. 금액·기간이 맞는 결제 완료는 자동 대조가
 *  회차를 닫으며 비고에 적습니다(0120). */
function PaySection({ contract, today, onDone, evidence }: {
  contract: Contract; today: string; onDone: () => void;
  /** 회차별 국내 결제 근거(스냅샷). 에이전트가 없거나 원화 계약이 아니면 null. */
  evidence: Map<number, PaymentEvidence> | null;
}) {
  const payments = contract.payments;
  const paid = payments.filter((p) => p.done);
  const total = n(contract.amount_incl_vat);
  // 수금율은 **항상 계약 통화 기준**입니다. 환율 환산은 대시보드의 예상 MRR 에서만 씁니다.
  const percent = total ? Math.round((n(contract.collected) / total) * 100) : 0;
  // 미납 = 입금 전인데 날짜가 지난 회차.
  const overdue = payments.filter((p) => !p.done && dueClass(p.paid_on, today) === "over").length;

  const [ask, setAsk] = useState<Payment | null>(null);

  async function save(id: number, fields: Record<string, string>) {
    await postForm(`/won-customers/payments/${id}`, fields);
    onDone();
  }

  return (
    <Section id="sec-pay" title="결제 내역" plain
             right={<>
               <span>{[contract.payment_method, contract.payment_type].filter(Boolean).join(" · ") || "결제 방식 미정"} · 총 {payments.length}회</span>
               {overdue > 0 && <span style={{ color: "var(--red-fg)", fontWeight: 700 }}>· 미납 {overdue}건</span>}
             </>}>
      <div className="panel">
        <div className="tile-h">
          <span className="tile-k">수금률</span>
          <span className="tile-s">{paid.length}/{payments.length}회 입금</span>
        </div>
        <div className="credit-top">
          <div className="tile-v">{money(contract.collected, contract.currency)}<small>/ {money(contract.amount_incl_vat, contract.currency)} (VAT 포함)</small></div>
          <div className="credit-pct">수금 <b className="tnum">{percent}%</b></div>
        </div>
        <div className="pbar" style={{ margin: "10px 0 14px" }}>
          <div className={`fill${overdue ? " amber" : ""}`} style={{ width: `${Math.min(percent, 100)}%` }} />
        </div>
        <div className="field-grid">
          <KV k="입금 완료" v={`${paid.length}회`} />
          <KV k="미납" v={<span style={overdue ? { color: "var(--red-fg)" } : undefined}>{overdue}회</span>} />
          <KV k="잔여 금액 (VAT 포함)" v={<span className="mono">{money(total - n(contract.collected), contract.currency)}</span>} />
          <KV k="다음 결제일" v={contract.next_pay_on ? fmt(contract.next_pay_on) : "완료"} />
        </div>
      </div>

      {payments.length ? (
        <div className="table-wrap">
          <table className="mini grant-table">
            <thead><tr>
              <th>회차</th><th>입금 날짜</th><th className="num">금액</th><th>적용 환율</th><th>상태</th><th>비고</th>
            </tr></thead>
            <tbody>
              {payments.map((payment) => (
                <PayRow key={payment.id} payment={payment} currency={contract.currency} today={today}
                        evidence={evidence?.get(payment.id)}
                        onAsk={() => setAsk(payment)}
                        onSave={(fields) => save(payment.id, fields)} />
              ))}
            </tbody>
          </table>
        </div>
      ) : <div className="panel"><div className="board-empty">결제 회차가 없습니다 — 계약 편집의 「결제」에서 결제 방식과 분납 횟수를 정해 저장하면 깔립니다.</div></div>}

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
            : "수금율과 다음 결제일이 바로 갱신됩니다. 그 날짜의 환율이 함께 저장됩니다 — 나중에 오늘 환율로 다시 환산하지 않기 위해서입니다. 은행이 적용한 환율이 다르면 「적용 환율」 칸에 직접 적으면 되고, 비우면 다시 그 날짜 고시가로 채워집니다."}
          okLabel={ask.done ? "입금 전으로" : "입금 완료"}
          danger={ask.done}
          onOk={() => save(ask.id, { done: String(!ask.done) })}
          onClose={() => setAsk(null)}
        />
      )}
    </Section>
  );
}

/** 스냅샷이 이 회차에 대해 말하는 것 — 비고가 비어 있을 때 그 자리에 흐리게 섭니다. 금액·기간이
 *  맞는 결제 완료(`paid`)는 자동 대조가 회차를 닫으며 비고에 적으므로 여기서는 안 적습니다. */
function payHint(e: PaymentEvidence | undefined, done: boolean): string {
  if (!e) return "";
  switch (e.kind) {
    case "failed": return `카드 결제 실패 ${fmt(e.on)}${e.code ? ` · ${e.code}` : ""}`;
    case "ready": return `미입금 · 결제 링크 발급${e.requested ? ` ${fmt(e.requested)}` : ""} · ₩${num(e.amount)}`;
    case "paid-mismatch": return `카드 결제 있음 ₩${num(e.amount)} (${fmt(e.paidOn)}) — 금액이 회차와 다름`;
    case "paid": return done ? "" : `카드 결제 확인 ${fmt(e.paidOn)} — 입금 완료로 표시하세요`;
    default: return "";
  }
}

/** 결제 회차 한 줄 — 지급 표(`GrantRow`)와 같은 방식. 날짜·금액·환율·비고는 칸에서 바로 고치고
 *  blur 에 저장, 상태는 알약 고르개(입금 완료 · 예정 · 미납 · 결제 실패)이고 바꾸면 확인 창.
 *  「미납」(날짜가 지난 예정)과 「결제 실패」(스냅샷)는 고를 수 있는 값이 아니라 보이는 값입니다. */
function PayRow({ payment, currency, today, onAsk, onSave, evidence }: {
  payment: Payment; currency: string; today: string;
  onAsk: () => void; onSave: (fields: Record<string, string>) => Promise<void>;
  evidence?: PaymentEvidence;
}) {
  // 네 칸 다 **서버 값이 바뀌면 따라옵니다** — 자동 대조·다른 사람의 저장이 SSE 로 오면
  // 내 칸이 옛 값을 들고 있다가 focus·blur 한 번에 그 값을 도로 저장해 남의 편집을 되돌립니다.
  const [when, setWhen] = useState(payment.paid_on ?? "");
  useEffect(() => setWhen(payment.paid_on ?? ""), [payment.paid_on]);
  // 금액은 **읽을 때는 목업처럼 ₩1,722,600**, 고칠 때는 숫자입니다. type="number" 로 두면
  // 표에 자릿수 구분 없는 날숫자가 남아 다른 금액 칸과 따로 놉니다.
  const [amount, setAmount] = useState(money(payment.amount, currency));
  useEffect(() => setAmount(money(payment.amount, currency)), [payment.amount, currency]);
  // **환율도 고칠 수 있습니다** (2026-08-31 운영자 지시). 입금 완료로 바꾸면 그 날짜
  // 고시가가 자동으로 들어가지만, 실제로 은행이 적용한 환율은 다를 수 있습니다 — 그때
  // 여기서 적습니다. **비우면 다시 자동**입니다: 저장할 때 그 날짜 고시가로 채워집니다.
  const [rate, setRate] = useState(payment.fx_rate ? String(payment.fx_rate) : "");
  useEffect(() => setRate(payment.fx_rate ? String(payment.fx_rate) : ""), [payment.fx_rate]);
  // 비고 (0120). 자동 대조가 「스냅샷 결제 확인 <날짜>」를 적는 칸이기도 합니다.
  const [note, setNote] = useState(payment.note ?? "");
  useEffect(() => setNote(payment.note ?? ""), [payment.note]);

  const overdue = !payment.done && dueClass(payment.paid_on, today) === "over";
  const failed = !payment.done && evidence?.kind === "failed";
  const state = payment.done ? "done" : failed ? "failed" : overdue ? "late" : "todo";
  const tone = { done: "st-ok", failed: "st-danger", late: "st-warn", todo: "st-neutral" }[state];
  const hint = payHint(evidence, payment.done);
  const raw = (text: string) => text.replace(/[^0-9.-]/g, "");
  return (
    <tr className={payment.done ? undefined : "pending"}>
      <td className="mono">{payment.no}/{payment.total}회</td>
      <td>
        <input type="date" className="cell-inp" value={when}
               onChange={(e) => setWhen(e.target.value)}
               onBlur={() => when !== payment.paid_on && onSave({ paid_on: when })} />
      </td>
      <td className="num">
        <input className="cell-inp" inputMode="decimal" style={{ textAlign: "right" }} value={amount}
               onFocus={() => setAmount(raw(amount))}
               onChange={(e) => setAmount(e.target.value)}
               onBlur={() => {
                 const next = raw(amount);
                 setAmount(money(next, currency));
                 if (next !== String(payment.amount ?? "")) void onSave({ amount: next });
               }} />
      </td>
      <td className="mono nowrap">
        <input className="cell-inp" inputMode="decimal" style={{ textAlign: "right" }}
               value={rate} placeholder="비우면 그날 고시가"
               onChange={(e) => setRate(e.target.value)}
               onBlur={() => {
                 const next = rate.trim();
                 if (next !== String(payment.fx_rate ?? "")) void onSave({ fx_rate: next || "auto" });
               }} />
        {payment.fx_on && (
          <div className="st-note">{fmt(payment.fx_on)} 고시</div>
        )}
      </td>
      <td>
        <select className={`st-sel ${tone}`} value={state}
                onChange={(e) => { if ((e.target.value === "done") !== payment.done) onAsk(); }}>
          <option value="todo">예정</option>
          {state === "late" && <option value="late" disabled>미납</option>}
          {state === "failed" && <option value="failed" disabled>결제 실패</option>}
          <option value="done">입금 완료</option>
        </select>
        {!payment.done && (
          <div className="st-note"><span className={dueClass(payment.paid_on, today) || undefined}>{dday(payment.paid_on, today)}</span></div>
        )}
      </td>
      <td>
        <input className={`memo-in${hint && !note ? " memo-in--hint" : ""}`} value={note} placeholder={hint || "비고 입력"} title={note || hint}
               onChange={(e) => setNote(e.target.value)}
               onBlur={() => note !== (payment.note ?? "") && void onSave({ note })} />
      </td>
    </tr>
  );
}

/* 6번은 「갱신 · 비고」였습니다 — 갱신 계획 · 사용 중단 이유 · 비고 세 칸을 계약에 저장하던
   패널입니다. 운영자 지시(2026-08-14)로 화면도 열도 지웠습니다(이관 0073). 그 자리에는 그
   전에 「고객 클레임」 표가 있었고, 그때는 섹션 번호를 비워 두었습니다. 이번에는 당깁니다 —
   비워 두면 화면에 6이 없는 1·2·3·4·5·7·8 이 남고, 앵커로 오는 링크는 4·5번뿐이라
   (`WonCustomers.tsx` 의 sec-credit·sec-pay) 번호를 당겨도 어긋나는 자리가 없습니다.
   갱신 계획은 워크북의 열로만 남습니다 — 시트는 운영자의 것이라 콘솔이 안 건드립니다. */

/** 6 MRR 관리. 막대는 인식 시작월부터 최대 12개월 — 지난 달은 채워집니다. */
function RevenueSection({ contract, today }: { contract: Contract; today: string }) {
  const mrr = contract.deal_type === "MRR";
  /** **MRR 을 나누는 개월수는 계약 개월수가 아니라 플랜 개월수입니다** (2026-09-09).
   *
   *  여기가 `contract.months`(계약 개월수)였습니다. 바로 아래 「월간 MRR (VAT 포함)」은
   *  서버가 플랜 개월수로 나눈 값을 그대로 그리는데, 그 옆의 「공급가 기준」만 이 값으로
   *  화면이 직접 나눴습니다 — 그래서 계약 날짜를 고치면 **한쪽만 움직였습니다.**
   *  운영자가 그걸로 잡았습니다. 인식 개월수를 적는 아래 문장도 같은 값을 씁니다. */
  const months = contract.plan_months || contract.months || 1;
  const base = contract.revenue_from ? `${contract.revenue_from}-01` : contract.starts_on || today;
  const bars = mrr
    ? Array.from({ length: Math.min(months, 12) }, (_, i) => {
        const month = addMonths(base, i);
        return { key: month, on: month.slice(0, 7) <= today.slice(0, 7), label: `${month.slice(5, 7)}월` };
      })
    : contract.payments.map((p) => ({
        key: `p${p.id}`, on: p.done, label: p.paid_on ? `${p.paid_on.slice(5, 7)}월` : "—",
      }));

  return (
    <Section id="sec-revenue" title="MRR 관리"
             right={mrr ? `${base.slice(0, 7).replace("-", ".")}부터 ${months}개월 인식` : "결제월에 일시 인식"}>
      <div className="field-grid c3">
        <KV k="계약 종류" v={<Tag tone={mrr ? "d-mrr" : "d-poc"}>{contract.deal_type}</Tag>} />
        <KV k="총 계약 금액 (VAT 포함)"
            v={<span className="mono">{money(contract.amount_incl_vat, contract.currency)}</span>} />
        <KV k="월간 MRR (VAT 포함)" v={<span className="mono">
          {mrr ? <>{money(contract.monthly_revenue, contract.currency)} <span className="muted">/ 월</span></>
               : <span className="muted">결제월에 일시 인식</span>}
        </span>} />
        <KV k="월간 MRR (공급가 기준)" v={<span className="mono">
          {/* **서버가 낸 값입니다.** 화면이 나누면 옆 칸과 자가 갈립니다 — 환율을
              서버가 한 번만 환산하는 것과 같은 이유입니다. */}
          {mrr ? <>{money(contract.monthly_supply_revenue, contract.currency)} <span className="muted">/ 월</span></>
               : <span className="muted">결제월에 일시 인식</span>}
        </span>} />
        <KV k="매출 인식 시작 월" v={<span className="mono">
          {!mrr ? <span className="muted">결제월 기준</span>
            : <>{(contract.revenue_from || "").replace("-", ".")}{" "}
                <span className="muted">{contract.revenue_from_set ? "(직접 지정)" : "(계약 시작월)"}</span></>}
        </span>} />
      </div>
      <div className="mrr-note">
        {mrr
          ? `${base.slice(0, 7).replace("-", ".")}부터 ${months}개월 인식 · VAT 포함 총액 ÷ ${months} = ${money(contract.monthly_revenue, contract.currency)}`
          : "결제가 발생한 달에 전액 인식"}
      </div>
      <div className="revbar">
          {bars.map((bar) => (
            <div className="col" key={bar.key}>
              <div className={`b${bar.on ? " on" : ""}`} />
              <div className="l">{bar.label}</div>
            </div>
          ))}
      </div>
    </Section>
  );
}

/** 소통 히스토리 등록 — **고객 단위**입니다. 계약 차수를 비우면 협상 단계(계약 전) 기록이고,
 *  그래서 계약이 하나도 없는 고객에게도 쓸 수 있습니다. */
export function CommForm({ contactId, contracts, onDone, onCancel, defaultSeq }: {
  contactId: number; contracts: Contract[]; onDone: () => void; onCancel?: () => void;
  /** 어느 묶음의 「+ 소통 등록」에서 열렸나 — 그 계약이 고른 채로 뜹니다. */
  defaultSeq?: number | null;
}) {
  const today = new Date().toISOString().slice(0, 10);
  const [channel, setChannel] = useState("email");
  const [handler, setHandler] = useState("");
  const [when, setWhen] = useState(today);
  const [seq, setSeq] = useState(defaultSeq ? String(defaultSeq) : "");
  const [summary, setSummary] = useState("");
  const [add, adding] = useAction(async () => {
    if (!summary.trim()) return;
    await postForm(`/customers/${contactId}/interactions`, {
      channel, handler, happened_at: when, contract_seq: seq, summary,
    });
    setSummary(""); setHandler("");
    onDone();
  });
  return (
    <div style={{ paddingBottom: 16, marginBottom: 14, borderBottom: "1px solid var(--line-soft)" }}>
      <div className="form-row">
        <div>
          <label className="form-label">타입</label>
          <select className="inp" value={channel} onChange={(e) => setChannel(e.target.value)}>
            {CHANNELS.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="form-label">날짜</label>
          <input className="inp" type="date" value={when} onChange={(e) => setWhen(e.target.value)} />
        </div>
        <div>
          <label className="form-label">담당자</label>
          <input className="inp" value={handler} onChange={(e) => setHandler(e.target.value)} />
        </div>
        <div>
          <label className="form-label">관련 계약</label>
          <select className="inp" value={seq} onChange={(e) => setSeq(e.target.value)}>
            <option value="">협상 단계 (계약 전)</option>
            {contracts.slice().reverse().map((c) => (
              <option key={c.seq} value={c.seq}>{c.label}</option>
            ))}
          </select>
        </div>
      </div>
      <div style={{ marginTop: 10 }}>
        <label className="form-label">소통 내용 및 메모</label>
        <textarea className="inp" rows={3} value={summary} onChange={(e) => setSummary(e.target.value)}
                  placeholder="어떤 내용을 주고받았는지 기록" />
      </div>
      <div style={{ display: "flex", gap: 7, justifyContent: "flex-end", marginTop: 10 }}>
        {onCancel && <button className="btn btn-sm" type="button" onClick={onCancel}>취소</button>}
        <button className="btn btn-sm btn-primary" type="button" disabled={adding}
                onClick={() => add()}>{adding ? "등록 중" : "등록"}</button>
      </div>
    </div>
  );
}
