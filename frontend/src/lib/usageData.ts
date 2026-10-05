// 가공된 사용 데이터를 **GitHub 에서 직접** 받습니다 (2026-10-01, 로컬 에이전트를 걷어내며).
//
// 매일 09:15(KST) 에 비공개 가공 레포의 GitHub Actions 가 스냅샷을 JSON 으로 가공해 그 레포의
// `data` 브랜치에 한 커밋으로 올립니다. 브라우저는 우리 서버에서 열쇠(`lib/usageSource.ts`)만 받아
// GitHub API 를 부르고, 계약의 스페이스로 합치는 일도 여기서 합니다(`screens/won/usageMerge.ts`).
// **이 파일은 우리 서버를 모릅니다** — `lib/api` 를 import 하지 않고, 부르는 곳은 GitHub API 하나뿐입니다
// (`tests/test_usage_data_stays_off_server.py` 가 고정합니다). 받은 값은 화면에만 그리고 어디로도
// 보내지 않습니다 — 예외는 회차 완료 표시 하나(`screens/won/reconcile.ts`).
//
// 받는 파일 — 가공기(`agent/export.go`)가 쓰는 그대로이고, 전부 `data` 가 지금 가리키는 **커밋 하나**에서
// 받습니다(`headOf` — 가공 둘이 한 계산에 섞이지 않게):
//   manifest.json                         스냅샷 시각 · 가공 시각 · 커밋 · 형식 — 모든 값의 「언제 것인가」
//   summary.json · evidence.json          목록의 두 열과 판정 재료 · 자동 대조 근거
//   sales.json · metrics.json             영업 인사이트 · 데이터 분석 화면
//   facts/index.json → facts/<묶음>.json  상세의 크레딧 · 작업 · 영상 분석 재료(스페이스 하나씩)
import { useEffect, useState } from "react";
import type { UsageSource } from "./usageSource";
import {
  mergeCredits, mergeJobs, mergeUsage,
  type CreditsData, type JobsData, type SpaceFacts, type UsageData,
} from "../screens/won/usageMerge";

/** 가공 결과가 사는 브랜치. `main` 에는 워크플로가 남고, 여기에는 매일 한 커밋만 섭니다. */
export const DATA_REF = "data";

/** 가공 파일의 모양 번호 — 가공기(`agent/export.go` 의 `dataFormat`)가 manifest 에 싣는 값과 같아야 합니다.
 *  재료의 모양을 바꾸면 둘 다 같은 커밋에서 올립니다. 다르면 숫자를 그리지 않고 그렇다고 말합니다 — 모양이
 *  다른 재료를 합치면 빈 칸 · NaN 이 「기록 없음」처럼 섭니다. 화면은 main 에 들어가면 곧 배포되고 재료는
 *  가공 레포가 그 커밋으로 다시 돌아야 바뀌므로, 그 사이가 실제로 생깁니다. */
export const FORMAT = 1;

export type Manifest = {
  format: number;
  /** 스냅샷이 만들어진 시각(RFC3339 UTC) — 판정의 「지금」입니다. 벽시계가 아닙니다. */
  snapshot_at: string;
  snapshot_commit: string;
  /** 가공한 시각. */
  exported_at: string;
  exporter: string;
  /** 가공 범위의 스페이스 수(엔터프라이즈 · 유료) · 묶음 파일 수. */
  spaces: number; groups: number;
};
export type SpaceResult<T> = { metric: string; spaces: number[]; snapshot_at: string; data: T };
export type Loaded<T> = { data: T | null; problem: string | null; busy: boolean };

/** 이 레포의 GitHub API 하나를 부릅니다 — 받는 곳은 여기 한 곳입니다. */
async function github(source: UsageSource, path: string, accept: string): Promise<Response> {
  // `no-cache` 는 「안 받는다」가 아니라 「ETag 로 다시 묻는다」입니다 — 안 바뀌었으면 304 이고, GitHub 는
  // 304 를 요청 한도에서 안 셉니다. 토큰 하나를 콘솔 전원이 나눠 쓰므로 그게 중요합니다.
  const response = await fetch(`https://api.github.com/repos/${source.repo}/${path}`, {
    headers: { Authorization: `Bearer ${source.token}`, Accept: accept, "X-GitHub-Api-Version": "2022-11-28" },
    cache: "no-cache",
  });
  if (!response.ok) throw new Error(failure(response.status, response.headers.get("x-ratelimit-remaining")));
  return response;
}

/** 파일 하나를 원문 그대로(`raw`) 받습니다 — 객체 모양은 1MB 까지라 큰 엔터프라이즈의 묶음 파일이 안 넘어옵니다.
 *  `ref` 는 브랜치나 커밋입니다 — 화면은 언제나 커밋으로 받습니다(아래 `headOf`). */
export async function usageFetch<T>(source: UsageSource, path: string, ref: string = DATA_REF): Promise<T> {
  return (await github(source, `contents/${path}?ref=${ref}`, "application/vnd.github.raw+json")).json() as Promise<T>;
}

