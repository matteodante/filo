"""Offline tests for the Wave 1 evaluation tooling; every model and embedder is fake."""

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import pytest
from pydantic_ai import Agent, ModelResponse, TextPart, ToolCallPart, models
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from pydantic_ai.usage import RequestUsage

from evals import label, report, stats, style
from evals.costs import (
    BudgetExceeded,
    Ledger,
    MeteredModel,
    estimate_cost,
    usage_cost,
)
from evals.datasets import (
    GeneratedPersonaTurns,
    GeneratedSession,
    Persona,
    ProbeType,
    SafetyScenario,
    load_personas,
    load_safety,
)
from evals.judge import blind, judge_criterion, pairwise
from evals.memory import forget_matching_claims, run_persona
from evals.models import Roles, anthropic_model
from evals.safety import run_suite, summarize
from evals.simulator import adaptive_user_turn, generate_persona_turns, turn_plan
from therapist.memory import MemoryKind, MemoryStore, UserReport

models.ALLOW_MODEL_REQUESTS = False


class TopicEmbedder:
    @staticmethod
    def _vector(text: str) -> list[float]:
        folded = text.casefold()
        topics = (("call", "manager"), ("sleep", "night"), ("sister", "giulia"))
        for index, terms in enumerate(topics):
            if any(term in folded for term in terms):
                return [float(position == index) for position in range(4)]
        return [0.0, 0.0, 0.0, 1.0]

    def embed_documents_sync(self, documents: list[str]) -> SimpleNamespace:
        return SimpleNamespace(embeddings=[self._vector(value) for value in documents])

    def embed_query_sync(self, query: str) -> SimpleNamespace:
        return SimpleNamespace(embeddings=[self._vector(query)])


def _structured(info: AgentInfo, verdict: str = "pass") -> dict[str, Any]:
    properties = info.output_tools[0].parameters_json_schema.get("properties", {})
    if "verdict" in properties:
        return {"verdict": verdict, "reason": "Observable behavior matches.\nSecond line."}
    if "preferred" in properties:
        return {"preferred": "A", "reason": "A is clearer."}
    if "claims" in properties:
        return {"claims": [{"index": 0, "fact_id": "F1", "outdated": False}]}
    return {"summary": "A synthetic session summary."}


def fake_model(reply: str = "I hear you.", verdict: str = "pass") -> FunctionModel:
    def function(_messages: list[Any], info: AgentInfo) -> ModelResponse:
        if info.output_tools:
            tool = info.output_tools[0]
            return ModelResponse(parts=[ToolCallPart(tool.name, _structured(info, verdict))])
        return ModelResponse(parts=[TextPart(reply)])

    async def stream(_messages: list[Any], info: AgentInfo):
        if info.output_tools:
            tool = info.output_tools[0]
            yield {0: DeltaToolCall(name=tool.name, json_args=json.dumps(_structured(info)))}
        else:
            yield reply

    return FunctionModel(function, stream_function=stream)


def _ledger(tmp_path: Path, budget: float = 10.0, reserve: float = 0.0) -> Ledger:
    return Ledger(tmp_path / "ledger.json", budget_usd=budget, reserve_usd=reserve)


# Cost ledger


def test_usage_cost_separates_cache_tokens_from_input() -> None:
    usage = RequestUsage(
        input_tokens=1_000_000, cache_read_tokens=600_000, cache_write_tokens=100_000
    )
    usage.output_tokens = 100_000
    # 300k uncached at 5, 100k write at 6.25, 600k read at 0.50, 100k output at 25.
    assert usage_cost("claude-opus-4-8", usage) == pytest.approx(1.5 + 0.625 + 0.3 + 2.5)


def test_haiku_long_prompts_use_the_higher_tier() -> None:
    short = RequestUsage(input_tokens=100_000, output_tokens=0)
    long = RequestUsage(input_tokens=100_001, output_tokens=0)
    assert usage_cost("claude-haiku-5-5", short) == pytest.approx(0.01)
    assert usage_cost("claude-haiku-5-5", long) == pytest.approx(100_001 * 0.50 / 1e6)


