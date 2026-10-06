# SPDX-License-Identifier: MIT

"""
Explicit parsing plan for ``project.urls``.

This module implements the project URL plan:

- Labels are classified into well-known *categories* using
  :data:`URL_LABEL_CATEGORIES`. Lookup is case-insensitive, treats
  ``-``/``_`` as spaces, and collapses whitespace, so ``Homepage``,
  ``home_page`` and ``主页`` all resolve to the ``homepage`` category.
  Unknown labels are kept as-is and are simply not categorized.
- The original labels and the original URLs are always preserved for
  output (``Project-URL`` entries); entries are never normalized away and
  multiple addresses of the same category are not overwritten by default.
- If several labels map to the same category but give *different*
  addresses, one diagnostic per category is reported (all such
  diagnostics are collected together when ``all_errors`` is enabled).
  Labels that map to the same category *and* the same address are all
  kept, preserving both sources.
- Every URL must be a valid *absolute* URL with a supported scheme
  (:data:`SUPPORTED_URL_SCHEMES`). Unsupported schemes such as
  ``mailto:`` are explicitly rejected. Fragments and query strings are
  significant and are never stripped from the stored URL.
- Validation is purely offline: URLs are parsed and checked, never
  fetched.
"""

from __future__ import annotations

import re
import typing
import urllib.parse

if typing.TYPE_CHECKING:
    from collections.abc import Mapping

    from .errors import ErrorCollector

__all__ = [
    "SUPPORTED_URL_SCHEMES",
    "URL_LABEL_CATEGORIES",
    "label_category",
    "normalize_label",
    "validate_project_urls",
]


def __dir__() -> list[str]:
    return __all__


SUPPORTED_URL_SCHEMES = frozenset({"http", "https"})
"""
URL schemes accepted in ``project.urls``. Everything else (``mailto:``,
``ftp:``, ``file:``, ...) is explicitly rejected.
"""

URL_LABEL_CATEGORIES = {
    # Homepage
    "homepage": "homepage",
    "home": "homepage",
    "home page": "homepage",
    "主页": "homepage",
    "首页": "homepage",
    # Source code
    "source": "source",
    "source code": "source",
    "repository": "source",
    "repo": "source",
    "源码": "source",
    "仓库": "source",
    # Documentation
    "documentation": "documentation",
    "docs": "documentation",
    "文档": "documentation",
    # Download
    "download": "download",
    "下载": "download",
    # Changelog
    "changelog": "changelog",
    "changes": "changelog",
    "release notes": "changelog",
    "更新日志": "changelog",
    # Issue tracker
    "issues": "issues",
    "issue tracker": "issues",
    "bug tracker": "issues",
    "tracker": "issues",
    "问题追踪": "issues",
    # Funding
    "funding": "funding",
    "sponsor": "funding",
    "赞助": "funding",
}
"""
Mapping of normalized ``project.urls`` labels to well-known categories.

Labels are normalized with :func:`normalize_label` before lookup. Labels
that are not present here are unknown and kept as-is.
"""

_RE_SEPARATOR = re.compile(r"[-_\s]+")
_RE_FORBIDDEN_CHARS = re.compile(r"[\x00-\x20]")


def normalize_label(label: str) -> str:
    """
    Normalize a ``project.urls`` label for category lookup.

    The label is stripped, ``-``/``_``/whitespace runs are collapsed to a
    single space, and the result is case-folded.
    """
    return _RE_SEPARATOR.sub(" ", label.strip()).casefold()


def label_category(label: str) -> str | None:
    """
    Return the well-known category for a ``project.urls`` label, or None
    if the label is unknown.
    """
    return URL_LABEL_CATEGORIES.get(normalize_label(label))


def _validate_url(label: str, url: str, *, key: str, errors: ErrorCollector) -> None:
    """
    Validate a single URL offline. The URL itself is never modified.
    """
    if _RE_FORBIDDEN_CHARS.search(url):
        msg = "{key} URL for {label!r} is not a valid absolute URL: it contains whitespace or control characters"
        errors.config_error(msg, key=key, label=label, got=url)
        return
    try:
        parsed = urllib.parse.urlsplit(url)
        # Accessing the port raises ValueError for invalid ports, and
        # hostname raises ValueError for malformed network locations.
        parsed.port  # noqa: B018
        hostname = parsed.hostname
    except ValueError:
        msg = "{key} URL for {label!r} is not a valid absolute URL"
        errors.config_error(msg, key=key, label=label, got=url)
        return
    if not parsed.scheme:
        msg = "{key} URL for {label!r} is not a valid absolute URL: missing scheme"
        errors.config_error(msg, key=key, label=label, got=url)
        return
    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_URL_SCHEMES:
        supported = ", ".join(sorted(SUPPORTED_URL_SCHEMES))
        msg = "{key} URL for {label!r} uses unsupported scheme {scheme!r}; supported schemes are: {supported}"
        errors.config_error(
            msg, key=key, label=label, scheme=scheme, supported=supported, got=url
        )
        return
    if not hostname:
        msg = "{key} URL for {label!r} is not a valid absolute URL: missing host"
        errors.config_error(msg, key=key, label=label, got=url)


def validate_project_urls(
    urls: Mapping[str, str], *, errors: ErrorCollector, key: str = "project.urls"
) -> None:
    """
    Validate a ``project.urls`` mapping following the module plan.

    Each URL is checked offline for being a valid absolute URL with a
    supported scheme. Then labels are classified into well-known
    categories and each category holding more than one distinct address
    is reported as a conflict. The mapping itself is never mutated:
    original labels and URLs are preserved for output.
    """
    categories: dict[str, dict[str, str]] = {}
    for label, url in urls.items():
        if not isinstance(label, str) or not isinstance(url, str):
            # Type errors are reported by the project table validation.
            continue
        _validate_url(label, url, key=key, errors=errors)
        category = label_category(label)
        if category is not None:
            categories.setdefault(category, {})[label] = url
    for category, entries in categories.items():
        addresses = sorted(set(entries.values()))
        if len(addresses) < 2:
            # Same address (or a single source): keep all labels.
            continue
        labels = ", ".join(sorted(repr(label) for label in entries))
        msg = "{key} labels {labels} map to the same well-known category {category!r} but give conflicting addresses: {addresses}"
        errors.config_error(
            msg,
            key=key,
            labels=labels,
            category=category,
            addresses=", ".join(addresses),
        )
