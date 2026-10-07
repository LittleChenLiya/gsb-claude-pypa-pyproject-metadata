# SPDX-License-Identifier: MIT

from __future__ import annotations

import pathlib

import pytest

import pyproject_metadata
import pyproject_metadata.pyproject


def _make_metadata(
    project_dir: pathlib.Path,
    *,
    resolve_license_files: bool,
    all_errors: bool = False,
) -> pyproject_metadata.StandardMetadata:
    return pyproject_metadata.StandardMetadata.from_pyproject(
        {
            "project": {
                "name": "link-root",
                "version": "1.0.0",
                "license": "MIT",
                "license-files": ["LICENSE*"],
            },
        },
        project_dir,
        resolve_license_files=resolve_license_files,
        all_errors=all_errors,
    )


@pytest.fixture
def can_symlink(tmp_path: pathlib.Path) -> bool:
    """Return True if this platform lets us create a symbolic link."""
    target = tmp_path / "symlink-probe-target"
    target.write_text("probe")
    link = tmp_path / "symlink-probe-link"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    return True


def test_resolved_license_files_plain(tmp_path: pathlib.Path) -> None:
    """A regular file inside the root: declared and real path agree."""
    (tmp_path / "LICENSE.txt").write_text("plain license")

    metadata = _make_metadata((tmp_path), resolve_license_files=True)

    assert metadata.license_files == [pathlib.Path("LICENSE.txt")]
    assert metadata.resolved_license_files == [
        pyproject_metadata.LicenseFile(
            declared=pathlib.Path("LICENSE.txt"),
            resolved=(tmp_path / "LICENSE.txt").resolve(),
        ),
    ]
    assert metadata.resolved_license_files[0].resolved.is_absolute()
    assert "License-File" in str(metadata.as_rfc822())


def test_resolved_license_files_inner_link_real(
    tmp_path: pathlib.Path, can_symlink: bool
) -> None:
    """A symlink whose final target stays inside the root is accepted."""
    if not can_symlink:
        pytest.skip("platform does not allow creating symbolic links")
    licenses = tmp_path / "licenses"
    licenses.mkdir()
    (licenses / "LICENSE.mit").write_text("inner license")
    (tmp_path / "LICENSE").symlink_to(licenses / "LICENSE.mit")

    metadata = _make_metadata(tmp_path, resolve_license_files=True)

    assert metadata.resolved_license_files == [
        pyproject_metadata.LicenseFile(
            declared=pathlib.Path("LICENSE"),
            resolved=(licenses / "LICENSE.mit").resolve(),
        ),
    ]


