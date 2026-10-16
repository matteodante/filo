import errno
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.error import URLError

import pytest
from pydantic_ai import Embedder, ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.embeddings import TestEmbeddingModel
from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models import infer_model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

import filo.auth as auth
from filo.chat import (
    MAX_REQUEST_OUTPUT_TOKENS,
    TurnStreamEvent,
    TurnStreamKind,
    crisis_resources,
)
from filo.cli import (
    DEFAULT_CODEX_MODEL,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_REPO,
    DEFAULT_EMBEDDING_REVISION,
    _chat,
    _chat_command,
    _conversation_model,
    _ensure_chat_consent,
    _local_endpoint_report,
    _local_model_names,
    _model_context_window,
    _select_model,
    _setup,
    build_parser,
    main,
)
from filo.failures import SetupProblem
from filo.memory import MemoryKind, MemoryMode, MemoryStore, UserReport
from filo.process_lock import exclusive_lock


def test_guided_setup_advertises_only_the_supported_chatgpt_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def select(*_: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(ask=lambda: DEFAULT_CODEX_MODEL)

    monkeypatch.setattr("filo.cli.questionary.select", select)
    monkeypatch.setattr("filo.cli._local_model_names", list)

    assert _select_model() == DEFAULT_CODEX_MODEL
    choices = captured["choices"]
    assert isinstance(choices, list)
    assert [(choice.title, choice.value) for choice in choices] == [
        ("ChatGPT Plus/Pro — GPT-5.6 Sol", DEFAULT_CODEX_MODEL)
    ]


def test_guided_setup_offers_a_reachable_local_server_before_chatgpt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def select(*_: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return SimpleNamespace(ask=lambda: "local:small")

    monkeypatch.setattr("filo.cli.questionary.select", select)
    monkeypatch.setattr("filo.cli._local_model_names", lambda: ["small", "large"])

    assert _select_model() == "local:small"
    choices = captured["choices"]
    assert isinstance(choices, list)
    assert [(choice.title, choice.value) for choice in choices] == [
        ("Local server — small", "local:small"),
        ("Local server — large", "local:large"),
        ("ChatGPT Plus/Pro — GPT-5.6 Sol", DEFAULT_CODEX_MODEL),
    ]
    assert captured["default"] == "local:small"


def test_local_model_names_reads_the_openai_compatible_catalogue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def read(self) -> bytes:
            return json.dumps({"data": [{"id": "small"}, {"id": ""}, {"no_id": True}]}).encode()

    def urlopen(url: str, timeout: float) -> object:
        requested.append(url)
        return Response()

    monkeypatch.setenv("FILO_LOCAL_BASE_URL", "https://example.invalid/v1/")
    monkeypatch.setattr("filo.cli.urlopen", urlopen)

    assert _local_model_names() == ["small"]
    assert requested == ["https://example.invalid/v1/models"]


def test_local_model_names_is_empty_when_the_server_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def urlopen(url: str, timeout: float) -> object:
        raise OSError("unreachable")

    monkeypatch.setattr("filo.cli.urlopen", urlopen)

    assert _local_model_names() == []


def test_local_endpoint_report_names_the_local_network_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def urlopen(url: str, timeout: float) -> object:
        raise URLError(OSError(errno.EHOSTUNREACH, "No route to host"))

    monkeypatch.setenv("FILO_LOCAL_BASE_URL", "http://rig.invalid:8080/v1")
    monkeypatch.setattr("filo.cli.urlopen", urlopen)

    report = _local_endpoint_report()

    assert "no route to host" in report
    assert "local network permission" in report


def test_local_endpoint_report_separates_other_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def urlopen(url: str, timeout: float) -> object:
        raise URLError(OSError(errno.ECONNREFUSED, "Connection refused"))

    monkeypatch.setattr("filo.cli.urlopen", urlopen)

    report = _local_endpoint_report()

    assert "local network permission" not in report
    assert "URLError" in report


def test_local_conversation_model_targets_the_configured_server_with_high_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FILO_LOCAL_BASE_URL", "https://example.invalid/v1")
    monkeypatch.delenv("FILO_LOCAL_API_KEY", raising=False)

    model = _conversation_model(MemoryStore(tmp_path), "local:qwen")

    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == "qwen"
    assert model.base_url == "https://example.invalid/v1/"
    assert model.settings == {"openai_reasoning_effort": "xhigh"}


def test_setup_builds_questionary_selects_with_available_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers = iter([DEFAULT_CODEX_MODEL, "it-IT", "128000", False, False])
    monkeypatch.setattr("filo.cli._ask", lambda _: next(answers))

    assert _setup(MemoryStore(tmp_path), SimpleNamespace()) == 0


def test_chat_consent_discloses_scope_fallibility_and_data_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.consent_version = "alpha-1"
    store.save_app_state(state)
    monkeypatch.setattr("builtins.input", lambda _: "I UNDERSTAND")

    assert _ensure_chat_consent(store, "it-IT")

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "Filo is experimental AI" in output
    assert crisis_resources("it-IT") in output
    assert "at least 18" in output
    assert "output can be wrong" in output
    assert "separate bounded case-data envelope" in output
    assert store.load_app_state().consent_version == "alpha-4"


def test_openai_responses_storage_is_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    model = _conversation_model(MemoryStore(tmp_path), "openai:gpt-5.6-sol")

    assert isinstance(model, OpenAIResponsesModel)
    assert model.settings["openai_store"] is False


def test_anthropic_requests_stay_within_the_output_reserve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    model = _conversation_model(MemoryStore(tmp_path), "anthropic:claude-sonnet-5-5")

    assert isinstance(model, AnthropicModel)
    assert model.settings["max_tokens"] == MAX_REQUEST_OUTPUT_TOKENS


def test_google_model_builds_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # PydanticAI moved to httpx2 while google-genai still builds its own httpx client.
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    model = infer_model(_conversation_model(MemoryStore(tmp_path), "google:gemini-2.5-flash"))

    assert isinstance(model, GoogleModel)


def test_hugging_face_telemetry_is_disabled() -> None:
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] == "1"


def test_embedding_model_commands_manage_only_the_model_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    snapshot = tmp_path / "snapshot"
    revision = SimpleNamespace(
        commit_hash=DEFAULT_EMBEDDING_REVISION,
        refs={"main"},
        last_modified=1,
        snapshot_path=snapshot,
        size_on_disk=1024,
    )
    model = SimpleNamespace(
        repo_type="model",
        repo_id=DEFAULT_EMBEDDING_REPO,
        repo_path=tmp_path / "cache" / "model",
        size_on_disk=1024,
        revisions=[revision],
    )

    class Strategy:
        expected_freed_size_str = "1.0 KB"
        executed = False

        def execute(self) -> None:
            self.executed = True

    strategy = Strategy()
    cache = SimpleNamespace(
        delete_revisions=lambda *revisions: (
            strategy if revisions == (DEFAULT_EMBEDDING_REVISION,) else None
        )
    )
    monkeypatch.setattr("filo.cli._embedding_cache_info", lambda: (cache, model, []))

    prefix = ["--data-dir", str(tmp_path), "memory", "model"]
    assert main([*prefix, "status"]) == 0
    assert main([*prefix, "remove", "--yes"]) == 0

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "Status: installed" in output
    assert DEFAULT_EMBEDDING_REVISION in output
    assert "Encrypted memory data was not changed" in output
    assert strategy.executed
    assert not (tmp_path / "filo.db").exists()


def test_embedding_model_verify_checks_checksums_and_local_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    revision = SimpleNamespace(
        commit_hash=DEFAULT_EMBEDDING_REVISION,
        refs={"main"},
        last_modified=1,
        snapshot_path=tmp_path / "snapshot",
        size_on_disk=1024,
    )
    model = SimpleNamespace(revisions=[revision])
    result = SimpleNamespace(mismatches=[], checked_count=11)
    monkeypatch.setattr(
        "filo.cli._embedding_cache_info",
        lambda: (SimpleNamespace(), model, []),
    )
    monkeypatch.setattr(
        "filo.cli.HfApi",
        lambda: SimpleNamespace(verify_repo_checksums=lambda *args, **kwargs: result),
    )
    inferred_models: list[object] = []
    monkeypatch.setattr(
        "filo.cli._verify_embedding_inference",
        lambda embedder: inferred_models.append(embedder) or 1024,
    )

    assert main(["memory", "model", "verify"]) == 0

    assert len(inferred_models) == 1
    assert "11 files, 1024 dimensions" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_embedding_model_install_uses_setup_smoke_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    prepared: list[bool] = []
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.embedding_model = "old:model"
    store.save_app_state(state)
    monkeypatch.setattr(
        "filo.cli._prepare_semantic_memory",
        lambda: prepared.append(True) or True,
    )
    monkeypatch.setattr("filo.cli._print_embedding_model_status", lambda: True)

    assert main(["--data-dir", str(tmp_path), "memory", "model", "install"]) == 0

    assert prepared == [True]
    assert MemoryStore(tmp_path).load_app_state().embedding_model == DEFAULT_EMBEDDING_MODEL
    assert "configuration updated" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_memory_commands_expose_and_correct_structured_memory(
    tmp_path: Path, capsys: object
) -> None:
    store = MemoryStore(tmp_path)
    session = store.start_session()
    message_id = store.save_turn(session, "I avoid calls", "What happens?", [])
    evidence = store.add_user_reports(
        [
            UserReport(
                kind=MemoryKind.EVENT,
                content="I avoid calls",
                evidence_quote="I avoid calls",
            )
        ],
        message_id,
        "I avoid calls",
    )[0]
    item = store.add_hypothesis(
        "Calls trigger anxiety",
        linked_claim_ids=[evidence.id],
        evidence_message_ids=[message_id],
    )

    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "memory",
                "review",
                item.id,
                "fits",
                "That fits",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "memory",
                "correct",
                item.id,
                "Some calls trigger anxiety",
            ]
        )
        == 2
    )
    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "memory",
                "correct",
                evidence.id,
                "I avoid most calls",
            ]
        )
        == 0
    )
    assert main(["--data-dir", str(tmp_path), "memory", "show"]) == 0

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "cannot become a user statement" in output
    assert "I avoid most calls" in output
    assert "user_statement" in output
    assert "Calls trigger anxiety" in output


