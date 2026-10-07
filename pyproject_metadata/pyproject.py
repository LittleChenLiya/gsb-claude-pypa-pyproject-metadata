# SPDX-License-Identifier: MIT

"""
This module focuses on reading pyproject.toml fields with error collection. It is
mostly internal, except for License and Readme classes, which are re-exported in
the top-level package.

These functions return None if an error occurred, and use the ErrorCollector to
record errors.
"""

from __future__ import annotations

import dataclasses
import pathlib
import typing
from typing import Any, Literal, overload

import packaging.requirements

if typing.TYPE_CHECKING:
    from collections.abc import Generator, Iterable

    from packaging.requirements import Requirement

    from .errors import ErrorCollector
    from .project_table import ProjectTable


__all__ = [
    "License",
    "LicenseFile",
    "Readme",
]


def __dir__() -> list[str]:
    return __all__


def _resolve_real_path(path: pathlib.Path) -> pathlib.Path:
    """Resolve a path to its final target, following all symlinks.

    This is a module-level function so that platforms without permission to
    create symbolic links (e.g. standard Windows accounts) can substitute it
    with a fixture in tests.
    """
    return path.resolve(strict=False)


@dataclasses.dataclass(frozen=True)
class License:
    """
    This represents a classic license, which contains text, and optionally a
    file path. Modern licenses are just SPDX identifiers, which are strings.
    """

    text: str
    file: pathlib.Path | None


@dataclasses.dataclass(frozen=True)
class LicenseFile:
    """
    This represents an expanded ``project.license-files`` entry when the
    real-path check is enabled. It records both the declared path (the glob
    result, kept relative to the project directory) and the actual final path
    after all symbolic links have been resolved. File contents are never read.
    """

    declared: pathlib.Path
    resolved: pathlib.Path


@dataclasses.dataclass(frozen=True)
class Readme:
    """
    This represents a readme, which contains text and a content type, and
    optionally a file path.
    """

    text: str
    file: pathlib.Path | None
    content_type: str


def ensure_str(value: object) -> str | None:
    """Ensure that a value is a string."""
    if isinstance(value, str):
        return value
    return None


def ensure_list(val: object) -> list[str] | None:
    """Ensure that a value is a list of strings."""
    if val is None:
        return None
    if not isinstance(val, list):
        return None
    for item in val:
        if not isinstance(item, str):
            return None

    return val


def ensure_dict(val: object) -> dict[str, str] | None:
    """Ensure that a value is a dictionary of strings."""
    if not isinstance(val, dict):
        return None
    for item in val.values():
        if not isinstance(item, str):
            return None
    return val


def ensure_people(val: object) -> list[tuple[str, str | None]]:
    """Ensure that a value is a list of tables with optional "name" and "email" keys."""
    if not isinstance(val, list):
        return []
    for each in val:
        if not isinstance(each, dict):
            return []
        for value in each.values():
            if not isinstance(value, str):
                return []
    return [(entry.get("name", "Unknown"), entry.get("email")) for entry in val]


def get_license(
    project: ProjectTable, project_dir: pathlib.Path, error_collector: ErrorCollector
) -> License | str | None:
    """Get the license field from the project table. Handles PEP 639 style license too.

    None is returned if the license field is not present or if an error occurred.
    """
    val = project.get("license")
    if val is None:
        return None
    if isinstance(val, str):
        return val

    if isinstance(val, dict):
        _license = ensure_dict(val)
        if _license is None:
            return None
    else:
        return None

    file: pathlib.Path | None = None
    filename = _license.get("file")
    text = _license.get("text")

    if (filename and text) or (not filename and not text):
        return None

    if filename:
        file = project_dir.joinpath(filename)
        if not file.is_file():
            msg = "License file not found ({filename!r})"
            error_collector.config_error(
                msg, key="project.license.file", filename=filename
            )
            return None
        text = file.read_text(encoding="utf-8")

    assert text is not None
    return License(text, file)


@overload
def get_license_files(
    project: ProjectTable,
    project_dir: pathlib.Path,
    error_collector: ErrorCollector,
    *,
    resolve_links: Literal[True],
) -> list[LicenseFile] | None: ...


@overload
def get_license_files(
    project: ProjectTable,
    project_dir: pathlib.Path,
    error_collector: ErrorCollector,
    *,
    resolve_links: Literal[False] = False,
) -> list[pathlib.Path] | None: ...


def get_license_files(
    project: ProjectTable,
    project_dir: pathlib.Path,
    error_collector: ErrorCollector,
    *,
    resolve_links: bool = False,
) -> list[LicenseFile] | list[pathlib.Path] | None:
    """Get the license-files list of files from the project table.

    Returns None if an error occurred (including invalid globs, etc) or if
    not present.

    When ``resolve_links`` is True, each expanded glob result is resolved to
    its final target and the result is checked to stay within the project
    directory; :class:`LicenseFile` entries carrying both the declared and the
    resolved paths are returned instead of bare paths. File contents are never
    read.
    """
    license_files = project.get("license-files")
    if license_files is None:
        return None
    if ensure_list(license_files) is None:
        return None

    return typing.cast(
        "list[LicenseFile] | list[pathlib.Path] | None",
        list(
            _get_files_from_globs(
                project_dir, license_files, error_collector, resolve_links=resolve_links
            )
        ),
    )


