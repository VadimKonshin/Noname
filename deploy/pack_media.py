"""Write the media volume to stdout as a gzipped tar archive."""
import sys
from pathlib import Path

from media_archive import pack

pack(Path('/app/media'), sys.stdout.buffer)
