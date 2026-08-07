#!/usr/bin/env python3
"""
Release helper for RCON Server Manager.

Typical flow (from a clean working tree):

  # Inspect only — safe, no side effects
  python scripts/release.py analyse
  python scripts/release.py analyse --bump minor

  # Preview README feature bullets (structure-safe; markers only)
  python scripts/release.py update-readme --dry-run

  # Full release: optional README feature append → merge develop → master, tag
  python scripts/release.py release --bump patch --yes

  # Dry-run of the full flow (prints planned git/gh commands)
  python scripts/release.py release --bump patch --dry-run

Requires: git, gh (authenticated), network access for push/release.

README updates only append bullets inside ``<!-- FEATURES:BEGIN/END -->``.
Other sections (Supported games, Quick start, Environment variables, etc.)
are never rewritten by this tool.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
TAG_RE = re.compile(r"^v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?P<pre>.*)?$")
CONVENTIONAL = re.compile(
    r"^(?P<type>feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<subject>.+)$",
    re.IGNORECASE,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from readme_features import (  # noqa: E402
    ReadmeError,
    preview_diff,
    update_readme_features,
)


class CmdError(RuntimeError):
    pass


def run(
    args: list[str],
    *,
    check: bool = True,
    capture: bool = True,
    dry_run: bool = False,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    cwd = cwd or REPO_ROOT
    pretty = " ".join(args)
    if dry_run:
        print(f"[dry-run] $ {pretty}")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    print(f"$ {pretty}")
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=capture,
        check=False,
    )
    if check and result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise CmdError(f"Command failed ({result.returncode}): {pretty}\n{err}")
    return result


def git(*args: str, dry_run: bool = False, check: bool = True) -> str:
    r = run(["git", *args], dry_run=dry_run, check=check)
    return (r.stdout or "").strip()


def gh(*args: str, dry_run: bool = False, check: bool = True) -> str:
    r = run(["gh", *args], dry_run=dry_run, check=check)
    return (r.stdout or "").strip()


@dataclass
class Version:
    major: int
    minor: int
    patch: int
    pre: str = ""

    @classmethod
    def parse(cls, tag: str) -> Version | None:
        m = TAG_RE.match(tag.strip())
        if not m:
            return None
        return cls(
            major=int(m.group("major")),
            minor=int(m.group("minor")),
            patch=int(m.group("patch")),
            pre=m.group("pre") or "",
        )

    def tag(self) -> str:
        return f"v{self.major}.{self.minor}.{self.patch}{self.pre}"

    def bump(self, kind: str) -> Version:
        if kind == "major":
            return Version(self.major + 1, 0, 0)
        if kind == "minor":
            return Version(self.major, self.minor + 1, 0)
        if kind == "patch":
            return Version(self.major, self.minor, self.patch + 1)
        raise ValueError(f"Unknown bump kind: {kind}")


@dataclass
class Commit:
    sha: str
    subject: str
    body: str = ""

    @property
    def short(self) -> str:
        return self.sha[:7]

    @property
    def conventional_type(self) -> str | None:
        m = CONVENTIONAL.match(self.subject)
        return m.group("type").lower() if m else None

    @property
    def is_breaking(self) -> bool:
        if "BREAKING CHANGE" in self.body.upper():
            return True
        m = CONVENTIONAL.match(self.subject)
        return bool(m and m.group("breaking"))


@dataclass
class Analysis:
    last_tag: str | None
    last_version: Version | None
    range_spec: str
    commits: list[Commit] = field(default_factory=list)
    suggested_bump: str = "patch"
    suggested_version: Version = field(default_factory=lambda: Version(0, 1, 0))

    def changelog_markdown(self, new_tag: str) -> str:
        lines = [
            f"## {new_tag}",
            "",
            f"_Changes since {self.last_tag or 'repository start'} "
            f"({len(self.commits)} commit{'s' if len(self.commits) != 1 else ''})._",
            "",
        ]
        buckets: dict[str, list[Commit]] = {
            "Features": [],
            "Fixes": [],
            "Performance": [],
            "Documentation": [],
            "Refactoring": [],
            "Build / CI": [],
            "Chores": [],
            "Other": [],
        }
        type_map = {
            "feat": "Features",
            "fix": "Fixes",
            "perf": "Performance",
            "docs": "Documentation",
            "refactor": "Refactoring",
            "build": "Build / CI",
            "ci": "Build / CI",
            "chore": "Chores",
            "test": "Chores",
            "style": "Chores",
            "revert": "Other",
        }
        breaking: list[Commit] = []
        for c in self.commits:
            if c.is_breaking:
                breaking.append(c)
            bucket = type_map.get(c.conventional_type or "", "Other")
            buckets[bucket].append(c)

        if breaking:
            lines.append("### Breaking changes")
            lines.append("")
            for c in breaking:
                lines.append(f"- {c.subject} (`{c.short}`)")
            lines.append("")

        for title, items in buckets.items():
            if not items:
                continue
            lines.append(f"### {title}")
            lines.append("")
            for c in items:
                lines.append(f"- {c.subject} (`{c.short}`)")
            lines.append("")

        if not self.commits:
            lines.append("_No commits in range._")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("### Docker")
        lines.append("")
        lines.append("```bash")
        lines.append(f"docker pull ghcr.io/sawpsyder/rcon_server_manager:{new_tag.lstrip('v')}")
        ver = Version.parse(new_tag)
        if ver and not ver.pre:
            lines.append("docker pull ghcr.io/sawpsyder/rcon_server_manager:latest")
        lines.append("```")
        lines.append("")
        return "\n".join(lines)


def ensure_repo() -> None:
    if not (REPO_ROOT / ".git").exists():
        raise CmdError(f"Not a git repo: {REPO_ROOT}")


def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD")


def working_tree_clean() -> bool:
    return git("status", "--porcelain") == ""


def list_version_tags() -> list[str]:
    out = git("tag", "--list", "v*", "--sort=-v:refname")
    if not out:
        return []
    tags = []
    for line in out.splitlines():
        t = line.strip()
        if Version.parse(t):
            tags.append(t)
    return tags


def last_release_tag() -> str | None:
    tags = list_version_tags()
    return tags[0] if tags else None


def commits_since_range(rev_range: str) -> list[Commit]:
    """Commits in a git rev range (e.g. v0.1.0..origin/develop)."""
    # %x1f unit sep, %x1e record sep
    fmt = "%H%x1f%s%x1f%b%x1e"
    out = git("log", rev_range, f"--format={fmt}", check=False)
    if not out:
        return []
    commits: list[Commit] = []
    for record in out.split("\x1e"):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split("\x1f", 2)
        if len(parts) < 2:
            continue
        sha, subject = parts[0].strip(), parts[1].strip()
        body = parts[2].strip() if len(parts) > 2 else ""
        if sha and subject:
            commits.append(Commit(sha=sha, subject=subject, body=body))
    return commits


def suggest_bump(commits: list[Commit]) -> str:
    if any(c.is_breaking for c in commits):
        return "major"
    types = {c.conventional_type for c in commits if c.conventional_type}
    if "feat" in types:
        return "minor"
    return "patch"


def analyse(
    bump: str | None = None,
    base: str = "develop",
    *,
    update_working_copy: bool = True,
) -> Analysis:
    ensure_repo()
    run(["git", "fetch", "origin", "--tags", "--prune"], check=False)

    last = last_release_tag()
    last_ver = Version.parse(last) if last else None

    # Resolve tip of develop for the commit range (prefer remote after fetch)
    remote_tip = f"origin/{base}"
    has_remote = git("rev-parse", "--verify", remote_tip, check=False)
    local_tip = git("rev-parse", "--verify", base, check=False)
    if update_working_copy and local_tip:
        git("checkout", base, check=False)
        run(["git", "pull", "--ff-only", "origin", base], check=False)
        tip = "HEAD"
    elif has_remote:
        tip = remote_tip
    elif local_tip:
        tip = base
    else:
        tip = "HEAD"

    if last:
        rev_range = f"{last}..{tip}"
    else:
        rev_range = tip

    commits = commits_since_range(rev_range)
    auto = suggest_bump(commits)
    kind = bump or auto
    if last_ver:
        suggested = last_ver.bump(kind)
    else:
        # First release defaults to v0.1.0
        suggested = Version(1, 0, 0) if kind == "major" else Version(0, 1, 0)

    return Analysis(
        last_tag=last,
        last_version=last_ver,
        range_spec=rev_range if last else f"{tip} (all)",
        commits=commits,
        suggested_bump=auto,
        suggested_version=suggested,
    )


def print_analysis(a: Analysis, chosen: Version | None = None) -> None:
    ver = chosen or a.suggested_version
    print()
    print("=" * 60)
    print("Release analysis")
    print("=" * 60)
    print(f"Last release tag : {a.last_tag or '(none)'}")
    print(f"Range            : {a.range_spec}")
    print(f"Commits          : {len(a.commits)}")
    print(f"Auto bump hint   : {a.suggested_bump}")
    print(f"Next version     : {ver.tag()}")
    print()
    if a.commits:
        print("Commits:")
        for c in a.commits:
            kind = c.conventional_type or "other"
            br = "!" if c.is_breaking else ""
            print(f"  [{kind}{br}] {c.short}  {c.subject}")
    else:
        print("No new commits since last release.")
    print()
    print("--- Changelog preview ---")
    print(a.changelog_markdown(ver.tag()))


def resolve_version(a: Analysis, bump: str | None, version: str | None) -> Version:
    if version:
        raw = version if version.startswith("v") else f"v{version}"
        parsed = Version.parse(raw)
        if not parsed:
            raise CmdError(f"Invalid version: {version}")
        return parsed
    if bump:
        if a.last_version:
            return a.last_version.bump(bump)
        return Version(1, 0, 0) if bump == "major" else Version(0, 1, 0)
    return a.suggested_version


def cmd_analyse(args: argparse.Namespace) -> int:
    a = analyse(bump=args.bump, base=args.develop_branch, update_working_copy=True)
    chosen = resolve_version(a, args.bump, args.version)
    print_analysis(a, chosen)
    return 0


def confirm(prompt: str, yes: bool) -> bool:
    if yes:
        return True
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def feat_subjects_from_analysis(a: Analysis) -> list[str]:
    return [
        c.subject
        for c in a.commits
        if c.conventional_type == "feat"
    ]


def apply_readme_feature_update(
    a: Analysis,
    *,
    dry_run: bool = False,
    skip: bool = False,
) -> list[str]:
    """Append feat bullets into README Features markers. Returns added lines."""
    if skip:
        print("README feature update skipped (--skip-readme).")
        return []
    subjects = feat_subjects_from_analysis(a)
    if not subjects:
        print("README feature update: no feat commits in range.")
        return []
    try:
        if dry_run:
            print(preview_diff(README_PATH, subjects))
            changed, added = update_readme_features(
                README_PATH, subjects, dry_run=True
            )
            return added if changed else []
        changed, added = update_readme_features(README_PATH, subjects, dry_run=False)
    except ReadmeError as exc:
        raise CmdError(f"README feature update failed: {exc}") from exc
    if not changed:
        print("README feature update: nothing new to append.")
        return []
    print(f"README feature update: appended {len(added)} bullet(s):")
    for line in added:
        print(f"  {line}")
    return added


def cmd_update_readme(args: argparse.Namespace) -> int:
    """Standalone: preview or apply feature bullets from commits since last tag."""
    ensure_repo()
    a = analyse(
        bump=None,
        base=args.develop_branch,
        update_working_copy=not args.dry_run,
    )
    print_analysis(a)
    if not working_tree_clean() and not args.dry_run:
        raise CmdError("Working tree is not clean. Commit or stash first.")
    added = apply_readme_feature_update(a, dry_run=args.dry_run)
    if args.dry_run or not added:
        return 0
    run(["git", "add", "README.md"])
    run(
        [
            "git",
            "commit",
            "-m",
            "docs: append release features to README",
        ]
    )
    print("Committed README feature list update.")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    ensure_repo()
    dry = args.dry_run
    develop = args.develop_branch
    master = args.master_branch

    if not dry and not working_tree_clean():
        raise CmdError("Working tree is not clean. Commit or stash first.")

    # Analysis without mutating the working tree on dry-run
    a = analyse(
        bump=args.bump,
        base=develop,
        update_working_copy=not dry,
    )
    new_ver = resolve_version(a, args.bump, args.version)
    new_tag = new_ver.tag()
    notes = a.changelog_markdown(new_tag)
    print_analysis(a, new_ver)

    if not a.commits and not args.allow_empty:
        raise CmdError("No commits since last release. Use --allow-empty to force.")

    existing = git("tag", "--list", new_tag)
    if existing and not dry:
        raise CmdError(f"Tag already exists: {new_tag}")

    if not confirm(
        f"Proceed with release {new_tag} (merge {develop} → {master})?",
        args.yes or dry,
    ):
        print("Aborted.")
        return 1

    if dry:
        print()
        print("Planned actions:")
        print(f"  1. checkout + pull origin/{develop}")
        print(
            "  2. append new feat bullets into README "
            f"({FEATURES_HINT if not args.skip_readme else 'skipped'})"
        )
        print(f"  3. merge {develop} → {master} (create master if missing)")
        print(f"  4. annotated tag {new_tag} on {master}")
        print(f"  5. push origin {master} + {new_tag}")
        print(f"  6. gh release create {new_tag} --target {master}")
        print(f"  7. checkout {develop}")
        print()
        apply_readme_feature_update(a, dry_run=True, skip=args.skip_readme)
        print()
        print("[dry-run] No remote or GitHub changes made.")
        return 0

    # --- live path ---
    run(["git", "checkout", develop])
    run(["git", "pull", "--ff-only", "origin", develop], check=False)

    # Re-analyse on the tip we actually ship
    a = analyse(bump=args.bump, base=develop, update_working_copy=False)
    new_ver = resolve_version(a, args.bump, args.version)
    new_tag = new_ver.tag()
    notes = a.changelog_markdown(new_tag)

    added = apply_readme_feature_update(a, dry_run=False, skip=args.skip_readme)
    if added:
        run(["git", "add", "README.md"])
        run(
            [
                "git",
                "commit",
                "-m",
                f"docs: append README features for {new_tag}",
            ]
        )
        run(["git", "push", "origin", develop])

    has_master = bool(git("show-ref", "--verify", f"refs/heads/{master}", check=False))
    remote_master = bool(
        git("show-ref", "--verify", f"refs/remotes/origin/{master}", check=False)
    )

    if not has_master and not remote_master:
        # First release: create master from develop
        run(["git", "branch", master, develop])
        run(["git", "checkout", master])
    elif remote_master and not has_master:
        run(["git", "checkout", "-B", master, f"origin/{master}"])
        run(["git", "pull", "--ff-only", "origin", master], check=False)
    else:
        run(["git", "checkout", master])
        if remote_master:
            run(["git", "pull", "--ff-only", "origin", master], check=False)

    merge_msg = f"Merge branch '{develop}' for release {new_tag}"
    r = run(
        ["git", "merge", "--no-ff", develop, "-m", merge_msg],
        check=False,
    )
    if r.returncode != 0:
        raise CmdError(
            f"Merge {develop} → {master} failed. Resolve conflicts, then re-run.\n"
            f"{r.stderr or r.stdout}"
        )

    run(["git", "tag", "-a", new_tag, "-m", f"Release {new_tag}"])
    run(["git", "push", "origin", master])
    run(["git", "push", "origin", new_tag])

    notes_file = REPO_ROOT / ".release-notes.tmp.md"
    notes_file.write_text(notes, encoding="utf-8")
    try:
        gh_args = [
            "release",
            "create",
            new_tag,
            "--title",
            f"RCON Server Manager {new_tag}",
            "--notes-file",
            str(notes_file),
            "--target",
            master,
        ]
        if new_ver.pre:
            gh_args.append("--prerelease")
        run(["gh", *gh_args])
    finally:
        notes_file.unlink(missing_ok=True)

    run(["git", "checkout", develop])

    print()
    print("=" * 60)
    print(f"Release {new_tag} complete.")
    print("  master + tag pushed; GitHub Release created.")
    if added:
        print(f"  README Features: +{len(added)} bullet(s) (markers only).")
    print(f"  Docker image workflow should run for tag {new_tag}.")
    print(f"  https://github.com/SawPsyder/rcon_server_manager/releases/tag/{new_tag}")
    print("=" * 60)
    return 0


FEATURES_HINT = "<!-- FEATURES:BEGIN/END --> only"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Analyse commits and cut a release (merge → tag → GitHub Release).",
    )
    p.add_argument(
        "--develop-branch",
        default="develop",
        help="Integration branch (default: develop)",
    )
    p.add_argument(
        "--master-branch",
        default="master",
        help="Release branch (default: master)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("analyse", help="Show commits since last release and suggested version")
    a.add_argument(
        "--bump",
        choices=("major", "minor", "patch"),
        help="Override automatic bump suggestion",
    )
    a.add_argument("--version", help="Explicit next version (e.g. 0.2.0 or v0.2.0)")
    a.set_defaults(func=cmd_analyse)

    u = sub.add_parser(
        "update-readme",
        help="Append feat bullets into README Features markers (structure-safe)",
    )
    u.add_argument(
        "--dry-run",
        action="store_true",
        help="Show bullets that would be added without writing README",
    )
    u.set_defaults(func=cmd_update_readme)

    r = sub.add_parser(
        "release",
        help="Merge develop→master, tag, push, create GitHub Release",
    )
    r.add_argument(
        "--bump",
        choices=("major", "minor", "patch"),
        help="Version bump relative to last tag (default: auto from commits)",
    )
    r.add_argument("--version", help="Explicit version (overrides --bump)")
    r.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Do not prompt for confirmation",
    )
    r.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without changing git remotes or GitHub",
    )
    r.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow release even when there are no new commits",
    )
    r.add_argument(
        "--skip-readme",
        action="store_true",
        help="Do not append feat bullets to README Features on this release",
    )
    r.set_defaults(func=cmd_release)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CmdError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
