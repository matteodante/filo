"""Minimal command-line interface for chat and user-controlled memory."""

import argparse
import errno
import json
import os
import secrets
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.request import Request, urlopen

os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"

import questionary
from huggingface_hub import HfApi, scan_cache_dir, snapshot_download
from huggingface_hub.errors import CacheNotFound
from pydantic_ai import Embedder
from pydantic_ai.models import Model
from pydantic_ai.models.openai import (
    OpenAIChatModel,
    OpenAIChatModelSettings,
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.providers.openai import OpenAIProvider
from rich.console import Console
from rich.live import Live

from filo import telegram_service
from filo.auth import (
    AuthError,
    codex_model,
    credential_lock,
    load_credential,
    login_codex,
    logout_codex,
)
from filo.chat import (
    MAX_CONTEXT_WINDOW_TOKENS,
    MIN_CONTEXT_WINDOW_TOKENS,
    ChatSession,
    TurnStreamEvent,
    TurnStreamKind,
    crisis_resources,
)
from filo.memory import (
    ClaimCorrection,
    ClaimFit,
    HypothesisReview,
    MemoryMode,
    MemoryStore,
    RetentionPolicy,
)
from filo.memory import (
    MemoryError as MemoryStoreError,
)
from filo.protocol import ProtocolError, ProtocolPack
from filo.telegram import TELEGRAM_SECRET, TelegramBot, TelegramChannel, TelegramError

PACKAGE_PROTOCOL = Path(__file__).resolve().parent / "protocols" / "transdiagnostic"
REPOSITORY_PROTOCOL = Path(__file__).resolve().parents[2] / "protocols" / "transdiagnostic"
DEFAULT_PROTOCOL = PACKAGE_PROTOCOL if PACKAGE_PROTOCOL.is_dir() else REPOSITORY_PROTOCOL
DEFAULT_CODEX_MODEL = "codex:gpt-5.6-sol"
DEFAULT_EMBEDDING_REPO = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_EMBEDDING_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"
DEFAULT_EMBEDDING_MODEL = (
    f"sentence-transformers:{DEFAULT_EMBEDDING_REPO}@{DEFAULT_EMBEDDING_REVISION}"
)
PROVIDER_SECRETS = {
    "openai:": ("openai_api_key", "OPENAI_API_KEY", "OpenAI API key"),
    "anthropic:": ("anthropic_api_key", "ANTHROPIC_API_KEY", "Anthropic API key"),
    "google:": ("google_api_key", "GOOGLE_API_KEY", "Google API key"),
    "openrouter:": ("openrouter_api_key", "OPENROUTER_API_KEY", "OpenRouter API key"),
}
LOCAL_PREFIX = "local:"
LOCAL_BASE_URL_ENV = "FILO_LOCAL_BASE_URL"
LOCAL_BASE_URL_DEFAULT = "http://localhost:8080/v1"
LOCAL_API_KEY_ENV = "FILO_LOCAL_API_KEY"
LOCAL_REASONING_EFFORT = "xhigh"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="filo", description="Experimental AI conversation CLI")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.getenv("FILO_DATA_DIR", Path.home() / ".filo")),
        help="Encrypted local memory directory (default: ~/.filo)",
    )
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path(os.getenv("FILO_PROTOCOL_PATH", DEFAULT_PROTOCOL)),
        help="Protocol pack directory",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("setup", help="Configure the harness interactively")

    chat = commands.add_parser("chat", help="Start an interactive conversation")
    chat.add_argument("--model")
    chat.add_argument("--locale", choices=("it-IT", "en-US"))
    chat.add_argument("--context-window-tokens", type=_context_window_tokens)
    chat.add_argument("--memory-mode", type=_memory_mode_arg, choices=tuple(MemoryMode))
    chat.add_argument(
        "--plain",
        action="store_true",
        help="Use the line-oriented interface instead of the full-screen TUI",
    )

    telegram = commands.add_parser("telegram", help="Run a private single-user Telegram bot")
    telegram.add_argument("--model")
    telegram.add_argument("--locale", choices=("it-IT", "en-US"))
    telegram.add_argument("--allowed-user-id", type=int)
    telegram.add_argument("--context-window-tokens", type=_context_window_tokens)
    telegram.add_argument("--memory-mode", type=_memory_mode_arg, choices=tuple(MemoryMode))

    service = commands.add_parser(
        "telegram-service",
        help="Run Telegram automatically as a per-user background service",
    )
    service_commands = service.add_subparsers(dest="service_command", required=True)
    service_commands.add_parser("install", help="Install and start the service")
    service_commands.add_parser("status", help="Show installation and runtime status")
    service_commands.add_parser("restart", help="Restart the installed service")
    service_commands.add_parser("uninstall", help="Stop and remove the service")

    auth = commands.add_parser("auth", help="Manage experimental ChatGPT Codex login")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    auth_commands.add_parser("login")
    auth_commands.add_parser("status")
    auth_commands.add_parser("logout")

    memory = commands.add_parser("memory", help="Inspect or edit longitudinal memory")
    memory_commands = memory.add_subparsers(dest="memory_command", required=True)
    memory_commands.add_parser("show")
    memory_commands.add_parser("case")
    memory_commands.add_parser("sessions")
    memory_commands.add_parser("interventions")
    review = memory_commands.add_parser("review")
    review.add_argument("id")
    review.add_argument(
        "fit",
        choices=(
            ClaimFit.FITS,
            ClaimFit.PARTLY_FITS,
            ClaimFit.DOES_NOT_FIT,
            ClaimFit.UNSURE,
        ),
    )
    review.add_argument("evidence")
    correct = memory_commands.add_parser("correct")
    correct.add_argument("id")
    correct.add_argument("text")
    forget = memory_commands.add_parser("forget")
    forget.add_argument("id")
    model = memory_commands.add_parser("model", help="Manage the local embedding model")
    model_commands = model.add_subparsers(dest="model_command", required=True)
    model_commands.add_parser("status")
    model_commands.add_parser("verify")
    model_commands.add_parser("install")
    remove_model = model_commands.add_parser("remove")
    remove_model.add_argument("--yes", action="store_true")

    export = commands.add_parser("export", help="Export decrypted user-owned data as JSON")
    export.add_argument("--output", type=Path)
    delete = commands.add_parser("delete-data", help="Delete conversation and structured memory")
    delete.add_argument("--yes", action="store_true")
    delete_session = commands.add_parser("delete-session", help="Delete one local session")
    delete_session.add_argument("id")
    delete_before = commands.add_parser("delete-before", help="Delete sessions before an ISO date")
    delete_before.add_argument("date", type=datetime.fromisoformat)
    privacy = commands.add_parser("privacy", help="Inspect or set the default memory mode")
    privacy_commands = privacy.add_subparsers(dest="privacy_command", required=True)
    privacy_commands.add_parser("show")
    privacy_set = privacy_commands.add_parser("set-default")
    privacy_set.add_argument("mode", type=_memory_mode_arg, choices=tuple(MemoryMode))
    retention = commands.add_parser("retention", help="Manage local retention")
    retention_commands = retention.add_subparsers(dest="retention_command", required=True)
    retention_commands.add_parser("show")
    retention_set = retention_commands.add_parser("set")
    retention_set.add_argument("--raw-message-days", type=_optional_days)
    retention_set.add_argument("--session-summary-days", type=_optional_days)
    retention_set.add_argument("--stale-hypothesis-days", type=_optional_days)
    retention_commands.add_parser("dry-run")
    retention_commands.add_parser("apply")
    commands.add_parser("doctor", help="Check local configuration without contacting a model")

    protocol = commands.add_parser("protocol", help="Protocol-pack utilities")
    protocol_commands = protocol.add_subparsers(dest="protocol_command", required=True)
    validate = protocol_commands.add_parser("validate")
    validate.add_argument("path", nargs="?", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _main(argv)
    except MemoryStoreError as error:
        print(f"Memory error: {error}")
        return 2


def _main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        pack = ProtocolPack.load(args.protocol)
    except ProtocolError as error:
        print(f"Protocol error: {error}")
        return 2

    if args.command == "protocol":
        path = args.path or args.protocol
        try:
            validated = ProtocolPack.load(path)
        except ProtocolError as error:
            print(f"Protocol error: {error}")
            return 2
        categories: dict[str, int] = {}
        for skill in validated.manifest.skills:
            categories[skill.category] = categories.get(skill.category, 0) + 1
        print(f"Protocol ID: {validated.id}")
        print(f"Status: {validated.status}")
        print(f"Population: {validated.manifest.population}")
        print(f"Skills: {len(validated.skill_ids)}")
        print(
            "Categories: "
            + ", ".join(f"{name}={count}" for name, count in sorted(categories.items()))
        )
        print(f"Reviewers: {len(validated.manifest.reviewers)}")
        print(f"Next review: {validated.manifest.next_review or 'not scheduled'}")
        print("Hash validation: OK")
        return 0

    if args.command == "memory" and args.memory_command == "model":
        return _memory_model(args)

    database_exists = (args.data_dir / "filo.db").exists()
    if args.command == "telegram-service":
        return _telegram_service(args, database_exists)
    if args.command == "doctor":
        store = MemoryStore(args.data_dir) if database_exists else None
        state = store.load_app_state() if store else None
        model = (state.default_model if state else None) or "not configured"
        memory = "initialized" if database_exists else "not initialized"
        print(f"Protocol: {pack.id} ({pack.status})")
        print(f"Memory: {args.data_dir} ({memory})")
        print(f"Model: {model}")
        if state and state.default_model:
            print(
                "Conversation context: "
                f"{state.default_context_window_tokens or MAX_CONTEXT_WINDOW_TOKENS} tokens"
            )
        semantic_configuration = (
            "configured"
            if state and state.embedding_model == DEFAULT_EMBEDDING_MODEL
            else "run `filo setup`"
        )
        _, cached_model, cache_warnings = _embedding_cache_info()
        semantic_model = (
            "installed"
            if _embedding_revision(cached_model) and not cache_warnings
            else "corrupted"
            if cache_warnings
            else "not installed"
        )
        print(f"Semantic memory: {semantic_configuration}")
        print(f"Semantic model: {semantic_model}")
        telegram = "configured" if store and _telegram_config(store) else "not configured"
        print(f"Telegram: {telegram}")
        if model.startswith(LOCAL_PREFIX):
            print(f"Local server: {_local_endpoint_report()}")
        return 0

    if args.command == "setup" and not _prepare_semantic_memory():
        return 2
    store = MemoryStore(args.data_dir)
    if args.command == "setup":
        return _setup(store, args)
    if args.command == "auth":
        return _auth(args, store)
    if args.command == "privacy":
        state = store.load_app_state()
        if args.privacy_command == "show":
            print(f"Default memory mode: {state.default_memory_mode.value}")
            print("Changing mode does not delete previously stored data.")
            return 0
        state.default_memory_mode = MemoryMode(args.mode)
        store.save_app_state(state)
        print(f"Default memory mode set to {state.default_memory_mode.value}.")
        return 0
    if args.command == "retention":
        return _retention(args, store)
    if args.command == "delete-session":
        if not store.delete_session(args.id):
            print("Session not found.")
            return 1
        print(f"Session {args.id} deleted locally.")
        return 0
    if args.command == "delete-before":
        result = store.delete_before(args.date)
        print(f"Deleted {result['sessions']} session(s) before {args.date.isoformat()}.")
        return 0
    if args.command in {"chat", "telegram"}:
        state = store.load_app_state()
        if state.embedding_model != DEFAULT_EMBEDDING_MODEL:
            print("Semantic memory is not configured. Run `filo setup`.")
            return 2
        try:
            embedder = _default_embedder(local_files_only=True)
            if args.command == "chat" and _uses_tui(args):
                _verify_embedding_inference(embedder)
            store = MemoryStore(
                args.data_dir,
                embedding_model=DEFAULT_EMBEDDING_MODEL,
                embedder=embedder,
            )
        except Exception as error:
            print(
                f"Semantic memory is unavailable ({type(error).__name__}). "
                "Run `filo memory model install`."
            )
            return 2
        selected_model = args.model or state.default_model
        if not selected_model and load_credential(store):
            selected_model = DEFAULT_CODEX_MODEL
        if not selected_model:
            print("Missing model. Run `filo setup` or pass --model.")
            return 2
        _load_provider_secret(store, selected_model)
        locale = args.locale or state.default_locale or "en-US"
        try:
            model = _conversation_model(store, selected_model)
            if selected_model.startswith("ollama:"):
                os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434/v1"
        except AuthError as error:
            print(f"Authentication error: {error}")
            return 2
        model_context_window = (
            state.default_context_window_tokens if selected_model == state.default_model else None
        ) or _model_context_window(selected_model)
        context_window_tokens = min(
            args.context_window_tokens or model_context_window,
            model_context_window,
            MAX_CONTEXT_WINDOW_TOKENS,
        )
        memory_mode = MemoryMode(args.memory_mode or state.default_memory_mode)
        if memory_mode is not MemoryMode.EPHEMERAL:
            store.apply_retention(state.retention_policy)
        conversation_store = (
            MemoryStore(
                embedding_model=DEFAULT_EMBEDDING_MODEL,
                embedder=embedder,
                ephemeral=True,
            )
            if memory_mode is MemoryMode.EPHEMERAL
            else store
        )
        try:
            session = ChatSession(
                model,
                pack,
                conversation_store,
                locale,
                context_window_tokens=context_window_tokens,
                memory_mode=memory_mode,
            )
        except ValueError as error:
            print(f"Context configuration error: {error}")
            return 2
        if args.command == "chat":
            if not _uses_tui(args):
                return _chat(session, store)
            return _chat_tui(session, store)
        token_payload = store.load_secret(TELEGRAM_SECRET)
        token = token_payload.decode() if token_payload else ""
        if not token:
            print("Telegram is not configured. Run `filo setup`.")
            return 2
        allowed_user_id = args.allowed_user_id or state.telegram_allowed_user_id
        if not allowed_user_id or allowed_user_id <= 0:
            print("Telegram is not configured. Run `filo setup`.")
            return 2
        try:
            TelegramChannel(
                TelegramBot(token),
                session,
                conversation_store,
                allowed_user_id,
                configuration_store=store,
            ).run()
        except TelegramError as error:
            print(f"Telegram startup error: {error}")
            return 2
        except KeyboardInterrupt:
            print()
        return 0
    if args.command == "memory":
        return _memory(args, store)
    if args.command == "export":
        content = json.dumps(store.export(), ensure_ascii=False, separators=(",", ":"))
        if args.output:
            args.output.write_text(content + "\n", encoding="utf-8")
            print(f"Exported to {args.output}")
        else:
            print(content)
        return 0
    if args.command == "delete-data":
        confirmed = (
            args.yes
            or input("Delete all local conversation and memory data? [y/N] ").lower() == "y"
        )
        if not confirmed:
            print("Cancelled.")
            return 1
        # A refresh in flight in a running listener would otherwise save the
        # ChatGPT credential back after it was deleted.
        with credential_lock(store):
            store.delete_all()
        print("Local conversation and memory data deleted.")
        return 0
    return 2


def _telegram_service(args: argparse.Namespace, database_exists: bool) -> int:
    try:
        if args.service_command == "status":
            current = telegram_service.status()
            print(f"Installed: {'yes' if current.installed else 'no'}")
            print(f"Running: {'yes' if current.running else 'no'}")
            if current.detail:
                print(current.detail)
            return 0 if current.running else 1
        if args.service_command == "uninstall":
            removed = telegram_service.uninstall()
            print(
                "Telegram background service removed."
                if removed
                else "Telegram background service was not installed."
            )
            return 0
        if args.service_command == "restart":
            telegram_service.restart()
            print("Telegram background service restarted.")
            return 0
        if not database_exists:
            print("Telegram is not configured. Run `filo setup` first.")
            return 2
        store = MemoryStore(args.data_dir)
        state = store.load_app_state()
        if (
            not _telegram_config(store)
            or not state.default_model
            or state.embedding_model != DEFAULT_EMBEDDING_MODEL
        ):
            print("Telegram is not fully configured. Run `filo setup` first.")
            return 2
        path = _install_telegram_service(args)
        print(f"Telegram background service installed and started: {path}")
        print("Use `filo telegram-service status` to inspect it.")
        return 0
    except telegram_service.TelegramServiceError as error:
        print(f"Telegram service error: {error}")
        return 2


def _install_telegram_service(args: argparse.Namespace) -> Path | str:
    executable = Path(sys.argv[0]).resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise telegram_service.TelegramServiceError(
            "Cannot locate the `filo` executable. Install the project and try again."
        )
    command = [
        str(executable),
        "--data-dir",
        str(args.data_dir.resolve()),
        "--protocol",
        str(args.protocol.resolve()),
        "telegram",
    ]
    return telegram_service.install(
        command,
        args.data_dir.resolve(),
        environment={
            LOCAL_BASE_URL_ENV: _local_base_url(),
            # Service output goes to a file, where Python block-buffers it and
            # diagnostics surface minutes late or vanish on a crash.
            "PYTHONUNBUFFERED": "1",
        },
    )


def _setup(store: MemoryStore, args: argparse.Namespace) -> int:
    state = store.load_app_state()
    loaded = state.model_copy(deep=True)
    install_background = False
    try:
        model = _select_model()
        provider_secret = _prompt_provider_secret(store, model)
        if provider_secret is False:
            return 2
        locale = _ask(
            questionary.select(
                "Conversation language",
                choices=[
                    questionary.Choice("Italian", value="it-IT"),
                    questionary.Choice("English", value="en-US"),
                ],
                default=state.default_locale or "en-US",
            )
        )
        model_context_window = _model_context_window(model)
        if model_context_window < MIN_CONTEXT_WINDOW_TOKENS:
            print(
                f"The selected model exposes only {model_context_window} context tokens; "
                f"at least {MIN_CONTEXT_WINDOW_TOKENS} are required."
            )
            return 2
        current_context_window = (
            state.default_context_window_tokens if model == state.default_model else None
        )
        context_window_tokens = int(
            _ask(
                questionary.text(
                    "Conversation context window (tokens)",
                    default=str(
                        min(
                            current_context_window or model_context_window,
                            model_context_window,
                        )
                    ),
                    instruction=(
                        f"Allowed range: {MIN_CONTEXT_WINDOW_TOKENS}-"
                        f"{model_context_window} for {model}"
                    ),
                    validate=lambda value: (
                        value.strip().isdigit()
                        and MIN_CONTEXT_WINDOW_TOKENS <= int(value.strip()) <= model_context_window
                    ),
                )
            ).strip()
        )

        has_telegram = _telegram_config(store)
        configure_telegram = _ask(_yes_no_select("Configure Telegram?", default=has_telegram))
        if configure_telegram:
            token = _ask(
                questionary.password(
                    "Telegram bot token",
                    instruction="Leave blank to keep the current token",
                )
            ).strip()
            token_payload = token.encode() if token else store.load_secret(TELEGRAM_SECRET)
            if not token_payload:
                print("A Telegram bot token is required.")
                return 2
            current_id = state.telegram_allowed_user_id
            if token or not current_id:
                try:
                    user_id, update_offset = _pair_telegram_user(token_payload.decode())
                except TelegramError as error:
                    print(f"Telegram setup error: {error}")
                    return 2
                state.telegram_update_offset = update_offset
            else:
                user_id = current_id
            state.telegram_allowed_user_id = user_id
            install_background = _ask(
                _yes_no_select(
                    "Install and start Telegram as a background service?",
                    default=False,
                )
            )
        else:
            token_payload = None

        state.default_model = model
        state.default_context_window_tokens = context_window_tokens
        state.default_locale = locale
        state.embedding_model = DEFAULT_EMBEDDING_MODEL
        if model.startswith("codex:") and not load_credential(store):
            login_now = _ask(_yes_no_select("Log in with ChatGPT now?", default=True))
            if login_now:
                try:
                    login_codex(store)
                except AuthError as error:
                    print(f"Authentication error: {error}")
                    return 2
        with store.transaction():
            if provider_secret:
                store.save_secret(*provider_secret)
            if token_payload:
                store.save_secret(TELEGRAM_SECRET, token_payload)
            # Setup can sit at a prompt for minutes while a listener keeps
            # writing its offset and consent, so only the fields setup changed
            # are applied to the state as it is now.
            current = store.load_app_state()
            for field in type(state).model_fields:
                if getattr(state, field) != getattr(loaded, field):
                    setattr(current, field, getattr(state, field))
            store.save_app_state(current)
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled.")
        return 1

    print(f"Configuration saved securely in {store.directory}.")
    telegram = "configured" if _telegram_config(store) else "skipped"
    print(f"Model: {model}; locale: {locale}; Telegram: {telegram}")
    if install_background:
        try:
            path = _install_telegram_service(args)
        except telegram_service.TelegramServiceError as error:
            print(f"Telegram service error: {error}")
            print("Configuration was saved; retry with `filo telegram-service install`.")
            return 2
        print(f"Telegram background service installed and started: {path}")
    if model.startswith("codex:") and not load_credential(store):
        print("Next: run `filo auth login` for ChatGPT Codex access.")
    return 0


def _select_model() -> str:
    local_models = _local_model_names()
    choices = [
        questionary.Choice(f"Local server — {name}", value=f"{LOCAL_PREFIX}{name}")
        for name in local_models
    ]
    choices.append(questionary.Choice("ChatGPT Plus/Pro — GPT-5.6 Sol", value=DEFAULT_CODEX_MODEL))
    default = choices[0].value
    return _ask(
        questionary.select(
            "Model provider",
            choices=choices,
            default=default,
        )
    )


def _prompt_provider_secret(
    store: MemoryStore, model: str
) -> tuple[str, bytes] | Literal[False] | None:
    config = _provider_secret_config(model)
    if config is None:
        return None
    secret_name, _, label = config
    current = store.load_secret(secret_name)
    value = _ask(
        questionary.password(
            label,
            instruction="Leave blank to keep the saved key" if current else None,
        )
    ).strip()
    payload = value.encode() if value else current
    if not payload:
        print(f"{label} is required.")
        return False
    return secret_name, payload


def _load_provider_secret(store: MemoryStore, model: str) -> None:
    config = _provider_secret_config(model)
    if config is None:
        return
    secret_name, environment_name, _ = config
    payload = store.load_secret(secret_name)
    if payload:
        os.environ[environment_name] = payload.decode()


def _provider_secret_config(model: str) -> tuple[str, str, str] | None:
    return next(
        (config for prefix, config in PROVIDER_SECRETS.items() if model.startswith(prefix)),
        None,
    )


def _conversation_model(store: MemoryStore, model: str) -> str | Model:
    if model.startswith("codex:"):
        return codex_model(store, model.removeprefix("codex:"))
    if model.startswith(LOCAL_PREFIX):
        return _local_model(model.removeprefix(LOCAL_PREFIX))
    if model.startswith("openai:"):
        return OpenAIResponsesModel(
            model.removeprefix("openai:"),
            settings=OpenAIResponsesModelSettings(openai_store=False),
        )
    return model


def _local_base_url() -> str:
    return os.getenv(LOCAL_BASE_URL_ENV, "").strip() or LOCAL_BASE_URL_DEFAULT


def _local_model(model_name: str) -> OpenAIChatModel:
    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(
            base_url=_local_base_url(),
            api_key=os.getenv(LOCAL_API_KEY_ENV, "").strip() or "local",
        ),
        settings=OpenAIChatModelSettings(openai_reasoning_effort=LOCAL_REASONING_EFFORT),
    )


