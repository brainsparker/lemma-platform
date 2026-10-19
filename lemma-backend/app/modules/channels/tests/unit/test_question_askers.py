"""Every question a line can ask has something that takes its answer.

A question's ref names its asker by prefix (``tool:``, ``wf:``, ``msg:``), and
answering looks the asker up by that prefix (``answers_for``). A prefix nobody
registered is a question whose answer is recorded and acted on by nothing --
the paused run never resumes, the workflow never moves. So: every module that
asks a question (``NewQuestion``) is listed here with the ref it asks under,
and each of those refs finds its asker once the worker's modules are loaded.
A new asker fails the first test until it is added below, and the second
until it registers.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from app.modules.agent.domain.asked_questions import message_ref
from app.modules.agent.services.pause_questions import tool_ref
from app.modules.workflow.infrastructure.form_questions import form_ref

APP = Path(__file__).resolve().parents[4]

#: Each module that asks, and a ref of the kind it asks under.
ASKERS: dict[str, Callable[[], str]] = {
    "modules/agent/services/pause_questions.py": lambda: tool_ref(uuid4(), "call"),
    "modules/agent/tools/messaging/pydantic_adapter.py": lambda: message_ref(
        uuid4(), "call"
    ),
    "modules/workflow/infrastructure/form_questions.py": lambda: form_ref(
        uuid4(), "node", uuid4()
    ),
}


def _asks_a_question(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "NewQuestion"
        for node in ast.walk(tree)
    )


def test_every_module_that_asks_a_question_is_listed() -> None:
    asking = {
        str(path.relative_to(APP))
        for path in APP.rglob("*.py")
        if "tests" not in path.parts and _asks_a_question(path)
    }
    assert asking == set(ASKERS)


def test_every_ref_asked_under_has_a_registered_asker() -> None:
    from app.modules.channels.services.questions import answerer, registered_askers
    from app.worker import register_worker_modules

    register_worker_modules()

    for module, ref_of in ASKERS.items():
        ref = ref_of()
        assert answerer(ref) is not None, f"{module} asks {ref!r}: nothing answers it"
    assert {ref_of().partition(":")[0] for ref_of in ASKERS.values()} <= (
        registered_askers()
    )
