import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { PLAN_NAMES_ID, PlanNameList, PlanSpaceAlertView, PlanSpaceLine } from "../src/screens/won/PlanSpaceHint";
import { RULE, spacesByPlan, type SpaceSummary } from "../src/screens/won/usage";

// 계약 폼의 space_seq 안내 줄과 「플랜명」 추천 목록 (2026-10-12).
//
// 운영자: 「여러 고객이 같이 쓰는 플랜은 번호를 보여주는데, 중복이라 채우기 안된다고 설명도 넣어줘」 ·
// 「실제 플랜을 만들기 전에 계약 추가부터 하면 계약 추가가 안되는 문제가 있겠네」. 둘 다 화면의 모양이
// 지키는 약속이라 그린 결과로 고정한다 — 채우기 버튼이 공용 플랜에 서면 남의 사용량이 섞이고, 플랜명이
// 고르개가 되면 제품에 플랜을 만들기 전에는 계약을 못 넣는다.

const space = (space_seq: number, plan_name: string): SpaceSummary => ({
  space_seq, known: true, plan_name, sub_status: "active", next_billing_day: null, billing_anchor_date: null,
  used_total: 0, used_30d: 0, used_90d: 0, first_use: null, last_use: null,
  jobs_ok: 0, jobs_failed: 0, last_job: null, projects: 0, reworked: 0,
});
// **플랜 이름과 스페이스 번호는 전부 지어낸 것이다** — 이 저장소는 공개라 실제 고객의 것을 적지 않는다.
const noop = () => {};
const css = readFileSync(new URL("../../src/api/static/won.css", import.meta.url), "utf-8");

describe("space_seq 칸 아래의 안내 줄", () => {
  it("한 고객의 플랜이면 번호와 [채우기] 가 선다", () => {
    const html = renderToStaticMarkup(
      <PlanSpaceLine hint={{ kind: "fill", plan: "Acme Biz", spaces: [1001, 1002] }} onFill={noop} />);
    expect(html).toContain("제품에서 「Acme Biz」 플랜을 쓰는 스페이스: <b>1001, 1002</b>");
    expect(html).toContain(">채우기</button>");
    expect(html).toContain('type="button"');      // 폼 안이라 submit 이면 누르는 순간 계약이 저장된다
    expect(html).not.toContain("막았습니다");
  });

  it("여러 고객이 같이 쓸 수 있는 플랜은 번호를 다 보여 주되 채우기는 없고, 왜 없는지 적는다", () => {
    const spaces = [3001, 3002, 3003, 3004, 3005];
    const html = renderToStaticMarkup(
      <PlanSpaceLine hint={{ kind: "shared", plan: "Shared Plan", spaces }} onFill={noop} />);
    expect(html).toContain("스페이스 5개: <b>3001, 3002, 3003, 3004, 3005</b>");
    expect(html).not.toContain("<button");
    expect(html).toContain(`스페이스가 ${RULE.fillSpaces}개를 넘는 플랜은 여러 고객이 같이 쓰는 플랜일 수 있어 [채우기]를 막았습니다`);
    expect(html).toContain("다른 고객의 사용량이 이 계약에 섞입니다");
    expect(html).toContain("이 고객의 번호만 골라 직접 적어 주세요");
  });

  it("번호가 아주 많으면 앞의 60개와 「외 N개」 — 한 고객이 수십 개를 쓰는 플랜도 복사해 적을 수 있다", () => {
    const spaces = Array.from({ length: 64 }, (_, i) => 1000 + i);
    const html = renderToStaticMarkup(<PlanSpaceLine hint={{ kind: "shared", plan: "Big Biz", spaces }} onFill={noop} />);
    expect(html).toContain("스페이스 64개:");
    expect(html).toContain("1059</b> 외 4개");
    expect(html).not.toContain("1060");
  });

  it("할 말이 없으면 아무것도 안 그린다 — 폼은 예전 그대로다", () => {
    expect(renderToStaticMarkup(<PlanSpaceLine hint={{ kind: "none" }} onFill={noop} />)).toBe("");
  });
});

