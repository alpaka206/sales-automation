"""Populate the dev DB with sample data for manual testing."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.base import Base  # noqa: E402
from src.db.models import Contact, Conversation, Message, Prospect  # noqa: E402
from src.db.session import SessionLocal, engine  # noqa: E402


def seed(force: bool = False) -> None:
    os.makedirs("data", exist_ok=True)
    Base.metadata.create_all(engine)

    session = SessionLocal()
    try:
        existing = session.query(Contact).count()
        if existing > 0 and not force:
            print(f"DB already has {existing} contact(s). Use --force to truncate and re-seed.")
            return

        if force:
            for model in (Message, Conversation, Prospect, Contact):
                session.query(model).delete()
            session.commit()
            print("Truncated existing data.")

        now = datetime.now(timezone.utc)

        c1 = Contact(
            hubspot_contact_id="hs-seed-001",
            email="kim@enterprise.co.kr",
            normalized_email="kim@enterprise.co.kr",
            full_name="Kim Minjun",
            company="Enterprise Korea",
            domain="enterprise.co.kr",
            country="korea",
            score=85,
        )
        c2 = Contact(
            hubspot_contact_id="hs-seed-002",
            email="tanaka@saas.jp",
            normalized_email="tanaka@saas.jp",
            full_name="Tanaka Yuki",
            company="SaaS Japan Inc.",
            domain="saas.jp",
            country="japan",
            score=72,
        )
        c3 = Contact(
            email="freelancer@gmail.com",
            normalized_email="freelancer@gmail.com",
            full_name="Lee Freelancer",
            country="korea",
            score=35,
        )
        session.add_all([c1, c2, c3])
        session.flush()

        p1 = Prospect(
            source="manual_csv",
            email="kim@enterprise.co.kr",
            normalized_email="kim@enterprise.co.kr",
            full_name="Kim Minjun",
            company="Enterprise Korea",
            domain="enterprise.co.kr",
            country="korea",
            icp_score=85,
            icp_rationale="SaaS, Korea, 100+ employees",
            status="drafted",
            contact_id=c1.id,
            last_contacted_at=now - timedelta(days=2),
        )
        p2 = Prospect(
            source="youtube",
            email="lowfit@tiny.com",
            normalized_email="lowfit@tiny.com",
            full_name="Low Fit",
            company="Tiny Co",
            domain="tiny.com",
            icp_score=20,
            icp_rationale="Too small, non-target region",
            status="skipped_lowscore",
        )
        session.add_all([p1, p2])
        session.flush()

        conv1 = Conversation(contact_id=c1.id, prospect_id=p1.id, topic="outbound_opening", stage="initial")
        conv2 = Conversation(contact_id=c2.id, topic="purchase_inquiry", stage="initial")
        session.add_all([conv1, conv2])
        session.flush()

        m1 = Message(
            conversation_id=conv1.id,
            direction="outbound",
            channel="email",
            to_address="kim@enterprise.co.kr",
            subject="Enterprise Korea 협업 제안",
            body="안녕하세요, Kim Minjun님. 귀사와 협업을 제안드립니다.",
            language="ko",
            status="sent",
            sent_at=now - timedelta(days=2),
        )
        m2 = Message(
            conversation_id=conv2.id,
            direction="outbound",
            channel="email",
            to_address="tanaka@saas.jp",
            subject="Re: Product Inquiry",
            body="Tanaka님, 안녕하세요. 문의 주셔서 감사합니다.",
            language="ko",
            status="pending_approval",
        )
        m3 = Message(
            conversation_id=conv1.id,
            direction="outbound",
            channel="email",
            to_address="kim@enterprise.co.kr",
            subject="Follow-up: Enterprise Korea",
            body="Kim Minjun님, 지난 메일 확인 부탁드립니다.",
            language="ko",
            status="rejected",
        )
        m4 = Message(
            conversation_id=conv1.id,
            direction="inbound",
            channel="email",
            from_address="kim@enterprise.co.kr",
            subject="Re: Enterprise Korea 협업 제안",
            body="네, 관심 있습니다. 미팅 일정 잡아주세요.",
            language="ko",
            status="received",
            replied=True,
        )
        session.add_all([m1, m2, m3, m4])

        conv1.last_outgoing_at = m1.sent_at
        conv1.last_incoming_at = now - timedelta(days=1)

        session.commit()

        print("Seeded dev database:")
        print(f"  Contacts:      {session.query(Contact).count()}")
        print(f"  Prospects:     {session.query(Prospect).count()}")
        print(f"  Conversations: {session.query(Conversation).count()}")
        print(f"  Messages:      {session.query(Message).count()}")
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed dev DB with sample data")
    parser.add_argument("--force", action="store_true", help="Truncate and re-seed")
    args = parser.parse_args()
    seed(force=args.force)
