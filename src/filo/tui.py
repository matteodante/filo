"""Full-screen terminal chat built from Textual's standard widgets."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import ClassVar

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Footer, Header, Input, Markdown, Static

from filo.chat import ChatSession, ChatTurn, TurnStreamEvent, TurnStreamKind
from filo.memory import MemoryStore

CommandHandler = Callable[[str, Callable[[str], None]], bool]


class FiloApp(App[None]):
    TITLE = "Filo"
    SUB_TITLE = "Experimental AI conversation"
    CSS = """
    Screen {
        background: $surface;
    }

    #conversation {
        height: 1fr;
        padding: 1 2;
        scrollbar-size: 1 1;
    }

    .message {
        width: 90%;
        height: auto;
        margin: 0 0 1 0;
        padding: 1 2;
        border: round $primary;
    }

    .user {
        margin-left: 8;
        border: round $accent;
        background: $boost;
    }

    .assistant {
        margin-right: 8;
        border: round $primary;
        background: $panel;
    }

    .tool {
        width: 82%;
        color: $warning;
        border: round $warning;
        background: $panel;
    }

    .notice {
        width: 82%;
        color: $warning;
        border: round $warning;
    }

    .error {
        width: 82%;
        color: $error;
        border: round $error;
    }

    #status {
        height: 1;
        padding: 0 2;
        color: $text-muted;
    }

    #input {
        dock: bottom;
        margin: 0 1 1 1;
    }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+q", "safe_quit", "Quit"),
        Binding("pageup", "page_up", "Scroll up", show=False),
        Binding("pagedown", "page_down", "Scroll down", show=False),
    ]

    class Streamed(Message):
        def __init__(self, event: TurnStreamEvent) -> None:
            super().__init__()
            self.event = event

    class Finished(Message):
        def __init__(self, turn: ChatTurn | None = None, error: Exception | None = None) -> None:
            super().__init__()
            self.turn = turn
            self.error = error

    class CommandOutput(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    def __init__(
        self,
        session: ChatSession,
        store: MemoryStore,
        command_handler: CommandHandler,
    ) -> None:
        super().__init__()
        self.session = session
        self.store = store
        self.command_handler = command_handler
        self._reply: Markdown | None = None
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="conversation")
        yield Static("Ready", id="status", markup=False)
        yield Input(placeholder="Write a message or /help", id="input")
        yield Footer()

    async def on_mount(self) -> None:
        active = self.store.active_session()
        if active is not None:
            for role, content in self.store.session_messages(active.id, turn_limit=50):
                await self._mount_message(role, content)
        self.query_one("#input", Input).focus()

    @on(Input.Submitted)
    async def submit(self, message: Input.Submitted) -> None:
        text = message.value.strip()
        if not text or self._busy:
            return
        message.input.clear()
        if text == "/quit":
            self.action_safe_quit()
            return
        await self._mount_message("user", text)
        self._busy = True
        message.input.disabled = True
        self.query_one("#status", Static).update("Thinking…")
        self._reply = None
        self.process_submission(text)

    @work(exclusive=True, thread=True, exit_on_error=False)
    def process_submission(self, text: str) -> None:
        try:
            if text.startswith("/"):
                handled = self.command_handler(text, self._post_command_output)
                if not handled:
                    self.post_message(self.CommandOutput("Unknown command. Use /help."))
                self.post_message(self.Finished())
                return
            turn = self.session.respond(text, on_event=self._post_stream_event)
        except Exception as error:  # Provider SDKs expose different error types.
            self.post_message(self.Finished(error=error))
            return
        # The reply is already on screen; input stays disabled until its reports are recorded,
        # so no command can run on the store while the extraction does. A failure keeps the
        # marker for the next turn and must not be shown as a failed reply.
        with suppress(Exception):
            self.session.extract_pending()
        self.post_message(self.Finished(turn=turn))

    def _post_command_output(self, output: str) -> None:
        self.post_message(self.CommandOutput(output))

    def _post_stream_event(self, event: TurnStreamEvent) -> None:
        self.post_message(self.Streamed(event))

    @on(Streamed)
    async def show_stream(self, message: Streamed) -> None:
        if message.event.kind is TurnStreamKind.REPLY:
            if self._reply is None:
                self._reply = Markdown(classes="message assistant", open_links=True)
                await self.query_one("#conversation", VerticalScroll).mount(self._reply)
            await self._reply.update(f"**Filo**\n\n{message.event.text}")
        else:
            await self._mount_static(message.event.text, "message tool")
        self._scroll_end()

    @on(CommandOutput)
    async def show_command_output(self, message: CommandOutput) -> None:
        await self._mount_static(message.text, "message notice")

    @on(Finished)
    async def finish_submission(self, message: Finished) -> None:
        if message.error is not None:
            if self._reply is not None:
                await self._reply.remove()
                self._reply = None
            await self._mount_static(
                f"Model error: {type(message.error).__name__}: {message.error}\n"
                "If you may be in danger, /crisis shows emergency resources.",
                "message error",
            )
        elif message.turn is not None:
            if message.turn.notice:
                await self._mount_static(message.turn.notice, "message notice")
            if self._reply is None:
                self._reply = Markdown(
                    f"**Filo**\n\n{message.turn.text}",
                    classes="message assistant",
                    open_links=True,
                )
                await self.query_one("#conversation", VerticalScroll).mount(self._reply)
        self._busy = False
        input_widget = self.query_one("#input", Input)
        input_widget.disabled = False
        input_widget.focus()
        self.query_one("#status", Static).update(self._context_status())
        self._scroll_end()

    async def _mount_message(self, role: str, content: str) -> None:
        if role == "assistant":
            widget = Markdown(
                f"**Filo**\n\n{content}",
                classes="message assistant",
                open_links=True,
            )
            await self.query_one("#conversation", VerticalScroll).mount(widget)
        else:
            await self._mount_static(f"You\n\n{content}", "message user")
        self._scroll_end()

    async def _mount_static(self, content: str, classes: str) -> None:
        await self.query_one("#conversation", VerticalScroll).mount(
            Static(content, classes=classes, markup=False)
        )
        self._scroll_end()

    def _context_status(self) -> str:
        active = self.store.active_session()
        if active is None:
            return "Ready · no active session"
        return (
            f"Ready · context {active.last_context_tokens}/"
            f"{self.session.context_window_tokens} estimated tokens"
        )

    def _scroll_end(self) -> None:
        self.query_one("#conversation", VerticalScroll).scroll_end(animate=False)

    def action_safe_quit(self) -> None:
        if self._busy:
            self.notify("Wait for the current response to finish.", severity="warning")
            return
        self.exit()

    def action_page_up(self) -> None:
        self.query_one("#conversation", VerticalScroll).scroll_page_up()

    def action_page_down(self) -> None:
        self.query_one("#conversation", VerticalScroll).scroll_page_down()
