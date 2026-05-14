# 004 — LLM client (Claude CLI adapter first)

## Goal
Implement `src/llm/client.py` with provider routing. Start with `claude_cli` provider; stub the other two so the interface compiles. Prompt loader pulls from `src/llm/prompts/<area>/<name>.md` and concatenates `company_rules/*.md`.

## Steps
1. `src/llm/prompts/__init__.py` — `load_prompt(name: str, variables: dict) -> str` reads md file, prepends `company_rules/` content (cached), substitutes `{{var}}` placeholders.
2. `src/llm/providers/claude_cli.py` — wraps `subprocess.run(["claude", "-p", prompt, "--output-format", "text"], timeout=120)`. Honor `CLAUDE_CLI_PATH` env if set.
3. `src/llm/providers/anthropic_api.py` — uses anthropic SDK, model from `ANTHROPIC_MODEL`.
4. `src/llm/providers/ollama.py` — httpx POST to `${OLLAMA_HOST}/api/generate`.
5. `src/llm/client.py` — `LLMClient.complete(prompt_name, variables, schema=None)`. Routes to provider. If `schema` given, append `\nReturn JSON only.` to prompt, then `schema.model_validate_json(output)`. On parse fail: one retry with stronger instruction.
6. Log each call into `events` table.

## Verification
- `python -c "from src.llm.client import LLMClient; print(LLMClient().complete('test/hello', {'name':'world'}))"` (after adding a tiny test prompt) returns something.
- `tests/test_llm_client.py` mocks `subprocess.run` and asserts argv shape and stdout pass-through.

## Done when
- Tests green, claude_cli mode works on a real machine where `claude` is on PATH.
