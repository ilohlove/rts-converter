"""Secure, public GitHub Releases updater for the frozen Windows app."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app_metadata import (
    APP_NAME,
    APP_VERSION,
    EXECUTABLE_NAME,
    GITHUB_API_VERSION,
    LEGACY_EXECUTABLE_NAME,
    LATEST_RELEASE_API,
    RELEASE_EXECUTABLE_NAMES,
    RELEASES_URL,
    UPDATE_CACHE_DIRECTORY_NAME,
)


MAX_RELEASE_JSON_BYTES = 1024 * 1024
MAX_CHECKSUM_BYTES = 4096
MAX_EXECUTABLE_BYTES = 100 * 1024 * 1024
API_TIMEOUT_SECONDS = 12
DOWNLOAD_TIMEOUT_SECONDS = 60
UPDATE_WAIT_SECONDS = 60
_VERSION_PATTERN = re.compile(r"^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class UpdateError(RuntimeError):
    """Expected update failures that can be shown to the user."""


VersionTuple = tuple[int, int, int]
ProgressCallback = Callable[[int, int | None], None]


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    version: VersionTuple
    html_url: str
    body: str
    immutable: bool
    executable_asset_url: str
    checksum_asset_url: str
    api_digest: str
    executable_name: str = EXECUTABLE_NAME

    @property
    def version_text(self) -> str:
        return ".".join(str(part) for part in self.version)


@dataclass(frozen=True)
class DownloadedUpdate:
    release: ReleaseInfo
    staged_path: Path
    sha256: str


def parse_version(value: str) -> VersionTuple:
    match = _VERSION_PATTERN.fullmatch(value.strip()) if isinstance(value, str) else None
    if not match:
        raise UpdateError(
            f"Phiên bản không hợp lệ: {value!r} / Invalid version: {value!r}."
        )
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def version_to_tag(version: VersionTuple) -> str:
    return "v" + ".".join(str(part) for part in version)


def check_for_update(
    current_version: str = APP_VERSION,
    *,
    api_url: str = LATEST_RELEASE_API,
) -> ReleaseInfo | None:
    """Return the latest immutable stable release when it is newer."""

    current = parse_version(current_version)
    payload = _request_json(api_url)
    if not isinstance(payload, dict):
        raise UpdateError("Phản hồi GitHub không hợp lệ / Invalid GitHub response.")

    if payload.get("draft") is True or payload.get("prerelease") is True:
        return None
    if payload.get("immutable") is not True:
        raise UpdateError(
            "GitHub Release chưa bất biến / GitHub Release is not immutable yet."
        )

    tag_name = payload.get("tag_name")
    if not isinstance(tag_name, str):
        raise UpdateError("Release thiếu tag / Release is missing tag_name.")
    version = parse_version(tag_name)
    if version <= current:
        return None

    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("Release thiếu assets / Release assets are missing.")
    supported_asset_names = {
        name
        for executable_name in RELEASE_EXECUTABLE_NAMES
        for name in (executable_name, f"{executable_name}.sha256")
    }
    matching: dict[str, dict] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if name in supported_asset_names:
            if name in matching:
                raise UpdateError(f"Asset bị trùng / Duplicate asset: {name}.")
            if asset.get("state") != "uploaded":
                raise UpdateError(f"Asset chưa upload xong / Asset is not uploaded: {name}.")
            matching[name] = asset

    executable_name = next(
        (
            name
            for name in RELEASE_EXECUTABLE_NAMES
            if name in matching and f"{name}.sha256" in matching
        ),
        None,
    )
    if executable_name is None:
        raise UpdateError(
            f"Release phải có một cặp EXE/checksum được hỗ trợ / "
            f"Release must contain a supported executable/checksum pair: "
            f"{', '.join(RELEASE_EXECUTABLE_NAMES)}."
        )

    checksum_name = f"{executable_name}.sha256"
    executable_url = _asset_api_url(matching[executable_name], executable_name)
    checksum_url = _asset_api_url(matching[checksum_name], checksum_name)
    api_digest = _normalise_digest(matching[executable_name].get("digest"))
    html_url = payload.get("html_url")
    if not isinstance(html_url, str) or not html_url.startswith("https://"):
        html_url = RELEASES_URL

    body = payload.get("body")
    return ReleaseInfo(
        tag_name=tag_name,
        version=version,
        html_url=html_url,
        body=body if isinstance(body, str) else "",
        immutable=True,
        executable_asset_url=executable_url,
        checksum_asset_url=checksum_url,
        api_digest=api_digest,
        executable_name=executable_name,
    )


def download_update(
    release: ReleaseInfo,
    *,
    progress: ProgressCallback | None = None,
    root_dir: Path | None = None,
) -> DownloadedUpdate:
    """Download and verify the exact executable referenced by a release."""

    checksum_bytes = _download_bytes(
        release.checksum_asset_url,
        max_bytes=MAX_CHECKSUM_BYTES,
        accept="application/octet-stream",
    )
    manifest_digest = _parse_checksum_manifest(
        checksum_bytes,
        executable_name=release.executable_name,
    )
    if manifest_digest != release.api_digest:
        raise UpdateError(
            "Checksum manifest không khớp digest GitHub / "
            "Checksum manifest does not match GitHub's asset digest."
        )

    base = root_dir or _default_update_root()
    stage_dir = base / release.tag_name
    try:
        stage_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UpdateError(
            f"Không thể tạo thư mục stage / Could not create update staging directory: {exc}"
        ) from exc
    part_path = stage_dir / f".{EXECUTABLE_NAME}.part"
    staged_path = stage_dir / EXECUTABLE_NAME
    part_path.unlink(missing_ok=True)

    digest = hashlib.sha256()
    total = 0
    try:
        request = _request(
            release.executable_asset_url,
            accept="application/octet-stream",
        )
        with urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            _ensure_https(response.geturl())
            header = response.headers.get("Content-Length")
            expected_size = int(header) if header and header.isdigit() else None
            if expected_size is not None and expected_size > MAX_EXECUTABLE_BYTES:
                raise UpdateError("EXE vượt giới hạn kích thước / EXE exceeds size limit.")
            with part_path.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_EXECUTABLE_BYTES:
                        raise UpdateError("EXE vượt giới hạn kích thước / EXE exceeds size limit.")
                    digest.update(chunk)
                    handle.write(chunk)
                    if progress:
                        if progress(total, expected_size) is False:
                            raise UpdateError(
                                "Đã hủy tải cập nhật / Update download cancelled."
                            )
        actual_digest = digest.hexdigest()
        if actual_digest != manifest_digest or actual_digest != release.api_digest:
            raise UpdateError(
                "SHA-256 EXE không khớp / Downloaded EXE SHA-256 does not match."
            )
        os.replace(part_path, staged_path)
        return DownloadedUpdate(release, staged_path, actual_digest)
    except HTTPError as exc:
        raise UpdateError(f"GitHub tải asset lỗi {exc.code} / GitHub asset download failed: {exc.code}.") from exc
    except (OSError, URLError, ValueError) as exc:
        raise UpdateError(f"Không thể tải bản cập nhật / Could not download update: {exc}") from exc
    finally:
        part_path.unlink(missing_ok=True)


def launch_update(downloaded: DownloadedUpdate) -> None:
    """Launch the staged executable as an independent replacement helper."""

    if not getattr(sys, "frozen", False):
        raise UpdateError(
            "Không thể tự thay khi chạy source / Self-update is only available in a frozen EXE."
        )

    target = Path(sys.executable).resolve()
    if not target.is_file():
        raise UpdateError("Không tìm thấy EXE hiện tại / Current EXE was not found.")
    if not _directory_can_write(target.parent):
        raise UpdateError(
            "Thư mục EXE không cho ghi / The EXE directory is not writable."
        )

    command = [
        str(downloaded.staged_path),
        "--apply-update",
        "--target",
        str(target),
        "--pid",
        str(os.getpid()),
        "--expected-sha256",
        downloaded.sha256,
    ]
    environment = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            command,
            cwd=str(downloaded.staged_path.parent),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
    except OSError as exc:
        raise UpdateError(f"Không thể khởi chạy updater / Could not launch updater: {exc}") from exc


def apply_update_from_argv(argv: Iterable[str] | None = None) -> bool:
    """Handle the private helper mode before Tkinter starts."""

    args = list(sys.argv[1:] if argv is None else argv)
    if "--apply-update" not in args:
        return False
    try:
        target = Path(_argument(args, "--target")).resolve()
        pid = int(_argument(args, "--pid"))
        expected = _normalise_digest(_argument(args, "--expected-sha256"))
        _apply_update(target, pid, expected)
    except (UpdateError, ValueError, OSError) as exc:
        _show_native_error(str(exc))
        return True
    return True


def open_release_page() -> None:
    webbrowser.open(RELEASES_URL)


def _apply_update(target: Path, pid: int, expected_digest: str) -> None:
    staged = Path(sys.executable).resolve()
    is_legacy_target = target.name.casefold() == LEGACY_EXECUTABLE_NAME.casefold()
    destination = target.with_name(EXECUTABLE_NAME) if is_legacy_target else target
    if staged == target or staged == destination:
        raise UpdateError(
            "Bản stage trùng EXE đích / Staged EXE equals the install target."
        )
    if _sha256_file(staged) != expected_digest:
        raise UpdateError("SHA-256 bản stage không khớp / Staged EXE SHA-256 does not match.")
    if not _wait_for_process(pid, UPDATE_WAIT_SECONDS):
        raise UpdateError("EXE cũ chưa thoát / The old EXE did not exit in time.")

    destination_matches = False
    if destination != target and destination.exists():
        if not destination.is_file() or _sha256_file(destination) != expected_digest:
            raise UpdateError(
                f"{destination.name} đã tồn tại với nội dung khác / "
                f"{destination.name} already exists with different content."
            )
        destination_matches = True

    candidate = destination.with_name(destination.name + ".new")
    backup = target.with_name(target.name + ".old")
    candidate.unlink(missing_ok=True)
    try:
        if not destination_matches:
            shutil.copy2(staged, candidate)
            if _sha256_file(candidate) != expected_digest:
                raise UpdateError(
                    "SHA-256 bản copy không khớp / "
                    "Replacement copy hash does not match."
                )
        _replace_with_retry(target, backup, UPDATE_WAIT_SECONDS)
        try:
            if not destination_matches:
                if destination == target:
                    _replace_with_retry(candidate, destination, UPDATE_WAIT_SECONDS)
                else:
                    _move_without_overwrite_with_retry(
                        candidate,
                        destination,
                        expected_digest,
                        UPDATE_WAIT_SECONDS,
                    )
        except Exception:
            if backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
    finally:
        candidate.unlink(missing_ok=True)

    environment = {**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [str(destination)],
        cwd=str(destination.parent),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )


def _request(url: str, *, accept: str = "application/vnd.github+json") -> Request:
    _ensure_https(url)
    return Request(
        url,
        headers={
            "Accept": accept,
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": f"{APP_NAME}/{APP_VERSION}",
        },
    )


def _request_json(url: str) -> object:
    try:
        with urlopen(_request(url), timeout=API_TIMEOUT_SECONDS) as response:
            _ensure_https(response.geturl())
            data = response.read(MAX_RELEASE_JSON_BYTES + 1)
            if len(data) > MAX_RELEASE_JSON_BYTES:
                raise UpdateError("Phản hồi GitHub quá lớn / GitHub response is too large.")
            return json.loads(data.decode("utf-8"))
    except HTTPError as exc:
        raise UpdateError(f"GitHub API lỗi {exc.code} / GitHub API failed: {exc.code}.") from exc
    except (OSError, URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError(f"Không thể kiểm tra cập nhật / Could not check for updates: {exc}") from exc


def _download_bytes(url: str, *, max_bytes: int, accept: str) -> bytes:
    try:
        with urlopen(_request(url, accept=accept), timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            _ensure_https(response.geturl())
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise UpdateError("Asset quá lớn / Asset is too large.")
            return data
    except HTTPError as exc:
        raise UpdateError(f"GitHub tải checksum lỗi {exc.code} / Checksum download failed: {exc.code}.") from exc
    except (OSError, URLError) as exc:
        raise UpdateError(f"Không thể tải checksum / Could not download checksum: {exc}") from exc


def _asset_api_url(asset: dict, name: str) -> str:
    url = asset.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise UpdateError(f"URL asset không hợp lệ / Invalid asset URL: {name}.")
    return url


def _normalise_digest(value: object) -> str:
    if not isinstance(value, str):
        raise UpdateError("Thiếu digest SHA-256 / Missing SHA-256 digest.")
    digest = value.removeprefix("sha256:").strip().lower()
    if not _SHA256_PATTERN.fullmatch(digest):
        raise UpdateError("Digest SHA-256 không hợp lệ / Invalid SHA-256 digest.")
    return digest


def _parse_checksum_manifest(
    data: bytes,
    *,
    executable_name: str = EXECUTABLE_NAME,
) -> str:
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise UpdateError("Checksum không phải ASCII / Checksum is not ASCII.") from exc
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64}) (?: |\*)(.+)", line)
        if (
            match
            and match.group(2) == executable_name
        ):
            digest = match.group(1).lower()
            if _SHA256_PATTERN.fullmatch(digest):
                return digest
    raise UpdateError("Checksum manifest không hợp lệ / Invalid checksum manifest.")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise UpdateError(f"Không thể đọc EXE / Could not read EXE: {exc}") from exc
    return digest.hexdigest()


def _default_update_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path(tempfile.gettempdir())
    return base / UPDATE_CACHE_DIRECTORY_NAME / "updates"


def _directory_can_write(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".update-check-", delete=True):
            pass
        return True
    except OSError:
        return False


def _wait_for_process(pid: int, timeout_seconds: int) -> bool:
    if pid <= 0:
        return True
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not handle:
            return True
        try:
            result = ctypes.windll.kernel32.WaitForSingleObject(
                handle, timeout_seconds * 1000
            )
            return result == 0
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.25)
    return False


def _replace_with_retry(source: Path, destination: Path, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.5)
    raise UpdateError(
        f"Không thể thay file sau {timeout_seconds}s / Could not replace file after "
        f"{timeout_seconds}s: {last_error}"
    )


def _move_without_overwrite_with_retry(
    source: Path,
    destination: Path,
    expected_digest: str,
    timeout_seconds: int,
) -> None:
    """Move a migration candidate without replacing an unrelated destination."""

    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            if os.name == "nt":
                os.rename(source, destination)
            else:
                os.link(source, destination)
                source.unlink()
            return
        except FileExistsError as exc:
            if (
                destination.is_file()
                and _sha256_file(destination) == expected_digest
            ):
                source.unlink(missing_ok=True)
                return
            raise UpdateError(
                f"{destination.name} đã tồn tại với nội dung khác / "
                f"{destination.name} already exists with different content."
            ) from exc
        except OSError as exc:
            last_error = exc
            time.sleep(0.5)
    raise UpdateError(
        f"Không thể tạo {destination.name} sau {timeout_seconds}s / "
        f"Could not install {destination.name} after {timeout_seconds}s: {last_error}"
    )


def _argument(args: list[str], name: str) -> str:
    try:
        index = args.index(name)
        return args[index + 1]
    except (ValueError, IndexError) as exc:
        raise UpdateError(f"Thiếu tham số {name} / Missing argument {name}.") from exc


def _ensure_https(url: str) -> None:
    if not isinstance(url, str) or not url.lower().startswith("https://"):
        raise UpdateError("Chỉ cho phép HTTPS / HTTPS is required.")


def _show_native_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(0, message, f"{APP_NAME} Update", 0x10)
    else:
        print(message, file=sys.stderr)


__all__ = [
    "DownloadedUpdate",
    "ReleaseInfo",
    "UpdateError",
    "apply_update_from_argv",
    "check_for_update",
    "download_update",
    "launch_update",
    "open_release_page",
    "parse_version",
    "version_to_tag",
]
