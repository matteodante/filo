# Pydantic AI ecosystem review — 2026-07-22

> Historical design review. Its "Now" section describes the implementation before commit
> `e5382b7`; the normal turn now returns plain text and uses validated state tools. Consult
> `AGENTS.md`, `README.md`, and `src/filo/chat.py` for the current contract.

## Scope and conclusion

The repository currently resolves `pydantic-ai-slim==2.15.0` and
`pydantic-evals==2.15.0`. Pydantic AI 2.15.0 was released on 21 July 2026 and
is the current stable release. Keep it: typed output, application-owned
encrypted memory, offline `TestModel` tests, and opt-in live Evals fit the
current single-user CLI.

The highest-value changes are small defensive controls around output size,
context construction, coherent history, resource limits, failure visibility,
and stochastic evaluation. Do not add Harness Memory, durable execution,
fallback providers, MCP, or multi-agent orchestration yet.

## Now

### 1. Bound every model-written field

`FiloReply.reply` is bounded, but observations, hypotheses, IDs, focus,
and every `SessionReflection` string and collection remain unbounded. Add
Pydantic length constraints to each model-written field and item. This limits
memory amplification, prompt growth, retry cost, and malformed large outputs.
Keep two output retries; reserve output validators for cross-field invariants
that schemas cannot express.

### 2. Replace character slicing with structured budgets

Slicing a serialized context can cut JSON inside a field. Build
a bounded context first, prioritizing formulation, confirmed memories, active
hypotheses, recent sessions, and retrieved excerpts, then serialize complete
valid JSON. Likewise, select complete recent transcript turns instead of
using `session_transcript[-16000:]`, which can begin mid-turn.

### 3. Preserve complete history units

`load_session_history()` flattens stored `new_messages()` and then slices the
last messages. Output retries add request/response messages, so this may split
a logical run and make the number of retained user turns unpredictable. Trim
complete stored run or turn groups under a budget. Continue using Pydantic
AI's documented message adapter; do not implement a custom tool-pair repair
layer.

### 4. Add explicit run limits

Pass `UsageLimits` to normal turns and consolidation. With two output retries,
allow at most three model requests, plus measured input/total-token ceilings
and a bounded output-token policy. Catch `UsageLimitExceeded` explicitly.
Output retries, usage limits, and HTTP retries are separate controls.

### 5. Make live Evals stochastic and isolated

Keep deterministic tests single-run. Use Pydantic Evals `repeat=3` for routine
live evaluation and `repeat=5` for release candidates. Critical invariants
should pass every run; softer judge scores may use an aggregate threshold.
Create a fresh encrypted database directory for every repeat: the current
single `tmp_path` would contaminate repeated cases. Retry only transient task
or evaluator transport errors, never failed semantic assertions.

### 6. Improve failure diagnostics without leaking content

Use `capture_run_messages()` in synthetic live tests so exhausted output
retries show the invalid exchange. Do not send captured therapy content to
telemetry. If OpenTelemetry is enabled later, configure
`InstrumentationSettings(include_content=False)` and export only sanitized
operational metadata such as model, protocol version/hash, request count,
tokens, duration, and error class.

### 7. Tighten consolidation failures

Consolidation currently catches every `Exception` and silently closes the
session without a summary. Handle documented provider, output-validation, and
usage-limit failures explicitly and retain a privacy-safe error code.
Unexpected programming and schema errors should remain visible in development
and tests.

### 8. Add provider HTTP retries only when observed

Provider SDKs already have retry behavior. If operational evidence shows it is
insufficient, use Pydantic AI's official retry transport with three attempts,
bounded exponential backoff, `Retry-After`, and only network errors plus
429/502/503/504. Do not retry schema or clinical-policy failures.

## Next

- Store content-free run metadata and usage for reproducible evaluations.
- Generate JSON Schema for the YAML datasets and archive versioned reports.
- Reuse long-lived `Agent` instances only after context construction is cleanly
  separated; per-turn construction is not currently a material bottleneck.
- Borrow Harness Memory's compare-and-swap and idempotency ideas if SaaS adds
  concurrent writers, without adopting its notebook data model.
- Add durable execution only for genuinely long-running consolidation,
  background jobs, or human-review workflows.
- Add `FallbackModel` only with explicit consent and governance for sending
  sensitive data to another provider.

## Skip for the current CLI

- Harness Memory: it is model-writable Markdown and does not encode clinical
  status, evidence provenance, correction, forgetting, or case formulation.
- Harness compaction: the current protocol is about 9.8 KB and history is
  already deliberately bounded; structured budgeting is simpler.
- Harness guardrails: the current agent instructions already define contextual
  safety behavior, and typed output covers the current no-tool flow.
- Pydantic Graph beta, step persistence, and durable execution for a
  synchronous single-call CLI.
- MCP, multi-agent orchestration, vector databases, autonomous memory writing,
  silent provider fallback, or hosted tracing containing therapy content.
- Provider-native structured output as the default: tool output is more
  portable across the supported providers. Evaluate native output per adapter
  only if retry rates justify the branch.

The experimental ChatGPT/Codex backend must remain opt-in, personal, and
excluded from SaaS. Targeting it through `OpenAIResponsesModel` does not make
the undocumented backend a supported OpenAI or Pydantic provider contract.

## Primary sources

- [Pydantic AI 2.15.0 release](https://github.com/pydantic/pydantic-ai/releases/tag/v2.15.0)
- [Pydantic AI agents, limits, metadata, and errors](https://pydantic.dev/docs/ai/core-concepts/agent/)
- [Structured output and validation](https://pydantic.dev/docs/ai/core-concepts/output/)
- [Messages and chat history](https://pydantic.dev/docs/ai/core-concepts/message-history/)
- [HTTP request retries](https://pydantic.dev/docs/ai/advanced-features/retries/)
- [Models and fallback](https://pydantic.dev/docs/ai/models/overview/)
- [Private instrumentation](https://pydantic.dev/docs/ai/integrations/logfire/)
- [Pydantic Evals multi-run](https://pydantic.dev/docs/ai/evals/how-to/multi-run/)
- [Pydantic Evals retry strategies](https://pydantic.dev/docs/ai/evals/how-to/retry-strategies/)
- [Dataset serialization](https://pydantic.dev/docs/ai/evals/how-to/dataset-serialization/)
- [Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness)
- [Harness Memory](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/memory)
- [Harness compaction](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/compaction)
- [Harness step persistence](https://github.com/pydantic/pydantic-ai-harness/tree/main/pydantic_ai_harness/step_persistence)
