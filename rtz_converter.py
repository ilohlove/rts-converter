"""Core RTZ parsing and CSV conversion services.

The module intentionally uses only the Python standard library so the desktop
application can be packaged as a small, self-contained Windows executable.
"""

from __future__ import annotations

import csv
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable


CSV_FIELDS = (
    "route_name",
    "sequence",
    "waypoint_id",
    "waypoint_name",
    "latitude",
    "longitude",
    "leg_geometry_type",
)

# Namespace URI -> version declared on the root route element.
SUPPORTED_NAMESPACES = {
    "http://www.cirm.org/RTZ/1/0": "1.0",
    "http://www.cirm.org/RTZ/1/2": "1.2",
}

_GEOMETRY_TYPES = frozenset({"Loxodrome", "Orthodrome"})
_INTEGER_PATTERN = re.compile(r"^[+-]?[0-9]+$")


class RtzConversionError(ValueError):
    """An expected, user-facing RTZ parsing or CSV writing failure."""


class ExistingFileAction(str, Enum):
    """Decision returned by the batch overwrite callback."""

    OVERWRITE = "overwrite"
    SKIP = "skip"
    CANCEL = "cancel"


class OutputFormat(str, Enum):
    """Supported output formats for one conversion batch."""

    CSV = "csv"
    TXT = "txt"


class ConversionStatus(str, Enum):
    """Outcome for one source file in a conversion batch."""

    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class WaypointRow:
    route_name: str
    sequence: int
    waypoint_id: str
    waypoint_name: str
    latitude: str
    longitude: str
    leg_geometry_type: str


@dataclass(frozen=True)
class ParsedRoute:
    source: Path
    route_name: str
    waypoints: tuple[WaypointRow, ...]
    warnings: tuple[str, ...]

    @property
    def rows(self) -> tuple[WaypointRow, ...]:
        """Alias that makes the object convenient for CSV-oriented callers."""

        return self.waypoints


@dataclass(frozen=True)
class ConversionResult:
    source: Path
    output: Path | None
    status: ConversionStatus
    waypoint_count: int = 0
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.status is ConversionStatus.SUCCESS


@dataclass(frozen=True)
class BatchResult:
    results: tuple[ConversionResult, ...]
    cancelled: bool = False

    @property
    def total_count(self) -> int:
        return len(self.results)

    @property
    def success_count(self) -> int:
        return self._count(ConversionStatus.SUCCESS)

    @property
    def converted_count(self) -> int:
        return self.success_count

    @property
    def skipped_count(self) -> int:
        return self._count(ConversionStatus.SKIPPED)

    @property
    def failed_count(self) -> int:
        return self._count(ConversionStatus.FAILED)

    @property
    def error_count(self) -> int:
        return self.failed_count

    def _count(self, status: ConversionStatus) -> int:
        return sum(result.status is status for result in self.results)


PathInput = str | os.PathLike[str]
OverwriteDecider = Callable[[Path, Path], ExistingFileAction]


