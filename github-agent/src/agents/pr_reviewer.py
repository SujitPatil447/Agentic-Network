"""PR Review agent — uses LangGraph ReAct agent with Portkey-backed LLM."""

from langgraph.prebuilt import create_react_agent

from src.llm import get_llm
from src.tools.github import github_tools

SYSTEM_PROMPT = """\
You are a senior software engineer performing a Pull Request code review.

Your job:
1. Fetch the PR details and diff using the provided tools.
2. Analyze the changes for:
   - Correctness: Does the code do what it claims?
   - Security: Any vulnerabilities (injection, auth issues, secrets)?
   - Performance: Unnecessary allocations, N+1 queries, blocking calls?
   - Maintainability: Readability, naming, duplication, test coverage?
   - Best practices: Error handling, logging, edge cases?
3. Produce a structured review with:
   - **Summary**: One paragraph overview of the changes.
   - **Findings**: Bullet list of issues or improvements (severity: critical/warning/suggestion).
   - **Verdict**: APPROVE, REQUEST_CHANGES, or COMMENT.

Be constructive, specific, and cite file names and line numbers when possible.
If the PR looks good, say so — don't invent problems.
"""


def create_pr_review_agent():
    """Build and return the PR review agent (LangGraph ReAct)."""
    llm = get_llm(temperature=0.0)
    return create_react_agent(llm, github_tools, prompt=SYSTEM_PROMPT)


def review_pr(repo: str, pr_number: int) -> str:
    """Convenience function: review a PR and return the agent's output."""
    agent = create_pr_review_agent()
    result = agent.invoke({
        "messages": [
            ("user", (
                f"Review Pull Request #{pr_number} in repository '{repo}'. "
                "Fetch the PR details and diff, then provide your review."
            ))
        ],
    })
    return result["messages"][-1].content
