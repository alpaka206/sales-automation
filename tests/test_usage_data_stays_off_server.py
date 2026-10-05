"""사용 데이터(스냅샷)는 **우리 서버를 한 바이트도 안 지난다** (2026-09-15 운영자 지시: 「절대 없어. 명심해」).

2026-10-01 에 길이 바뀌었다. PC 마다 깔던 로컬 에이전트(127.0.0.1 HTTP 서버 · 자동 업데이트)를 걷고:

    스냅샷(비공개) → 매일 09:15 KST, 비공개 가공 레포의 GitHub Actions 가 네트워크 없이(`unshare -n`)
                     가공해 그 레포의 `data` 브랜치에 JSON 을 한 커밋으로 올린다
    브라우저      → /api/ui/usage-source 로 {repo, token} 만 받아 GitHub 에서 직접 받고, 계약의
                     스페이스로 합치는 일도 그 안에서 한다

서버가 하는 일은 설정 두 칸을 로그인한 브라우저에 건네는 것뿐이다. 그 약속의 코드상 모양:

1. 데이터를 들고 있는 프론트 모듈은 우리 API 클라이언트(`lib/api.ts`)를 import 하지 않고 서버 경로를
   부르지 않는다. 합치기(`usageMerge.ts`)와 판정(`usage.ts`)은 순수 함수다. 거꾸로 서버에서 열쇠를
   받는 모듈(`usageSource.ts`)은 데이터 모듈을 import 하지 않는다 — 우리 서버와 말하는 모듈과
   데이터를 쥔 모듈이 갈린다.
2. 그 모듈들이 `fetch` 하는 곳은 `https://api.github.com/repos/` 뿐이다(`usageData.ts` 한 곳).
3. 서버(`src/` 파이썬)에는 GitHub 를 부르는 주소가 없고, 토큰 이름은 설정과 그것을 건네는 라우트
   두 곳에만 있다 — 서버가 데이터를 받아 오기 시작하면 여기서 빨개진다.
4. 가공기(Go, `agent/`)에는 네트워크 코드가 없다 — HTTP 도, git 으로 바깥에 닿는 명령도.
5. 워크플로 견본은 운영자가 고른 커밋만 돌리고, 스냅샷을 받기 전에 빌드를 끝내며(저장소 테스트는 안
   돌린다), 가공기를 네트워크 · 로컬 데몬 소켓 · 권한 없이 가두고, 스냅샷 저장소에는 받기만 하며(아래),
   원본을 지운 뒤 새 폴더에서 **자기 레포의** `data` 브랜치로만 민다 — 이 저장소는 공개라 여기로 밀면
   그대로 공개다.
6. 가공기·화면·배포 설정(`render.yaml` · `.env.example`)에 토큰처럼 생긴 글자가 없다 — 토큰은 Render
   대시보드와 가공 레포의 Actions 시크릿에만 산다.

**예외 하나**: `won/reconcile.ts` (2026-09-15 운영자 결정 — 「결제가 된 게 들어왔으면 바뀌는
건 상관없지, 완전 자동으로」). 스냅샷 근거로 우리 회차를 완료 처리한다. 건너가는 것은
회차의 완료 여부·날짜·근거 메모뿐이고, 부를 수 있는 경로와 보낼 수 있는 칸을 아래에서
목록으로 고정한다(경로 둘 · 칸 다섯) — 그 밖의 것을 보내기 시작하면 빨개진다.

여기서 걸리면 「어느 화면이나 서버 코드가 스냅샷 값을 서버로 끌어오기 시작했다」는 뜻이다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / "frontend" / "src"
AGENT = ROOT / "agent"

# 사용 데이터를 들고 있는 모듈들. 새로 생기면 여기 더한다.
DATA_SIDE = (
    FRONT / "lib" / "usageData.ts",
    FRONT / "screens" / "won" / "usageMerge.ts",
    FRONT / "screens" / "won" / "usage.ts",
    FRONT / "screens" / "won" / "useUsage.ts",
    FRONT / "screens" / "won" / "UsageBits.tsx",
    FRONT / "screens" / "won" / "WonUsageSections.tsx",
)
# 서버에서 열쇠만 받는 모듈 — `lib/api` 를 쓰는 대신 데이터를 손에 쥐지 않는다.
SOURCE = FRONT / "lib" / "usageSource.ts"
API = FRONT / "lib" / "api"
GITHUB_API = "`https://api.github.com/repos/"


def _code(text: str, comments: tuple[str, ...] = ("//", "/*", "*")) -> str:
    """주석 줄을 뺀 본문 — 설명에서 그 이름을 **언급**하는 것은 된다."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(comments))


