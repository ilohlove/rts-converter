"""Application and GitHub release metadata."""

APP_NAME = "RTZ Converter"
APP_VERSION = "1.0.0"
GITHUB_OWNER = "ilohlove"
GITHUB_REPOSITORY = "rtz-converter"
EXECUTABLE_NAME = "RTZ-to-CSV.exe"
CHECKSUM_ASSET_NAME = f"{EXECUTABLE_NAME}.sha256"
GITHUB_API_VERSION = "2026-03-10"
RELEASES_URL = (
    f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPOSITORY}/releases"
)
LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPOSITORY}/releases/latest"
)

__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "CHECKSUM_ASSET_NAME",
    "EXECUTABLE_NAME",
    "GITHUB_API_VERSION",
    "GITHUB_OWNER",
    "GITHUB_REPOSITORY",
    "LATEST_RELEASE_API",
    "RELEASES_URL",
]
