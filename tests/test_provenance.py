# SPDX-License-Identifier: MIT

"""
Tests for the explicit field provenance plan (pyproject_metadata.provenance).

Run with:

    python -m pytest tests/test_provenance.py -v
"""

from __future__ import annotations

import pathlib
import sys

import pytest

if sys.version_info < (3, 11):
    import tomli as tomllib
else:
    import tomllib

import pyproject_metadata
from pyproject_metadata import (
    ConfigurationError,
    FieldOrigin,
    build_provenance_plan,
)

DIR = pathlib.Path(__file__).parent.resolve()
PACKAGES = DIR / "packages"


def load_example(name: str) -> dict:
    with PACKAGES.joinpath(name, "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


def test_static_and_dynamic_version_rejected() -> None:
    """version defined statically AND declared dynamic must be rejected."""
    data = load_example("provenance-conflict")
    with pytest.raises(ConfigurationError, match="version"):
        build_provenance_plan(data)

    plan = build_provenance_plan(data, strict=False)
    assert plan.conflicts == ["version"]


def test_legal_dynamic_version_pending_backend() -> None:
    """A legal dynamic version is marked as pending from the backend."""
    plan = build_provenance_plan(load_example("provenance-dynamic"))
    assert plan.conflicts == []
    assert plan.missing_required == []
    assert plan.dynamic_fields == ["version"]
    assert plan.pending_backend == ["version"]
    assert "version" not in plan.static_fields


def test_missing_name_reported_as_required() -> None:
    """A missing name is reported as a missing required field."""
    plan = build_provenance_plan({"project": {"version": "1.0.0"}})
    assert "project.name" in plan.missing_required
    assert "project.version" not in plan.missing_required


def test_missing_version_reported_unless_dynamic() -> None:
    plan = build_provenance_plan({"project": {"name": "foo"}})
    assert "project.version" in plan.missing_required

    plan = build_provenance_plan({"project": {"name": "foo", "dynamic": ["version"]}})
    assert plan.missing_required == []


def test_backend_never_executed() -> None:
    """Building the plan never executes the backend nor imports the package."""
    for example in ("provenance-static", "provenance-dynamic"):
        plan = build_provenance_plan(load_example(example))
        assert plan.backend_calls == 0
    assert "provenance_static" not in sys.modules
    assert "provenance_dynamic" not in sys.modules


def test_empty_value_does_not_count_as_present() -> None:
    """An empty value must not substitute for a missing required field."""
    plan = build_provenance_plan({"project": {"name": "", "version": "1.0.0"}})
    assert "project.name" in plan.missing_required

    plan = build_provenance_plan({"project": {"name": "foo", "version": ""}})
    assert "project.version" in plan.missing_required


def test_optional_dependencies_nested_spec_paths() -> None:
    """Nested optional-dependencies are located by their specification path."""
    plan = build_provenance_plan(load_example("provenance-static"))
    locations = {p.location for p in plan.fields}
    assert "project.optional-dependencies" in locations
    assert "project.optional-dependencies.test" in locations
    assert "project.optional-dependencies.docs" in locations
    # The real field name (with hyphens) is kept, not a Python identifier.
    nested = [p for p in plan.fields if p.location.endswith(".test")]
    assert nested[0].field == "optional-dependencies"
    assert nested[0].origin is FieldOrigin.STATIC


def test_unknown_fields_separate_from_missing_required() -> None:
    """Unknown fields and missing required fields are reported separately."""
    plan = build_provenance_plan(
        {"project": {"version": "1.0.0", "fancy-field": "x"}}
    )
    assert plan.unknown_fields == ["project.fancy-field"]
    assert plan.missing_required == ["project.name"]
    assert "project.fancy-field" not in plan.missing_required


def test_report_keeps_locations_and_real_field_names() -> None:
    plan = build_provenance_plan(load_example("provenance-static"))
    report = plan.report()
    assert "backend calls: 0" in report
    assert "project.optional-dependencies.test" in report
    assert "optional-dependencies" in report
    assert "project.name" in report


def test_no_project_table_reports_required() -> None:
    plan = build_provenance_plan({})
    assert "project.name" in plan.missing_required
    assert "project.version" in plan.missing_required
    assert plan.backend_calls == 0


def test_exported_from_top_level() -> None:
    assert pyproject_metadata.build_provenance_plan is build_provenance_plan
    assert pyproject_metadata.FieldOrigin is FieldOrigin
