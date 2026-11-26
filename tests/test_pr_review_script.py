"""Tests for the GitHub Actions PR review helper script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

SCRIPT_PATH = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "pr_review.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pr_review", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


PR_REVIEW = _load_script()


def _make_pr(
    number: int,
    *,
    base_ref: str = "main",
    head_ref: str = "",
    author: str = "Widar91",
    association: str = "COLLABORATOR",
    cross_repo: bool = False,
    draft: bool = False,
) -> object:
    return PR_REVIEW.PullRequest(
        number=number,
        title=f"PR #{number}",
        author=author,
        author_association=association,
        is_cross_repo=cross_repo,
        is_draft=draft,
        base_ref=base_ref,
        head_ref=head_ref or f"branch-{number}",
        head_oid=f"{number:040d}",
    )


def test_eligibility_rejects_forks_drafts_and_non_collaborators() -> None:
    assert PR_REVIEW.is_eligible_pr(_make_pr(1))
    assert not PR_REVIEW.is_eligible_pr(_make_pr(2, cross_repo=True))
    assert not PR_REVIEW.is_eligible_pr(_make_pr(3, draft=True))
    assert not PR_REVIEW.is_eligible_pr(_make_pr(4, association="NONE"))
    assert not PR_REVIEW.is_eligible_pr(_make_pr(5, author="other"), frozenset({"Widar91"}))
    assert PR_REVIEW.is_eligible_pr(_make_pr(6, author="Widar91"), frozenset({"Widar91"}))


def test_order_pr_stack_puts_base_before_children() -> None:
    top = _make_pr(177, base_ref="b174", head_ref="b177")
    mid = _make_pr(174, base_ref="b162", head_ref="b174")
    base = _make_pr(162, base_ref="main", head_ref="b162")
    other = _make_pr(165, base_ref="main", head_ref="b165")

    ordered = PR_REVIEW.order_pr_stack([top, other, mid, base])
    assert [p.number for p in ordered] == [162, 165, 174, 177]


def test_has_review_marker_matches_exact_sha() -> None:
    sha = "a" * 40
    other = "b" * 40
    texts = [f"LGTM\n\n{PR_REVIEW.marker_for(sha)}"]
    assert PR_REVIEW.has_review_marker(texts, sha)
    assert not PR_REVIEW.has_review_marker(texts, other)


def test_summarize_check_runs_states() -> None:
    assert PR_REVIEW.summarize_check_runs([]).state == "pending"
    pending_run = {"name": "check", "status": "in_progress"}
    assert PR_REVIEW.summarize_check_runs([pending_run]).state == "pending"

    failed = PR_REVIEW.summarize_check_runs(
        [
            {"name": "lint and types", "status": "completed", "conclusion": "success"},
            {
                "name": "test (Python 3.11)",
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://example.test/run/1",
            },
            {"name": "PR review", "status": "in_progress"},
        ]
    )
    assert failed.state == "failed"
    assert failed.failed_checks == [("test (Python 3.11)", "https://example.test/run/1")]

    passed = PR_REVIEW.summarize_check_runs(
        [
            {"name": "check", "status": "completed", "conclusion": "success"},
            {"name": "smoke", "status": "completed", "conclusion": "skipped"},
        ]
    )
    assert passed.state == "passed"


def test_select_model_adapts_to_pr_size_and_scope() -> None:
    small_docs = [{"path": "docs/notes.md", "additions": 400, "deletions": 20}]
    assert PR_REVIEW.select_model(small_docs)[0] == PR_REVIEW.SMALL_MODEL

    small_logic = [{"path": "src/scrooge/overview.py", "additions": 20, "deletions": 5}]
    assert PR_REVIEW.select_model(small_logic)[0] == PR_REVIEW.SMALL_MODEL

    critical_logic = [{"path": "src/scrooge/validation.py", "additions": 70, "deletions": 20}]
    assert PR_REVIEW.select_model(critical_logic)[0] == PR_REVIEW.LARGE_MODEL

    large_logic = [{"path": "frontend/src/routes/+page.svelte", "additions": 220, "deletions": 50}]
    assert PR_REVIEW.select_model(large_logic)[0] == PR_REVIEW.LARGE_MODEL
