import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { DealTypeHint } from "../src/screens/won/ContractFields";
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
