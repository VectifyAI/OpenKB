"""OpenKB package."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    __version__ = _version("openkb")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
