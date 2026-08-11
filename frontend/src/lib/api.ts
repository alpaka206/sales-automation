// Every read goes through here. The server already builds these shapes for the Jinja
// screens — /api/ui/* returns the SAME context dicts — so there is one source of truth
// for what a screen shows, not a parallel API that drifts from it.
import { QueryClient } from "@tanstack/react-query";

/** 상태 코드를 달고 던집니다. "권한 없음"과 "서버가 터졌다"를 화면이 구분할 수 있어야
 *  합니다 — 구분하지 못해서, 500 을 내던 접근 승인 화면이 관리자에게 권한이 없다고
 *  말하고 있었습니다. */
export class HttpError extends Error {
  constructor(readonly status: number, path: string) {
    super(`${status} ${path}`);
  }
}

export async function getJSON<T>(path: string): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) throw new HttpError(response.status, path);
  return response.json();
}

// Writes still go to the routes the Jinja forms post to. Reusing them means the send
// guard, the stage-sync rules and the safe-mode block stay in exactly one place.
export async function postForm(path: string, data: Record<string, string>) {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(data),
  });
  if (!response.ok) throw new Error(`${response.status} ${path}`);
  return response;
}

/** Re-read what is on screen whenever the server says something changed.
 *
 * This is the part React does not do by itself: its state lives in one tab, so a stage
 * moved by someone else — or by this operator in another window — was invisible until a
 * reload. The server publishes a topic; every open console invalidates and refetches.
 * Reconnection is the browser's job (EventSource retries on its own). */
export function listenForChanges(client: QueryClient) {
  const source = new EventSource("/api/ui/events", { withCredentials: true });
  // 이벤트를 묶습니다. 한 번의 저장이 두 번의 재요청이 되고 있었습니다 — 쓴 탭은 스스로
  // invalidate 하고, 곧이어 자기가 일으킨 SSE 이벤트를 받아 또 합니다. 연달아 쓰면 그만큼
  // 늘어납니다. 왕복 하나가 200ms 인 환경에서는 그게 그대로 화면 지연입니다.
  let pending: ReturnType<typeof setTimeout> | null = null;
  source.onmessage = () => {
    // 여기서 "내가 방금 쓴 것의 메아리" 를 시간으로 걸러 보려던 적이 있습니다. 두 가지가
    // 틀렸습니다. 첫째, 이 이벤트에는 **누가 썼는지가 없습니다**(토픽은 경로뿐) — 그래서
    // 남의 저장까지 같이 버려지고, 되받을 길이 없는 화면이 있습니다(포커스 재요청은 꺼져
    // 있고 대부분 폴링도 없습니다). 둘째, 정작 자기 메아리를 못 막습니다: 보드의 쓰기는
    // 303 → 302 를 따라가느라 응답이 세 홉 뒤에 끝나는데 서버는 첫 핸들러가 끝날 때 이미
    // 알리므로, 창을 열기 전에 메아리가 지나갑니다. 제대로 하려면 서버가 쓴 탭을 토픽에
    // 실어 보내야 하고, 그건 아끼는 왕복 하나가 감당할 크기가 아닙니다.
    if (pending) clearTimeout(pending);
    pending = setTimeout(() => {
      pending = null;
      void client.invalidateQueries();
    }, 300);
  };
  return () => {
    if (pending) clearTimeout(pending);
    source.close();
  };
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The data behind these screens changes when a person does something, not on its
      // own: serve it from cache. This is the "받아와서 cache" the full-page reloads were
      // not doing.
      //
      // 포커스 재요청은 껐습니다. 창을 다시 누를 때마다 화면에 떠 있는 모든 질의를 다시
      // 받았는데, 그건 SSE 가 이미 하는 일입니다 — 서버에서 뭔가 바뀌면 그때 알려 옵니다.
      // 알트탭 한 번이 재요청 한 묶음이 될 이유가 없고, 왕복 하나가 200ms 입니다.
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});
