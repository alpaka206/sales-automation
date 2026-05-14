# 018 — Wire up approval endpoint (approve → send → log)

## Why

`POST /approve/{message_id}` is a stub that logs and returns a dummy response.
It does NOT call `approval.approve()` / `approval.reject()`, does NOT trigger
the send dispatcher, and does NOT log the engagement to HubSpot.
This means no message can ever leave the system — the entire human-in-the-loop
pipeline is broken.

## What to do

1. In `src/api/main.py`, replace the stub `approve_message` handler:
   - Parse `ApprovalBody` (already done).
   - On `action == "approve"` or `"edit"`: call `approval.approve(message_id, approver, edited_body)`.
   - On `action == "reject"`: call `approval.reject(message_id, approver, reason)`.
   - After approve: reload the `Message` with its `Conversation` relation, call `senders.send(message)`.
   - After send succeeds: call `approval.mark_sent(message_id)`.
   - After send: call `hubspot.create_email_engagement()` to log on the contact timeline (best-effort, log error but don't fail the request).
   - Return the updated message status + id.
   - Wrap in try/except for `ApprovalError` → 400, send failure → 500.

2. The sender `send()` is `async`; the route is sync. Either:
   - Make the route `async def` and `await send(msg)`, or
   - Use `asyncio.run()` / `loop.run_until_complete()` inside the sync route.
   Prefer making the route async.

3. `Message.conversation` relationship may need `lazy="joined"` or an explicit
   `joinedload` so `send()` can access `conversation.contact_id`.

## Acceptance criteria

- `POST /approve/{id}` with `action=approve` sets status to `approved`, then `sent`.
- `POST /approve/{id}` with `action=reject` sets status to `rejected`.
- `POST /approve/{id}` with `action=edit` updates body, then proceeds like approve.
- Invalid `message_id` → 404 or 400 with clear error.
- Double-approve → 400 with "not pending_approval" error.
- If sender raises, status stays `approved` (not `sent`), response is 500.

## Verify

```bash
pytest tests/test_approval_endpoint.py -v
```

Write a new test file `tests/test_approval_endpoint.py` that uses FastAPI's
`TestClient`, patches `senders.send` and `hubspot.create_email_engagement`,
and verifies each acceptance criterion above.
