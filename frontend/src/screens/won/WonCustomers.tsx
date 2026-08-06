import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { getJSON, postForm } from "../../lib/api";
import { ActionButton } from "../../ui/ActionButton";
import {
  type ListData, type Row,
  STATUS_ORDER, daysUntil, dday, dueClass, dueText, fmt, initials, money, n, num,
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

// "1,800 크레딧" 만 보이면 그게 마지막 회차인지 열두 번 중 두 번째인지 알 수 없습니다 —
// 열어 봐야 판단이 되는 것을 목록에서 끝내려고 회차를 같이 적습니다. 금액 **뒤**입니다:
// 훑을 때 먼저 읽는 것은 얼마인가이고, 몇 번째인가는 그 다음입니다.
const round = (item: { no: number | null; total: number | null }) =>
  item.no ? ` · ${item.no}/${item.total}회차` : "";

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
  const [dept, setDept] = useState("all");
  const [view, setView] = useState<"" | "활성" | "갱신임박">("");

  const today = data?.today ?? new Date().toISOString().slice(0, 10);
  const rate = data?.fx_rate ?? 1380;

  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    const query = search.trim().toLowerCase();
    const filtered = all.filter((row) => {
      if (view === "활성" && row.plan_status === "사용 중단") return false;
      if (view === "갱신임박") {
        const left = daysUntil(row.active?.ends_on, today);
        if (left === null || left < 0 || left > 60) return false;
      }
      if (deal !== "all" && row.active?.deal_type !== deal) return false;
      if (status !== "all" && row.plan_status !== status) return false;
      if (plan !== "all" && row.active?.plan !== plan) return false;
      if (type !== "all" && row.customer_type !== type) return false;
      if (dept !== "all" && row.department !== dept) return false;
      if (query) {
        const hay = [row.company, row.client_id, row.industry, row.country]
          .join(" ").toLowerCase();
        if (!hay.includes(query)) return false;
      }
      return true;
    });
    // 세팅중 → 사용중 → 사용 중단. 손이 가야 하는 것이 위입니다.
    return filtered.sort(
      (a, b) =>
        (STATUS_ORDER[a.plan_status] ?? 9) - (STATUS_ORDER[b.plan_status] ?? 9) ||
        a.company.localeCompare(b.company),
    );
  }, [data, search, deal, status, plan, type, dept, view, today]);

  if (!data) return <div className="won"><div className="page">불러오는 중…</div></div>;

  const live = data.rows.filter((r) => r.plan_status === "사용중").length;
  const setup = data.rows.filter((r) => r.plan_status === "세팅중").length;
  const activeRows = data.rows.filter((r) => r.plan_status !== "사용 중단" && r.active);
  const mrrCount = activeRows.filter((r) => r.active?.deal_type === "MRR").length;
  const pocCount = activeRows.filter((r) => r.active?.deal_type === "PoC").length;
  // KRW·USD 계약을 각 통화 그대로 더한 뒤, 원화 환산만 카드 숫자에 씁니다.
  const mrrKrw = activeRows
    .filter((r) => r.active?.currency === "KRW")
    .reduce((sum, r) => sum + n(r.active?.monthly_revenue), 0);
  const mrrUsd = activeRows
    .filter((r) => r.active?.currency === "USD")
    .reduce((sum, r) => sum + n(r.active?.monthly_revenue), 0);
  const renewing = data.rows.filter((r) => {
    const left = daysUntil(r.active?.ends_on, today);
    return left !== null && left >= 0 && left <= 60;
  });

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
          <div style={{ display: "flex", gap: 8 }}>
            {/* 브라우저의 다운로드가 기능 전부입니다 — fetch 로 돌리면 Save As 를 다시 짜게 됩니다. */}
            <a className="btn" href="/won-customers/export.csv">CSV 내보내기</a>
            <button className="btn btn-primary" type="button"
                    onClick={() => navigate("/won-customers/new")}>+ 수주 고객 추가</button>
          </div>
        </div>

        <div className="kpi-row">
          <button className={`kpi wide${view === "활성" ? " is-on" : ""}`} type="button"
                  onClick={() => setView(view === "활성" ? "" : "활성")}>
            <div className="kpi-label">활성 고객</div>
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
            <div className="kpi-label">이번달 예상 MRR <span style={{ color: "var(--faint)" }}>(VAT 포함)</span></div>
            <div className="kpi-value money">{money(mrrKrw + mrrUsd * rate)}</div>
            <div className="kpi-tail">
              <div className="kpi-legend">
                <i>KRW</i><b>{money(mrrKrw)}</b>
                <i>USD</i><b>{money(mrrUsd, "USD")}</b>
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
                        : "환율을 가져오지 못해 설정값을 씁니다."}>
                  {data.fx_on ? `${fmt(data.fx_on)} 고시 기준` : "설정값"}
                </span>
              </div>
            </div>
          </div>

          <button className={`kpi${view === "갱신임박" ? " is-on" : ""}`} type="button"
                  onClick={() => setView(view === "갱신임박" ? "" : "갱신임박")}>
            <div className="kpi-label">갱신 임박 고객</div>
            <div className="kpi-value"><span>{renewing.length}</span><span className="unit">곳</span></div>
            <div className="kpi-tail">
              <div className="kpi-chips">
                {renewing.slice(0, 3).map((row) => (
                  <span key={row.client_id} className="mini-chip">{row.company}</span>
                ))}
              </div>
            </div>
          </button>
        </div>

        <div className="board">
          <Board title="크레딧 지급 예정" count={data.boards.credit.length}>
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
          <Board title="결제 예정" count={data.boards.payment.length}>
            {data.boards.payment.slice(0, 4).map((item) => (
              <button key={`${item.client_id}-${item.on}`} className="board-row" type="button"
                      onClick={() => open(item.client_id, "sec-pay")}>
                <div style={{ minWidth: 0 }}>
                  <div className="board-name">{item.company}</div>
                  <div className="board-meta">{money(item.amount, item.currency)}{round(item)}</div>
                </div>
                <span className={`board-when ${dueClass(item.on, today)}`}>{dueText(item.on, today)}</span>
              </button>
            ))}
            {!data.boards.payment.length && <div className="board-empty">확인할 항목이 없습니다.</div>}
          </Board>
          <Board title="미처리 클레임 · 히스토리" count={data.boards.claim.length} risk>
            {data.boards.claim.slice(0, 4).map((item, index) => (
              <button key={index} className="board-row" type="button"
                      onClick={() => open(item.client_id, "sec-care")}>
                <div style={{ minWidth: 0 }}>
                  <div className="board-name">{item.company}</div>
                  <div className="board-meta">{item.kind} · {item.progress}</div>
                </div>
                <span className="board-when">{fmt(item.on)}</span>
              </button>
            ))}
            {!data.boards.claim.length && <div className="board-empty">확인할 항목이 없습니다.</div>}
          </Board>
        </div>

        {data.pending.length > 0 && (
          <div className="intake">
            <div className="intake-head">
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
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)}
                   placeholder="고객사, Client ID, 산업, 국가 검색" />
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
          <Select value={dept} onChange={setDept} all="담당부서 전체" options={data.options.departments} />
          <span className="result-count">{rows.length}곳</span>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th style={{ width: "19%" }}>고객사</th>
                <th style={{ width: "9%" }}>산업 분야</th>
                <th style={{ width: "7%" }}>국가</th>
                <th style={{ width: "8%" }}>플랜 상태</th>
                <th style={{ width: "11%" }}>플랜</th>
                <th style={{ width: "7%" }}>수주 유형</th>
                <th style={{ width: "15%" }}>계약 기간</th>
                <th style={{ width: "12%" }}>다음 크레딧 지급</th>
                <th style={{ width: "12%" }}>다음 결제</th>
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

