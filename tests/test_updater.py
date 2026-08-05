import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import updater
from updater import (
    ReleaseInfo,
    UpdateError,
    _apply_update,
    _parse_checksum_manifest,
    check_for_update,
    download_update,
    parse_version,
    version_to_tag,
)


EXECUTABLE_NAME = "RTZ-to-CSV.exe"
CHECKSUM_NAME = "RTZ-to-CSV.exe.sha256"


def release_payload(
    *,
    tag="v1.1.0",
    immutable=True,
    prerelease=False,
    digest="sha256:" + "a" * 64,
    include_checksum=True,
):
    assets = [
        {
            "name": EXECUTABLE_NAME,
            "state": "uploaded",
            "url": "https://api.github.com/repos/ilohlove/rtz-converter/releases/assets/1",
            "digest": digest,
        }
    ]
    if include_checksum:
        assets.append(
            {
                "name": CHECKSUM_NAME,
                "state": "uploaded",
                "url": "https://api.github.com/repos/ilohlove/rtz-converter/releases/assets/2",
            }
        )
    return {
        "tag_name": tag,
        "html_url": "https://github.com/ilohlove/rtz-converter/releases/tag/" + tag,
        "body": "TXT export improvements",
        "draft": False,
        "prerelease": prerelease,
        "immutable": immutable,
        "assets": assets,
    }


class VersionTests(unittest.TestCase):
    def test_strict_semver_and_tag(self):
        self.assertEqual((1, 2, 3), parse_version("v1.2.3"))
        self.assertEqual("v1.2.3", version_to_tag((1, 2, 3)))
        for value in ("1.2", "v1.2.3-beta", "01.2.3", "latest"):
            with self.subTest(value=value):
                with self.assertRaises(UpdateError):
                    parse_version(value)


class ReleaseCheckTests(unittest.TestCase):
    def test_returns_new_immutable_stable_release(self):
        payload = release_payload()
        with mock.patch("updater._request_json", return_value=payload):
            release = check_for_update("1.0.0")
        self.assertIsNotNone(release)
        self.assertEqual((1, 1, 0), release.version)
        self.assertEqual("a" * 64, release.api_digest)
        self.assertTrue(release.immutable)

    def test_equal_or_older_release_is_ignored(self):
        for tag in ("v1.0.0", "v0.9.9"):
            with self.subTest(tag=tag):
                with mock.patch(
                    "updater._request_json",
                    return_value=release_payload(tag=tag),
                ):
                    self.assertIsNone(check_for_update("1.0.0"))

    def test_ignores_prerelease_and_rejects_mutable_or_missing_asset(self):
        with mock.patch(
            "updater._request_json", return_value=release_payload(prerelease=True)
        ):
            self.assertIsNone(check_for_update("1.0.0"))
        for payload in (
            release_payload(immutable=False),
            release_payload(include_checksum=False),
        ):
            with mock.patch("updater._request_json", return_value=payload):
                with self.assertRaises(UpdateError):
                    check_for_update("1.0.0")


class DownloadTests(unittest.TestCase):
    def test_manifest_parser_requires_executable_name(self):
        digest = "b" * 64
        self.assertEqual(
            digest,
            _parse_checksum_manifest(
                f"{digest}  {EXECUTABLE_NAME}\nother\n".encode("ascii")
            ),
        )
        with self.assertRaises(UpdateError):
            _parse_checksum_manifest(b"not-a-checksum\n")

    def test_download_verifies_manifest_and_writes_staged_exe(self):
        content = b"new executable bytes"
        digest = hashlib.sha256(content).hexdigest()
        release = ReleaseInfo(
            tag_name="v1.1.0",
            version=(1, 1, 0),
            html_url="https://github.com/ilohlove/rtz-converter/releases/tag/v1.1.0",
            body="",
            immutable=True,
            executable_asset_url="https://api.github.com/assets/exe",
            checksum_asset_url="https://api.github.com/assets/checksum",
            api_digest=digest,
        )

        class Response:
            headers = {"Content-Length": str(len(content))}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self):
                return "https://objects.githubusercontent.com/asset"

            def read(self, _size=-1):
                if self._read:
                    self._read = False
                    return content
                return b""

            _read = True

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch(
                "updater._download_bytes",
                return_value=f"{digest}  {EXECUTABLE_NAME}\n".encode("ascii"),
            ), mock.patch("updater.urlopen", return_value=Response()):
                downloaded = download_update(release, root_dir=Path(directory))
            self.assertEqual(content, downloaded.staged_path.read_bytes())
            self.assertEqual(digest, downloaded.sha256)

    def test_download_rejects_manifest_digest_mismatch(self):
        content = b"new executable bytes"
        actual = hashlib.sha256(content).hexdigest()
        release = ReleaseInfo(
            tag_name="v1.1.0",
            version=(1, 1, 0),
            html_url="https://github.com/ilohlove/rtz-converter/releases/tag/v1.1.0",
            body="",
            immutable=True,
            executable_asset_url="https://api.github.com/assets/exe",
            checksum_asset_url="https://api.github.com/assets/checksum",
            api_digest=actual,
        )
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch(
                "updater._download_bytes",
                return_value=f"{'c' * 64}  {EXECUTABLE_NAME}\n".encode("ascii"),
            ):
                with self.assertRaises(UpdateError):
                    download_update(release, root_dir=Path(directory))


class ApplyUpdateTests(unittest.TestCase):
    def test_apply_update_replaces_target_and_starts_new_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged.exe"
            target = root / "RTZ-to-CSV.exe"
            staged.write_bytes(b"new executable")
            target.write_bytes(b"old executable")
            digest = hashlib.sha256(staged.read_bytes()).hexdigest()

            with mock.patch.object(updater.sys, "executable", str(staged)), mock.patch(
                "updater._wait_for_process", return_value=True
            ), mock.patch("updater.subprocess.Popen") as popen:
                _apply_update(target, 123, digest)

            self.assertEqual(b"new executable", target.read_bytes())
            self.assertTrue((root / "RTZ-to-CSV.exe.old").exists())
            popen.assert_called_once()
            self.assertEqual([str(target)], popen.call_args.args[0])
            self.assertEqual(
                "1", popen.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"]
            )


if __name__ == "__main__":
    unittest.main()
