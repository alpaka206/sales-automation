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
import { Operations } from "./screens/Operations";
import { CompanyDetail } from "./screens/CompanyDetail";
import { SettingsUsers } from "./screens/SettingsUsers";
import { Logs } from "./screens/Simple";
import { WonCustomers } from "./screens/won/WonCustomers";
import { WonCustomerDetail } from "./screens/won/WonCustomerDetail";
import { WonNew } from "./screens/won/WonNew";
import { SignIn } from "./screens/SignIn";

const root = createRoot(document.getElementById("root")!);

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
            <Route path="operations" element={<Operations />} />
            <Route path="companies/:domain" element={<CompanyDetail />} />
            <Route path="settings/users" element={<SettingsUsers />} />
            <Route path="logs" element={<Logs />} />
            <Route path="outbound-history" element={<WonCustomers />} />
            <Route path="won-customers" element={<WonCustomers />} />
            <Route path="won-customers/new" element={<WonNew />} />
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
