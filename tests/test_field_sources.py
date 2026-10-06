"""
Tests for the explicit field-source planner (``pyproject_metadata.field_sources``).

Run with::

    python -m pytest tests/test_field_sources.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pyproject_metadata import (
    ConfigurationError,
    FieldSource,
    FieldSourceProblemKind,
    plan_field_sources,
)

if sys.version_info < (3, 11):
    import tomli as tomllib
else:
    import tomllib

DIR = Path(__file__).parent.resolve()
PACKAGES = DIR / "packages"


def _load(name: str) -> dict:
    with (PACKAGES / name / "pyproject.toml").open("rb") as file_obj:
        return tomllib.load(file_obj)


def _kinds(plan: object) -> set[FieldSourceProblemKind]:
    return {problem.kind for problem in plan.problems}


# ---------------------------------------------------------------------------
# Acceptance: version both static and dynamic is rejected
# ---------------------------------------------------------------------------


def test_version_static_and_dynamic_is_rejected() -> None:
    pyproject = _load("field-sources-conflict")
    plan = plan_field_sources(pyproject)

    conflict = [
        p
        for p in plan.problems
        if p.kind is FieldSourceProblemKind.STATIC_DYNAMIC_CONFLICT
    ]
    assert len(conflict) == 1
    problem = conflict[0]
    # The report keeps the real location and real field name.
    assert problem.location == "project.version"
    assert problem.field == "version"
    assert "both" in problem.message

    with pytest.raises(ConfigurationError, match=r"project\.version"):
        plan.finalize()


# ---------------------------------------------------------------------------
# Acceptance: a legal dynamic version is pending the backend
# ---------------------------------------------------------------------------


def test_legal_dynamic_version_is_pending_backend() -> None:
    pyproject = _load("field-sources-dynamic")
    plan = plan_field_sources(pyproject)

    assert plan.problems == []
    assert plan.source_of("version") is FieldSource.DYNAMIC
    assert plan.is_pending_backend("version") is True
    assert plan.source_of("name") is FieldSource.STATIC

    # Once a backend has computed the value it upgrades the source; the
    # planner itself still executes no backend.
    plan_with_value = plan_field_sources(pyproject, {"version": "2.0.0"})
    assert plan_with_value.source_of("version") is FieldSource.BACKEND
    assert plan_with_value.is_pending_backend("version") is False
    assert plan_with_value.problems == []


# ---------------------------------------------------------------------------
# Acceptance: missing name is a required-field problem
# ---------------------------------------------------------------------------


def test_missing_name_is_required_problem() -> None:
    pyproject = {"project": {"version": "1.0.0"}}
    plan = plan_field_sources(pyproject)

    assert len(plan.missing_required) == 1
    problem = plan.missing_required[0]
    assert problem.field == "name"
    assert problem.location == "project.name"
    assert not plan.unknown_fields

    with pytest.raises(ConfigurationError, match=r"project\.name"):
        plan.finalize()


def test_missing_project_section_is_required_problem() -> None:
    plan = plan_field_sources({})
    assert [p.field for p in plan.missing_required] == ["project"]
    assert plan.unknown_fields == []


# ---------------------------------------------------------------------------
# Acceptance: the planner never executes a backend
# ---------------------------------------------------------------------------


def test_no_backend_is_ever_executed() -> None:
    # A plausible but non-installable backend: importing/calling it would raise
    # ModuleNotFoundError, proving the planner must never attempt to do so.
    pyproject = {
        "build-system": {
            "requires": ["definitely-not-installed-backend-xyz"],
            "build-backend": "definitely_not_installed_backend_xyz.buildapi",
        },
        "project": {
            "name": "no-backend",
            "dynamic": ["version", "description"],
        },
    }
    plan = plan_field_sources(pyproject)

    # The backend hook call counter is contractually always zero.
    assert plan.backend_hook_calls == 0
    assert plan.is_pending_backend("version") is True
    assert plan.is_pending_backend("description") is True
    assert plan.problems == []


def test_planner_does_not_import_example_packages() -> None:
    # Planning the on-disk examples must not import the described projects.
    before = set(sys.modules)
    for name in (
        "field-sources-static",
        "field-sources-dynamic",
        "field-sources-conflict",
    ):
        plan_field_sources(_load(name))
    imported = {m for m in (set(sys.modules) - before) if m.startswith("field")}
    assert not imported


# ---------------------------------------------------------------------------
# An explicit empty value is present; it must not be treated as missing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("empty", ["", [], {}])
def test_empty_value_is_not_missing(empty: object) -> None:
    # An empty name is (rightly) a type/validity problem elsewhere, but for the
    # source planner the key *is present*, so it must never be "missing".
    plan = plan_field_sources({"project": {"name": empty, "version": ""}})
    locations = {p.location for p in plan.missing_required}
    assert "project.name" not in locations
    assert "project.version" not in locations
    assert plan.source_of("name") is FieldSource.STATIC
    assert plan.source_of("version") is FieldSource.STATIC


def test_empty_dynamic_list_is_static_and_present() -> None:
    plan = plan_field_sources({"project": {"name": "p", "version": "1", "dynamic": []}})
    assert plan.source_of("dynamic") is FieldSource.STATIC
    assert plan.problems == []


# ---------------------------------------------------------------------------
# Missing required fields and unknown fields are reported separately
# ---------------------------------------------------------------------------


def test_missing_required_and_unknown_fields_are_separate() -> None:
    pyproject = {
        "project": {
            "version": "1.0.0",
            "totally-unknown": True,
        }
    }
    plan = plan_field_sources(pyproject)

    assert [p.field for p in plan.missing_required] == ["name"]
    assert [p.field for p in plan.unknown_fields] == ["totally-unknown"]
    unknown = plan.unknown_fields[0]
    assert unknown.location == "project.totally-unknown"
    assert unknown.message.startswith("Unknown field")


def test_unknown_field_in_dynamic_list_is_unknown_problem() -> None:
    plan = plan_field_sources(
        {"project": {"name": "p", "version": "1", "dynamic": ["bogus-field"]}}
    )
    assert [p.field for p in plan.unknown_fields] == ["bogus-field"]
    assert plan.unknown_fields[0].location == "project.dynamic[0]"


def test_name_cannot_be_dynamic() -> None:
    plan = plan_field_sources(
        {"project": {"name": "p", "version": "1", "dynamic": ["name"]}}
    )
    assert FieldSourceProblemKind.DYNAMIC_NOT_ALLOWED in _kinds(plan)
    # name is still present, so it is not a missing-required problem.
    assert not plan.missing_required


# ---------------------------------------------------------------------------
# Nested optional-dependencies are located by their spec path
# ---------------------------------------------------------------------------


def test_optional_dependencies_nested_locations() -> None:
    pyproject = _load("field-sources-static")
    plan = plan_field_sources(pyproject)

    locations = {f.location for f in plan.fields}
    assert "project.optional-dependencies.test[0]" in locations
    assert "project.optional-dependencies.docs[0]" in locations
    assert "project.dependencies[0]" in locations

    leaves = [
        f for f in plan.fields if f.location == "project.optional-dependencies.test[0]"
    ]
    assert len(leaves) == 1
    leaf = leaves[0]
    # The leaf keeps the real top-level field name, not the extra name.
    assert leaf.field == "optional-dependencies"
    assert leaf.source is FieldSource.STATIC
    assert leaf.top_level is False

    # Top-level lookup still resolves the field itself.
    assert plan.source_of("optional-dependencies") is FieldSource.STATIC


def test_dotted_extra_name_is_quoted_in_location() -> None:
    pyproject = {
        "project": {
            "name": "p",
            "version": "1",
            "optional-dependencies": {"a.b": ["x"]},
        }
    }
    plan = plan_field_sources(pyproject)
    assert "project.optional-dependencies.'a.b'[0]" in {f.location for f in plan.fields}


# ---------------------------------------------------------------------------
# Backend-provided values
# ---------------------------------------------------------------------------


def test_backend_value_for_static_field_conflicts() -> None:
    pyproject = _load("field-sources-static")
    plan = plan_field_sources(pyproject, {"version": "2.0.0"})

    conflicts = [
        p
        for p in plan.problems
        if p.kind is FieldSourceProblemKind.STATIC_BACKEND_CONFLICT
    ]
    assert [p.field for p in conflicts] == ["version"]
    assert conflicts[0].location == "project.version"


def test_backend_value_without_dynamic_declaration_is_problem() -> None:
    pyproject = {"project": {"name": "p", "version": "1"}}
    plan = plan_field_sources(pyproject, {"description": "from backend"})

    problems = [
        p
        for p in plan.problems
        if p.kind is FieldSourceProblemKind.BACKEND_NOT_DECLARED
    ]
    assert [p.field for p in problems] == ["description"]


def test_backend_value_for_dynamic_field_is_backend_source() -> None:
    pyproject = {"project": {"name": "p", "dynamic": ["version", "description"]}}
    plan = plan_field_sources(pyproject, {"version": "3.0.0", "description": "x"})
    assert plan.source_of("version") is FieldSource.BACKEND
    assert plan.source_of("description") is FieldSource.BACKEND
    assert plan.problems == []


# ---------------------------------------------------------------------------
# The all-static example plans cleanly
# ---------------------------------------------------------------------------


def test_static_example_is_clean() -> None:
    plan = plan_field_sources(_load("field-sources-static"))
    assert plan.problems == []
    assert plan.backend_hook_calls == 0
    assert plan.source_of("name") is FieldSource.STATIC
    assert plan.source_of("version") is FieldSource.STATIC
