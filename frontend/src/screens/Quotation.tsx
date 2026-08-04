import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getJSON } from "../lib/api";
import type { Policy } from "../lib/quote";
import { PrintSheet, SheetFields, SheetSection } from "../ui/PrintSheet";

/** 견적서 — the document the operator sends after the 견적 계산기 settles the number.
 *
 * The prices are NOT retyped here: the line item is a tier from
 * src/common/quote_tiers.py, so a quote cannot quote a price the calculator would not.
 * Anything the tier table does not know — a negotiated discount, a custom line, the
 * validity period — is typed, and typed values are the only ones that can be wrong.
 */
type Line = { id: number; name: string; qty: number; unit: number; note: string };

const CURRENCIES = ["KRW", "USD"] as const;
type Currency = (typeof CURRENCIES)[number];

/** ISO for <input type="date">. Local date, not UTC — a quote is dated where it is written. */
function today() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

function plusDays(iso: string, days: number) {
  const date = new Date(`${iso}T00:00:00`);
  date.setDate(date.getDate() + days);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

export function Quotation() {
  const { data: policy } = useQuery<Policy>({
    queryKey: ["quote-policy"],
    queryFn: () => getJSON("/api/ui/quote-policy"),
    staleTime: Infinity,
  });

  const [company, setCompany] = useState("");
  const [person, setPerson] = useState("");
  const [email, setEmail] = useState("");
  const [issuedOn, setIssuedOn] = useState(today());
  const [validDays, setValidDays] = useState("14");
  const [currency, setCurrency] = useState<Currency>("KRW");
  const [owner, setOwner] = useState("");
  const [discount, setDiscount] = useState("0");
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [nextId, setNextId] = useState(1);

  const addTier = (tierId: string) => {
    const tier = policy?.tiers.find((entry) => String(entry.id) === tierId);
    if (!tier) return;
    setLines((current) => [
      ...current,
      {
        id: nextId,
        name: `Business Plan Tier ${tier.id}`,
        qty: 1,
        unit: currency === "KRW" ? tier.krw : tier.usd,
        note: `${tier.cycleMonths}개월 · 크레딧 ${tier.credits.toLocaleString()} · 더빙 ${tier.dubbingMin.toLocaleString()}분`,
      },
    ]);
    setNextId((value) => value + 1);
  };

  const addBlank = () => {
    setLines((current) => [...current, { id: nextId, name: "", qty: 1, unit: 0, note: "" }]);
    setNextId((value) => value + 1);
  };

  const patch = (id: number, change: Partial<Line>) =>
    setLines((current) => current.map((line) => (line.id === id ? { ...line, ...change } : line)));

  const totals = useMemo(() => {
    const subtotal = lines.reduce((sum, line) => sum + line.qty * line.unit, 0);
    const rate = Math.min(100, Math.max(0, parseFloat(discount) || 0));
    const off = Math.round(subtotal * (rate / 100));
    return { subtotal, rate, off, total: subtotal - off };
  }, [lines, discount]);

  const fmt = (value: number) =>
    currency === "KRW" ? `₩${Math.round(value).toLocaleString()}` : `$${value.toLocaleString()}`;

  return (
    <>
      <section className="card mb-gap no-print">
        <div className="grid grid-3" style={{ gap: 12 }}>
          <label style={{ display: "block" }}>
            <span className="field-label">고객사</span>
            <input className="input" value={company} onChange={(event) => setCompany(event.target.value)} />
          </label>
          <label style={{ display: "block" }}>
            <span className="field-label">담당자</span>
            <input className="input" value={person} onChange={(event) => setPerson(event.target.value)} />
          </label>
          <label style={{ display: "block" }}>
            <span className="field-label">이메일</span>
            <input className="input" type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
          </label>
          <label style={{ display: "block" }}>
            <span className="field-label">견적일</span>
            <input className="input" type="date" value={issuedOn} onChange={(event) => setIssuedOn(event.target.value)} />
          </label>
          <label style={{ display: "block" }}>
            <span className="field-label">유효기간 (일)</span>
            <input className="input" type="number" min={1} value={validDays}
                   onChange={(event) => setValidDays(event.target.value)} />
          </label>
          <label style={{ display: "block" }}>
            <span className="field-label">통화</span>
            <select className="select" value={currency}
                    onChange={(event) => setCurrency(event.target.value as Currency)}>
              {CURRENCIES.map((code) => <option key={code} value={code}>{code}</option>)}
            </select>
          </label>
          <label style={{ display: "block" }}>
            <span className="field-label">작성자</span>
            <input className="input" value={owner} onChange={(event) => setOwner(event.target.value)} />
          </label>
          <label style={{ display: "block" }}>
            <span className="field-label">할인율 (%)</span>
            <input className="input" type="number" min={0} max={100} value={discount}
                   onChange={(event) => setDiscount(event.target.value)} />
          </label>
        </div>

        <div className="row mb-gap" style={{ gap: 8, marginTop: 14, flexWrap: "wrap" }}>
          {/* The tier list comes from the price table, so a line item cannot carry a
              price the calculator would not have produced. */}
          <select className="select" style={{ maxWidth: 260 }} defaultValue=""
                  onChange={(event) => { addTier(event.target.value); event.currentTarget.value = ""; }}>
            <option value="" disabled>플랜 추가…</option>
            {policy?.tiers.map((tier) => (
              <option key={tier.id} value={tier.id}>
                Tier {tier.id} · {tier.cycleMonths}개월 · {currency === "KRW" ? `₩${tier.krw.toLocaleString()}` : `$${tier.usd.toLocaleString()}`}
              </option>
            ))}
          </select>
          <button type="button" className="btn btn--subtle" onClick={addBlank}>직접 입력 항목 추가</button>
        </div>

        {lines.map((line) => (
          <div key={line.id} className="row" style={{ gap: 8, marginTop: 8, alignItems: "center" }}>
            <input className="input" style={{ flex: 2 }} placeholder="항목" value={line.name}
                   onChange={(event) => patch(line.id, { name: event.target.value })} />
            <input className="input" style={{ flex: 2 }} placeholder="설명" value={line.note}
                   onChange={(event) => patch(line.id, { note: event.target.value })} />
            <input className="input" style={{ width: 80 }} type="number" min={0} value={line.qty}
                   onChange={(event) => patch(line.id, { qty: Number(event.target.value) })} />
            <input className="input" style={{ width: 130 }} type="number" min={0} value={line.unit}
                   onChange={(event) => patch(line.id, { unit: Number(event.target.value) })} />
            <button type="button" className="btn btn--ghost btn--sm"
                    onClick={() => setLines((current) => current.filter((entry) => entry.id !== line.id))}>
              삭제
            </button>
          </div>
        ))}

        <label style={{ display: "block", marginTop: 14 }}>
          <span className="field-label">비고</span>
          <textarea className="input" rows={3} value={notes} onChange={(event) => setNotes(event.target.value)} />
        </label>
      </section>

      <PrintSheet
        title="견 적 서"
        meta={<>발행일 {issuedOn} · 유효기간 {plusDays(issuedOn, Number(validDays) || 0)}까지</>}
      >
        <SheetSection title="공급받는 자">
          <SheetFields rows={[["회사명", company], ["담당자", person], ["이메일", email], ["작성자", owner]]} />
        </SheetSection>

        <SheetSection title="견적 내역">
          <table className="sheet__table">
            <thead>
              <tr>
                <th>항목</th><th>설명</th>
                <th className="num">수량</th><th className="num">단가</th><th className="num">금액</th>
              </tr>
            </thead>
            <tbody>
              {lines.length === 0 ? (
                <tr><td colSpan={5} style={{ color: "#888" }}>항목을 추가하세요.</td></tr>
              ) : (
                lines.map((line) => (
                  <tr key={line.id}>
                    <td>{line.name || "-"}</td>
                    <td style={{ color: "#666" }}>{line.note}</td>
                    <td className="num">{line.qty}</td>
                    <td className="num">{fmt(line.unit)}</td>
                    <td className="num">{fmt(line.qty * line.unit)}</td>
                  </tr>
                ))
              )}
              {totals.rate > 0 && (
                <tr>
                  <td colSpan={4} className="num">할인 {totals.rate}%</td>
                  <td className="num">-{fmt(totals.off)}</td>
                </tr>
              )}
              <tr className="sheet__total">
                <td colSpan={4} className="num">합계 (VAT 별도)</td>
                <td className="num">{fmt(totals.total)}</td>
              </tr>
            </tbody>
          </table>
        </SheetSection>

        {notes.trim() && (
          <SheetSection title="비고">
            <div className="sheet__body">{notes}</div>
          </SheetSection>
        )}

        <div className="sheet__sign">
          <div>공급자 (주)이스트소프트</div>
          <div>공급받는 자 {company}</div>
        </div>
      </PrintSheet>
    </>
  );
}
