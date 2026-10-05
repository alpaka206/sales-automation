// 사용 데이터의 **열쇠만** 우리 서버에서 받습니다 (2026-10-01, 로컬 에이전트를 걷어내며).
//
// 가공된 스냅샷은 비공개 가공 레포에 있고, 브라우저가 그것을 GitHub 에서 직접 받습니다
// (`lib/usageData.ts`). 서버가 건네는 것은 그 레포 이름과 읽기 전용 토큰 둘뿐입니다 — 이 파일은
// `lib/api` 를 쓰는 대신 **데이터 모듈을 import 하지 않습니다.** 서버와 말하는 모듈과 데이터를 쥔
// 모듈이 갈려 있어야 「데이터가 서버로 간다」가 코드에 안 생깁니다
// (`tests/test_usage_data_stays_off_server.py` 가 두 방향을 다 고정합니다).
//
// 토큰은 메모리(React Query 캐시)에만 둡니다 — localStorage 에 두면 로그아웃해도 남습니다. 응답도
// 서버가 `no-store` 로 내려 브라우저 디스크에 안 남습니다.
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { getJSON } from "./api";

export type UsageSource = { repo: string; token: string };

export function useUsageSource() {
  const { data, error, isPending } = useQuery({
    queryKey: ["usage-source"],
    queryFn: () => getJSON<{ repo: string | null; token: string | null }>("/api/ui/usage-source"),
    // 바뀌는 것은 운영자가 Render 에서 토큰을 갈 때뿐입니다. 그때도 SSE 무효화가 다시 받아 옵니다.
    staleTime: 30 * 60_000,
  });
  // 같은 값이면 같은 객체 — 이것을 훅의 의존성으로 쓰는 쪽이 렌더마다 다시 돌지 않게.
  const source = useMemo<UsageSource | null>(
    () => (data?.repo && data.token ? { repo: data.repo, token: data.token } : null), [data]);
  return {
    source,
    /** 받기 전에는 null — 「연결 안 됨」을 그리면 안 되는 동안입니다. */
    configured: data ? source !== null : null,
    // 한 번 받은 열쇠가 있으면 다시 받기 실패는 문제가 아닙니다 — 열쇠는 그대로 맞습니다.
    problem: !data && error ? error.message : null,
    busy: isPending,
  };
}
