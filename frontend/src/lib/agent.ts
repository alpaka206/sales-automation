// 로컬 데이터 에이전트(`perso-agent`)와의 연결. **이 파일은 우리 서버를 모릅니다.**
//
// 에이전트는 각자 PC 의 `127.0.0.1` 에 떠 있고, 브라우저가 거기를 **직접** 부릅니다. 여기서
// 받은 값은 화면에만 그리고 어디로도 보내지 않습니다 — `lib/api.ts` 를 import 하지 않는
// 것이 그 약속의 코드상 형태입니다(`tests/test_agent_stays_local.py` 가 두 파일의 import
// 를 훑어 고정합니다).
//
// 브라우저가 이 길을 막는 경우:
//  - Chrome 142+ : 공개 출처가 루프백에 접근하면 권한을 한 번 묻습니다. 우리가 부르는 것은
//    루프백이라 `targetAddressSpace: "loopback"` 입니다 — `"local"` 은 요청 자체가 깨집니다
//    (Chrome 152 실측).
//  - Safari : 막습니다. 프롬프트도 없습니다. 에이전트가 같은 화면을 `http://127.0.0.1:<포트>`
//    에 띄우므로 그 주소를 안내합니다.
import { useCallback, useEffect, useState } from "react";

const KEY = "perso.agent";
export type Pair = { port: number; token: string };

export type AsOf = {
  commit: string; committed_at: string | null; pulled_at: string | null;
  pull: string; stale: boolean;
};
export type AgentStatus = {
  as_of: AsOf; snapshot_at: string; repo: string; folder_mb: number;
  metrics: string[]; space_metrics?: string[];
};

/** 에이전트가 브라우저를 열 때 `#agent=<포트>&token=<토큰>` 으로 넘겨 줍니다.
 *  **프래그먼트라 우리 서버로 안 갑니다** — 쿼리스트링이면 접근 로그에 남습니다. */
export function readPairing(): Pair | null {
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const port = Number(hash.get("agent"));
  const token = hash.get("token");
  if (port && token) {
    const pair = { port, token };
    try { localStorage.setItem(KEY, JSON.stringify(pair)); } catch { /* 사생활 창 */ }
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
    return pair;
  }
  try {
    const saved = localStorage.getItem(KEY);
    return saved ? (JSON.parse(saved) as Pair) : null;
  } catch { return null; }
}

export function forgetPairing() {
  try { localStorage.removeItem(KEY); } catch { /* 무시 */ }
}

/** 루프백 호출. `targetAddressSpace` 는 TS 타입에 아직 없어 캐스팅합니다. */
export async function agentFetch<T>(pair: Pair, path: string, method = "GET"): Promise<T> {
  const init = {
    method,
    headers: { Authorization: `Bearer ${pair.token}` },
    targetAddressSpace: "loopback",
    cache: "no-store" as RequestCache,
  } as RequestInit;
  const response = await fetch(`http://127.0.0.1:${pair.port}${path}`, init);
  if (response.status === 401) {
    // 에이전트가 다시 켜지면 토큰이 바뀝니다(프로세스마다 새로 만듭니다). 저장값을
    // 붙들고 있으면 계속 401 이므로 버립니다.
    forgetPairing();
    throw new Error("토큰이 만료됐습니다 — 에이전트를 다시 열어 주세요");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: string } | null;
    throw new Error(body?.error ?? `${response.status}`);
  }
  return response.json() as Promise<T>;
}

/** 에이전트 연결 상태. 페어링을 읽고 `/v1/status` 를 한 번 부릅니다.
 *
 *  `hashchange` 를 듣는 이유: 해시만 바뀌는 이동은 같은 문서 안의 이동이라 `useEffect` 가
 *  다시 안 돕니다. 에이전트가 다시 켜져 새 토큰으로 이 주소를 열면 옛 토큰을 붙들고 401 만
 *  받습니다 — 실측으로 잡은 버그입니다. */
export function useAgent() {
  const [pair, setPair] = useState<Pair | null>(null);
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  const connect = useCallback(() => {
    const p = readPairing();
    setPair(p);
    if (!p) { setStatus(null); setBusy(false); return; }
    setBusy(true); setProblem(null);
    agentFetch<AgentStatus>(p, "/v1/status")
      .then((s) => setStatus(s))
      .catch((error) => { setStatus(null); setProblem(error instanceof Error ? error.message : String(error)); })
      .finally(() => setBusy(false));
  }, []);

  useEffect(() => {
    connect();
    window.addEventListener("hashchange", connect);
    return () => window.removeEventListener("hashchange", connect);
  }, [connect]);

  return { pair, status, problem, busy, reconnect: connect };
}

/** 스페이스 단위 지표 하나. `spaces` 가 비면 부르지 않습니다. */
export function useSpaceMetric<T>(pair: Pair | null, metric: string, spaces: number[]) {
  const key = spaces.join(",");
  const [data, setData] = useState<SpaceResult<T> | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!pair || !key) { setData(null); return; }
    let live = true;
    setBusy(true); setProblem(null);
    agentFetch<SpaceResult<T>>(pair, `/v1/spaces/${metric}?s=${key}`)
      .then((r) => { if (live) setData(r); })
      .catch((error) => { if (live) { setData(null); setProblem(error instanceof Error ? error.message : String(error)); } })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [pair, metric, key]);
  return { data, problem, busy };
}

export type SpaceResult<T> = {
  metric: string; spaces: number[]; as_of: AsOf; snapshot_at: string;
  data: T; computed_ms: number;
};

/** 다운로드 링크. 저장소가 공개라 Release 자산은 인증 없이 받아집니다. */
export const AGENT_RELEASES = "https://github.com/alpaka206/sales-automation/releases/latest";
export const AGENT_DOWNLOADS = {
  windows: `${AGENT_RELEASES}/download/perso-agent.exe`,
  macArm: `${AGENT_RELEASES}/download/perso-agent-mac-arm64`,
  macIntel: `${AGENT_RELEASES}/download/perso-agent-mac-intel`,
};