def _imports(path: Path) -> set[Path]:
    """그 파일이 import 하는 우리 모듈들(확장자 없이). `lib/` 안에서는 `./api` 로 적히므로 경로로 푼다."""
    specs = re.findall(
        r"""(?:^\s*(?:import|export)\b[^;]*?\bfrom\s+|\bimport\(\s*)["']([^"']+)["']""",
        _code(path.read_text(encoding="utf-8")),
        flags=re.M,
    )
    return {
        (path.parent / re.sub(r"\.(?:tsx?|js)$", "", spec)).resolve()
        for spec in specs
        if spec.startswith(".")
    }


def test_data_side_modules_never_touch_our_api():
    for path in DATA_SIDE:
        assert API.resolve() not in _imports(path), f"{path.name} 가 우리 API 클라이언트를 import 합니다"
        code = _code(path.read_text(encoding="utf-8"))
        for needle in ("getJSON(", "postForm(", 'fetch("/', "fetch('/", "fetch(`/",
                       "sendBeacon(", "XMLHttpRequest", "WebSocket(", "EventSource("):
            assert needle not in code, f"{path.name} 가 서버 경로를 부릅니다: {needle}"
    data = {path.with_suffix("").resolve() for path in DATA_SIDE}
    assert not data & _imports(SOURCE), "usageSource.ts 가 데이터 모듈을 import 합니다 — 열쇠와 데이터를 한 모듈이 쥡니다"
    # 옛 루프백 클라이언트는 없어야 한다 — 남아 있으면 지켜지지 않는 길이 하나 남는다.
    assert not (FRONT / "lib" / "agent.ts").exists(), "lib/agent.ts 가 아직 있습니다"


def test_the_only_fetch_target_is_the_github_api():
    targets = {}
    for path in DATA_SIDE:
        found = re.findall(r"\bfetch\(\s*([^\s,)]+)", _code(path.read_text(encoding="utf-8")))
        if found:
            targets[path.name] = found
    assert "usageData.ts" in targets, "usageData.ts 에 fetch 가 없습니다 — 파일이 바뀌었으면 이 테스트도 고쳐야 합니다"
    for name, found in targets.items():
        assert name == "usageData.ts", f"{name} 가 직접 fetch 합니다 — 받는 곳은 usageData.ts 한 곳입니다"
        for target in found:
            assert target.startswith(GITHUB_API), f"GitHub API 가 아닌 곳을 부릅니다: {target}"


def test_the_server_never_fetches_usage_data():
    sources = {path: path.read_text(encoding="utf-8") for path in (ROOT / "src").rglob("*.py")}
    for path, text in sources.items():
        for needle in ("api.github.com", "githubusercontent.com"):
            assert needle not in text, f"{path.relative_to(ROOT)} 에 GitHub 주소가 있습니다: {needle}"
    holders = {path.relative_to(ROOT).as_posix() for path, text in sources.items() if "USAGE_DATA_TOKEN" in text}
    # 설정과 그것을 건네는 라우트 — 그 밖에서 토큰을 읽는다면 서버가 그것으로 무언가를 하기 시작한 것이다.
    assert holders == {"src/common/config.py", "src/api/routes/ui_api.py"}, holders


# 가공기가 바깥에 닿는 길. 워크플로가 이름공간째 끊지만(`unshare -n`, 아래), 코드에도 없어야 한다.
NETWORK = ("http.Get(", "http.Post(", "http.PostForm(", "http.Head(", "http.NewRequest", "http.Client{",
           "http.DefaultClient", "http.ListenAndServe", "net.Dial", "net.Listen")


