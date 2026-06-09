import os
import sys


def app_dir() -> str:
    """Return the writable application directory.

    In a PyInstaller build this is the directory that contains the exe.
    In source runs this is the repository root.
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def runtime_path(*parts: str) -> str:
    return os.path.join(app_dir(), *parts)


def ensure_runtime_dirs() -> None:
    for name in ('cookies', 'downloads', 'logs'):
        os.makedirs(runtime_path(name), exist_ok=True)
