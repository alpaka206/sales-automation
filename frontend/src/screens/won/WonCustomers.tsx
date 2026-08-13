import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { getJSON, postForm } from "../../lib/api";
import { ActionButton } from "../../ui/ActionButton";
import {
  type ListData, type Row,
  STATUS_ORDER, daysUntil, dday, dueClass, dueText, fmt, initials, money, num,
  planTone, statusTone,
} from "./shared";

/** 수주 고객 목록 — 운영자가 준 HTML 목업을 그대로 옮긴 화면.
 *
 * 마크업과 클래스 이름이 목업과 같습니다. 스타일은 `static/won.css` 에 있고 전부 `.won`
 * 아래로 스코프되어 있어서, 이 화면만 목업처럼 보이고 나머지 콘솔은 그대로입니다.
 */
const TAG_STATUS: Record<string, string> = { ok: "st-live", warn: "st-setup", off: "st-stop" };

function StatusTag({ status }: { status: string }) {
  return <span className={`tag ${TAG_STATUS[statusTone(status)]}`}>{status}</span>;
}
function PlanTag({ plan }: { plan: string | null }) {
  if (!plan) return <span className="tag neutral">미등록</span>;
  return <span className={`tag plan-${planTone(plan)}`}>{plan}</span>;
}
function DealTag({ deal }: { deal: string | null }) {
  if (!deal) return <>—</>;
  return <span className={`tag ${deal === "MRR" ? "d-mrr" : "d-poc"}`}>{deal}</span>;
}

// 아바타 색. 저장하지 않고 이름에서 만듭니다 — 색 하나 때문에 칸을 만들 이유가 없고,
// 같은 고객은 언제 봐도 같은 색이어야 합니다.
const AVATAR_COLORS = ["#0F766E", "#B45309", "#3730A3", "#B42318", "#026AA2", "#4B5563"];
const avatarColor = (id: number) => AVATAR_COLORS[id % AVATAR_COLORS.length];

/** 목업의 인라인 SVG 그대로. 콘솔의 `Icon` 을 쓰지 않는 이유는 won.css 와 같습니다 —
 *  획 두께(1.9~2)가 달라서, 섞으면 이 화면만 다른 굵기의 아이콘이 섞입니다. */
const GLYPHS: Record<string, string> = {
  person: '<path d="M16 19a4 4 0 0 0-8 0"/><circle cx="12" cy="10" r="3"/>',
  trend: '<path d="M3 17l6-6 4 4 7-7"/><path d="M14 8h6v6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  card: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>',
  alert: '<path d="M12 9v4M12 17h.01"/><circle cx="12" cy="12" r="9"/>',
  inbound: '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 20h16"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
};
function G({ name, size = 14, stroke = "currentColor", width = 2 }: {
  name: keyof typeof GLYPHS; size?: number; stroke?: string; width?: number;
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={stroke}
         strokeWidth={width} aria-hidden="true"
         dangerouslySetInnerHTML={{ __html: GLYPHS[name] }} />
  );
}

/** 목업의 `man()` — 범례는 만원 단위입니다. 카드의 큰 숫자가 이미 원 단위라, 그 아래
 *  통화별 내역까지 자릿수를 다 적으면 어느 쪽이 합계인지 한눈에 안 갈립니다. */
const man = (value: number) => `₩${Math.round(value / 10000).toLocaleString("en-US")}만`;

// "1,800 크레딧" 만 보이면 그게 마지막 회차인지 열두 번 중 두 번째인지 알 수 없습니다 —
// 열어 봐야 판단이 되는 것을 목록에서 끝내려고 회차를 같이 적습니다. 금액 **뒤**입니다:
// 훑을 때 먼저 읽는 것은 얼마인가이고, 몇 번째인가는 그 다음입니다.
const round = (item: { no: number | null; total: number | null }, unit = "회차") =>
  item.no ? ` · ${item.no}/${item.total}${unit}` : "";

