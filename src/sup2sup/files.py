"""File writes shared by the CLI and desktop app."""

import os
from pathlib import Path
import tempfile


def same_path(first: str | Path, second: str | Path) -> bool:
    a, b = Path(first).resolve(), Path(second).resolve()
    return a == b or a.exists() and b.exists() and a.samefile(b)


def write_bytes(path: str | Path, data: bytes, *, overwrite: bool = False) -> None:
    target = Path(path)
    # Write completely before publishing. A same-directory temporary also avoids cross-drive moves.
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, target)
        else:
            # Unlike rename on POSIX, link never replaces an existing destination.
            os.link(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
