"""One confirmed company can safely collapse a duplicate Client ID."""

from __future__ import annotations

import pytest

from src.agents.client_merge import ClientMergeConflict, merge_client_ids
from src.db.models import (
    Client,
    ClientContract,
    Contact,
    ContractRecord,
    Conversation,
    PendingWon,
)


def test_merge_moves_all_references_and_renumbers_contract_collision(db_session) -> None:
    target_contact = Contact(
        normalized_email="first@jupiter-mercury.com",
        full_name="First",
        company="JUPITER AND MERCURY",
        sheet_client_id=1323,
    )
    source_contact = Contact(
        normalized_email="second@jupiter-mercury.com",
        full_name="Second",
        company="JUPITER AND MERCURY",
        sheet_client_id=1364,
    )
    db_session.add_all([target_contact, source_contact])
    db_session.flush()
    target = Client(client_id=1323, company="JUPITER AND MERCURY")
    source = Client(
        client_id=1364,
        company="JUPITER AND MERCURY",
        contact_id=source_contact.id,
        country="US",
    )
    db_session.add_all([target, source])
    db_session.flush()
    target_contract = ClientContract(client_id=1323, seq=1)
    source_contract = ClientContract(client_id=1364, seq=1)
    conversation = Conversation(
        contact_id=source_contact.id,
        hubspot_ticket_id="ticket-1364",
        sheet_client_id=1364,
    )
    db_session.add_all([target_contract, source_contract, conversation])
    db_session.flush()
    db_session.add_all(
        [
            ContractRecord(
                contact_id=source_contact.id,
                conversation_id=conversation.id,
                sheet_client_id=1364,
            ),
            PendingWon(
                ticket_id="ticket-1364",
                company="JUPITER AND MERCURY",
                client_id=1364,
                conversation_id=conversation.id,
            ),
        ]
    )
    db_session.flush()

    result = merge_client_ids(
        db_session, 1364, 1323, "JUPITER AND MERCURY"
    )
    db_session.commit()

    assert db_session.get(Client, 1364) is None
    merged = db_session.get(Client, 1323)
    assert merged.country == "US"
    assert merged.contact_id == source_contact.id
    assert sorted(contract.seq for contract in merged.contracts) == [1, 2]
    assert source_contact.sheet_client_id == 1323
    assert conversation.sheet_client_id == 1323
    assert db_session.query(ContractRecord).one().sheet_client_id == 1323
    assert db_session.query(PendingWon).one().client_id == 1323
    assert result["contract_seq_changes"] == [
        {"contract_id": source_contract.id, "from": 1, "to": 2}
    ]


def test_merge_refuses_a_different_stored_company(db_session) -> None:
    db_session.add_all(
        [
            Contact(
                normalized_email="wrong@example.com",
                full_name="Wrong",
                company="OTHER COMPANY",
                sheet_client_id=1364,
            ),
            Client(client_id=1323, company="JUPITER AND MERCURY"),
        ]
    )
    db_session.flush()

    with pytest.raises(ClientMergeConflict, match="do not all match"):
        merge_client_ids(db_session, 1364, 1323, "JUPITER AND MERCURY")
