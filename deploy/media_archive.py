"""Pack and unpack product photos without leaving the media directory."""
import hashlib
import os
import tarfile
from pathlib import Path


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def member_destination(root, name):
    parts = Path(name).parts
    if not parts or any(part in {'', '.', '..'} for part in parts):
        raise ValueError(name)
    base = Path(root).resolve()
    dest = (base.joinpath(*parts)).resolve()
    if os.path.commonpath([str(base), str(dest)]) != str(base):
        raise ValueError(name)
    return dest


def pack(root, fileobj):
    count = 0
    root = Path(root)
    with tarfile.open(fileobj=fileobj, mode='w|gz') as archive:
        if root.is_dir():
            for path in sorted(root.rglob('*')):
                if path.is_symlink() or not path.is_file() or path.name.startswith('.'):
                    continue
                archive.add(path, arcname=path.relative_to(root).as_posix())
                count += 1
    return count


def unpack(fileobj, root):
    count = 0
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=fileobj, mode='r|gz') as archive:
        for member in archive:
            if not member.isfile():
                continue
            dest = member_destination(root, member.name)
            source = archive.extractfile(member)
            if source is None:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open('wb') as handle:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    handle.write(chunk)
            os.chmod(dest, 0o644)
            count += 1
    return count
