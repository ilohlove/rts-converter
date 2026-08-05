import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import updater
from app_metadata import (
    APP_NAME,
    APP_VERSION,
    CHECKSUM_ASSET_NAME,
    EXECUTABLE_NAME,
    GITHUB_REPOSITORY,
    LEGACY_CHECKSUM_ASSET_NAME,
    LEGACY_EXECUTABLE_NAME,
    LATEST_RELEASE_API,
    RELEASES_URL,
)
from updater import (
    ReleaseInfo,
    UpdateError,
    _apply_update,
    _default_update_root,
    _parse_checksum_manifest,
    check_for_update,
    download_update,
    parse_version,
    version_to_tag,
)


def release_payload(
    *,
    tag="v1.2.0",
    immutable=True,
    prerelease=False,
    digest="sha256:" + "a" * 64,
    executable_names=(EXECUTABLE_NAME,),
    checksum_names=None,
):
    if checksum_names is None:
        checksum_names = tuple(f"{name}.sha256" for name in executable_names)

    assets = []
    asset_id = 1
    for name in executable_names:
        assets.append(
            {
                "name": name,
                "state": "uploaded",
                "url": (
                    "https://api.github.com/repos/ilohlove/"
                    f"{GITHUB_REPOSITORY}/releases/assets/{asset_id}"
                ),
                "digest": digest,
            }
        )
        asset_id += 1
    for name in checksum_names:
        assets.append(
            {
                "name": name,
                "state": "uploaded",
                "url": (
                    "https://api.github.com/repos/ilohlove/"
                    f"{GITHUB_REPOSITORY}/releases/assets/{asset_id}"
                ),
            }
        )
        asset_id += 1
    return {
        "tag_name": tag,
        "html_url": f"{RELEASES_URL}/tag/{tag}",
        "body": "RTS Converter branding",
        "draft": False,
        "prerelease": prerelease,
        "immutable": immutable,
        "assets": assets,
    }


def release_info(content: bytes, *, executable_name=EXECUTABLE_NAME):
    digest = hashlib.sha256(content).hexdigest()
    return ReleaseInfo(
        tag_name="v1.2.0",
        version=(1, 2, 0),
        html_url=f"{RELEASES_URL}/tag/v1.2.0",
        body="",
        immutable=True,
        executable_asset_url="https://api.github.com/assets/exe",
        checksum_asset_url="https://api.github.com/assets/checksum",
        api_digest=digest,
        executable_name=executable_name,
    )


class MetadataTests(unittest.TestCase):
    def test_rts_branding_and_release_urls(self):
        self.assertEqual("RTS Converter", APP_NAME)
        self.assertEqual("1.1.0", APP_VERSION)
        self.assertEqual("rts-converter", GITHUB_REPOSITORY)
        self.assertEqual("RTS Converter.exe", EXECUTABLE_NAME)
        self.assertEqual("RTS Converter.exe.sha256", CHECKSUM_ASSET_NAME)
        self.assertEqual("RTZ-to-CSV.exe", LEGACY_EXECUTABLE_NAME)
        self.assertEqual("RTZ-to-CSV.exe.sha256", LEGACY_CHECKSUM_ASSET_NAME)
        self.assertIn("/repos/ilohlove/rts-converter/", LATEST_RELEASE_API)

    def test_update_cache_uses_new_brand_without_removing_legacy_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            legacy_cache = base / "RTZ-to-CSV" / "updates"
            legacy_cache.mkdir(parents=True)
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(base)}):
                self.assertEqual(
                    base / "RTS Converter" / "updates",
                    _default_update_root(),
                )
            self.assertTrue(legacy_cache.is_dir())


class VersionTests(unittest.TestCase):
    def test_strict_semver_and_tag(self):
        self.assertEqual((1, 2, 3), parse_version("v1.2.3"))
        self.assertEqual("v1.2.3", version_to_tag((1, 2, 3)))
        for value in ("1.2", "v1.2.3-beta", "01.2.3", "latest"):
            with self.subTest(value=value):
                with self.assertRaises(UpdateError):
                    parse_version(value)