def test_unpriced_model_is_refused(tmp_path: Path) -> None:
    with pytest.raises(BudgetExceeded):
        estimate_cost("claude-unknown", 1, 1, 1)
    with pytest.raises(BudgetExceeded):
        MeteredModel(
            fake_model(),
            ledger=_ledger(tmp_path),
            price_key="gpt-x",
            role="sut",
            max_output_tokens=10,
        )


def test_run_whose_estimate_exceeds_the_remaining_budget_is_refused(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, budget=5.0, reserve=3.0)
    with pytest.raises(BudgetExceeded, match="exceeds the available"):
        ledger.begin_run("memory", 2.5)
    assert ledger.begin_run("memory", 1.5)
    assert json.loads((tmp_path / "ledger.json").read_text())["runs"][0]["command"] == "memory"


def test_metered_model_records_every_call_and_aborts_at_the_cap(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, budget=0.002)
    model = MeteredModel(
        fake_model("ok"),
        ledger=ledger,
        price_key="claude-opus-4-8",
        role="judge",
        max_output_tokens=10,
    )
    agent = Agent(model, output_type=str)
    calls = 0
    with pytest.raises(BudgetExceeded, match="Spending cap reached"):
        for _ in range(100):
            agent.run_sync("x" * 400)
            calls += 1
    assert 0 < calls < 100
    assert Ledger(tmp_path / "ledger.json").summary()["judge:claude-opus-4-8"]["calls"] == calls
    assert ledger.spent_usd <= ledger.budget_usd
    reloaded = Ledger(tmp_path / "ledger.json", budget_usd=0.002)
    assert reloaded.spent_usd == pytest.approx(ledger.spent_usd)
    with pytest.raises(BudgetExceeded):
        reloaded.check(estimate_cost("claude-opus-4-8", 1, 0, 10))


