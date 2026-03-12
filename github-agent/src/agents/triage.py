"""Triage Agent — investigate issues via Datadog, present to human, create Jira tickets.

Flow:
  1. investigate  — Agent uses Datadog tools to find & summarise issues
  2. human_review — Pipeline pauses; human accepts/rejects each issue
  3. create_tickets — For accepted issues, agent creates Jira tickets
"""

from __future__ import annotations

import json
import operator
from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command

from src.llm import get_llm
from src.tools.datadog import datadog_tools


# ── Shared state ──────────────────────────────────────────────────────────────

class Issue(TypedDict, total=False):
    id: int
    title: str
    severity: str
    service: str
    summary: str
    accepted: bool


class TriageState(TypedDict, total=False):
    query: str
    cluster: str
    timeframe: str
    investigation_report: str
    issues: list[Issue]
    accepted_issues: list[Issue]
    tickets_created: Annotated[list[str], operator.add]
    status: str


# ── System prompts ────────────────────────────────────────────────────────────

INVESTIGATE_PROMPT = """\
You are a TRIAGE AGENT investigating production issues.

You have access to Datadog tools to search logs, list triggered monitors, and
get monitor details.

IMPORTANT: Every Datadog tool call MUST include the 'cluster' parameter provided
by the user. Never omit the cluster — it scopes queries to the correct
Kubernetes cluster and avoids pulling data from unrelated projects.

Your task:
1. Use the Datadog tools to investigate the query/area provided by the user.
   Always pass the cluster value in every tool call.
   Always use the timeframe provided by the user when searching logs.
2. Identify distinct issues from the data.
3. Produce your output as **valid JSON only** (no markdown fences), with this schema:
   {{
     "report": "A paragraph summarising what you found",
     "issues": [
       {{
         "id": 1,
         "title": "Short issue title",
         "severity": "high | medium | low",
         "service": "affected-service-name",
         "summary": "Detailed description of the issue"
       }}
     ]
   }}

Be precise and ground every finding in actual log/monitor data from the tools.
If nothing is found, return an empty issues list.
"""

CREATE_TICKETS_PROMPT = """\
You are a TRIAGE AGENT creating Jira tickets for accepted issues.

For EACH issue provided, create a Jira ticket using the create_jira_ticket tool with:
- summary: The issue title
- description: A well-formatted description including:
  * Root cause / what was observed
  * Affected service
  * Severity
  * Full summary from the investigation
- issue_type: "Bug"
- priority: Map severity → Jira priority (high→High, medium→Medium, low→Low)
- labels: "triage-agent,{service}"

After creating all tickets, output a brief confirmation of what was created.
"""


# ── Node functions ────────────────────────────────────────────────────────────

def investigate(state: TriageState) -> Command:
    """Use Datadog tools to investigate and produce a list of issues."""
    from langgraph.prebuilt import create_react_agent

    llm = get_llm(temperature=0.0)
    agent = create_react_agent(llm, datadog_tools, prompt=INVESTIGATE_PROMPT)

    cluster = state.get("cluster", "default")
    timeframe = state.get("timeframe", "1h")
    result = agent.invoke(
        {"messages": [("user", f"Cluster: {cluster}\nTimeframe: {timeframe}\nInvestigate this area: {state['query']}")]}
    )

    raw = result["messages"][-1].content

    # Parse structured output
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from the response
        import re
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
        else:
            parsed = {"report": raw, "issues": []}

    issues: list[Issue] = []
    for item in parsed.get("issues", []):
        issues.append(
            Issue(
                id=item.get("id", 0),
                title=item.get("title", ""),
                severity=item.get("severity", "medium"),
                service=item.get("service", ""),
                summary=item.get("summary", ""),
                accepted=False,
            )
        )

    return Command(
        update={
            "investigation_report": parsed.get("report", raw),
            "issues": issues,
            "status": "awaiting_human_review",
        },
        goto="human_review",
    )


