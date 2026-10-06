# SPDX-License-Identifier: MIT

from __future__ import annotations

import socket

import pytest

import pyproject_metadata
from pyproject_metadata import (
    ConfigurationError,
    StandardMetadata,
    build_url_plan,
    canonical_url_label,
)
from pyproject_metadata.errors import ExceptionGroup


@pytest.mark.parametrize(
    ("label", "category"),
    [
        ("Homepage", "Homepage"),
        ("homepage", "Homepage"),
        ("Home-Page", "Homepage"),
        ("home page", "Homepage"),
        ("主页", "Homepage"),
        ("Documentation", "Documentation"),
        ("docs", "Documentation"),
        ("Source Code", "Source"),
        ("repository", "Source"),
        ("Bug Tracker", "Bug Tracker"),
        ("issues", "Bug Tracker"),
        ("something-else", None),
        ("Totally Unknown", None),
    ],
)
def test_canonical_url_label(label: str, category: str | None) -> None:
    assert canonical_url_label(label) == category


def test_same_address_keeps_both_sources() -> None:
    plan = build_url_plan(
        {
            "Homepage": "https://example.com/",
            "主页": "https://example.com/",
        }
    )
    assert len(plan) == 2
    assert plan.as_dict() == {
        "Homepage": "https://example.com/",
        "主页": "https://example.com/",
    }
    homepage_entries = plan.by_category()["Homepage"]
    assert [entry.label for entry in homepage_entries] == ["Homepage", "主页"]


def test_same_category_different_addresses_is_source_conflict() -> None:
    with pytest.raises(
        ConfigurationError,
        match=r"Conflicting sources for URL category 'Homepage' in \"project.urls\"",
    ):
        build_url_plan(
            {
                "Homepage": "https://example.com/",
                "主页": "https://elsewhere.example.com/",
            }
        )


def test_source_conflict_no_partial_plan() -> None:
    # Even the valid entries must not be returned when the build fails.
    plan = None
    with pytest.raises(ConfigurationError):
        plan = build_url_plan(
            {
                "Documentation": "https://docs.example.com/",
                "Homepage": "https://example.com/",
                "主页": "https://elsewhere.example.com/",
            }
        )
    assert plan is None


def test_all_errors_collects_diagnostics() -> None:
    with pytest.raises(ExceptionGroup) as exc_info:
        build_url_plan(
            {
                "Homepage": "https://example.com/",
                "主页": "https://elsewhere.example.com/",
                "Contact": "mailto:someone@example.com",
                "Source": "not-an-absolute-url",
            },
            all_errors=True,
        )
    messages = [str(error) for error in exc_info.value.exceptions]
    assert len(messages) == 3
    assert any("Conflicting sources" in message for message in messages)
    assert any("unsupported scheme 'mailto'" in message for message in messages)
    assert any("not an absolute URL" in message for message in messages)


@pytest.mark.parametrize("scheme", ["mailto", "ftp", "file", "git"])
def test_unsupported_schemes_rejected(scheme: str) -> None:
    with pytest.raises(ConfigurationError, match=f"uses unsupported scheme {scheme!r}"):
        build_url_plan({"Contact": f"{scheme}:someone@example.com"})


@pytest.mark.parametrize(
    "url",
    [
        "example.com",
        "example.com/path",
        "/absolute/path",
        "//example.com/scheme-relative",
    ],
)
def test_non_absolute_urls_rejected(url: str) -> None:
    with pytest.raises(ConfigurationError, match="not an absolute URL"):
        build_url_plan({"Homepage": url})


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/some path",
        "https://example.com/\t",
        "https://example.com/a\nb",
    ],
)
def test_whitespace_urls_rejected(url: str) -> None:
    with pytest.raises(ConfigurationError, match="contains whitespace"):
        build_url_plan({"Homepage": url})


def test_missing_host_rejected() -> None:
    with pytest.raises(ConfigurationError, match="missing host"):
        build_url_plan({"Homepage": "https:///path/only"})


def test_unknown_labels_kept_as_is() -> None:
    plan = build_url_plan({"My Custom Label": "https://example.com/custom"})
    (entry,) = plan.entries
    assert entry.label == "My Custom Label"
    assert entry.url == "https://example.com/custom"
    assert entry.category is None
    assert plan.by_category() == {}
    assert plan.as_dict() == {"My Custom Label": "https://example.com/custom"}


def test_query_and_fragment_preserved() -> None:
    url = "https://example.com/page?query=1&other=2#fragment"
    plan = build_url_plan({"Homepage": url})
    assert plan.as_dict()["Homepage"] == url


def test_original_labels_and_urls_preserved_for_output() -> None:
    urls = {
        "Homepage": "https://example.com/",
        "主页": "https://example.com/",
        "Docs": "https://docs.example.com/",
        "Unknown Thing": "https://thing.example.com/",
    }
    plan = build_url_plan(urls)
    assert plan.as_dict() == urls
    assert [entry.label for entry in plan] == list(urls)


def test_validation_is_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*_args: object, **_kwargs: object) -> None:
        pytest.fail("network access attempted during URL validation")

    monkeypatch.setattr(socket, "create_connection", _fail)
    monkeypatch.setattr(socket, "getaddrinfo", _fail)
    plan = build_url_plan({"Homepage": "https://example.com/"})
    assert len(plan) == 1


def _metadata(urls: dict[str, str], **kwargs: bool) -> StandardMetadata:
    return StandardMetadata.from_pyproject(
        {"project": {"name": "test", "version": "1.0", "urls": urls}},
        allow_extra_keys=False,
        **kwargs,  # type: ignore[arg-type]
    )


def test_standard_metadata_url_plan() -> None:
    metadata = _metadata(
        {
            "Homepage": "https://example.com/",
            "主页": "https://example.com/",
        }
    )
    plan = metadata.url_plan()
    assert len(plan) == 2
    # The raw urls mapping is untouched, and output keeps the original
    # labels and URLs.
    message = metadata.as_rfc822()
    project_urls = message.get_all("Project-URL")
    assert project_urls == [
        "Homepage, https://example.com/",
        "主页, https://example.com/",
    ]


def test_standard_metadata_url_plan_conflict() -> None:
    metadata = _metadata(
        {
            "Homepage": "https://example.com/",
            "主页": "https://elsewhere.example.com/",
        }
    )
    with pytest.raises(ConfigurationError, match="Conflicting sources"):
        metadata.url_plan()


def test_standard_metadata_url_plan_all_errors() -> None:
    metadata = _metadata(
        {
            "Homepage": "https://example.com/",
            "主页": "https://elsewhere.example.com/",
            "Contact": "mailto:someone@example.com",
        },
        all_errors=True,
    )
    with pytest.raises(ExceptionGroup) as exc_info:
        metadata.url_plan()
    assert len(exc_info.value.exceptions) == 2


def test_url_plan_exports() -> None:
    assert "build_url_plan" in pyproject_metadata.__all__
    assert "URLPlan" in pyproject_metadata.__all__
    assert "URLPlanEntry" in pyproject_metadata.__all__
    assert "canonical_url_label" in pyproject_metadata.__all__
