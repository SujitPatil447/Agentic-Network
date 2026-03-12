"""Jira tools for the Triage Agent — create tickets, add details."""

from jira import JIRA
from langchain_core.tools import tool

from src.config import settings


def _get_jira_client() -> JIRA:
    return JIRA(
        server=settings.jira_url,
        basic_auth=(settings.jira_user_email, settings.jira_api_token),
    )


@tool
def create_jira_ticket(
    summary: str,
    description: str,
    issue_type: str = "Bug",
    priority: str = "Medium [P3]",
    labels: str = "",
) -> str:
    """Create a Jira ticket in the configured project.

    Args:
        summary: Short title for the ticket (one line).
        description: Full description with root cause, affected services, severity, etc.
        issue_type: Jira issue type — 'Bug', 'Task', 'Story'. Default 'Bug'.
        priority: Priority name — 'Critical [P1]', 'High [P2]', 'Medium [P3]', 'Low [P4]'. Default 'Medium [P3]'.
        labels: Comma-separated labels to attach (e.g. 'triage-agent,production-issue').
    """
    client = _get_jira_client()

    # Map shorthand severity to Jira priority names
    priority_map = {
        "highest": "Critical [P1]",
        "high": "High [P2]",
        "medium": "Medium [P3]",
        "low": "Low [P4]",
        "lowest": "Low [P4]",
    }
    resolved_priority = priority_map.get(priority.lower(), priority)

    fields = {
        "project": {"key": settings.jira_project_key},
        "summary": summary,
        "description": description,
        "issuetype": {"name": issue_type},
        "priority": {"name": resolved_priority},
    }

    if labels.strip():
        fields["labels"] = [l.strip() for l in labels.split(",")]

    issue = client.create_issue(fields=fields)

    return (
        f"Created {issue.key}: {summary}\n"
        f"URL: {settings.jira_url}/browse/{issue.key}"
    )


@tool
def add_jira_comment(issue_key: str, comment: str) -> str:
    """Add a comment to an existing Jira ticket.

    Args:
        issue_key: The Jira issue key (e.g. 'ENG-123').
        comment: The comment body (supports Jira wiki markup).
    """
    client = _get_jira_client()
    client.add_comment(issue_key, comment)
    return f"Comment added to {issue_key}."


# Collect all Jira tools
jira_tools = [
    create_jira_ticket,
    add_jira_comment,
]
