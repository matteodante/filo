import asyncio
from pathlib import Path

from textual.containers import VerticalScroll
from textual.widgets import Input, Markdown, Static

from filo.chat import ChatTurn, TurnStreamEvent, TurnStreamKind
from filo.memory import MemoryStore
from filo.tui import FiloApp


class FakeSession:
    context_window_tokens = 128_000

    def respond(self, text: str, *, on_event: object) -> ChatTurn:
        on_event(  # type: ignore[operator]
            TurnStreamEvent(TurnStreamKind.TOOL_INPUT, "TOOL INPUT · test\n{}")
        )
        on_event(  # type: ignore[operator]
            TurnStreamEvent(TurnStreamKind.TOOL_OUTPUT, "TOOL OUTPUT · test · success\n{}")
        )
        on_event(  # type: ignore[operator]
            TurnStreamEvent(TurnStreamKind.REPLY, "**Streaming")
        )
        on_event(  # type: ignore[operator]
            TurnStreamEvent(TurnStreamKind.REPLY, "**Streaming reply.**")
        )
        return ChatTurn("**Streaming reply.**", notice="Context notice.")


def test_tui_loads_history_and_renders_streamed_markdown(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    session = store.start_session()
    store.save_turn(session, "Earlier user message.", "**Earlier reply.**", [])
    app = FiloApp(FakeSession(), store, lambda *_: False)  # type: ignore[arg-type]

    async def scenario() -> None:
        async with app.run_test() as pilot:
            assert app.title == "Filo"
            assert len(app.query(".user")) == 1
            assert len(app.query(".assistant")) == 1

            await pilot.click("#input")
            await pilot.press(*"New message.", "enter")
            for _ in range(20):
                await pilot.pause(0.01)
                if not app.query_one("#input", Input).disabled:
                    break

            assert len(app.query(".user")) == 2
            assert len(app.query(".tool")) == 2
            reply = app.query(".assistant").last(Markdown).source
            assert reply.startswith("**Filo**")
            assert reply.endswith("**Streaming reply.**")
            assert app.query(".notice").last(Static).content == "Context notice."
            assert not app.query_one("#input", Input).disabled

    asyncio.run(scenario())


def test_tui_opens_at_latest_history(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    session = store.start_session()
    for index in range(20):
        store.save_turn(
            session,
            f"Earlier user message {index} with enough text to fill the viewport.",
            f"Earlier assistant reply {index} with enough text to fill the viewport.",
            [],
        )
    app = FiloApp(FakeSession(), store, lambda *_: False)  # type: ignore[arg-type]

    async def scenario() -> None:
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            conversation = app.query_one("#conversation", VerticalScroll)
            assert conversation.max_scroll_y > 0
            assert conversation.is_vertical_scroll_end

    asyncio.run(scenario())