export function WonCustomers() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["won-customers"],
    queryFn: () => getJSON<ListData>("/api/ui/won-customers"),
  });

  const [search, setSearch] = useState("");
  const [deal, setDeal] = useState("all");
  const [status, setStatus] = useState("all");
  const [plan, setPlan] = useState("all");
  const [type, setType] = useState("all");
  // **기본은 GTM 입니다.** 이 화면을 매일 여는 쪽이 GTM 이고, 「전체」로 두면 위 카드가
  // 세 팀을 합친 — 아무 팀의 것도 아닌 — 숫자로 시작합니다.
  const [dept, setDept] = useState("GTM");
  const [view, setView] = useState<"" | "활성" | "갱신임박">("");

  const today = data?.today ?? new Date().toISOString().slice(0, 10);
  const rate = data?.fx_rate ?? 1380;

  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    const query = search.trim().toLowerCase();
    const filtered = all.filter((row) => {
      // 담당부서는 화면 맨 위 고르개가 거릅니다 — 카드도 목록도 같은 값을 봅니다.
      if (dept !== "all" && row.department !== dept) return false;
      if (view === "활성" && row.plan_status === "사용 중단") return false;
      if (view === "갱신임박") {
        // 사용 중단은 갱신 대상이 아닙니다 — 세지도, 목록에 넣지도 않습니다.
        if (row.plan_status === "사용 중단") return false;
        const left = daysUntil(row.active?.ends_on, today);
        if (left === null || left < 0 || left > 60) return false;
      }
      if (deal !== "all" && row.active?.deal_type !== deal) return false;
      if (status !== "all" && row.plan_status !== status) return false;
      if (plan !== "all" && row.active?.plan !== plan) return false;
      if (type !== "all" && row.customer_type !== type) return false;
      if (query) {
        // **사람으로 찾습니다.** 산업 분야·국가는 뺐습니다 — 그 둘은 바로 위 필터가
        // 하는 일이고, 검색어에 섞이면 "교육" 한 단어가 교육 산업 고객 전부를 끌고
        // 옵니다. 대신 이메일과 전화번호가 들어옵니다: 클레임이나 결제 문의는 회사
        // 이름이 아니라 메일 주소로 기억되는 일이 흔합니다. 담당부서·고객 종류는
        // 남습니다 — "GTM" 이나 "Inbound" 로 찾는 사람이 반드시 있습니다.
        const hay = [row.company, row.client_id, row.contact_name, row.contact_info,
                     row.email, row.phone, row.department, row.customer_type]
          .join(" ").toLowerCase();
        if (!hay.includes(query)) return false;
      }
      return true;
    });
    // 세팅중 → 사용중 → 사용 중단, 같은 상태 안에서는 **계약 종료일이 빠른 순**.
    // 손이 먼저 가야 하는 것이 위입니다 — 가나다순은 그걸 알려주지 않습니다.
    return filtered.sort(
      (a, b) =>
        (STATUS_ORDER[a.plan_status] ?? 9) - (STATUS_ORDER[b.plan_status] ?? 9) ||
        (a.active?.ends_on ?? "9999-12-31").localeCompare(b.active?.ends_on ?? "9999-12-31"),
    );
  }, [data, search, deal, status, plan, type, dept, view, today]);

  if (!data) return <div className="won"><div className="page">불러오는 중…</div></div>;

  // **화면 위의 담당부서 고르개가 이 화면의 모집단입니다** — 카드도 목록도 같은 값을 봅니다.
  // 둘이 다른 모집단을 쓰면 "고객 12곳에 MRR 3천만원" 이 어느 팀의 숫자도 아니게 되고,
  // 그걸 눈치챌 방법이 화면에 없습니다.
  const scoped = dept === "all" ? data.rows : data.rows.filter((r) => r.department === dept);
  const deptLabel = dept === "all" ? data.options.all_departments : dept;
  const live = scoped.filter((r) => r.plan_status === "사용중").length;
  const setup = scoped.filter((r) => r.plan_status === "세팅중").length;
  const activeRows = scoped.filter((r) => r.plan_status !== "사용 중단" && r.active);
  const mrrCount = activeRows.filter((r) => r.active?.deal_type === "MRR").length;
  const pocCount = activeRows.filter((r) => r.active?.deal_type === "PoC").length;
  // 「이번달 예상 MRR」은 **서버가 계약 기간으로 계산해서**(계약 금액 ÷ 개월수) 담당부서별·
  // 통화별로 내려줍니다. 여기서 행을 걸러 더하면 그 필터가 곧 정의가 됩니다 — 실제로 플랜
  // 상태로 거르고 있었고, 그래서 세팅중 고객이 통째로 빠졌습니다. 행에는 활성 계약 하나만
  // 실려 있다는 문제도 있었습니다(고객의 다른 계약이 돌고 있어도 안 잡힘).
  const mrr = data.month_revenue?.[deptLabel] ?? {};
  const mrrKrw = mrr.KRW ?? 0;
  const mrrUsd = mrr.USD ?? 0;
  const renewing = activeRows
    .filter((r) => {
      const left = daysUntil(r.active?.ends_on, today);
      return left !== null && left >= 0 && left <= 60;
    })
    .sort((a, b) => (a.active?.ends_on ?? "").localeCompare(b.active?.ends_on ?? ""));

  const open = (clientId: number, section?: string) =>
    navigate(`/won-customers/${clientId}${section ? `#${section}` : ""}`);

  async function dismiss(id: number) {
    await postForm(`/won-customers/pending/${id}/dismiss`, {});
    await queryClient.invalidateQueries({ queryKey: ["won-customers"] });
  }

  return (
    <div className="won">
      <div className="page">
        <div className="page-head">
          <div><h1 className="page-title">수주 고객</h1></div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {/* **이 화면의 축입니다.** 아래 필터들과 달리 목록만 거르는 것이 아니라 위 카드
                둘의 모집단까지 정하므로, 그것들 사이가 아니라 제목 옆에 있습니다. */}
            <label className="sr-only" htmlFor="won-dept">담당부서</label>
            <select className="select" id="won-dept" value={dept}
                    onChange={(event) => setDept(event.target.value)}>
              <option value="all">담당부서 {data.options.all_departments}</option>
              {data.options.departments.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
            {/* 브라우저의 다운로드가 기능 전부입니다 — fetch 로 돌리면 Save As 를 다시 짜게 됩니다. */}
            <a className="btn" href="/won-customers/export.csv">CSV 내보내기</a>
            <button className="btn btn-primary" type="button"
                    onClick={() => navigate("/won-customers/new")}>
              <G name="plus" size={15} /> 수주 고객 추가
            </button>
          </div>
        </div>

        <div className="kpi-row">
          <button className={`kpi wide${view === "활성" ? " is-on" : ""}`} type="button"
                  onClick={() => setView(view === "활성" ? "" : "활성")}>
            <div className="kpi-label">
              <G name="person" /> 활성 고객 <span style={{ color: "var(--faint)" }}>({deptLabel})</span>
            </div>
            <div className="kpi-flex">
              <div className="kpi-main">
                <div className="kpi-value"><span>{live + setup}</span><span className="unit">곳</span></div>
              </div>
              <div className="kpi-group">
                <div className="cap">플랜 상태</div>
                <div className="row"><span className="dot" style={{ background: "var(--teal-600)" }} />사용중<b>{live}</b><em>곳</em></div>
                <div className="row"><span className="dot" style={{ background: "#E4A11B" }} />세팅중<b>{setup}</b><em>곳</em></div>
              </div>
              <div className="kpi-group">
                <div className="cap">수주 유형</div>
                <div className="row"><span className="dot" style={{ background: "var(--teal-600)" }} />MRR<b>{mrrCount}</b><em>곳</em></div>
                <div className="row"><span className="dot" style={{ background: "#E4A11B" }} />PoC<b>{pocCount}</b><em>곳</em></div>
              </div>
            </div>
          </button>

          <div className="kpi">
            {/* GTM 이라고 적혀 있어야 합니다. 서버가 담당부서로 거르는데 화면이 말하지
                않으면, 아래 목록을 더한 값과 안 맞을 때 어느 쪽이 틀린 건지 알 수 없습니다. */}
            <div className="kpi-label"><G name="trend" /> 이번달 예상 MRR <span style={{ color: "var(--faint)" }}>({deptLabel} · VAT 포함 · USD)</span></div>
            {/* **합계 통화가 USD 입니다.** 원화 계약을 오늘 고시가로 환산해 더합니다 —
                이 팀이 보고하는 단위가 달러이고, 그 자리에서 다시 나누던 계산을 화면이
                합니다. 계약마다의 금액은 아래 표에서 계약 통화 그대로 봅니다. */}
            <div className="kpi-value money">{money(mrrUsd + (rate ? mrrKrw / rate : 0), "USD")}</div>
            <div className="kpi-tail">
              <div className="kpi-legend">
                <i>KRW 계약 <b>{man(mrrKrw)}</b></i>
                <i>USD 계약 <b>{money(mrrUsd, "USD")}</b></i>
              </div>
              {/* 손으로 적던 칸이었습니다. 이제 오늘 고시가를 가져오므로 적을 이유가
                  없고, 적게 두면 두 사람이 다른 환율로 다른 MRR 을 봅니다. 어느 날짜의
                  값인지 같이 보여 줍니다 — 그게 숫자를 설명하는 유일한 단서입니다. */}
              <div className="fx-row">
                적용 환율 <b>{num(Math.round(rate))}</b> 원 / USD
                {/* 한국에서 낮에 보면 거의 항상 어제 날짜입니다 — ECB 가 유럽 오후에
                    하루 한 번 내기 때문입니다. 그래서 "오늘" 이라고 쓰지 않고 실제
                    고시일을 적습니다. */}
                <span style={{ color: "var(--faint)", marginLeft: 6 }}
                      title={data.fx_on
                        ? "ECB 기준환율은 유럽 시간 오후에 하루 한 번 고시되어, 한국에서는 낮에 전일자 값이 보입니다."
                        : `환율을 가져오지 못해 설정값을 씁니다. 10분 뒤 다시 시도합니다.${
                            data.fx_error ? `

마지막 실패: ${data.fx_error}` : ""}`}>
                  {data.fx_on ? `${fmt(data.fx_on)} 고시 기준` : "설정값"}
                </span>
              </div>
            </div>
          </div>

          <button className={`kpi${view === "갱신임박" ? " is-on" : ""}`} type="button"
                  onClick={() => setView(view === "갱신임박" ? "" : "갱신임박")}>
            <div className="kpi-label"><G name="clock" /> 갱신 임박 고객</div>
            <div className="kpi-value"><span>{renewing.length}</span><span className="unit">곳</span></div>
            <div className="kpi-tail">
              <div className="kpi-chips">
                {renewing.length ? renewing.slice(0, 3).map((row) => (
                  <span key={row.client_id} className="mini-chip">
                    {row.company.length > 9 ? `${row.company.slice(0, 9)}…` : row.company}{" "}
                    {dday(row.active?.ends_on, today)}
                  </span>
                )) : <span className="mini-chip calm">만료 60일 이내 없음</span>}
              </div>
            </div>
          </button>
        </div>

        <div className="board">
          <Board title="크레딧 지급 예정" count={data.boards.credit.length}
                 icon={<G name="clock" size={15} stroke="var(--teal-600)" width={1.9} />}
                 warn={data.boards.credit.some((x) => (daysUntil(x.on, today) ?? 99) <= 7)}>
            {data.boards.credit.slice(0, 4).map((item) => (
              <button key={`${item.client_id}-${item.on}`} className="board-row" type="button"
                      onClick={() => open(item.client_id, "sec-credit")}>
                <div style={{ minWidth: 0 }}>
                  <div className="board-name">{item.company}</div>
                  <div className="board-meta">{num(item.amount)} 크레딧{round(item)}</div>
                </div>
                <span className={`board-when ${dueClass(item.on, today)}`}>{dueText(item.on, today)}</span>
              </button>
            ))}
            {!data.boards.credit.length && <div className="board-empty">확인할 항목이 없습니다.</div>}
          </Board>
          <Board title="결제 예정" count={data.boards.payment.length}
                 icon={<G name="card" size={15} stroke="var(--teal-600)" width={1.9} />}
                 warn={data.boards.payment.some((x) => (daysUntil(x.on, today) ?? 99) <= 7)}>
            {data.boards.payment.slice(0, 4).map((item) => (
              <button key={`${item.client_id}-${item.on}`} className="board-row" type="button"
                      onClick={() => open(item.client_id, "sec-pay")}>
                <div style={{ minWidth: 0 }}>
                  <div className="board-name">{item.company}</div>
                  <div className="board-meta">{money(item.amount, item.currency)}{round(item, "차 분납")}</div>
                </div>
                <span className={`board-when ${dueClass(item.on, today)}`}>{dueText(item.on, today)}</span>
              </button>
            ))}
            {!data.boards.payment.length && <div className="board-empty">확인할 항목이 없습니다.</div>}
          </Board>
        </div>

        {data.pending.length > 0 && (
          <div className="intake">
            <div className="intake-head">
              <G name="inbound" size={16} stroke="#B45309" />
              <div>
                <div className="intake-title">수주 전환 대기</div>
                <div className="intake-sub">계약 정보를 입력해야 고객 목록에 등록됩니다.</div>
              </div>
              <span className="intake-count">{data.pending.length}</span>
            </div>
            <div className="intake-grid">
              {data.pending.map((item) => (
                <div key={item.id} className="intake-card">
                  <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                    <div className="intake-co">{item.company || "고객사 미확인"}</div>
                    {/* 그 고객 아래 계약이 이미 있으면 재계약입니다. 우리 장부가 아는
                        사실이라 HubSpot 의 Won type 을 읽지 않습니다. */}
                    <span className={`tag ${item.won_type === "Renewal" ? "blue" : "neutral"}`}>
                      {item.won_type}
                    </span>
                    <span className="intake-date">Won {fmt(item.won_on)}</span>
                  </div>
                  <div className={`intake-match${item.known ? "" : " new"}`}>
                    {item.client_id
                      ? `${item.company || "고객사 미확인"} (ID ${item.client_id}) → ${item.next_seq}차 계약`
                      : "Client ID 없음 → 새 고객으로 등록"}
                  </div>
                  <div className="intake-actions">
                    <button className="btn btn-sm btn-primary" type="button"
                            onClick={() => navigate(`/won-customers/new?pending=${item.id}`)}>
                      계약 정보 입력
                    </button>
                    <ActionButton className="btn btn-sm" pending="보류 중"
                                  onClick={() => dismiss(item.id)}>보류</ActionButton>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="toolbar">
          <div className="search">
            <G name="search" size={15} width={1.9} />
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)}
                   placeholder="고객사, Client ID, 담당자, 이메일, 전화번호 검색" />
          </div>
          <div className="chips">
            {["all", "MRR", "PoC"].map((key) => (
              <button key={key} type="button" className={`chip${deal === key ? " is-on" : ""}`}
                      onClick={() => setDeal(key)}>{key === "all" ? "전체" : key}</button>
            ))}
          </div>
          <div className="divider-v" />
          <Select value={status} onChange={setStatus} all="플랜 상태 전체" options={data.options.plan_statuses} />
          <Select value={plan} onChange={setPlan} all="플랜 전체" options={data.options.plans} />
          <Select value={type} onChange={setType} all="고객 종류 전체"
                  options={[...data.options.customer_types, "2025 Inbound"]} />
          <span className="result-count">{rows.length}곳 / 전체 {data.rows.length}곳</span>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th style={{ width: "17%" }}>고객사</th>
                <th style={{ width: "8%" }}>산업 분야</th>
                <th style={{ width: "7%" }}>국가</th>
                <th style={{ width: "8%" }}>플랜 상태</th>
                <th style={{ width: "9%" }}>플랜</th>
                <th style={{ width: "7%" }}>수주 유형</th>
                {/* MRR 은 계약 금액 ÷ 개월수, PoC 는 첫 결제가 이번 달일 때만 전액.
                    부서와 무관하게 모든 행에 나옵니다 — 위 카드만 GTM 으로 거릅니다. */}
                <th style={{ width: "10%" }}>이번달 매출</th>
                <th style={{ width: "12%" }}>계약 기간</th>
                <th style={{ width: "11%" }}>다음 크레딧 지급</th>
                <th style={{ width: "11%" }}>다음 결제</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <RowView key={row.client_id} row={row} rows={rows} index={index}
                         today={today} onOpen={() => open(row.client_id)} />
              ))}
            </tbody>
          </table>
          {!rows.length && (
            <div className="empty">
              <strong>조건에 맞는 고객이 없습니다</strong>검색어나 필터를 바꿔보세요.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Board({ title, count, risk, warn, icon, children }: {
  title: string; count: number; risk?: boolean; warn?: boolean;
  icon: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <div className="board-card">
      <div className="board-head">
        {icon}
        <span className="board-title">{title}</span>
        {/* 목업의 규칙: 7일 안에 걸린 것이 하나라도 있으면 건수에 색이 붙습니다 —
            숫자만으로는 "네 건" 이 급한 넷인지 다음 달 넷인지 구별되지 않습니다. */}
        <span className={`board-count${risk && count ? " risk" : warn ? " warn" : ""}`}>{count}</span>
      </div>
      <div>{children}</div>
    </div>
  );
}

function Select({ value, onChange, all, options }: {
  value: string; onChange: (value: string) => void; all: string; options: string[];
}) {
  return (
    <select className="select" value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="all">{all}</option>
      {options.map((option) => <option key={option} value={option}>{option}</option>)}
    </select>
  );
}

function RowView({ row, rows, index, today, onOpen }: {
  row: Row; rows: Row[]; index: number; today: string; onOpen: () => void;
}) {
  const contract = row.active;
  const endLeft = daysUntil(contract?.ends_on, today);
  // 상태가 바뀌는 자리에 그룹 머리를 끼웁니다 — 목업과 같은 구조입니다.
  const groupHead =
    index === 0 || rows[index - 1].plan_status !== row.plan_status ? (
      <tr className="group-row">
        <td colSpan={10}>
          {row.plan_status} · {rows.filter((r) => r.plan_status === row.plan_status).length}곳
        </td>
      </tr>
    ) : null;

  return (
    <>
      {groupHead}
      <tr className={row.plan_status === "사용 중단" ? "dim" : undefined}
          tabIndex={0} onClick={onOpen}>
        <td>
          <div className="co">
            <div className="avatar" style={{ background: avatarColor(row.client_id) }}>
              {initials(row.company)}
            </div>
            <div>
              <div className="co-name">{row.company}</div>
              <div className="co-id">ID {row.client_id} · {row.customer_type}</div>
            </div>
          </div>
        </td>
        <td className="nowrap">{row.industry || "—"}</td>
        <td className="nowrap">{row.country || "—"}</td>
        <td><StatusTag status={row.plan_status} /></td>
        <td><PlanTag plan={contract?.plan ?? null} /></td>
        <td><DealTag deal={contract?.deal_type ?? null} /></td>
        {/* 통화는 안 섞습니다. 환산은 위 카드가 오늘 고시가로 한 번만 하고, 행마다 다시
            하면 같은 화면에 서로 다른 환율이 생깁니다. 두 통화를 다 쓰는 고객은 두 줄. */}
        <td className="datecell">
          {Object.keys(row.month_revenue).length === 0
            ? <span className="muted">—</span>
            : Object.entries(row.month_revenue).map(([code, amount]) => (
                <div key={code}>{money(amount, code)}</div>
              ))}
        </td>
        <td className="datecell">
          {contract ? `${fmt(contract.starts_on)} – ${fmt(contract.ends_on)}` : "—"}
          {contract && (
            <span className={`sub ${endLeft !== null && endLeft >= 0 && endLeft <= 60 ? "due" : ""}`}>
              {/* 종료일은 바로 위 줄에 있습니다. "만료" 라는 말도 계약 기간 칸에서는
                  군더더기라, 남은 날짜만 적습니다. 지난 계약은 D+ 로 나옵니다. */}
              {dday(contract.ends_on, today)}
            </span>
          )}
        </td>
        <td className="datecell">
          {contract?.next_credit_on ? fmt(contract.next_credit_on) : <span className="muted">완료</span>}
          {contract?.next_credit_on && (
            <span className={`sub ${dueClass(contract.next_credit_on, today)}`}>
              {num(contract.next_credit_amount)} 크레딧
              {contract.next_credit_no ? ` · ${contract.next_credit_no}/${contract.next_credit_total}회차` : ""}
              {" · "}{dday(contract.next_credit_on, today)}
            </span>
          )}
        </td>
        <td className="datecell">
          {contract?.next_pay_on ? fmt(contract.next_pay_on) : <span className="muted">수금 완료</span>}
          {contract?.next_pay_on && (
            <span className={`sub ${dueClass(contract.next_pay_on, today)}`}>
              {money(contract.next_pay_amount, contract.currency)}
              {contract.next_pay_no ? ` · ${contract.next_pay_no}/${contract.next_pay_total}차 분납` : ""}
              {" · "}{dday(contract.next_pay_on, today)}
            </span>
          )}
        </td>
      </tr>
    </>
  );
}