def _local_endpoint_report() -> str:
    """Describe whether the configured `local:` server answers.

    A background service reaches this code path with a permission a terminal
    never lacks: macOS grants local network access to command-line tools and to
    launchd daemons, but not to launchd agents, which is how the Telegram
    listener is installed. The block surfaces as EHOSTUNREACH against an address
    that answers fine from a shell, so name it rather than leaving it to
    reappear later as an opaque model error.
    """
    base = _local_base_url()
    try:
        with urlopen(f"{base.rstrip('/')}/models", timeout=5):
            return f"{base} reachable"
    except OSError as error:
        reason = getattr(error, "reason", None)
        code = getattr(reason, "errno", None) or getattr(error, "errno", None)
        if code == errno.EHOSTUNREACH:
            return (
                f"{base} unreachable — no route to host. A background service may "
                "lack macOS local network permission; see AGENTS.md."
            )
        return f"{base} unreachable — {type(error).__name__}"


def _local_model_names() -> list[str]:
    try:
        with urlopen(f"{_local_base_url().rstrip('/')}/models", timeout=2) as response:
            payload = json.load(response)
    except (OSError, ValueError, TypeError):
        return []
    entries = payload.get("data")
    if not isinstance(entries, list):
        return []
    return [
        entry["id"]
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"]
    ]


