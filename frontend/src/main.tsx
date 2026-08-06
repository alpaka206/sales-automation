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
import { QuoteCalculator } from "./screens/QuoteCalculator";
import { Overview } from "./screens/Overview";
import { WonCustomers } from "./screens/won/WonCustomers";
import { WonCustomerDetail } from "./screens/won/WonCustomerDetail";
import { WonNew } from "./screens/won/WonNew";
import { WonContractForm } from "./screens/won/WonContractForm";
import { Quotation } from "./screens/Quotation";
import { ContractDoc } from "./screens/ContractDoc";
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
            <Route path="customers" element={<Customers />} />
            <Route path="customers/:id" element={<CustomerDetail />} />
            <Route path="email-templates" element={<EmailTemplates />} />
            <Route path="policy-docs" element={<PolicyDocs />} />
            <Route path="operations" element={<Operations />} />
            <Route path="companies/:domain" element={<CompanyDetail />} />
            <Route path="settings/users" element={<SettingsUsers />} />
            <Route path="logs" element={<Logs />} />
            <Route path="tools/quote-calculator" element={<QuoteCalculator />} />
            <Route path="overview" element={<Overview />} />
            <Route path="outbound-history" element={<WonCustomers />} />
            <Route path="won-customers" element={<WonCustomers />} />
            <Route path="won-customers/new" element={<WonNew />} />
            <Route path="won-customers/:clientId" element={<WonCustomerDetail />} />
            <Route path="won-customers/:clientId/contracts/new" element={<WonContractForm />} />
            <Route path="won-customers/:clientId/contracts/:contractId" element={<WonContractForm />} />
            <Route path="tools/quotation" element={<Quotation />} />
            <Route path="tools/contract" element={<ContractDoc />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
  );
}
