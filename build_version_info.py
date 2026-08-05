"""Generate the Windows version resource consumed by PyInstaller."""

from __future__ import annotations

import argparse
from pathlib import Path

from app_metadata import APP_NAME, APP_VERSION, EXECUTABLE_NAME, GITHUB_OWNER


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"APP_VERSION must be MAJOR.MINOR.PATCH, got {version!r}")
    return (*[int(part) for part in parts], 0)


def render_version_info() -> str:
    version = _version_tuple(APP_VERSION)
    version_text = APP_VERSION
    return f'''# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version},
    prodvers={version},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', '{GITHUB_OWNER}'),
        StringStruct('FileDescription', '{APP_NAME}'),
        StringStruct('FileVersion', '{version_text}'),
        StringStruct('InternalName', '{EXECUTABLE_NAME.removesuffix('.exe')}'),
        StringStruct('OriginalFilename', '{EXECUTABLE_NAME}'),
        StringStruct('ProductName', '{APP_NAME}'),
        StringStruct('ProductVersion', '{version_text}'),
        StringStruct('LegalCopyright', 'Copyright (c) 2026 {GITHUB_OWNER}')]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_version_info(), encoding="utf-8")
    print(f"Generated {args.output} for version {APP_VERSION}")


if __name__ == "__main__":
    main()
