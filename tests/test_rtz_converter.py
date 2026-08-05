import csv
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from rtz_converter import (
    ConversionStatus,
    ExistingFileAction,
    OutputFormat,
    RtzConversionError,
    convert_many,
    convert_rtz,
    destination_for,
    destination_for_txt,
    format_coordinate_dmm,
    parse_rtz,
    write_csv_atomic,
    write_txt_atomic,
)


NS_10 = "http://www.cirm.org/RTZ/1/0"
NS_12 = "http://www.cirm.org/RTZ/1/2"
CSV_HEADER = [
    "route_name",
    "sequence",
    "waypoint_id",
    "waypoint_name",
    "latitude",
    "longitude",
    "leg_geometry_type",
]
MISSING = object()


def make_waypoint(
    waypoint_id="1",
    name="Waypoint",
    lat="10.0000",
    lon="20.0000",
    geometry="Loxodrome",
    *,
    include_position=True,
):
    return {
        "id": waypoint_id,
        "name": name,
        "lat": lat,
        "lon": lon,
        "geometry": geometry,
        "include_position": include_position,
    }


def write_rtz(
    directory,
    filename="route.rtz",
    *,
    version="1.0",
    namespace=MISSING,
    route_name="Test route",
    waypoints=None,
    default_geometry=None,
    active_path_ids=(),
):
    if namespace is MISSING:
        namespace = NS_10 if version == "1.0" else NS_12

    root_attributes = {"version": version}
    if namespace is not None:
        root_attributes["xmlns"] = namespace
    root = ET.Element("route", root_attributes)

    route_info = ET.SubElement(root, "routeInfo")
    if route_name is not MISSING:
        route_info.set("routeName", route_name)

    waypoints_element = ET.SubElement(root, "waypoints")
    if default_geometry is not None:
        default_waypoint = ET.SubElement(waypoints_element, "defaultWaypoint")
        ET.SubElement(
            default_waypoint, "leg", {"geometryType": default_geometry}
        )
    if waypoints is None:
        waypoints = [
            make_waypoint("1", "First", "10.0000", "20.0000", "Loxodrome"),
            make_waypoint("2", "Second", "11.0000", "21.0000", "Orthodrome"),
        ]

    for item in waypoints:
        waypoint_attributes = {}
        if item.get("id", MISSING) is not MISSING:
            waypoint_attributes["id"] = item["id"]
        if item.get("name", MISSING) is not MISSING:
            waypoint_attributes["name"] = item["name"]
        waypoint_element = ET.SubElement(
            waypoints_element, "waypoint", waypoint_attributes
        )

        if item.get("include_position", True):
            position_attributes = {}
            if item.get("lat", MISSING) is not MISSING:
                position_attributes["lat"] = item["lat"]
            if item.get("lon", MISSING) is not MISSING:
                position_attributes["lon"] = item["lon"]
            ET.SubElement(waypoint_element, "position", position_attributes)

        geometry = item.get("geometry", MISSING)
        if geometry is not MISSING and geometry is not None:
            ET.SubElement(waypoint_element, "leg", {"geometryType": geometry})

    if active_path_ids:
        extensions = ET.SubElement(root, "extensions")
        extension = ET.SubElement(
            extensions,
            "extension",
            {"name": "ActivePath", "manufacturer": "Test"},
        )
        path_element = ET.SubElement(extension, "path")
        ids_element = ET.SubElement(path_element, "wp_ids")
        for waypoint_id in active_path_ids:
            ET.SubElement(ids_element, "wp_id", {"value": str(waypoint_id)})

    output = Path(directory) / filename
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    return output


def write_core_fixture(directory, filename="core-fixture.rtz"):
    items = [
        make_waypoint("1", "Yokohama", "35.4483333", "139.685"),
    ]
    for identifier in range(2, 50):
        items.append(
            make_waypoint(
                str(identifier),
                lat=f"{35 + identifier / 1000:.7f}",
                lon=f"{139 + identifier / 1000:.7f}",
            )
        )
    items.append(
        make_waypoint("50", "Vancouver, B.C., Canada", "49.3", "-123.0883333")
    )
    return write_rtz(
        directory,
        filename,
        route_name="Exported route",
        waypoints=items,
        active_path_ids=range(1, 52),
    )


class ParseRtzTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_accepts_cirm_10_and_12_namespaces(self):
        for version, namespace in (("1.0", NS_10), ("1.2", NS_12)):
            with self.subTest(version=version):
                source = write_rtz(
                    self.directory,
                    f"route-{version}.rtz",
                    version=version,
                    namespace=namespace,
                )

                parsed = parse_rtz(source)

                self.assertEqual(source, parsed.source)
                self.assertEqual("Test route", parsed.route_name)
                self.assertEqual(2, len(parsed.waypoints))

    def test_preserves_document_order_text_precision_unicode_and_blank_name(self):
        source = write_rtz(
            self.directory,
            route_name="Hải trình, thử nghiệm",
            waypoints=[
                make_waypoint(
                    "20",
                    "Cảng, Đà Nẵng",
                    "-01.2300",
                    "+109.50000",
                    "Orthodrome",
                ),
                make_waypoint("3", "", "0.000000", "-179.9900", "Loxodrome"),
            ],
        )

        parsed = parse_rtz(source)

        self.assertEqual("Hải trình, thử nghiệm", parsed.route_name)
        self.assertEqual([1, 2], [row.sequence for row in parsed.waypoints])
        self.assertEqual(["20", "3"], [row.waypoint_id for row in parsed.waypoints])
        first, second = parsed.waypoints
        self.assertEqual("Cảng, Đà Nẵng", first.waypoint_name)
        self.assertEqual("-01.2300", first.latitude)
        self.assertEqual("+109.50000", first.longitude)
        self.assertEqual("Orthodrome", first.leg_geometry_type)
        self.assertEqual("", second.waypoint_name)
        self.assertEqual("0.000000", second.latitude)
        self.assertEqual("-179.9900", second.longitude)

    def test_uses_default_waypoint_geometry_when_leg_is_absent(self):
        source = write_rtz(
            self.directory,
            default_geometry="Loxodrome",
            waypoints=[
                make_waypoint("1", geometry="Orthodrome"),
                make_waypoint("2", geometry=None),
            ],
        )

        parsed = parse_rtz(source)

        self.assertEqual(
            ["Orthodrome", "Loxodrome"],
            [row.leg_geometry_type for row in parsed.waypoints],
        )

    def test_sample_has_50_waypoints_and_ignores_vendor_active_path(self):
        parsed = parse_rtz(write_core_fixture(self.directory))

        self.assertEqual("Exported route", parsed.route_name)
        self.assertEqual(50, len(parsed.waypoints))
        first = parsed.waypoints[0]
        self.assertEqual((1, "1", "Yokohama"), (
            first.sequence,
            first.waypoint_id,
            first.waypoint_name,
        ))
        self.assertEqual(("35.4483333", "139.685"), (
            first.latitude,
            first.longitude,
        ))
        last = parsed.waypoints[-1]
        self.assertEqual((50, "50", "Vancouver, B.C., Canada"), (
            last.sequence,
            last.waypoint_id,
            last.waypoint_name,
        ))
        self.assertEqual(("49.3", "-123.0883333"), (
            last.latitude,
            last.longitude,
        ))
        self.assertEqual((), parsed.warnings)

    def test_rejects_malformed_xml(self):
        source = self.directory / "malformed.rtz"
        source.write_text("<route><broken></route>", encoding="utf-8")

        with self.assertRaises(RtzConversionError):
            parse_rtz(source)

    def test_rejects_missing_or_unsupported_namespace(self):
        for label, namespace in (
            ("missing", None),
            ("unsupported", "https://example.test/not-rtz"),
        ):
            with self.subTest(label=label):
                source = write_rtz(
                    self.directory,
                    f"namespace-{label}.rtz",
                    namespace=namespace,
                )
                with self.assertRaises(RtzConversionError):
                    parse_rtz(source)

    def test_rejects_unsupported_or_namespace_mismatched_version(self):
        cases = (
            ("9.9", NS_10),
            ("1.2", NS_10),
            ("1.0", NS_12),
        )
        for index, (version, namespace) in enumerate(cases):
            with self.subTest(version=version, namespace=namespace):
                source = write_rtz(
                    self.directory,
                    f"bad-version-{index}.rtz",
                    version=version,
                    namespace=namespace,
                )
                with self.assertRaises(RtzConversionError):
                    parse_rtz(source)

    def test_rejects_missing_or_blank_route_name(self):
        for index, route_name in enumerate((MISSING, "", "   \t")):
            with self.subTest(route_name=route_name):
                source = write_rtz(
                    self.directory,
                    f"route-name-{index}.rtz",
                    route_name=route_name,
                )
                with self.assertRaises(RtzConversionError):
                    parse_rtz(source)

    def test_rejects_fewer_than_two_waypoints(self):
        for count in (0, 1):
            with self.subTest(count=count):
                items = [] if count == 0 else [make_waypoint("1")]
                source = write_rtz(
                    self.directory,
                    f"count-{count}.rtz",
                    waypoints=items,
                )
                with self.assertRaises(RtzConversionError):
                    parse_rtz(source)

    def test_rejects_missing_invalid_nonfinite_or_out_of_range_coordinates(self):
        invalid_first_waypoints = (
            make_waypoint("1", lat=MISSING),
            make_waypoint("1", lon=MISSING),
            make_waypoint("1", lat="north"),
            make_waypoint("1", lon="east"),
            make_waypoint("1", lat="NaN"),
            make_waypoint("1", lon="Infinity"),
            make_waypoint("1", lat="90.0001"),
            make_waypoint("1", lat="-90.0001"),
            make_waypoint("1", lon="180.0001"),
            make_waypoint("1", lon="-180.0001"),
            make_waypoint("1", include_position=False),
        )
        for index, invalid_waypoint in enumerate(invalid_first_waypoints):
            with self.subTest(index=index, waypoint=invalid_waypoint):
                source = write_rtz(
                    self.directory,
                    f"coordinate-{index}.rtz",
                    waypoints=[invalid_waypoint, make_waypoint("2")],
                )
                with self.assertRaises(RtzConversionError):
                    parse_rtz(source)

    def test_rejects_duplicate_missing_or_invalid_waypoint_ids(self):
        cases = (
            [make_waypoint("1"), make_waypoint("1")],
            [make_waypoint("1"), make_waypoint("+1")],
            [make_waypoint(MISSING), make_waypoint("2")],
            [make_waypoint(""), make_waypoint("2")],
            [make_waypoint("abc"), make_waypoint("2")],
            [make_waypoint("-1"), make_waypoint("2")],
            [make_waypoint("1.5"), make_waypoint("2")],
        )
        for index, items in enumerate(cases):
            with self.subTest(index=index):
                source = write_rtz(
                    self.directory,
                    f"waypoint-id-{index}.rtz",
                    waypoints=items,
                )
                with self.assertRaises(RtzConversionError):
                    parse_rtz(source)

    def test_rejects_invalid_leg_geometry(self):
        for index, geometry in enumerate(("", "GreatCircle", "loxodrome")):
            with self.subTest(geometry=geometry):
                source = write_rtz(
                    self.directory,
                    f"geometry-{index}.rtz",
                    waypoints=[
                        make_waypoint("1", geometry=geometry),
                        make_waypoint("2"),
                    ],
                )
                with self.assertRaises(RtzConversionError):
                    parse_rtz(source)


class CsvWritingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_writes_utf8_bom_header_and_csv_quoting(self):
        source = write_rtz(
            self.directory,
            route_name="Hải trình, thử",
            waypoints=[
                make_waypoint("1", "Cảng, Đà Nẵng", "-12.3400", "109.123400"),
                make_waypoint("2", "", "-13", "110"),
            ],
        )
        parsed = parse_rtz(source)
        output = self.directory / "quoted.csv"

        returned = write_csv_atomic(parsed, output)

        self.assertEqual(output, returned)
        raw = output.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8-sig")
        self.assertIn('"Hải trình, thử"', text)
        self.assertIn('"Cảng, Đà Nẵng"', text)
        with output.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(CSV_HEADER, rows[0])
        self.assertEqual(3, len(rows))
        self.assertEqual(
            [
                "Hải trình, thử",
                "1",
                "1",
                "Cảng, Đà Nẵng",
                "-12.3400",
                "109.123400",
                "Loxodrome",
            ],
            rows[1],
        )
        self.assertEqual("", rows[2][3])

    def test_atomic_replace_failure_preserves_old_output_and_removes_temp_file(self):
        source = write_rtz(self.directory)
        parsed = parse_rtz(source)
        output = self.directory / "route.csv"
        old_bytes = b"old csv stays intact\r\n"
        output.write_bytes(old_bytes)
        files_before = set(self.directory.iterdir())

        with mock.patch(
            "rtz_converter.os.replace", side_effect=OSError("replace denied")
        ):
            with self.assertRaises(RtzConversionError):
                write_csv_atomic(parsed, output)

        self.assertEqual(old_bytes, output.read_bytes())
        self.assertEqual(files_before, set(self.directory.iterdir()))

    def test_write_to_missing_parent_fails_without_creating_output(self):
        source = write_rtz(self.directory)
        parsed = parse_rtz(source)
        output = self.directory / "missing" / "route.csv"

        with self.assertRaises(RtzConversionError):
            write_csv_atomic(parsed, output)

        self.assertFalse(output.exists())


class TxtWritingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_formats_decimal_coordinates_with_rounding_and_hemisphere(self):
        cases = (
            ("35.4483333", "latitude", "35° 26.9' N"),
            ("-12.34", "latitude", "12° 20.4' S"),
            ("139.685", "longitude", "139° 41.1' E"),
            ("-123.0883333", "longitude", "123° 05.3' W"),
            ("10.9991666667", "latitude", "11° 00.0' N"),
            ("-10.9991666667", "latitude", "11° 00.0' S"),
            ("0", "longitude", "0° 00.0' E"),
        )
        for value, axis, expected in cases:
            with self.subTest(value=value, axis=axis):
                self.assertEqual(expected, format_coordinate_dmm(value, axis))

    def test_writes_bom_tab_delimited_txt_in_waypoint_order(self):
        source = write_rtz(
            self.directory,
            waypoints=[
                make_waypoint("1", lat="35.4483333", lon="139.685"),
                make_waypoint("2", lat="-12.34", lon="-123.0883333"),
            ],
        )
        parsed = parse_rtz(source)
        output = destination_for_txt(source)

        returned = write_txt_atomic(parsed, output)

        self.assertEqual(output, returned)
        raw = output.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(raw.endswith(b"\r\n"))
        self.assertEqual(
            [
                "35° 26.9' N\t139° 41.1' E",
                "12° 20.4' S\t123° 05.3' W",
            ],
            raw.decode("utf-8-sig").splitlines(),
        )

    def test_sample_txt_has_50_lines_and_expected_first_last_coordinates(self):
        sample = write_core_fixture(self.directory)
        output = self.directory / "sample.txt"

        write_txt_atomic(parse_rtz(sample), output)

        lines = output.read_bytes().decode("utf-8-sig").splitlines()
        self.assertEqual(50, len(lines))
        self.assertEqual("35° 26.9' N\t139° 41.1' E", lines[0])
        self.assertEqual("49° 18.0' N\t123° 05.3' W", lines[-1])
        self.assertTrue(all("\t" in line for line in lines))

    def test_txt_atomic_replace_failure_preserves_old_output(self):
        source = write_rtz(self.directory)
        parsed = parse_rtz(source)
        output = self.directory / "route.txt"
        old_bytes = b"old txt stays intact\r\n"
        output.write_bytes(old_bytes)
        files_before = set(self.directory.iterdir())

        with mock.patch(
            "rtz_converter.os.replace", side_effect=OSError("replace denied")
        ):
            with self.assertRaises(RtzConversionError):
                write_txt_atomic(parsed, output)

        self.assertEqual(old_bytes, output.read_bytes())
        self.assertEqual(files_before, set(self.directory.iterdir()))


class ConversionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_destination_and_single_conversion_result(self):
        source = write_rtz(self.directory, "voyage.RTZ")

        self.assertEqual(self.directory / "voyage.csv", destination_for(source))
        self.assertEqual(self.directory / "voyage.txt", destination_for_txt(source))
        result = convert_rtz(source)

        self.assertEqual(source, result.source)
        self.assertEqual(self.directory / "voyage.csv", result.output)
        self.assertEqual(ConversionStatus.SUCCESS, result.status)
        self.assertEqual(2, result.waypoint_count)
        self.assertIsNone(result.error)
        self.assertTrue(result.output.exists())

    def test_batch_txt_uses_txt_destination_and_output_format(self):
        source = write_rtz(self.directory, "voyage.rtz")
        output = destination_for_txt(source)
        decisions = []

        def overwrite_decider(callback_source, callback_output):
            decisions.append((callback_source, callback_output))
            return ExistingFileAction.OVERWRITE

        batch = convert_many(
            [source],
            overwrite_decider,
            output_format=OutputFormat.TXT,
        )

        self.assertFalse(batch.cancelled)
        self.assertEqual(1, len(batch.results))
        result = batch.results[0]
        self.assertEqual(ConversionStatus.SUCCESS, result.status)
        self.assertEqual(output, result.output)
        self.assertTrue(output.exists())
        self.assertTrue(output.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual([], decisions)

        output.write_bytes(b"old txt")
        batch = convert_many(
            [source],
            overwrite_decider,
            output_format=".txt",
        )
        self.assertEqual([(source, output)], decisions)
        self.assertEqual(ConversionStatus.SUCCESS, batch.results[0].status)

    def test_batch_overwrites_existing_csv_when_decider_allows_it(self):
        source = write_rtz(self.directory, "overwrite.rtz")
        output = destination_for(source)
        output.write_text("old", encoding="utf-8")
        decisions = []

        def overwrite_decider(callback_source, callback_output):
            decisions.append((callback_source, callback_output))
            return ExistingFileAction.OVERWRITE

        batch = convert_many([source], overwrite_decider)

        self.assertFalse(batch.cancelled)
        self.assertEqual([(source, output)], decisions)
        self.assertEqual(1, len(batch.results))
        result = batch.results[0]
        self.assertEqual(ConversionStatus.SUCCESS, result.status)
        self.assertEqual(output, result.output)
        self.assertEqual(2, result.waypoint_count)
        self.assertTrue(output.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_batch_skip_preserves_existing_csv(self):
        source = write_rtz(self.directory, "skip.rtz")
        output = destination_for(source)
        old_bytes = b"do not overwrite"
        output.write_bytes(old_bytes)

        batch = convert_many(
            [source], lambda _source, _output: ExistingFileAction.SKIP
        )

        self.assertFalse(batch.cancelled)
        self.assertEqual(1, len(batch.results))
        result = batch.results[0]
        self.assertEqual(ConversionStatus.SKIPPED, result.status)
        self.assertEqual(output, result.output)
        self.assertEqual(old_bytes, output.read_bytes())

    def test_batch_cancel_stops_before_current_and_remaining_files(self):
        first = write_rtz(self.directory, "first.rtz")
        second = write_rtz(self.directory, "second.rtz")
        first_output = destination_for(first)
        first_output.write_bytes(b"existing")
        calls = []

        def cancel_decider(source, output):
            calls.append((source, output))
            return ExistingFileAction.CANCEL

        batch = convert_many([first, second], cancel_decider)

        self.assertTrue(batch.cancelled)
        self.assertEqual((), batch.results)
        self.assertEqual([(first, first_output)], calls)
        self.assertEqual(b"existing", first_output.read_bytes())
        self.assertFalse(destination_for(second).exists())

    def test_batch_records_invalid_file_and_continues_with_next_file(self):
        invalid = self.directory / "invalid.rtz"
        invalid.write_text("not xml", encoding="utf-8")
        valid = write_rtz(self.directory, "valid.rtz")

        batch = convert_many(
            [invalid, valid],
            lambda _source, _output: ExistingFileAction.OVERWRITE,
        )

        self.assertFalse(batch.cancelled)
        self.assertEqual(2, len(batch.results))
        failed, succeeded = batch.results
        self.assertEqual(ConversionStatus.FAILED, failed.status)
        self.assertEqual(invalid, failed.source)
        self.assertIsNone(failed.output)
        self.assertIsNotNone(failed.error)
        self.assertFalse(destination_for(invalid).exists())
        self.assertEqual(ConversionStatus.SUCCESS, succeeded.status)
        self.assertEqual(valid, succeeded.source)
        self.assertEqual(destination_for(valid), succeeded.output)
        self.assertTrue(succeeded.output.exists())


if __name__ == "__main__":
    unittest.main()
