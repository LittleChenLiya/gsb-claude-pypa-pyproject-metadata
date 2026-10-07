# SPDX-License-Identifier: MIT

"""
Tests for the opt-in ``resolve_license_files`` root-boundary check.

The three required cases are covered:

1. a regular file inside the temporary root;
2. a symlink whose final target stays inside the root;
3. a symlink (or, when the platform lacks permission to create symlinks,
   a resolver fixture) whose final target escapes the root.

On platforms where symlink creation is not permitted, the boundary logic
is exercised by monkeypatching ``pyproject_metadata.pyproject._resolve_path``
with an in-memory resolver fixture.
"""

from __future__ import annotations

import pathlib
import typing

import pytest

import pyproject_metadata
import pyproject_metadata.pyproject as pyproject_mod

if typing.TYPE_CHECKING:
    from collections.abc import Callable


pytestmark = pytest.mark.usefixtures("no_file_reads")


PROJECT = {
    "project": {
        "name": "example",
        "version": "1.2.3",
        "license": "MIT",
        "license-files": ["LICENSE*"],
    }
}


def _make_tree(
    tmp_path: pathlib.Path, *, escaped: bool
) -> tuple[pathlib.Path, pathlib.Path]:
    """Create a temporary project root and a target file.

    Returns ``(project_dir, target)``.
    """
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)
    if escaped:
        target = tmp_path / "outside" / "LICENSE.outside"
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        target = project_dir / "licenses" / "LICENSE.real"
        target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("license text, never read\n", encoding="utf-8")
    return project_dir, target


def _make_symlink(link: pathlib.Path, target: pathlib.Path) -> bool:
    """Try to create ``link`` as a symlink. Return False if not permitted."""
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    return True


def _parse(
    project_dir: pathlib.Path, **kwargs: object
) -> pyproject_metadata.StandardMetadata:
    return pyproject_metadata.StandardMetadata.from_pyproject(
        PROJECT,
        project_dir,
        **kwargs,  # type: ignore[arg-type]
    )


def _resolve_fixture(
    real_map: dict[pathlib.Path, pathlib.Path],
) -> Callable[[pathlib.Path], pathlib.Path]:
    """Build an in-memory resolver fixture for platforms without symlinks.

    Paths in ``real_map`` are mapped to a fake final target; every other
    path is resolved with the standard ``Path.resolve``.
    """

    def fake_resolve(self: pathlib.Path) -> pathlib.Path:
        if self in real_map:
            return real_map[self]
        return pathlib.Path.resolve(self)

    return fake_resolve


@pytest.fixture
def no_file_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure the boundary check never reads license file contents."""

    def forbidden_read_text(self: pathlib.Path, *args: object, **kwargs: object) -> str:
        if self.name.startswith("LICENSE"):
            pytest.fail(f"license file contents were read: {self}")
        return pathlib.Path.read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    def forbidden_read_bytes(self: pathlib.Path) -> bytes:
        if self.name.startswith("LICENSE"):
            pytest.fail(f"license file contents were read: {self}")
        return pathlib.Path.read_bytes(self)

    monkeypatch.setattr(pathlib.Path, "read_text", forbidden_read_text)
    monkeypatch.setattr(pathlib.Path, "read_bytes", forbidden_read_bytes)


def test_option_off_keeps_original_interface(
    tmp_path: pathlib.Path,
) -> None:
    """Without the option, everything works exactly as before."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    plain = project_dir / "LICENSE.txt"
    plain.write_text("MIT\n", encoding="utf-8")

    metadata = _parse(project_dir)

    assert metadata.license_file_resolutions is None
    assert metadata.license_files == [pathlib.Path("LICENSE.txt")]

    # Even an escaping link is accepted when the option is off:
    # the default non-following-compatible policy is preserved.
    project_dir2, target2 = _make_tree(tmp_path / "b", escaped=True)
    link2 = project_dir2 / "LICENSE.escape"
    if _make_symlink(link2, target2):
        metadata2 = _parse(project_dir2)
        assert metadata2.license_files == [pathlib.Path("LICENSE.escape")]
        assert metadata2.license_file_resolutions is None


def test_regular_file_inside_root(
    tmp_path: pathlib.Path,
) -> None:
    """Case 1: a plain file inside the temporary project root."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    license_file = project_dir / "LICENSE.txt"
    license_file.write_text("MIT\n", encoding="utf-8")

    metadata = _parse(project_dir, resolve_license_files=True)

    assert metadata.license_files == [pathlib.Path("LICENSE.txt")]
    assert metadata.license_file_resolutions == [
        pyproject_metadata.ResolvedLicenseFile(
            declared=pathlib.Path("LICENSE.txt"),
            resolved=license_file.resolve(),
        )
    ]
    # the Core Metadata is produced and references the declared name
    message = str(metadata.as_rfc822())
    assert "License-File: LICENSE.txt" in message


def test_inroot_link(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Case 2: a symlink whose final target stays inside the root."""
    project_dir, target = _make_tree(tmp_path, escaped=False)
    link = project_dir / "LICENSE.link"
    symlinks_ok = _make_symlink(link, target)
    if not symlinks_ok:
        # Resolver fixture: emulate the link's final target without symlinks.
        link.write_text("stand-in\n", encoding="utf-8")
        monkeypatch.setattr(
            pyproject_mod,
            "_resolve_path",
            lambda p: target.resolve() if p == link.resolve() else p.resolve(),
        )

    metadata = _parse(project_dir, resolve_license_files=True)

    assert metadata.license_files == [pathlib.Path("LICENSE.link")]
    resolutions = metadata.license_file_resolutions
    assert resolutions is not None
    assert len(resolutions) == 1
    assert resolutions[0].declared == pathlib.Path("LICENSE.link")
    assert resolutions[0].resolved == target.resolve()
    assert resolutions[0].resolved.is_relative_to(project_dir.resolve())
    message = str(metadata.as_rfc822())
    assert "License-File: LICENSE.link" in message


