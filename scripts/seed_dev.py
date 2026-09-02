"""Populate a local SQLite DB with inbound-focused demo data."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.base import Base  # noqa: E402
from src.db.models import (  # noqa: E402
    Contact,
    ContractRecord,
    Conversation,
    CustomerInteraction,
    CustomerProfile,
    Message,
)
from src.db.session import SessionLocal, engine  # noqa: E402


def seed(force: bool = False) -> None:
    if engine.url.get_backend_name() != "sqlite":
        raise SystemExit(
            "Refusing to seed a remote database. Set DATABASE_URL to a local SQLite file first."
        )

    if force:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    with SessionLocal() as session:
        existing = session.query(Contact).count()
        if existing and not force:
            print(f"DB already has {existing} contact(s). Use --force to recreate local demo data.")
            return

        now = datetime.now(timezone.utc)
        buyer = Contact(
            hubspot_contact_id="hs-demo-inbound-001",
            email="buyer@enterprise.co.kr",
            normalized_email="buyer@enterprise.co.kr",
            full_name="김민준",
            company="Enterprise Korea",
            domain="enterprise.co.kr",
            country="Korea",
            score=86,
            phone="+821012345678",
        )
        customer = Contact(
            hubspot_contact_id="hs-demo-customer-002",
            email="ops@media.jp",
            normalized_email="ops@media.jp",
            full_name="Tanaka Yuki",
            company="Media Japan",
            domain="media.jp",
            country="Japan",
            score=78,
        )
        stale_lead = Contact(
            email="producer@studio.example",
            normalized_email="producer@studio.example",
            full_name="Alex Producer",
            company="Studio Example",
            domain="studio.example",
            country="United States",
            score=62,
        )
        session.add_all([buyer, customer, stale_lead])
        session.flush()

        inquiry = Conversation(
            contact_id=buyer.id,
            topic="pricing_question",
            stage="initial",
            hubspot_ticket_id="demo-ticket-001",
            inquiry_language="ko",
            last_incoming_at=now - timedelta(minutes=25),
            summary="엔터프라이즈 더빙 견적과 납기 문의",
            customer_requests="한국어·영어 더빙 120분 견적, 다음 주 미팅 희망",
        )
        service_thread = Conversation(
            contact_id=customer.id,
            topic="service_support",
            stage="active",
            inquiry_language="ja",
            last_outgoing_at=now - timedelta(days=2),
        )
        stale_thread = Conversation(
            contact_id=stale_lead.id,
            topic="trial_inquiry",
            stage="negotiation",
            inquiry_language="en",
            last_incoming_at=now - timedelta(days=21),
        )
        session.add_all([inquiry, service_thread, stale_thread])
        session.flush()

        session.add_all(
            [
                Message(
                    conversation_id=inquiry.id,
                    direction="inbound",
                    channel="email",
                    from_address=buyer.email,
                    subject="더빙 120분 견적 문의",
                    body="한국어와 영어 더빙 120분 견적과 가능한 납기를 알려주세요.",
                    language="ko",
                    status="received",
                    created_at=now - timedelta(minutes=25),
                ),
                Message(
                    conversation_id=inquiry.id,
                    direction="outgoing",
                    channel="email",
                    to_address=buyer.email,
                    subject="RE: 더빙 120분 견적 문의",
                    body="문의 주셔서 감사합니다. 견적과 납기를 확인해 안내드리겠습니다.",
                    language="ko",
                    target_language="ko",
                    status="pending_approval",
                    score_snapshot=86,
                    draft_provider="gemini",
                ),
                Message(
                    conversation_id=service_thread.id,
                    direction="outgoing",
                    channel="email",
                    to_address=customer.email,
                    subject="RE: Monthly usage review",
                    body="ご利用状況を確認しました。",
                    language="ja",
                    target_language="ja",
                    status="sent",
                    sent_at=now - timedelta(days=2),
                ),
            ]
        )
        session.add_all(
            [
                CustomerProfile(
                    contact_id=buyer.id,
                    customer_state="negotiation",
                    pipeline_stage="new",
                    lead_temperature="hot",
                    next_action="견적 검토 후 미팅 링크 발송",
                    next_action_at=now + timedelta(days=1),
                    industry="미디어",
                    source="HubSpot",
                ),
                CustomerProfile(
                    contact_id=customer.id,
                    customer_state="service",
                    pipeline_stage="active",
                    lead_temperature="warm",
                    current_plan="Business",
                    industry="방송",
                    user_seq="demo-user-002",
                ),
                CustomerProfile(
                    contact_id=stale_lead.id,
                    customer_state="negotiation",
                    pipeline_stage="negotiation",
                    lead_temperature="cold",
                    next_action="최근 논의 내용 확인",
                    lost_reason=None,
                ),
                CustomerInteraction(
                    contact_id=buyer.id,
                    conversation_id=inquiry.id,
                    channel="meeting",
                    direction="note",
                    subject="첫 미팅 요청",
                    summary="다음 주 온라인 미팅을 희망함",
                    happened_at=now - timedelta(minutes=15),
                ),
                ContractRecord(
                    contact_id=customer.id,
                    status="active",
                    plan="Business",
                    amount=12_000_000,
                    currency="KRW",
                    payment_method="stripe",
                    contract_date=now - timedelta(days=300),
                    expires_at=now + timedelta(days=45),
                    language_pairs=["ja-ko", "en-ja"],
                    unit_price="분당 10,000원",
                ),
            ]
        )
        session.commit()

        print("Seeded local inbound demo database:")
        print(f"  Contacts:      {session.query(Contact).count()}")
        print(f"  Conversations: {session.query(Conversation).count()}")
        print(f"  Messages:      {session.query(Message).count()}")
        print(f"  Contracts:     {session.query(ContractRecord).count()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed a local SQLite DB with inbound demo data")
    parser.add_argument("--force", action="store_true", help="Recreate the local demo database")
    args = parser.parse_args()
    seed(force=args.force)
