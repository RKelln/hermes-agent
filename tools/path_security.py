"""Shared path validation helpers for tool implementations (skills, cron, credential files)."""

import re
from pathlib import Path
from typing import Optional


def validate_within_dir(path: Path, root: Path) -> Optional[str]:
    """Error message if *path* does not resolve inside *root* (symlinks and ``..`` followed)."""
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError) as exc:
        return f"Path escapes allowed directory: {exc}"
    return None


def has_traversal_component(path_str: str) -> bool:
    """Cheap pre-check for a literal ``..`` component before full resolution."""
    return ".." in Path(path_str).parts


# Control chars + Unicode line separators (NEL, LS, PS): newline-bearing paths are legal POSIX
# names, but they corrupt line-delimited protocols (MEDIA: tags) and forge log lines. Same
# class as _LOG_UNSAFE_CHARS in gateway.platforms.base.
_UNSAFE_PATH_CHARS = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]")


def has_unsafe_path_chars(path_str: str) -> bool:
    """True when *path_str* contains control characters or line separators."""
    return bool(_UNSAFE_PATH_CHARS.search(path_str))




def skill_scripts_relpath(resolved: Path, hermes_home: Path) -> Optional[Path]:
    """Return the HERMES_HOME-relative path when *resolved* lies inside the
    skill-scripts allowlist (``HERMES_HOME/skills/<cat>/<skill>/scripts/``),
    else ``None``.

    The allowlist is the ONLY escape from the scripts-dir boundary: cron
    scripts may also live inside a skill's own ``scripts/`` directory so a
    skill can ship cron-runnable code without wrapper boilerplate or copy
    drift. The shape is enforced on the RESOLVED path (``resolve()`` follows
    symlinks and normalizes ``..``), so a symlink inside a skill that points
    outside HERMES_HOME — or into any directory that is not a skill's
    ``scripts/`` dir — fails containment and is refused.

    The pattern is deliberately narrow: exactly ``skills/`` + one category
    component + one skill component + ``scripts/`` (deeper nesting under the
    skill's scripts dir is allowed). Nothing else under HERMES_HOME is
    reachable.
    """
    try:
        rel = resolved.relative_to(hermes_home.resolve())
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if len(parts) < 4 or parts[0] != "skills" or parts[3] != "scripts":
        return None
    return rel
