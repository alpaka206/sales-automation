r"""워크북의 수주 고객 탭을 DB 로 채운다 (CLI). 로직은 ``src/agents/sheet_to_db.py``.

    .\.venv\Scripts\python.exe -m scripts.import_sheet_to_db          # 무엇이 들어갈지만
    .\.venv\Scripts\python.exe -m scripts.import_sheet_to_db --write  # 실제로 넣는다
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.tls import use_os_trust_store  # noqa: E402

use_os_trust_store()

from src.agents.sheet_to_db import import_from_sheet  # noqa: E402

if __name__ == "__main__":
    result = import_from_sheet(write="--write" in sys.argv)
    print(" · ".join(f"{k} {v}" for k, v in result.items()))
    if result.get("dry_run"):
        print("넣으려면 --write 를 붙이세요.")