def parse_rtz(path: PathInput) -> ParsedRoute:
    """Parse and validate a CIRM RTZ 1.0 or 1.2 route file."""

    source = _coerce_path(path, "RTZ")
    try:
        root = ET.parse(source).getroot()
    except ET.ParseError as exc:
        raise RtzConversionError(
            f"XML RTZ không hợp lệ trong '{source}': {exc} / "
            f"Invalid RTZ XML in '{source}': {exc}"
        ) from exc
    except OSError as exc:
        raise RtzConversionError(
            f"Không thể đọc file RTZ '{source}': {exc} / "
            f"Cannot read RTZ file '{source}': {exc}"
        ) from exc

    namespace, local_name = _split_tag(root.tag)
    if local_name != "route":
        raise RtzConversionError(
            "Phần tử gốc phải là 'route' / The root element must be 'route'."
        )

    expected_version = SUPPORTED_NAMESPACES.get(namespace)
    if expected_version is None:
        shown_namespace = namespace or "(không có / none)"
        raise RtzConversionError(
            f"Namespace RTZ không được hỗ trợ: {shown_namespace}. "
            "Chỉ hỗ trợ RTZ 1.0 và 1.2 / "
            f"Unsupported RTZ namespace: {shown_namespace}. "
            "Only RTZ 1.0 and 1.2 are supported."
        )

    declared_version = root.get("version")
    if declared_version != expected_version:
        shown_version = declared_version if declared_version is not None else "(missing)"
        raise RtzConversionError(
            f"Phiên bản route '{shown_version}' không khớp namespace RTZ "
            f"{expected_version} / Route version '{shown_version}' does not match "
            f"the RTZ {expected_version} namespace."
        )

    qualified = lambda name: f"{{{namespace}}}{name}"
    route_info = root.find(qualified("routeInfo"))
    if route_info is None:
        raise RtzConversionError(
            "Thiếu phần tử routeInfo / Missing routeInfo element."
        )

    route_name = route_info.get("routeName")
    if route_name is None or not route_name.strip():
        raise RtzConversionError(
            "routeName là bắt buộc và không được để trống / "
            "routeName is required and must not be blank."
        )

    waypoints_element = root.find(qualified("waypoints"))
    if waypoints_element is None:
        raise RtzConversionError(
            "Thiếu phần tử waypoints / Missing waypoints element."
        )

    waypoint_elements = waypoints_element.findall(qualified("waypoint"))
    if len(waypoint_elements) < 2:
        raise RtzConversionError(
            "Tuyến phải có ít nhất 2 waypoint / "
            "The route must contain at least 2 waypoints."
        )

    default_geometry = _default_geometry(waypoints_element, qualified)
    seen_ids: set[int] = set()
    rows: list[WaypointRow] = []

    for sequence, waypoint in enumerate(waypoint_elements, start=1):
        waypoint_id_text, waypoint_id_number = _parse_waypoint_id(
            waypoint.get("id"), sequence
        )
        if waypoint_id_number in seen_ids:
            raise RtzConversionError(
                f"ID waypoint bị trùng: {waypoint_id_text} / "
                f"Duplicate waypoint ID: {waypoint_id_text}."
            )
        seen_ids.add(waypoint_id_number)

        position = waypoint.find(qualified("position"))
        if position is None:
            raise RtzConversionError(
                f"Waypoint {waypoint_id_text} thiếu position / "
                f"Waypoint {waypoint_id_text} is missing position."
            )

        latitude = _validate_coordinate(
            position.get("lat"),
            "latitude",
            waypoint_id_text,
            Decimal("-90"),
            Decimal("90"),
            upper_inclusive=True,
        )
        longitude = _validate_coordinate(
            position.get("lon"),
            "longitude",
            waypoint_id_text,
            Decimal("-180"),
            Decimal("180"),
            upper_inclusive=False,
        )

        geometry = default_geometry
        leg = waypoint.find(qualified("leg"))
        if leg is not None and "geometryType" in leg.attrib:
            geometry = _validate_geometry(
                leg.get("geometryType"), f"waypoint {waypoint_id_text}"
            )

        rows.append(
            WaypointRow(
                route_name=route_name,
                sequence=sequence,
                waypoint_id=waypoint_id_text,
                waypoint_name=waypoint.get("name", ""),
                latitude=latitude,
                longitude=longitude,
                leg_geometry_type=geometry,
            )
        )

    return ParsedRoute(
        source=source,
        route_name=route_name,
        waypoints=tuple(rows),
        # Vendor extensions are intentionally outside the CSV/TXT export
        # contract. In particular, Transas ActivePath may reference IDs that
        # are not present in the core waypoint list.
        warnings=(),
    )


def write_csv_atomic(parsed: ParsedRoute, output: PathInput) -> Path:
    """Write a parsed route as UTF-8 BOM CSV and atomically replace output."""

    output_path = _coerce_path(output, "CSV")

    def write_content(handle) -> None:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in parsed.waypoints:
            writer.writerow({field: getattr(row, field) for field in CSV_FIELDS})

    return _write_text_atomic(output_path, "CSV", write_content)


def write_txt_atomic(parsed: ParsedRoute, output: PathInput) -> Path:
    """Write latitude/longitude-only DMM TXT and atomically replace output."""

    output_path = _coerce_path(output, "TXT")

    def write_content(handle) -> None:
        for row in parsed.waypoints:
            latitude = format_coordinate_dmm(row.latitude, "latitude")
            longitude = format_coordinate_dmm(row.longitude, "longitude")
            handle.write(f"{latitude}\t{longitude}\r\n")

    return _write_text_atomic(output_path, "TXT", write_content)