/** 상태 코드를 **고칠 사람이 할 일**로 옮깁니다 — 「401」만 적으면 무엇을 고쳐야 하는지 아무도 모릅니다.
 *  403 은 둘입니다: 한도(남은 횟수 0)와 권한 없음(토큰의 Repository access · Contents 가 이 레포를 안 가리킴).
 *  GitHub 가 그 머리글을 CORS 로 내보내 주어서(2026-10-01 실측) 브라우저가 둘을 가를 수 있습니다.
 *  422 는 커밋을 묻는 길(`commits/data`)이 「그런 브랜치가 없다」고 답하는 모양입니다. */
function failure(status: number, remaining: string | null): string {
  if (status === 401) return "토큰이 틀렸거나 만료됐습니다 — Render 의 USAGE_DATA_TOKEN 을 새 토큰으로 바꿔야 합니다";
  if (status === 404 || status === 422) return "가공된 데이터를 못 찾았습니다 — 가공 레포의 Actions(export)가 한 번이라도 성공했는지 확인하세요";
  if (status === 429 || (status === 403 && remaining === "0")) return "GitHub 요청 한도에 걸렸습니다 — 잠시 뒤 다시 엽니다";
  if (status === 403) return "토큰에 이 레포를 읽을 권한이 없습니다 — 토큰의 Repository access 와 Contents(Read-only)를 확인하세요";
  return `GitHub 응답 ${status}`;
}

// ── 캐시 — 이 탭의 메모리에만 ─────────────────────────────────────────────
// 실패는 기억하지 않습니다 — 기억하면 토큰을 고친 뒤에도 같은 오류를 봅니다.
const HEAD_TTL = 5 * 60_000;
type Head = { sha: string; manifest: Manifest };
let head: { repo: string; at: number; promise: Promise<Head> } | null = null;

/** 지금 `data` 가 가리키는 커밋과 그 manifest — 5분 기억합니다(화면을 옮길 때마다 묻지는 않되, 열어 둔 콘솔도
 *  09:15 가공을 곧 봅니다). **파일은 전부 이 커밋에서 받습니다.** `ref=data` 로 받으면 가공이 올라온 직후 5분
 *  동안, 앞서 받아 둔 파일(어제 가공)과 새로 받는 파일(오늘 가공)이 한 계산에서 섞입니다 — 묶음 이름(e1 …)은
 *  차례라 날마다 바뀔 수 있고 b · p · u 는 가공 하나 안에서만 통해서, 스페이스가 빠지거나 두 번 세지는데
 *  오류는 안 납니다. */
function headOf(source: UsageSource): Promise<Head> {
  if (!head || head.repo !== source.repo || Date.now() - head.at > HEAD_TTL) {
    const memo = { repo: source.repo, at: Date.now(), promise: resolveHead(source) };
    head = memo;
    memo.promise.catch(() => { if (head === memo) head = null; });
  }
  return head.promise;
}

async function resolveHead(source: UsageSource): Promise<Head> {
  const sha = (await (await github(source, `commits/${DATA_REF}`, "application/vnd.github.sha")).text()).trim();
  if (!/^[0-9a-f]{40}$/.test(sha)) throw new Error("GitHub 가 data 브랜치의 커밋을 알려 주지 않았습니다");
  const manifest = await usageFetch<Manifest>(source, "manifest.json", sha);
  if (manifest.format !== FORMAT) {
    throw new Error(`가공 데이터 형식(${manifest.format})이 이 화면(${FORMAT})과 다릅니다 — 화면을 새로 고치고, 그래도`
      + " 같으면 가공 레포의 EXPORTER_COMMIT 을 배포된 커밋으로 맞춘 뒤 Actions 에서 Run workflow 를 누르세요");
  }
  return { sha, manifest };
}

const manifestOf = (source: UsageSource) => headOf(source).then((h) => h.manifest);

const files = new Map<string, Promise<unknown>>();
let generation = "";

/** 파일은 (레포, 커밋)마다 한 번만 받습니다. 커밋이 바뀌면 옛 것을 버립니다 — 큰 묶음 파일은 몇 MB 라
 *  며칠 열어 둔 탭이 날마다 한 벌씩 쌓으면 안 됩니다. */
function fileOf<T>(source: UsageSource, h: Head, path: string): Promise<T> {
  const gen = `${source.repo}@${h.sha}`;
  if (gen !== generation) { files.clear(); generation = gen; }
  const hit = files.get(path) as Promise<T> | undefined;
  if (hit) return hit;
  const promise = usageFetch<T>(source, path, h.sha);
  files.set(path, promise);
  promise.catch(() => { if (files.get(path) === promise) files.delete(path); });
  return promise;
}

// ── 훅 ──────────────────────────────────────────────────────────────────
/** `key` 가 하나의 질문입니다 — null 이면 묻지 않습니다. 답은 그 질문의 것일 때만 돌려줍니다: 계약을
 *  바꾼 직후에 앞 계약의 숫자가 「계산 중」 옆에 서 있으면 안 됩니다. */
