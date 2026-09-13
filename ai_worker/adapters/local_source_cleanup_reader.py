"""Local 내용 주소 객체의 bytes를 읽어 조사합니다. 생성 시각·Source 소유를 추정하지 않습니다."""

import hashlib
import os
import re
import stat
from pathlib import Path

from ai_worker.tasks.rag.source_cleanup.survey import Inventory, ObjectObservation

_FLAGS = os.O_RDONLY | os.O_NOFOLLOW


class LocalSourceCleanupReader:
    def __init__(self, root: Path) -> None:
        self._root = root

    def read_inventory(self) -> Inventory:
        # Open every directory relative to an already-open descriptor: no symlink traversal.
        if not self._root.is_absolute() or ".." in self._root.parts:
            raise ValueError("Invalid inventory root")
        fd = os.open("/", _FLAGS | os.O_DIRECTORY)
        try:
            for part in self._root.parts[1:]:
                child = os.open(part, _FLAGS | os.O_DIRECTORY, dir_fd=fd)
                os.close(fd)
                fd = child
            root_stat = os.fstat(fd)
            if root_stat.st_uid != os.geteuid() or stat.S_IMODE(root_stat.st_mode) != 0o700:
                raise ValueError("Invalid inventory root permissions")
            objects: list[ObjectObservation] = []
            complete = self._walk(fd, (), objects)
            return Inventory(str(self._root), tuple(objects), complete)
        finally:
            os.close(fd)

    def _walk(self, directory: int, parts: tuple[str, ...], objects: list[ObjectObservation]) -> bool:
        complete = True
        for name in sorted(os.listdir(directory)):
            path = (*parts, name)
            metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                allowed = (not parts and name == "sha256") or (
                    parts == ("sha256",) and re.fullmatch(r"[0-9a-f]{2}", name)
                )
                if not allowed:
                    complete = False
                    continue
                child = os.open(name, _FLAGS | os.O_DIRECTORY, dir_fd=directory)
                try:
                    complete = self._walk(child, path, objects) and complete
                finally:
                    os.close(child)
                continue
            key = "/".join(path)
            match = re.fullmatch(r"sha256/([0-9a-f]{2})/([0-9a-f]{64})\.artifact", key)
            if not stat.S_ISREG(metadata.st_mode) or not match or match[1] != match[2][:2]:
                complete = False
                continue
            descriptor = os.open(name, _FLAGS | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode):
                    complete = False
                    continue
                digest = hashlib.sha256()
                size = 0
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                after = os.fstat(stream.fileno())

            def fingerprint(s: os.stat_result) -> tuple[int, int, int, int, int]:
                return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (
                fingerprint(metadata) != fingerprint(before)
                or fingerprint(before) != fingerprint(after)
                or fingerprint(after) != fingerprint(current)
                or digest.hexdigest() != match[2]
            ):
                complete = False
                continue
            objects.append(ObjectObservation(key, digest.hexdigest(), size))
        return complete
