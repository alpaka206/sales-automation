import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router-dom";
import { getJSON } from "../../lib/api";
import { SubmitButton, useAction } from "../../ui/ActionButton";
import { type ListData } from "./shared";

/** 수주 고객 추가 — 목업의 1단계 모달을 화면으로.
 *
 * 두 갈래입니다. 어느 쪽이든 끝은 "계약을 등록한다" 하나라서, 고른 뒤 가는 곳이
 * 같습니다. Won 티켓은 목록의 「수주 전환 대기」에서 이미 대상이 정해지므로 이 화면을
 * 거치지 않고 계약 폼으로 바로 갑니다.
 *
 *   신규 고객 등록  — 고객 종류를 고르면 그 번호대의 다음 ID 가 발급됩니다.
 *   기존 고객에 계약 추가 — Client ID 는 그대로, 차수만 올라갑니다.
 *
 * **어느 쪽이든 이 화면은 아무것도 저장하지 않습니다.** 1단계에서 받은 칸을 계약 폼으로
 * 넘기고, 고객은 그 폼을 저장할 때 계약과 함께 만들어집니다 — 여기서 먼저 만들면 계약을
 * 채우지 않고 나갔을 때 계약 0건짜리 고객이 남고, 그 고객이 워크북에 「세팅중」으로 실려
 * 나갑니다(2026-08-25).
 *
 * 모달이 아니라 화면인 이유: 이 폼은 스크롤이 필요할 만큼 길고, 모달 안에서 스크롤하면
 * 세로 640px 짜리 노트북에서 버튼이 화면 밖으로 나갑니다.
 */
const TYPE_NOTE: Record<string, string> = {
  "GTM Inbound": "HubSpot 티켓과 연동되는 인바운드 문의 고객",
  "GTM Outbound": "GTM 팀이 직접 발굴한 고객",
  "Interactive": "Interactive 팀 고객",
  "AX": "AX 팀 고객",
};
const TYPE_BASE: Record<string, number> = {
  "GTM Inbound": 1000, "GTM Outbound": 2000, "Interactive": 3000, "AX": 4000,
};

