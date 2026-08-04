"""로컬에서 서버로 정책 문서를 올리는 클라이언트.

DB 대신 서버를 상대합니다. 이유는 하나뿐이고 네트워크가 정한 것입니다:

    담당자 PC → 노션              OK
    담당자 PC → DB(5432/6543)     차단
    담당자 PC → 서버 HTTPS(443)   OK

노션을 읽을 수 있는 기계는 DB에 못 쓰고, DB에 쓸 수 있는 기계는 노션을 못 읽습니다. 열려 있는
경로는 담당자 PC에서 서버로 가는 HTTPS 하나이므로, 그 위로 나릅니다.

DB 대신 서버를 상대하는 이유: docs/정책문서-동기화-설계.md
"""

from __future__ import annotations

import logging

import httpx

from ..common.tls import use_os_trust_store

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(120.0, connect=30.0)


class PolicyPushError(RuntimeError):
    """서버에 닿지 못했거나 서버가 거절했습니다."""


class PolicyServer:
    """콘솔 서버의 정책 동기화 엔드포인트."""

    def __init__(self, base_url: str, token: str) -> None:
        if not base_url:
            raise PolicyPushError(
                "서버 주소가 없습니다. .env 의 PUBLIC_BASE_URL 을 채우거나 --server 로 지정하세요."
            )
        if not token:
            raise PolicyPushError(
                "INTERNAL_API_TOKEN 이 없습니다. 서버와 같은 값을 .env 에 넣어 주세요."
            )
        self._base = base_url.rstrip("/")
        self._headers = {"X-Internal-Token": token}
        # 사내망은 HTTPS 를 사설 루트로 재서명합니다. 브라우저는 그 루트를 믿고 파이썬은
        # certifi 만 믿어서, 같은 주소가 브라우저에서만 열립니다. 검증을 끄는 것이 아니라
        # 브라우저가 보는 저장소를 파이썬도 보게 합니다.
        use_os_trust_store()

    def sources(self) -> list[dict]:
        """서버에 등록된 노션 문서 목록. 무엇을 읽어야 하는지는 서버가 정합니다."""
        try:
            response = httpx.get(
                f"{self._base}/api/policy/sources", headers=self._headers, timeout=_TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise PolicyPushError(f"서버에 닿지 못했습니다: {exc}") from exc
        if response.status_code == 401:
            raise PolicyPushError("INTERNAL_API_TOKEN 이 서버와 다릅니다.")
        if response.status_code >= 400:
            raise PolicyPushError(f"서버가 {response.status_code} 로 응답했습니다.")
        return response.json().get("sources", [])

    def push(self, pages: list[dict]) -> dict:
        """읽은 본문을 올립니다. 어떤 행이 갱신될지는 서버의 등록부가 정합니다."""
        try:
            response = httpx.post(
                f"{self._base}/api/policy/push",
                headers=self._headers,
                json={"pages": pages},
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise PolicyPushError(f"서버에 닿지 못했습니다: {exc}") from exc
        if response.status_code >= 400:
            raise PolicyPushError(
                f"서버가 {response.status_code} 로 거절했습니다: {response.text[:200]}"
            )
        return response.json()
