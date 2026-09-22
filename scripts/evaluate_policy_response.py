"""Explicit live Gemini evaluation with isolated storage and no customer delivery.

Source export reads only model-authorized policies and reply building blocks in a
read-only transaction. Raw policies/outputs belong under ignored tmp/, not Git.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta


ROOT = Path(__file__).resolve().parents[1]


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode()).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8")


def private_path(value: str) -> Path:
    path = Path(value).resolve()
    if not path.is_relative_to(ROOT / "tmp"):
        raise ValueError("Raw evaluation artifacts must stay under this repository's ignored tmp/")
    return path


def isolate_writes() -> None:
    os.environ.update({
        "LIVE_EXTERNAL_WRITES": "false", "LIVE_HUBSPOT_WRITES": "false",
        "LIVE_SHEETS_WRITES": "false", "SLACK_ENABLED": "false", "APPROVAL_CHANNEL": "none",
        "INBOUND_POLL_ENABLED": "false", "INBOUND_WORKER_ENABLED": "false",
        "SEND_WORKER_ENABLED": "false", "FOLLOWUP_SEQUENCE_SINCE": "",
        "HUBSPOT_ACCESS_TOKEN": "", "HUBSPOT_PRIVATE_APP_TOKEN": "",
        "SLACK_BOT_TOKEN": "", "GOOGLE_SHEETS_OAUTH_REFRESH_TOKEN": "",
    })


def export_sources(settings, target: Path) -> dict:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    source_url = settings.DATABASE_URL.replace("postgres://", "postgresql://", 1)
    url = make_url(source_url)
    if url.get_backend_name() == "sqlite":
        database = Path(url.database).resolve()
        engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(
            database.as_uri() + "?mode=ro", uri=True))
    elif url.get_backend_name() == "postgresql":
        engine = create_engine(source_url, connect_args={
            "options": "-c default_transaction_read_only=on -c statement_timeout=15000",
            "connect_timeout": 15,
        })
    else:
        raise ValueError("Only read-only SQLite/PostgreSQL source snapshots supported")
    try:
        with engine.connect() as conn, conn.begin():
            if url.get_backend_name() == "postgresql":
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            policies = [dict(row) for row in conn.execute(text(
                "SELECT id,label,doc_key,mode,scope,model_access,title,body,usage_note,version "
                "FROM policy_sources WHERE model_access='customer_context' "
                "AND mode IN ('rules','knowledge') ORDER BY id"
            )).mappings()]
            templates = [dict(row) for row in conn.execute(text(
                "SELECT key,name,language,body,version FROM email_templates WHERE key IN "
                "('reply_format','reply_format_en','meeting_link','whatsapp_link',"
                "'sender_name','sender_name_en') ORDER BY key"
            )).mappings()]
    finally:
        engine.dispose()
    snapshot = {"policies": policies, "templates": templates}
    snapshot["sha256"] = digest(snapshot)
    write_json(target, snapshot)
    return {"status": "EXPORTED_READ_ONLY", "source_driver": url.drivername,
            "policy_count": len(policies), "template_count": len(templates),
            "sha256": snapshot["sha256"], "raw_path": str(target)}


def export_http(settings, target: Path, base_url: str | None = None) -> dict:
    """Use the configured authenticated read API when PostgreSQL is unreachable."""
    import httpx
    from src.common.tls import use_os_trust_store

    use_os_trust_store()
    base = (base_url or settings.PUBLIC_BASE_URL).rstrip("/")
    if not base.startswith("https://") or not settings.WEB_UI_PASSWORD:
        raise ValueError("Configured HTTPS base and Basic credentials are required for this adapter")
    with httpx.Client(base_url=base, auth=(settings.WEB_UI_USERNAME, settings.WEB_UI_PASSWORD),
                      timeout=60, follow_redirects=False) as client:
        response = client.get("/api/ui/policy-docs")
        response.raise_for_status()
        rows = response.json()["rows"]
        if any("placement" not in row for row in rows):
            raise ValueError("Remote API does not expose the model-access boundary")
        policies = []
        for row in rows:
            if row["placement"] == "human_only":
                continue
            if row["placement"] not in {"", "rules_all", "rules_first", "rules_followup"}:
                raise ValueError("Unknown policy placement")
            policies.append({key: row[key] for key in (
                "id", "label", "title", "mode", "scope", "body", "usage_note", "version")})
            policies[-1].update(model_access="customer_context", doc_key=f"api:{row['id']}")
        response = client.get("/api/ui/email-templates")
        response.raise_for_status()
        keys = {"reply_format", "reply_format_en", "meeting_link", "whatsapp_link",
                "sender_name", "sender_name_en"}
        templates = [{key: row[key] for key in ("key", "name", "language", "body", "version")}
                     for row in response.json()["items"] if row["key"] in keys]
        after = client.get("/api/ui/policy-docs")
        after.raise_for_status()
        if digest(after.json()["rows"]) != digest(rows):
            raise ValueError("Policy snapshot changed during export")
    snapshot = {"policies": sorted(policies, key=lambda r: r["id"]), "templates": templates,
                "source": "authenticated GET API", "keys": "synthetic api:id; API omits doc_key"}
    snapshot["sha256"] = digest(snapshot)
    write_json(target, snapshot)
    return {"status": "EXPORTED_READ_ONLY_HTTP", "policy_count": len(policies),
            "template_count": len(templates), "sha256": snapshot["sha256"]}


def run_live(args, settings) -> int:
    """Exercise real classifier/router/drafter/finalization; never import send worker."""
    from src.common.tls import use_os_trust_store

    use_os_trust_store()
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    source = json.loads(Path(args.sources).read_text(encoding="utf-8")) if args.sources else dataset
    output = private_path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    cases = [c for c in dataset["cases"] if not args.case or c["id"] in args.case]
    if not cases:
        raise ValueError("No evaluation cases selected")
    with tempfile.TemporaryDirectory(prefix="gemini-policy-eval-") as temp:
        # No production engine has been imported. Every app DB write goes here.
        settings.DATABASE_URL = "sqlite:///" + Path(temp, "eval.db").as_posix()
        os.environ["DATABASE_URL"] = settings.DATABASE_URL
        from src.db.base import Base
        from src.db.models import Contact, Conversation, EmailTemplate, Message, PolicySource
        from src.db.session import engine, SessionLocal
        from src.agents.inbound import InboundAgent
        from src.llm.client import LLMClient
        from src.llm.knowledge import SelectDocsResult
        import src.llm.client as client_module
        from src.common import safe_mode

        safe_mode.EMAIL_SENDING_ENABLED = False
        Base.metadata.create_all(engine)
        with SessionLocal() as session:
            for row in source["policies"]:
                session.add(PolicySource(**row))
            for row in source.get("templates", []):
                session.add(EmailTemplate(**row))
            session.commit()

        original_call = client_module.call_gemini
        calls: list[dict] = []
        budget_used = 0
        current_run = ""

        def measured(*a, **kw):
            nonlocal budget_used
            if budget_used >= args.max_calls:
                raise RuntimeError("Evaluation call cap reached")
            budget_used += 1
            started = time.monotonic()
            record = {"run": current_run, "model": kw.get("model"),
                      "max_tokens": kw.get("max_tokens"), "thinking_budget": kw.get("thinking_budget"),
                      "grounded": kw.get("grounded", False)}
            try:
                result = original_call(*a, **kw)
                record.update(status="PASS", input_tokens=result.input_tokens,
                              candidate_output_tokens=result.output_tokens)
                return result
            except Exception as exc:
                record.update(status="FAIL", error_type=type(exc).__name__, code=getattr(exc, "code", None))
                raise
            finally:
                record["seconds"] = round(time.monotonic() - started, 3)
                calls.append(record)
                write_json(output / "calls.json", calls)

        client_module.call_gemini = measured

        class EvaluationClient(LLMClient):
            def __init__(self, arm):
                super().__init__()
                self.arm = arm
                self.prompts = []

            def complete(self, name, variables=None, **kwargs):
                self.prompts.append({"name": name, "variables": copy.deepcopy(variables)})
                if name == "inbound/draft_reply" and args.draft_thinking_budget is not None:
                    kwargs["thinking_budget"] = args.draft_thinking_budget
                if self.arm == "all" and name == "inbound/select_docs":
                    # Existing empty-selection fallback supplies the same authorized candidates.
                    return SelectDocsResult(slugs=[], reasoning="evaluation all-candidates arm")
                return super().complete(name, variables, **kwargs)

        results = []
        manifest = {"data_origin": dataset["data_origin"], "dataset_sha256": digest(dataset),
                    "policy_sha256": digest(source["policies"]), "models": settings.gemini_model_for,
                    "api_surface": "vertex-ai", "repeats": args.repeats, "arms": args.arms,
                    "max_calls": args.max_calls, "production_writes": False, "customer_send": False,
                    "source": source.get("source", "external authorized snapshot" if args.sources else "synthetic local fixture"),
                    "authorization": "User explicitly authorized live evaluation in follow-up request"}
        manifest["runtime_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                      for name in ("src/agents/inbound.py", "src/agents/draft_evidence.py",
                                                   "src/llm/policy_context.py", "src/llm/knowledge.py",
                                                   "src/llm/client.py", "src/llm/providers/gemini_vertex.py",
                                                   "src/llm/prompts/inbound/draft_reply.md", __file__.replace(str(ROOT) + os.sep, "").replace(os.sep, "/"))}
        manifest["command"] = [sys.executable, *sys.argv]
        manifest["draft_thinking_budget_override"] = args.draft_thinking_budget
        manifest["region"] = settings.GOOGLE_CLOUD_LOCATION
        write_json(output / "manifest.json", manifest)
        try:
            for repeat in range(args.repeats):
                for case in cases:
                    # Alternate order across repetitions to avoid one fixed arm order.
                    for arm in args.arms if repeat % 2 == 0 else list(reversed(args.arms)):
                        current_run = f"{case['id']}-{arm}-{repeat + 1}"
                        started = time.monotonic()
                        at = datetime(2026, 9, 21, 0, 0)
                        with SessionLocal() as session:
                            contact = Contact(full_name="평가 고객", normalized_email=current_run+"@example.test",
                                              email=current_run+"@example.test", company="가상 평가사")
                            session.add(contact)
                            session.flush()
                            conv = Conversation(contact_id=contact.id, stage="new", summary=case.get("summary"))
                            session.add(conv)
                            session.flush()
                            turns = case.get("history", []) + [{"direction": "inbound", "body": case["inquiry"]}]
                            for i, turn in enumerate(turns):
                                session.add(Message(conversation_id=conv.id, direction=turn["direction"],
                                                    body=turn["body"], created_at=at+timedelta(minutes=i),
                                                    sent_at=at+timedelta(minutes=i) if turn["direction"] == "outgoing" else None,
                                                    status=turn.get("status", "sent" if turn["direction"] == "outgoing" else "received"),
                                                    prompt_variant=turn.get("prompt_variant")))
                            pending = Message(conversation_id=conv.id, direction="outgoing", body="", status="drafting")
                            session.add(pending)
                            session.commit()
                            conv_id, msg_id = conv.id, pending.id
                        llm = EvaluationClient(arm)
                        agent = InboundAgent.__new__(InboundAgent)
                        agent.llm = llm
                        info = {"full_name": "평가 고객", "company": "가상 평가사", "country": "KR",
                                "email": current_run+"@example.test", "lifecycle_stage": "lead",
                                "last_message": case.get("initial_inquiry", case["inquiry"]),
                                "subject": "평가 문의", "inquiry_language": case.get("language", "ko")}
                        result = {"run": current_run, "case_id": case["id"], "arm": arm, "repeat": repeat+1}
                        try:
                            classification = agent._classify(info)
                            draft = agent._draft_reply(info, classification, conv_id, info["inquiry_language"])
                            saved = agent._finalize_draft(msg_id, info, classification, draft, conv_id, info["inquiry_language"])
                            result.update(status="GENERATED" if saved else "NOT_PENDING", body=draft.body,
                                          answer_points=[p.model_dump() for p in draft.answer_points],
                                          policy_quotes=[q.model_dump() for q in draft.policy_quotes],
                                          body_ko=draft.body_ko, language=draft.language, classification=classification.model_dump(),
                                          provenance=draft._context_manifest, prompts=llm.prompts)
                            with SessionLocal() as session:
                                result["persisted_status"] = session.get(Message, msg_id).status
                        except Exception as exc:
                            result.update(status="ERROR", error_type=type(exc).__name__, code=getattr(exc, "code", None))
                            # Synthetic/authorized raw prompts remain only in ignored tmp/.
                            result["prompts"] = llm.prompts
                        result["seconds"] = round(time.monotonic()-started, 3)
                        results.append(result)
                        write_json(output / "results.json", results)
                        print(json.dumps({k: result[k] for k in ("run", "status", "seconds")}), flush=True)
                        if budget_used >= args.max_calls:
                            raise RuntimeError("Evaluation call cap reached")
        finally:
            client_module.call_gemini = original_call
            engine.dispose()
        manifest.update(actual_calls=len(calls), completed_runs=len(results),
                        generation_errors=sum(r["status"] != "GENERATED" for r in results))
        write_json(output / "manifest.json", manifest)
    return 0 if not manifest["generation_errors"] else 1


def main() -> int:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--export-sources")
    parser.add_argument("--export-http")
    parser.add_argument("--source-base-url")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--dataset")
    parser.add_argument("--sources")
    parser.add_argument("--output", default="tmp/policy-eval-2026-09-21/live")
    parser.add_argument("--case", action="append")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--arms", nargs="+", choices=("router", "all"), default=["router", "all"])
    parser.add_argument("--max-calls", type=int, default=120)
    parser.add_argument("--draft-thinking-budget", type=int)
    args = parser.parse_args()
    isolate_writes()
    from src.common.config import settings

    if args.preflight:
        from sqlalchemy.engine import make_url
        print(json.dumps({
            "vertex_credentials_present": bool(settings.GOOGLE_CREDENTIALS_JSON),
            "models": settings.gemini_model_for, "region": settings.GOOGLE_CLOUD_LOCATION,
            "database_driver": make_url(settings.DATABASE_URL.replace(
                "postgres://", "postgresql://", 1)).drivername,
            "external_writes": settings.LIVE_EXTERNAL_WRITES,
            "public_base_configured": bool(settings.PUBLIC_BASE_URL),
            "basic_credentials_present": bool(settings.WEB_UI_PASSWORD),
            "auth_mode": settings.AUTH_MODE,
            "no_network_calls": True,
        }))
        return 0
    if args.export_sources:
        print(json.dumps(export_sources(settings, private_path(args.export_sources))))
        return 0
    if args.export_http:
        print(json.dumps(export_http(settings, private_path(args.export_http), args.source_base_url)))
        return 0
    if args.live:
        if not args.dataset or args.repeats < 1 or args.max_calls < 1:
            parser.error("--live requires --dataset and positive repeats/max-calls")
        return run_live(args, settings)
    parser.error("Choose --preflight or --export-sources")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Connection errors must not echo configured URLs, credentials or raw inputs.
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__,
                          "status_code": getattr(getattr(exc, "response", None), "status_code", None)}))
        raise SystemExit(1) from None