def get_readme(  # noqa: C901
    project: ProjectTable, project_dir: pathlib.Path, error_collector: ErrorCollector
) -> Readme | None:
    """Get the text of the readme from the project table.

    Returns None if an error occurred or if the readme field is not present.
    """
    if "readme" not in project:
        return None

    filename: str | None = None
    file: pathlib.Path | None = None
    text: str | None = None
    content_type: str | None = None

    readme = project["readme"]
    if isinstance(readme, str):
        # readme is a file
        text = None
        filename = readme
        if filename.endswith(".md"):
            content_type = "text/markdown"
        elif filename.endswith(".rst"):
            content_type = "text/x-rst"
        else:
            msg = "Could not infer content type for readme file {filename!r}"
            error_collector.config_error(msg, key="project.readme", filename=filename)
            return None
    elif isinstance(readme, dict):
        # readme is a dict containing either 'file' or 'text', and content-type
        for field in readme:
            if field not in ("content-type", "file", "text"):
                return None

        content_type_raw = readme.get("content-type")
        if content_type_raw is not None:
            content_type = ensure_str(content_type_raw)
            if content_type is None:
                return None
        filename_raw = readme.get("file")
        if filename_raw is not None:
            filename = ensure_str(filename_raw)
            if filename is None:
                return None

        text_raw = readme.get("text")
        if text_raw is not None:
            text = ensure_str(text_raw)
            if text is None:
                return None

        if (filename and text) or (not filename and not text):
            return None
        if not content_type:
            return None
    else:
        return None

    if filename:
        file = project_dir.joinpath(filename)
        if not file.is_file():
            msg = "Readme file not found ({filename!r})"
            error_collector.config_error(
                msg, key="project.readme.file", filename=filename
            )
            return None
        text = file.read_text(encoding="utf-8")

    assert text is not None
    return Readme(text, file, content_type)


def get_dependencies(project: ProjectTable) -> list[Requirement]:
    """Get the dependencies from the project table."""
    requirement_strings: list[str] | None = None
    requirement_strings_raw = project.get("dependencies")
    if requirement_strings_raw is not None:
        requirement_strings = ensure_list(requirement_strings_raw)
    if requirement_strings is None:
        return []

    requirements: list[Requirement] = []
    try:
        requirements.extend(
            packaging.requirements.Requirement(req) for req in requirement_strings
        )
    except packaging.requirements.InvalidRequirement:
        return []
    return requirements


def get_optional_dependencies(
    project: ProjectTable,
) -> dict[str, list[Requirement]]:
    """Get the optional dependencies from the project table."""
    val = project.get("optional-dependencies")
    if not val:
        return {}

    requirements_dict: dict[str, list[Requirement]] = {}
    if not isinstance(val, dict):
        return {}
    for extra, requirements in val.items():
        assert isinstance(extra, str)
        if not isinstance(requirements, list):
            return {}
        requirements_dict[extra] = []
        for req in requirements:
            if not isinstance(req, str):
                return {}
            try:
                requirements_dict[extra].append(packaging.requirements.Requirement(req))
            except packaging.requirements.InvalidRequirement:
                return {}
    return requirements_dict


def get_entrypoints(project: ProjectTable) -> dict[str, dict[str, str]]:
    """Get the entrypoints from the project table."""
    val = project.get("entry-points")
    if val is None:
        return {}
    if not isinstance(val, dict):
        return {}
    for entrypoints in val.values():
        if not isinstance(entrypoints, dict):
            return {}
        for entrypoint in entrypoints.values():
            if not isinstance(entrypoint, str):
                return {}
    return val


def get_dynamic(project: dict[str, Any]) -> list[str]:
    """Get the dynamic fields from the project table.

    Returns an empty list if the field is not present or if an error occurred.
    """
    return ensure_list(project.get("dynamic", [])) or []


def _get_files_from_globs(
    project_dir: pathlib.Path,
    globs: Iterable[str],
    error_collector: ErrorCollector,
    *,
    resolve_links: bool = False,
) -> Generator[LicenseFile | pathlib.Path, None, None]:
    """Given a list of globs, get files that match.

    When ``resolve_links`` is True, each expanded result is resolved to its
    final target (following symbolic links) and verified not to escape the
    project directory. Escaping results are reported as configuration errors
    (thus no metadata can be produced from them) instead of yielded.
    """
    real_project_dir = _resolve_real_path(project_dir)
    for glob in globs:
        # Reject any glob that escapes the project directory: absolute paths
        # (POSIX or Windows) or any pattern containing a ".." path segment.
        pure_path = pathlib.PurePosixPath(glob)
        if (
            pure_path.is_absolute()
            or pathlib.PureWindowsPath(glob).is_absolute()
            or ".." in pure_path.parts
            or ".." in pathlib.PureWindowsPath(glob).parts
        ):
            msg = "{glob!r} is an invalid {key} glob: the pattern must match files within the project directory"
            error_collector.config_error(msg, key="project.license-files", glob=glob)
            continue
        files = [f for f in project_dir.glob(glob) if f.is_file()]
        if not files:
            msg = "Every pattern in {key} must match at least one file: {glob!r} did not match any"
            error_collector.config_error(msg, key="project.license-files", glob=glob)
            continue
        for f in files:
            declared = f.relative_to(project_dir)
            if not resolve_links:
                yield declared
                continue
            real_path = _resolve_real_path(f)
            if not real_path.is_relative_to(real_project_dir):
                msg = (
                    "{key} entry {declared!r} resolves outside of the project "
                    "directory: {resolved!r}"
                )
                error_collector.config_error(
                    msg,
                    key="project.license-files",
                    declared=str(declared),
                    resolved=str(real_path),
                )
                continue
            yield LicenseFile(declared, real_path)
