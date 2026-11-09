"""Blob storage port and a local-folder adapter."""

from pathlib import Path, PurePosixPath
from typing import Protocol


class BlobStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...


def check_key(key: str) -> str:
    """Reject keys that are empty, absolute or climb out of the root."""
    path = PurePosixPath(key)
    if not key or key.startswith("/") or ".." in path.parts or "\\" in key:
        raise ValueError(f"unsafe blob key: {key!r}")
    return key


class LocalBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / check_key(key)).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"unsafe blob key: {key!r}")
        return path

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()
