import type { ListData } from "./shared";

type PendingWon = ListData["pending"][number];

/** 선택된 Won 대기 건이 갈 계약 폼 주소.
 *
 * **고객을 여기서 만들지 않습니다.** 예전에는 이 함수가 `POST /won-customers` 로 고객을
 * 먼저 만들고 폼으로 보냈습니다. 폼을 채우지 않고 나가면 계약이 0건인 고객이 남았고, 그
 * 고객은 「세팅중」으로 워크북 「고객 기본 정보」에 실려 나갔습니다 — 지울 길도 없어서
 * 누를 때마다 한 줄씩 쌓였습니다(2026-08-25 운영자 보고). 이제 고객은 계약을 저장하는
 * 순간 같이 만들어집니다(`POST /won-customers` 가 둘을 한 트랜잭션에서 만듭니다).
 *
 * 그래서 이 함수는 주소만 고릅니다 — 비동기도 아니고 실패할 것도 없습니다.
 */
export function pendingContractPath(data: ListData, item: PendingWon): string {
  const known = Boolean(item.client_id) && data.rows.some((row) => row.client_id === item.client_id);
  return known
    ? `/won-customers/${item.client_id}/contracts/new?pending=${item.id}`
    : `/won-customers/new/contract?pending=${item.id}`;
}
