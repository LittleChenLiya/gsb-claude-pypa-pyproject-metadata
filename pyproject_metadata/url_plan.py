# SPDX-License-Identifier: MIT

"""
Explicit project URL parsing plan.

This module implements an *explicit* plan for parsing ``project.urls``
entries. It is opt-in: :class:`pyproject_metadata.StandardMetadata` keeps the
raw ``urls`` mapping untouched, and callers that want validated, categorized
URLs build a :class:`URLPlan` via :func:`build_url_plan` (or
:meth:`pyproject_metadata.StandardMetadata.url_plan`).

The plan follows these rules:

- Labels are classified into well-known categories via
  :data:`URL_CATEGORY_LABELS` (case-insensitive, with multilingual aliases
  such as "主页" for the homepage category). Unknown labels are kept as-is
  with no category.
- The original label and the original URL are always preserved for output;
  nothing is rewritten, and multiple addresses in the same category are
  never silently overwritten.
- Duplicate canonical labels (same category) pointing at *different*
  addresses are reported as source-conflict diagnostics, collected together
  when ``all_errors`` is enabled. The same address from several labels is
  fine and both sources are kept.
- URLs must be absolute URLs with a supported scheme
  (:data:`SUPPORTED_URL_SCHEMES`); anything else (relative URLs, ``mailto:``
  and other unsupported schemes, malformed URLs) is rejected.
- Query strings and fragments are part of the address and are never
  stripped.
- Validation is purely offline: URLs are only parsed with
  :func:`urllib.parse.urlsplit`, never fetched.
- If any diagnostic is produced, the build fails and no (partial) plan is
  returned.
"""

from __future__ import annotations

import dataclasses
import typing
import urllib.parse

from .errors import ErrorCollector

if typing.TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = [
    "SUPPORTED_URL_SCHEMES",
    "URL_CATEGORY_LABELS",
    "URLPlan",
    "URLPlanEntry",
    "build_url_plan",
    "canonical_url_label",
]


def __dir__() -> list[str]:
    return __all__


#: URL schemes supported by the URL plan. Everything else (``mailto:``,
#: ``ftp:``, ``file:``, ...) is explicitly rejected.
SUPPORTED_URL_SCHEMES = frozenset({"http", "https"})

#: Well-known URL categories and their accepted labels. Labels are matched
#: after normalization (see :func:`canonical_url_label`). The key of each
#: category is its canonical display name.
URL_CATEGORY_LABELS: dict[str, tuple[str, ...]] = {
    "Homepage": ("homepage", "home", "home page", "主页", "首页", "官网", "官方网站"),
    "Documentation": ("documentation", "docs", "doc", "文档"),
    "Source": (
        "source",
        "source code",
        "repository",
        "repo",
        "code",
        "源代码",
        "源码",
        "仓库",
    ),
    "Download": ("download", "downloads", "下载"),
    "Changelog": (
        "changelog",
        "change log",
        "changes",
        "release notes",
        "news",
        "更新日志",
    ),
    "Bug Tracker": (
        "bug tracker",
        "issue tracker",
        "issues",
        "bug reports",
        "tracker",
        "问题追踪",
    ),
    "Funding": ("funding", "sponsor", "donate", "donation", "赞助"),
    "Mailing List": ("mailing list", "讨论组"),
    "CI": ("ci", "continuous integration"),
    "Code of Conduct": ("code of conduct", "行为准则"),
}

_NORMALIZED_LABELS: dict[str, str] = {
    alias: category
    for category, aliases in URL_CATEGORY_LABELS.items()
    for alias in aliases
}


def _normalize_label(label: str) -> str:
    """
    Normalize a URL label for comparison: lowercase, and collapse runs of
    whitespace, hyphens, and underscores into single spaces.
    """
    return " ".join(label.replace("-", " ").replace("_", " ").casefold().split())


def canonical_url_label(label: str) -> str | None:
    """
    Return the well-known category for a ``project.urls`` label, or None if
    the label is unknown. Unknown labels are kept as-is by the plan.
    """
    return _NORMALIZED_LABELS.get(_normalize_label(label))


@dataclasses.dataclass(frozen=True)
class URLPlanEntry:
    """
    A single planned URL. The original label and URL are preserved verbatim
    for output; ``category`` is the well-known category the label maps to, or
    None for unknown labels.
    """

    label: str
    url: str
    category: str | None


