import { describe, expect, it } from "vitest";
import { amount, scaleFor, tickLabel } from "../src/screens/won/shared";

/** `man()` 한 줄이 하던 일입니다(`₩{value/10000}만`). 세 군데서 어긋났고, 그 셋이 여기
 *  고정됩니다 — 운영자가 「숫자가 애매하다」고 한 것이 전부 이 셋이었습니다. */
describe("금액 표기", () => {
  const 만 = scaleFor(12_000_000, "KRW");

  it("0 에는 단위를 안 붙인다", () => {
    // `₩0만` 은 금액이 아닙니다.
    expect(amount(0, "KRW", 만)).toBe("₩0");
    expect(tickLabel(0, 만)).toBe("0");
  });

  it("부호는 통화 기호 앞이다", () => {
    // `₩-940만` 은 원화가 음수인 것처럼 읽힙니다.
    expect(amount(-9_400_000, "KRW", 만)).toBe("-₩940만");
    expect(tickLabel(-5_000_000, 만)).toBe("-500만");
  });

  it("한 축은 한 단위를 쓴다", () => {
    // 어떤 눈금은 50만·어떤 눈금은 1,000만이면 자릿수를 세어야 비교가 됩니다.
    expect([0, 5_000_000, 10_000_000].map((v) => tickLabel(v, 만)))
      .toEqual(["0", "500만", "1,000만"]);
  });

  it("눈금에는 통화 기호가 없다", () => {
    // 기호는 큰 숫자와 고르개가 이미 말합니다 — 눈금마다 반복하면 격자보다 시끄럽습니다.
    expect(tickLabel(5_000_000, 만)).not.toContain("₩");
    expect(amount(5_000_000, "KRW", 만)).toContain("₩");
  });

  it("접을 수 없는 값은 접지 않는다", () => {
    // 5,000원을 `0.5만` 이라고 쓰면 접은 것이 아니라 읽기 어렵게 만든 것입니다.
    expect(amount(5_000, "KRW", 만)).toBe("₩5,000");
  });

  it("한 자리 수만 소수 한 자리까지", () => {
    // `2.5억` 은 `250,000,000` 보다 읽히고 `3억` 보다 정확합니다.
    expect(amount(250_000_000, "KRW")).toBe("₩2.5억");
    expect(amount(1_200_000, "KRW", 만)).toBe("₩120만");
  });

  it("달러에는 억·만이 없다", () => {
    const usd = scaleFor(4_200, "USD");
    expect(amount(4_200, "USD", usd)).toBe("$4,200");
    expect(amount(-940, "USD", usd)).toBe("-$940");
    expect(tickLabel(2_000, usd)).toBe("2,000");
  });
});
