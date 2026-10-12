import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { DealTypeHint, PlanPeriodHint } from "../src/screens/won/ContractFields";
import { carryOverKeys, derive, emptyDraft } from "../src/screens/won/contractDraft";

const css = readFileSync(new URL("../../src/api/static/won.css", import.meta.url), "utf-8");

// 2026-09-22 운영자 보고: 「동그라미 안에 i도 없고 밑으로 밀려져 있어, 위에 이상한 ------
// 표시도 있고」. 원인은 클래스 이름이 목업의 `.won .hint`(점선 안내문)와 겹친 것이라,
// 이름이 다시 `hint` 로 돌아가면 같은 그림이 됩니다.
it("the (i) has its own class, a visible i, and opens on hover — not on click", () => {
  const html = renderToStaticMarkup(<DealTypeHint />);
  expect(html).toContain('class="ihint"');
  expect(html).not.toContain('class="hint"');
  expect(html).toContain('<span class="ihint__i" aria-hidden="true">i</span>');
  expect(html).not.toContain("<details");
  expect(css).not.toMatch(/\.won \.hint\s*>\s*summary/);
  expect(css).toMatch(/\.won \.ihint:hover \.ihint__pop[^{]*\{[^}]*display:\s*block/);
});

// 공급가와 「VAT 해당 여부」는 안 씁니다(2026-09-22). 초안이 그 칸을 들고 있으면 서버가 지운
// 키를 폼이 계속 보냅니다.
it("the contract draft no longer carries the VAT flag", () => {
  expect(Object.keys(emptyDraft)).not.toContain("vat_applicable");
  expect(carryOverKeys).not.toContain("vat_applicable");
  expect(derive(emptyDraft)).not.toHaveProperty("vatApplicable");
});

// 2026-10-12 운영자 — 「Perso 계정 및 플랜」의 안내가 하루에 네 번 모양을 바꿨고, 마지막이 이것입니다.
//   「플랜 기간은 말 그대로 플랜이 활성중인 기간이고, 계약 기간은 해당 계약의 기간이야」
//   「결론적으로 해당 설명 부분은 계약기간에 따라 플랜상태랑 MRR이 정해지고, 플랜기간이랑 계약기간이랑 다른점
//     이 두가지를 간단히 설명할거야」
//   (작은 글씨 두 줄로 만든 것을 보고) 「아 편집 눌렀을떄만 뜨네」 → 「그럼 방향을 바꿀게. 계약 및 결제 정보 속
//     매출 인식 (I)에 POC랑 차이 설명했듯이 플랜 시작일 옆에 (I) 추가해서 해당 내용 적절히 넣어」.
// 그래서 「매출 인식」의 (i) 와 같은 옷의 (i) 가 「플랜 시작일」 옆에 서고, 폼과 읽기 카드가 한 벌을 같이 씁니다.
it("the (i) beside 플랜 시작일 says how the two periods differ and what the contract period decides", () => {
  const html = renderToStaticMarkup(<PlanPeriodHint />);
  expect(html).toContain('class="ihint"');                    // 「매출 인식」의 (i) 와 같은 옷 — hover · 포커스로 열린다
  expect(html).toContain('<span class="ihint__i" aria-hidden="true">i</span>');
  expect(html).toContain("플랜 기간 — 플랜이 활성 중인 기간<br/>");
  expect(html).toContain("계약 기간 — 해당 계약의 기간<br/>");
  expect(html).toContain("플랜 상태 · MRR — 계약 기간으로 정해집니다");
  expect(html).not.toContain("저장하면");                     // 읽기 카드에는 저장할 것이 없다
});

it("in the form the same (i) also says what this contract becomes when saved", () => {
  const html = renderToStaticMarkup(<PlanPeriodHint preview="세팅중" />);
  expect(html).toContain("플랜 상태 · MRR — 계약 기간으로 정해집니다<br/>");
  expect(html).toContain("이 계약은 저장하면 <b>세팅중</b> 입니다");
});

it("the form and the read card share that one (i), and the plan section carries no other notice", () => {
  const form = readFileSync(new URL("../src/screens/won/ContractFields.tsx", import.meta.url), "utf-8");
  const detail = readFileSync(new URL("../src/screens/won/WonCustomerDetail.tsx", import.meta.url), "utf-8");
  expect(form).toContain("플랜 시작일<PlanPeriodHint preview={planPreview(draft)} />");
  expect(detail).toContain("<KV k={<>플랜 시작일<PlanPeriodHint /></>}");
  const plan = form.slice(form.indexOf("export function PlanFields"), form.indexOf("export function scheduleLabel"));
  expect(plan).not.toContain('className="note-box"');          // 위에도 아래에도 상자는 없다
  expect(plan).not.toContain("HELP");                          // 작은 글씨 두 줄도 없다 — (i) 하나가 말한다
  expect(plan.match(/planPreview\(draft\)/g)).toHaveLength(1);
});

// 같은 날 이어서: 「해당 사항 계약 기간 옆에도 (I)에 추가해줘」. 두 기간을 견주는 설명이라 양쪽 기간 옆에 다 선다 —
// 플랜 날짜를 보다가도, 계약 날짜를 보다가도 같은 세 줄을 만난다.
it("the same (i) also stands beside the contract period — in the read card and in the form", () => {
  const form = readFileSync(new URL("../src/screens/won/ContractFields.tsx", import.meta.url), "utf-8");
  const detail = readFileSync(new URL("../src/screens/won/WonCustomerDetail.tsx", import.meta.url), "utf-8");
  expect(detail).toContain("<KV k={<>계약기간<PlanPeriodHint /></>}");
  expect(form).toContain('계약 시작일<span className="req"> *</span><PlanPeriodHint preview={planPreview(draft)} />');
  // 한 벌이다 — 세 줄을 적은 곳은 `PlanPeriodHint` 하나.
  expect(form.match(/플랜 기간 — 플랜이 활성 중인 기간/g)).toHaveLength(1);
  expect(detail).not.toContain("플랜이 활성 중인 기간");
});
