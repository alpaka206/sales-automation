import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { TicketHistoryBox } from "../src/ui/TicketHistoryBox";

it("shows actual records inside a bordered group; only the title navigates", () => {
  const html = renderToStaticMarkup(
    <MemoryRouter>
      <TicketHistoryBox subject="이전 문의" at="2026-09-21T00:00:00" href="/tickets/1"
        records={[{ record_key: "message:1", channel: "email", direction: "inbound",
          handler: null, subject: "문의", summary: "고객이 실제로 보낸 내용",
          context: null, happened_at: "2026-09-21T00:00:00" }]} />
    </MemoryRouter>,
  );
  expect(html).toContain('class="history-box"');
  expect(html).toContain("고객이 실제로 보낸 내용");
  expect(html.indexOf("</a>")).toBeLessThan(html.indexOf("<article"));
  expect(html).toContain("<details>");
});
