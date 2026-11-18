"""Unpack a gzipped tar archive from stdin into the media volume."""
import sys
from pathlib import Path

from media_archive import unpack

unpack(sys.stdin.buffer, Path('/app/media'))