function useLoad<T>(source: UsageSource | null, key: string | null,
                    load: (source: UsageSource, key: string) => Promise<T>): Loaded<T> {
  const id = source && key !== null ? `${source.repo}\n${source.token}\n${key}` : null;
  const [got, setGot] = useState<{ id: string; data: T | null; problem: string | null } | null>(null);
  useEffect(() => {
    if (!source || key === null || id === null) return;
    let live = true;
    const done = (data: T | null, problem: string | null) => { if (live) setGot({ id, data, problem }); };
    load(source, key).then(
      (data) => done(data, null),
      (error: unknown) => done(null, error instanceof Error ? error.message : String(error)));
    return () => { live = false; };
    // `id` 가 레포 · 토큰 · 질문을 다 담습니다 — `source` 객체나 `load` 함수가 렌더마다 새것이어도 다시 묻지 않습니다.
  }, [id]);
  const hit = id !== null && got?.id === id ? got : null;
  return { data: hit ? hit.data : null, problem: hit ? hit.problem : null, busy: id !== null && !hit };
}

const STALE_MS = 36 * 3_600_000;
/** 스냅샷이 36시간 넘게 묵었나 — 매일 가공되므로 하루를 건너뛰었다는 뜻입니다. */
export const isStale = (snapshotAt: string) => Date.now() - Date.parse(snapshotAt) > STALE_MS;

/** 화면 도장 — 브라우저 시간대의 `YYYY-MM-DD HH:mm`. 가공 파일의 시각은 전부 UTC 라 그대로 자르면 9시간 이릅니다. */
export function stamp(iso: string): string {
  const at = new Date(iso);
  const p = (v: number) => String(v).padStart(2, "0");
  return `${at.getFullYear()}-${p(at.getMonth() + 1)}-${p(at.getDate())} ${p(at.getHours())}:${p(at.getMinutes())}`;
}

/** 스페이스 목록의 열쇠 — 순서와 중복이 달라도 같은 질문입니다. */
export const spacesKey = (spaces: number[]) => [...new Set(spaces)].sort((a, b) => a - b).join(",");

/** 연결 상태 — 「데이터 분석」 화면의 도장. */
export function useUsageStatus(source: UsageSource | null) {
  const { data, problem, busy } = useLoad(source, "manifest.json", manifestOf);
  return { manifest: data, problem, busy, stale: data ? isStale(data.snapshot_at) : false };
}

/** 맨 위 파일 하나(summary · evidence · sales · metrics)와 그 스냅샷 시각. `path` 가 null 이면 안 받습니다. */
export function useUsageFile<T>(source: UsageSource | null, path: string | null) {
  return useLoad(source, path, async (s, p) => {
    const h = await headOf(s);
    return { snapshot_at: h.manifest.snapshot_at, data: await fileOf<T>(s, h, p) };
  });
}

/** 영업 인사이트 — 제품 전체 집계 한 파일. */
export function useSalesInsight<T>(source: UsageSource | null) {
  return useUsageFile<T>(source, "sales.json");
}

type MetricData = { credits: CreditsData; jobs: JobsData; usage: UsageData };
const MERGE: { [M in keyof MetricData]: (facts: SpaceFacts[]) => MetricData[M] } = {
  credits: mergeCredits, jobs: mergeJobs, usage: mergeUsage,
};

/** 계약 하나의 크레딧 · 작업 · 영상 분석. 그 스페이스들이 든 묶음 파일만 받아 그 스페이스 줄만 합칩니다.
 *  가공 범위 밖의 스페이스는 색인에 없어 빠집니다 — 어느 번호가 빠졌는지는 같은 커밋의 summary.json 으로
 *  `usageFor`(`won/useUsage.ts`)가 `missing` 에 적고, 목록과 상세 머리가 그 번호를 답니다. */
export async function loadSpaceMetric<M extends keyof MetricData>(
  source: UsageSource, metric: M, spaces: number[],
): Promise<SpaceResult<MetricData[M]>> {
  const h = await headOf(source);
  const index = await fileOf<Record<string, string>>(source, h, "facts/index.json");
  const want = new Set(spaces);
  const groups = [...new Set([...want].map((space) => index[space]).filter((g): g is string => !!g))];
  const parts = await Promise.all(groups.map((g) => fileOf<{ spaces: SpaceFacts[] }>(source, h, `facts/${g}.json`)));
  // 스페이스 순으로 — 합치기의 동률(같은 시각 · 같은 차례의 기록)이 받은 순서에 따라 흔들리지 않게.
  const facts = parts.flatMap((p) => p.spaces.filter((f) => want.has(f.space_seq)))
    .sort((a, b) => a.space_seq - b.space_seq);
  return { metric, spaces: facts.map((f) => f.space_seq), snapshot_at: h.manifest.snapshot_at, data: MERGE[metric](facts) };
}

export function useSpaceMetric<M extends keyof MetricData>(source: UsageSource | null, metric: M, spaces: number[]) {
  const list = spacesKey(spaces);
  return useLoad(source, list ? `${metric}:${list}` : null, (s) => loadSpaceMetric(s, metric, spaces));
}
