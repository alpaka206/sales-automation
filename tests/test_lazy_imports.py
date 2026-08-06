"""함수 **안**에서 하는 import 도 실제로 있는 이름을 가리켜야 합니다.

모듈 맨 위의 import 는 서버가 켜질 때 다 검사됩니다 — 이름 하나가 사라지면 부팅이 실패하고
바로 압니다. 함수 안 import 는 그렇지 않습니다: **그 함수를 부르는 요청이 올 때** 터지고,
그때까지는 테스트도 부팅도 전부 초록입니다.

실제로 그랬습니다. 서명 리팩터링(0061)이 `strip_known_signature` · `strips_text_signature`
를 지웠는데 `messages.py` 의 두 함수가 계속 부르고 있었고, 이메일 미리보기와 번역하기가
500 이었습니다. 개발 서버는 리팩터링 전에 켜 둔 프로세스라 옛 모듈을 들고 있어서 멀쩡해
보였습니다 — 배포하면 그때 처음 나타났을 겁니다.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

SRC = pathlib.Path("src")


def _lazy_imports():
    """(파일, 줄, 모듈, 이름) — 함수/메서드 본문 안의 `from X import a, b`."""
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.ImportFrom) or inner.module is None:
                    continue
                # 상대 import 를 절대 경로로. `src/api/routes/x.py` 에서 `...integrations`
                # 는 `src.integrations` 입니다.
                if inner.level:
                    parts = path.with_suffix("").parts[: -inner.level]
                    module = ".".join((*parts, inner.module))
                else:
                    module = inner.module
                for alias in inner.names:
                    if alias.name != "*":
                        yield path, inner.lineno, module, alias.name


@pytest.mark.parametrize(
    "path,lineno,module,name",
    list(_lazy_imports()),
    ids=lambda value: str(value).replace("\\", "/"),
)
def test_a_lazy_import_points_at_something_that_exists(path, lineno, module, name):
    if not module.startswith("src."):
        pytest.skip("서드파티는 requirements 가 책임집니다")
    imported = importlib.import_module(module)
    if hasattr(imported, name):
        return
    # 하위 패키지일 수도 있습니다(`from src.db import migrations`) — 아직 안 불러왔으면
    # 속성으로는 안 보입니다.
    try:
        importlib.import_module(f"{module}.{name}")
    except ImportError:
        pytest.fail(f"{path}:{lineno} — {module}.{name} 가 없습니다")