export function WonNew() {
  const navigate = useNavigate();
  const { data } = useQuery({
    queryKey: ["won-customers"],
    queryFn: () => getJSON<ListData>("/api/ui/won-customers"),
  });

  // 계약 폼에서 되돌아온 값. 1단계는 아무것도 저장하지 않으므로, 계약을 취소하고 돌아왔을
  // 때 이것이 없으면 여덟 칸을 처음부터 다시 칩니다. `form` 은 「직접 입력」 산업 분야처럼
  // 서버로 보내는 값만으로는 복원되지 않는 칸입니다.
  const back = useLocation().state as
    | { customer?: Record<string, string>; form?: { industry: string; customIndustry: string } }
    | null;
  const was = back?.customer;

  const [mode, setMode] = useState<"new" | "existing">("new");
  const [type, setType] = useState(was?.customer_type ?? "");
  const [pickedClient, setPickedClient] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [note, setNote] = useState<string | null>(null);

  // 신규 고객 칸
  const [company, setCompany] = useState(was?.company ?? "");
  const [industry, setIndustry] = useState(back?.form?.industry ?? "");
  const [customIndustry, setCustomIndustry] = useState(back?.form?.customIndustry ?? "");
  const [country, setCountry] = useState(was?.country ?? "대한민국");
  const [wonOn, setWonOn] = useState(was?.first_won_on ?? "");
  const [owner, setOwner] = useState(was?.owner ?? "");

  const [save, saving] = useAction(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!data) return;
    setNote(null);
    try {
      if (mode === "existing") {
        if (!pickedClient) {
          setNote("계약을 추가할 기존 고객을 골라 주세요.");
          return;
        }
        navigate(`/won-customers/${pickedClient}/contracts/new`);
        return;
      }
      if (!type) { setNote("고객 종류를 골라 주세요."); return; }
      // 저장하지 않고 넘깁니다 — 만드는 것은 계약 폼입니다.
      navigate("/won-customers/new/contract", {
        state: {
          customer: {
            customer_type: type,
            company,
            industry: industry === "__custom" ? customIndustry : industry,
            country,
            first_won_on: wonOn,
            owner,
          },
          // 계약을 취소하고 돌아왔을 때 이 화면을 그대로 되살리기 위한 것입니다 —
          // 「직접 입력」인지 고른 값인지는 위 `customer` 만으로는 알 수 없습니다.
          form: { industry, customIndustry },
        },
      });
    } catch (error) {
      setNote(error instanceof Error ? error.message : String(error));
    }
  });

  if (!data) return <div className="won"><div className="page">불러오는 중…</div></div>;

  // 그 번호대의 다음 ID — 화면에 미리 보여 줍니다. 실제 발급은 저장할 때 서버가 합니다.
  const nextId = (base: number) => {
    const used = data.rows
      .map((row) => row.client_id)
      .filter((id) => id >= base && id < base + 1000);
    return Math.max(base, ...used) + 1;
  };
  // 이미 있는 고객과 이름이 겹치는지. 서버는 **완전 일치**만 막으므로(client_ids 의
  // `company_key`) 「서울대학교」와 「서울대학교 산학협력단」은 그냥 통과합니다 — 실제로
  // 그렇게 한 회사에 번호가 둘 생겼습니다. 사람만 판단할 수 있는 일이라 판단 재료를 줍니다.
  const nameKey = (value: string) => value.toLowerCase().replace(/[^a-z0-9가-힣]/g, "");
  const typed = nameKey(company);
  const similar = typed.length >= 2
    ? data.rows.filter((row) => {
        const other = nameKey(row.company);
        return Boolean(other) && (other.includes(typed) || typed.includes(other));
      })
    : [];

  const matches = data.rows.filter((row) => {
    const q = query.trim().toLowerCase();
    return !q || `${row.company} ${row.client_id}`.toLowerCase().includes(q);
  });

  return (
    <div className="won">
      <div className="page">
        <div className="page-head">
          <div>
            <h1 className="page-title">수주 고객 추가</h1>
            <p className="page-sub">
              {mode === "new" ? "고객 종류를 선택하면 규칙에 맞는 Client ID가 자동 생성됩니다."
               : "선택한 고객의 Client ID는 유지되고, 새 계약이 최신 계약으로 등록됩니다."}
            </p>
          </div>
          <button className="btn" type="button" onClick={() => navigate("/won-customers")}>← 목록</button>
        </div>

        <form className="sec" onSubmit={save}>
          <div className="seg customer-paths">
            <button type="button" className={`seg-btn${mode === "new" ? " is-on" : ""}`}
                    onClick={() => setMode("new")}>신규 고객 등록</button>
            <button type="button" className={`seg-btn${mode === "existing" ? " is-on" : ""}`}
                    onClick={() => setMode("existing")}>기존 고객에 계약 추가</button>
          </div>

          {mode === "new" && (
            <>
              <div className="type-grid">
                {data.options.customer_types.map((key) => (
                  <button key={key} type="button"
                          className={`type-card${type === key ? " is-on" : ""}`}
                          onClick={() => setType(key)}>
                    <div className="type-name">{key}</div>
                    <div className="type-range">
                      {TYPE_BASE[key]}번대 · 다음 ID {nextId(TYPE_BASE[key])}
                    </div>
                    <div className="type-note">{TYPE_NOTE[key]}</div>
                  </button>
                ))}
              </div>
              <div className="note-box">
                9000번대는 2025년 Inbound 고객 전용 번호대로, 신규 생성에는 사용하지 않습니다.
              </div>
              <div className="form-sec">고객 정보</div>
              <div className="form-grid3">
                <Field label="고객사" required>
                  <input className="inp" value={company} onChange={(e) => setCompany(e.target.value)} required />
                  {similar.length > 0 && (
                    <div className="note-box" style={{ marginTop: 6 }} role="status">
                      이름이 비슷한 고객이 이미 있습니다. 같은 회사라면 새 번호를 내지 말고
                      그 고객에 계약을 추가하세요.
                      {/* 검색어를 넣지 않고 **그 고객을 바로 고릅니다.** 검색은 원문
                          부분일치라 「서울대학교 산학협력단」으로 「서울대학교」를 못 찾습니다 —
                          경고가 방금 이름을 대 준 고객이 목록에서 사라집니다. */}
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6 }}>
                        {similar.map((row) => (
                          <button key={row.client_id} type="button" className="btn btn-sm"
                                  onClick={() => {
                                    setMode("existing");
                                    setPickedClient(row.client_id);
                                    setQuery(row.company);
                                  }}>
                            {row.company} (ID {row.client_id})
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </Field>
                <Field label="산업 분야" required>
                  <select className="inp" value={industry} onChange={(e) => setIndustry(e.target.value)}>
                    <option value="">선택</option>
                    {data.options.industries.map((item) => <option key={item}>{item}</option>)}
                    <option value="__custom">직접 입력</option>
                  </select>
                  {industry === "__custom" && (
                    <input className="inp" style={{ marginTop: 6 }} placeholder="산업 분야"
                           value={customIndustry} onChange={(e) => setCustomIndustry(e.target.value)} />
                  )}
                </Field>
                <Field label="국가" required>
                  <input className="inp" value={country} onChange={(e) => setCountry(e.target.value)} required />
                </Field>
                <Field label="최초 수주일">
                  <input className="inp" type="date" value={wonOn} onChange={(e) => setWonOn(e.target.value)} />
                </Field>
                <Field label="담당">
                  <input className="inp" value={owner} onChange={(e) => setOwner(e.target.value)}
                         placeholder="비우면 로그인한 사람" />
                </Field>
              </div>
            </>
          )}

          {mode === "existing" && (
            <>
              <div className="search" style={{ maxWidth: "none", marginBottom: 11 }}>
                <input type="text" placeholder="고객사, Client ID 검색"
                       value={query} onChange={(e) => setQuery(e.target.value)} />
              </div>
              <div className="pick-list">
                {matches.map((row) => (
                  <button key={row.client_id} type="button"
                          className={`pick-row${pickedClient === row.client_id ? " is-on" : ""}`}
                          onClick={() => setPickedClient(row.client_id)}>
                    <div>
                      <div className="pick-name">{row.company}</div>
                      <div className="pick-meta">
                        ID {row.client_id} · {row.customer_type} · 계약 {row.contract_count ?? 0}건
                      </div>
                    </div>
                  </button>
                ))}
                {!matches.length && <div className="board-empty">찾는 고객이 없습니다.</div>}
              </div>
            </>
          )}

          <div className="modal-foot" style={{ marginTop: 18 }}>
            <button className="btn" type="button" onClick={() => navigate("/won-customers")}>취소</button>
            <SubmitButton busy={saving} pending="처리 중">다음</SubmitButton>
          </div>
          {note && <div className="note-box" role="status">{note}</div>}
        </form>
      </div>
    </div>
  );
}

export function Field({ label, required, children }: {
  label: string; required?: boolean; children: React.ReactNode;
}) {
  return (
    <div>
      <label className="form-label">{label}{required && <span className="req"> *</span>}</label>
      {children}
    </div>
  );
}