def human_review(state: TriageState) -> Command:
    """Pause for human review — present issues and wait for accept/reject decisions."""
    issues = state.get("issues", [])

    if not issues:
        return Command(
            update={"status": "no_issues_found", "accepted_issues": []},
            goto=END,
        )

    # Build a readable summary for the human
    display = ["=" * 60, "TRIAGE INVESTIGATION RESULTS", "=" * 60, ""]
    display.append(state.get("investigation_report", ""))
    display.append("")
    display.append(f"Found {len(issues)} issue(s):\n")

    for issue in issues:
        display.append(f"  [{issue['id']}] ({issue['severity'].upper()}) {issue['title']}")
        display.append(f"      Service: {issue['service']}")
        display.append(f"      {issue['summary'][:200]}")
        display.append("")

    display.append("Enter the IDs of issues to accept (comma-separated), or 'all' / 'none':")
    prompt_text = "\n".join(display)

    # ── HUMAN-IN-THE-LOOP: pipeline pauses here ──
    human_input = interrupt(prompt_text)

    # Parse the human's response
    accepted_ids: set[int] = set()
    response = str(human_input).strip().lower()

    if response == "all":
        accepted_ids = {issue["id"] for issue in issues}
    elif response == "none":
        accepted_ids = set()
    else:
        for part in response.replace(" ", "").split(","):
            try:
                accepted_ids.add(int(part))
            except ValueError:
                continue

    accepted = [issue for issue in issues if issue["id"] in accepted_ids]
    for issue in accepted:
        issue["accepted"] = True

    if not accepted:
        return Command(
            update={"accepted_issues": [], "status": "no_issues_accepted"},
            goto=END,
        )

    return Command(
        update={"accepted_issues": accepted, "status": "creating_tickets"},
        goto="create_tickets",
    )


def create_tickets(state: TriageState) -> Command:
    """Create Jira tickets for all accepted issues."""
    from langgraph.prebuilt import create_react_agent
    from src.tools.jira import jira_tools

    accepted = state.get("accepted_issues", [])
    if not accepted:
        return Command(update={"status": "done_no_tickets"}, goto=END)

    llm = get_llm(temperature=0.0)
    agent = create_react_agent(llm, jira_tools, prompt=CREATE_TICKETS_PROMPT)

    issues_text = "\n".join(
        f"- Issue #{i['id']}: {i['title']} | Severity: {i['severity']} | "
        f"Service: {i['service']} | Details: {i['summary']}"
        for i in accepted
    )

    result = agent.invoke(
        {"messages": [("user", f"Create Jira tickets for these accepted issues:\n{issues_text}")]}
    )

    confirmation = result["messages"][-1].content

    return Command(
        update={
            "tickets_created": [confirmation],
            "status": "done",
        },
        goto=END,
    )


# ── Graph assembly ────────────────────────────────────────────────────────────

def build_triage_graph():
    """Build and compile the triage pipeline graph."""
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver

    graph = StateGraph(TriageState)

    graph.add_node("investigate", investigate)
    graph.add_node("human_review", human_review)
    graph.add_node("create_tickets", create_tickets)

    graph.add_edge(START, "investigate")
    # Edges are handled via Command(goto=...) inside nodes

    conn = sqlite3.connect("triage_checkpoints.db", check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return graph.compile(checkpointer=checkpointer)


# ── Public API ────────────────────────────────────────────────────────────────

def run_triage(query: str, cluster: str, thread_id: str = "triage-001", timeframe: str = "1h") -> dict:
    """Start a triage investigation. Returns when pipeline pauses for human review.

    Args:
        query: What to investigate (e.g. 'status:error service:auth-api last 1h').
        cluster: Kubernetes cluster name to scope Datadog queries.
        thread_id: Unique ID for this triage run (for resuming later).
        timeframe: How far back to search — e.g. '1h', '6h', '1d'. Default '1h'.

    Returns:
        Current state dict with investigation_report, issues, and status.
    """
    app = build_triage_graph()
    config = {"configurable": {"thread_id": thread_id}}

    state = app.invoke({"query": query, "cluster": cluster, "timeframe": timeframe, "tickets_created": []}, config)
    return state


def resume_triage(human_input: str, thread_id: str = "triage-001") -> dict:
    """Resume a paused triage pipeline after human review.

    Args:
        human_input: Comma-separated issue IDs to accept, or 'all' / 'none'.
        thread_id: Same thread_id used in run_triage.

    Returns:
        Final state dict with tickets_created and status.
    """
    app = build_triage_graph()
    config = {"configurable": {"thread_id": thread_id}}

    state = app.invoke(Command(resume=human_input), config)
    return state


def get_triage_status(thread_id: str = "triage-001") -> dict:
    """Get the current state of a triage run.

    Args:
        thread_id: The thread_id of the triage run.

    Returns:
        Current state snapshot.
    """
    app = build_triage_graph()
    config = {"configurable": {"thread_id": thread_id}}

    snapshot = app.get_state(config)
    return dict(snapshot.values) if snapshot.values else {}
