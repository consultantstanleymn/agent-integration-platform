"""Build and drive the case workflow graph.

The graph wires the pure node functions in ``aip.graph.nodes`` into a state
machine. It pauses at ``human_approval`` with LangGraph ``interrupt`` so a team
lead can approve or reject a proposal, and it resumes from a checkpointer after
a process restart.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from aip.graph import nodes
from aip.graph.nodes import NodeDeps
from aip.graph.state import CaseState, Decision

NODE_NAMES = (
    "classify",
    "retrieve",
    "propose",
    "check_authority",
    "human_approval",
    "act",
    "record",
)


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


def _route_after_check(state: CaseState) -> str:
    if state.status == "refused":
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
    builder.add_node("classify", lambda state: nodes.classify(state, deps))
    builder.add_node("retrieve", lambda state: nodes.retrieve(state, deps))
    builder.add_node("propose", lambda state: nodes.propose(state, deps))
    builder.add_node("check_authority", lambda state: nodes.check_authority(state, deps))
    builder.add_node("human_approval", lambda state: _human_approval(state, deps))
    builder.add_node("act", lambda state: nodes.act(state, deps))
    builder.add_node("record", lambda state: nodes.record(state, deps))

    builder.add_edge(START, "classify")
    builder.add_edge("classify", "retrieve")
    builder.add_edge("retrieve", "propose")
    builder.add_edge("propose", "check_authority")
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


def sqlite_checkpointer(path: str) -> SqliteSaver:
    connection = sqlite3.connect(path, check_same_thread=False)
    return SqliteSaver(connection)


@contextmanager
def postgres_checkpointer(url: str) -> Iterator[PostgresSaver]:
    conn_string = url.replace("postgresql+psycopg://", "postgresql://", 1)
    with PostgresSaver.from_conn_string(conn_string) as saver:
        saver.setup()
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