def format_coordinate_dmm(value: str, axis: str) -> str:
    """Format a decimal-degree coordinate as degrees and decimal minutes."""

    normalized_axis = axis.lower().strip()
    if normalized_axis not in {"latitude", "lat", "longitude", "lon"}:
        raise RtzConversionError(
            f"Trục tọa độ không hợp lệ: {axis!r} / Invalid coordinate axis: {axis!r}."
        )

    try:
        coordinate = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RtzConversionError(
            f"Tọa độ không hợp lệ: {value!r} / Invalid coordinate: {value!r}."
        ) from exc
    if not coordinate.is_finite():
        raise RtzConversionError(
            f"Tọa độ không hữu hạn: {value!r} / Coordinate is not finite: {value!r}."
        )

    is_latitude = normalized_axis in {"latitude", "lat"}
    hemisphere = (
        ("N" if coordinate >= 0 else "S")
        if is_latitude
        else ("E" if coordinate >= 0 else "W")
    )
    absolute = abs(coordinate)
    degrees = int(absolute)
    minutes = ((absolute - Decimal(degrees)) * Decimal("60")).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    if minutes >= Decimal("60.0"):
        degrees += 1
        minutes = Decimal("0.0")

    return f"{degrees}° {format(minutes, '04.1f')}' {hemisphere}"


def _write_text_atomic(output_path: Path, kind: str, write_content: Callable) -> Path:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            dir=output_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            write_content(temporary_file)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        os.replace(temporary_path, output_path)
        temporary_path = None
        return output_path
    except Exception as exc:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(exc, RtzConversionError):
            raise
        raise RtzConversionError(
            f"Không thể ghi file {kind} '{output_path}': {exc} / "
            f"Cannot write {kind} file '{output_path}': {exc}"
        ) from exc


def convert_rtz(
    source: PathInput,
    output: PathInput | None = None,
    *,
    output_format: OutputFormat | str = OutputFormat.CSV,
) -> ConversionResult:
    """Parse one RTZ file and write the selected output format."""

    source_path = _coerce_path(source, "RTZ")
    normalized_format = _normalize_output_format(output_format)
    output_path = (
        destination_for(source_path, normalized_format)
        if output is None
        else _coerce_path(output, normalized_format.value.upper())
    )
    if _paths_refer_to_same_location(source_path, output_path):
        raise RtzConversionError(
            f"File {normalized_format.value.upper()} đầu ra không được trùng file RTZ "
            "nguồn / The output file must not be the source RTZ file."
        )

    parsed = parse_rtz(source_path)
    if normalized_format is OutputFormat.CSV:
        write_csv_atomic(parsed, output_path)
    else:
        write_txt_atomic(parsed, output_path)
    return ConversionResult(
        source=source_path,
        output=output_path,
        status=ConversionStatus.SUCCESS,
        waypoint_count=len(parsed.waypoints),
        warnings=parsed.warnings,
    )


def destination_for(
    source: PathInput,
    output_format: OutputFormat | str = OutputFormat.CSV,
) -> Path:
    """Return the same-directory destination for an RTZ source path."""

    normalized_format = _normalize_output_format(output_format)
    return _coerce_path(source, "RTZ").with_suffix(f".{normalized_format.value}")


def destination_for_txt(source: PathInput) -> Path:
    """Return the same-directory TXT destination for an RTZ source path."""

    return destination_for(source, OutputFormat.TXT)


def convert_many(
    sources: Iterable[PathInput],
    overwrite_decider: OverwriteDecider,
    *,
    output_format: OutputFormat | str = OutputFormat.CSV,
) -> BatchResult:
    """Convert several files in one format, honoring overwrite choices."""

    normalized_format = _normalize_output_format(output_format)
    results: list[ConversionResult] = []
    cancelled = False

    for source_value in sources:
        try:
            source = _coerce_path(source_value, "RTZ")
            output = destination_for(source, normalized_format)

            if output.exists():
                action_value = overwrite_decider(source, output)
                try:
                    action = ExistingFileAction(action_value)
                except (TypeError, ValueError) as exc:
                    raise RtzConversionError(
                        f"Lựa chọn xử lý file đã tồn tại không hợp lệ: {action_value!r} / "
                        f"Invalid existing-file action: {action_value!r}."
                    ) from exc

                if action is ExistingFileAction.CANCEL:
                    cancelled = True
                    break
                if action is ExistingFileAction.SKIP:
                    results.append(
                        ConversionResult(
                            source=source,
                            output=output,
                            status=ConversionStatus.SKIPPED,
                        )
                    )
                    continue

            results.append(
                convert_rtz(source, output, output_format=normalized_format)
            )
        except Exception as exc:
            try:
                failed_source = _coerce_path(source_value, "RTZ")
            except RtzConversionError:
                failed_source = Path("<invalid path>")
            message = str(exc)
            if not isinstance(exc, RtzConversionError):
                message = (
                    f"Lỗi không mong đợi: {exc} / Unexpected conversion error: {exc}"
                )
            results.append(
                ConversionResult(
                    source=failed_source,
                    output=None,
                    status=ConversionStatus.FAILED,
                    error=message,
                )
            )

    return BatchResult(results=tuple(results), cancelled=cancelled)


