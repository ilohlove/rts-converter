import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import rts_converter_app
from app_metadata import APP_NAME, APP_VERSION, EXECUTABLE_NAME, GITHUB_REPOSITORY
from generate_icon import ICON_SIZES, encode_assets, render_icon


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PNG_PATH = PROJECT_ROOT / "assets" / "rts_converter.png"
ICO_PATH = PROJECT_ROOT / "assets" / "rts_converter.ico"


class BrandingTests(unittest.TestCase):
    def test_public_branding_and_entrypoint_title(self):
        self.assertEqual("RTS Converter", APP_NAME)
        self.assertEqual("1.1.0", APP_VERSION)
        self.assertEqual("RTS Converter.exe", EXECUTABLE_NAME)
        self.assertEqual("rts-converter", GITHUB_REPOSITORY)
        self.assertEqual(
            "RTS Converter v1.1.0 / RTZ to CSV/TXT",
            rts_converter_app.APP_TITLE,
        )

    def test_resource_path_supports_pyinstaller_bundle_root(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                rts_converter_app.sys,
                "_MEIPASS",
                directory,
                create=True,
            ):
                self.assertEqual(
                    Path(directory) / "assets" / "rts_converter.ico",
                    rts_converter_app.resource_path(
                        rts_converter_app.ICON_RELATIVE_PATH
                    ),
                )


class IconAssetTests(unittest.TestCase):
    def test_checked_in_assets_match_generator(self):
        png_bytes, ico_bytes = encode_assets(render_icon())
        self.assertEqual(png_bytes, PNG_PATH.read_bytes())
        self.assertEqual(ico_bytes, ICO_PATH.read_bytes())

    def test_png_has_transparency_and_visible_content(self):
        with Image.open(PNG_PATH) as image:
            rgba = image.convert("RGBA")
            self.assertEqual((1024, 1024), rgba.size)
            self.assertEqual(0, rgba.getpixel((0, 0))[3])
            self.assertGreater(rgba.getpixel((512, 512))[3], 0)
            self.assertIsNotNone(rgba.getbbox())

    def test_ico_contains_every_required_nonblank_frame(self):
        expected_sizes = {(size, size) for size in ICON_SIZES}
        with Image.open(ICO_PATH) as image:
            self.assertEqual(expected_sizes, image.ico.sizes())
            for size in expected_sizes:
                with self.subTest(size=size):
                    frame = image.ico.getimage(size).convert("RGBA")
                    alpha_min, alpha_max = frame.getchannel("A").getextrema()
                    self.assertEqual(0, alpha_min)
                    self.assertEqual(255, alpha_max)
                    self.assertIsNotNone(frame.getbbox())


if __name__ == "__main__":
    unittest.main()
