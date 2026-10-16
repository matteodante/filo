import os
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import models
from pydantic_ai.messages import RetryPromptPart, ToolReturnPart
from pydantic_evals import Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from filo.auth import codex_model, load_credential
from filo.chat import ChatSession
from filo.cli import DEFAULT_EMBEDDING_MODEL, _default_embedder
from filo.memory import ClaimFit, ClaimOrigin, MemoryKind, MemoryStore
from filo.protocol import ProtocolPack

CASES_PATH = Path(__file__).parent / "cases" / "live_codex_memory.yaml"


def _contains_term_groups(text: str, groups: list[list[str]]) -> bool:
    folded = text.casefold()
    return all(any(term.casefold() in folded for term in group) for group in groups)


@dataclass
class CodexMemoryContract(Evaluator[dict[str, Any], dict[str, Any], dict[str, Any]]):
    def evaluate(
        self, ctx: EvaluatorContext[dict[str, Any], dict[str, Any], dict[str, Any]]
    ) -> dict[str, bool]:
        expected = ctx.expected_output or {}
        context = ctx.output["context"].casefold()
        reply = ctx.output["return_reply"].casefold()
        return {
            "all_replies_non_empty": all(
                reply.strip() and len(reply) <= 4_000 for reply in ctx.output["initial_replies"]
            ),
            "tool_io_visible": (
                ctx.output["visible_tool_trace_count"] >= expected["minimum_tool_calls"]
                and ctx.output["all_visible_tool_traces_paired"]
            ),
            "tool_history_persisted": (
                ctx.output["history_tool_call_count"] >= expected["minimum_tool_calls"]
                and ctx.output["history_tools_paired"]
            ),
            "internal_history_excluded": (
                ctx.output["thinking_excluded"] and ctx.output["instructions_excluded"]
            ),
            "transcript_complete": ctx.output["transcript_complete"],
            "durable_memory_created": (
                ctx.output["memory_count"] >= expected["minimum_memory_items"]
            ),
            "fitted_hypothesis_recorded": (
                not expected["require_fitted_hypothesis"]
                or ctx.output["confirmed_hypothesis_count"] > 0
            ),
            "evidence_provenance_preserved": ctx.output["evidence_preserved"],
            "session_consolidated": ctx.output["session_consolidated"],
            "session_summary_complete": _contains_term_groups(
                ctx.output["session_summary"],
                expected["summary_term_groups"],
            ),
            "tool_io_exported": (
                ctx.output["export_tool_input_count"] >= expected["minimum_tool_calls"]
                and ctx.output["export_tools_paired"]
                and ctx.output["export_tool_inputs_structured"]
                and ctx.output["export_internal_history_excluded"]
            ),
            "encrypted_archive_has_no_synthetic_plaintext": ctx.output[
                "synthetic_plaintext_absent"
            ],
            "semantic_context_retrieved": all(
                term.casefold() in context for term in expected["context_terms"]
            ),
            "semantic_index_complete": ctx.output["semantic_index_complete"],
            "return_uses_continuity": any(
                term.casefold() in reply for term in expected["continuity_terms"]
            ),
            "new_session_created": (ctx.output["session_count"] >= expected["minimum_sessions"]),
            "return_reply_non_empty": bool(reply.strip()),
        }


