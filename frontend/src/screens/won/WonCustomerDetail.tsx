import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { getJSON, postForm } from "../../lib/api";
import { useAction } from "../../ui/ActionButton";
import { Confirm } from "./Confirm";
import { WonContractForm } from "./WonContractForm";
import {
  RETIRED,
  type Contract, type Grant, type ListData, type Options, type Payment, type Row,
  addMonths, dday, dueClass, fmt, initials, money, n, num, planTone, statusTone,
} from "./shared";

/** 수주 고객 상세 — 목업(`수주관리목업_0806.html` 의 `detailHTML`)의 섹션 그대로. 목업의
 * 8개 중 「갱신 · 비고」가 빠져 일곱 개입니다(이관 0073).
 *
 * **계약 선택 드롭다운이 이 화면의 축입니다.** 고객은 하나이고 계약이 여럿이라, 2~6번
 * 섹션은 전부 "지금 고른 계약" 의 내용이고 고르는 순간 함께 바뀝니다.
 *
 * 계약을 따라가지 **않는** 것은 **7번 소통 히스토리** 하나입니다 — 협상 단계 대화가 계약보다
 * 먼저 쌓이고 그대로 이어집니다(0065).
 */
const SECTIONS: [string, string][] = [
  ["sec-basic", "고객 기본 정보"],
  ["sec-contract", "계약 · 결제 정보"],
  ["sec-plan", "Perso 계정 · 플랜"],
  ["sec-credit", "크레딧 지급"],
  ["sec-pay", "결제 현황"],
  ["sec-revenue", "매출 관리"],
  ["sec-comm", "소통 히스토리"],
];

const AVATAR_COLORS = ["#0F766E", "#B45309", "#3730A3", "#B42318", "#026AA2", "#4B5563"];

/** 목업의 `statusTag` / `dealTag` / `planTag`. */
const Tag = ({ tone, children }: { tone: string; children: React.ReactNode }) =>
  <span className={`tag ${tone}`}>{children}</span>;

const stateTone = (state: string) =>
  state === "진행 중" ? "st-live" : state === "세팅중" ? "st-setup" : "st-stop";

