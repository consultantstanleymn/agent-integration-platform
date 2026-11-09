"""Build and drive the case workflow graph.

The graph wires the pure node functions in ``aip.graph.nodes`` into a state
machine. It pauses at ``human_approval`` with LangGraph ``interrupt`` so a team
lead can approve or reject a proposal, and it resumes from a checkpointer after
a process restart.
"""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from aip.graph import nodes
from aip.graph.nodes import NodeDeps
from aip.graph.state import CaseState, Decision
from aip.observability import span

SETUP_LOCK_ID = 764201  # arbitrary constant for the checkpoint setup lock

NODE_NAMES = (
    "classify",
    "retrieve",
    "propose",
    "check_authority",
    "human_approval",
    "act",
    "record",
)


def _traced(name: str, fn: Callable[[CaseState], dict[str, Any]]) -> Any:
    """Run a node inside a span named graph.node.<name>."""

    def run(state: CaseState) -> dict[str, Any]:
        with span(f"graph.node.{name}", tenant_id=state.tenant_id):
            return fn(state)

    return run


def _human_approval(state: CaseState, deps: NodeDeps) -> dict[str, Any]:
    payload = interrupt(
        {
            "thread_id": state.thread_id,
            "proposal": state.proposal.model_dump() if state.proposal else None,
        }
    )
    decision = Decision.model_validate(payload)
    approved = decision.decision == "approve"
    return {
        "decision": decision,
        "status": "running" if approved else "rejected",
    }


def _stopped(state: CaseState) -> bool:
    return state.status in ("refused", "budget_exceeded")


def _route_or_record(next_node: str) -> Callable[[CaseState], str]:
    def route(state: CaseState) -> str:
        return "record" if _stopped(state) else next_node

    return route


def _route_after_check(state: CaseState) -> str:
    if _stopped(state):
        return "record"
    if state.needs_approval:
        return "human_approval"
    return "act"


def _route_after_approval(state: CaseState) -> str:
    if state.decision is not None and state.decision.decision == "approve":
        return "act"
    return "record"


def build_graph(
    deps: NodeDeps,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    builder = StateGraph(CaseState)
    builder.add_node("classify", _traced("classify", lambda state: nodes.classify(state, deps)))
    builder.add_node("retrieve", _traced("retrieve", lambda state: nodes.retrieve(state, deps)))
    builder.add_node("propose", _traced("propose", lambda state: nodes.propose(state, deps)))
    builder.add_node(
        "check_authority", _traced("check_authority", lambda state: nodes.check_authority(state, deps))
    )
    builder.add_node("human_approval", _traced("human_approval", lambda state: _human_approval(state, deps)))
    builder.add_node("act", _traced("act", lambda state: nodes.act(state, deps)))
    builder.add_node("record", _traced("record", lambda state: nodes.record(state, deps)))

    builder.add_edge(START, "classify")
    for source, target in (("classify", "retrieve"), ("retrieve", "propose"), ("propose", "check_authority")):
        builder.add_conditional_edges(source, _route_or_record(target), {target: target, "record": "record"})
    builder.add_conditional_edges(
        "check_authority",
        _route_after_check,
        {"record": "record", "human_approval": "human_approval", "act": "act"},
    )
    builder.add_conditional_edges(
        "human_approval",
        _route_after_approval,
        {"act": "act", "record": "record"},
    )
    builder.add_edge("act", "record")
    builder.add_edge("record", END)

    return builder.compile(checkpointer=checkpointer)


def case_serde() -> JsonPlusSerializer:
    """Serializer that names the state classes the checkpointer may restore."""
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("aip.graph.state", "Proposal"),
            ("aip.graph.state", "Decision"),
            ("aip.graph.state", "Ticket"),
            ("aip.graph.state", "CaseState"),
        ]
    )


def sqlite_checkpointer(path: str) -> SqliteSaver:
    connection = sqlite3.connect(path, check_same_thread=False)
    return SqliteSaver(connection, serde=case_serde())


@contextmanager
def postgres_checkpointer(url: str) -> Iterator[PostgresSaver]:
    conn_string = url.replace("postgresql+psycopg://", "postgresql://", 1)
    with PostgresSaver.from_conn_string(conn_string) as saver:
        saver.serde = case_serde()
        # Several API workers start together. Take an advisory lock so only one runs the setup DDL.
        conn: Any = saver.conn
        conn.execute("SELECT pg_advisory_lock(%s)", (SETUP_LOCK_ID,))
        try:
            saver.setup()
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (SETUP_LOCK_ID,))
        yield saver


def _config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


def run_case(graph: CompiledStateGraph, state: CaseState) -> CaseState:
    config = _config(state.thread_id)
    graph.invoke(state.model_dump(), config)
    snapshot = graph.get_state(config)
    result = CaseState.model_validate(snapshot.values)
    if snapshot.next == ("human_approval",):
        result.status = "awaiting_approval"
    return result


def resume_case(graph: CompiledStateGraph, thread_id: str, decision: dict[str, Any]) -> CaseState:
    config = _config(thread_id)
    graph.invoke(Command(resume=decision), config)
    snapshot = graph.get_state(config)
    return CaseState.model_validate(snapshot.values)
