import { postForm } from "../../lib/api";
import type { ListData } from "./shared";

type PendingWon = ListData["pending"][number];

/** 선택된 Won 대기 건을 계약 폼으로 보낼 준비를 하고 그 주소를 돌려줍니다. */
export async function pendingContractPath(data: ListData, item: PendingWon): Promise<string> {
  const existing = data.rows.some((row) => row.client_id === item.client_id);
  let clientId = item.client_id;
  if (!clientId || !existing) {
    const response = await postForm("/won-customers", {
      customer_type: "GTM Inbound",
      company: item.company || "고객사 미확인",
      client_id: clientId ? String(clientId) : "",
    });
    const created = await response.json() as { client_id: number };
    clientId = created.client_id;
  }
  return `/won-customers/${clientId}/contracts/new?pending=${item.id}`;
}
