"""The CLI the Desktop app carries for its Agent Host, as the script stages it.

Building one needs a standalone CPython and the CLI's wheels, which is not a
thing to do on every PR; `make desktop-dmg` builds one and runs it from inside
the app. What is decided without building is here: what is pruned, and which
files are code that has to be signed before the app around it is.
"""

from __future__ import annotations

from pathlib import Path

from scripts.build_desktop_lemma_cli import macho_files, prune_for_cli
from scripts.build_local_host_pack import LEMMA_CLI_LAUNCHER


def _touch(path: Path, content: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_every_mach_o_is_found_for_signing_and_nothing_else(tmp_path: Path) -> None:
    thin = _touch(tmp_path / "python/bin/python3.14", bytes.fromhex("cffaedfe") + b"rest")
    universal = _touch(
        tmp_path / "cli/pydantic_core/_pydantic_core.so", bytes.fromhex("cafebabe")
    )
    _touch(tmp_path / "bin/lemma", LEMMA_CLI_LAUNCHER.encode())
    _touch(tmp_path / "cli/lemma_cli/app.py", b"print('hi')\n")
    (tmp_path / "python/bin/python3").symlink_to("python3.14")

    # The symlink is the same file, signed once through its target.
    assert macho_files(tmp_path) == sorted([thin, universal])


def test_what_no_lemma_command_runs_is_pruned(tmp_path: Path) -> None:
    python = tmp_path / "python"
    kept = [
        _touch(python / "bin/python3.14"),
        _touch(python / "lib/libpython3.14.dylib"),
        _touch(python / "lib/python3.14/json/__init__.py"),
        _touch(python / "lib/python3.14/lib-dynload/_dbm.cpython-314-darwin.so"),
    ]
    pruned = [
        _touch(python / "bin/pip3"),
        _touch(python / "bin/idle3"),
        _touch(python / "bin/python3.14-config"),
        _touch(python / "lib/python3.14/site-packages/pip/__init__.py"),
        _touch(python / "lib/python3.14/tkinter/__init__.py"),
        _touch(python / "lib/python3.14/test/test_x.py"),
        _touch(python / "lib/python3.14/config-3.14-darwin/libpython3.14.a"),
        _touch(python / "lib/python3.14/lib-dynload/_tkinter.cpython-314-darwin.so"),
        _touch(python / "lib/tcl8.6/init.tcl"),
        _touch(python / "lib/libtk8.6.dylib"),
    ]

    prune_for_cli(python)

    assert all(path.exists() for path in kept)
    assert not [path for path in pruned if path.exists()]
