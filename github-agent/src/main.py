"""Entrypoint — run agents from CLI or import for AgentCore wrapping later."""

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from src.agents.pr_reviewer import review_pr  # noqa: E402
from src.agents.triage import run_triage, resume_triage, get_triage_status  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="GitHub AI Agents")
    sub = parser.add_subparsers(dest="command")

    # --- review-pr ---
    pr_parser = sub.add_parser("review-pr", help="Review a GitHub Pull Request")
    pr_parser.add_argument("repo", help="Repository in owner/repo format")
    pr_parser.add_argument("pr_number", type=int, help="PR number")
    pr_parser.add_argument(
        "--post-comment",
        action="store_true",
        help="Post the review as a comment on the PR",
    )

    # --- triage ---
    triage_parser = sub.add_parser("triage", help="Investigate issues via Datadog and create Jira tickets")
    triage_parser.add_argument("query", help="Datadog search query (e.g. 'status:error service:auth-api')")
    triage_parser.add_argument("--cluster", default=None, help="Override DD_CLUSTER from .env (e.g. 'prod-us-east-1')")
    triage_parser.add_argument("--thread-id", default="triage-001", help="Unique ID for this triage run")

    # --- triage-approve ---
    approve_parser = sub.add_parser("triage-approve", help="Accept issues from a paused triage run")
    approve_parser.add_argument("selection", help="Issue IDs to accept: comma-separated, 'all', or 'none'")
    approve_parser.add_argument("--thread-id", default="triage-001", help="Thread ID of the triage run")

    # --- triage-status ---
    status_parser = sub.add_parser("triage-status", help="Check status of a triage run")
    status_parser.add_argument("--thread-id", default="triage-001", help="Thread ID of the triage run")

    # --- slack-bot ---
    sub.add_parser("slack-bot", help="Start the Slack bot")

    args = parser.parse_args()

    if args.command == "review-pr":
        print(f"\n\U0001f50d Reviewing PR #{args.pr_number} in {args.repo}...\n")
        output = review_pr(args.repo, args.pr_number)
        print("\n--- Review ---\n")
        print(output)

        if args.post_comment:
            from src.tools.github import add_pr_review_comment

            result = add_pr_review_comment.invoke({
                "repo_full_name": args.repo,
                "pr_number": args.pr_number,
                "body": f"## \U0001f916 AI Code Review\n\n{output}",
            })
            print(f"\n{result}")

    elif args.command == "triage":
        from src.config import settings as _cfg
        cluster = args.cluster or _cfg.dd_cluster
        if not cluster:
            print("Error: No cluster specified. Set DD_CLUSTER in .env or pass --cluster.")
            sys.exit(1)
        print(f"\n\U0001f50d Investigating cluster '{cluster}': {args.query}\n")
        state = run_triage(args.query, cluster=cluster, thread_id=args.thread_id)

        print("\n" + "=" * 60)
        print(state.get("investigation_report", ""))
        print()

        issues = state.get("issues", [])
        if issues:
            print(f"Found {len(issues)} issue(s):\n")
            for issue in issues:
                print(f"  [{issue['id']}] ({issue['severity'].upper()}) {issue['title']}")
                print(f"      Service: {issue['service']}")
                print(f"      {issue['summary'][:200]}")
                print()

            # ── Human-in-the-loop: ask which issues to create tickets for ──
            print("Which issues should we create Jira tickets for?")
            selection = input("Enter IDs (comma-separated), 'all', or 'none': ").strip()

            if selection.lower() == "none":
                print("\nNo issues accepted. Done.")
            else:
                print(f"\nCreating Jira tickets for: {selection}\n")
                state = resume_triage(selection, thread_id=args.thread_id)

                print(f"Status: {state.get('status', 'unknown')}")
                for ticket in state.get("tickets_created", []):
                    print(ticket)
        else:
            print("No issues found.")

    elif args.command == "triage-approve":
        print(f"\n\u2705 Resuming triage (thread: {args.thread_id}) with selection: {args.selection}\n")
        state = resume_triage(args.selection, thread_id=args.thread_id)

        print(f"Status: {state.get('status', 'unknown')}")
        for ticket in state.get("tickets_created", []):
            print(ticket)

    elif args.command == "slack-bot":
        from src.slack_bot import start_slack_bot
        start_slack_bot()

    elif args.command == "triage-status":
        state = get_triage_status(thread_id=args.thread_id)
        if state:
            print(f"Status: {state.get('status', 'unknown')}")
            print(f"Issues found: {len(state.get('issues', []))}")
            print(f"Issues accepted: {len(state.get('accepted_issues', []))}")
            print(f"Tickets created: {len(state.get('tickets_created', []))}")
        else:
            print("No triage run found for this thread ID.")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