def test_the_exporter_has_no_network_code():
    # 테스트 파일도 본다 — `go test` 는 CI 에서 돌고, 그 코드도 이 저장소에서 바깥에 닿을 이유가 없다.
    sources = list(AGENT.glob("*.go"))
    assert sources, "agent/ 에 Go 소스가 없습니다"
    code = "\n".join(_code(path.read_text(encoding="utf-8"), ("//",)) for path in sources)
    found = [needle for needle in NETWORK if re.search(r"\b" + re.escape(needle), code)]
    assert not found, f"가공기에 네트워크 코드가 있습니다: {found}"
    # git 도 바깥에 닿는 명령은 없다 — 스냅샷은 워크플로가 체크아웃해 준다(옛 에이전트는 PC 마다 fetch 했다).
    calls = re.findall(r'[(,]\s*"(fetch|pull|push|clone|ls-remote)"', code)
    assert not calls, f"가공기가 git 으로 바깥에 닿습니다: {calls}"


WORKFLOW = AGENT / "export" / "perso-usage-data.yml"
OWN_REPO = r"\$\{\{\s*github\.repository\s*\}\}"
PUSH = r"\bgit\s+(?:-\S+\s+(?:[^-\s]\S*\s+)?)*push\b"  # `git push …` · `git -c x=y push …` — 커밋 메시지 속 「push」 는 아니다


def _script(step: dict) -> str:
    """그 단계의 셸 — 줄 잇기(`\\`)를 풀고 주석 줄을 뺀다."""
    text = re.sub(r"\\\n\s*", " ", str(step.get("run", "")))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#"))


SNAPSHOT_REMOTE = "https://github.com/est-perso/perso-data-snapshot.git"
# 스냅샷 토큰을 쥔 단계가 부를 수 있는 git 명령 — 이 러너 안의 일과 받기(fetch)뿐이다.
READ_ONLY_GIT = {"init", "remote", "config", "fetch", "log", "checkout"}


