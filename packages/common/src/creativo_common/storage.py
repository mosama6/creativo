from pathlib import Path


class StorageError(Exception):
    pass


class FileStorage:
    """Local object store. Keys are relative paths, never URLs."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        if not key or key.startswith(("/", "\\")) or ".." in Path(key).parts:
            raise StorageError("invalid_storage_key")
        path = (self.root / key).resolve()
        if path != self.root and self.root not in path.parents:
            raise StorageError("invalid_storage_key")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        path = self.path_for(key)
        if not path.is_file():
            raise StorageError("missing_object")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        try:
            return self.path_for(key).is_file()
        except StorageError:
            return False