def test_escaping_link_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case 3: a symlink/fixture whose final target escapes the root.

    Parsing fails (no Core Metadata is produced); the error reports both the
    declared and actual paths.
    """
    project_dir, target = _make_tree(tmp_path, escaped=True)
    link = project_dir / "LICENSE.escape"
    symlinks_ok = _make_symlink(link, target)
    if not symlinks_ok:
        link.write_text("stand-in\n", encoding="utf-8")
        monkeypatch.setattr(
            pyproject_mod,
            "_resolve_path",
            lambda p: target.resolve() if p == link.resolve() else p.resolve(),
        )

    metadata: pyproject_metadata.StandardMetadata | None = None
    with pytest.raises(pyproject_metadata.ConfigurationError) as excinfo:
        metadata = _parse(project_dir, resolve_license_files=True)

    # No metadata object (and therefore no partial Core Metadata) is produced
    assert metadata is None
    message = str(excinfo.value)
    assert '"project.license-files"' in message
    assert "LICENSE.escape" in message  # declared path
    # actual resolved path (Windows paths get backslashes escaped by repr)
    assert repr(str(target.resolve())) in message
    assert "outside the project directory" in message


def test_escaping_link_fails_all_errors(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With all_errors, the boundary failure is collected; still no metadata."""
    project_dir, target = _make_tree(tmp_path, escaped=True)
    link = project_dir / "LICENSE.escape"
    if not _make_symlink(link, target):
        link.write_text("stand-in\n", encoding="utf-8")
        monkeypatch.setattr(
            pyproject_mod,
            "_resolve_path",
            lambda p: target.resolve() if p == link.resolve() else p.resolve(),
        )

    metadata: pyproject_metadata.StandardMetadata | None = None
    with pytest.raises(pyproject_metadata.errors.ExceptionGroup) as excinfo:
        metadata = _parse(project_dir, resolve_license_files=True, all_errors=True)

    assert metadata is None
    messages = [str(e) for e in excinfo.value.exceptions]
    assert any("outside the project directory" in m for m in messages)
    assert any("LICENSE.escape" in m for m in messages)


def test_resolver_fixture_mapping(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the resolver fixture seam directly (in-memory, no symlinks)."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    link = project_dir / "LICENSE.link"
    link.write_text("stand-in\n", encoding="utf-8")
    inside_target = project_dir / "real"
    inside_target.mkdir()
    resolved_inside = inside_target / "LICENSE.target"

    fake = _resolve_fixture({link.resolve(): resolved_inside.resolve()})
    monkeypatch.setattr(pyproject_mod, "_resolve_path", fake)

    metadata = _parse(project_dir, resolve_license_files=True)
    assert metadata.license_file_resolutions is not None
    assert metadata.license_file_resolutions[0].resolved == resolved_inside.resolve()


def test_mixed_good_and_escaping_match_yields_no_metadata(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One good file plus one escaping match: still no metadata is produced."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    good = project_dir / "LICENSE.good"
    good.write_text("MIT\n", encoding="utf-8")

    target = tmp_path / "outside" / "LICENSE.outside"
    target.parent.mkdir()
    target.write_text("leaked\n", encoding="utf-8")
    link = project_dir / "LICENSE.bad"
    if not _make_symlink(link, target):
        link.write_text("stand-in\n", encoding="utf-8")
        monkeypatch.setattr(
            pyproject_mod,
            "_resolve_path",
            lambda p: target.resolve() if p == link.resolve() else p.resolve(),
        )

    metadata: pyproject_metadata.StandardMetadata | None = None
    with pytest.raises(pyproject_metadata.ConfigurationError, match="outside"):
        metadata = _parse(project_dir, resolve_license_files=True)
    # No metadata object (and therefore no partial Core Metadata) is produced,
    # even though a good, root-contained match was present alongside the bad one.
    assert metadata is None


def test_get_license_files_default_interface(tmp_path: pathlib.Path) -> None:
    """The internal helper keeps its original shape when resolve is off."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "LICENSE.txt").write_text("MIT\n", encoding="utf-8")

    collector = pyproject_metadata.errors.ErrorCollector(collect_errors=False)
    result = pyproject_mod.get_license_files(
        {"license-files": ["LICENSE*"]},  # type: ignore[arg-type]
        project_dir,
        collector,
    )
    assert result is not None
    declared, resolved = result
    assert declared == [pathlib.Path("LICENSE.txt")]
    assert resolved is None