@dataclasses.dataclass(frozen=True)
class URLPlan:
    """
    The result of explicitly planning ``project.urls``. Entries keep their
    original order, labels, and URLs; nothing is dropped or overwritten.
    """

    entries: tuple[URLPlanEntry, ...]

    def __iter__(self) -> Iterator[URLPlanEntry]:
        """
        Iterate over the planned entries in their original order.
        """
        return iter(self.entries)

    def __len__(self) -> int:
        """
        Return the number of planned entries.
        """
        return len(self.entries)

    def by_category(self) -> dict[str, list[URLPlanEntry]]:
        """
        Group entries by well-known category. Entries with unknown labels are
        not included; use ``entries`` for the full list.
        """
        grouped: dict[str, list[URLPlanEntry]] = {}
        for entry in self.entries:
            if entry.category is not None:
                grouped.setdefault(entry.category, []).append(entry)
        return grouped

    def as_dict(self) -> dict[str, str]:
        """
        Return the original ``{label: url}`` mapping, suitable for writing
        metadata out again.
        """
        return {entry.label: entry.url for entry in self.entries}


def _validate_url(label: str, url: str, *, errors: ErrorCollector) -> None:
    """
    Validate a single URL offline. Only absolute URLs with a supported scheme
    are accepted; the URL string itself is never modified, so queries and
    fragments are preserved.
    """
    if any(c in url for c in " \t\r\n"):
        msg = "URL {url!r} for {key} contains whitespace, which is not a valid absolute URL"
        errors.config_error(msg, key=f"project.urls.{label}", url=url)
        return
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        msg = "URL {url!r} for {key} is not a valid absolute URL"
        errors.config_error(msg, key=f"project.urls.{label}", url=url)
        return
    if not parsed.scheme:
        msg = "URL {url!r} for {key} is not an absolute URL (missing scheme)"
        errors.config_error(msg, key=f"project.urls.{label}", url=url)
        return
    if parsed.scheme.lower() not in SUPPORTED_URL_SCHEMES:
        supported = ", ".join(sorted(SUPPORTED_URL_SCHEMES))
        msg = "URL {url!r} for {key} uses unsupported scheme {scheme!r} (supported: {supported})"
        errors.config_error(
            msg,
            key=f"project.urls.{label}",
            url=url,
            scheme=parsed.scheme,
            supported=supported,
        )
        return
    if not parsed.netloc:
        msg = "URL {url!r} for {key} is not a valid absolute URL (missing host)"
        errors.config_error(msg, key=f"project.urls.{label}", url=url)


def build_url_plan(
    urls: Mapping[str, str],
    *,
    all_errors: bool = False,
) -> URLPlan:
    """
    Build an explicit URL plan for a ``project.urls`` mapping.

    Every entry is validated offline and classified via
    :func:`canonical_url_label`. If several labels map to the same well-known
    category with different addresses, a source-conflict diagnostic is
    reported (collected with ``all_errors=True``); identical addresses keep
    all of their sources. On any failure a
    :class:`~pyproject_metadata.errors.ConfigurationError` (or an
    ``ExceptionGroup`` of them) is raised and no partial plan is returned.
    """
    errors = ErrorCollector(collect_errors=all_errors)

    entries = []
    for label, url in urls.items():
        if not isinstance(url, str):
            # Type errors are reported by the project table validation; skip
            # here so the plan does not fail with a confusing message.
            continue
        _validate_url(label, url, errors=errors)
        entries.append(
            URLPlanEntry(label=label, url=url, category=canonical_url_label(label))
        )

    # Centralized diagnostics for duplicate canonical labels: same category,
    # different addresses is a source conflict; identical addresses keep all
    # of their sources.
    by_category: dict[str, list[URLPlanEntry]] = {}
    for entry in entries:
        if entry.category is not None:
            by_category.setdefault(entry.category, []).append(entry)
    for category, group in by_category.items():
        addresses = {entry.url for entry in group}
        if len(addresses) > 1:
            sources = ", ".join(f"{entry.label!r} -> {entry.url!r}" for entry in group)
            msg = (
                "Conflicting sources for URL category {category!r} in {key}: {sources}"
            )
            errors.config_error(
                msg, key="project.urls", category=category, sources=sources
            )

    errors.finalize("Failed to build the project URL plan")
    return URLPlan(entries=tuple(entries))
