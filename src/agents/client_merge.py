"""Safely merge a duplicate Client ID into the company's canonical ID."""

from __future__ import annotations

import re

from sqlalchemy import func, update

from ..db.models import (
    Client,
    Contact,
    ContractRecord,
    Conversation,
    PendingWon,
)


class ClientMergeConflict(RuntimeError):
    """The requested IDs cannot be proven to belong to the expected company."""


def _company_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9가-힣]", "", (value or "").casefold())


def _client_row(client: Client | None) -> dict | None:
    if client is None:
        return None
    return {
        "client_id": client.client_id,
        "company": client.company,
        "contact_id": client.contact_id,
        "contracts": [
            {"id": contract.id, "seq": contract.seq, "ticket_id": contract.ticket_id}
            for contract in sorted(client.contracts, key=lambda row: (row.seq, row.id))
        ],
    }


def client_merge_snapshot(session, source_id: int, target_id: int) -> dict:
    """Return all direct references that a Client ID merge must move."""
    names = {
        str(name).strip()
        for name in [
            *(row[0] for row in session.query(Contact.company)
            .filter(
                Contact.sheet_client_id.in_((source_id, target_id)),
                Contact.company.isnot(None),
            )
            .all()),
            *(row[0] for row in session.query(Client.company)
            .filter(Client.client_id.in_((source_id, target_id)))
            .all()),
        ]
        if str(name or "").strip()
    }
    counts = {
        "contacts": session.query(func.count(Contact.id))
        .filter(Contact.sheet_client_id == source_id)
        .scalar()
        or 0,
        "conversations": session.query(func.count(Conversation.id))
        .filter(Conversation.sheet_client_id == source_id)
        .scalar()
        or 0,
        "contract_records": session.query(func.count(ContractRecord.id))
        .filter(ContractRecord.sheet_client_id == source_id)
        .scalar()
        or 0,
        "pending_won": session.query(func.count(PendingWon.id))
        .filter(PendingWon.client_id == source_id)
        .scalar()
        or 0,
    }
    return {
        "source_id": source_id,
        "target_id": target_id,
        "company_names": sorted(names),
        "source": _client_row(session.get(Client, source_id)),
        "target": _client_row(session.get(Client, target_id)),
        "source_references": counts,
    }


def validate_client_merge(snapshot: dict, expected_company: str) -> None:
    source_id = int(snapshot["source_id"])
    target_id = int(snapshot["target_id"])
    if source_id == target_id:
        raise ClientMergeConflict("source and target Client IDs are the same")
    if source_id // 1000 != target_id // 1000:
        raise ClientMergeConflict("source and target Client IDs are in different customer bands")
    expected = _company_key(expected_company)
    if not expected:
        raise ClientMergeConflict("expected_company is required")
    known = {_company_key(name) for name in snapshot["company_names"] if _company_key(name)}
    if not known:
        raise ClientMergeConflict("no company name is stored for either Client ID")
    if any(name != expected for name in known):
        raise ClientMergeConflict(
            "stored company names do not all match the explicitly confirmed company"
        )


_CLIENT_FIELDS = (
    "company",
    "industry",
    "country",
    "department",
    "first_won_on",
    "owner",
    "contact_id",
)


def merge_client_ids(
    session,
    source_id: int,
    target_id: int,
    expected_company: str,
) -> dict:
    """Move every local reference from source to target inside one DB transaction."""
    before = client_merge_snapshot(session, source_id, target_id)
    validate_client_merge(before, expected_company)

    source = session.get(Client, source_id)
    target = session.get(Client, target_id)
    if source is not None and target is None:
        target = Client(
            client_id=target_id,
            **{field: getattr(source, field) for field in _CLIENT_FIELDS},
        )
        session.add(target)
        session.flush()
    elif source is not None and target is not None:
        for field in _CLIENT_FIELDS:
            if not getattr(target, field) and getattr(source, field):
                setattr(target, field, getattr(source, field))

    contract_seq_changes: list[dict[str, int]] = []
    if source is not None and target is not None:
        used = {contract.seq for contract in target.contracts}
        for contract in sorted(list(source.contracts), key=lambda row: (row.seq, row.id)):
            old_seq = contract.seq
            new_seq = old_seq
            while new_seq in used:
                new_seq += 1
            used.add(new_seq)
            if new_seq != old_seq:
                contract_seq_changes.append(
                    {"contract_id": contract.id, "from": old_seq, "to": new_seq}
                )
            contract.seq = new_seq
            contract.client = target
        session.flush()
        session.delete(source)

    moved = {}
    for model, column, label in (
        (Contact, Contact.sheet_client_id, "contacts"),
        (Conversation, Conversation.sheet_client_id, "conversations"),
        (ContractRecord, ContractRecord.sheet_client_id, "contract_records"),
        (PendingWon, PendingWon.client_id, "pending_won"),
    ):
        result = session.execute(
            update(model).where(column == source_id).values({column.key: target_id})
        )
        moved[label] = result.rowcount or 0

    session.flush()
    return {
        "before": before,
        "moved": moved,
        "contract_seq_changes": contract_seq_changes,
        "after": client_merge_snapshot(session, source_id, target_id),
    }