function Board({ title, count, risk, children }: {
  title: string; count: number; risk?: boolean; children: React.ReactNode;
}) {
  return (
    <div className="board-card">
      <div className="board-head">
        <span className="board-title">{title}</span>
        <span className={`board-count${risk && count ? " risk" : ""}`}>{count}</span>
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
        <td colSpan={9}>
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
              <div className="co-name">
                {row.company}
                {row.setup_count > 0 && (
                  <span className="tag st-setup" style={{ marginLeft: 6 }}>
                    세팅중 계약 {row.setup_count}
                  </span>
                )}
              </div>
              <div className="co-id">ID {row.client_id} · {row.customer_type}</div>
            </div>
          </div>
        </td>
        <td className="nowrap">{row.industry || "—"}</td>
        <td className="nowrap">{row.country || "—"}</td>
        <td><StatusTag status={row.plan_status} /></td>
        <td><PlanTag plan={contract?.plan ?? null} /></td>
        <td><DealTag deal={contract?.deal_type ?? null} /></td>
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
          {contract?.next_pay_on ? fmt(contract.next_pay_on) : <span className="muted">완료</span>}
          {contract?.next_pay_on && (
            <span className={`sub ${dueClass(contract.next_pay_on, today)}`}>
              {money(contract.next_pay_amount, contract.currency)}
              {contract.next_pay_no ? ` · ${contract.next_pay_no}/${contract.next_pay_total}회차` : ""}
              {" · "}{dday(contract.next_pay_on, today)}
            </span>
          )}
        </td>
      </tr>
    </>
  );
}
