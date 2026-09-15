import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { listenForChanges, queryClient } from "./lib/api";
import { Shell } from "./app/Shell";
import { Dashboard } from "./screens/Dashboard";
import { Messages } from "./screens/Messages";
import { MessageDetail } from "./screens/MessageDetail";
import { Customers } from "./screens/Customers";
import { CustomerDetail } from "./screens/CustomerDetail";
import { EmailTemplates } from "./screens/EmailTemplates";
import { PolicyDocs } from "./screens/PolicyDocs";
import { SalesInsights } from "./screens/SalesInsights";
import { DataAgent } from "./screens/DataAgent";
import { CompanyDetail } from "./screens/CompanyDetail";
import { SettingsUsers } from "./screens/SettingsUsers";
import { SettingsMailboxes } from "./screens/SettingsMailboxes";
import { Logs } from "./screens/Simple";
import { WonCustomers } from "./screens/won/WonCustomers";
import { WonCustomerDetail } from "./screens/won/WonCustomerDetail";
import { WonNew } from "./screens/won/WonNew";
import { SignIn } from "./screens/SignIn";

const root = createRoot(document.getElementById("root")!);

// **날짜 칸은 연도 네 자리를 치면 달로 넘어간다** (2026-09-15 운영자 지시). 크롬의
// `<input type="date">` 는 `max` 가 없으면 연도를 여섯 자리(275760년)까지 받아서, 2026 을
// 치고도 커서가 연도에 머문다 — 달·일로 가려면 화살표나 Tab 을 눌러야 했다. 상한이 네 자리
// 연도이면 네 글자에서 넘어간다. 칸이 열일곱 곳이라 한 자리(포커스가 들어올 때)에서 다 건다 —
// 칸마다 속성을 적으면 다음에 만드는 칸이 빠진다. 이미 `max` 가 있는 칸은 그대로 둔다.
document.addEventListener("focusin", (event) => {
  const el = event.target;
  if (!(el instanceof HTMLInputElement) || el.max) return;
  if (el.type === "date") el.max = "9999-12-31";
  else if (el.type === "month") el.max = "9999-12";
});

// Sign-in renders before there is a session, so it is not a route: it has no sidebar, no
// data to fetch, and no event stream to open. /auth/* is also the one prefix the auth
// middleware lets through, which is why the URL stays exactly what it was.
if (location.pathname.startsWith("/auth/")) {
  root.render(
    <StrictMode>
      <SignIn pending={location.pathname.startsWith("/auth/google")} />
    </StrictMode>,
  );
} else {
  mountConsole();
}

// Opened once for the whole app, not per screen: one stream feeds every cached query.
function mountConsole() {
  listenForChanges(queryClient);

  // basename: the app is served from /app, so the router's "/" is /app.
  root.render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/app">
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<Dashboard />} />
            <Route path="messages" element={<Messages />} />
            <Route path="messages/:id" element={<MessageDetail />} />
            {/* 같은 화면을 **티켓(대화) 기준**으로 엽니다. 보드 카드가 이 길로 들어옵니다:
                HubSpot 에서 들여온 티켓에는 메일 행이 없어서 `/messages/:id` 로는 열 수
                없고, 그래서 예전에는 그 카드가 고객 페이지로 빠졌습니다 — Deal Detail 은
                티켓의 값인데 티켓을 열 방법이 없었던 셈입니다. */}
            <Route path="tickets/:conversationId" element={<MessageDetail />} />
            <Route path="customers" element={<Customers />} />
            <Route path="customers/:id" element={<CustomerDetail />} />
            <Route path="email-templates" element={<EmailTemplates />} />
            <Route path="policy-docs" element={<PolicyDocs />} />
            <Route path="operations" element={<SalesInsights />} />
            {/* 스냅샷 데이터를 로컬 에이전트에서 가져와 그린다 — 데이터는 우리 서버를
                안 지난다. 화면만 여기서 오고 숫자는 브라우저가 127.0.0.1 에서 받는다. */}
            <Route path="data" element={<DataAgent />} />
            <Route path="companies/:domain" element={<CompanyDetail />} />
            <Route path="settings/users" element={<SettingsUsers />} />
            <Route path="settings/mailboxes" element={<SettingsMailboxes />} />
            <Route path="logs" element={<Logs />} />
            <Route path="outbound-history" element={<WonCustomers />} />
            <Route path="won-customers" element={<WonCustomers />} />
            <Route path="won-customers/new" element={<WonNew />} />
            {/* 아직 없는 고객의 첫 계약 폼. 뒤에 세울 상세가 없어서 목록 위에
                뜹니다 — 고객은 그 폼을 저장할 때 계약과 함께 만들어집니다. */}
            <Route path="won-customers/new/contract" element={<WonCustomers />} />
            <Route path="won-customers/:clientId" element={<WonCustomerDetail />} />
            {/* 계약 폼은 모달입니다 — 뒤에 상세가 남아 있어야 어느 고객의 계약인지가
                보입니다. 그래서 같은 화면을 그리고, 상세가 경로를 보고 모달을 엽니다.
                주소는 그대로라 새로고침해도 열려 있고 뒤로가기로 닫힙니다. */}
            <Route path="won-customers/:clientId/contracts/new" element={<WonCustomerDetail />} />
            <Route path="won-customers/:clientId/contracts/:contractId" element={<WonCustomerDetail />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
  );
}
