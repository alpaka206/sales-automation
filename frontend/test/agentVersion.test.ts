import { describe, expect, it } from "vitest";
import { versionOutdated } from "../src/lib/agent";

// 콘솔이 요구하는 최소 버전과 에이전트 버전의 비교 — 낡은 에이전트는 새 키를 안 주고 화면은
// 빈 값을 조용히 그리므로, 이 판정 하나가 「업데이트 필요」를 띄운다.
describe("versionOutdated", () => {
  it("숫자 세 자리로 비교한다", () => {
    expect(versionOutdated("1.0.0", "1.1.0")).toBe(true);
    expect(versionOutdated("1.1.0", "1.1.0")).toBe(false);
    expect(versionOutdated("1.2.0", "1.1.0")).toBe(false);
    expect(versionOutdated("1.10.0", "1.9.0")).toBe(false); // 문자열 비교면 틀리는 자리
    expect(versionOutdated("2.0.0", "1.99.99")).toBe(false);
  });
  it("손 빌드(dev)는 최신으로, 버전 칸이 없는 옛 빌드는 가장 낡은 것으로 본다", () => {
    expect(versionOutdated("dev", "9.9.9")).toBe(false);
    expect(versionOutdated(undefined, "1.1.0")).toBe(true);
  });
});
