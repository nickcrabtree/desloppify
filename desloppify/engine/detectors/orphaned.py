"""Orphaned file detection: files with zero importers that aren't entry points."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from desloppify.base.discovery.file_paths import count_lines, rel
from desloppify.base.discovery.source import find_source_files

_DUNDER_ALL_RE = re.compile(r"^__all__\s*[:=]", re.MULTILINE)

# ---------------------------------------------------------------------------
# Next.js App Router convention files
# ---------------------------------------------------------------------------

# Files that are entry points when inside an app/ directory
_NEXTJS_APP_DIR_CONVENTIONS: set[str] = {
    "page",
    "layout",
    "loading",
    "error",
    "not-found",
    "global-error",
    "route",
    "template",
    "default",
    "opengraph-image",
    "twitter-image",
    "sitemap",
    "robots",
    "icon",
    "apple-icon",
}

# Files that are entry points at the project root (or src/)
_NEXTJS_ROOT_CONVENTIONS: set[str] = {
    "middleware",
    "instrumentation",
    "instrumentation-client",
}

_NEXTJS_EXTENSIONS: set[str] = {".ts", ".tsx", ".js", ".jsx"}


def _detect_nextjs_project(path: Path) -> bool:
    """Return True if the scan root looks like a Next.js project."""
    for name in ("next.config.js", "next.config.mjs", "next.config.ts"):
        if (path / name).exists():
            return True
    return False


def _is_nextjs_convention_entry(rel_path: str) -> bool:
    """Return True if *rel_path* is a Next.js App Router convention file.

    Checks:
    - Files with convention names inside any ``app/`` directory segment
    - Root-level convention files (middleware, instrumentation)
    """
    p = Path(rel_path)
    ext = p.suffix
    if ext not in _NEXTJS_EXTENSIONS:
        return False

    stem = p.stem
    parts = p.parts

    # Root-level conventions: middleware.ts, instrumentation.ts, etc.
    # These can live at the project root or inside src/
    if stem in _NEXTJS_ROOT_CONVENTIONS and len(parts) <= 2:
        return True

    # App directory conventions: any file inside an app/ segment
    if stem in _NEXTJS_APP_DIR_CONVENTIONS:
        if "app" in parts:
            return True

    return False


# ---------------------------------------------------------------------------
# HTML / template asset references (script-tag and worker loaded files)
# ---------------------------------------------------------------------------

_WEB_SCRIPT_EXTENSIONS: set[str] = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}

# Flask/Jinja: url_for('static', filename='js/foo.js')
_JINJA_STATIC_RE = re.compile(
    r"""url_for\(\s*['"]static['"]\s*,\s*filename\s*=\s*['"]([^'"]+)['"]"""
)
# <script ... src="/static/js/foo.js"> — literal src (Jinja exprs handled above)
_SCRIPT_SRC_RE = re.compile(
    r"""<script\b[^>]*?\bsrc\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE
)
# new Worker('/static/js/foo.worker.js')
_WORKER_RE = re.compile(r"""new\s+Worker\(\s*['"]([^'"]+)['"]""")


def _is_web_language(extensions: list[str]) -> bool:
    """Return True if *extensions* covers a browser-script language."""
    return any(ext in _WEB_SCRIPT_EXTENSIONS for ext in extensions)


def find_html_loaded_assets(path: Path, extensions: list[str]) -> set[str]:
    """Return script/worker asset references the module graph cannot see.

    Browser apps load scripts via ``<script src=...>`` tags — frequently through
    a server-side helper such as Flask/Jinja ``url_for('static', filename=...)``
    — and via ``new Worker(...)``. Those files have no importer in the module
    graph yet are plainly live, so the orphaned detector treats anything matched
    here as dynamically imported. Genuinely unreferenced files are unaffected.

    Only runs for web languages; returns an empty set otherwise.
    """
    if not _is_web_language(extensions):
        return set()

    targets: set[str] = set()

    # HTML templates: <script src>, url_for static assets, and worker refs.
    for html_path in find_source_files(path, [".html"]):
        try:
            text = Path(html_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _JINJA_STATIC_RE.finditer(text):
            targets.add(match.group(1))
        for match in _SCRIPT_SRC_RE.finditer(text):
            src = match.group(1)
            if "{{" not in src:  # skip unresolved Jinja expressions
                targets.add(src)
        for match in _WORKER_RE.finditer(text):
            targets.add(match.group(1))

    # Source files: a Web Worker is constructed with a sibling script URL.
    for src_path in find_source_files(path, list(extensions)):
        try:
            text = Path(src_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _WORKER_RE.finditer(text):
            targets.add(match.group(1))

    return targets


@dataclass
class OrphanedDetectionOptions:
    """Optional behavior flags for orphaned-file detection."""

    extra_entry_patterns: list[str] | None = None
    extra_barrel_names: set[str] | None = None
    dynamic_import_finder: Callable[[Path, list[str]], set[str]] | None = None
    alias_resolver: Callable[[str], str] | None = None
    detect_frameworks: bool = True


def _has_dunder_all(filepath: str) -> bool:
    """Return True if the file defines ``__all__``, signaling a public API surface."""
    try:
        text = Path(filepath).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _DUNDER_ALL_RE.search(text) is not None


def _is_dynamically_imported(
    filepath: str,
    dynamic_targets: set[str],
    alias_resolver: Callable[[str], str] | None = None,
) -> bool:
    """Check if a file is referenced by any dynamic/side-effect import."""
    r = rel(filepath)
    stem = Path(filepath).stem
    name_no_ext = str(Path(r).with_suffix(""))

    for target in dynamic_targets:
        resolved = alias_resolver(target) if alias_resolver else target
        resolved = resolved.lstrip("./")
        if resolved == name_no_ext or resolved == r:
            return True
        if name_no_ext.endswith("/" + resolved) or name_no_ext.endswith(resolved):
            return True
        if resolved.endswith("/" + stem) or resolved == stem:
            return True
        if resolved.endswith("/" + Path(filepath).name):
            return True

    return False


def detect_orphaned_files(
    path: Path,
    graph: dict,
    extensions: list[str],
    options: OrphanedDetectionOptions | None = None,
) -> tuple[list[dict], int]:
    """Find files with zero importers that aren't known entry points."""
    resolved_options = options or OrphanedDetectionOptions()
    all_entry_patterns = resolved_options.extra_entry_patterns or []
    all_barrel_names = resolved_options.extra_barrel_names or set()
    dynamic_import_finder = resolved_options.dynamic_import_finder
    alias_resolver = resolved_options.alias_resolver

    # Framework convention detection
    is_nextjs = (
        resolved_options.detect_frameworks and _detect_nextjs_project(path)
    )

    dynamic_targets = (
        dynamic_import_finder(path, extensions) if dynamic_import_finder else set()
    )

    total_files = len(graph)
    entries = []
    for filepath, entry in graph.items():
        if entry["importer_count"] > 0:
            continue

        r = rel(filepath)

        if any(p in r for p in all_entry_patterns):
            continue

        basename = Path(filepath).name
        if basename in all_barrel_names:
            continue

        if is_nextjs and _is_nextjs_convention_entry(r):
            continue

        if dynamic_targets and _is_dynamically_imported(
            filepath, dynamic_targets, alias_resolver
        ):
            continue

        if _has_dunder_all(filepath):
            continue

        try:
            loc = count_lines(Path(filepath))
        except (OSError, UnicodeDecodeError):
            loc = 0

        if loc < 10:
            continue

        entries.append(
            {
                "file": filepath,
                "loc": loc,
                "import_count": entry.get("import_count", 0),
            }
        )

    return sorted(entries, key=lambda e: -e["loc"]), total_files
