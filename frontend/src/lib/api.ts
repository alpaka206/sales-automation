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