def test_privacy_mode_and_retention_commands_persist_configuration(
    tmp_path: Path, capsys: object
) -> None:
    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "privacy",
                "set-default",
                "transcript-only",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "retention",
                "set",
                "--raw-message-days",
                "30",
                "--stale-hypothesis-days",
                "180",
            ]
        )
        == 0
    )
    state = MemoryStore(tmp_path).load_app_state()
    assert state.default_memory_mode.value == "transcript_only"
    assert state.retention_policy.raw_message_days == 30
    assert state.retention_policy.stale_hypothesis_days == 180
    assert main(["--data-dir", str(tmp_path), "privacy", "show"]) == 0
    assert "Changing mode does not delete" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_incompatible_store_reports_clean_break_without_migrating(
    tmp_path: Path, capsys: object
) -> None:
    store = MemoryStore(tmp_path)
    store._write_state("schema_version", b"2")
    assert main(["--data-dir", str(tmp_path), "memory", "show"]) == 2
    assert "migration is not supported" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_interactive_command_and_output_stay_out_of_transcript(
    tmp_path: Path, capsys: object
) -> None:
    store = MemoryStore(tmp_path)
    session = store.start_session()
    message_id = store.save_turn(session, "Original user turn.", "Original reply.", [])
    store.add_user_reports(
        [
            UserReport(
                kind=MemoryKind.FACT,
                content="Rendered command output.",
                evidence_quote="Rendered command output.",
            )
        ],
        message_id,
        "Rendered command output.",
    )
    before = store.session_transcript(session.id)

    assert _chat_command("/memory", SimpleNamespace(), store) is True  # type: ignore[arg-type]

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "Rendered command output." in output
    assert store.session_transcript(session.id) == before
    assert "Rendered command output." not in before


