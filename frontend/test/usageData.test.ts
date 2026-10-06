import { afterEach, describe, expect, it, vi } from "vitest";
import { FORMAT, loadSpaceMetric, usageFetch } from "../src/lib/usageData";
import { mergeJobs, type SpaceFacts } from "../src/screens/won/usageMerge";

// 브라우저가 가공 레포에서 받는 길 — 부르는 곳은 GitHub API 하나이고, 계약의 스페이스가 든 묶음 파일만
// 받는다. 캐시는 모듈 안에 살아서(탭 하나의 메모리) 테스트마다 다른 레포 이름으로 서로를 안 본다.

const facts = (space_seq: number, ok: number): SpaceFacts => ({
  space_seq,
  credits: { monthly: null, weekly: null, buckets: null, records: null },
  jobs: { ok, failed: 1, kinds: null, proc_n: 0, proc_sum: null, proc_max: null, dur_sum: null,
          wait_n: 0, wait_sum: null, events: null, conc: null },
  usage: { pairs: null, lengths: null, sources: null, categories: null, voices: 0, voice_users: null,
           seats: 0, members: 0, users_30d: null },
});

const MANIFEST = {
  format: FORMAT, snapshot_at: "2026-10-01T00:00:00Z", snapshot_commit: "abc1234",
  exported_at: "2026-10-01T00:15:00Z", exporter: "test", spaces: 4, groups: 3,
};
const SHA_A = "a".repeat(40);
const SHA_B = "b".repeat(40);

/** 가짜 GitHub — `data` 의 머리 커밋(`head.sha`)과 커밋마다의 파일. 없는 것은 404. 받은 경로와 ref 를 적는다. */
function serve(commits: Record<string, Record<string, unknown>>, head = { sha: SHA_A }) {
  const asked: string[] = [];
  const refs = new Set<string>();
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = new URL(url);
    if (u.pathname.endsWith("/commits/data")) return new Response(head.sha);
    const path = u.pathname.split("/contents/")[1];
    const ref = u.searchParams.get("ref") ?? "";
    asked.push(path);
    refs.add(ref);
    const files = commits[ref === "data" ? head.sha : ref]; // 브랜치로 물으면 그 순간의 머리 커밋
    return files && path in files ? new Response(JSON.stringify(files[path])) : new Response("", { status: 404 });
  }));
  return { asked, refs };
}

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("usageFetch", () => {
  it("GitHub API 를 읽기 토큰으로 부르고, 실패는 고칠 사람이 할 일로 말한다", async () => {
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => new Response("", { status: 401 }));
    vi.stubGlobal("fetch", fetch);
    await expect(usageFetch({ repo: "o/r", token: "t" }, "manifest.json")).rejects.toThrow("USAGE_DATA_TOKEN");
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("https://api.github.com/repos/o/r/contents/manifest.json?ref=data");
    expect(init?.headers).toMatchObject({ Authorization: "Bearer t", Accept: "application/vnd.github.raw+json" });
    // 304 로 다시 묻는 길 — 안 바뀐 파일은 요청 한도를 안 쓴다.
    expect(init?.cache).toBe("no-cache");

    // 403 은 남은 횟수 머리글로 한도와 권한 없음을 가른다. 422 는 「data 브랜치가 없다」(커밋을 묻는 길),
    // 409 는 「레포에 커밋이 하나도 없다」(가공 레포를 막 만든 때 — 2026-10-05 실측).
    const cases = [[404, null, "Actions(export)"], [422, null, "Actions(export)"], [409, null, "Actions(export)"], [403, "0", "요청 한도"],
                   [403, "4999", "읽을 권한이 없습니다"], [429, null, "요청 한도"], [500, null, "GitHub 응답 500"]] as const;
    for (const [status, remaining, text] of cases) {
      const headers = remaining === null ? undefined : { "x-ratelimit-remaining": remaining };
      fetch.mockResolvedValueOnce(new Response("", { status, headers }));
      await expect(usageFetch({ repo: "o/r", token: "t" }, "x.json")).rejects.toThrow(text);
    }
  });
});

