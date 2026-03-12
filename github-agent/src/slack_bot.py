"""Slack bot for the Triage Agent — slash command + interactive buttons for HITL."""

import json
import logging
import threading
import uuid

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from src.config import settings
from src.agents.triage import run_triage, resume_triage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = App(
    token=settings.slack_bot_token,
    signing_secret=settings.slack_signing_secret,
)


# ── /triage slash command ─────────────────────────────────────────────────────

@app.command("/triage")
def handle_triage_command(ack, command, client):
    """Handle /triage <query> — run the Datadog investigation."""
    ack()

    query = command.get("text", "").strip()
    channel = command["channel_id"]
    user = command["user_id"]

    if not query:
        client.chat_postMessage(
            channel=channel,
            text="Usage: `/triage <query>`\nExample: `/triage status:error service:auth-api`",
        )
        return

    cluster = settings.dd_cluster
    if not cluster:
        client.chat_postMessage(channel=channel, text="Error: `DD_CLUSTER` not set in config.")
        return

    # Acknowledge with a "working" message
    client.chat_postMessage(
        channel=channel,
        text=f":mag: Investigating `{query}` on cluster `{cluster}`... This may take a minute.",
    )

    # Run investigation in a background thread to avoid Slack timeout
    thread = threading.Thread(
        target=_run_investigation,
        args=(query, cluster, channel, user, client),
        daemon=True,
    )
    thread.start()


def _run_investigation(query, cluster, channel, user, client):
    """Run the triage pipeline and post results to Slack."""
    thread_id = f"triage-{uuid.uuid4().hex[:8]}"

    try:
        state = run_triage(query, cluster=cluster, thread_id=thread_id, timeframe="1d")
    except Exception as e:
        logger.exception("Triage investigation failed")
        client.chat_postMessage(channel=channel, text=f":x: Investigation failed: {e}")
        return

    report = state.get("investigation_report", "No report generated.")
    issues = state.get("issues", [])

    if not issues:
        client.chat_postMessage(
            channel=channel,
            text=f":white_check_mark: Investigation complete — no issues found.\n\n{report}",
        )
        return

    # Build the interactive message with checkboxes
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Triage Investigation Results"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": report[:2900]},
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Found {len(issues)} issue(s).* Select which ones to create Jira tickets for:"},
        },
    ]

    # Add a checkbox for each issue
    options = []
    for issue in issues:
        severity_emoji = {"high": ":red_circle:", "medium": ":large_orange_circle:", "low": ":white_circle:"}.get(
            issue.get("severity", "medium"), ":white_circle:"
        )
        # Slack checkbox text must be < 151 chars
        label = f"{severity_emoji} [{issue['id']}] {issue['title']} ({issue['service']})"
        if len(label) > 148:
            label = label[:145] + "..."
        options.append({
            "text": {
                "type": "mrkdwn",
                "text": label,
            },
            "description": {
                "type": "mrkdwn",
                "text": issue["summary"][:148],
            },
            "value": str(issue["id"]),
        })

    blocks.append({
        "type": "actions",
        "block_id": "issue_checkboxes",
        "elements": [
            {
                "type": "checkboxes",
                "action_id": "select_issues",
                "options": options,
            }
        ],
    })

    # Add approve / reject buttons
    blocks.append({
        "type": "actions",
        "block_id": "approval_buttons",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": ":white_check_mark: Create Tickets"},
                "style": "primary",
                "action_id": "approve_issues",
                "value": json.dumps({"thread_id": thread_id}),
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Select All"},
                "action_id": "select_all_issues",
                "value": json.dumps({"thread_id": thread_id, "issue_ids": [str(i["id"]) for i in issues]}),
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": ":x: Dismiss"},
                "style": "danger",
                "action_id": "dismiss_issues",
                "value": json.dumps({"thread_id": thread_id}),
            },
        ],
    })

    client.chat_postMessage(channel=channel, blocks=blocks, text="Triage results ready for review.")


