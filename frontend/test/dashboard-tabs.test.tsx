import { readFileSync } from "node:fs";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";
import { Dashboard } from "../src/screens/Dashboard";

// 2026-11-05 운영자: 「상단에 오늘 접수, 답변대기, 협상중 빼고, 대신에 신규 문의, 회신 도착, 전체 이렇게 나누자」 ·
// 「각각 탭을 누르면 해당 상태값의 문의만 보여 아래 "답변 대기중인 문의"에」.
// 고른 탭은 주소에 있으니, 주소별로 그려 보면 탭을 누른 뒤의 화면이다.

const row = (subject: string, stage: string, status: string) => ({
  id: null,
  conversation_id: 1,
  href: `/tickets/${subject}`,
  status,
  stage,
  subject,
  category: null,
  email: `${subject}@example.com`,
  received_at: "2026-11-01T00:00:00",
  waiting_since: "2026-11-01T00:00:00",
});
const fresh = row("fresh-inquiry", "new", "pending_approval");
const replied = row("customer-replied", "negotiation", "received");

const payload = (reply = [replied]) => ({
  queue: {
    new: { total: 12, rows: [fresh] },
    reply: { total: reply.length, rows: reply },
    all: { total: 12 + reply.length, rows: [fresh, ...reply] },
  },
  now: "2026-11-05T00:00:00",
  stage_labels: {},
  category_labels: {},
  unqualified: [],
  manual_log_stages: [],
  deal_details: {},
  stages: [],
});

function screen(url: string, data = payload()) {
  const client = new QueryClient();
  client.setQueryData(["dashboard"], data);
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <Dashboard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** 켜진 탭의 글자 — 켜졌다는 것을 화면 낭독기(aria-selected)와 눈(is-active)이 같이 말하는지까지 본다. */
function activeTab(html: string) {
  const tabs = [...html.matchAll(/<button[^>]*role="tab"[^>]*>(.*?)<\/button>/g)];
  const on = tabs.filter((tab) => tab[0].includes('aria-selected="true"'));
  expect(on).toHaveLength(1);
  expect(on[0][0]).toContain("is-active");
  return on[0][1].replace(/<[^>]+>/g, "");
}

it("the three tabs stand in the operator's order, each with its own number", () => {
  const tabs = [...screen("/").matchAll(/role="tab"[^>]*>(.*?)<\/button>/g)].map((tab) => tab[1].replace(/<[^>]+>/g, ""));
  expect(tabs).toEqual(["신규 문의 12", "회신 도착 1", "전체 13"]);
});

it("a tab shows only its own inquiries in the table below", () => {
  const fresher = screen("/?tab=new");
  expect(activeTab(fresher)).toBe("신규 문의 12");
  expect(fresher).toContain("fresh-inquiry");
  expect(fresher).not.toContain("customer-replied");

  const replies = screen("/?tab=reply");
  expect(activeTab(replies)).toBe("회신 도착 1");
  expect(replies).toContain("customer-replied");
  expect(replies).not.toContain("fresh-inquiry");
});

it("with no tab picked — or a stale address — nothing is hidden", () => {
  for (const url of ["/", "/?tab=all", "/?tab=nonsense"]) {
    const html = screen(url);
    expect(activeTab(html)).toBe("전체 13");
    expect(html).toContain("fresh-inquiry");
    expect(html).toContain("customer-replied");
  }
});

it("an empty tab says what is missing, not that nothing waits at all", () => {
  const html = screen("/?tab=reply", payload([]));
  expect(html).toContain("도착한 회신이 없습니다.");
  expect(html).not.toContain("답변을 기다리는 문의가 없습니다.");
});

it("a row hands its whole address to the screen it opens, so 뒤로 lands on the same tab", () => {
  // `state.from` 은 정적 렌더링에 안 보여서 글자로 고정한다 — 탭이 주소에 있는 것은 위 테스트들이 본다. 잘라서 넘기면 전체로 돌아간다.
  const queue = readFileSync(new URL("../src/ui/QueueTable.tsx", import.meta.url), "utf-8");
  expect(queue).toContain("const from = location.pathname + location.search;");
  expect(queue).toContain("state={{ from }}");
});

it("the old counters are gone from the heading", () => {
  const html = screen("/");
  for (const gone of ["오늘 접수", "협상중"]) expect(html).not.toContain(gone);
  expect(html).toContain("답변 대기중인 문의");
});