def _coerce_path(value: PathInput, kind: str) -> Path:
    try:
        return Path(value)
    except (TypeError, ValueError) as exc:
        raise RtzConversionError(
            f"Đường dẫn {kind} không hợp lệ: {value!r} / "
            f"Invalid {kind} path: {value!r}."
        ) from exc


def _normalize_output_format(value: OutputFormat | str) -> OutputFormat:
    if isinstance(value, OutputFormat):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower().lstrip(".")
        try:
            return OutputFormat(normalized)
        except ValueError:
            pass
    raise RtzConversionError(
        f"Định dạng đầu ra không hợp lệ: {value!r} / "
        f"Invalid output format: {value!r}. Use CSV or TXT."
    )


def _split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith("{") and "}" in tag:
        namespace, local_name = tag[1:].split("}", 1)
        return namespace, local_name
    return "", tag


def _parse_waypoint_id(value: str | None, sequence: int) -> tuple[str, int]:
    if value is None or not value.strip():
        raise RtzConversionError(
            f"Waypoint thứ {sequence} thiếu ID / Waypoint {sequence} is missing an ID."
        )

    normalized = value.strip()
    if not _INTEGER_PATTERN.fullmatch(normalized):
        raise RtzConversionError(
            f"ID waypoint phải là số nguyên không âm: {value!r} / "
            f"Waypoint ID must be a non-negative integer: {value!r}."
        )

    number = int(normalized)
    if number < 0:
        raise RtzConversionError(
            f"ID waypoint không được âm: {value!r} / "
            f"Waypoint ID must not be negative: {value!r}."
        )
    return value, number


def _validate_coordinate(
    value: str | None,
    label: str,
    waypoint_id: str,
    lower: Decimal,
    upper: Decimal,
    *,
    upper_inclusive: bool,
) -> str:
    if value is None or not value.strip():
        raise RtzConversionError(
            f"Waypoint {waypoint_id} thiếu {label} / "
            f"Waypoint {waypoint_id} is missing {label}."
        )

    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise RtzConversionError(
            f"{label} không hợp lệ tại waypoint {waypoint_id}: {value!r} / "
            f"Invalid {label} at waypoint {waypoint_id}: {value!r}."
        ) from exc

    out_of_range = not number.is_finite()
    if not out_of_range:
        outside_upper = number > upper if upper_inclusive else number >= upper
        out_of_range = number < lower or outside_upper
    if out_of_range:
        closing = "]" if upper_inclusive else ")"
        raise RtzConversionError(
            f"{label} ngoài phạm vi tại waypoint {waypoint_id}: {value!r}; "
            f"yêu cầu [{lower}, {upper}{closing} / "
            f"{label} is out of range at waypoint {waypoint_id}: {value!r}; "
            f"expected [{lower}, {upper}{closing}."
        )
    return value


def _validate_geometry(value: str | None, location: str) -> str:
    if value not in _GEOMETRY_TYPES:
        raise RtzConversionError(
            f"geometryType không hợp lệ tại {location}: {value!r}. "
            "Chỉ chấp nhận Loxodrome hoặc Orthodrome / "
            f"Invalid geometryType at {location}: {value!r}. "
            "Only Loxodrome or Orthodrome is accepted."
        )
    return value


def _default_geometry(waypoints: ET.Element, qualified: Callable[[str], str]) -> str:
    default_waypoint = waypoints.find(qualified("defaultWaypoint"))
    if default_waypoint is None:
        return ""
    default_leg = default_waypoint.find(qualified("leg"))
    if default_leg is None or "geometryType" not in default_leg.attrib:
        return ""
    return _validate_geometry(default_leg.get("geometryType"), "defaultWaypoint")


def _paths_refer_to_same_location(left: Path, right: Path) -> bool:
    left_normalized = os.path.normcase(os.path.abspath(left))
    right_normalized = os.path.normcase(os.path.abspath(right))
    return left_normalized == right_normalized


__all__ = [
    "BatchResult",
    "CSV_FIELDS",
    "ConversionResult",
    "ConversionStatus",
    "ExistingFileAction",
    "OutputFormat",
    "ParsedRoute",
    "RtzConversionError",
    "SUPPORTED_NAMESPACES",
    "WaypointRow",
    "convert_many",
    "convert_rtz",
    "destination_for",
    "destination_for_txt",
    "format_coordinate_dmm",
    "parse_rtz",
    "write_csv_atomic",
    "write_txt_atomic",
]
