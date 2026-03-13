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
    evidence: str
    mitigation: str
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
You are a PRODUCTION TRIAGE AGENT performing structured incident investigation.

You have access to Datadog tools: search_logs, list_triggered_monitors,
get_monitor_details, and search_events.

## Constraints
- Every Datadog tool call MUST include the 'cluster' parameter. Never omit it.
- Always use the timeframe provided by the user when searching logs.
- DO NOT invent data, IDs, tags, or conclusions not supported by actual Datadog results.
- NEVER jump to root cause from a single log line or single monitor — validate
  with at least two signals whenever possible.
- Explicitly label each conclusion as FACT, LIKELY ROOT CAUSE, or HYPOTHESIS.

## Investigation Workflow

### Step 1 — Check triggered monitors
Call list_triggered_monitors to see if any monitors are alerting/warning.
If monitors are found, use get_monitor_details for the most relevant ones.

### Step 2 — Search error logs
Call search_logs with the user's query. Look for:
  - Recurring error patterns (same exception, same status code)
  - Affected services and endpoints
  - Error frequency and timing

### Step 3 — Check for recent deployments/changes
Call search_events to look for deploy, restart, scaling, or config-change events
in the same time window. Correlate event timestamps with the onset of errors.

### Step 4 — Slice by dimensions
If errors are found, make additional search_logs calls to narrow down:
  - By specific service (service:<name>)
  - By status code (e.g. status:error @http.status_code:500)
  - By host or pod if a pattern suggests one instance is unhealthy
Keep queries minimal to stay within rate limits.

### Step 5 — Build hypotheses & validate
For each issue found:
  - State what was observed (FACT)
  - Propose likely cause (HYPOTHESIS or LIKELY ROOT CAUSE)
  - Note what evidence supports or weakens the hypothesis

## Output Format
Produce your output as **valid JSON only** (no markdown fences), with this schema:
{{
  "executive_summary": "What is broken, since when, and severity",
  "affected_scope": "Environment, services, versions, endpoints affected",
  "evidence": "Key metrics, log patterns, monitors, events that support findings",
  "report": "Detailed narrative of the investigation",
  "issues": [
    {{
      "id": 1,
      "title": "Short issue title",
      "severity": "high | medium | low",
      "service": "affected-service-name",
      "summary": "Detailed description including root cause status (fact/hypothesis/likely)",
      "evidence": "Specific log messages, monitor names, or event data supporting this issue",
      "mitigation": "Suggested immediate action (rollback, restart, isolate, etc.)"
    }}
  ],
  "observability_gaps": "Missing monitors, tags, dashboards, or deployment markers noticed"
}}

Be precise and ground every finding in actual data from the tools.
If nothing is found, return an empty issues list with a brief report.
"""

CREATE_TICKETS_PROMPT = """\
You are a TRIAGE AGENT creating Jira tickets for accepted production issues.

For EACH issue provided, create a Jira ticket using the create_jira_ticket tool with:
- summary: The issue title
- description: A well-formatted description including:
  * Executive Summary — What is broken and severity
  * Evidence — Specific log messages, monitor alerts, events that confirm the issue
  * Root Cause Analysis — What was found (fact vs hypothesis)
  * Affected Service & Scope
  * Immediate Mitigation — Suggested actions (rollback, restart, isolate, etc.)
  * Permanent Fix — What should be done long-term
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
                evidence=item.get("evidence", ""),
                mitigation=item.get("mitigation", ""),
                accepted=False,
            )
        )

    return Command(
        update={
            "investigation_report": _build_report(parsed),
            "issues": issues,
            "status": "awaiting_human_review",
        },
        goto="human_review",
    )


def _build_report(parsed: dict) -> str:
    """Build a human-readable report from the structured investigation output."""
    parts = []
    if parsed.get("executive_summary"):
        parts.append(f"**Executive Summary:** {parsed['executive_summary']}")
    if parsed.get("affected_scope"):
        parts.append(f"**Affected Scope:** {parsed['affected_scope']}")
    if parsed.get("evidence"):
        parts.append(f"**Evidence:** {parsed['evidence']}")
    if parsed.get("report"):
        parts.append(f"\n{parsed['report']}")
    if parsed.get("observability_gaps"):
        parts.append(f"**Observability Gaps:** {parsed['observability_gaps']}")
    return "\n\n".join(parts) if parts else parsed.get("report", "No report generated.")


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
        f"Service: {i['service']} | Details: {i['summary']}\n"
        f"  Evidence: {i.get('evidence', 'N/A')}\n"
        f"  Mitigation: {i.get('mitigation', 'N/A')}"
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