def test_crisis_command_shows_static_resources_after_a_model_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.consent_version = "alpha-4"
    store.save_app_state(state)
    inputs = iter(["I am not safe tonight.", "/crisis", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    calls: list[str] = []

    def respond(text: str, *, on_event: object) -> SimpleNamespace:
        calls.append(text)
        raise RuntimeError("provider unavailable")

    session = SimpleNamespace(respond=respond, locale="it-IT", memory_mode=MemoryMode.STANDARD)

    assert _chat(session, store) == 0  # type: ignore[arg-type]

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert calls == ["I am not safe tonight."]
    assert output.index("/crisis shows emergency resources") < output.index("112")
    assert crisis_resources("it-IT") in output
    assert "1522" in output
    assert store.list_sessions() == []


def test_crisis_text_is_localized_and_covers_both_regions() -> None:
    english = crisis_resources("en-US")
    italian = crisis_resources("it-IT")

    assert english != italian
    assert crisis_resources("fr-FR") == english
    for text in (english, italian):
        for resource in (
            "112",
            "911",
            "988",
            "chat.988lifeline.org",
            "1-800-222-1222",
            "1-800-799-7233",
            "88788",
            "1522",
            "02 2327 2327",
        ):
            assert resource in text
    assert "cannot monitor you" in english
    assert "non può tenerti d'occhio" in italian


def test_interactive_chat_prints_tool_input_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.consent_version = "alpha-4"
    store.save_app_state(state)
    inputs = iter(["Please remember this.", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    def respond(_: str, *, on_event: object) -> SimpleNamespace:
        on_event(  # type: ignore[operator]
            TurnStreamEvent(
                TurnStreamKind.TOOL_INPUT,
                'TOOL INPUT · record_user_reports\n{"content": "detail"}',
            )
        )
        on_event(  # type: ignore[operator]
            TurnStreamEvent(
                TurnStreamKind.TOOL_OUTPUT,
                'TOOL OUTPUT · record_user_reports · success\n{"staged": 1}',
            )
        )
        on_event(  # type: ignore[operator]
            TurnStreamEvent(TurnStreamKind.REPLY, "Done.")
        )
        return SimpleNamespace(text="Done.", notice=None, tool_trace=None)

    session = SimpleNamespace(respond=respond, locale="en-US", extract_pending=lambda: 0)

    assert _chat(session, store) == 0  # type: ignore[arg-type]

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "TOOL INPUT · record_user_reports" in output
    assert '"content": "detail"' in output
    assert "TOOL OUTPUT · record_user_reports · success" in output
    assert output.index("TOOL INPUT") < output.index("filo> Done.")


def test_plain_chat_does_not_persist_rejected_draft_in_redirected_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.consent_version = "alpha-4"
    store.save_app_state(state)
    inputs = iter(["Please answer.", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))

    def respond(_: str, *, on_event: object) -> SimpleNamespace:
        on_event(TurnStreamEvent(TurnStreamKind.REPLY, "rejected draft"))  # type: ignore[operator]
        on_event(TurnStreamEvent(TurnStreamKind.REPLY, "Validated reply."))  # type: ignore[operator]
        return SimpleNamespace(text="Validated reply.", notice=None)

    session = SimpleNamespace(respond=respond, locale="en-US", extract_pending=lambda: 0)
    assert _chat(session, store) == 0  # type: ignore[arg-type]

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "rejected draft" not in output
    assert output.count("filo> Validated reply.") == 1


def test_plain_chat_retries_the_failed_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.consent_version = "alpha-4"
    store.save_app_state(state)
    inputs = iter(["/retry", "Busy week.", "/retry", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    received: list[str] = []

    def respond(text: str, *, on_event: object) -> SimpleNamespace:
        received.append(text)
        if len(received) == 1:
            session.failed_text = text
            raise auth.LoginRequired("Not logged in.")
        session.failed_text = None
        return SimpleNamespace(text=f"Reply to {text}", notice=None)

    session = SimpleNamespace(
        respond=respond,
        locale="en-US",
        extract_pending=lambda: 0,
        failed_text=None,
        memory_mode=MemoryMode.STANDARD,
    )
    assert _chat(session, store) == 0  # type: ignore[arg-type]

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert received == ["Busy week.", "Busy week."]
    assert output.count("Nothing to retry.") == 1
    assert "filo auth login" in output
    assert "filo> Reply to Busy week." in output
    assert store.last_failure()["kind"] == "login"  # type: ignore[index]


def test_plain_chat_survives_a_database_error_during_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.consent_version = "alpha-4"
    store.save_app_state(state)
    inputs = iter(["First.", "Second.", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    received: list[str] = []

    def respond(text: str, *, on_event: object) -> SimpleNamespace:
        received.append(text)
        return SimpleNamespace(text=f"Reply to {text}", notice=None)

    def extract_pending() -> int:
        raise sqlite3.OperationalError("database is locked")

    session = SimpleNamespace(respond=respond, locale="en-US", extract_pending=extract_pending)
    assert _chat(session, store) == 0  # type: ignore[arg-type]

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert received == ["First.", "Second."]
    assert "filo> Reply to Second." in output
    assert "Memory extraction error: OperationalError" in output


def test_plain_chat_replaces_draft_in_interactive_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.consent_version = "alpha-4"
    store.save_app_state(state)
    inputs = iter(["Please answer.", "/quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    updates: list[str] = []

    class FakeConsole:
        is_terminal = True

        def print(self, _: str) -> None:
            pass

    class FakeLive:
        def __init__(self, _: str, *, console: object, **_kwargs: object) -> None:
            self.console = console

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def update(self, text: str, *, refresh: bool) -> None:
            assert refresh
            updates.append(text)

    def respond(_: str, *, on_event: object) -> SimpleNamespace:
        on_event(TurnStreamEvent(TurnStreamKind.REPLY, "rejected draft"))  # type: ignore[operator]
        on_event(TurnStreamEvent(TurnStreamKind.REPLY, "Validated reply."))  # type: ignore[operator]
        return SimpleNamespace(text="Validated reply.", notice=None)

    monkeypatch.setattr("filo.cli.Console", FakeConsole)
    monkeypatch.setattr("filo.cli.Live", FakeLive)

    session = SimpleNamespace(respond=respond, locale="en-US", extract_pending=lambda: 0)
    assert _chat(session, store) == 0  # type: ignore[arg-type]
    assert updates == ["filo> rejected draft", "filo> Validated reply."]


def test_export_outputs_all_user_owned_layers(tmp_path: Path, capsys: object) -> None:
    store = MemoryStore(tmp_path)
    session = store.start_session()
    store.save_turn(
        session,
        "Busy month",
        "What made it busy?",
        [
            ModelRequest(parts=[UserPromptPart(content="Busy month")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "record_user_reports",
                        {"content": "Busy month"},
                        tool_call_id="record",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        "record_user_reports",
                        {"staged": 1},
                        tool_call_id="record",
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="What made it busy?")]),
        ],
    )

    assert main(["--data-dir", str(tmp_path), "export"]) == 0

    exported = json.loads(capsys.readouterr().out.splitlines()[-1])  # type: ignore[attr-defined]
    assert exported["messages"][0]["content"] == "Busy month"
    assert exported["sessions"][0]["id"] == session.id
    assert exported["messages"][1]["tool_exchanges"] == [
        {
            "direction": "input",
            "tool_name": "record_user_reports",
            "content": {"content": "Busy month"},
        },
        {
            "direction": "output",
            "tool_name": "record_user_reports",
            "content": {"staged": 1},
            "outcome": "success",
        },
    ]
    assert "case_formulation" in exported
    assert "interventions" in exported


def test_doctor_does_not_create_memory(tmp_path: Path) -> None:
    data_dir = tmp_path / "not-created"

    assert main(["--data-dir", str(data_dir), "doctor"]) == 0
    assert not data_dir.exists()


def test_doctor_reports_login_service_and_last_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    store = _paired_telegram(tmp_path)
    auth.save_credential(
        store,
        auth.CodexCredential(
            access_token="synthetic-access",
            refresh_token="synthetic-refresh",
            expires_at=int(datetime.now(UTC).timestamp()) - 60,
            account_id="account",
        ),
    )
    store.record_failure("usage_limit", "ModelHTTPError")
    monkeypatch.setattr(
        "filo.cli.telegram_service.status",
        lambda: SimpleNamespace(installed=True, running=False, detail=""),
    )

    assert main(["--data-dir", str(tmp_path), "doctor"]) == 0

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "ChatGPT login: access token expired at" in output
    assert "refreshes on the next request" in output
    assert "Telegram service: installed but not running" in output
    assert "Last failed turn: usage_limit (ModelHTTPError) at" in output
    assert "synthetic-access" not in output


def test_doctor_without_login_or_failures(tmp_path: Path, capsys: object) -> None:
    MemoryStore(tmp_path)

    assert main(["--data-dir", str(tmp_path), "doctor"]) == 0

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "ChatGPT login: not logged in" in output
    assert "Last failed turn: none recorded" in output
    assert "Telegram service" not in output


def test_context_window_override_is_bounded() -> None:
    parsed = build_parser().parse_args(
        ["chat", "--model", "test:model", "--context-window-tokens", "128000"]
    )

    assert parsed.context_window_tokens == 128_000
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["chat", "--model", "test:model", "--context-window-tokens", "128001"]
        )


def test_ollama_context_window_is_detected_and_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def read(self) -> bytes:
            return json.dumps({"model_info": {"model.context_length": 262_144}}).encode()

    monkeypatch.setattr("filo.cli.urlopen", lambda *args, **kwargs: Response())

    assert _model_context_window("ollama:test") == 128_000


def test_ollama_conversation_is_forced_to_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.default_model = "ollama:test"
    state.default_context_window_tokens = 32_000
    state.default_locale = "en-US"
    state.embedding_model = DEFAULT_EMBEDDING_MODEL
    state.consent_version = "alpha-2"
    store.save_app_state(state)
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setattr("filo.cli._default_embedder", lambda **_: object())
    monkeypatch.setattr("filo.cli.ChatSession", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("filo.cli._chat", lambda *_: 0)

    assert main(["--data-dir", str(tmp_path), "chat", "--plain"]) == 0
    assert os.environ["OLLAMA_BASE_URL"] == "http://localhost:11434/v1"


def test_context_override_cannot_exceed_saved_model_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.default_model = "test:model"
    state.default_context_window_tokens = 32_000
    state.default_locale = "en-US"
    state.embedding_model = DEFAULT_EMBEDDING_MODEL
    store.save_app_state(state)
    captured: dict[str, int] = {}

    def chat_session(*args: object, context_window_tokens: int, **kwargs: object) -> object:
        captured["context_window_tokens"] = context_window_tokens
        return object()

    monkeypatch.setattr("filo.cli._default_embedder", lambda **_: object())
    monkeypatch.setattr("filo.cli.ChatSession", chat_session)
    monkeypatch.setattr("filo.cli._chat", lambda *_: 0)

    assert (
        main(
            [
                "--data-dir",
                str(tmp_path),
                "chat",
                "--context-window-tokens",
                "64000",
            ]
        )
        == 0
    )
    assert captured["context_window_tokens"] == 32_000


def test_tui_preloads_embedding_model_before_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.default_model = "test:model"
    state.embedding_model = DEFAULT_EMBEDDING_MODEL
    state.consent_version = "alpha-2"
    store.save_app_state(state)
    embedder = object()
    verified: list[object] = []

    monkeypatch.setattr("filo.cli._uses_tui", lambda _: True)
    monkeypatch.setattr("filo.cli._default_embedder", lambda **_: embedder)
    monkeypatch.setattr(
        "filo.cli._verify_embedding_inference",
        lambda value: verified.append(value) or 1024,
    )
    monkeypatch.setattr("filo.cli.ChatSession", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("filo.cli._chat_tui", lambda *_: 0)

    assert main(["--data-dir", str(tmp_path), "chat"]) == 0
    assert verified == [embedder]


def test_telegram_service_install_uses_saved_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.default_model = "test:model"
    state.default_locale = "en-US"
    state.embedding_model = DEFAULT_EMBEDDING_MODEL
    state.telegram_allowed_user_id = 42
    store.save_secret("telegram_bot_token", b"secret")
    store.save_app_state(state)
    captured: list[tuple[list[str], Path, dict[str, str]]] = []
    monkeypatch.setattr(
        "filo.cli.telegram_service.install",
        lambda command, data_dir, environment: (
            captured.append((command, data_dir, environment)) or tmp_path / "service.plist"
        ),
    )
    monkeypatch.setenv("FILO_LOCAL_BASE_URL", "http://rig.invalid:8080/v1")
    monkeypatch.setattr("filo.cli.sys.argv", [str(tmp_path / "filo")])
    (tmp_path / "filo").write_text("#!/bin/sh\n")
    (tmp_path / "filo").chmod(0o755)

    assert main(["--data-dir", str(tmp_path), "telegram-service", "install"]) == 0

    command, data_dir, environment = captured[0]
    assert command[0] == str((tmp_path / "filo").resolve())
    assert command[-1] == "telegram"
    assert command[1:3] == ["--data-dir", str(tmp_path.resolve())]
    assert data_dir == tmp_path.resolve()
    assert environment == {
        "FILO_LOCAL_BASE_URL": "http://rig.invalid:8080/v1",
        "PYTHONUNBUFFERED": "1",
    }
    assert "installed and started" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_telegram_service_status_does_not_create_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    monkeypatch.setattr(
        "filo.cli.telegram_service.status",
        lambda: SimpleNamespace(installed=False, running=False, detail=""),
    )

    assert main(["--data-dir", str(tmp_path), "telegram-service", "status"]) == 1

    assert not (tmp_path / "filo.db").exists()
    assert "Installed: no" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_semantic_memory_is_mandatory_for_conversation_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured_models: list[str | None] = []

    def memory_store(directory: Path, *, embedding_model: str | None = None) -> MemoryStore:
        configured_models.append(embedding_model)
        return MemoryStore(directory)

    monkeypatch.setattr("filo.cli.MemoryStore", memory_store)

    assert main(["--data-dir", str(tmp_path), "telegram", "--model", "test"]) == 2
    assert configured_models == [None]
    with pytest.raises(SystemExit):
        build_parser().parse_args(["chat", "--model", "test", "--no-semantic-memory"])


def test_telegram_fails_closed_without_channel_credentials(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.default_model = "test"
    state.default_locale = "en-US"
    state.embedding_model = DEFAULT_EMBEDDING_MODEL
    store.save_app_state(state)
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "filo.cli._default_embedder", lambda **_: object()
    )
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)  # type: ignore[attr-defined]

    assert main(["--data-dir", str(tmp_path), "telegram", "--model", "test"]) == 2
    assert "Run `filo setup`" in capsys.readouterr().out  # type: ignore[attr-defined]


def _paired_telegram(path: Path) -> MemoryStore:
    store = MemoryStore(path)
    store.save_secret("telegram_bot_token", b"synthetic-token")
    state = store.load_app_state()
    state.telegram_allowed_user_id = 42
    store.save_app_state(state)
    return store


def test_a_second_telegram_listener_exits_with_a_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    _paired_telegram(tmp_path)
    monkeypatch.setattr("filo.cli._conversation", lambda *_: pytest.fail("must not start"))

    with exclusive_lock(tmp_path / "telegram-poller.lock"):
        assert main(["--data-dir", str(tmp_path), "telegram"]) == 2

    assert "already running" in capsys.readouterr().out  # type: ignore[attr-defined]


def test_telegram_listener_starts_despite_missing_setup_and_reports_it_per_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _paired_telegram(tmp_path)  # no login, no default model, no semantic memory
    channels: list[Any] = []
    monkeypatch.setattr("filo.cli.TelegramChannel.run", lambda channel: channels.append(channel))

    assert main(["--data-dir", str(tmp_path), "telegram"]) == 0

    (channel,) = channels
    assert channel.session.model.model_name == DEFAULT_CODEX_MODEL.removeprefix("codex:")
    with pytest.raises(SetupProblem, match="filo setup"):
        channel.store._embedder.embed_query_sync("work calls")

    # Once setup is complete, the same running listener embeds without a restart.
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.embedding_model = DEFAULT_EMBEDDING_MODEL
    store.save_app_state(state)
    with pytest.raises(SetupProblem, match="filo memory model install"):
        channel.store._embedder.embed_query_sync("work calls")
    monkeypatch.setattr(
        "filo.cli._default_embedder",
        lambda **_: Embedder(TestEmbeddingModel(dimensions=8)),
    )
    assert len(channel.store._embedder.embed_query_sync("work calls").embeddings[0]) == 8


def test_setup_stops_when_semantic_memory_cannot_be_prepared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("filo.cli._prepare_semantic_memory", lambda: False)

    assert main(["--data-dir", str(tmp_path), "setup"]) == 2
    assert not (tmp_path / "filo.db").exists()
    assert not (tmp_path / "memory.key").exists()


def test_setup_does_not_pair_while_a_listener_polls_the_bot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    selections = iter(["test", "en-US", True])
    monkeypatch.setattr("filo.cli._prepare_semantic_memory", lambda: True)
    monkeypatch.setattr(
        "filo.cli.questionary.select",
        lambda *args, **kwargs: SimpleNamespace(ask=lambda: next(selections)),
    )
    monkeypatch.setattr(
        "filo.cli.questionary.text",
        lambda *args, **kwargs: SimpleNamespace(ask=lambda: kwargs["default"]),
    )
    monkeypatch.setattr(
        "filo.cli.questionary.password",
        lambda *args, **kwargs: SimpleNamespace(ask=lambda: "secret-bot-token"),
    )
    monkeypatch.setattr("filo.cli._pair_telegram_user", lambda _token: pytest.fail("must not poll"))
    tmp_path.mkdir(exist_ok=True)

    with exclusive_lock(tmp_path / "telegram-poller.lock"):
        assert main(["--data-dir", str(tmp_path), "setup"]) == 2

    assert "listener is running" in capsys.readouterr().out  # type: ignore[attr-defined]
    assert MemoryStore(tmp_path).load_secret("telegram_bot_token") is None


def test_setup_saves_encrypted_defaults_used_by_telegram(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    selections = iter(["test", "en-US", True, True, False])
    monkeypatch.setattr("filo.cli._prepare_semantic_memory", lambda: True)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "filo.cli.questionary.select",
        lambda *args, **kwargs: type("Prompt", (), {"ask": lambda _: next(selections)})(),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "filo.cli.questionary.text",
        lambda *args, **kwargs: type("Prompt", (), {"ask": lambda _: kwargs["default"]})(),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "filo.cli.questionary.password",
        lambda *args, **kwargs: type("Prompt", (), {"ask": lambda _: "secret-bot-token"})(),
    )
    monkeypatch.setattr("filo.cli.secrets.token_urlsafe", lambda _: "pair-code")  # type: ignore[attr-defined]

    class PairingBot:
        def __init__(self, token: str) -> None:
            assert token == "secret-bot-token"

        def get_me(self) -> dict[str, str]:
            return {"username": "test_filo_bot"}

        def delete_webhook(self) -> None:
            pass

        def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict]:
            if offset == -1:
                return []
            return [
                {
                    "update_id": 7,
                    "message": {
                        "from": {"id": 42, "first_name": "Test", "is_bot": False},
                        "chat": {"id": 42, "type": "private"},
                        "text": "/start pair-code",
                    },
                }
            ]

    monkeypatch.setattr("filo.cli.TelegramBot", PairingBot)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "filo.cli.TelegramChannel.run", lambda _: None
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "filo.cli._default_embedder", lambda **_: object()
    )
    monkeypatch.delenv("FILO_MODEL", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delenv("FILO_LOCALE", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)  # type: ignore[attr-defined]

    assert main(["--data-dir", str(tmp_path), "setup"]) == 0
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    assert state.default_model == "test"
    assert state.default_context_window_tokens == 128_000
    assert state.default_locale == "en-US"
    assert state.embedding_model == DEFAULT_EMBEDDING_MODEL
    assert state.telegram_allowed_user_id == 42
    assert store.load_secret("telegram_bot_token") == b"secret-bot-token"
    assert "secret-bot-token" not in json.dumps(store.export())
    assert b"secret-bot-token" not in store.database_path.read_bytes()

    assert main(["--data-dir", str(tmp_path), "telegram"]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "Configuration saved securely" in output


def test_setup_can_install_telegram_background_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object
) -> None:
    selections = iter(["test", "en-US", True, True, True])
    monkeypatch.setattr("filo.cli._prepare_semantic_memory", lambda: True)
    monkeypatch.setattr(
        "filo.cli.questionary.select",
        lambda *args, **kwargs: type("Prompt", (), {"ask": lambda _: next(selections)})(),
    )
    monkeypatch.setattr(
        "filo.cli.questionary.text",
        lambda *args, **kwargs: type("Prompt", (), {"ask": lambda _: kwargs["default"]})(),
    )
    monkeypatch.setattr(
        "filo.cli.questionary.password",
        lambda *args, **kwargs: type("Prompt", (), {"ask": lambda _: "secret-bot-token"})(),
    )
    monkeypatch.setattr("filo.cli.secrets.token_urlsafe", lambda _: "pair-code")

    class PairingBot:
        def __init__(self, _: str) -> None:
            pass

        def get_me(self) -> dict[str, str]:
            return {"username": "test_bot"}

        def delete_webhook(self) -> None:
            pass

        def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict]:
            if offset == -1:
                return []
            return [
                {
                    "update_id": 7,
                    "message": {
                        "from": {"id": 42, "first_name": "Test", "is_bot": False},
                        "chat": {"id": 42, "type": "private"},
                        "text": "/start pair-code",
                    },
                }
            ]

    installed: list[list[str]] = []
    executable = tmp_path / "filo"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    monkeypatch.setattr("filo.cli.TelegramBot", PairingBot)
    monkeypatch.setattr("filo.cli.sys.argv", [str(executable)])
    monkeypatch.setattr(
        "filo.cli.telegram_service.install",
        lambda command, _, environment: installed.append(command) or "native service",
    )

    assert main(["--data-dir", str(tmp_path), "setup"]) == 0

    assert installed[0][-1] == "telegram"
    assert "background service installed and started" in capsys.readouterr().out


def test_setup_stores_remote_provider_key_encrypted(tmp_path: Path, monkeypatch: object) -> None:
    selections = iter(["openai:gpt-5.6-sol", "en-US", False])
    monkeypatch.setattr("filo.cli._prepare_semantic_memory", lambda: True)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "filo.cli.questionary.select",
        lambda *args, **kwargs: type("Prompt", (), {"ask": lambda _: next(selections)})(),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "filo.cli.questionary.text",
        lambda *args, **kwargs: type("Prompt", (), {"ask": lambda _: kwargs["default"]})(),
    )
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "filo.cli.questionary.password",
        lambda *args, **kwargs: type("Prompt", (), {"ask": lambda _: "private-api-key"})(),
    )

    assert main(["--data-dir", str(tmp_path), "setup"]) == 0

    store = MemoryStore(tmp_path)
    assert store.load_secret("openai_api_key") == b"private-api-key"
    assert store.load_app_state().embedding_model == DEFAULT_EMBEDDING_MODEL
    assert "private-api-key" not in json.dumps(store.export())
    assert b"private-api-key" not in store.database_path.read_bytes()


def test_setup_displays_and_overwrites_saved_context_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.default_model = "test"
    state.default_context_window_tokens = 32_000
    state.default_locale = "en-US"
    store.save_app_state(state)
    selections = iter(["test", "en-US", False])
    context_prompt: dict[str, object] = {}
    monkeypatch.setattr("filo.cli._prepare_semantic_memory", lambda: True)
    monkeypatch.setattr(
        "filo.cli.questionary.select",
        lambda *args, **kwargs: type("Prompt", (), {"ask": lambda _: next(selections)})(),
    )

    def text_prompt(*args: object, **kwargs: object) -> object:
        context_prompt.update(kwargs)
        return type("Prompt", (), {"ask": lambda _: "64000"})()

    monkeypatch.setattr("filo.cli.questionary.text", text_prompt)

    assert main(["--data-dir", str(tmp_path), "setup"]) == 0

    saved = MemoryStore(tmp_path).load_app_state()
    assert context_prompt["default"] == "32000"
    assert "16000-128000" in str(context_prompt["instruction"])
    assert saved.default_context_window_tokens == 64_000


def test_setup_does_not_persist_provider_secret_when_later_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selections = iter(["openai:gpt-5.6-sol", None])
    monkeypatch.setattr("filo.cli._prepare_semantic_memory", lambda: True)
    monkeypatch.setattr(
        "filo.cli.questionary.select",
        lambda *args, **kwargs: type("Prompt", (), {"ask": lambda _: next(selections)})(),
    )
    monkeypatch.setattr(
        "filo.cli.questionary.password",
        lambda *args, **kwargs: type("Prompt", (), {"ask": lambda _: "must-not-persist"})(),
    )

    assert main(["--data-dir", str(tmp_path), "setup"]) == 1
    store = MemoryStore(tmp_path)
    assert store.load_secret("openai_api_key") is None
    assert store.load_app_state().default_model is None


def test_setup_keeps_listener_state_written_while_it_waits_at_a_prompt(
    tmp_path: Path, monkeypatch: object
) -> None:
    store = MemoryStore(tmp_path)
    store.save_secret("telegram_bot_token", b"secret-bot-token")
    state = store.load_app_state()
    state.telegram_allowed_user_id = 42
    state.telegram_update_offset = 5
    store.save_app_state(state)
    selections = iter(["test", "en-US", True, False])

    def listener_writes_meanwhile(*args: object, **kwargs: object) -> object:
        running = MemoryStore(tmp_path)
        current = running.load_app_state()
        current.telegram_update_offset = 99
        current.telegram_consent_version = "listener-consent"
        running.save_app_state(current)
        return type("Prompt", (), {"ask": lambda _: kwargs["default"]})()

    monkeypatch.setattr("filo.cli._prepare_semantic_memory", lambda: True)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "filo.cli.questionary.select",
        lambda *args, **kwargs: type("Prompt", (), {"ask": lambda _: next(selections)})(),
    )
    monkeypatch.setattr("filo.cli.questionary.text", listener_writes_meanwhile)  # type: ignore[attr-defined]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "filo.cli.questionary.password",
        lambda *args, **kwargs: type("Prompt", (), {"ask": lambda _: ""})(),
    )
    monkeypatch.delenv("FILO_MODEL", raising=False)  # type: ignore[attr-defined]
    monkeypatch.delenv("FILO_LOCALE", raising=False)  # type: ignore[attr-defined]

    assert main(["--data-dir", str(tmp_path), "setup"]) == 0

    state = MemoryStore(tmp_path).load_app_state()
    assert state.default_model == "test"
    assert state.telegram_allowed_user_id == 42
    assert state.telegram_update_offset == 99
    assert state.telegram_consent_version == "listener-consent"
