"""Private local artifacts, independent of the operator's POSIX umask.

These checks do not defend against a hostile OS administrator, implement Windows
DACLs, encrypt files, or revoke copies that have already been exported.
"""
import os
from pathlib import Path
import stat
import tempfile


def private_directory(path):
    """Create a new application directory privately; leave existing parents alone."""
    path = Path(path)
    missing = []
    parent = path
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    if not parent.is_dir():
        raise NotADirectoryError('private artifact parent must be a directory')
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            if not directory.is_dir():
                raise
        else:
            _chmod_private(directory, 0o700)
    return path


def _check_regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('private artifact must be a single-link regular file')


def _owner_only(fd):
    if os.name == 'posix':
        os.fchmod(fd, 0o600)


def _chmod_private(path, mode):
    if os.name == 'posix':
        options = {'follow_symlinks': False} if os.chmod in os.supports_follow_symlinks else {}
        os.chmod(path, mode, **options)


def _open_private(path, flags):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('private artifact cannot be a symbolic link')
    flags |= getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags, 0o600)
    try:
        _check_regular(os.fstat(fd))
        _owner_only(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def private_file(path):
    """Tighten existing files without opening them, preserving SQLite's locks.

    Closing any descriptor for a live SQLite SHM inode can drop this process's
    POSIX locks. Existing files therefore use metadata-only checks and chmod.
    """
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        try:
            fd = _open_private(path, os.O_RDWR | os.O_CREAT | os.O_EXCL)
        except FileExistsError:
            info = path.lstat()  # Another process created it; do not reopen it.
        else:
            os.close(fd)
            return path
    _check_regular(info)
    _chmod_private(path, 0o600)
    return path


def append_private_text(path, text):
    fd = _open_private(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)


def write_private_text(path, text, *, exclusive=False):
    """Write a private export atomically, or create a new config without clobbering."""
    path = Path(path)
    if exclusive:
        fd = _open_private(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
        return
    try:
        info = path.lstat()
    except FileNotFoundError:
        pass
    else:
        _check_regular(info)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            _owner_only(stream.fileno())
            stream.write(text)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
