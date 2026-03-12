"""Entrypoint — run agents from CLI or import for AgentCore wrapping later."""

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from src.agents.pr_reviewer import review_pr  # noqa: E402


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

    args = parser.parse_args()

    if args.command == "review-pr":
        print(f"\n🔍 Reviewing PR #{args.pr_number} in {args.repo}...\n")
        output = review_pr(args.repo, args.pr_number)
        print("\n--- Review ---\n")
        print(output)

        if args.post_comment:
            from src.tools.github import add_pr_review_comment

            result = add_pr_review_comment.invoke({
                "repo_full_name": args.repo,
                "pr_number": args.pr_number,
                "body": f"## 🤖 AI Code Review\n\n{output}",
            })
            print(f"\n{result}")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
