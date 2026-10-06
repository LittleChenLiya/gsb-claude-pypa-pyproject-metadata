# SPDX-License-Identifier: MIT

r"""
Explicit field-source planning for the ``[project]`` table (PEP 621).

Every project metadata field has exactly one of three *sources*:

``static``
    The field is explicitly defined in the ``[project]`` table of
    ``pyproject.toml``.

``dynamic``
    The field is listed in ``project.dynamic`` and has no static definition;
    the value is declared to be supplied by the build backend. If no backend
    value is available yet, it is merely *pending* the backend.

``backend``
    The field was declared dynamic and a concrete value has been provided by
    the build backend (passed in as already-computed data).

A single field must never mix a static definition with a ``dynamic``
declaration. PEP 621 states that a listed dynamic field must not also appear
under ``[project]``. The only field that may legitimately remain backend-pending
while still satisfying the required-field check is ``version``; ``name`` is
required and can never be dynamic.

The planner is a *pure data analysis*: it never executes a build backend hook,
never installs ``[build-system].requires``, and never imports the project being
described. Backend values, when available, must be passed in pre-computed via
``backend_values``. Consequently ``FieldSourcePlan.backend_hook_calls`` is
always ``0``.

The report preserves, for every planned field and every problem, both the
canonical input location (e.g. ``project.optional-dependencies.test[0]``) and
the real field name as written in the TOML (e.g. ``optional-dependencies``).

The tests for this module can be run with::

    python -m pytest tests/test_field_sources.py -q
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
    "FieldSource",
    "FieldSourcePlan",
    "FieldSourceProblem",
    "FieldSourceProblemKind",
    "PlannedField",
    "plan_field_sources",
]


def __dir__() -> list[str]:
    return __all__


class FieldSource(enum.Enum):
    """
    Where a project field value comes from.
    """

    STATIC = "static"
    DYNAMIC = "dynamic"
    BACKEND = "backend"


class FieldSourceProblemKind(enum.Enum):
    """
    Classification of a field-source problem.

    Missing required fields and unknown fields are deliberately separate, as
    are the static/dynamic and static/backend conflict categories.
    """

    MISSING_REQUIRED = "missing-required"
    UNKNOWN_FIELD = "unknown-field"
    STATIC_DYNAMIC_CONFLICT = "static-dynamic-conflict"
    STATIC_BACKEND_CONFLICT = "static-backend-conflict"
    DYNAMIC_NOT_ALLOWED = "dynamic-not-allowed"
    BACKEND_NOT_DECLARED = "backend-not-declared"


@dataclasses.dataclass(frozen=True)
class PlannedField:
    """
    A single leaf of the field-source plan.

    ``location`` is the canonical dotted/indexed path into the input
    (``project.version``, ``project.optional-dependencies.test[0]``);
    ``field`` is the real PEP 621 field name (``version``,
    ``optional-dependencies``).
    """

    location: str
    field: str
    source: FieldSource
    pending_backend: bool = False
    top_level: bool = True


@dataclasses.dataclass(frozen=True)
class FieldSourceProblem:
    """
    A problem found while planning field sources.

    ``location`` and ``field`` pinpoint the real input location and field name.
    """

    kind: FieldSourceProblemKind
    location: str
    field: str
    message: str

    def as_configuration_error(self) -> ConfigurationError:
        """
        Return this problem as a :class:`ConfigurationError`.
        """
        return ConfigurationError(self.message, key=self.location)


@dataclasses.dataclass(frozen=True)
class FieldSourcePlan:
    """
    The result of :func:`plan_field_sources`.

    ``fields`` lists every planned leaf (each top-level field and, for
    ``dependencies``/``optional-dependencies``, each requirement). ``problems``
    lists all detected issues, kept separate by kind.

    ``backend_hook_calls`` is always ``0``: planning never executes a backend.
    """

    fields: list[PlannedField]
    problems: list[FieldSourceProblem]
    backend_hook_calls: int = 0

    @property
    def missing_required(self) -> list[FieldSourceProblem]:
        """Problems of the missing-required-field kind."""
        return [
            p
            for p in self.problems
            if p.kind is FieldSourceProblemKind.MISSING_REQUIRED
        ]

    @property
    def unknown_fields(self) -> list[FieldSourceProblem]:
        """Problems of the unknown-field kind."""
        return [
            p for p in self.problems if p.kind is FieldSourceProblemKind.UNKNOWN_FIELD
        ]

    def source_of(self, field: str) -> FieldSource | None:
        """
        Return the source of a top-level project field, or None if unplanned.
        """
        planned = next(
            (f for f in self.fields if f.field == field and f.top_level),
            None,
        )
        return planned.source if planned is not None else None

    def is_pending_backend(self, field: str) -> bool:
        """
        Whether a field is declared dynamic and still awaiting a backend value.
        """
        planned = next(
            (f for f in self.fields if f.field == field and f.top_level), None
        )
        return planned is not None and planned.pending_backend

    def finalize(self) -> None:
        """
        Raise a :class:`ConfigurationError` for the first problem, if any.
        """
        if self.problems:
            raise self.problems[0].as_configuration_error()


# Fields PEP 621 forbids in project.dynamic: there is no point at which the
# static reader could accept a backend-supplied value for these.
_NEVER_DYNAMIC = frozenset({"name", "dynamic"})

# Unconditionally required top-level project fields. ``version`` is conditional
# (a dynamic declaration satisfies it) and therefore handled separately.
_REQUIRED_FIELDS = frozenset({"name"})


def _quote_key(key: str) -> str:
    """
    Quote a table key for a location, matching ``project_table.join``.
    """
    return repr(key) if "." in key else key


def _dynamic_entries(project: Mapping[str, Any]) -> tuple[list[str], dict[str, str]]:
    """
    Return the de-duplicated dynamic entries and their first input locations.

    A non-list ``dynamic`` value is a type problem handled by the type layer;
    here it simply declares nothing.
    """
    entries: list[str] = []
    locations: dict[str, str] = {}
    raw_dynamic = project.get("dynamic", [])
    if isinstance(raw_dynamic, list):
        for index, entry in enumerate(raw_dynamic):
            if isinstance(entry, str):
                # Keep the first location for any duplicate dynamic entry.
                locations.setdefault(entry, f"project.dynamic[{index}]")
                if entry not in entries:
                    entries.append(entry)
    return entries, locations


def _plan_dynamic_fields(
    dynamic_entries: list[str],
    dynamic_locations: Mapping[str, str],
    present: frozenset[str],
    backend_values: Mapping[str, Any],
    fields: list[PlannedField],
    problems: list[FieldSourceProblem],
) -> None:
    """Classify every entry of ``project.dynamic``."""
    for field_name in dynamic_entries:
        location = dynamic_locations[field_name]
        if field_name in _NEVER_DYNAMIC:
            problems.append(
                FieldSourceProblem(
                    FieldSourceProblemKind.DYNAMIC_NOT_ALLOWED,
                    location,
                    field_name,
                    f'Field "project.{field_name}" cannot be listed in '
                    '"project.dynamic"',
                )
            )
            continue
        if field_name not in constants.KNOWN_PROJECT_FIELDS:
            problems.append(
                FieldSourceProblem(
                    FieldSourceProblemKind.UNKNOWN_FIELD,
                    location,
                    field_name,
                    f'Unknown field {field_name!r} listed in "project.dynamic"',
                )
            )
            continue
        if field_name in present:
            # Statically defined and declared dynamic: sources must not mix.
            # The static definition is planned by the static-field pass, which
            # keeps the real value location visible.
            problems.append(
                FieldSourceProblem(
                    FieldSourceProblemKind.STATIC_DYNAMIC_CONFLICT,
                    f"project.{field_name}",
                    field_name,
                    f'Field "project.{field_name}" is both statically defined '
                    'and listed in "project.dynamic"; a field cannot use both '
                    "sources",
                )
            )
            continue
        source = (
            FieldSource.BACKEND if field_name in backend_values else FieldSource.DYNAMIC
        )
        fields.append(
            PlannedField(
                f"project.{field_name}",
                field_name,
                source,
                pending_backend=source is FieldSource.DYNAMIC,
            )
        )


def _plan_static_fields(
    project: Mapping[str, Any],
    dynamic: frozenset[str],
    backend_values: Mapping[str, Any],
    fields: list[PlannedField],
    problems: list[FieldSourceProblem],
) -> None:
    """Classify every key statically present in the project table."""
    for field_name, value in project.items():
        if field_name == "dynamic":
            fields.append(
                PlannedField("project.dynamic", "dynamic", FieldSource.STATIC)
            )
        else:
            if (
                field_name not in dynamic
                and field_name not in constants.KNOWN_PROJECT_FIELDS
            ):
                problems.append(
                    FieldSourceProblem(
                        FieldSourceProblemKind.UNKNOWN_FIELD,
                        f"project.{field_name}",
                        field_name,
                        f'Unknown field {field_name!r} present in "project"',
                    )
                )
            fields.append(
                PlannedField(f"project.{field_name}", field_name, FieldSource.STATIC)
            )
            # Nested leaves for requirement arrays, located by their spec path.
            if field_name not in dynamic:
                _plan_requirement_leaves(field_name, value, fields)

        if field_name in backend_values:
            problems.append(
                FieldSourceProblem(
                    FieldSourceProblemKind.STATIC_BACKEND_CONFLICT,
                    f"project.{field_name}",
                    field_name,
                    f'Field "project.{field_name}" is statically defined but '
                    "also given a backend-provided value",
                )
            )


def _check_required(
    present: frozenset[str], dynamic: frozenset[str]
) -> list[FieldSourceProblem]:
    """
    Report missing required fields.

    Presence is key membership: an explicit empty value ("", [], {}) is a real
    static value and never counts as missing. ``version`` is satisfied by a
    dynamic declaration; ``name`` can never be dynamic.
    """
    problems: list[FieldSourceProblem] = [
        FieldSourceProblem(
            FieldSourceProblemKind.MISSING_REQUIRED,
            f"project.{required}",
            required,
            f'Field "project.{required}" is required',
        )
        for required in sorted(_REQUIRED_FIELDS)
        if required not in present
    ]
    if "version" not in present and "version" not in dynamic:
        problems.append(
            FieldSourceProblem(
                FieldSourceProblemKind.MISSING_REQUIRED,
                "project.version",
                "version",
                'Field "project.version" is required unless "version" is '
                'listed in "project.dynamic"',
            )
        )
    return problems


def plan_field_sources(
    pyproject: Mapping[str, Any],
    backend_values: Mapping[str, Any] | None = None,
) -> FieldSourcePlan:
    """
    Plan the explicit source of every ``[project]`` field.

    ``pyproject`` is the parsed ``pyproject.toml`` mapping. ``backend_values``
    is an optional mapping of project field name to an already-computed backend
    value; it is plain data, so no backend is ever executed.

    Configuration problems are never raised here; they are returned in the
    plan, separated by :class:`FieldSourceProblemKind`. Call
    :meth:`FieldSourcePlan.finalize` to raise them as
    :class:`ConfigurationError`.
    """
    fields: list[PlannedField] = []
    problems: list[FieldSourceProblem] = []
    backend_values = dict(backend_values or {})

    project = pyproject.get("project")
    if not isinstance(project, dict):
        if "project" not in pyproject:
            problems.append(
                FieldSourceProblem(
                    FieldSourceProblemKind.MISSING_REQUIRED,
                    "project",
                    "project",
                    'Section "project" missing in pyproject.toml',
                )
            )
        else:
            problems.append(
                FieldSourceProblem(
                    FieldSourceProblemKind.UNKNOWN_FIELD,
                    "project",
                    "project",
                    'Field "project" has an invalid type, expecting a table',
                )
            )
        return FieldSourcePlan(fields=fields, problems=problems)

    present = frozenset(project)
    dynamic_entries, dynamic_locations = _dynamic_entries(project)
    dynamic = frozenset(dynamic_entries)

    _plan_dynamic_fields(
        dynamic_entries,
        dynamic_locations,
        present,
        backend_values,
        fields,
        problems,
    )
    _plan_static_fields(project, dynamic, backend_values, fields, problems)

    # Backend values without a matching dynamic declaration.
    problems.extend(
        FieldSourceProblem(
            FieldSourceProblemKind.BACKEND_NOT_DECLARED,
            f"project.{field_name}",
            field_name,
            f'A backend value was provided for "project.{field_name}" '
            'but it is not listed in "project.dynamic"',
        )
        for field_name in backend_values
        if field_name not in present and field_name not in dynamic
    )

    problems.extend(_check_required(present, dynamic))

    return FieldSourcePlan(fields=fields, problems=problems)


def _plan_requirement_leaves(
    field_name: str, value: object, fields: list[PlannedField]
) -> None:
    """
    Plan the individual requirements of the requirement-array fields.

    Nested ``optional-dependencies`` entries are located by their spec path,
    e.g. ``project.optional-dependencies.test[0]``.
    """
    if field_name == "dependencies" and isinstance(value, list):
        fields.extend(
            PlannedField(
                f"project.dependencies[{index}]",
                field_name,
                FieldSource.STATIC,
                top_level=False,
            )
            for index, _item in enumerate(value)
        )
    elif field_name == "optional-dependencies" and isinstance(value, dict):
        for extra, requirements in value.items():
            key = _quote_key(str(extra))
            if isinstance(requirements, list):
                fields.extend(
                    PlannedField(
                        f"project.optional-dependencies.{key}[{index}]",
                        field_name,
                        FieldSource.STATIC,
                        top_level=False,
                    )
                    for index, _item in enumerate(requirements)
                )
