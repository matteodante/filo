import json
import tempfile
from pathlib import Path
from typing import Any

from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_evals import Dataset
from pydantic_evals.evaluators import EqualsExpected

from filo.chat import ChatSession
from filo.memory import MemoryStore
from filo.protocol import ProtocolPack

CASES_PATH = Path(__file__).parent / "cases" / "process_contracts.yaml"
PACK_PATH = Path("protocols/transdiagnostic")


def test_deterministic_process_contracts(tmp_path: Path) -> None:
    loaded = Dataset[dict[str, Any], dict[str, Any], dict[str, Any]].from_file(CASES_PATH)

    def run_case(inputs: dict[str, Any]) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(dir=tmp_path) as directory:
            store = MemoryStore(Path(directory))
            tool_path: list[str] = []

            async def stream(messages: list[Any], _info: Any):
                returned = any(
                    getattr(part, "part_kind", "") == "tool-return"
                    for message in messages
                    for part in message.parts
                )
                if inputs.get("tool_calls") and not returned:
                    tool_path.extend(
                        call["name"]
                        for call in inputs["tool_calls"]
                        if call["name"] not in tool_path
                    )
                    yield {
                        index: DeltaToolCall(
                            name=call["name"],
                            json_args=json.dumps(call["arguments"]),
                            tool_call_id=f"call-{index}",
                        )
                        for index, call in enumerate(inputs["tool_calls"])
                    }
                else:
                    yield inputs["model_reply"]

            try:
                turn = ChatSession(
                    FunctionModel(stream_function=stream),
                    ProtocolPack.load(PACK_PATH),
                    store,
                    inputs["locale"],
                ).respond(inputs["user_text"])
            except AgentRunError:
                return {
                    "completed": False,
                    "memory_count": len(store.list_claims()),
                    "reply": "",
                    "tool_path": tool_path,
                }
            return {
                "completed": True,
                "memory_count": len(store.list_claims()),
                "reply": turn.text,
                "tool_path": tool_path,
            }

    dataset = Dataset(
        name=loaded.name,
        cases=loaded.cases,
        evaluators=[EqualsExpected()],
    )
    report = dataset.evaluate_sync(run_case, progress=False)

    assert not report.failures, report.render(include_errors=True)
    assert all(result.value for case in report.cases for result in case.assertions.values()), (
        report.render(include_input=True, include_output=True, include_reasons=True)
    )
