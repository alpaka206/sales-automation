import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { kst } from "../lib/format";
import { PrintSheet, SheetFields, SheetSection } from "../ui/PrintSheet";

/** 계약서 — the contract's own facts, laid out to print.
 *
 * Deliberately NOT a legal template. Every field here is one the team already recorded
 * against the contract (고객 상세 → 계약), so the document cannot disagree with the
 * record it came from; the terms are typed in, because clauses this app invented would
 * read as agreed text nobody agreed to. Fill 계약 조항 from the reviewed wording.
 */
type Row = {
  id: number;
  contact_id: number;
  company: string;
  name: string;
  email: string | null;
  status: string;
  plan: string | null;
  amount: string | number | null;
  currency: string;
  payment_method: string | null;
  contract_date: string | null;
  payment_due_at: string | null;
  paid_at: string | null;
  expires_at: string | null;
  unit_price: string | null;
  language_pairs: string[];
  sheet_client_id: number | null;
};
type Data = { rows: Row[]; status_options: { key: string; label: string }[] };

export function ContractDoc() {
  const [params, setParams] = useSearchParams();
  const selected = params.get("contract") ?? "";
  const [terms, setTerms] = useState("");
  const [special, setSpecial] = useState("");

  const { data } = useQuery({
    queryKey: ["contracts", "", ""],
    queryFn: () => getJSON<Data>("/api/ui/contracts?status=&q="),
  });

  const contract = data?.rows.find((row) => String(row.id) === selected);
  const statusLabel = data?.status_options.find((option) => option.key === contract?.status)?.label;
  const money = contract?.amount == null
    ? "-"
    : `${Number(contract.amount).toLocaleString()} ${contract.currency}`;

  return (
    <>
      <section className="card mb-gap no-print">
        <label style={{ display: "block" }}>
          <span className="field-label">계약 선택</span>
          <select className="select" value={selected}
                  onChange={(event) => setParams({ contract: event.target.value }, { replace: true })}>
            <option value="">계약을 선택하세요…</option>
            {data?.rows.map((row) => (
              <option key={row.id} value={row.id}>
                {row.company} · {row.plan || "플랜 미지정"} · {kst(row.contract_date, "date") || "계약일 미지정"}
              </option>
            ))}
          </select>
        </label>

        {/* Typed, never generated: wording this app made up would print as agreed text
            that nobody agreed to. Paste the reviewed clauses. */}
        <label style={{ display: "block", marginTop: 12 }}>
          <span className="field-label">계약 조항</span>
          <textarea className="input" rows={8} value={terms}
                    onChange={(event) => setTerms(event.target.value)}
                    placeholder="법무 검토를 마친 조항을 붙여넣으세요. 이 화면은 조항을 만들어 주지 않습니다." />
        </label>
        <label style={{ display: "block", marginTop: 12 }}>
          <span className="field-label">특약 사항</span>
          <textarea className="input" rows={4} value={special}
                    onChange={(event) => setSpecial(event.target.value)} />
        </label>
      </section>

      <PrintSheet
        title="계 약 서"
        meta={contract
          ? <>계약일 {kst(contract.contract_date, "date") || "-"}{contract.sheet_client_id ? ` · Client ID ${contract.sheet_client_id}` : ""}</>
          : undefined}
      >
        {!contract ? (
          <div className="sheet__body" style={{ color: "#888" }}>
            위에서 계약을 선택하면 기록된 내용이 그대로 채워집니다.
          </div>
        ) : (
          <>
            <SheetSection title="당사자">
              <SheetFields rows={[
                ["공급자", "(주)이스트소프트"],
                ["고객사", contract.company],
                ["담당자", contract.name],
                ["이메일", contract.email],
              ]} />
            </SheetSection>

            <SheetSection title="계약 조건">
              <SheetFields rows={[
                ["플랜", contract.plan],
                ["계약 금액", money],
                ["단가", contract.unit_price],
                ["언어쌍", contract.language_pairs.join(", ")],
                ["결제 방식", contract.payment_method],
                ["상태", statusLabel ?? contract.status],
                ["계약일", kst(contract.contract_date, "date")],
                ["만료일", kst(contract.expires_at, "date")],
                ["결제 예정일", kst(contract.payment_due_at, "date")],
                ["입금일", kst(contract.paid_at, "date")],
              ]} />
            </SheetSection>

            {terms.trim() && (
              <SheetSection title="계약 조항">
                <div className="sheet__body">{terms}</div>
              </SheetSection>
            )}
            {special.trim() && (
              <SheetSection title="특약 사항">
                <div className="sheet__body">{special}</div>
              </SheetSection>
            )}

            <div className="sheet__sign">
              <div>공급자 (주)이스트소프트</div>
              <div>고객사 {contract.company}</div>
            </div>
          </>
        )}
      </PrintSheet>
    </>
  );
}
