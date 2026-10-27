"""tools.gbs: scene spec -> GB Studio 4.3.2 project -> ROM -> PyBoy screenshots.

    from tools.gbs import make_project, build_rom, build_web, play, validate

See docs/gbstudio-4.3.2-format.md.
"""
from .build import BuildError, build_rom, build_web
from .play import GBRunner, play
from .project import SpecError, make_project
from .validate import count_unique_tiles, validate

__all__ = [
    "make_project",
    "build_rom",
    "build_web",
    "play",
    "validate",
    "count_unique_tiles",
    "GBRunner",
    "BuildError",
    "SpecError",
]