def _model_context_window(model: str) -> int:
    if model.startswith("ollama:"):
        detected = _ollama_context_window(model.removeprefix("ollama:"))
        if detected is not None:
            return min(detected, MAX_CONTEXT_WINDOW_TOKENS)
    return MAX_CONTEXT_WINDOW_TOKENS


def _ollama_context_window(model: str) -> int | None:
    request = Request(
        "http://localhost:11434/api/show",
        data=json.dumps({"model": model}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=2) as response:
            payload = json.load(response)
    except (OSError, ValueError, TypeError):
        return None
    model_info = payload.get("model_info")
    if not isinstance(model_info, dict):
        return None
    values = [
        value
        for key, value in model_info.items()
        if key.endswith(".context_length") and isinstance(value, int)
    ]
    return max(values) if values else None


def _pair_telegram_user(token: str) -> tuple[int, int]:
    bot = TelegramBot(token)
    identity = bot.get_me()
    username = identity.get("username")
    if not isinstance(username, str) or not username:
        raise TelegramError("Telegram returned a bot without a username.")
    bot.delete_webhook()
    pending = bot.get_updates(-1, timeout=0)
    offset = (
        max(
            (update["update_id"] for update in pending if isinstance(update.get("update_id"), int)),
            default=-1,
        )
        + 1
    )
    code = secrets.token_urlsafe(6)
    print(f"Open https://t.me/{username}?start={code} and press Start.")
    print("Waiting for your private Telegram message (Ctrl-C to cancel)...")

    while True:
        for update in bot.get_updates(offset or None):
            update_id = update.get("update_id")
            if not isinstance(update_id, int):
                continue
            offset = update_id + 1
            message = update.get("message")
            if not isinstance(message, dict) or message.get("text") != f"/start {code}":
                continue
            sender = message.get("from")
            chat = message.get("chat")
            if (
                not isinstance(sender, dict)
                or not isinstance(chat, dict)
                or chat.get("type") != "private"
                or sender.get("is_bot") is True
                or not isinstance(sender.get("id"), int)
            ):
                continue
            label = sender.get("username") or sender.get("first_name") or sender["id"]
            confirmed = _ask(_yes_no_select(f"Connect Telegram account {label}?", default=True))
            if confirmed:
                return sender["id"], offset


def _ask(question: questionary.Question):
    answer = question.ask()
    if answer is None:
        raise KeyboardInterrupt
    return answer


def _yes_no_select(message: str, *, default: bool) -> questionary.Question:
    choices = [
        questionary.Choice("Yes", value=True),
        questionary.Choice("No", value=False),
    ]
    return questionary.select(
        message,
        choices=choices,
        default=choices[0 if default else 1],
    )


def _telegram_config(store: MemoryStore) -> bool:
    state = store.load_app_state()
    return bool(store.load_secret(TELEGRAM_SECRET) and state.telegram_allowed_user_id)


def _auth(args: argparse.Namespace, store: MemoryStore) -> int:
    try:
        if args.auth_command == "login":
            credential = login_codex(store)
            print(f"Logged in with ChatGPT Codex account {credential.account_id}.")
        elif args.auth_command == "status":
            credential = load_credential(store)
            if credential is None:
                print("Not logged in.")
                return 1
            expires = datetime.fromtimestamp(credential.expires_at, UTC).isoformat()
            print(f"Logged in with ChatGPT Codex account {credential.account_id}.")
            print(
                f"Access token expires: {expires}. Running chats and the Telegram listener "
                "refresh it automatically before expiry or after a rejected request."
            )
        elif args.auth_command == "logout":
            logout_codex(store)
            print("ChatGPT Codex credentials deleted.")
    except AuthError as error:
        print(f"Authentication error: {error}")
        return 2
    return 0


def _prepare_semantic_memory() -> bool:
    print("Preparing local semantic memory (the model downloads on first setup)...")
    try:
        _verify_embedding_inference(_default_embedder(local_files_only=False))
    except Exception as error:
        print(f"Semantic memory setup failed: {type(error).__name__}: {error}")
        return False
    print("Local semantic memory is ready.")
    return True


def _embedding_cache_info():
    try:
        cache = scan_cache_dir()
    except CacheNotFound:
        return None, None, []
    model = next(
        (
            repo
            for repo in cache.repos
            if repo.repo_type == "model" and repo.repo_id == DEFAULT_EMBEDDING_REPO
        ),
        None,
    )
    model_path = str(model.repo_path) if model else DEFAULT_EMBEDDING_REPO.replace("/", "--")
    warnings = [warning for warning in cache.warnings if model_path in str(warning)]
    return cache, model, warnings


def _embedding_revision(model):
    return (
        next(
            (
                revision
                for revision in model.revisions
                if revision.commit_hash == DEFAULT_EMBEDDING_REVISION
            ),
            None,
        )
        if model
        else None
    )


def _default_embedder(*, local_files_only: bool) -> Embedder:
    snapshot = snapshot_download(
        DEFAULT_EMBEDDING_REPO,
        revision=DEFAULT_EMBEDDING_REVISION,
        local_files_only=local_files_only,
    )
    return Embedder(f"sentence-transformers:{snapshot}")


def _verify_embedding_inference(embedder: Embedder) -> int:
    document = embedder.embed_documents_sync(
        ["I often postpone difficult phone calls."]
    ).embeddings[0]
    query = embedder.embed_query_sync("I avoid difficult calls").embeddings[0]
    if not document or len(document) != len(query):
        raise RuntimeError("embedding dimensions do not match")
    return len(document)


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def _print_embedding_model_status() -> bool:
    _, model, warnings = _embedding_cache_info()
    print(f"Model: {DEFAULT_EMBEDDING_MODEL}")
    if warnings:
        print("Status: corrupted")
        for warning in warnings:
            print(f"Warning: {warning}")
        return False
    revision = _embedding_revision(model)
    if revision is None:
        print("Status: not installed")
        print(f"Required revision: {DEFAULT_EMBEDDING_REVISION}")
        return False
    print("Status: installed")
    print(f"Location: {model.repo_path}")
    print(f"Size: {_format_size(revision.size_on_disk)}")
    print(f"Revision: {revision.commit_hash}")
    return True


def _memory_model(args: argparse.Namespace) -> int:
    if args.model_command == "status":
        return 0 if _print_embedding_model_status() else 1
    if args.model_command == "install":
        if not _prepare_semantic_memory():
            return 2
        if (args.data_dir / "filo.db").exists():
            store = MemoryStore(args.data_dir)
            state = store.load_app_state()
            state.embedding_model = DEFAULT_EMBEDDING_MODEL
            store.save_app_state(state)
            print("Semantic memory configuration updated.")
        _print_embedding_model_status()
        return 0

    cache, model, warnings = _embedding_cache_info()
    if warnings:
        _print_embedding_model_status()
        return 2
    revision = _embedding_revision(model)
    if cache is None or revision is None:
        print("Embedding model is not installed.")
        return 1 if args.model_command == "verify" else 0

    if args.model_command == "verify":
        try:
            result = HfApi().verify_repo_checksums(
                DEFAULT_EMBEDDING_REPO,
                revision=DEFAULT_EMBEDDING_REVISION,
                token=False,
            )
            if result.mismatches:
                raise RuntimeError(f"{len(result.mismatches)} checksum mismatch(es)")
            dimensions = _verify_embedding_inference(
                Embedder(f"sentence-transformers:{revision.snapshot_path}")
            )
        except Exception as error:
            print(f"Embedding model verification failed: {type(error).__name__}: {error}")
            return 2
        print(
            f"Embedding model verified: {result.checked_count} files, "
            f"{dimensions} dimensions, local inference OK."
        )
        return 0

    strategy = cache.delete_revisions(DEFAULT_EMBEDDING_REVISION)
    confirmed = (
        args.yes
        or input(
            f"Delete only {DEFAULT_EMBEDDING_REPO} from the shared Hugging Face cache "
            f"and free {strategy.expected_freed_size_str}? [y/N] "
        )
        .strip()
        .lower()
        == "y"
    )
    if not confirmed:
        print("Cancelled.")
        return 1
    strategy.execute()
    print(
        f"Embedding model removed; freed {strategy.expected_freed_size_str}. "
        "Encrypted memory data was not changed."
    )
    return 0


def _memory(args: argparse.Namespace, store: MemoryStore) -> int:
    try:
        if args.memory_command == "show":
            print(
                _json(
                    [
                        item.model_dump(mode="json")
                        for item in store.list_claims(include_inactive=True)
                    ]
                )
            )
        elif args.memory_command == "case":
            print(store.load_formulation().model_dump_json(indent=2))
        elif args.memory_command == "sessions":
            print(_json([session.model_dump() for session in store.list_sessions()]))
        elif args.memory_command == "interventions":
            print(_json([item.model_dump(mode="json") for item in store.list_interventions()]))
        elif args.memory_command == "review":
            reviewed, _ = store.review_hypotheses(
                [
                    HypothesisReview(
                        memory_id=args.id,
                        fit=ClaimFit(args.fit),
                        evidence_quote=args.evidence,
                    )
                ],
                0,
                args.evidence,
            )
            print(reviewed[0].model_dump_json(indent=2))
        elif args.memory_command == "correct":
            print(
                store.correct_claim(
                    ClaimCorrection(
                        memory_id=args.id,
                        correction_quote=args.text,
                        replacement_quote=args.text,
                    ),
                    0,
                    args.text,
                ).model_dump_json(indent=2)
            )
        elif args.memory_command == "forget":
            print(store.forget_claim(args.id).model_dump_json(indent=2))
    except (KeyError, ValueError) as error:
        print(str(error))
        return 2
    return 0


def _chat(session: ChatSession, store: MemoryStore) -> int:
    if not _ensure_chat_consent(store):
        return 1

    print("Commands: /case, /memory, /sessions [id], /interventions,")
    print("          /correct <id> <text>,")
    print("          /forget <id>, /crisis, /end, /help, /quit")
    while True:
        try:
            user_text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not user_text:
            continue
        if user_text == "/quit":
            return 0
        if user_text.startswith("/"):
            if _chat_command(user_text, session, store):
                continue
            print("Unknown command. Use /help.")
            continue
        console = Console()
        live = (
            Live(
                "",
                console=console,
                auto_refresh=False,
                transient=True,
                redirect_stdout=False,
                redirect_stderr=False,
            )
            if console.is_terminal
            else None
        )

        def render(
            event: TurnStreamEvent,
            live_display: Live | None = live,
        ) -> None:
            if event.kind is not TurnStreamKind.REPLY:
                (live_display.console.print if live_display else print)(event.text)
                return
            if live_display:
                live_display.update(f"filo> {event.text}", refresh=True)

        if live:
            live.start()
        failure: Exception | None = None
        try:
            turn = session.respond(user_text, on_event=render)
        except Exception as error:  # Provider SDKs expose different error types.
            failure = error
        finally:
            if live:
                live.stop()
        if failure is not None:
            print(f"Model error: {failure}")
            print("If you may be in danger, /crisis shows emergency resources.")
            continue
        print(f"filo> {turn.text}")
        if turn.notice:
            print(f"notice> {turn.notice}")


def _chat_tui(session: ChatSession, store: MemoryStore) -> int:
    if not _ensure_chat_consent(store):
        return 1
    from filo.tui import FiloApp

    app = FiloApp(
        session,
        store,
        lambda command, output: _chat_command(
            command,
            session,
            store,
            output=output,
        ),
    )
    app.run()
    return 0


def _uses_tui(args: argparse.Namespace) -> bool:
    return not args.plain and sys.stdin.isatty() and sys.stdout.isatty()


def _ensure_chat_consent(store: MemoryStore) -> bool:
    state = store.load_app_state()
    consent = "I UNDERSTAND"
    if state.consent_version != "alpha-4":
        notice = (
            "Filo is experimental AI for adults using it privately for self-reflection. "
            "It is not therapy, diagnosis, medical advice, emergency care, or human monitoring, "
            "and its output can be wrong. A remote model provider receives messages, successful "
            "session history, a separate bounded case-data envelope, and any dynamically loaded "
            "skill when configured. Embeddings run locally. The current memory mode is "
            f"{state.default_memory_mode.value}; local retention is configurable and does not "
            "control provider, Telegram, export, backup, or terminal copies. "
            "Exports are plaintext. "
            "By continuing, you confirm that you are at least 18 and accept these data flows."
        )
        print(notice)
        if input(f"Type {consent} to continue: ").strip() != consent:
            print("Consent not recorded.")
            return False
        state.consent_version = "alpha-4"
        store.save_app_state(state)
    return True


def _chat_command(
    command: str,
    session: ChatSession,
    store: MemoryStore,
    *,
    output: Callable[[str], None] = print,
) -> bool:
    parts = command.split(maxsplit=2)
    name = parts[0]
    try:
        if name == "/help":
            output("/case /memory /sessions [id] /correct <id> <text>")
            output("/interventions /forget <id> /crisis /end /quit")
        elif name == "/crisis":
            output(crisis_resources(session.locale))
        elif name == "/case":
            output(store.load_formulation().model_dump_json(indent=2))
        elif name == "/memory":
            output(
                _json(
                    [
                        item.model_dump(mode="json")
                        for item in store.list_claims(include_inactive=True)
                    ]
                )
            )
        elif name == "/sessions":
            sessions = store.list_sessions()
            if len(parts) == 1:
                output(_json([item.model_dump() for item in sessions]))
            else:
                found = next((item for item in sessions if item.id == parts[1]), None)
                output("Session not found." if found is None else found.model_dump_json(indent=2))
        elif name == "/interventions":
            output(_json([item.model_dump(mode="json") for item in store.list_interventions()]))
        elif name == "/correct" and len(parts) == 3:
            output(
                store.correct_claim(
                    ClaimCorrection(
                        memory_id=parts[1],
                        correction_quote=parts[2],
                        replacement_quote=parts[2],
                    ),
                    0,
                    parts[2],
                ).model_dump_json(indent=2)
            )
        elif name == "/forget" and len(parts) >= 2:
            output(store.forget_claim(parts[1]).model_dump_json(indent=2))
        elif name == "/end":
            closed = session.end()
            message = (
                "No active session." if closed is None else "Session closed; transcript preserved."
            )
            output(message)
        else:
            return False
    except (KeyError, ValueError) as error:
        output(str(error))
    return True


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _context_window_tokens(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Context window must be an integer.") from error
    if not MIN_CONTEXT_WINDOW_TOKENS <= parsed <= MAX_CONTEXT_WINDOW_TOKENS:
        raise argparse.ArgumentTypeError(
            f"Context window must be between {MIN_CONTEXT_WINDOW_TOKENS} "
            f"and {MAX_CONTEXT_WINDOW_TOKENS} tokens."
        )
    return parsed


def _memory_mode_arg(value: str) -> MemoryMode:
    try:
        return MemoryMode(value.replace("-", "_"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Memory mode must be standard, transcript-only, or ephemeral."
        ) from error


def _optional_days(value: str) -> int | None:
    if value.casefold() in {"none", "off"}:
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Retention days must be a positive integer or 'none'."
        ) from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("Retention days must be positive.")
    return parsed


def _retention(args: argparse.Namespace, store: MemoryStore) -> int:
    state = store.load_app_state()
    if args.retention_command == "show":
        print(state.retention_policy.model_dump_json(indent=2))
        return 0
    if args.retention_command == "set":
        state.retention_policy = RetentionPolicy(
            raw_message_days=args.raw_message_days,
            session_summary_days=args.session_summary_days,
            stale_hypothesis_days=args.stale_hypothesis_days,
        )
        store.save_app_state(state)
        print(state.retention_policy.model_dump_json(indent=2))
        return 0
    result = store.apply_retention(
        state.retention_policy,
        dry_run=args.retention_command == "dry-run",
    )
    label = "Would remove" if args.retention_command == "dry-run" else "Removed"
    print(
        f"{label}: {result['messages']} messages, "
        f"{result['session_summaries']} session summaries, "
        f"{result['stale_hypotheses']} stale hypotheses."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