// 2026-10-12 운영자: 「지금은 Perso 계정 및 플랜 편집을 눌러야만 "채우기"가 뜨는데, 만약 채우기가 있으면,
// Perso 계정 및 플랜 오른쪽에 칩 형식으로 space_seq 채우기 버튼 추가해줘. 추가 버튼 클릭 후, 다른 부분과 같이
// 확인 메시지 창 뜨게 해주고」 → 청록 칩을 보고: 「색이 상단 계약 토글이랑 겹쳐서 눈에 잘 안보여. 혹시 Perso
// 계정 및 플랜 오른쪽에 (!) 같은 칩 추가하고, 그거 누르면 채우기 버튼 뜨는걸로 하는거 어때」.
// 그래서 제목 옆의 주황 (!) 이고, 누르면 말풍선이 열려 무엇이 비었고 무엇이 들어갈지와 [채우기]가 보인다.
describe("읽기 카드 제목 옆의 (!) — space_seq 를 채울 수 있다는 표시", () => {
  const fill = { kind: "fill" as const, plan: "Acme Biz", spaces: [1001, 1002] };

  it("채울 수 있을 때만 서고, 누르면 열리는 말풍선에 무엇이 들어갈지와 [채우기]가 있다", () => {
    const html = renderToStaticMarkup(<PlanSpaceAlertView hint={fill} onFill={noop} />);
    expect(html).toContain('<details class="plan-alert">');           // 누르면 열린다 — 효과 훅 없이
    expect(html).toMatch(/<summary class="plan-alert__i"[^>]*>!<\/summary>/);
    expect(html).toContain('aria-label="space_seq 를 채울 수 있습니다"');
    expect(html).toContain("<b>space_seq 가 비어 있습니다</b>");
    expect(html).toContain("제품에서 「Acme Biz」 플랜을 쓰는 스페이스: <b>1001, 1002</b>");
    expect(html).toMatch(/<button type="button" class="btn btn-sm btn-primary">채우기<\/button>/);
  });

  it("주황이다 — 위의 계약 고르개(청록 알약)와 겹치면 안 보인다", () => {
    expect(css).toMatch(/\.won \.plan-alert__i\s*\{[^}]*background:var\(--amber-bg\)[^}]*color:var\(--amber-fg\)/);
    expect(css).not.toContain("chip-act");
  });

  it("여러 고객이 같이 쓸 수 있는 플랜 · 번호가 이미 있는 계약 · 모르는 플랜에는 표시가 없다", () => {
    const shared = { kind: "shared" as const, plan: "Shared Plan", spaces: [1, 2, 3, 4, 5] };
    expect(renderToStaticMarkup(<PlanSpaceAlertView hint={shared} onFill={noop} />)).toBe("");
    expect(renderToStaticMarkup(<PlanSpaceAlertView hint={{ kind: "none" }} onFill={noop} />)).toBe("");
  });

  it("[채우기]는 확인 창을 지나 space_seq 한 칸만 저장한다 — 다른 칸을 같이 보내면 그 값이 덮인다", () => {
    const detail = readFileSync(new URL("../src/screens/won/WonCustomerDetail.tsx", import.meta.url), "utf-8");
    expect(detail).toMatch(/mark=\{<PlanSpaceAlert [^>]*onFill=\{setFilling\}/);   // 제목 바로 옆(`mark`)에 선다
    expect(detail).toMatch(/title="space_seq 를 채웁니다"/);
    expect(detail).toMatch(/postForm\(`\/won-customers\/contracts\/\$\{contract\.id\}`, \{ space_seq: filling \}\)/);
  });
});

describe("「플랜명」 추천 목록", () => {
  it("제품에 있는 플랜 이름을 한 번씩, 한글 → 영문 순으로 낸다 (어느 PC · CI 에서나 같은 순서)", () => {
    const byPlan = spacesByPlan([space(1, "Beta Biz"), space(2, "가나다 Biz"), space(3, "Beta Biz"), space(4, "Acme Biz")]);
    const html = renderToStaticMarkup(<PlanNameList byPlan={byPlan} />);
    expect(html).toBe(`<datalist id="${PLAN_NAMES_ID}"><option value="가나다 Biz"></option>`
      + '<option value="Acme Biz"></option><option value="Beta Biz"></option></datalist>');
  });

  it("사용 데이터가 연결되지 않았으면 목록이 없을 뿐이다", () => {
    expect(renderToStaticMarkup(<PlanNameList byPlan={null} />)).toBe("");
  });

  it("플랜명 칸은 추천을 받는 글자 칸이다 — 고르개가 아니다 (제품에 플랜을 만들기 전에도 계약을 넣는다)", () => {
    const form = readFileSync(new URL("../src/screens/won/ContractFields.tsx", import.meta.url), "utf-8");
    expect(form).toMatch(/<input className="inp" list=\{PLAN_NAMES_ID\} value=\{draft\.plan_name\}/);
    expect(form).not.toMatch(/<Sel value=\{draft\.plan_name\}/);
    expect(form).not.toMatch(/<select[^>]*draft\.plan_name/);
  });
});
