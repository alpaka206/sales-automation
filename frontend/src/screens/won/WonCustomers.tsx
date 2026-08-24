import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { getJSON, postForm } from "../../lib/api";
import { ActionButton } from "../../ui/ActionButton";
import { MonthlyBars } from "./MonthlyBars";
import {
  type ListData, type Row,
  STATUS_ORDER, amount, daysUntil, dday, dueClass, dueText, fmt, initials, money, num,
  planTone, scaleFor, statusTone, tickLabel,
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
  // 어느 지표를, 어느 통화로. **환산은 서버가 이미 두 통화로 해 두었습니다** — 화면이
  // 다시 환산하면 같은 숫자가 화면마다 달라집니다.

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
  const untypedCount = activeRows.length - mrrCount - pocCount;
  // 「이번달 예상 MRR」은 **서버가 계약 기간으로 계산해서**(계약 금액 ÷ 개월수) 담당부서별·
  // 통화별로 내려줍니다. 여기서 행을 걸러 더하면 그 필터가 곧 정의가 됩니다 — 실제로 플랜
  // 상태로 거르고 있었고, 그래서 세팅중 고객이 통째로 빠졌습니다. 행에는 활성 계약 하나만
  // 실려 있다는 문제도 있었습니다(고객의 다른 계약이 돌고 있어도 안 잡힘).
  const months = data.months ?? [];
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
            <div className="kpi-value"><span>{live + setup}</span><span className="unit">곳</span></div>
            {/* 두 지표는 구성비를 읽는 값이라 원그래프로 보여 줍니다. 수주 유형이 비어 있는
                활성 계약도 모집단에서 사라지지 않도록 '미등록' 조각으로 드러냅니다. */}
            <div className="kpi-splits">
              <Donut cap="플랜 상태" slices={[
                { label: "사용중", n: live, color: "var(--teal-600)" },
                { label: "세팅중", n: setup, color: "#E4A11B" },
              ]} />
              <Donut cap="수주 유형" slices={[
                { label: "MRR", n: mrrCount, color: "var(--teal-600)" },
                { label: "PoC", n: pocCount, color: "#E4A11B" },
                { label: "미등록", n: untypedCount, color: "#AAB7B4" },
              ]} />
            </div>
          </button>

          {/* 지표 둘을 **한 카드 안 토글**로 두었습니다. 하나를 보고 있으면 다른 하나가
              어떤 모양인지 알 수 없었고, 두 지표가 갈리는 순간이 곧 그 계약을 봐야 할
              때라 그것을 보려고 버튼을 누르고 있어야 했습니다. 이제 둘 다 그립니다
              (2026-08-19, 운영자 지시). y축은 카드마다 따로입니다 — 인식 매출은 고르게
              깔리고 현금은 한 달에 몰려서, 축을 합치면 MRR 쪽이 바닥에 눌립니다. 눈금에
              단위(만·억)가 붙어 있어 두 축을 헷갈릴 일은 없습니다. */}
          <MetricCard title="월별 MRR" note={`${deptLabel} · VAT 포함`}
                      series={data.mrr_months?.[deptLabel] ?? {}}
                      months={months} now={data.month} />
          <MetricCard title="월 매출" note={`${deptLabel} · 입금 기준`}
                      series={data.cash_months?.[deptLabel] ?? {}}
                      months={months} now={data.month} />
        </div>

        {/* 환율 한 줄은 **두 카드 바깥에 한 번**입니다. 카드마다 넣으면 같은 문장이 화면에
            둘이 되고, 그러면 둘이 다른 값일 수 있는 것처럼 읽힙니다 — 실제로는 서버가
            계약마다 그 계약의 환율로 한 번 환산한 결과이고, 이 줄은 그중 오늘 고시가를
            쓴 곳(예상 MRR 카드의 큰 숫자)이 어느 날 값인지를 말합니다.

            손으로 적던 칸이었습니다. 이제 오늘 고시가를 가져오므로 적을 이유가 없고,
            적게 두면 두 사람이 다른 환율로 다른 MRR 을 봅니다. */}
        <div className="kpi-fx">
          <div className="fx-row">
            적용 환율 <b>{num(Math.round(rate))}</b> 원 / USD
            {/* 한국에서 낮에 보면 거의 항상 어제 날짜입니다 — ECB 가 유럽 오후에 하루 한 번
                내기 때문입니다. 그래서 "오늘" 이라고 쓰지 않고 실제 고시일을 적습니다. */}
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
          {/* 보드 줄은 처음부터 3열이었고 한 칸이 비어 있었습니다. 갱신 임박은 크레딧·결제
              예정과 같은 성격입니다 — 날짜가 다가와서 손이 가야 하는 목록. 같은 줄에 두면
              「이번 주에 볼 것」이 한자리에 모입니다.

              제목이 곧 필터입니다: 누르면 아래 목록이 갱신 임박만 남습니다. 예전 KPI 카드가
              그 일을 했는데, 카드를 옮기면서 그 기능까지 사라지면 안 됩니다. 줄을 누르면
              그 고객으로 들어갑니다 — 옆의 두 보드와 같습니다. */}
          <Board title="갱신 임박 고객" count={renewing.length}
                 icon={<G name="clock" size={15} stroke="var(--teal-600)" width={1.9} />}
                 warn={renewing.some((r) => (daysUntil(r.active?.ends_on, today) ?? 99) <= 7)}
                 on={view === "갱신임박"}
                 onToggle={() => setView(view === "갱신임박" ? "" : "갱신임박")}>
            {renewing.slice(0, 4).map((row) => (
              <button key={row.client_id} className="board-row" type="button"
                      onClick={() => open(row.client_id, "sec-contract")}>
                <div style={{ minWidth: 0 }}>
                  <div className="board-name">{row.company}</div>
                  <div className="board-meta">{fmt(row.active?.ends_on)} 만료</div>
                </div>
                <span className={`board-when ${dueClass(row.active?.ends_on, today)}`}>
                  {dday(row.active?.ends_on, today)}
                </span>
              </button>
            ))}
            {!renewing.length && <div className="board-empty">만료 60일 이내 없습니다.</div>}
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
                <th style={{ width: "10%" }} className="moneycell">이번달 매출</th>
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

function Board({ title, count, risk, warn, icon, children, on, onToggle }: {
  title: string; count: number; risk?: boolean; warn?: boolean;
  icon: React.ReactNode; children: React.ReactNode;
  /** 머리를 누르면 아래 목록을 그 묶음으로 거릅니다. 안 주면 머리는 그냥 제목입니다 —
   *  크레딧·결제 예정은 거를 목록이 아니라서(그 둘은 회차이지 고객이 아닙니다). */
  on?: boolean; onToggle?: () => void;
}) {
  const head = (
    <>
      {icon}
      <span className="board-title">{title}</span>
      {/* 목업의 규칙: 7일 안에 걸린 것이 하나라도 있으면 건수에 색이 붙습니다 —
          숫자만으로는 "네 건" 이 급한 넷인지 다음 달 넷인지 구별되지 않습니다. */}
      <span className={`board-count${risk && count ? " risk" : warn ? " warn" : ""}`}>{count}</span>
    </>
  );
  return (
    <div className={`board-card${on ? " is-on" : ""}`}>
      {onToggle
        ? <button type="button" className="board-head board-head--toggle"
                  aria-pressed={on} onClick={onToggle}
                  title={on ? "필터 해제" : "이 목록만 보기"}>{head}</button>
        : <div className="board-head">{head}</div>}
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
        <td className="datecell moneycell">
          {Object.keys(row.month_revenue).length === 0
            ? <span className="muted">—</span>
            : Object.entries(row.month_revenue).map(([code, value]) => (
                <div key={code} title={money(value, code)}>{amount(value, code)}</div>
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


function Donut({ cap, slices }: {
  cap: string;
  slices: { label: string; n: number; color: string }[];
}) {
  const total = slices.reduce((sum, slice) => sum + slice.n, 0);
  let cursor = 0;
  const stops = slices.map((slice) => {
    const start = cursor;
    cursor += total ? (slice.n / total) * 100 : 0;
    return `${slice.color} ${start}% ${cursor}%`;
  });
  const background = total ? `conic-gradient(${stops.join(", ")})` : "var(--line-soft)";
  const aria = slices.map((slice) => `${slice.label} ${slice.n}곳`).join(", ");
  return (
    <div className="kpi-donut">
      <span className="cap">{cap}</span>
      <div className="kpi-donut__body">
        <div className="kpi-donut__chart" style={{ background }} role="img" aria-label={aria}>
          <span><b>{total}</b><small>곳</small></span>
        </div>
        <span className="kpi-donut__legend">
          {slices.map((slice) => (
            <i key={slice.label}>
              <span className="dot" style={{ background: slice.color }} />
              {slice.label}<b>{slice.n}</b>
              <small>{total ? Math.round((slice.n / total) * 100) : 0}%</small>
            </i>
          ))}
        </span>
      </div>
    </div>
  );
}


/** 월별 막대 카드 하나 — 지표 한 개. 예전에는 카드 하나에 지표 둘을 토글로 담았습니다.
 *
 *  y축(단위 접기)은 **이 카드 안에서** 정해집니다. 두 카드가 축을 나눠 쓰면, 플랜 기간에
 *  고르게 깔리는 인식 매출이 한 달에 몰리는 현금 옆에서 바닥에 눌려 아무 모양도 안 남습니다.
 *  눈금 글자에 단위(만·억)가 붙으므로 두 축을 같은 자로 착각할 일은 없습니다.
 *
 *  통화도 카드마다 따로 고릅니다. 축이 이미 따로라 두 카드는 같은 자로 재는 그림이
 *  아니고, 어느 단위인지는 눈금과 큰 숫자가 각자 말합니다.
 */
function MetricCard({ title, note, series, months, now }: {
  title: string;
  note: string;
  series: Record<string, Record<string, number>>;
  months: string[];
  now: string;
}) {
  // 카드마다 따로입니다 (2026-08-19, 운영자 지시). 한동안 한 값을 나눠 썼는데 — 나란히
  // 두는 이유가 비교라 단위도 같아야 한다고 봤습니다 — 실제로는 인식 매출을 달러로,
  // 입금을 원화로 보고 싶은 때가 있습니다. 두 카드는 y축도 이미 따로라 같은 자로 재는
  // 그림이 아니었고, 단위는 눈금과 큰 숫자에 그때그때 적혀 있습니다.
  const [unit, setUnit] = useState<"KRW" | "USD">("USD");
  const at = (month: string) => series[month]?.[unit] ?? 0;
  // **단위는 그 구간(6개월) 최댓값 하나로 정합니다.** 눈금마다 따로 접으면 50만 옆에
  // 1,000만이 서고, 그러면 두 눈금을 비교하려고 자릿수를 세어야 합니다.
  const scale = scaleFor(Math.max(...months.map((month) => Math.abs(at(month))), 0), unit);
  return (
    <div className="kpi">
      <div className="kpi-head">
        {/* GTM 이라고 적혀 있어야 합니다. 서버가 담당부서로 거르는데 화면이 말하지 않으면,
            아래 목록을 더한 값과 안 맞을 때 어느 쪽이 틀린 건지 알 수 없습니다. */}
        <div className="kpi-label">
          <G name="trend" /> {title}
          <span style={{ color: "var(--faint)" }}> ({note})</span>
        </div>
        <div className="seg">
          <button type="button" className={unit === "KRW" ? "on" : ""}
                  onClick={() => setUnit("KRW")}>KRW</button>
          <button type="button" className={unit === "USD" ? "on" : ""}
                  onClick={() => setUnit("USD")}>USD</button>
        </div>
      </div>
      {/* **이번 달** 값입니다. 안 적어 두면 이번 달에 잡힌 것이 없을 때(월 매출은 결제
          회차가 있는 달에만 잡히므로 흔합니다) 큰 「0」 이 「매출 없음」으로 읽힙니다 —
          정작 옆 막대에는 지난 달들이 서 있는데. */}
      <div className="kpi-value money">
        {amount(at(now), unit, scale)}<span className="unit">이번 달</span>
      </div>
      <MonthlyBars months={months} valueAt={at} now={now}
                   format={(value) => amount(value, unit, scale)}
                   formatTick={(value) => tickLabel(value, scale)}
                   negativeNote="중도 해지 정산" />
    </div>
  );
}
