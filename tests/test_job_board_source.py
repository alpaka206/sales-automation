"""Tests for job board source with mocked Google CSE."""

from __future__ import annotations

import httpx
import pytest
import respx

from src.agents.outbound.sources.job_board import (
    JobBoardSource,
    _extract_company_from_title,
    _extract_domain_from_text,
)
from src.integrations.google_search import BASE_URL, GoogleSearchClient


def test_extract_company_from_title() -> None:
    assert _extract_company_from_title("(주)뷰티클리닉 채용공고") == "뷰티클리닉"
    assert _extract_company_from_title("㈜메디컬랩 마케팅 담당자") == "메디컬랩"
    assert _extract_company_from_title("아름다운성형외과 - SNS 마케터 모집") == "아름다운성형외과"
    assert _extract_company_from_title("Something short") is None


def test_extract_domain_from_text() -> None:
    text = "회사 홈페이지: https://www.beautyclinic.co.kr 방문해주세요"
    assert _extract_domain_from_text(text) == "beautyclinic.co.kr"

    text_social = "https://www.facebook.com/page and https://instagram.com/page"
    assert _extract_domain_from_text(text_social) is None

    assert _extract_domain_from_text("no urls here") is None


FAKE_SARAMIN_RESULTS = {
    "items": [
        {
            "title": "(주)뷰티클리닉 채용 - SNS 마케터",
            "snippet": "성형외과 SNS 마케팅 경력자 우대...",
            "link": "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=12345",
        },
        {
            "title": "아름다운성형외과 - 마케팅 담당자 채용",
            "snippet": "병원 마케팅 전략 수립...",
            "link": "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=67890",
        },
    ]
}

FAKE_JOBKOREA_RESULTS = {
    "items": [
        {
            "title": "㈜메디컬랩 마케팅 채용",
            "snippet": "의료 마케팅 담당자 모집...",
            "link": "https://www.jobkorea.co.kr/Recruit/GI_Read/12345",
        },
    ]
}

FAKE_PAGE_CLINIC = """
<html><body>
<h1>뷰티클리닉</h1>
<p>홈페이지: https://www.beautyclinic.co.kr</p>
<footer>문의: info@beautyclinic.co.kr | 02-123-4567</footer>
</body></html>
"""

FAKE_PAGE_HOSPITAL = """
<html><body>
<h1>아름다운성형외과</h1>
<p>Contact: contact@beautiful-ps.kr</p>
</body></html>
"""

FAKE_PAGE_MEDLAB = """
<html><body>
<h1>메디컬랩</h1>
<p>Visit us at 서울시 강남구</p>
</body></html>
"""


@respx.mock
def test_job_board_discover() -> None:
    respx.get(BASE_URL).mock(
        side_effect=[
            httpx.Response(200, json=FAKE_SARAMIN_RESULTS),
            httpx.Response(200, json=FAKE_JOBKOREA_RESULTS),
        ]
    )

    respx.get("https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=12345").mock(
        return_value=httpx.Response(
            200, text=FAKE_PAGE_CLINIC, headers={"content-type": "text/html"}
        )
    )
    respx.get("https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=67890").mock(
        return_value=httpx.Response(
            200, text=FAKE_PAGE_HOSPITAL, headers={"content-type": "text/html"}
        )
    )
    respx.get("https://www.jobkorea.co.kr/Recruit/GI_Read/12345").mock(
        return_value=httpx.Response(
            200, text=FAKE_PAGE_MEDLAB, headers={"content-type": "text/html"}
        )
    )

    client = GoogleSearchClient(api_key="test-key", cse_id="test-cx")
    source = JobBoardSource(client=client)
    results = source.discover({"keyword": "성형외과 마케팅"})

    assert len(results) == 3
    assert results[0].company == "뷰티클리닉"
    assert results[0].email == "info@beautyclinic.co.kr"
    assert results[0].domain == "beautyclinic.co.kr"
    assert results[0].source == "job_board"
    assert results[0].country == "KR"
    assert results[0].extra["job_site"] == "www.saramin.co.kr"

    assert results[1].company == "아름다운성형외과"
    assert results[1].email == "contact@beautiful-ps.kr"

    assert results[2].company == "메디컬랩"
    assert results[2].email is None


@respx.mock
def test_job_board_deduplicates_links() -> None:
    dup_results = {
        "items": [
            {"title": "Same Job", "snippet": "...", "link": "https://www.saramin.co.kr/same"},
            {"title": "Same Job Copy", "snippet": "...", "link": "https://www.saramin.co.kr/same"},
        ]
    }

    respx.get(BASE_URL).mock(
        return_value=httpx.Response(200, json=dup_results)
    )
    respx.get("https://www.saramin.co.kr/same").mock(
        return_value=httpx.Response(
            200, text="<html><body>No email</body></html>",
            headers={"content-type": "text/html"},
        )
    )

    client = GoogleSearchClient(api_key="test-key", cse_id="test-cx")
    source = JobBoardSource(client=client)
    results = source.discover({"keyword": "test", "sites": "saramin.co.kr"})

    assert len(results) == 1


def test_job_board_no_config() -> None:
    source = JobBoardSource(client=None)
    source.client = None
    results = source.discover({"keyword": "test"})
    assert results == []


def test_job_board_requires_keyword() -> None:
    client = GoogleSearchClient(api_key="test-key", cse_id="test-cx")
    source = JobBoardSource(client=client)
    with pytest.raises(ValueError, match="keyword"):
        source.discover({})


@respx.mock
def test_job_board_custom_sites() -> None:
    respx.get(BASE_URL).mock(
        return_value=httpx.Response(200, json={"items": []})
    )

    client = GoogleSearchClient(api_key="test-key", cse_id="test-cx")
    source = JobBoardSource(client=client)
    results = source.discover({"keyword": "개발자", "sites": "wanted.co.kr"})

    assert len(results) == 0
    assert respx.calls.call_count == 1


@respx.mock
def test_job_board_cse_failure_graceful() -> None:
    respx.get(BASE_URL).mock(
        side_effect=[
            httpx.Response(500, text="Server Error"),
            httpx.Response(200, json={"items": []}),
        ]
    )

    client = GoogleSearchClient(api_key="test-key", cse_id="test-cx")
    source = JobBoardSource(client=client)
    results = source.discover({"keyword": "test", "sites": "saramin.co.kr,jobkorea.co.kr"})

    assert len(results) == 0
