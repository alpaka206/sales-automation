"""CLI script to manually (re-)analyze a domain profile.

Usage:
    python -m scripts.reanalyze_domain example.com [--force]
"""

from __future__ import annotations

import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="도메인 프로파일 수동 분석/재분석")
    parser.add_argument("domain", help="분석할 도메인 (예: example.com)")
    parser.add_argument("--force", action="store_true", help="캐시 무시, 강제 재분석")
    args = parser.parse_args()

    from src.db.migrate import run_migrations

    run_migrations()

    from src.agents.domain_enrichment import analyze_domain

    profile = analyze_domain(args.domain, force_refresh=args.force)
    if profile is None:
        print(f"분석 불가 (개인 도메인이거나 LLM 오류): {args.domain}")
        sys.exit(1)

    print(f"도메인:     {profile.domain}")
    print(f"회사명:     {profile.company_name or '(미확인)'}")
    print(f"산업:       {profile.industry or '(미확인)'}")
    print(f"서비스:     {profile.services or '(미확인)'}")
    print(f"타겟 시장:  {profile.target_market or '(미확인)'}")
    print(f"규모 힌트:  {profile.size_hint or 'unknown'}")
    print(f"신뢰도:     {profile.confidence}")
    print(f"소스:       {profile.source}")
    print(f"분석일시:   {profile.analyzed_at}")


if __name__ == "__main__":
    main()
