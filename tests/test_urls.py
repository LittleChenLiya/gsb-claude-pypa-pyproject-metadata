# SPDX-License-Identifier: MIT

from __future__ import annotations

import re

import pytest

import pyproject_metadata
import pyproject_metadata.errors
from pyproject_metadata import urls as url_plan


def make_metadata(urls: dict[str, str], **kwargs: object) -> object:
    return pyproject_metadata.StandardMetadata.from_pyproject(
        {"project": {"name": "example", "version": "1.2.3", "urls": urls}},
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "label",
    [
        "Homepage",
        "homepage",
        "HOMEPAGE",
        "home_page",
        "home-page",
        "Home  Page",
        " 主页 ",
        "首页",
    ],
)
def test_label_category_homepage(label: str) -> None:
    assert url_plan.label_category(label) == "homepage"


@pytest.mark.parametrize(
    ("label", "category"),
    [
        ("Source", "source"),
        ("repository", "source"),
        ("仓库", "source"),
        ("Documentation", "documentation"),
        ("文档", "documentation"),
        ("Download", "download"),
        ("Changelog", "changelog"),
        ("Bug Tracker", "issues"),
        ("问题追踪", "issues"),
        ("Funding", "funding"),
    ],
)
def test_label_category_mapping(label: str, category: str) -> None:
    assert url_plan.label_category(label) == category


def test_label_category_unknown() -> None:
    assert url_plan.label_category("my-custom-label") is None


def test_same_category_conflicting_addresses() -> None:
    # "Homepage" and "主页" map to the same category but give different
    # addresses: a source conflict must be reported.
    with pytest.raises(
        pyproject_metadata.ConfigurationError,
        match=re.escape(
            "map to the same well-known category 'homepage' but give conflicting addresses"
        ),
    ) as exc_info:
        make_metadata(
            {"Homepage": "https://example.com/a", "主页": "https://example.com/b"}
        )
    message = str(exc_info.value)
    assert "'Homepage'" in message
    assert "'主页'" in message
    assert "https://example.com/a" in message
    assert "https://example.com/b" in message


def test_same_category_conflicting_addresses_normalized_labels() -> None:
    with pytest.raises(
        pyproject_metadata.ConfigurationError,
        match=re.escape("category 'homepage' but give conflicting addresses"),
    ):
        make_metadata(
            {"Home Page": "https://example.com/a", "home_page": "https://example.com/b"}
        )


def test_same_category_same_address_keeps_both_sources() -> None:
    urls = {"Homepage": "https://example.com", "主页": "https://example.com"}
    metadata = make_metadata(urls)
    # Both original labels and the original URL are preserved for output.
    assert metadata.urls == urls  # type: ignore[attr-defined]
    message = metadata.as_rfc822()  # type: ignore[attr-defined]
    assert message.get_all("Project-URL") == [
        "Homepage, https://example.com",
        "主页, https://example.com",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "mailto:dev@example.com",
        "ftp://example.com/pkg",
        "file:///tmp/pkg",
        "data:text/plain,hello",
    ],
)
def test_unsupported_scheme_rejected(url: str) -> None:
    scheme = url.split(":", maxsplit=1)[0]
    with pytest.raises(
        pyproject_metadata.ConfigurationError,
        match=re.escape(f"uses unsupported scheme {scheme!r}"),
    ):
        make_metadata({"Contact": url})


@pytest.mark.parametrize(
    "url",
    [
        "example.com",
        "example.com/path",
        "//example.com/path",
        "https://",
        "https:///path",
        "https://exa mple.com",
        "https://example.com:bad/",
        "https://example.com:99999/",
        "not a url at all",
    ],
)
def test_invalid_absolute_url_rejected(url: str) -> None:
    with pytest.raises(
        pyproject_metadata.ConfigurationError,
        match=re.escape("is not a valid absolute URL"),
    ):
        make_metadata({"Homepage": url})


def test_fragment_and_query_preserved() -> None:
    url = "https://example.com/page?x=1&y=2#frag"
    metadata = make_metadata({"Homepage": url})
    assert metadata.urls["Homepage"] == url  # type: ignore[attr-defined]
    message = metadata.as_rfc822()  # type: ignore[attr-defined]
    assert message.get_all("Project-URL") == [f"Homepage, {url}"]


def test_unknown_label_kept_as_is() -> None:
    urls = {"Totally Custom Label": "https://example.com/x"}
    metadata = make_metadata(urls)
    assert metadata.urls == urls  # type: ignore[attr-defined]
    message = metadata.as_rfc822()  # type: ignore[attr-defined]
    assert message.get_all("Project-URL") == [
        "Totally Custom Label, https://example.com/x"
    ]


def test_multiple_addresses_same_category_not_overwritten() -> None:
    # Two unknown (uncategorized) labels with different addresses are all
    # kept; nothing is silently overwritten.
    urls = {
        "Mirror One": "https://one.example.com",
        "Mirror Two": "https://two.example.com",
    }
    metadata = make_metadata(urls)
    assert metadata.urls == urls  # type: ignore[attr-defined]


def test_diagnostics_collected_with_all_errors() -> None:
    urls = {
        "Homepage": "https://example.com/a",
        "主页": "https://example.com/b",
        "Source": "https://example.com/s1",
        "仓库": "https://example.com/s2",
        "Contact": "mailto:dev@example.com",
    }
    with pytest.raises(pyproject_metadata.errors.ExceptionGroup) as exc_info:
        make_metadata(urls, all_errors=True)
    messages = [e.args[0] for e in exc_info.value.exceptions]
    assert len(messages) == 3
    assert sum("conflicting addresses" in m for m in messages) == 2
    assert sum("unsupported scheme" in m for m in messages) == 1


def test_failed_parse_returns_no_metadata() -> None:
    # A failed parse raises; no half-built metadata object is produced.
    with pytest.raises(pyproject_metadata.ConfigurationError):
        make_metadata({"Homepage": "not-a-url"})


def test_failed_compile_does_not_emit_partial_metadata() -> None:
    metadata = make_metadata({"Homepage": "https://example.com"})
    # Simulate a backend injecting a bad URL after parsing.
    metadata.urls["Broken"] = "not-a-url"  # type: ignore[attr-defined]
    with pytest.raises(
        pyproject_metadata.ConfigurationError,
        match=re.escape("is not a valid absolute URL"),
    ):
        metadata.as_rfc822()  # type: ignore[attr-defined]


def test_failed_compile_does_not_emit_partial_metadata_all_errors() -> None:
    metadata = make_metadata({"Homepage": "https://example.com"}, all_errors=True)
    metadata.urls["Broken"] = "not-a-url"  # type: ignore[attr-defined]
    with pytest.raises(pyproject_metadata.errors.ExceptionGroup):
        metadata.as_rfc822()  # type: ignore[attr-defined]


def test_valid_urls_accepted() -> None:
    urls = {
        "Homepage": "https://example.com",
        "Source": "https://github.com/example/project",
        "Docs Mirror": "http://docs.example.com:8080/project?lang=en#top",
    }
    metadata = make_metadata(urls)
    assert metadata.urls == urls  # type: ignore[attr-defined]
    assert metadata.as_json()["project_url"] == [  # type: ignore[attr-defined]
        "Homepage, https://example.com",
        "Source, https://github.com/example/project",
        "Docs Mirror, http://docs.example.com:8080/project?lang=en#top",
    ]
