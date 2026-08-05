# RTS Converter

Ứng dụng Windows song ngữ chuyển file tuyến hàng hải CIRM RTZ 1.0/1.2 sang CSV hoặc TXT chỉ chứa latitude/longitude. Công cụ xử lý nhiều file, ghi output cạnh file nguồn và bản `.exe` không cần cài Python.

Bilingual Windows application that converts CIRM maritime RTZ 1.0/1.2 routes to CSV or TXT containing only latitude/longitude. It supports batch selection, writes output beside each source file, and the packaged `.exe` does not require Python.

## Sử dụng / Usage

1. Mở `dist\RTS Converter.exe` / Open `dist\RTS Converter.exe`.
2. Chọn một hoặc nhiều file bằng **Chọn RTZ / Select RTZ**.
3. Chọn **CSV** hoặc **TXT (Lat/Lon)**.
4. Nhấn **Chuyển đổi / Convert**. Nếu output đã tồn tại, chọn ghi đè, bỏ qua hoặc dừng các file còn lại.
5. Dùng **Cập nhật / Update** để kiểm tra thủ công; ứng dụng cũng kiểm tra bản stable mới khi mở.

Each `Route.rtz` creates `Route.csv` or `Route.txt` in the same directory. Both outputs use UTF-8 BOM. TXT has no header and uses tab-separated DMM coordinates, for example:

```text
35° 25.2' N	139° 40.9' E
35° 25.3' N	139° 42.7' E
```

## CSV

```text
route_name,sequence,waypoint_id,waypoint_name,latitude,longitude,leg_geometry_type
```

`sequence` preserves XML waypoint order. `leg_geometry_type` describes the leg from the preceding waypoint to the current waypoint. Names and coordinate strings are preserved; missing optional fields remain blank.

## Validation

The converter rejects a file when its XML is not maritime RTZ 1.0/1.2, the route name is missing, fewer than two core waypoints exist, IDs are invalid/duplicated, or coordinates are missing, non-finite, or outside WGS84 bounds. Existing output remains unchanged when conversion fails.

Schedules, open attributes, and vendor extensions are not flattened. An `ActivePath` reference to a missing core waypoint produces a warning only; it does not add a row or block the export.

## Updates and releases / Cập nhật

The public source repository is [github.com/ilohlove/rts-converter](https://github.com/ilohlove/rts-converter). Releases use strict `vMAJOR.MINOR.PATCH` tags and stable, immutable GitHub Releases only. The app checks the latest release at every opening and from **Cập nhật / Update**. It shows release notes and asks before downloading.

The updater downloads `RTS Converter.exe` and its `RTS Converter.exe.sha256` manifest over HTTPS, verifies the GitHub asset digest and SHA-256, then stages the file and restarts the application. Releases also carry `RTZ-to-CSV.exe` compatibility assets so version 1.0.0 can migrate atomically to the new filename. If the installed directory is not writable, the app opens the Release page instead. No telemetry is collected.

The executable is unsigned, so Windows SmartScreen may show a warning. The updater uses SHA-256 integrity checks but does not replace Windows code signing. Releases are published by GitHub Actions after a version tag; immutable releases must be enabled once for the repository:

```powershell
gh api --method PUT repos/ilohlove/rts-converter/immutable-releases `
  --header 'Accept: application/vnd.github+json' `
  --header 'X-GitHub-Api-Version: 2026-03-10'
```

## Run from source

Requires Python 3.10+ with Tkinter.

```powershell
python .\rts_converter_app.py
python -m unittest discover -s tests -v
```

Tests use synthetic RTZ fixtures. Local route/sample files are ignored by git and are not part of the public repository.

## Build Windows EXE

Build on 64-bit Windows using the Python architecture intended for distribution:

```powershell
python -m pip install -r .\requirements-build.txt
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The script runs tests, verifies the deterministic application icon, embeds the version from `app_metadata.py`, writes `dist\RTS Converter.exe`, and creates `dist\RTS Converter.exe.sha256`.

## Publish a release

1. Update `APP_VERSION` in `app_metadata.py` and commit the change.
2. Run `powershell -ExecutionPolicy Bypass -File .\publish-release.ps1` from `main`.
3. GitHub Actions verifies the tag, runs tests, builds the x64 GUI executable, and publishes the new and legacy executable/checksum pairs.

The original immutable release is `v1.0.0`; the first fully rebranded release is `v1.1.0`. The tool only extracts route data; always validate the route separately in ECDIS.

## License

MIT. See [LICENSE](LICENSE).