export function WonCustomerDetail() {
  const { clientId } = useParams();
  const navigate = useNavigate();
  // 계약 폼은 이 화면 위의 모달입니다. 주소로 판단하므로 새로고침해도 열려 있고,
  // 뒤로가기가 곧 닫기입니다 — 모달을 상태로만 들면 둘 다 안 됩니다.
  const contractRoute = useLocation().pathname.includes("/contracts");
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
  const [addingComm, setAddingComm] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [retiring, setRetiring] = useState(false);

  // 액션 보드가 `/won-customers/2102#sec-credit` 로 보냅니다. 브라우저의 기본 앵커 이동은
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
  const comms = data.comms ?? [];

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
        <BasicSection client={data} contracts={contracts} options={list?.options} onDone={refresh} />

        {!current ? (
          <section className="sec" id="sec-contract">
            <div className="sec-head">
              <span className="sec-num">2</span><span className="sec-title">계약 및 결제 정보</span>
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
        ) : (
          <>
            <ContractSection
              client={data} contracts={contracts} current={current} today={today}
              showAll={showAll} onToggleAll={() => setShowAll(!showAll)}
              onPick={(seq) => { setPickedSeq(seq); setShowAll(false); }}
            />
            <PlanSection contract={current} />
            <CreditSection contract={current} today={today} onDone={refresh} />
            <PaySection contract={current} today={today} onDone={refresh} />
            <RevenueSection contract={current} today={today} />
          </>
        )}

        {/* 7 소통 히스토리 — 고객 단위. 계약을 골라도 바뀌지 않습니다. */}
        <section className="sec" id="sec-comm">
          <div className="sec-head">
            <span className="sec-num">7</span><span className="sec-title">소통 히스토리</span>
            <Tag tone="neutral">고객 단위 · 전체 계약 통합</Tag>
            <div className="sec-actions">
              <div className="chips">
                <Chip on={commFilter === "all"} onClick={() => setCommFilter("all")}
                      count={comms.length}>전체</Chip>
                <Chip on={commFilter === "nego"} onClick={() => setCommFilter("nego")}
                      count={comms.filter((x) => !x.contract_seq).length}>협상 단계</Chip>
                {contracts.slice().reverse().map((c) => (
                  <Chip key={c.seq} on={commFilter === c.seq} onClick={() => setCommFilter(c.seq)}
                        count={comms.filter((x) => x.contract_seq === c.seq).length}>{c.seq}차</Chip>
                ))}
              </div>
              {data.contact_id && (
                <button className="btn btn-sm btn-primary" type="button"
                        onClick={() => setAddingComm(!addingComm)}>+ 소통 등록</button>
              )}
            </div>
          </div>
          <div className="panel">
            {addingComm && data.contact_id && (
              <CommForm contactId={data.contact_id} contracts={contracts}
                        onCancel={() => setAddingComm(false)}
                        onDone={() => { setAddingComm(false); refresh(); }} />
            )}
            {(() => {
              const rows = comms.filter((item) =>
                commFilter === "all" ? true
                : commFilter === "nego" ? !item.contract_seq
                : item.contract_seq === commFilter);
              if (!rows.length) {
                return <div className="board-empty">
                  {commFilter === "all"
                    ? "등록된 소통 내역이 없습니다. + 소통 등록으로 기록하세요."
                    : "이 구분에 해당하는 소통 내역이 없습니다."}
                </div>;
              }
              return (
                <div className="timeline">
                  {rows.map((item, index) => (
                    <div key={item.id} className={`tl-item${index === 0 ? " mark" : ""}`}>
                      <div className="tl-meta">
                        <Tag tone="blue">{item.channel}</Tag>
                        {fmt(item.happened_at?.slice(0, 10))}
                        <span>·</span>
                        {item.handler || "—"}
                        <span style={{ marginLeft: 4 }}>
                          <Tag tone={item.contract_seq ? "neutral" : "st-setup"}>
                            {item.contract_seq ? `${item.contract_seq}차 계약` : "협상 단계"}
                          </Tag>
                        </span>
                      </div>
                      <div className="tl-text">{item.subject ? `${item.subject} — ` : ""}{item.summary}</div>
                    </div>
                  ))}
                </div>
              );
            })()}
          </div>
        </section>
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

function Chip({ on, count, onClick, children }: {
  on: boolean; count: number; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button type="button" className={`chip btn-sm${on ? " is-on" : ""}`} onClick={onClick}>
      {children}<span className="count">{count}</span>
    </button>
  );
}

/** 섹션 하나. 목업의 번호 뱃지 · 제목 · 오른쪽 액션 · 흰 카드. */
function Section({ num: number, id, title, right, children, plain }: {
  num: number; id: string; title: string;
  right?: React.ReactNode; children: React.ReactNode;
  /** 내용이 스스로 패널을 여러 개 그리는 섹션(크레딧·결제). */
  plain?: boolean;
}) {
  return (
    <section className="sec" id={id}>
      <div className="sec-head">
        <span className="sec-num">{number}</span>
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

function Stat({ label, value, sub, tone }: {
  label: string; value: React.ReactNode; sub?: string; tone?: string;
}) {
  return (
    <div>
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={tone ? { color: tone } : undefined}>{value}</div>
      {sub && <div style={{ fontSize: 11.5, color: "var(--faint)", marginTop: 2 }}>{sub}</div>}
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
    contact_name: "고객 담당자", contact_info: "고객 연락처",
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
      <Section num={1} id="sec-basic" title="고객 기본 정보"
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
          <KV k="고객 담당자" v={client.contact_name} />
          <KV k="고객 연락처" v={client.contact_info} />
          <KV k="최초 수주일" v={<span className="mono">{fmt(client.first_won_on)}</span>} />
          <KV k="플랜 상태" v={<Tag tone={statusTone(client.plan_status)}>{client.plan_status}</Tag>} />
        </div>
      </Section>
    );
  }

  return (
    <Section num={1} id="sec-basic" title="고객 기본 정보">
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

/** 2 계약 및 결제 정보 — 이 화면의 축.
 *
 * 오른쪽 액션이 넷입니다(목업 그대로): 계약 고르개 · 전체 계약 내역 접기/펴기 · 계약 추가 ·
 * 편집. 고르개를 바꾸면 이 아래 3·4·5·7번이 그 계약의 값으로 함께 바뀝니다.
 */
function ContractSection({ client, contracts, current, today, showAll, onToggleAll, onPick }: {
  client: Row; contracts: Contract[]; current: Contract; today: string;
  showAll: boolean; onToggleAll: () => void; onPick: (seq: number) => void;
}) {
  const navigate = useNavigate();
  const docs = current.doc_types || [];
  return (
    <section className="sec" id="sec-contract">
      <div className="sec-head">
        <span className="sec-num">2</span><span className="sec-title">계약 및 결제 정보</span>
        <div className="sec-actions">
          <select className="sel-pill" value={current.seq}
                  onChange={(event) => onPick(Number(event.target.value))}>
            {contracts.slice().reverse().map((c) => (
              <option key={c.seq} value={c.seq}>
                {c.label} · {fmt(c.starts_on)}–{fmt(c.ends_on)} · {c.state}
              </option>
            ))}
          </select>
          <button className={`btn btn-sm${showAll ? " btn-ghost" : ""}`} type="button" onClick={onToggleAll}>
            전체 계약 내역 {contracts.length}건 {showAll ? "▲" : "▼"}
          </button>
          <button className="btn btn-sm" type="button"
                  onClick={() => navigate(`/won-customers/${client.client_id}/contracts/new`)}>+ 계약 추가</button>
          <button className="btn btn-sm" type="button"
                  onClick={() => navigate(`/won-customers/${client.client_id}/contracts/${current.id}`)}>편집</button>
        </div>
      </div>

      {showAll && (
        <div className="panel" style={{ marginBottom: 10, background: "var(--bg-soft)" }}>
          <div className="sub-head">
            <span className="sub-title">전체 계약 내역</span>
            <span className="sub-count">{contracts.length}건</span>
            <button className="btn btn-sm btn-ghost" type="button" style={{ marginLeft: "auto" }}
                    onClick={() => navigate(`/won-customers/${client.client_id}/contracts/new`)}>+ 계약 추가</button>
          </div>
          <div className="table-wrap">
            <table className="mini">
              <thead><tr>
                <th>계약</th><th>상태</th><th>수주 유형</th><th>계약기간</th><th>플랜</th>
                <th className="num">총 계약금액</th><th className="num">계약 크레딧</th><th />
              </tr></thead>
              <tbody>
                {contracts.slice().reverse().map((c) => (
                  <tr key={c.seq}>
                    <td>{c.label}</td>
                    <td><Tag tone={stateTone(c.state)}>{c.state}</Tag></td>
                    <td><Tag tone={c.deal_type === "MRR" ? "d-mrr" : "d-poc"}>{c.deal_type}</Tag></td>
                    <td className="mono nowrap">{fmt(c.starts_on)} – {fmt(c.ends_on)}</td>
                    <td>{c.plan ? <Tag tone={`plan-${planTone(c.plan)}`}>{c.plan}</Tag> : "—"}</td>
                    <td className="num nowrap">{money(c.amount_incl_vat, c.currency)}</td>
                    <td className="num">{num(c.credits)}</td>
                    <td style={{ textAlign: "right" }}>
                      <button className="btn btn-sm btn-ghost" type="button"
                              disabled={c.seq === current.seq}
                              onClick={() => onPick(c.seq)}>
                        {c.seq === current.seq ? "보는 중" : "열기"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="panel">
        <div className="field-grid">
          <KV k="수주 유형" v={<Tag tone={current.deal_type === "MRR" ? "d-mrr" : "d-poc"}>{current.deal_type}</Tag>} />
          <KV k="Ticket ID" v={current.ticket_id
            ? <><Tag tone="blue">{current.ticket_id}</Tag> <span className="muted" style={{ fontSize: 12 }}>인바운드 연동</span></>
            : <span className="muted">연동 없음</span>} />
          <KV k="계약기간" v={<span className="mono">
            {current.starts_on} – {current.ends_on} <span className="muted">({current.months}개월)</span>
          </span>} />
          <KV k="계약서 유형" span={2} v={docs.length
            ? docs.map((t) => <span key={t} style={{ marginRight: 4 }}><Tag tone="neutral">{t}</Tag></span>)
            : "—"} />
          <KV k="계약 크레딧" v={<span className="mono">
            {num(current.credits)}{" "}
            <span className="muted">
              = {num(Math.round((current.credits ?? 0) / 60))}분 ·{" "}
              {current.vat_included ? "VAT 포함 금액 기준" : "공급가 기준"}
            </span>
          </span>} />
          <KV k="총 계약금액 (VAT 포함)" v={<span className="mono">
            {money(current.amount_incl_vat, current.currency)} <span className="muted">{current.currency}</span>
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
          <KV k="계약 비고" span={2} v={current.note} />
        </div>
      </div>
      {/* 오늘 기준 상태를 아래 섹션들이 함께 씁니다. */}
      <span hidden data-today={today} />
    </section>
  );
}

function PlanSection({ contract }: { contract: Contract }) {
  return (
    <Section num={3} id="sec-plan" title="Perso 계정 및 플랜">
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

function CreditSection({ contract, today, onDone }: {
  contract: Contract; today: string; onDone: () => void;
}) {
  const done = contract.credit_grants.filter((g) => g.done);
  const pending = contract.credit_grants.filter((g) => !g.done);
  const total = contract.credit_grants.length;
  // 계약 크레딧 대비 지급 진행률. 100%를 넘을 수 있습니다 — 테스트·보상 지급은 계약분
  // 밖이라, 넘은 것이 곧 오류는 아닙니다. 그래서 자르지 않고 그대로 보여 줍니다.
  const percent = contract.credits
    ? Math.round((contract.granted_credits / contract.credits) * 100)
    : 0;
  const left = (contract.credits ?? 0) - contract.granted_credits;

  const [ask, setAsk] = useState<Grant | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);

  async function save(id: number, fields: Record<string, string>) {
    await postForm(`/won-customers/credits/${id}`, fields);
    onDone();
  }

  const row = (grant: Grant, mode: "pending" | "done") =>
    editing === grant.no ? (
      <GrantEdit key={grant.id} grant={grant} total={total}
                 onCancel={() => setEditing(null)}
                 onRevert={grant.done ? () => { setEditing(null); setAsk(grant); } : undefined}
                 onSave={(fields) => save(grant.id, fields).then(() => setEditing(null))} />
    ) : mode === "pending" ? (
      <tr key={grant.id} className="pending">
        <td className="mono">{grant.no}/{total}</td>
        <td className="mono">
          {fmt(grant.grant_on)} <span className={dueClass(grant.grant_on, today) || undefined}
                                      style={{ color: "var(--faint)" }}>{dday(grant.grant_on, today)}</span>
          {grant.memo && <div className="memo-line">{grant.memo}</div>}
        </td>
        <td className="num">{num(grant.amount)}</td>
        <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
          <button className="btn btn-sm btn-ghost" type="button" onClick={() => setEditing(grant.no)}>수정</button>{" "}
          <button className="btn btn-sm" type="button" onClick={() => setAsk(grant)}>지급 완료</button>
        </td>
      </tr>
    ) : (
      <tr key={grant.id}>
        <td className="mono">{grant.no}/{total}</td>
        <td className="mono">{fmt(grant.grant_on)}
          {grant.memo && <div className="memo-line">{grant.memo}</div>}
        </td>
        <td className="num">{num(grant.amount)}</td>
        <td style={{ whiteSpace: "nowrap" }}>
          {grant.granted_by || "—"}{" "}
          <button className="btn btn-sm btn-ghost" type="button" onClick={() => setEditing(grant.no)}>수정</button>
        </td>
      </tr>
    );

  return (
    <Section num={4} id="sec-credit" title="크레딧 지급 현황" plain
             right={<button className="btn btn-sm" type="button"
                            onClick={() => setAdding(!adding)}>+ 지급 회차 추가</button>}>
      <div className="panel">
        <div className="meter">
          <div className="meter-head">
            <div>
              <div className="field-label">누적 지급 크레딧</div>
              <div className="meter-num">
                {num(contract.granted_credits)}{" "}
                <span style={{ fontSize: 13, fontWeight: 600, color: "var(--muted)" }}>
                  / {num(contract.credits)}
                </span>
              </div>
            </div>
            <div className="meter-note">
              {percent}% · {done.length}/{total}회차 · {num(Math.round(contract.granted_credits / 60))}분 지급
            </div>
          </div>
          <div className="meter-track">
            <div className="meter-fill" style={{ width: `${Math.min(percent, 100)}%` }} />
          </div>
        </div>
        <div className="stat-row">
          <Stat label="다음 지급일" value={contract.next_credit_on ? fmt(contract.next_credit_on) : "—"} />
          <Stat label="다음 지급 크레딧" value={contract.next_credit_amount ? num(contract.next_credit_amount) : "—"} />
          <Stat label="잔여 지급 회차" value={`${pending.length}회`} />
          <Stat label={left < 0 ? "계약 외 추가 지급" : "잔여 크레딧"} value={num(Math.abs(left))}
                tone={left < 0 ? "var(--amber-fg)" : undefined} />
        </div>
      </div>

      {adding && (
        <div className="panel">
          <GrantForm contract={contract} onCancel={() => setAdding(false)}
                     onDone={() => { setAdding(false); onDone(); }} />
        </div>
      )}

      <div className="split-2" style={{ marginTop: 10 }}>
        <div className="panel">
          <div className="sub-head">
            <span className="sub-title">지급 예정</span><span className="sub-count">{pending.length}건</span>
          </div>
          {pending.length ? (
            <div className="table-wrap"><table className="mini">
              <thead><tr>
                <th>회차</th><th>지급 예정일</th><th className="num">크레딧</th><th style={{ width: 96 }} />
              </tr></thead>
              <tbody>{pending.map((g) => row(g, "pending"))}</tbody>
            </table></div>
          ) : <div className="board-empty">지급 예정 회차가 없습니다.</div>}
        </div>
        <div className="panel">
          <div className="sub-head">
            <span className="sub-title">지급 완료</span><span className="sub-count">{done.length}건</span>
          </div>
          {done.length ? (
            <div className="table-wrap"><table className="mini">
              <thead><tr>
                <th>회차</th><th>지급 날짜</th><th className="num">크레딧</th><th>지급자</th>
              </tr></thead>
              <tbody>{done.slice().reverse().map((g) => row(g, "done"))}</tbody>
            </table></div>
          ) : <div className="board-empty">아직 지급 내역이 없습니다.</div>}
        </div>
      </div>

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

/** 목업의 `editRow` — 행 자리에서 그대로 펴지는 편집 폼. */
function GrantEdit({ grant, total, onSave, onCancel, onRevert }: {
  grant: Grant; total: number;
  onSave: (fields: Record<string, string>) => void;
  onCancel: () => void;
  onRevert?: () => void;
}) {
  const [when, setWhen] = useState(grant.grant_on ?? "");
  const [amount, setAmount] = useState(String(grant.amount ?? ""));
  const [by, setBy] = useState(grant.granted_by ?? "");
  const [memo, setMemo] = useState(grant.memo ?? "");
  return (
    <tr className="pending">
      <td className="mono">{grant.no}/{total}</td>
      <td colSpan={3}>
        <div className="form-row" style={{ gridTemplateColumns: grant.done ? "1fr 1fr 1fr" : "1fr 1fr" }}>
          <div>
            <label className="form-label">지급 날짜</label>
            <input className="inp" type="date" value={when} onChange={(e) => setWhen(e.target.value)} />
          </div>
          <div>
            <label className="form-label">지급 크레딧</label>
            <input className="inp" type="number" value={amount} onChange={(e) => setAmount(e.target.value)} />
          </div>
          {grant.done && (
            <div>
              <label className="form-label">지급자</label>
              <input className="inp" value={by} onChange={(e) => setBy(e.target.value)} />
            </div>
          )}
        </div>
        <div style={{ marginTop: 8 }}>
          <label className="form-label">메모</label>
          <input className="inp" value={memo} onChange={(e) => setMemo(e.target.value)} />
        </div>
        <div style={{ display: "flex", gap: 7, justifyContent: "flex-end", marginTop: 9 }}>
          {onRevert && (
            <button className="btn btn-sm" type="button" style={{ marginRight: "auto", color: "var(--red-fg)" }}
                    onClick={onRevert}>지급 취소</button>
          )}
          <button className="btn btn-sm" type="button" onClick={onCancel}>취소</button>
          <button className="btn btn-sm btn-primary" type="button"
                  onClick={() => onSave({ grant_on: when, amount, granted_by: by, memo })}>저장</button>
        </div>
      </td>
    </tr>
  );
}

function PaySection({ contract, today, onDone }: {
  contract: Contract; today: string; onDone: () => void;
}) {
  const paid = contract.payments.filter((p) => p.done);
  const total = n(contract.amount_incl_vat);
  // 수금율은 **항상 계약 통화 기준**입니다. 환율 환산은 대시보드의 예상 MRR 에서만 씁니다.
  const percent = total ? Math.round((n(contract.collected) / total) * 100) : 0;

  const [ask, setAsk] = useState<Payment | null>(null);

  async function save(id: number, fields: Record<string, string>) {
    await postForm(`/won-customers/payments/${id}`, fields);
    onDone();
  }

  return (
    <Section num={5} id="sec-pay" title="결제 현황" plain>
      <div className="panel">
        <div className="meter">
          <div className="meter-head">
            <div>
              <div className="field-label">수금율</div>
              <div className="meter-num">{percent}%</div>
            </div>
            <div className="meter-note">
              {money(contract.collected, contract.currency)} / {money(contract.amount_incl_vat, contract.currency)}{" "}
              <span style={{ color: "var(--faint)" }}>(VAT 포함)</span>
            </div>
          </div>
          <div className="meter-track">
            <div className={`meter-fill${percent < 100 ? " amber" : ""}`}
                 style={{ width: `${Math.min(percent, 100)}%` }} />
          </div>
        </div>
        <div className="stat-row">
          <Stat label="총 계약 금액 (VAT 포함)" value={money(contract.amount_incl_vat, contract.currency)}
                sub={contract.currency !== "KRW"
                  ? "VAT 해당 없음"
                  : `공급가 ${money(contract.amount_excl_vat, contract.currency)}${
                      contract.vat_included ? " (역산)" : ""}`} />
          <Stat label="수금 완료 금액 (VAT 포함)" value={money(contract.collected, contract.currency)} />
          <Stat label="잔여 금액 (VAT 포함)" value={money(total - n(contract.collected), contract.currency)} />
          <Stat label="다음 결제일" value={contract.next_pay_on ? fmt(contract.next_pay_on) : "완료"} />
        </div>
        <div className="stat-row" style={{ borderTop: "none", paddingTop: 0, marginTop: 12 }}>
          <Stat label="총 분납 횟수" value={`${contract.payments.length}회`} />
          <Stat label="분납 완료" value={`${paid.length}회`} />
          <Stat label="잔여 분납" value={`${contract.payments.length - paid.length}회`} />
          <Stat label="결제 수단" value={
            <span style={{ fontSize: 14 }}>{contract.payment_method || "—"} · {contract.payment_type || "—"}</span>
          } />
        </div>
      </div>

      <div className="panel">
        <div className="sub-head">
          <span className="sub-title">결제 히스토리</span>
          <span className="sub-count">{contract.payments.length}건</span>
          <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--faint)" }}>
            입금 확인 후 상태를 바꾸면 수금율에 바로 반영됩니다
          </span>
        </div>
        <div className="table-wrap">
          <table className="mini">
            <thead><tr>
              <th>분납 차수</th><th style={{ width: 190 }}>입금 날짜</th>
              <th className="num">금액</th><th>적용 환율</th><th style={{ width: 130 }}>상태</th>
            </tr></thead>
            <tbody>
              {contract.payments.map((payment) => (
                <PayRow key={payment.id} payment={payment} currency={contract.currency} today={today}
                        onAsk={() => setAsk(payment)}
                        onSave={(fields) => save(payment.id, fields)} />
              ))}
            </tbody>
          </table>
        </div>
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

/** 목업처럼 날짜와 금액을 **그 칸에서 바로** 고칩니다 — 수정 버튼을 거치지 않습니다.
 *
 * 상태만 확인 창을 거칩니다: 수금율과 다음 결제일이 그 자리에서 달라지고, 입금 완료로
 * 넘길 때는 그 날짜의 환율까지 함께 박히기 때문입니다(노션 §6).
 */
function PayRow({ payment, currency, today, onAsk, onSave }: {
  payment: Payment; currency: string; today: string;
  onAsk: () => void; onSave: (fields: Record<string, string>) => Promise<void>;
}) {
  const [when, setWhen] = useState(payment.paid_on ?? "");
  // 금액은 **읽을 때는 목업처럼 ₩1,722,600**, 고칠 때는 숫자입니다. type="number" 로 두면
  // 표에 자릿수 구분 없는 날숫자가 남아 다른 금액 칸과 따로 놉니다.
  const [amount, setAmount] = useState(money(payment.amount, currency));
  // **환율도 고칠 수 있습니다** (2026-08-31 운영자 지시). 입금 완료로 바꾸면 그 날짜
  // 고시가가 자동으로 들어가지만, 실제로 은행이 적용한 환율은 다를 수 있습니다 — 그때
  // 여기서 적습니다. **비우면 다시 자동**입니다: 저장할 때 그 날짜 고시가로 채워집니다.
  const [rate, setRate] = useState(payment.fx_rate ? String(payment.fx_rate) : "");
  // 비우고 저장하면 서버가 채워 넣으므로 **화면 값이 내가 친 것과 달라집니다** — 그때
  // 따라옵니다(날짜·금액 칸에는 없어도 되는 줄입니다: 그 둘은 적은 값이 그대로 남습니다).
  useEffect(() => setRate(payment.fx_rate ? String(payment.fx_rate) : ""), [payment.fx_rate]);
  const overdue = !payment.done && dueClass(payment.paid_on, today) === "over";
  const raw = (text: string) => text.replace(/[^0-9.-]/g, "");
  return (
    <tr className={payment.done ? undefined : "pending"}>
      <td className="mono">{payment.no}/{payment.total}차</td>
      <td>
        <input type="date" className="cell-inp" value={when}
               onChange={(e) => setWhen(e.target.value)}
               onBlur={() => when !== payment.paid_on && onSave({ paid_on: when })} />
        {overdue && (
          <div className="memo-line" style={{ color: "var(--red-fg)" }}>
            {fmt(payment.paid_on)} · {dday(payment.paid_on, today)}
          </div>
        )}
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
          <div className="memo-line">{fmt(payment.fx_on)} 고시</div>
        )}
      </td>
      <td>
        <select className={`pay-sel${payment.done ? " is-done" : ""}`} value={payment.done ? "1" : "0"}
                onChange={onAsk}>
          <option value="1">입금 완료</option>
          <option value="0">입금 전</option>
        </select>
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

/** 6 매출 관리. 막대는 인식 시작월부터 최대 12개월 — 지난 달은 채워집니다. */
function RevenueSection({ contract, today }: { contract: Contract; today: string }) {
  const mrr = contract.deal_type === "MRR";
  const months = contract.months || 1;
  const base = contract.revenue_from ? `${contract.revenue_from}-01` : contract.starts_on || today;
  const bars = mrr
    ? Array.from({ length: Math.min(months, 12) }, (_, i) => {
        const month = addMonths(base, i);
        return { key: month, on: month.slice(0, 7) <= today.slice(0, 7), height: "70%",
                 label: `${month.slice(5, 7)}월` };
      })
    : contract.payments.map((p) => ({
        key: `p${p.id}`, on: p.done, height: p.done ? "90%" : "20%",
        label: p.paid_on ? `${p.paid_on.slice(5, 7)}월` : "—",
      }));

  return (
    <Section num={6} id="sec-revenue" title="매출 관리">
      <div className="field-grid">
        <KV k="계약 종류" v={<Tag tone={mrr ? "d-mrr" : "d-poc"}>{contract.deal_type}</Tag>} />
        <KV k="총 계약 금액 (VAT 포함)"
            v={<span className="mono">{money(contract.amount_incl_vat, contract.currency)}</span>} />
        <KV k="월간 매출 (VAT 포함)" v={<span className="mono">
          {mrr ? <>{money(contract.monthly_revenue, contract.currency)} <span className="muted">/ 월</span></>
               : <span className="muted">결제월에 일시 인식</span>}
        </span>} />
        <KV k="월간 매출 (공급가 기준)" v={<span className="mono">
          {mrr ? <>{money(n(contract.amount_excl_vat) / months, contract.currency)} <span className="muted">/ 월</span></>
               : <span className="muted">결제월에 일시 인식</span>}
        </span>} />
        <KV k="매출 인식 시작 월" v={<span className="mono">
          {!mrr ? <span className="muted">결제월 기준</span>
            : <>{(contract.revenue_from || "").replace("-", ".")}{" "}
                <span className="muted">{contract.revenue_from_set ? "(직접 지정)" : "(계약 시작월)"}</span></>}
        </span>} />
      </div>
      <div style={{ marginTop: 14, paddingTop: 14, borderTop: "1px solid var(--line-soft)" }}>
        <div className="field-label">
          {mrr
            ? `${base.slice(0, 7).replace("-", ".")}부터 ${months}개월 인식 · VAT 포함 총액 ÷ ${months} = ${money(contract.monthly_revenue, contract.currency)}`
            : "결제가 발생한 달에 전액 인식"}
        </div>
        <div className="revbar">
          {bars.map((bar) => (
            <div className="col" key={bar.key}>
              <div className={`b${bar.on ? " on" : ""}`} style={{ height: bar.height }} />
              <div className="l">{bar.label}</div>
            </div>
          ))}
        </div>
      </div>
    </Section>
  );
}

/** 소통 히스토리 등록 — **고객 단위**입니다. 계약 차수를 비우면 협상 단계(계약 전) 기록이고,
 *  그래서 계약이 하나도 없는 고객에게도 쓸 수 있습니다. */
export function CommForm({ contactId, contracts, onDone, onCancel }: {
  contactId: number; contracts: Contract[]; onDone: () => void; onCancel?: () => void;
}) {
  const today = new Date().toISOString().slice(0, 10);
  const [channel, setChannel] = useState("email");
  const [handler, setHandler] = useState("");
  const [when, setWhen] = useState(today);
  const [seq, setSeq] = useState("");
  const [summary, setSummary] = useState("");
  const [add, adding] = useAction(async () => {
    if (!summary.trim()) return;
    await postForm(`/customers/${contactId}/interactions`, {
      channel, handler, happened_at: when, contract_seq: seq, summary,
    });
    setSummary(""); setHandler(""); setSeq("");
    onDone();
  });
  return (
    <div style={{ paddingBottom: 16, marginBottom: 14, borderBottom: "1px solid var(--line-soft)" }}>
      <div className="form-row">
        <div>
          <label className="form-label">소통 플랫폼</label>
          <select className="inp" value={channel} onChange={(e) => setChannel(e.target.value)}>
            {[["email", "메일"], ["phone", "전화"], ["whatsapp", "왓츠앱"],
              ["meeting", "미팅"], ["sms", "문자"], ["manual", "기타"]].map(([value, label]) => (
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
