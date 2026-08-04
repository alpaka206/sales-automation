// Every read goes through here. The server already builds these shapes for the Jinja
// screens — /api/ui/* returns the SAME context dicts — so there is one source of truth
// for what a screen shows, not a parallel API that drifts from it.
import { QueryClient } from "@tanstack/react-query";

export async function getJSON<T>(path: string): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) throw new Error(`${response.status} ${path}`);
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
  source.onmessage = () => {
    void client.invalidateQueries();
  };
  return () => source.close();
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The data behind these screens changes when a person does something, not on its
      // own: serve it from cache, revalidate on focus. This is the "받아와서 cache" the
      // full-page reloads were not doing.
      staleTime: 30_000,
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
});