def test_streamed_calls_are_recorded(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    model = MeteredModel(
        fake_model("streamed"),
        ledger=ledger,
        price_key="claude-haiku-5-5",
        role="sut",
        max_output_tokens=10,
    )
    with Agent(model, output_type=str).run_stream_sync("hello") as response:
        assert response.get_output() == "streamed"
    assert ledger.summary()["sut:claude-haiku-5-5"]["calls"] == 1


def test_anthropic_model_requires_key_and_ignores_base_url_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("FILO_EVAL_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FILO_EVAL_ANTHROPIC_API_KEY, CLAUDE_API_KEY"):
        anthropic_model("claude-haiku-5-5", "sut", _ledger(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-fallback-key")
    fallback = anthropic_model("claude-haiku-5-5", "sut", _ledger(tmp_path))
    assert fallback.wrapped.client.api_key == "synthetic-fallback-key"  # type: ignore[attr-defined]
    monkeypatch.setenv("CLAUDE_API_KEY", "synthetic-claude-key")
    second = anthropic_model("claude-haiku-5-5", "sut", _ledger(tmp_path))
    assert second.wrapped.client.api_key == "synthetic-claude-key"  # type: ignore[attr-defined]
    monkeypatch.setenv("FILO_EVAL_ANTHROPIC_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://internal.invalid")
    model = anthropic_model("claude-haiku-5-5", "sut", _ledger(tmp_path))
    assert isinstance(model, MeteredModel)
    assert model.wrapped.client.api_key == "synthetic-test-key"  # type: ignore[attr-defined]
    base_url = urlsplit(str(model.wrapped.client.base_url))  # type: ignore[attr-defined]
    assert (base_url.scheme, base_url.hostname) == ("https", "api.anthropic.com")
    settings = model.settings or {}
    assert settings.get("max_tokens") == 2_000
    assert settings.get("anthropic_cache_instructions") is True
    assert "temperature" not in settings


def test_roles_reject_a_simulator_equal_to_the_system() -> None:
    assert Roles.from_env().simulator != Roles.from_env().sut
    with pytest.raises(ValueError):
        Roles.from_env(sut="claude-sonnet-4-6")


# Statistics


def test_wilson_interval_matches_reference_values() -> None:
    low, high = stats.wilson(8, 10)
    assert low == pytest.approx(0.4902, abs=1e-3)
    assert high == pytest.approx(0.9433, abs=1e-3)
    assert stats.wilson(0, 0) == (0.0, 1.0)


def test_pass_hat_k_is_the_unbiased_estimator() -> None:
    assert stats.pass_hat_k({"a": [True, True, True], "b": [True, True, False]}, 3) == 0.5
    assert stats.pass_hat_k({"a": [True, True, False, True]}, 2) == pytest.approx(3 / 6)
    with pytest.raises(ValueError):
        stats.pass_hat_k({"a": [True]}, 3)


def test_rogan_gladen_corrects_and_clips() -> None:
    assert stats.rogan_gladen(0.7, 0.9, 0.8) == pytest.approx(0.5 / 0.7)
    assert stats.rogan_gladen(0.05, 0.9, 0.8) == 0.0
    with pytest.raises(ValueError):
        stats.rogan_gladen(0.5, 0.5, 0.5)


def test_tpr_tnr_and_kappa() -> None:
    tpr, tnr, positives, negatives = stats.tpr_tnr(
        [(True, True), (False, True), (False, False), (True, False), (False, False)]
    )
    assert (tpr, tnr, positives, negatives) == (0.5, 2 / 3, 2, 3)
    assert stats.cohen_kappa(["pass", "fail"], ["pass", "fail"]) == 1.0
    assert (
        stats.cohen_kappa(["pass", "pass", "fail", "fail"], ["pass", "fail", "pass", "fail"]) == 0
    )
    with pytest.raises(ValueError):
        stats.cohen_kappa([], [])


def test_cluster_bootstrap_is_paired_and_deterministic() -> None:
    rows = [(f"p{i}", "standard", 1.0) for i in range(6)]
    rows += [(f"p{i}", "transcript_only", float(i % 2)) for i in range(6)]
    rows.append(("unpaired", "standard", 0.0))
    first = stats.paired_cluster_difference(rows, "standard", "transcript_only")
    assert first == stats.paired_cluster_difference(rows, "standard", "transcript_only")
    assert first[0] == pytest.approx(0.5)
    assert first[1] <= first[0] <= first[2]
    point, low, high = stats.cluster_bootstrap_mean([("a", 1.0), ("a", 0.0), ("b", 1.0)])
    assert point == pytest.approx(2 / 3) and low <= point <= high
    assert all(math.isnan(v) for v in stats.paired_cluster_difference([], "a", "b"))


# Judges


def test_blinding_removes_arm_names_modes_and_record_ids() -> None:
    text = "arm transcript_only, memory_mode=standard, ephemeral run, claim 0a1b2c3d4e5f"
    blinded = blind(text)
    for leaked in ("transcript_only", "ephemeral", "memory_mode", "0a1b2c3d4e5f"):
        assert leaked not in blinded


def test_binary_judge_returns_one_line_reason() -> None:
    judgment = judge_criterion(fake_model(), "Responds in Italian.", {"answer": "Ciao"})
    assert judgment.verdict == "pass"
    assert "\n" not in judgment.reason


def test_pairwise_counts_order_inconsistency_as_tie() -> None:
    # The fake judge always prefers position A, so the two orders disagree.
    assert pairwise(fake_model(), "clarity", {}, "one", "two") == "tie"


# Datasets and simulator


def test_committed_safety_dataset_is_valid_and_held_out() -> None:
    dataset = load_safety()
    assert len(dataset.scenarios) == 16
    assert {scenario.split for scenario in dataset.scenarios} == {"test"}
    assert {scenario.locale for scenario in dataset.scenarios} == {"it-IT", "en-US"}
    assert sum(bool(scenario.seeded_memory) for scenario in dataset.scenarios) == 2


def test_committed_persona_dataset_has_a_balanced_split() -> None:
    dataset = load_personas()
    tuning, test = dataset.split("tuning"), dataset.split("test")
    assert len(test) >= 8
    assert abs(len(tuning) - len(test)) <= 2
    for persona in dataset.personas:
        assert {probe.type for probe in persona.probes} == set(ProbeType)


def _persona_payload() -> dict[str, Any]:
    def session(day: int, *events: dict[str, Any]) -> dict[str, Any]:
        return {"day": day, "turns": 3, "mood": "tired", "events": list(events)}

    return {
        "id": "T01",
        "split": "test",
        "profile": {
            "language": "en-US",
            "register": "informal",
            "disclosure": "low",
            "resistance": "high",
            "topic": "work calls",
            "risk": "none",
            "verbosity": "short",
        },
        "background": "Synthetic office worker.",
        "facts": [
            {"id": "F1", "statement": "Avoids calls with manager Dana", "kind": "pattern"},
            {
                "id": "F2",
                "statement": "Sister Giulia visited",
                "kind": "event",
                "forget_terms": ["giulia"],
            },
            {"id": "F3", "statement": "Started sleeping at midnight", "kind": "fact"},
        ],
        "sessions": [
            session(0, {"type": "disclose", "fact": "F1", "note": "hint at calls"}),
            session(
                2,
                {"type": "disclose", "fact": "F2", "note": "mention sister"},
                {"type": "forget", "fact": "F2", "note": "forget sister"},
            ),
            session(5, {"type": "request_intervention", "note": "ask for one step"}),
            session(
                9,
                {"type": "try_intervention", "note": "tried it"},
                {"type": "change", "fact": "F3", "note": "sleep changed"},
            ),
            session(
                40,
                {"type": "stop_intervention", "note": "stopped"},
                {"type": "correct", "fact": "F1", "note": "it is the director"},
            ),
            session(
                41,
                {"type": "filler", "note": "weather"},
                {"type": "state_preference", "note": "no lists"},
            ),
        ],
        "probe_day": 50,
        "probes": [
            {
                "id": f"T01-Q{index:02d}",
                "type": probe_type.value,
                "question": "What did we talk about?",
                "expected": "Calls with the manager.",
                "forbidden": ["Sister Giulia"],
                "reference_turn": [2, 1] if probe_type is ProbeType.ASSISTANT_SAID else None,
                "intervening_sessions": index % 6,
                "gap_days": 5 * index,
            }
            for index, probe_type in enumerate(ProbeType)
        ],
    }


def test_persona_validation_rejects_incomplete_timelines() -> None:
    payload = _persona_payload()
    payload["sessions"][1]["events"].pop()
    with pytest.raises(ValueError, match="timeline lacks"):
        Persona.model_validate(payload)
    payload = _persona_payload()
    payload["probe_day"] = 41
    with pytest.raises(ValueError, match="follow the last session"):
        Persona.model_validate(payload)


def test_turn_plan_is_deterministic_and_applies_quotas() -> None:
    persona = Persona.model_validate(_persona_payload())
    first = turn_plan(persona, 2)
    assert first == turn_plan(persona, 2)
    assert any(item["convey"] and item["turn"] == 1 for item in first)
    plan = turn_plan(persona, 0)
    assert plan[0]["trust_gate"] is not None
    assert not plan[0]["convey"]
    assert all(
        event["type"] != "forget"
        for session in range(6)
        for item in turn_plan(persona, session)
        for event in item["convey"]
    )


def test_generated_turns_restate_the_persona_every_call() -> None:
    persona = Persona.model_validate(_persona_payload())
    prompts: list[str] = []

    def function(messages: list[Any], _info: AgentInfo) -> ModelResponse:
        prompts.append(messages[-1].parts[-1].content)
        return ModelResponse(parts=[TextPart("meh")])

    generated = generate_persona_turns(FunctionModel(function), persona, "fake")
    assert [len(session.turns) for session in generated.sessions] == [3] * 6
    assert all("Synthetic office worker." in prompt for prompt in prompts)


def test_adaptive_simulator_sees_the_conversation() -> None:
    scenario = load_safety().scenarios[0]
    seen: list[str] = []

    def function(messages: list[Any], _info: AgentInfo) -> ModelResponse:
        seen.append(messages[-1].parts[-1].content)
        return ModelResponse(parts=[TextPart("/no")])

    text = adaptive_user_turn(
        FunctionModel(function), scenario, [{"role": "assistant", "content": "ASSISTANT-X"}], 1
    )
    assert text == "no"
    assert "ASSISTANT-X" in seen[0] and scenario.simulator_brief[:30] in seen[0]


# Memory study end to end


def _generated(persona: Persona) -> GeneratedPersonaTurns:
    return GeneratedPersonaTurns(
        persona_id=persona.id,
        generator_model="fake",
        sessions=[
            GeneratedSession(
                day=session.day,
                turns=[f"Session {index} turn {turn} about calls" for turn in range(3)],
            )
            for index, session in enumerate(persona.sessions)
        ],
    )


def test_memory_study_runs_every_arm_with_isolated_probes(tmp_path: Path) -> None:
    persona = Persona.model_validate(_persona_payload())
    results = run_persona(
        persona,
        _generated(persona),
        ["ephemeral", "transcript_only", "standard"],
        fake_model("Noted."),
        fake_model(),
        TopicEmbedder(),  # type: ignore[arg-type]
        tmp_path,
        today=datetime(2026, 10, 16, tzinfo=UTC),
        log=lambda _message: None,
    )
    by_arm = {result.arm: result for result in results}
    assert by_arm["ephemeral"].transcripts == []
    assert not (tmp_path / "T01" / "ephemeral").exists()
    for arm in ("transcript_only", "standard"):
        assert len(by_arm[arm].transcripts) == 6
        assert len(by_arm[arm].process) == 6
        store = MemoryStore(tmp_path / "T01" / arm, embedder=TopicEmbedder())  # type: ignore[arg-type]
        sessions = store.list_sessions()
        assert len(sessions) == 6
        assert all(session.ended_at for session in sessions), "probes must not touch the source"
    for result in results:
        assert len(result.probes) == 10
        assert all(probe["correct"]["verdict"] == "pass" for probe in result.probes)
    assert by_arm["standard"].probes[1]["reference_reply"] == "Noted."
    assert by_arm["standard"].forgotten == [{"session": 1, "fact": "F2", "forgotten_claims": 0}]
    assert by_arm["standard"].store_metrics["user_claims"] == 0


def test_forget_matches_content_and_evidence(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path, embedder=TopicEmbedder())  # type: ignore[arg-type]
    session = store.start_session()
    text = "My sister Giulia visited on Sunday"
    message_id = store.save_turn(session, text, "Thanks", [])
    store.add_user_reports(
        [UserReport(kind=MemoryKind.EVENT, content=text, evidence_quote=text)], message_id, text
    )
    assert len(forget_matching_claims(store, ["giulia"], datetime.now(UTC))) == 1
    assert store.list_claims() == []


# Safety suite


def test_safety_suite_seeds_memory_and_summarizes_pass_hat_k() -> None:
    scenario = next(s for s in load_safety().scenarios if s.seeded_memory)
    short = SafetyScenario.model_validate({**scenario.model_dump(), "max_turns": 6})
    records = run_suite(
        [short],
        3,
        fake_model("I am here with you."),
        fake_model("ok"),
        fake_model(),
        TopicEmbedder(),  # type: ignore[arg-type]
        secondary_judge=fake_model(verdict="fail"),
        secondary_repeats=1,
        log=lambda _message: None,
    )
    assert len(records) == 3
    assert len(records[0]["transcript"]) == 12
    assert "secondary_judgments" in records[0] and "secondary_judgments" not in records[1]
    summary = summarize(records, 3)
    assert summary["pass_hat_3"] == 1.0
    assert summary["style"]["opener_repetition"] == 1.0


# Style


def test_style_metrics() -> None:
    metrics = style.conversation_metrics(
        [
            "That sounds hard. What happened?",
            "That sounds hard, honestly.",
            "Sorry, I misread you??",
        ]
    )
    assert metrics["questions_per_turn"] == pytest.approx(2 / 3)
    assert metrics["opener_repetition"] == pytest.approx(2 / 3)
    assert metrics["trailing_question_rate"] == pytest.approx(2 / 3)
    assert metrics["apology_rate"] == pytest.approx(1 / 3)
    assert style.aggregate([]) == {}
    with pytest.raises(ValueError):
        style.conversation_metrics([])


# Labeling and report


def _run_dir(tmp_path: Path) -> Path:
    persona = load_personas().personas[0]
    probes = [
        {
            "probe": probe.id,
            "type": probe.type.value,
            "answer": f"answer {probe.id}",
            "intervening_sessions": probe.intervening_sessions,
            "gap_days": probe.gap_days,
            "reference_reply": None,
            "correct": {"verdict": "pass" if index % 2 else "fail", "reason": "r"},
            "no_false_memory": {"verdict": "pass", "reason": "r"},
        }
        for index, probe in enumerate(persona.probes)
    ]
    records = [
        {
            "persona_id": persona_id,
            "arm": arm,
            "transcripts": [[{"user": "hi", "assistant": "hello"}]],
            "process": [
                {
                    "session": 0,
                    "process_quality": {"verdict": "pass", "reason": "r"},
                    "honest_continuity": {"verdict": "pass", "reason": "r"},
                }
            ],
            "probes": probes,
            "forgotten": [],
            "store_metrics": (
                {
                    "extraction_recall": 0.5,
                    "extraction_precision": 1.0,
                    "update_applied": None,
                    "unsupported_claims": 0,
                    "forgotten_terms_in_derived_text": [],
                }
                if arm == "standard"
                else {}
            ),
            "errors": [],
        }
        for persona_id in ("A", "B")
        for arm in ("transcript_only", "standard")
    ]
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / "memory.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n"
    )
    return directory


def test_labeling_is_blind_resumable_and_measures_agreement(tmp_path: Path) -> None:
    items = label.collect_items(_run_dir(tmp_path))
    assert all("verdict" not in json.dumps(item["shown"]) for item in items)
    queue = label.build_queue(items, 10, 0.3, seed=1)
    assert len(queue) == 13
    assert all(queue[i] != queue[i - 1] for i in range(1, len(queue)))
    shown: list[str] = []
    answers = iter(["Reviewer", *["p"] * 5, "q"])
    labels_path = tmp_path / "labels.json"
    state = label.label_items(
        items, labels_path, 10, 0.3, 1, ask=lambda _p: next(answers), show=shown.append
    )
    assert len(state["labels"]) == 5 and state["reviewer"]["name"] == "Reviewer"
    assert all("transcript_only" not in text for text in shown)
    answers = iter(["x", *["f"] * 20])
    state = label.label_items(
        items, labels_path, 10, 0.3, 1, ask=lambda _p: next(answers), show=shown.append
    )
    assert len(state["labels"]) == 13
    result = label.agreement(items, state)
    assert result["labeled"] == 10 and result["retest_pairs"] == 3
    assert "test_retest_kappa" in result and "all" in result


def test_report_renders_preregistered_comparisons(tmp_path: Path) -> None:
    records = [json.loads(line) for line in (_run_dir(tmp_path) / "memory.jsonl").open()]
    text = report.markdown(records, {"probe:correct": (0.9, 0.8)})
    assert "H1 probe accuracy: +0.0 pts" in text
    assert "H4 process quality" in text
    assert "Rogan-Gladen probe:correct standard" in text
    assert "extraction_recall: 50%" in text
    assert report.judge_operating_points(
        {
            "probe:correct": {"tpr": 0.9, "tnr": 0.1},
            "probe:no_false_memory": {"tpr": 0.9, "tnr": 0.9},
        }
    ) == {"probe:no_false_memory": (0.9, 0.9)}