def _assert_the_snapshot_is_only_read(step: dict) -> None:
    """스냅샷 저장소에는 **받기만** 한다 — 회사 계정이 그 저장소에 쓰기 권한을 가져도 (2026-10-02 운영자:
    「쓰기 권한이 있더라도 스냅샷 저장소에는 아무 문제 없도록 해줘」).

    classic 토큰(`repo`)은 계정이 쓸 수 있는 곳이면 쓰기까지 여는 열쇠라, 「안 쓴다」는 토큰의 권한이 아니라
    그 토큰을 쥔 **단계 하나**가 지킨다. 그래서 그 단계가 할 수 있는 일을 글자로 묶는다:
    액션에 안 넘기고 · 스크립트보다 먼저 도는 것(shell · 다른 env)이 없고 · 받기 말고 바깥에 닿는 도구가 없고 · git 은
    빈 환경과 설정 없이 돌고 · 토큰은 명령줄 -c 로만 가고(.git 에 안 남는다) · 이 클론의 push 는 없는 주소로 막혀 있고 ·
    git 명령은 받기와 이 러너 안의 일뿐이다.
    """
    assert "uses" not in step, "스냅샷 토큰을 액션에 넘깁니다 — 러너의 git 으로 직접 받습니다"
    # 아래는 전부 `run` 을 읽는다 — 그 앞에 도는 것이 있으면 소용없다. `shell` 은 스크립트를 무엇으로 돌릴지 바꾸고, env 의
    # `BASH_ENV` · `BASH_FUNC_<이름>%%` 는 셸이 스크립트 첫 줄보다 먼저 실행한다(토큰이 아직 환경에 있을 때).
    assert set(step) <= {"name", "env", "run"} and set(step.get("env") or {}) == {"SNAPSHOT_TOKEN"}, \
        f"스냅샷 토큰을 쥔 단계에 run 말고 도는 것이 있습니다: {sorted(step)} · env {sorted(step.get('env') or {})}"
    script = _script(step)
    lines = script.splitlines()
    for needle in (r"\bcurl\b", r"\bwget\b", r"\bgh\s", r"api\.github\.com", r"\bpython", r"\bnode\b",
                   r"\bsudo\b", r"\bssh\b", PUSH,
                   # git 이 다른 명령을 대신 돌리게 하는 옵션 · 설정 — 받기를 다른 일로 바꿀 수 있다.
                   r"--upload-pack", r"--receive-pack", r"\s-u\s", r"--exec\b", r"core\.sshCommand", r"\balias\.",
                   r"\bprotocol\.", r"\bext::", r"\bcredential\.", r"--mirror"):
        assert not re.search(needle, script), f"스냅샷 토큰을 쥔 단계가 받기 말고 다른 것을 합니다: {needle}"

    # git 은 빈 환경 · 전역/시스템 설정 없이 — 앞 단계가 남긴 insteadOf · 프록시 · 인증서 · GIT_* 가 토큰을 딴 데로
    # 보내거나 명령을 끼워 넣지 못한다. 그 감싸개 밖에서 부르는 git 은 없다(아래 호출은 전부 `git` 이름으로 간다).
    wrapper = re.search(r"\bgit\(\)\s*\{(.*?)\}", script, re.S)
    assert wrapper, "git 을 감싸는 함수가 없습니다"
    for needle in ("env -i", "GIT_CONFIG_GLOBAL=/dev/null", "GIT_CONFIG_NOSYSTEM=1", "/usr/bin/git"):
        assert needle in wrapper.group(1), f"git 이 앞 단계의 환경 · 설정을 읽습니다: {needle} 없음"

    # 토큰을 만지기 전에 PATH 를 시스템 것으로 — 앞 단계가 GITHUB_PATH 로 끼운 가짜 base64 · env 가 토큰을 못 본다.
    # 그리고 snapshot/ 이 이미 있으면 멈춘다 — 미리 심어 둔 .git 설정(insteadOf · 훅)을 그대로 쓰지 않는다.
    first_token = next(i for i, line in enumerate(lines) if "SNAPSHOT_TOKEN" in line)
    assert "export PATH=/usr/bin:/bin" in lines[:first_token], "토큰을 만지기 전에 PATH 를 되돌리지 않습니다"
    assert any(re.search(r"\[ -e snapshot \].*exit 1", line) for line in lines[:first_token]), \
        "snapshot/ 이 이미 있을 때 멈추지 않습니다"

    # 토큰 변수는 auth 머리글을 만드는 데만 쓰고 바로 지우고, 그 머리글은 그 명령의 -c 로만 준다.
    token_lines = [line for line in lines if "SNAPSHOT_TOKEN" in line]
    assert len(token_lines) == 2 and "unset SNAPSHOT_TOKEN" in token_lines, token_lines
    # git 에 주는 -c 는 그 머리글 하나뿐이다 — url.*.insteadOf · http.proxy · http.sslVerify 같은 것으로 토큰이 실린
    # 요청을 다른 곳으로 돌리지 못한다.
    for option in re.findall(r"(?<![\w-])-c\s+(\S+)", script):
        assert option == 'http.extraheader="$auth"', f"토큰 머리글 말고 다른 -c 를 줍니다: {option}"
    for line in lines:
        if "$auth" in line and not line.startswith(("auth=", 'echo "::add-mask::$auth"')):
            assert re.search(r'\s-c\s+http\.extraheader="\$auth"\s', line), f"토큰이 명령줄 -c 밖으로 갑니다: {line}"

    # 이 클론의 push 는 없는 주소로 막혀 있다 — 무엇이 여기서 push 해도 github.com 에 안 닿는다.
    assert re.search(r"\bconfig\s+remote\.origin\.pushurl\s+no-push://", script), "push 주소를 막지 않았습니다"
    for scheme in ("https://", "http://"):
        assert re.search(rf"\.pushInsteadOf\s+{re.escape(scheme)}(?:\s|$)", script, re.M), f"{scheme} push 를 막지 않았습니다"

    # 부르는 git 명령은 받기와 이 러너 안의 일뿐이고, 원격은 스냅샷 저장소 하나다.
    calls = re.findall(r'(?<![/\w-])git((?:\s+-[Cc]\s+(?:"[^"]*"|\S+))*)\s+([a-z][a-z-]*)([^\n]*)', script)
    assert calls, "git 명령이 없습니다 — 단계가 바뀌었으면 이 테스트도 고쳐야 합니다"
    for _options, sub, rest in calls:
        assert sub in READ_ONLY_GIT, f"스냅샷 토큰을 쥔 단계의 git {sub}"
        if sub == "remote":
            assert rest.split() == ["add", "origin", SNAPSHOT_REMOTE], f"원격이 스냅샷 저장소가 아닙니다: {rest}"
        if sub == "fetch":
            # 받는 곳은 origin(= 스냅샷 저장소) 하나 — 주소를 직접 적으면 토큰 머리글이 그 주소로 간다.
            where = [word for word in rest.split() if not word.startswith("-")]
            assert where and where[0] == "origin", f"origin 이 아닌 곳에서 받습니다: {rest}"
        if sub == "config":
            key = next(word for word in rest.split() if not word.startswith("--"))
            assert key == "remote.origin.pushurl" or re.fullmatch(r"url\.no-push://\S+\.pushInsteadOf", key), \
                f"push 막기 말고 다른 설정을 씁니다: {key}"