describe("loadSpaceMetric", () => {
  const files = {
    "manifest.json": MANIFEST,
    "facts/index.json": { 1: "e1", 2: "e1", 3: "p03", 9: "p09" },
    "facts/e1.json": { format: FORMAT, group: "e1", spaces: [facts(1, 10), facts(2, 20)] },
    "facts/p03.json": { format: FORMAT, group: "p03", spaces: [facts(3, 30)] },
    "facts/p09.json": { format: FORMAT, group: "p09", spaces: [facts(9, 90)] },
  };

  it("계약의 스페이스가 든 묶음만 받아 그 스페이스 줄만 합친다 — 범위 밖 번호는 빠진다", async () => {
    const { asked, refs } = serve({ [SHA_A]: files });
    const r = await loadSpaceMetric({ repo: "o/merge", token: "t" }, "jobs", [3, 1, 99]);
    expect(asked.sort()).toEqual(["facts/e1.json", "facts/index.json", "facts/p03.json", "manifest.json"]);
    expect([...refs]).toEqual([SHA_A]); // 브랜치 이름이 아니라 그 커밋으로 받는다
    expect(r.spaces).toEqual([1, 3]); // 같은 묶음의 2 는 안 섞인다
    expect(r.snapshot_at).toBe(MANIFEST.snapshot_at);
    expect(r.data).toEqual(mergeJobs([facts(1, 10), facts(3, 30)]));
    expect(r.data.ok).toBe(40);

    // 같은 가공이면 다시 받지 않는다 — 계약을 오가도 왕복이 없다.
    await loadSpaceMetric({ repo: "o/merge", token: "t" }, "jobs", [2]);
    expect(asked).toHaveLength(4);
  });

  it("실패한 파일은 기억하지 않는다 — 고친 뒤 다시 열면 다시 받는다", async () => {
    const { "facts/p03.json": _missing, ...broken } = files;
    serve({ [SHA_A]: broken });
    await expect(loadSpaceMetric({ repo: "o/retry", token: "t" }, "jobs", [3])).rejects.toThrow("Actions(export)");
    const { asked } = serve({ [SHA_A]: files });
    const r = await loadSpaceMetric({ repo: "o/retry", token: "t" }, "jobs", [3]);
    expect(asked).toEqual(["facts/p03.json"]); // manifest · 색인은 성공한 것이라 그대로 쓴다
    expect(r.data.ok).toBe(30);
  });

  it("09:15 에 새 가공이 올라와도 한 계산에 두 가공이 섞이지 않는다 — 머리 커밋을 다시 물을 때까지 그 커밋에서만 받는다", async () => {
    // 새 가공에서는 더 오래된 엔터프라이즈가 처음 묶여 묶음 이름이 밀렸다: 1 · 2 가 e2 로, 9 가 e1 로.
    const next = {
      "manifest.json": { ...MANIFEST, snapshot_at: "2026-10-02T00:00:00Z", exported_at: "2026-10-02T00:15:00Z" },
      "facts/index.json": { 1: "e2", 2: "e2", 3: "p03", 9: "e1" },
      "facts/e1.json": { format: FORMAT, group: "e1", spaces: [facts(9, 900)] },
      "facts/e2.json": { format: FORMAT, group: "e2", spaces: [facts(1, 100), facts(2, 200)] },
      "facts/p03.json": { format: FORMAT, group: "p03", spaces: [facts(3, 300)] },
    };
    const head = { sha: SHA_A };
    const { refs } = serve({ [SHA_A]: files, [SHA_B]: next }, head);
    vi.useFakeTimers({ now: Date.parse("2026-10-01T09:14:00+09:00"), toFake: ["Date"] });
    const source = { repo: "o/pin", token: "t" };

    await loadSpaceMetric(source, "jobs", [3]); // 어제 가공의 색인을 받아 둔다
    head.sha = SHA_B; // 가공이 올라왔다
    // 색인은 어제 것(1 → e1)인데 e1 을 브랜치로 받았다면 오늘 e1(9 만 있다)이 와서 1 이 조용히 빠졌을 것이다.
    const same = await loadSpaceMetric(source, "jobs", [1]);
    expect(same.data.ok).toBe(10);
    expect(same.snapshot_at).toBe(MANIFEST.snapshot_at);
    expect([...refs]).toEqual([SHA_A]);

    vi.setSystemTime(Date.parse("2026-10-01T09:20:00+09:00")); // 5분이 지나면 새 가공으로 통째로 옮겨 간다
    const fresh = await loadSpaceMetric(source, "jobs", [1]);
    expect(fresh.data.ok).toBe(100);
    expect(fresh.snapshot_at).toBe("2026-10-02T00:00:00Z");
    expect(refs.has(SHA_B)).toBe(true);
  });

  it("가공 데이터의 형식이 화면과 다르면 숫자를 그리지 않고 그렇다고 말한다", async () => {
    serve({ [SHA_A]: { ...files, "manifest.json": { ...MANIFEST, format: FORMAT + 1 } } });
    await expect(loadSpaceMetric({ repo: "o/format", token: "t" }, "jobs", [1]))
      .rejects.toThrow(`형식(${FORMAT + 1})이 이 화면(${FORMAT})과 다릅니다`);
  });
});
