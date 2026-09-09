import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

/** 훅은 **이른 return 위**에 있어야 한다.
 *
 *  2026-09-09 에 티켓 화면이 통째로 죽었다 — React #310, "Rendered more hooks than
 *  during the previous render". 원인은 한 줄이었다:
 *
 *      if (isPending || !data) return <LoadingBlock />;              // 293
 *      ...
 *      const [draftExpanded, setDraftExpanded] = useState(false);   // 313
 *
 *  로딩 중 렌더는 훅을 N개 부르고, 데이터가 온 렌더는 N+1개를 부른다. React 는 그
 *  어긋남을 렌더 자체를 죽여서 막는다 — 그리고 에러 경계가 없던 그때는 그것이 곧
 *  **하얀 화면**이었다. 서버 로그에는 200 만 찍혀서 어디에도 단서가 없었다.
 *
 *  **왜 아무도 못 잡았나**: tsc 는 훅 순서를 모르고, 이 저장소에는 eslint 가 없다
 *  (`react-hooks/rules-of-hooks` 가 정확히 이걸 잡는 규칙이다). 화면을 실제로 그려 보는
 *  테스트도 없어서 로딩→완료 전환을 지나는 코드가 한 줄도 없었다. 그래서 **소스를 읽어**
 *  막는다 — 이 저장소의 화면들이 전부 같은 관문 한 줄을 쓰기 때문에 이 검사가 통한다.
 *
 *  eslint 를 들이면 이 파일은 지워도 된다. 그때까지는 이것이 그 자리를 지킨다.
 */
const HOOK =
  /\b(useState|useQuery|useMemo|useRef|useEffect|useLayoutEffect|useCallback|useReducer|useContext|useHubSpotRecord)\s*\(/;

// 컴포넌트가 데이터를 기다리다 돌아가는 그 한 줄 — **무언가를 그리며** 돌아간다.
// `return` 뒤에 값이 있어야 한다: 값 없는 `if (!data) return;` 은 `useEffect` 안에서
// 빠져나가는 흔한 줄이고, 그건 컴포넌트의 관문이 아니다.
const GUARD = /^\s{0,4}if \(.*\breturn\s+[^;]/;

function tsxFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return tsxFiles(full);
    return name.endsWith(".tsx") ? [full] : [];
  });
}

describe("훅은 이른 return 위에서만 불린다", () => {
  const files = tsxFiles(join(__dirname, "..", "src"));

  it("검사할 화면이 실제로 있다", () => {
    expect(files.length).toBeGreaterThan(10);
  });

  for (const file of files) {
    const lines = readFileSync(file, "utf-8").split(/\r?\n/);
    const guard = lines.findIndex(
      (line) => GUARD.test(line) && /isPending|!data|isLoading/.test(line),
    );
    if (guard === -1) continue;

    // **컴포넌트 하나만 본다.** 이 저장소는 한 파일에 화면과 그 부품들을 같이 두는데
    // (`WonCustomerDetail.tsx` 에는 열 개가 넘는다), 파일 끝까지 세면 다음 컴포넌트의
    // 멀쩡한 훅이 걸린다. 경계는 들여쓰기 0 의 선언 — 이 저장소의 실제 모양이다.
    const next = lines.findIndex(
      (line, index) =>
        index > guard && /^(export\s+)?(function|const|class)\s/.test(line),
    );
    const offenders = lines
      .map((line, index) => [index + 1, line] as const)
      .slice(guard + 1, next === -1 ? undefined : next)
      .filter(([, line]) => !/^\s*(\*|\/\/|\/\*)/.test(line)) // 주석은 코드가 아니다
      .filter(([, line]) => HOOK.test(line));

    const name = file.split(/[\\/]/).slice(-2).join("/");
    it(`${name} — 관문 뒤에 훅이 없다`, () => {
      expect(offenders.map(([n, line]) => `${n}: ${line.trim()}`)).toEqual([]);
    });
  }
});
