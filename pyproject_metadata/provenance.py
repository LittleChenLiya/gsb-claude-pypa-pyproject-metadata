# SPDX-License-Identifier: MIT

"""
Explicit field provenance plan for ``[project]`` metadata.

This module classifies every metadata field by where its value comes from:

- ``FieldOrigin.STATIC``: the value is defined directly in the ``[project]``
  table.
- ``FieldOrigin.DYNAMIC``: the field is declared in ``project.dynamic`` and
  its value is pending from the build backend.
- ``FieldOrigin.BACKEND``: the value was provided by the build backend. This
  plan never produces such entries itself: it is computed purely from the
  parsed TOML mapping, it never executes the build backend and never imports
  the project package, so ``FieldProvenancePlan.backend_calls`` is always 0.

A field that is both statically defined and declared dynamic is a conflict and
is rejected. Missing required fields (``project.name``, and ``project.version``
unless dynamic) are reported separately from unknown fields. An empty value
(such as ``name = ""``) never counts as fulfilling a required field. Nested
``optional-dependencies`` entries are located by their specification path,
e.g. ``project.optional-dependencies.test``, keeping the real field names.
"""

from __future__ import annotations

import dataclasses
import enum
import typing

from . import constants
from .errors import ConfigurationError

if typing.TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

__all__ = [
    "FieldOrigin",
    "FieldProvenance",
    "FieldProvenancePlan",
    "build_provenance_plan",
]


def __dir__() -> list[str]:
    return __all__


class FieldOrigin(enum.Enum):
    """
    Where the value of a metadata field comes from.
    """

    STATIC = "static"
    DYNAMIC = "dynamic"
    BACKEND = "backend"


@dataclasses.dataclass(frozen=True)
class FieldProvenance:
    """
    The provenance of a single metadata field.

    Keeps the real field name (with hyphens, exactly as written in
    ``pyproject.toml``) and the input location (the specification path).
    """

    field: str
    location: str
    origin: FieldOrigin


@dataclasses.dataclass
class FieldProvenancePlan:
    """
    An explicit plan of where every ``[project]`` field value comes from.

    ``missing_required`` (required fields that are absent or empty) and
    ``unknown_fields`` (keys not known to the specification) are reported
    separately. ``conflicts`` lists fields that are both statically defined
    and declared dynamic, which is not allowed. ``backend_calls`` counts how
    many times the build backend was executed to build this plan; it is
    always 0 because the plan is derived from the parsed TOML mapping alone.
    """

    fields: list[FieldProvenance] = dataclasses.field(default_factory=list)
    missing_required: list[str] = dataclasses.field(default_factory=list)
    unknown_fields: list[str] = dataclasses.field(default_factory=list)
    conflicts: list[str] = dataclasses.field(default_factory=list)
    backend_calls: int = 0

    @property
    def static_fields(self) -> list[str]:
        """
        Real names of the fields statically defined in ``[project]``.
        """
        return [p.field for p in self.fields if p.origin is FieldOrigin.STATIC]

    @property
    def dynamic_fields(self) -> list[str]:
        """
        Real names of the fields declared in ``project.dynamic``.
        """
        return [p.field for p in self.fields if p.origin is FieldOrigin.DYNAMIC]

    @property
    def pending_backend(self) -> list[str]:
        """
        Fields declared dynamic whose values are still to be provided by the
        build backend.
        """
        return self.dynamic_fields

    def report(self) -> str:
        """
        Render a human-readable report, keeping input locations and real
        field names.
        """
        lines = [f"Field provenance plan (backend calls: {self.backend_calls}):"]
        for provenance in self.fields:
            lines.append(
                f"  {provenance.origin.value}: {provenance.location} ({provenance.field})"
            )
        for location in self.missing_required:
            lines.append(f"  missing required: {location}")
        for location in self.unknown_fields:
            lines.append(f"  unknown field: {location}")
        for field in self.conflicts:
            lines.append(
                f"  conflict: project.{field} ({field}) is both static and dynamic"
            )
        return "\n".join(lines)


def _is_empty(value: object) -> bool:
    """
    An empty value (None or an empty string) never counts as fulfilling a
    required field.
    """
    return value is None or (isinstance(value, str) and not value)


def build_provenance_plan(
    data: Mapping[str, Any], *, strict: bool = True
) -> FieldProvenancePlan:
    """
    Build a :class:`FieldProvenancePlan` from a parsed ``pyproject.toml``
    mapping.

    This never executes the build backend and never imports the project
    package. If ``strict`` is True (the default), a field that is both
    statically defined and declared dynamic is rejected by raising
    :class:`ConfigurationError`; otherwise the conflict is only recorded in
    the plan.
    """
    plan = FieldProvenancePlan()

    project = data.get("project")
    if not isinstance(project, dict):
        project = {}

    dynamic_raw = project.get("dynamic", [])
    dynamic: list[str] = []
    if isinstance(dynamic_raw, list):
        dynamic = [field for field in dynamic_raw if isinstance(field, str)]

    # Unknown fields are reported separately from missing required fields.
    for key in project:
        if key not in constants.KNOWN_PROJECT_FIELDS:
            plan.unknown_fields.append(f"project.{key}")

    # Statically defined fields, keeping the real field names. Nested
    # optional-dependencies entries are located by their specification path.
    for field in project:
        if field == "dynamic" or field not in constants.KNOWN_PROJECT_FIELDS:
            continue
        plan.fields.append(
            FieldProvenance(
                field=field, location=f"project.{field}", origin=FieldOrigin.STATIC
            )
        )
        if field == "optional-dependencies":
            extras = project[field]
            if isinstance(extras, dict):
                for extra in extras:
                    plan.fields.append(
                        FieldProvenance(
                            field="optional-dependencies",
                            location=f"project.optional-dependencies.{extra}",
                            origin=FieldOrigin.STATIC,
                        )
                    )

    # Fields declared dynamic: their values are pending from the backend.
    for field in dynamic:
        plan.fields.append(
            FieldProvenance(
                field=field, location="project.dynamic", origin=FieldOrigin.DYNAMIC
            )
        )

    # A field cannot be both statically defined and declared dynamic.
    static = {p.field for p in plan.fields if p.origin is FieldOrigin.STATIC}
    for field in dynamic:
        if field in static and field not in plan.conflicts:
            plan.conflicts.append(field)
    if strict and plan.conflicts:
        fields = ", ".join(sorted(plan.conflicts))
        msg = (
            f"Field(s) {fields} cannot be both statically defined in "
            '"project" and declared in "project.dynamic"'
        )
        raise ConfigurationError(msg, key="project.dynamic")

    # Missing required fields; an empty value does not count as present.
    if "name" not in project or _is_empty(project["name"]):
        plan.missing_required.append("project.name")
    if "version" not in dynamic and (
        "version" not in project or _is_empty(project["version"])
    ):
        plan.missing_required.append("project.version")

    return plan
