import { describe, expect, it } from "vitest";
import { stripQuotedReply } from "../src/lib/format";

// 운영자가 본 그대로의 모양입니다 (2026-09-01, 티켓 331342615228 의 회신).
// 답장 아래에 붙은 인용문이 **바로 위 줄에 이미 서 있는 그 문의**입니다.
const REPLY = `Namaste 😊
Perso Dubbing ka use karne ke liye dhanyavaad.

On Mon, Aug 31, 2026 at 6:55 PM, piyushdigitechhealthcare@gmail.com wrote:
>Email: piyushdigitechhealthcare@gmail.com
>Inquiry Details: muje apne hisab se customise krvane h`;

describe("stripQuotedReply", () => {
  it("답장이 새로 한 말만 남긴다", () => {
    expect(stripQuotedReply(REPLY)).toBe(
      "Namaste 😊\nPerso Dubbing ka use karne ke liye dhanyavaad.",
    );
  });

  it("인용문이 없으면 그대로 둔다", () => {
    expect(stripQuotedReply("한 줄짜리 문의입니다.")).toBe("한 줄짜리 문의입니다.");
  });

  it("본문이 인용문뿐이면 자르지 않는다", () => {
    // 전달 메일이 이렇습니다. 잘라 버리면 그 줄이 화면에서 통째로 빈칸이 됩니다 —
    // 중복이 빈칸보다 낫습니다.
    const forwarded = "> 원문입니다\n> 두 번째 줄";
    expect(stripQuotedReply(forwarded)).toBe(forwarded);
  });

  it("아웃룩 구분선과 한국어 클라이언트도 자른다", () => {
    expect(stripQuotedReply("답장입니다\n________________\n원문")).toBe("답장입니다");
    expect(stripQuotedReply("답장입니다\n홍길동님이 작성:\n원문")).toBe("답장입니다");
  });
});