# ── Track checkbox selections per message ─────────────────────────────────────

_selections: dict[str, set[str]] = {}  # message_ts -> set of issue IDs


@app.action("select_issues")
def handle_checkbox_selection(ack, body):
    """Track which issues the user has checked."""
    ack()
    message_ts = body["message"]["ts"]
    selected = body["actions"][0].get("selected_options", [])
    _selections[message_ts] = {opt["value"] for opt in selected}


# ── "Select All" button ──────────────────────────────────────────────────────

@app.action("select_all_issues")
def handle_select_all(ack, body, client):
    """Select all issues and update checkboxes."""
    ack()
    data = json.loads(body["actions"][0]["value"])
    message_ts = body["message"]["ts"]
    _selections[message_ts] = set(data["issue_ids"])

    client.chat_postMessage(
        channel=body["channel"]["id"],
        thread_ts=message_ts,
        text=f":ballot_box_with_check: All {len(data['issue_ids'])} issues selected. Click *Create Tickets* to proceed.",
    )


# ── "Create Tickets" button ──────────────────────────────────────────────────

@app.action("approve_issues")
def handle_approve(ack, body, client):
    """Resume the pipeline for selected issues and create Jira tickets."""
    ack()

    data = json.loads(body["actions"][0]["value"])
    thread_id = data["thread_id"]
    message_ts = body["message"]["ts"]
    channel = body["channel"]["id"]

    selected_ids = _selections.get(message_ts, set())

    if not selected_ids:
        client.chat_postMessage(
            channel=channel,
            thread_ts=message_ts,
            text=":warning: No issues selected. Check the boxes first, then click *Create Tickets*.",
        )
        return

    selection_str = ",".join(sorted(selected_ids))

    client.chat_postMessage(
        channel=channel,
        thread_ts=message_ts,
        text=f":hourglass_flowing_sand: Creating Jira tickets for issue(s) {selection_str}...",
    )

    # Run in background to avoid timeout
    t = threading.Thread(
        target=_create_tickets_async,
        args=(selection_str, thread_id, channel, message_ts, client),
        daemon=True,
    )
    t.start()


def _create_tickets_async(selection_str, thread_id, channel, message_ts, client):
    """Resume the triage pipeline and post ticket results."""
    try:
        state = resume_triage(selection_str, thread_id=thread_id)
    except Exception as e:
        logger.exception("Ticket creation failed")
        client.chat_postMessage(
            channel=channel, thread_ts=message_ts, text=f":x: Ticket creation failed: {e}"
        )
        return

    status = state.get("status", "unknown")
    tickets = state.get("tickets_created", [])

    if tickets:
        msg = ":white_check_mark: *Jira tickets created:*\n\n" + "\n".join(tickets)
    else:
        msg = f"Status: {status} — no tickets were created."

    client.chat_postMessage(channel=channel, thread_ts=message_ts, text=msg)

    # Clean up selection state
    _selections.pop(message_ts, None)


# ── "Dismiss" button ─────────────────────────────────────────────────────────

@app.action("dismiss_issues")
def handle_dismiss(ack, body, client):
    """User chose to dismiss all issues."""
    ack()
    message_ts = body["message"]["ts"]
    channel = body["channel"]["id"]

    client.chat_postMessage(
        channel=channel,
        thread_ts=message_ts,
        text=":no_entry_sign: Triage dismissed — no tickets created.",
    )
    _selections.pop(message_ts, None)


# ── Entry point ──────────────────────────────────────────────────────────────

def start_slack_bot():
    """Start the Slack bot. Uses Socket Mode (no public URL needed)."""
    logger.info("Starting Triage Slack bot...")
    handler = SocketModeHandler(app, settings.slack_app_token)
    handler.start()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    # Re-import after env is loaded
    from src.config import settings as _s

    # For HTTP mode (with ngrok), use this instead of Socket Mode:
    app.start(port=3000)
