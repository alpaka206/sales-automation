# Inbound architecture

## Reply pipeline

`HubSpot New → webhook/poller dedup → receipt acknowledgement → classify → select policy docs → draft → Slack review notice → human approval → SMTP → HubSpot log/stage`

The send worker is the single delivery path for both the API and web UI. The SMTP result is committed before best-effort CRM side effects. A CRM logging failure never changes a delivered email back to failed.

## Customer operations

- `Contact`: identity and HubSpot link
- `Conversation` / `Message`: inquiry and reply history
- `CustomerProfile`: customer state, pipeline, temperature, qualification, next action
- `CustomerInteraction`: HubSpot/email/meeting/Kakao/phone/manual touchpoints
- `ContractRecord`: contract, invoice, payment, plan, renewal facts

External services without credentials are connected through explicit manual sync or URL fields. They must never be represented as successfully synchronized when only local data exists.