def test_resolved_license_files_inner_link_fixture(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolve fixture: an inner link is accepted without a real symlink.

    Used on platforms (e.g. Windows without privileges) that cannot create
    symbolic links; the resolver is replaced with a table-driven fake.
    """
    (tmp_path / "LICENSE").write_text("placeholder")
    target = tmp_path / "licenses" / "LICENSE.mit"
    target.parent.mkdir()
    target.write_text("inner target")

    mapping = {(tmp_path / "LICENSE").resolve(): target.resolve()}

    def fake_resolve(path: pathlib.Path) -> pathlib.Path:
        return mapping.get(path.resolve(strict=False), path.resolve(strict=False))

    monkeypatch.setattr(
        pyproject_metadata.pyproject, "_resolve_real_path", fake_resolve
    )

    metadata = _make_metadata(tmp_path, resolve_license_files=True)

    assert metadata.resolved_license_files == [
        pyproject_metadata.LicenseFile(
            declared=pathlib.Path("LICENSE"),
            resolved=target.resolve(),
        ),
    ]


@pytest.mark.parametrize("all_errors", [False, True])
def test_resolved_license_files_escaping_link_real(
    tmp_path: pathlib.Path, can_symlink: bool, all_errors: bool
) -> None:
    """A symlink escaping the root fails and produces no Core Metadata."""
    if not can_symlink:
        pytest.skip("platform does not allow creating symbolic links")
    outside = tmp_path.parent / "outside-license.txt"
    outside.write_text("outside license")
    (tmp_path / "LICENSE").symlink_to(outside)

    expected = (
        "\"project.license-files\" entry 'LICENSE' resolves outside of the "
        "project directory"
    )
    match = (
        r'"project.license-files" entry .LICENSE. resolves outside of the '
        r"project directory"
    )
    if all_errors:
        with pytest.raises(pyproject_metadata.errors.ExceptionGroup) as excinfo:
            _make_metadata(tmp_path, resolve_license_files=True, all_errors=True)
        assert len(excinfo.value.exceptions) == 1
        assert expected in str(excinfo.value.exceptions[0])
    else:
        with pytest.raises(pyproject_metadata.ConfigurationError, match=match):
            _make_metadata(tmp_path, resolve_license_files=True)


@pytest.mark.parametrize("all_errors", [False, True])
def test_resolved_license_files_escaping_link_fixture(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, all_errors: bool
) -> None:
    """Resolve fixture: an escaping link fails and yields zero metadata.

    Runs on every platform, including those without symlink privileges.
    """
    (tmp_path / "LICENSE").write_text("placeholder")
    outside = tmp_path.parent / "outside-license.txt"
    outside.write_text("outside license")

    link = (tmp_path / "LICENSE").resolve()
    mapping = {link: outside.resolve()}

    def fake_resolve(path: pathlib.Path) -> pathlib.Path:
        return mapping.get(path.resolve(strict=False), path.resolve(strict=False))

    monkeypatch.setattr(
        pyproject_metadata.pyproject, "_resolve_real_path", fake_resolve
    )

    expected = (
        "\"project.license-files\" entry 'LICENSE' resolves outside of the "
        "project directory"
    )
    match = (
        r'"project.license-files" entry .LICENSE. resolves outside of the '
        r"project directory"
    )
    if all_errors:
        with pytest.raises(pyproject_metadata.errors.ExceptionGroup) as excinfo:
            _make_metadata(tmp_path, resolve_license_files=True, all_errors=True)
        assert len(excinfo.value.exceptions) == 1
        assert expected in str(excinfo.value.exceptions[0])
        # No partial Core Metadata: from_pyproject never returns.
        assert "License-File" not in str(excinfo.value)
    else:
        with pytest.raises(pyproject_metadata.ConfigurationError, match=match):
            _make_metadata(tmp_path, resolve_license_files=True)


def test_unresolved_license_files_escape_allowed_by_default(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default policy does not follow links: an escaping link is accepted."""
    (tmp_path / "LICENSE").write_text("placeholder")
    outside = tmp_path.parent / "outside-license.txt"
    outside.write_text("outside license")

    link = (tmp_path / "LICENSE").resolve()
    mapping = {link: outside.resolve()}

    def fake_resolve(path: pathlib.Path) -> pathlib.Path:
        return mapping.get(path.resolve(strict=False), path.resolve(strict=False))

    monkeypatch.setattr(
        pyproject_metadata.pyproject, "_resolve_real_path", fake_resolve
    )

    metadata = _make_metadata(tmp_path, resolve_license_files=False)

    assert metadata.license_files == [pathlib.Path("LICENSE")]
    assert metadata.resolved_license_files is None


def test_resolved_license_files_does_not_read_contents(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real-path check never reads license file contents."""
    (tmp_path / "LICENSE.txt").write_text("license contents")
    read_paths: list[pathlib.Path] = []

    def boom(self: pathlib.Path, *_args: object, **_kwargs: object) -> object:
        read_paths.append(self)
        msg = "license file contents must not be read"
        raise AssertionError(msg)

    monkeypatch.setattr(pathlib.Path, "read_text", boom)

    metadata = _make_metadata(tmp_path, resolve_license_files=True)
    # Writing Core Metadata does not embed license-file contents either.
    str(metadata.as_rfc822())
    assert read_paths == []


def test_resolved_license_files_default_interface(
    tmp_path: pathlib.Path,
) -> None:
    """The original interface keeps working unchanged when the option is off."""
    (tmp_path / "LICENSE.txt").write_text("plain license")

    metadata = pyproject_metadata.StandardMetadata.from_pyproject(
        {
            "project": {
                "name": "link-root",
                "version": "1.0.0",
                "license": "MIT",
                "license-files": ["LICENSE*"],
            },
        },
        tmp_path,
    )

    assert metadata.license_files == [pathlib.Path("LICENSE.txt")]
    assert metadata.resolved_license_files is None