class ReleaseCheckTests(unittest.TestCase):
    def test_prefers_new_asset_pair_when_release_has_both_pairs(self):
        payload = release_payload(
            executable_names=(EXECUTABLE_NAME, LEGACY_EXECUTABLE_NAME),
            checksum_names=(CHECKSUM_ASSET_NAME, LEGACY_CHECKSUM_ASSET_NAME),
        )
        with mock.patch("updater._request_json", return_value=payload):
            release = check_for_update("1.1.0")
        self.assertIsNotNone(release)
        self.assertEqual(EXECUTABLE_NAME, release.executable_name)
        self.assertEqual((1, 2, 0), release.version)
        self.assertEqual("a" * 64, release.api_digest)
        self.assertTrue(release.immutable)

    def test_falls_back_to_complete_legacy_asset_pair(self):
        payload = release_payload(executable_names=(LEGACY_EXECUTABLE_NAME,))
        with mock.patch("updater._request_json", return_value=payload):
            release = check_for_update("1.1.0")
        self.assertIsNotNone(release)
        self.assertEqual(LEGACY_EXECUTABLE_NAME, release.executable_name)

    def test_equal_or_older_release_is_ignored(self):
        for tag in ("v1.1.0", "v1.0.0"):
            with self.subTest(tag=tag):
                with mock.patch(
                    "updater._request_json",
                    return_value=release_payload(tag=tag),
                ):
                    self.assertIsNone(check_for_update("1.1.0"))

    def test_ignores_prerelease_and_rejects_mutable_or_incomplete_pair(self):
        with mock.patch(
            "updater._request_json", return_value=release_payload(prerelease=True)
        ):
            self.assertIsNone(check_for_update("1.1.0"))
        invalid_payloads = (
            release_payload(immutable=False),
            release_payload(checksum_names=()),
        )
        for payload in invalid_payloads:
            with mock.patch("updater._request_json", return_value=payload):
                with self.assertRaises(UpdateError):
                    check_for_update("1.1.0")


class DownloadTests(unittest.TestCase):
    def test_manifest_parser_supports_spaced_and_legacy_filenames(self):
        digest = "b" * 64
        self.assertEqual(
            digest,
            _parse_checksum_manifest(
                f"{digest}  {EXECUTABLE_NAME}\n".encode("ascii")
            ),
        )
        self.assertEqual(
            digest,
            _parse_checksum_manifest(
                f"{digest} *{LEGACY_EXECUTABLE_NAME}\n".encode("ascii"),
                executable_name=LEGACY_EXECUTABLE_NAME,
            ),
        )
        with self.assertRaises(UpdateError):
            _parse_checksum_manifest(
                f"{digest}  {LEGACY_EXECUTABLE_NAME}\n".encode("ascii")
            )
        with self.assertRaises(UpdateError):
            _parse_checksum_manifest(
                f"{digest}  subdir/{EXECUTABLE_NAME}\n".encode("ascii")
            )
        with self.assertRaises(UpdateError):
            _parse_checksum_manifest(
                f"{digest}  {EXECUTABLE_NAME.lower()}\n".encode("ascii")
            )
        with self.assertRaises(UpdateError):
            _parse_checksum_manifest(b"not-a-checksum\n")

    def test_download_verifies_manifest_and_stages_new_executable_name(self):
        content = b"new executable bytes"
        release = release_info(content, executable_name=LEGACY_EXECUTABLE_NAME)

        class Response:
            headers = {"Content-Length": str(len(content))}

            def __init__(self):
                self._read = False

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return "https://objects.githubusercontent.com/asset"

            def read(self, _size=-1):
                if not self._read:
                    self._read = True
                    return content
                return b""

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch(
                "updater._download_bytes",
                return_value=(
                    f"{release.api_digest}  {LEGACY_EXECUTABLE_NAME}\n".encode(
                        "ascii"
                    )
                ),
            ), mock.patch("updater.urlopen", return_value=Response()):
                downloaded = download_update(release, root_dir=Path(directory))
            self.assertEqual(content, downloaded.staged_path.read_bytes())
            self.assertEqual(EXECUTABLE_NAME, downloaded.staged_path.name)
            self.assertEqual(release.api_digest, downloaded.sha256)

    def test_download_rejects_manifest_digest_mismatch(self):
        content = b"new executable bytes"
        release = release_info(content)
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch(
                "updater._download_bytes",
                return_value=f"{'c' * 64}  {EXECUTABLE_NAME}\n".encode("ascii"),
            ):
                with self.assertRaises(UpdateError):
                    download_update(release, root_dir=Path(directory))