def test_the_workflow_isolates_the_exporter_and_pushes_only_its_own_data_branch():
    """스냅샷을 지키는 것은 「코드에 네트워크가 없다」(위)만이 아니다 — 워크플로가 이 넷을 지킨다:

    1. 돌리는 것은 사람이 고른 커밋이다. 공개 저장소 main 에 커밋 하나가 들어가는 것(계정 탈취 · 잘못된
       병합)만으로 다음 날 아침 그 코드가 스냅샷 옆에서 돌면 안 된다.
    2. 스냅샷이 디스크에 있는 동안 네트워크를 가진 채 도는 저장소 코드가 없다 — `go test` 는 저장소 코드를
       실행하므로 이 잡에서 안 돌리고(CI 의 exporter 잡이 돌린다), 빌드는 스냅샷을 받기 전에 끝낸다.
    3. 가공기는 네트워크 · 로컬 데몬 소켓 · 권한 없이, 러너가 아닌 사용자로 돈다 — sudo 로 다시 root 가 되거나
       docker 소켓 · resolved 로 빠져나가거나 다음 단계가 실행할 파일을 고치지 못한다.
    4. 미는 단계는 원본을 먼저 지우고, 가공기가 쓴 폴더가 아니라 새 폴더에서 훅 없이 **자기 레포의** `data` 로만
       민다 — 이 저장소는 공개라 여기로 밀면 그대로 공개다.
    """
    import yaml  # uvicorn[standard] 가 깐다

    flow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # 이 테스트는 단계(steps)를 읽는다 — 그래서 워크플로와 잡에는 단계 밖에서 도는 것이 없어야 한다. 잡 단위 `uses`(재사용
    # 워크플로)는 `secrets: inherit` 로 스냅샷 토큰을 이 테스트가 안 읽는 코드에 넘기고, `container` · `services` 는 남의
    # 이미지를 돌리고, 워크플로 · 잡 단위 `env`(BASH_ENV 등) · `defaults`(shell)는 토큰을 쥔 단계의 셸에 스크립트보다 먼저
    # 닿는다. 첫 판은 단계만 봐서 `secrets: inherit` 를 단 잡이 초록으로 지나갔다(2026-10-02 검토).
    assert set(flow) <= {"name", "on", True, "permissions", "concurrency", "jobs"}, f"워크플로에 단계 밖의 것: {set(flow)}"
    for name, job in flow["jobs"].items():
        assert set(job) <= {"runs-on", "timeout-minutes", "steps"}, f"잡 {name} 에 단계 밖의 것: {set(job)}"
    steps = [step for job in flow["jobs"].values() for step in job.get("steps", [])]
    uses = [str(step["uses"]) for step in steps if "uses" in step]
    # 액션은 GitHub 가 만든 것(`actions/…`)만, **커밋 번호로** — 태그는 옮겨질 수 있고, 그 액션들은 이 잡에서 스냅샷
    # 토큰보다 먼저 돌아 뒤 단계의 환경을 건드릴 수 있다.
    assert uses and all(re.fullmatch(r"actions/[\w.-]+@[0-9a-f]{40}", name) for name in uses), uses
    # 방아쇠는 예약과 손 실행뿐 — pull_request · push 처럼 남이 올린 코드로 도는 방아쇠는 그 코드에 시크릿을 쥐여 준다.
    triggers = flow.get("on", flow.get(True))  # YAML 1.1 은 `on` 을 참(True)으로 읽는다
    assert triggers and set(triggers) <= {"schedule", "workflow_dispatch"}, triggers
    # 시크릿은 어떤 액션에도 안 넘긴다.
    for step in steps:
        if "uses" in step:
            assert "secrets." not in str(step.get("with", "")) + str(step.get("env", "")), f"액션에 시크릿을 넘깁니다: {step}"
    [code] = [step for step in steps if str(step.get("uses", "")).startswith("actions/checkout@")]
    # 남기면 그 자격증명이 .git/config 에 앉아 뒤의 모든 단계 — 가공기까지 — 가 쥔다.
    assert (code.get("with") or {}).get("persist-credentials") in (False, "false"), code
    # 시크릿은 워크플로 전체에서 **한 번**, 그 단계의 env 에만 — `run` 에 `${{ secrets… }}` 로 박거나 잡 · 워크플로 단위
    # env 에 두면 그 단계 밖의 코드도 쥔다. 줄이 아니라 **읽어 들인 값**에서 표현식을 센다 — GitHub 는 값 안의 `${{ }}` 를
    # 전부 푼다(`run` 안의 셸 주석 줄도. 안 푸는 것은 YAML 주석뿐이다). 점 없는 꼴(`secrets['…']` · `toJSON(secrets)`)도,
    # 대소문자도 안 가린다 — GitHub 가 `SECRETS.…` 도 받는다. 표현식의 끝은 GitHub 처럼 **따옴표 밖의** `}}` 다(runner 의
    # TemplateReader) — 처음 나온 `}}` 에서 끊으면 `format('}}', toJSON(secrets))` 의 `secrets` 를 못 본다.
    expressions = re.findall(r"\$\{\{((?:'[^']*'|[^'])*?)\}\}", str(flow))
    secret_uses = [e.strip() for e in expressions if re.search(r"\bsecrets\b", e, re.I)]
    assert secret_uses == ["secrets.SNAPSHOT_TOKEN"], secret_uses
    holders = [step for step in steps if "SNAPSHOT_TOKEN" in str(step)]
    assert len(holders) == 1, "스냅샷 토큰을 쥐는 단계는 하나입니다"
    [snapshot] = holders
    assert (snapshot.get("env") or {}).get("SNAPSHOT_TOKEN") == "${{ secrets.SNAPSHOT_TOKEN }}", snapshot
    at = steps.index(snapshot)
    _assert_the_snapshot_is_only_read(snapshot)

    # 1. 고른 커밋 — 비운 ref 는 checkout 이 기본 브랜치로 읽으므로, 그 앞에서 40자 sha 인지 본다.
    pin = r"\$\{\{\s*vars\.EXPORTER_COMMIT\s*\}\}"
    assert re.fullmatch(pin, str(code["with"].get("ref", ""))), f"가공 코드가 고른 커밋에 묶이지 않았습니다: {code}"
    guard = [i for i, step in enumerate(steps[: steps.index(code)])
             if re.search(pin, str(step.get("env", ""))) and "{40}" in _script(step)]
    assert guard, "가공 코드를 받기 전에 EXPORTER_COMMIT 이 40자 sha 인지 보는 단계가 없습니다"

    # 2. 저장소 코드를 실행하는 명령은 없고, 빌드는 스냅샷보다 먼저다. 스냅샷 뒤에 code/ 를 부르는 단계는 가공 하나.
    for step in steps:
        assert not re.search(r"\bgo\s+(?:test|vet|run|generate)\b", _script(step)), f"저장소 코드를 실행합니다: {step}"
    builds = [i for i, step in enumerate(steps) if re.search(r"\bgo\s+build\b", _script(step))]
    assert builds and max(builds) < at, "빌드가 스냅샷을 받은 뒤에 있습니다"
    after = [step for step in steps[at + 1:]
             if "code/" in _script(step) or str(step.get("working-directory", "")).startswith("code")]
    assert len(after) == 1 and "--snapshot" in _script(after[0]), "스냅샷 뒤에 code/ 를 부르는 단계는 가공 하나여야 합니다"
    [export] = after

    # 3. 가공기를 가두는 것 — 이름공간 셋, 로컬 데몬 소켓 가리기, 러너가 아닌 사용자 · 그룹 없음 · no_new_privs.
    run = _script(export)
    for needle in (r"\bunshare\b[^\n]*--net\b", r"\bunshare\b[^\n]*--mount\b", r"\bunshare\b[^\n]*--pid\b",
                   r"\bmount -t tmpfs tmpfs /run\b", r"\bsetpriv\b[^\n]*--reuid=nobody\b",
                   r"\bsetpriv\b[^\n]*--clear-groups\b", r"\bsetpriv\b[^\n]*--no-new-privs\b"):
        assert re.search(needle, run), f"가공기를 가두는 것이 빠졌습니다: {needle}"
    assert run.index("setpriv") < run.index("--snapshot"), "가공기가 권한을 내려놓기 전에 돕니다"

    # 4. 미는 단계 — 원본을 먼저 지우고, 가공기가 쓴 폴더 밖에서, 훅 없이, 자기 레포의 data 로만.
    pushers = [step for step in steps if re.search(PUSH, _script(step))]
    assert len(pushers) == 1, "data 브랜치로 미는 단계는 하나입니다"
    [pusher] = pushers
    script = _script(pusher)
    assert "rm -rf snapshot" in script and script.index("rm -rf snapshot") < re.search(PUSH, script).start(), \
        "원본(snapshot/)을 지우기 전에 네트워크가 있는 단계에서 밉니다"
    assert "working-directory" not in pusher, "가공기가 쓴 폴더를 그대로 저장소로 씁니다"
    assert (pusher.get("env") or {}).get("GIT_CONFIG_GLOBAL") == "/dev/null", "미는 git 이 전역 설정을 읽습니다"
    # 미는 열쇠는 그 잡의 `github.token` 하나다 — 자기 레포 밖은 못 건드린다. 스냅샷 토큰은 여기 없다.
    assert "secrets." not in script + str(pusher.get("env", "")), "미는 단계가 시크릿을 씁니다"
    for line in script.splitlines():
        if re.search(r"\bgit\b[^\n]*\b(?:commit|push)\b", line):
            assert "core.hooksPath=/dev/null" in line, f"훅을 끄지 않고 git 을 부릅니다: {line}"
        if re.search(PUSH, line):
            assert re.search(OWN_REPO, line), f"자기 레포가 아닌 곳으로 밉니다: {line}"
            assert re.search(r"\s(?:\S+:)?(?:refs/heads/)?data$", line), f"data 브랜치가 아닌 곳으로 밉니다: {line}"
            assert not re.search(r"--(?:all|mirror|tags)\b", line), f"data 말고도 밉니다: {line}"


TOKEN_LIKE = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_")
TEXT = {".go", ".mod", ".sum", ".md", ".yml", ".yaml", ".sha256", ".json", ".txt", ".sh", ".ts", ".tsx"}


def test_no_token_is_written_into_the_public_repo():
    """토큰은 Render 대시보드(서버)와 가공 레포의 시크릿(Actions)에만 산다. 이 저장소는 공개다."""
    files = [ROOT / "render.yaml", ROOT / ".env.example"]
    files += [path for path in AGENT.rglob("*")
              if path.suffix in TEXT and not {"dist", "bin"} & set(path.relative_to(AGENT).parts)]
    files += [path for path in FRONT.rglob("*") if path.suffix in TEXT]
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not TOKEN_LIKE.search(text), f"토큰처럼 생긴 글자가 있습니다: {path.relative_to(ROOT)}"


# 유일하게 허용된 건너감 — 경로 둘, 칸 다섯.
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
