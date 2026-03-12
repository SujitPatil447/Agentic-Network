"""GitHub tools for LangChain agents — uses PyGithub for real API access."""

from github import Auth, Github
from langchain_core.tools import tool

from src.config import settings


def _get_github_client() -> Github:
    auth = Auth.Token(settings.github_token)
    return Github(auth=auth)


@tool
def get_pr_details(repo_full_name: str, pr_number: int) -> str:
    """Get the title, body, and metadata of a Pull Request.

    Args:
        repo_full_name: Repository in 'owner/repo' format (e.g. 'octocat/Hello-World').
        pr_number: The PR number.
    """
    g = _get_github_client()
    repo = g.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)
    return (
        f"PR #{pr.number}: {pr.title}\n"
        f"Author: {pr.user.login}\n"
        f"State: {pr.state}\n"
        f"Base: {pr.base.ref} ← Head: {pr.head.ref}\n"
        f"Changed files: {pr.changed_files}, "
        f"Additions: {pr.additions}, Deletions: {pr.deletions}\n\n"
        f"Description:\n{pr.body or '(no description)'}"
    )


@tool
def get_pr_diff(repo_full_name: str, pr_number: int) -> str:
    """Get the full diff of a Pull Request (file-by-file patches).

    Args:
        repo_full_name: Repository in 'owner/repo' format.
        pr_number: The PR number.
    """
    g = _get_github_client()
    repo = g.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)
    files = pr.get_files()

    diff_parts = []
    for f in files:
        header = f"--- {f.filename} ({f.status}, +{f.additions}/-{f.deletions})"
        patch = f.patch or "(binary or empty)"
        diff_parts.append(f"{header}\n{patch}")

    return "\n\n".join(diff_parts)


@tool
def get_pr_comments(repo_full_name: str, pr_number: int) -> str:
    """Get existing review comments on a Pull Request.

    Args:
        repo_full_name: Repository in 'owner/repo' format.
        pr_number: The PR number.
    """
    g = _get_github_client()
    repo = g.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)
    comments = pr.get_review_comments()

    if comments.totalCount == 0:
        return "No review comments yet."

    parts = []
    for c in comments:
        parts.append(
            f"[{c.user.login}] on {c.path}:{c.original_line}\n{c.body}"
        )
    return "\n---\n".join(parts)


@tool
def add_pr_review_comment(
    repo_full_name: str, pr_number: int, body: str
) -> str:
    """Post a general review comment (issue comment) on a Pull Request.

    Args:
        repo_full_name: Repository in 'owner/repo' format.
        pr_number: The PR number.
        body: The review comment text (supports Markdown).
    """
    g = _get_github_client()
    repo = g.get_repo(repo_full_name)
    pr = repo.get_pull(pr_number)
    comment = pr.create_issue_comment(body)
    return f"Comment posted: {comment.html_url}"


# Collect all tools for easy registration with agents
github_tools = [
    get_pr_details,
    get_pr_diff,
    get_pr_comments,
    add_pr_review_comment,
]
