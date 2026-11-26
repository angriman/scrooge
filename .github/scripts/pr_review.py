"""Automated PR triage and review orchestrator for GitHub Actions.

Selects up to N open same-repo PRs from collaborators (ordered bottom-of-stack
first), asks authors to fix failing CI, or runs `agy -p` with model adaptation
(Hydrogen High for large/critical logic PRs, Hydrogen Medium otherwise) to post
reviews or push verified small fixes without auto-merging.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOG = logging.getLogger("pr_review")

SMALL_MODEL = "gemini-4-hydrogen-medium"
LARGE_MODEL = "gemini-4-hydrogen-high"
MARKER_PREFIX = "<!-- scrooge-auto-review:"
ALLOWED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "action_required", "startup_failure"})
IGNORE_CHECK_NAMES = frozenset({"PR review", "review"})
CRITICAL_PREFIXES = ("src/scrooge/storage/", "src/scrooge/extractors/")
CRITICAL_FILES = frozenset(
    {
        "src/scrooge/validation.py",
        "src/scrooge/staging.py",
        "src/scrooge/marketdata.py",
        "src/scrooge/pipeline.py",
        "src/scrooge/importing.py",
        "src/scrooge/closing.py",
        "src/scrooge/web/api.py",
        "src/scrooge/web/auth.py",
        "src/scrooge/web/app.py",
    }
)
LOGIC_PREFIXES = ("src/scrooge/", "frontend/src/")
LOGIC_SUFFIXES = (".py", ".ts", ".svelte")

REVIEW_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "outcome": {
                "type": "string",
                "enum": ["approve_ready", "small_fixes", "request_changes"],
            },
            "body": {"type": "string"},
            "commit_message": {"type": "string"},
            "comments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "line": {"type": "integer", "minimum": 1},
                        "body": {"type": "string"},
                    },
                    "required": ["path", "line", "body"],
                },
            },
        },
        "required": ["outcome", "body", "commit_message", "comments"],
    }
)


@dataclass(frozen=True)
class PullRequest:
    """Minimal metadata for an open pull request."""

    number: int
    title: str
    author: str
    author_association: str
    is_cross_repo: bool
    is_draft: bool
    base_ref: str
    head_ref: str
    head_oid: str


@dataclass(frozen=True)
class CheckSummary:
    """Aggregated CI check status for a PR head commit."""

    state: str
    failed_checks: list[tuple[str, str]]


@dataclass(frozen=True)
class ReviewSubmission:
    """Payload describing a GitHub PR review to submit."""

    event: str
    body: str
    comments: list[dict[str, object]]
    markers: list[str]


def marker_for(sha: str) -> str:
    """Return the HTML comment marker tagging a reviewed commit SHA."""
    return f"{MARKER_PREFIX} {sha} -->"


def has_review_marker(texts: list[str], sha: str) -> bool:
    """Return True if any comment or review body already marks `sha`."""
    marker = marker_for(sha)
    return any(marker in text for text in texts)


def is_eligible_pr(pr: PullRequest, allowed_authors: frozenset[str] | None = None) -> bool:
    """Return True only for non-draft same-repo PRs from trusted collaborators."""
    if pr.is_cross_repo or pr.is_draft:
        return False
    if pr.author_association not in ALLOWED_ASSOCIATIONS:
        return False
    return not (allowed_authors and pr.author not in allowed_authors)


def order_pr_stack(prs: list[PullRequest]) -> list[PullRequest]:
    """Order PRs bottom-of-stack first, breaking ties by PR number."""
    by_head = {pr.head_ref: pr for pr in prs}
    depths: dict[int, int] = {}

    def depth_of(pr: PullRequest, seen: set[int]) -> int:
        if pr.number in depths:
            return depths[pr.number]
        if pr.number in seen or pr.base_ref == "main" or pr.base_ref not in by_head:
            depths[pr.number] = 0
            return 0
        seen.add(pr.number)
        d = 1 + depth_of(by_head[pr.base_ref], seen)
        depths[pr.number] = d
        return d

    for pr in prs:
        depth_of(pr, set())

    return sorted(prs, key=lambda pr: (depths[pr.number], pr.number))


def summarize_check_runs(
    runs: list[dict[str, object]],
    ignore_names: frozenset[str] = IGNORE_CHECK_NAMES,
) -> CheckSummary:
    """Summarize GitHub check runs into `pending`, `failed`, or `passed`."""
    relevant = [r for r in runs if str(r.get("name", "")) not in ignore_names]
    if not relevant:
        return CheckSummary(state="pending", failed_checks=[])
    if any(r.get("status") != "completed" for r in relevant):
        return CheckSummary(state="pending", failed_checks=[])

    failed: list[tuple[str, str]] = []
    for run in relevant:
        if run.get("conclusion") in FAILED_CONCLUSIONS:
            name = str(run.get("name") or "unknown check")
            url = str(run.get("html_url") or "")
            failed.append((name, url))

    if failed:
        return CheckSummary(state="failed", failed_checks=failed)
    return CheckSummary(state="passed", failed_checks=[])


def select_model(files: list[dict[str, object]]) -> tuple[str, str]:
    """Choose Hydrogen High for large/critical logic PRs and Hydrogen Medium otherwise."""
    total_lines = 0
    paths: list[str] = []
    for entry in files:
        additions = entry.get("additions", 0)
        deletions = entry.get("deletions", 0)
        total_lines += (additions if isinstance(additions, int) else 0) + (
            deletions if isinstance(deletions, int) else 0
        )
        paths.append(str(entry.get("path", "")))

    touches_logic = any(
        (p.startswith(LOGIC_PREFIXES) and p.endswith(LOGIC_SUFFIXES)) or p == "pyproject.toml"
        for p in paths
    )
    touches_critical = any(p in CRITICAL_FILES or p.startswith(CRITICAL_PREFIXES) for p in paths)

    if touches_critical and total_lines >= 80:
        return LARGE_MODEL, f"critical logic ({total_lines} lines across {len(paths)} files)"
    if touches_logic and (total_lines >= 250 or len(paths) >= 8):
        return LARGE_MODEL, f"large logic PR ({total_lines} lines across {len(paths)} files)"
    return SMALL_MODEL, f"focused/small PR ({total_lines} lines across {len(paths)} files)"


def _exe(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise RuntimeError(f"Required executable not found on PATH: {name}")
    return resolved


def _run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(  # noqa: S603  # fixed executable resolved via shutil.which, shell=False
        argv,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(argv)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def _gh_json_dict(args: list[str]) -> dict[str, Any]:
    out = _run([_exe("gh"), *args]).stdout
    if not out.strip():
        return {}
    parsed = json.loads(out)
    return dict(parsed) if isinstance(parsed, dict) else {}


def _gh_json_list(args: list[str]) -> list[dict[str, Any]]:
    out = _run([_exe("gh"), *args]).stdout
    if not out.strip():
        return []
    parsed = json.loads(out)
    if not isinstance(parsed, list):
        return []
    return [dict(item) for item in parsed if isinstance(item, dict)]


def list_open_prs(repo: str) -> list[PullRequest]:
    """Fetch open pull requests from GitHub."""
    raw = _gh_json_list(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            (
                "number,title,author,authorAssociation,isCrossRepository,"
                "isDraft,baseRefName,headRefName,headRefOid"
            ),
        ]
    )
    prs: list[PullRequest] = []
    for item in raw:
        author_obj = item.get("author") or {}
        prs.append(
            PullRequest(
                number=int(item["number"]),
                title=str(item["title"]),
                author=str(author_obj.get("login", "")),
                author_association=str(item.get("authorAssociation", "")),
                is_cross_repo=bool(item.get("isCrossRepository", True)),
                is_draft=bool(item.get("isDraft", False)),
                base_ref=str(item["baseRefName"]),
                head_ref=str(item["headRefName"]),
                head_oid=str(item["headRefOid"]),
            )
        )
    return prs


def pr_review_texts(repo: str, number: int) -> list[str]:
    """Return existing comment and review bodies for a PR."""
    data = _gh_json_dict(["pr", "view", str(number), "--repo", repo, "--json", "comments,reviews"])
    texts = [str(c.get("body", "")) for c in data.get("comments", [])]
    texts.extend(str(r.get("body", "")) for r in data.get("reviews", []))
    return texts


def pr_files(repo: str, number: int) -> list[dict[str, object]]:
    """Return changed file metadata for a PR."""
    data = _gh_json_dict(["pr", "view", str(number), "--repo", repo, "--json", "files"])
    files = data.get("files", [])
    return [dict(f) for f in files if isinstance(f, dict)] if isinstance(files, list) else []


def check_runs_for_sha(repo: str, sha: str) -> list[dict[str, object]]:
    """Return GitHub Actions check runs for a commit SHA."""
    data = _gh_json_dict(["api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100"])
    runs = data.get("check_runs", [])
    if not isinstance(runs, list):
        return []
    return [
        dict(r)
        for r in runs
        if isinstance(r, dict)
        and (r.get("app") or {}).get("slug", "github-actions") == "github-actions"
    ]


def post_ci_failure_comment(
    repo: str,
    pr: PullRequest,
    failed_checks: list[tuple[str, str]],
    *,
    dry_run: bool,
) -> None:
    """Post a PR comment asking the author to address failing CI checks."""
    bullets = "\n".join(f"- [{name}]({url})" if url else f"- {name}" for name, url in failed_checks)
    body = (
        f"@{pr.author} CI failed on `{pr.head_oid[:7]}`. "
        f"Please address these failures before review:\n\n"
        f"{bullets}\n\n"
        f"{marker_for(pr.head_oid)}"
    )
    if dry_run:
        LOG.info("[dry-run] Would comment CI failure on #%d:\n%s", pr.number, body)
        return
    _run([_exe("gh"), "pr", "comment", str(pr.number), "--repo", repo, "--body", body])


def post_review(
    repo: str,
    pr: PullRequest,
    review: ReviewSubmission,
    *,
    dry_run: bool,
) -> None:
    """Post a GitHub PR review, falling back to body-only comments if line anchoring fails."""
    marker_block = "\n".join(marker_for(sha) for sha in review.markers)
    full_body = f"{review.body.strip()}\n\n{marker_block}"
    payload: dict[str, object] = {
        "commit_id": pr.head_oid,
        "event": review.event,
        "body": full_body,
        "comments": [
            {"path": str(c["path"]), "line": int(str(c["line"])), "body": str(c["body"])}
            for c in review.comments
        ],
    }
    if dry_run:
        LOG.info(
            "[dry-run] Would submit %s review on #%d:\n%s",
            review.event,
            pr.number,
            json.dumps(payload, indent=2),
        )
        return

    gh = _exe("gh")
    api_path = f"repos/{repo}/pulls/{pr.number}/reviews"
    proc = subprocess.run(  # noqa: S603  # fixed executable resolved via shutil.which, shell=False
        [gh, "api", "-X", "POST", api_path, "--input", "-"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0:
        return

    if review.comments:
        notes = "\n".join(f"- `{c['path']}:{c['line']}`: {c['body']}" for c in review.comments)
        fallback_payload = {
            "commit_id": pr.head_oid,
            "event": review.event,
            "body": f"{review.body.strip()}\n\n{notes}\n\n{marker_block}",
            "comments": [],
        }
        proc2 = subprocess.run(  # noqa: S603  # fixed executable resolved via shutil.which, shell=False
            [gh, "api", "-X", "POST", api_path, "--input", "-"],
            input=json.dumps(fallback_payload),
            text=True,
            capture_output=True,
            check=False,
        )
        if proc2.returncode == 0:
            return
        raise RuntimeError(f"Failed to post fallback review on #{pr.number}: {proc2.stderr}")

    raise RuntimeError(f"Failed to post review on #{pr.number}: {proc.stderr}")


def build_review_prompt(pr: PullRequest) -> str:
    """Build the review prompt for `agy -p` following repo conventions."""
    return (
        f"You are reviewing PR #{pr.number}: {pr.title!r} "
        f"(head `{pr.head_ref}`, base `{pr.base_ref}`).\n"
        "Read `AGENTS.md`, `.agents/skills/review-prs/SKILL.md`, and relevant sections of "
        "`docs/parser-spec.md` and `docs/webapp-plan.md`.\n"
        f"Inspect ONLY this PR's changes via `git diff origin/{pr.base_ref}...HEAD` and "
        f"`git log --oneline origin/{pr.base_ref}..HEAD`.\n\n"
        "Hard rules:\n"
        "- NEVER run `gh pr merge`, `gh pr review`, `gh pr comment`, or `git push`. "
        "The caller handles GitHub API calls and git pushes.\n"
        "- NEVER auto-merge or suggest auto-merging; a human decides when to merge.\n"
        "- Keep review comments short, technical, anchored to new-file lines in the diff, "
        "with a concrete failing case and numbers when pointing out a bug. "
        "No praise, no PR summary.\n"
        "- Never include `Co-Authored-By`, session IDs, links, or real financial numbers.\n\n"
        "Choose exactly one `outcome`:\n"
        "1. `approve_ready`: Clean PR ready for human merge. Make no edits (`git status` "
        'must stay clean). Set `commit_message` to `""` and `comments` to `[]`.\n'
        "2. `small_fixes`: Only small, mechanical, unambiguous fixes are needed (e.g., a "
        "missing test case, title/copy tweak, docstring, clamp, formatting/lint fix). Apply "
        "the minimal fix in the working tree, run `make check` (and `make web-check` if "
        "`frontend/` changed) to verify it passes, leave the edits uncommitted in the "
        "working tree, and set `commit_message` (imperative subject <= 72 chars, optional "
        "short body) and `body` describing what was fixed.\n"
        "3. `request_changes`: Major issues (wrong figures, wrong design, missing edge cases, "
        "spec disagreement, or UI changes needing manual visual inspection). Ensure "
        "`git checkout -- .` and `git clean -fd` leave the working tree clean, write a 2-3 "
        "sentence `body`, and provide line-anchored `comments` (`path`, `line`, `body`). "
        'Set `commit_message` to `""`.\n'
    )


def _run_agy_review(
    workdir: Path,
    pr: PullRequest,
    model: str,
    timeout_minutes: int,
) -> dict[str, Any]:
    agy = _exe("agy")
    settings = json.dumps({"modelProvider": "gemini", "agentMode": "auto"})
    proc = _run(
        [
            agy,
            "-p",
            build_review_prompt(pr),
            "--model",
            model,
            "--settings",
            settings,
            "--output-format",
            "json",
            "--json-schema",
            REVIEW_SCHEMA,
            "--dangerously-skip-permissions",
            "--print-timeout",
            f"{timeout_minutes}m",
        ],
        cwd=workdir,
    )
    envelope = json.loads(proc.stdout)
    if envelope.get("status") != "SUCCESS":
        raise RuntimeError(f"agy failed on #{pr.number}: {envelope.get('error', envelope)}")
    structured = envelope.get("structured_output")
    if isinstance(structured, dict):
        return dict(structured)
    return dict(json.loads(str(envelope["response"])))


def _verify_worktree_checks(workdir: Path, changed_paths: list[str]) -> tuple[bool, str]:
    make = _exe("make")
    outputs: list[str] = []
    if any(not p.startswith("frontend/") for p in changed_paths):
        sync_proc = _run([_exe("uv"), "sync", "--locked"], cwd=workdir, check=False)
        if sync_proc.returncode != 0:
            return False, f"uv sync failed:\n{sync_proc.stderr}"
        check_proc = _run([make, "check"], cwd=workdir, check=False)
        outputs.append(check_proc.stdout + "\n" + check_proc.stderr)
        if check_proc.returncode != 0:
            return False, f"`make check` failed:\n{outputs[-1]}"
    if any(p.startswith("frontend/") for p in changed_paths):
        web_proc = _run([make, "web-check"], cwd=workdir, check=False)
        outputs.append(web_proc.stdout + "\n" + web_proc.stderr)
        if web_proc.returncode != 0:
            return False, f"`make web-check` failed:\n{outputs[-1]}"
    return True, "\n".join(outputs)


def _commit_and_push_fixes(
    workdir: Path,
    pr: PullRequest,
    commit_message: str,
    *,
    dry_run: bool,
) -> str:
    git = _exe("git")
    _run([git, "add", "-A"], cwd=workdir)
    msg = commit_message.strip() or f"Address small review fixes on PR #{pr.number}"
    _run(
        [
            git,
            "-c",
            "user.name=github-actions[bot]",
            "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit",
            "-m",
            msg,
        ],
        cwd=workdir,
    )
    new_sha = _run([git, "rev-parse", "HEAD"], cwd=workdir).stdout.strip()
    if dry_run:
        LOG.info("[dry-run] Would push commit %s to origin/%s", new_sha, pr.head_ref)
        return new_sha
    _run([git, "push", "origin", f"HEAD:refs/heads/{pr.head_ref}"], cwd=workdir)
    return new_sha


def _changed_files(workdir: Path) -> list[str]:
    out = _run([_exe("git"), "status", "--porcelain"], cwd=workdir).stdout
    return [line[3:].strip() for line in out.splitlines() if len(line) > 3]


def _clean_worktree(workdir: Path) -> None:
    git = _exe("git")
    _run([git, "checkout", "--", "."], cwd=workdir, check=False)
    _run([git, "clean", "-fd"], cwd=workdir, check=False)


@dataclass(frozen=True)
class ReviewContext:
    """Target repository, PR, worktree, and model for an automated review."""

    repo: str
    workdir: Path
    pr: PullRequest
    model: str
    dry_run: bool


def _apply_review_outcome(ctx: ReviewContext, result: dict[str, Any]) -> str:
    repo, workdir, pr, model, dry_run = ctx.repo, ctx.workdir, ctx.pr, ctx.model, ctx.dry_run
    outcome = str(result.get("outcome", "request_changes"))
    body = str(result.get("body", "")).strip()
    raw_comments = result.get("comments")
    comments = (
        [dict(c) for c in raw_comments if isinstance(c, dict)]
        if isinstance(raw_comments, list)
        else []
    )
    dirty = _changed_files(workdir)

    if outcome == "small_fixes" and dirty:
        ok, check_log = _verify_worktree_checks(workdir, dirty)
        if not ok:
            LOG.warning("Small fixes on #%d failed verification; discarding edits.", pr.number)
            _clean_worktree(workdir)
            fail_body = (
                f"{body}\n\nAutomated small fix did not pass verification checks:\n"
                f"```\n{check_log[-1500:]}\n```"
            )
            post_review(
                repo,
                pr,
                ReviewSubmission("REQUEST_CHANGES", fail_body, comments, [pr.head_oid]),
                dry_run=dry_run,
            )
            return "request_changes"

        new_sha = _commit_and_push_fixes(
            workdir,
            pr,
            str(result.get("commit_message", "")),
            dry_run=dry_run,
        )
        fix_body = (
            f"Pushed small fix `{new_sha[:7]}` (reviewed with `{model}`). "
            f"Ready for human merge.\n\n{body}"
        )
        post_review(
            repo,
            pr,
            ReviewSubmission("COMMENT", fix_body, [], [pr.head_oid, new_sha]),
            dry_run=dry_run,
        )
        return "small_fixes_pushed"

    if dirty:
        _clean_worktree(workdir)

    if outcome == "approve_ready":
        ok_body = (
            f"Automated review (`{model}`) found no blocking issues. "
            f"Ready for human merge.\n\n{body}"
        ).strip()
        post_review(
            repo,
            pr,
            ReviewSubmission("COMMENT", ok_body, comments, [pr.head_oid]),
            dry_run=dry_run,
        )
        return "approve_ready"

    post_review(
        repo,
        pr,
        ReviewSubmission(
            "REQUEST_CHANGES",
            body or "Automated review requested changes.",
            comments,
            [pr.head_oid],
        ),
        dry_run=dry_run,
    )
    return "request_changes"


def process_pr(
    repo: str,
    repo_root: Path,
    pr: PullRequest,
    *,
    timeout_minutes: int,
    dry_run: bool,
) -> str:
    """Process one PR: report CI failures or run `agy` review in an isolated worktree."""
    runs = check_runs_for_sha(repo, pr.head_oid)
    summary = summarize_check_runs(runs)
    if summary.state == "pending":
        LOG.info("Skipping #%d (%s): CI checks are pending or missing.", pr.number, pr.head_oid[:7])
        return "skipped_pending"

    if summary.state == "failed":
        LOG.info("PR #%d has failed CI checks; requesting fixes from @%s.", pr.number, pr.author)
        post_ci_failure_comment(repo, pr, summary.failed_checks, dry_run=dry_run)
        return "ci_failed_commented"

    files = pr_files(repo, pr.number)
    model, reason = select_model(files)
    LOG.info("Reviewing #%d with %s (%s)", pr.number, model, reason)

    git = _exe("git")
    _run([git, "fetch", "origin", pr.head_ref, pr.base_ref], cwd=repo_root)
    tmpdir = Path(tempfile.mkdtemp(prefix=f"scrooge-pr-{pr.number}-"))
    workdir = tmpdir / "repo"
    try:
        _run([git, "worktree", "add", "--detach", str(workdir), pr.head_oid], cwd=repo_root)
        result = _run_agy_review(workdir, pr, model, timeout_minutes)
        ctx = ReviewContext(repo=repo, workdir=workdir, pr=pr, model=model, dry_run=dry_run)
        return _apply_review_outcome(ctx, result)
    finally:
        _run([git, "worktree", "remove", "--force", str(workdir)], cwd=repo_root, check=False)
        shutil.rmtree(tmpdir, ignore_errors=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Automated Scrooge PR reviewer")
    parser.add_argument("--repo", required=True, help="GitHub owner/repo")
    parser.add_argument("--max-prs", type=int, default=5, help="Soft cap of PRs per run")
    parser.add_argument("--pr", type=int, default=None, help="Optional specific PR number")
    parser.add_argument(
        "--allowed-authors",
        default="",
        help="Optional comma-separated allowlist of PR author logins",
    )
    parser.add_argument("--timeout", type=int, default=15, help="agy timeout per PR in minutes")
    parser.add_argument("--dry-run", action="store_true", help="Do not push or comment on GitHub")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the automated PR review batch."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    if not args.dry_run and not os.environ.get("GEMINI_API_KEY"):
        LOG.error("GEMINI_API_KEY is not set; add it to repository secrets first.")
        return 1

    allowed = frozenset(a.strip() for a in args.allowed_authors.split(",") if a.strip())
    prs = [pr for pr in list_open_prs(args.repo) if is_eligible_pr(pr, allowed or None)]
    if args.pr is not None:
        prs = [pr for pr in prs if pr.number == args.pr]
    ordered = order_pr_stack(prs)

    processed = 0
    repo_root = Path.cwd()
    for pr in ordered:
        if processed >= max(0, args.max_prs):
            LOG.info("Reached soft cap of %d PRs; stopping.", args.max_prs)
            break
        if has_review_marker(pr_review_texts(args.repo, pr.number), pr.head_oid):
            LOG.info("Skipping #%d (%s): already reviewed.", pr.number, pr.head_oid[:7])
            continue
        status = process_pr(
            args.repo,
            repo_root,
            pr,
            timeout_minutes=args.timeout,
            dry_run=args.dry_run,
        )
        if status != "skipped_pending":
            processed += 1
        LOG.info(
            "PR #%d finished with status=%s (%d/%d)",
            pr.number,
            status,
            processed,
            args.max_prs,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
