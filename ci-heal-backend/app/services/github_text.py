"""Branch names we generate, and the redaction and length caps for GitHub text.

The model's `branch_name` is not an input. Callers pass a category and a commit SHA.
"""

import re

from app.services.secret_redactor import redact_outbound

AI_NOTE = "AI-generated, human review required."
TITLE_CAP = 120
BODY_CAP = 59999
_SLUG = re.compile(r"[^a-z0-9]+")


def make_fix_branch(category: str, sha: str) -> str:
    """Build `ai-fix-<slug>-<sha7>` from the category and the head commit.

    Args:
        category: Failure category, used only as a readable slug.
        sha: Head commit. The first seven hex characters are kept.

    Returns:
        A branch name that starts with `ai-fix-`.
    """
    slug = _SLUG.sub("-", category.lower()).strip("-")[:40] or "fix"
    short = "".join(ch for ch in sha if ch in "0123456789abcdefABCDEF")[:7] or "0000000"
    return f"ai-fix-{slug}-{short}"


def failure_marker(failure_id: int) -> str:
    """Return the body marker that finds an issue already opened for this failure.

    Args:
        failure_id: Primary key of the failure row.

    Returns:
        A stable token with no spaces, safe to search for.
    """
    return f"ci-heal-failure-{failure_id}"


def bound_outbound(title: str, head: str, footer: str) -> tuple[str, str]:
    """Redact a title and body, then cap them. `footer` stays at the end.

    Args:
        title: Pull-request or issue title before redaction.
        head: Body text that may be truncated.
        footer: Decision id and failure marker, appended after the cap.

    Returns:
        A title of at most 120 characters and a body shorter than 60000 characters.
    """
    clean_title = str(redact_outbound(title).text)
    clean_head = str(redact_outbound(head).text)
    if len(clean_title) > TITLE_CAP:
        clean_title = clean_title[: TITLE_CAP - 3] + "..."
    room = BODY_CAP - len(footer)
    if room < 3:
        return clean_title, footer[:BODY_CAP]
    if len(clean_head) > room:
        clean_head = clean_head[: room - 3] + "..."
    return clean_title, clean_head + footer


def compose_pull_request(
    *,
    title: str,
    category: str,
    confidence: float,
    agreement: float | None,
    reasoning: str,
    decision_id: int,
    failure_id: int,
) -> tuple[str, str]:
    """Build the pull-request title and body a human reviewer will read.

    Args:
        title: Model title, or empty to use a generated one.
        category: Failure category label.
        confidence: Classifier confidence.
        agreement: Agreement score. None is written as `n/a`.
        reasoning: Why this change was proposed.
        decision_id: Stored decision id, printed in the body.
        failure_id: Used in the idempotency marker.

    Returns:
        Redacted title and body, within the length caps.
    """
    shown = "n/a" if agreement is None else f"{agreement:.2f}"
    detail = reasoning.strip() or "No reasoning was recorded."
    head = (
        f"{AI_NOTE}\n\n"
        f"Category: {category}\n"
        f"Classifier confidence: {confidence:.2f}\n"
        f"Agreement: {shown}\n"
        f"Reasoning:\n{detail}\n"
    )
    footer = f"\nDecision: {decision_id}\n{failure_marker(failure_id)}\n"
    return bound_outbound(title.strip() or f"Fix {category}", head, footer)


def compose_issue(
    *,
    title: str,
    category: str,
    confidence: float,
    reasoning: str,
    decision_id: int,
    failure_id: int,
) -> tuple[str, str]:
    """Build the issue title and body, with the same caps as a pull request.

    Args:
        title: Model title, or empty to use a generated one.
        category: Failure category label.
        confidence: Classifier confidence.
        reasoning: Why a human needs to look.
        decision_id: Stored decision id, printed in the body.
        failure_id: Used in the idempotency marker.

    Returns:
        Redacted title and body, within the length caps.
    """
    detail = reasoning.strip() or "No reasoning was recorded."
    head = (
        f"{AI_NOTE}\n\n"
        f"Category: {category}\n"
        f"Classifier confidence: {confidence:.2f}\n"
        f"Reasoning:\n{detail}\n"
    )
    footer = f"\nDecision: {decision_id}\n{failure_marker(failure_id)}\n"
    return bound_outbound(title.strip() or f"CI failure: {category}", head, footer)


def dry_pull_url(repo: str, branch: str) -> str:
    """Return the simulated pull-request URL recorded when dry-run is on.

    Args:
        repo: `owner/name`.
        branch: The `ai-fix-*` branch that would have been pushed.

    Returns:
        A URL that is obviously not a live GitHub link.
    """
    return f"dry-run://github/{repo}/pull/{branch}"


def dry_issue_url(repo: str, failure_id: int) -> str:
    """Return the simulated issue URL recorded when dry-run is on.

    Args:
        repo: `owner/name`.
        failure_id: The failure the issue would have described.

    Returns:
        A URL that is obviously not a live GitHub link.
    """
    return f"dry-run://github/{repo}/issues/{failure_id}"