class ApplyUpdateTests(unittest.TestCase):
    def test_apply_update_replaces_new_name_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged.exe"
            target = root / EXECUTABLE_NAME
            staged.write_bytes(b"new executable")
            target.write_bytes(b"old executable")
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()

            with mock.patch.object(updater.sys, "executable", str(staged)), mock.patch(
                "updater._wait_for_process", return_value=True
            ), mock.patch("updater.subprocess.Popen") as popen:
                _apply_update(target, 123, digest)

            self.assertEqual(b"new executable", target.read_bytes())
            self.assertEqual(
                b"old executable",
                (root / f"{EXECUTABLE_NAME}.old").read_bytes(),
            )
            self.assertEqual([str(target)], popen.call_args.args[0])

    def test_legacy_target_migrates_to_new_name_and_preserves_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged.exe"
            legacy_target = root / LEGACY_EXECUTABLE_NAME
            new_target = root / EXECUTABLE_NAME
            staged.write_bytes(b"new executable")
            legacy_target.write_bytes(b"old executable")
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()

            with mock.patch.object(updater.sys, "executable", str(staged)), mock.patch(
                "updater._wait_for_process", return_value=True
            ), mock.patch("updater.subprocess.Popen") as popen:
                _apply_update(legacy_target, 123, digest)

            self.assertFalse(legacy_target.exists())
            self.assertEqual(b"new executable", new_target.read_bytes())
            self.assertEqual(
                b"old executable",
                (root / f"{LEGACY_EXECUTABLE_NAME}.old").read_bytes(),
            )
            self.assertEqual([str(new_target)], popen.call_args.args[0])
            self.assertEqual(
                "1", popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"]
            )

    def test_legacy_migration_refuses_different_existing_new_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged.exe"
            legacy_target = root / LEGACY_EXECUTABLE_NAME
            new_target = root / EXECUTABLE_NAME
            staged.write_bytes(b"downloaded executable")
            legacy_target.write_bytes(b"old executable")
            new_target.write_bytes(b"unrelated executable")
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()

            with mock.patch.object(updater.sys, "executable", str(staged)), mock.patch(
                "updater._wait_for_process", return_value=True
            ), mock.patch("updater.subprocess.Popen") as popen:
                with self.assertRaises(UpdateError):
                    _apply_update(legacy_target, 123, digest)

            self.assertEqual(b"old executable", legacy_target.read_bytes())
            self.assertEqual(b"unrelated executable", new_target.read_bytes())
            self.assertFalse((root / f"{LEGACY_EXECUTABLE_NAME}.old").exists())
            popen.assert_not_called()

    def test_legacy_migration_accepts_matching_existing_new_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged.exe"
            legacy_target = root / LEGACY_EXECUTABLE_NAME
            new_target = root / EXECUTABLE_NAME
            staged.write_bytes(b"new executable")
            legacy_target.write_bytes(b"old executable")
            new_target.write_bytes(staged.read_bytes())
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()

            with mock.patch.object(updater.sys, "executable", str(staged)), mock.patch(
                "updater._wait_for_process", return_value=True
            ), mock.patch("updater.subprocess.Popen") as popen:
                _apply_update(legacy_target, 123, digest)

            self.assertFalse(legacy_target.exists())
            self.assertEqual(b"new executable", new_target.read_bytes())
            self.assertEqual(
                b"old executable",
                (root / f"{LEGACY_EXECUTABLE_NAME}.old").read_bytes(),
            )
            self.assertEqual([str(new_target)], popen.call_args.args[0])

    def test_legacy_migration_rolls_back_when_new_name_install_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged.exe"
            legacy_target = root / LEGACY_EXECUTABLE_NAME
            new_target = root / EXECUTABLE_NAME
            staged.write_bytes(b"new executable")
            legacy_target.write_bytes(b"old executable")
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()

            with mock.patch.object(updater.sys, "executable", str(staged)), mock.patch(
                "updater._wait_for_process", return_value=True
            ), mock.patch(
                "updater._move_without_overwrite_with_retry",
                side_effect=UpdateError("install failed"),
            ), mock.patch("updater.subprocess.Popen") as popen:
                with self.assertRaises(UpdateError):
                    _apply_update(legacy_target, 123, digest)

            self.assertEqual(b"old executable", legacy_target.read_bytes())
            self.assertFalse(new_target.exists())
            self.assertFalse((root / f"{LEGACY_EXECUTABLE_NAME}.old").exists())
            self.assertFalse((root / f"{EXECUTABLE_NAME}.new").exists())
            popen.assert_not_called()

    def test_copy_failure_preserves_target_and_removes_partial_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged.exe"
            target = root / EXECUTABLE_NAME
            candidate = root / f"{EXECUTABLE_NAME}.new"
            staged.write_bytes(b"new executable")
            target.write_bytes(b"old executable")
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()

            def fail_after_partial_copy(_source, destination):
                Path(destination).write_bytes(b"partial")
                raise OSError("copy failed")

            with mock.patch.object(updater.sys, "executable", str(staged)), mock.patch(
                "updater._wait_for_process", return_value=True
            ), mock.patch(
                "updater.shutil.copy2", side_effect=fail_after_partial_copy
            ), mock.patch("updater.subprocess.Popen") as popen:
                with self.assertRaises(OSError):
                    _apply_update(target, 123, digest)

            self.assertEqual(b"old executable", target.read_bytes())
            self.assertFalse(candidate.exists())
            self.assertFalse((root / f"{EXECUTABLE_NAME}.old").exists())
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
