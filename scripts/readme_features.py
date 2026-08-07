#!/usr/bin/env python3
"""Structure-preserving README feature list updates for releases.

Only the bullet list between ``<!-- FEATURES:BEGIN -->`` and
``<!-- FEATURES:END -->`` may change. Headings, supported-games sections,
Compose sample, env table, and any other prose are left untouched.

New bullets are **appended** from conventional ``feat:`` commits; existing
bullets are never reordered or rewritten.
"""

from __future__ import annotations

import re
from pathlib import Path

FEATURES_BEGIN = "<!-- FEATURES:BEGIN -->"
FEATURES_END = "<!-- FEATURES:END -->"

# Required top-level shape - refuse to write if these disappear
REQUIRED_HEADINGS = (
    "# RCON Server Manager",
    "## Features",
    "## Supported games",
    "## Quick start",
    "## Environment variables",
)

CONVENTIONAL = re.compile(
    r"^(?P<type>feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<subject>.+)$",
    re.IGNORECASE,
)

# feat commits that are tooling / infra, not product features
SKIP_SUBJECT_RE = re.compile(
    r"^(readme|docs?|ci|release|test|chore|bump|version)\b",
    re.IGNORECASE,
)


class ReadmeError(RuntimeError):
    pass


def _normalize_bullet_text(text: str) -> str:
    t = text.strip()
    if t.startswith("- "):
        t = t[2:].strip()
    elif t.startswith("* "):
        t = t[2:].strip()
    t = t.rstrip(".").strip()
    return re.sub(r"\s+", " ", t)


def _bullet_key(text: str) -> str:
    return _normalize_bullet_text(text).casefold()


def feat_subjects_to_bullets(subjects: list[str]) -> list[str]:
    """Turn conventional commit subjects into high-level feature bullets."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in subjects:
        subject = (raw or "").strip()
        if not subject:
            continue
        m = CONVENTIONAL.match(subject)
        if m:
            if m.group("type").lower() != "feat":
                continue
            text = m.group("subject").strip()
        else:
            # bare subject only if caller already filtered to feats
            text = subject
        if SKIP_SUBJECT_RE.match(text):
            continue
        text = _normalize_bullet_text(text)
        if not text or len(text) < 4:
            continue
        # Present as a short product-facing line
        text = text[0].upper() + text[1:] if text else text
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(f"- {text}")
    return out


def parse_feature_block(readme: str) -> tuple[str, list[str], str]:
    """Return (before_block_inclusive_begin, bullets, after_end)."""
    begin = readme.find(FEATURES_BEGIN)
    end = readme.find(FEATURES_END)
    if begin < 0 or end < 0 or end < begin:
        raise ReadmeError(
            f"README must contain {FEATURES_BEGIN} … {FEATURES_END} markers "
            "around the Features bullet list."
        )
    after_begin = begin + len(FEATURES_BEGIN)
    before = readme[:after_begin]
    middle = readme[after_begin:end]
    after = readme[end:]  # includes FEATURES:END

    bullets: list[str] = []
    for line in middle.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            bullets.append(f"- {_normalize_bullet_text(stripped)}")
        else:
            raise ReadmeError(
                "Features block may only contain bullet lines (`- …`). "
                f"Found non-bullet content: {stripped!r}"
            )
    return before, bullets, after


def merge_feature_bullets(existing: list[str], additions: list[str]) -> list[str]:
    """Append new bullets; never reorder or rewrite existing ones."""
    keys = {_bullet_key(b) for b in existing}
    merged = list(existing)
    for b in additions:
        key = _bullet_key(b)
        if not key or key in keys:
            continue
        # Skip if existing bullet already covers this as a substring (either way)
        if any(key in k or k in key for k in keys if len(k) >= 12 and len(key) >= 12):
            continue
        keys.add(key)
        merged.append(b if b.startswith("- ") else f"- {b}")
    return merged


def render_features_readme(before: str, bullets: list[str], after: str) -> str:
    # before ends right after BEGIN marker; after starts with END marker
    block = "\n" + "\n".join(bullets) + "\n"
    return before + block + after


def assert_structure_intact(readme: str) -> None:
    for heading in REQUIRED_HEADINGS:
        if heading not in readme:
            raise ReadmeError(f"README structure check failed: missing {heading!r}")
    if FEATURES_BEGIN not in readme or FEATURES_END not in readme:
        raise ReadmeError("README structure check failed: feature markers missing")
    # Markers must stay in Features section order
    i_feat = readme.find("## Features")
    i_begin = readme.find(FEATURES_BEGIN)
    i_end = readme.find(FEATURES_END)
    i_games = readme.find("## Supported games")
    if not (i_feat < i_begin < i_end < i_games):
        raise ReadmeError(
            "README structure check failed: feature markers must sit under "
            "## Features and before ## Supported games"
        )


def update_readme_features(
    readme_path: Path,
    feat_subjects: list[str],
    *,
    dry_run: bool = False,
) -> tuple[bool, list[str]]:
    """
    Append feature bullets derived from *feat_subjects*.

    Returns ``(changed, added_bullets)``.
    """
    original = readme_path.read_text(encoding="utf-8")
    assert_structure_intact(original)

    before, existing, after = parse_feature_block(original)
    additions = feat_subjects_to_bullets(feat_subjects)
    merged = merge_feature_bullets(existing, additions)
    added = merged[len(existing) :]

    if not added:
        return False, []

    updated = render_features_readme(before, merged, after)
    assert_structure_intact(updated)

    # Only the features block content may differ
    if (
        updated[: updated.find(FEATURES_BEGIN)] != original[: original.find(FEATURES_BEGIN)]
        or updated[updated.find(FEATURES_END) :] != original[original.find(FEATURES_END) :]
    ):
        raise ReadmeError(
            "Internal error: update would change README content outside the "
            "FEATURES markers - aborting."
        )

    if dry_run:
        return True, added

    readme_path.write_text(updated, encoding="utf-8", newline="\n")
    return True, added


def preview_diff(readme_path: Path, feat_subjects: list[str]) -> str:
    original = readme_path.read_text(encoding="utf-8")
    before, existing, after = parse_feature_block(original)
    additions = feat_subjects_to_bullets(feat_subjects)
    merged = merge_feature_bullets(existing, additions)
    added = merged[len(existing) :]
    if not added:
        return "No new feature bullets to add."
    lines = ["Would append to Features:", ""]
    lines.extend(added)
    return "\n".join(lines)