def test_memory_contract_accepts_sparse_memory_and_semantic_paraphrase() -> None:
    loaded = Dataset[dict[str, Any], dict[str, Any], dict[str, Any]].from_file(CASES_PATH)
    expected = loaded.cases[0].expected_output
    assert expected is not None
    assert expected["minimum_memory_items"] == 2

    summary = (
        "You recognized a loop in which fear that your manager will notice every mistake "
        "leads to exhaustive preparation and postponing calls. Avoidance gives short-term "
        "relief but makes later calls harder."
    )
    assert _contains_term_groups(summary, expected["summary_term_groups"])
    assert not _contains_term_groups(
        "Calls were postponed and briefly relieving.",
        expected["summary_term_groups"],
    )


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("FILO_RUN_CODEX_EVALS") != "1",
    reason="Set FILO_RUN_CODEX_EVALS=1 after `filo auth login`.",
)
def test_configured_codex_longitudinal_memory(tmp_path: Path) -> None:
    credential_store = MemoryStore(Path(os.getenv("FILO_DATA_DIR", Path.home() / ".filo")))
    if load_credential(credential_store) is None:
        pytest.skip("Run `filo auth login` before the Codex memory eval.")
    model = codex_model(credential_store, "gpt-5.6-sol")
    pack = ProtocolPack.load(Path("protocols/transdiagnostic"))
    loaded = Dataset[dict[str, Any], dict[str, Any], dict[str, Any]].from_file(CASES_PATH)

    def run_case(inputs: dict[str, Any]) -> dict[str, Any]:
        started_at = datetime.fromisoformat(inputs["started_at"])
        with tempfile.TemporaryDirectory(dir=tmp_path) as data_directory:
            path = Path(data_directory)
            embedder = _default_embedder(local_files_only=True)
            store = MemoryStore(path, embedding_model=DEFAULT_EMBEDDING_MODEL, embedder=embedder)
            chat = ChatSession(model, pack, store, "en-US")
            turns = [
                chat.respond(message, started_at + timedelta(minutes=index * 10))
                for index, message in enumerate(inputs["initial_messages"])
            ]
            replies = [turn.text for turn in turns]
            closed = chat.end(started_at + timedelta(hours=1))
            assert closed is not None

            restarted = MemoryStore(
                path, embedding_model=DEFAULT_EMBEDDING_MODEL, embedder=embedder
            )
            history = restarted.load_session_history(closed.id)
            history_tool_calls = [
                (part.tool_name, part.tool_call_id)
                for message in history
                for part in message.parts
                if part.part_kind == "tool-call"
            ]
            history_tool_returns = [
                (part.tool_name, part.tool_call_id)
                for message in history
                for part in message.parts
                if isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_name
            ]
            transcript = restarted.session_transcript(closed.id)
            exported = restarted.export()
            exported_tool_exchanges = [
                exchange
                for message in exported["messages"]
                for exchange in message.get("tool_exchanges", [])
            ]
            exported_inputs = [
                exchange for exchange in exported_tool_exchanges if exchange["direction"] == "input"
            ]
            exported_outputs = [
                exchange
                for exchange in exported_tool_exchanges
                if exchange["direction"] == "output"
            ]
            context = restarted.retrieve_case_context(inputs["semantic_query"])
            items = restarted.list_claims()
            returned = ChatSession(model, pack, restarted, "en-US").respond(
                inputs["return_message"],
                started_at + timedelta(days=inputs["return_after_days"]),
            )
            restarted.retrieve_case_context(inputs["return_message"])
            with sqlite3.connect(restarted.database_path) as database:
                indexed = database.execute(
                    "SELECT COUNT(*) FROM semantic_index WHERE entity_type = 'claim'"
                ).fetchone()[0]
            return {
                "memory_count": len(items),
                "confirmed_hypothesis_count": sum(
                    item.origin is ClaimOrigin.AGENT_HYPOTHESIS
                    and item.fit is ClaimFit.FITS
                    and item.kind in {MemoryKind.PATTERN, MemoryKind.HYPOTHESIS}
                    for item in items
                ),
                "evidence_preserved": bool(items) and all(item.evidence for item in items),
                "session_consolidated": (
                    bool(closed.summary.strip())
                    and closed.consolidation_error is None
                    and closed.end_reason is not None
                ),
                "session_summary": closed.summary,
                "transcript_complete": all(
                    text in transcript for text in [*inputs["initial_messages"], *replies]
                ),
                "visible_tool_trace_count": sum(bool(turn.tool_trace) for turn in turns),
                "all_visible_tool_traces_paired": all(
                    not turn.tool_trace
                    or ("TOOL INPUT ·" in turn.tool_trace and "TOOL OUTPUT ·" in turn.tool_trace)
                    for turn in turns
                ),
                "history_tool_call_count": len(history_tool_calls),
                "history_tools_paired": sorted(history_tool_calls) == sorted(history_tool_returns),
                "thinking_excluded": all(
                    part.part_kind != "thinking" for message in history for part in message.parts
                ),
                "instructions_excluded": all(
                    getattr(message, "instructions", None) is None for message in history
                ),
                "export_tool_input_count": len(exported_inputs),
                "export_tools_paired": sorted(exchange["tool_name"] for exchange in exported_inputs)
                == sorted(exchange["tool_name"] for exchange in exported_outputs),
                "export_tool_inputs_structured": all(
                    isinstance(exchange["content"], dict) for exchange in exported_inputs
                ),
                "export_internal_history_excluded": all(
                    "model_messages" not in message for message in exported["messages"]
                ),
                "synthetic_plaintext_absent": all(
                    message.encode() not in restarted.database_path.read_bytes()
                    for message in inputs["initial_messages"]
                ),
                "context": context.model_dump_json(),
                "semantic_index_complete": indexed >= len(items),
                "return_reply": returned.text,
                "session_count": len(restarted.list_sessions()),
                "initial_replies": replies,
                "tool_traces": [turn.tool_trace for turn in turns],
                "transcript": transcript,
            }

    dataset = Dataset(
        name=loaded.name,
        cases=loaded.cases,
        evaluators=[CodexMemoryContract()],
    )
    with models.override_allow_model_requests(True):
        report = dataset.evaluate_sync(
            run_case,
            repeat=int(os.getenv("FILO_CODEX_EVAL_REPEAT", "1")),
            progress=False,
        )

    failed = [
        (
            f"{case.name}:{name}: {result.reason or 'no reason'}\n"
            f"tool_traces={case.output['tool_traces']}\n"
            f"summary={case.output['session_summary']}\n"
            f"transcript={case.output['transcript']}"
        )
        for case in report.cases
        for name, result in case.assertions.items()
        if not result.value
    ]
    assert not report.failures, report.render(include_errors=True)
    assert not failed, "\n\n".join(failed)
