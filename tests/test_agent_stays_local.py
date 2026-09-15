"""로컬 데이터(스냅샷)는 **서버로 보낼 일이 절대 없다** (2026-09-15 운영자 지시).

에이전트가 답한 값은 브라우저 화면에서 끝난다. 그 약속의 코드상 형태는 두 가지다:

1. 에이전트를 부르는 프론트 모듈들은 우리 API 클라이언트(`lib/api.ts`)를 **import 하지
   않고**, 서버 경로로 `fetch` 하지 않는다. 판정(`usage.ts`)은 순수 함수다.
2. 에이전트(Go)는 `127.0.0.1` 에만 바인딩하고, 바깥으로 HTTP 를 보내는 코드가 없다.

**예외 하나**: `won/reconcile.ts` (같은 날 운영자 결정 — 「결제가 된 게 들어왔으면 바뀌는
건 상관없지, 완전 자동으로」). 스냅샷 근거로 우리 회차를 완료 처리한다. 건너가는 것은
회차의 완료 여부·날짜·근거 메모뿐이고, 부를 수 있는 경로와 보낼 수 있는 칸을 아래에서
목록으로 고정한다(경로 둘 · 칸 다섯) — 그 밖의 것을 보내기 시작하면 빨개진다.

여기서 걸리면 「어느 화면이 스냅샷 값을 서버로 보내기 시작했다」는 뜻이다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / "frontend" / "src"

# 에이전트에서 받은 값을 들고 있는 모듈들. 새로 생기면 여기 더한다.
AGENT_SIDE = (
    FRONT / "lib" / "agent.ts",
    FRONT / "screens" / "won" / "usage.ts",
    FRONT / "screens" / "won" / "useUsage.ts",
    FRONT / "screens" / "won" / "UsageBits.tsx",
    FRONT / "screens" / "won" / "WonUsageSections.tsx",
)


def test_agent_side_modules_never_touch_our_api():
    for path in AGENT_SIDE:
        text = path.read_text(encoding="utf-8")
        # 주석에서 그 파일을 **언급**하는 것은 된다 — import 문만 본다.
        imports = re.findall(r'^\s*import.*?from\s+"([^"]+)"', text, flags=re.M | re.S)
        assert not any(target.endswith("lib/api") for target in imports), f"{path.name} 가 우리 API 클라이언트를 import 합니다"
        for needle in ("getJSON(", "postForm(", 'fetch("/', "fetch('/", "fetch(`/"):
            assert needle not in text, f"{path.name} 가 서버 경로를 부릅니다: {needle}"


def test_the_only_fetch_target_is_loopback():
    text = (FRONT / "lib" / "agent.ts").read_text(encoding="utf-8")
    targets = re.findall(r"fetch\(\s*`([^`]+)`", text)
    assert targets, "agent.ts 에 fetch 가 없습니다 — 파일이 바뀌었으면 이 테스트도 고쳐야 합니다"
    for target in targets:
        assert target.startswith("http://127.0.0.1:"), f"루프백이 아닌 곳을 부릅니다: {target}"


def test_the_agent_binds_loopback_only_and_never_calls_out():
    agent = ROOT / "agent"
    sources = [p for p in agent.glob("*.go") if not p.name.endswith("_test.go")]
    assert sources, "agent/ 에 Go 소스가 없습니다"
    joined = "\n".join(p.read_text(encoding="utf-8") for p in sources)
    assert 'net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", p))' in joined
    # 주석의 「0.0.0.0 이면 …」은 된다 — 문자열 리터럴로 쓰인 것만 잡는다.
    assert '"0.0.0.0' not in joined, "0.0.0.0 바인딩이 생겼습니다"
    for needle in ("http.Get(", "http.Post(", "http.NewRequest(", "http.Client{", "net.Dial("):
        assert needle not in joined, f"에이전트가 바깥으로 요청을 보냅니다: {needle}"
    # 배포되는 바이너리에 토큰을 넣지 않는다 — git 접근은 각자 PC 의 자격증명이다.
    assert not re.search(r"ghp_[A-Za-z0-9]{20,}|github_pat_", joined)


# 유일하게 허용된 건너감 — 경로 둘, 칸 넷.
RECONCILE = FRONT / "screens" / "won" / "reconcile.ts"
ALLOWED_ROUTES = ("/won-customers/credits/", "/won-customers/payments/")
ALLOWED_FIELDS = {"done", "granted_by", "memo", "paid_on", "note"}


def test_reconcile_writes_only_completion_flags_to_two_routes():
    text = RECONCILE.read_text(encoding="utf-8")
    calls = re.findall(r"postForm\(\s*`([^`]+)`\s*,\s*\{([^}]*)\}", text)
    assert calls, "reconcile.ts 에 postForm 이 없습니다 — 파일이 바뀌었으면 이 테스트도 고쳐야 합니다"
    for route, body in calls:
        assert any(route.startswith(r) for r in ALLOWED_ROUTES), f"허용되지 않은 경로: {route}"
        fields = {part.split(":")[0].strip() for part in body.split(",") if part.strip()}
        assert fields <= ALLOWED_FIELDS, f"허용되지 않은 칸을 보냅니다: {fields - ALLOWED_FIELDS}"
