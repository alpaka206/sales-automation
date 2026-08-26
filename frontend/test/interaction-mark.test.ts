import { describe, expect, it } from "vitest";

import { directionMark, interactionMark } from "../src/ui/InteractionForm";

/**
 * 티켓 기록의 말은 셋으로 갈린다 (2026-08-26 운영자 지시):
 *
 *   고객이 보낸 것        → 「문의 접수」
 *   우리가 보낸 **첫** 답 → 「문의 회신」
 *   그 뒤로 오간 것       → 채널 + 방향 (「이메일 발송」 · 「왓츠앱 수신」 · 「전화 주고받음」)
 *
 * 앞의 둘은 이 티켓에서 한 번씩 일어나는 사건이라 이름이 있다. 셋을 다 「문의 접수/회신」로
 * 적으면 그 두 사건이 어느 줄인지 목록에서 사라진다 — 왓츠앱으로 받은 것과 전화로 통화한
 * 것이 화면에서 똑같이 「문의 접수」였다.
 */
describe("티켓 기록의 라벨", () => {
  it("한 번씩 일어나는 두 사건은 이름을 지킨다", () => {
    expect(directionMark("inbound").label).toBe("문의 접수");
    expect(directionMark("outgoing").label).toBe("문의 회신");
  });

  it("그 뒤로 오간 것은 고를 때 쓴 말 그대로다", () => {
    expect(interactionMark("email", "outgoing").label).toBe("이메일 발송");
    expect(interactionMark("whatsapp", "inbound").label).toBe("WhatsApp 수신");
    expect(interactionMark("phone", "note").label).toBe("전화 주고받음");
    expect(interactionMark("meeting", "note").label).toBe("미팅 주고받음");
  });

  it("채널마다 앞에 서는 모양이 다르다", () => {
    const icons = ["email", "whatsapp", "phone", "meeting"].map(
      (channel) => interactionMark(channel, "note").icon,
    );
    expect(new Set(icons).size).toBe(icons.length);
    expect(interactionMark("email", "note").icon).toBe("mail");
    expect(interactionMark("phone", "note").icon).toBe("phone");
  });

  it("방향의 색은 그대로다 — 누가 보낸 것인가는 글자보다 색이 먼저 말한다", () => {
    expect(interactionMark("whatsapp", "inbound").tone).toBe(directionMark("inbound").tone);
    expect(interactionMark("whatsapp", "outgoing").tone).toBe(directionMark("outgoing").tone);
  });

  it("옛 행이 들고 있는 별칭도 읽는다", () => {
    // `incoming`/`outbound` 는 예전에 쓰이던 값이다. 새 말로 바꾸지 않았으므로 여전히 온다.
    expect(interactionMark("email", "incoming").label).toBe("이메일 수신");
    expect(interactionMark("email", "outbound").label).toBe("이메일 발송");
  });
});
