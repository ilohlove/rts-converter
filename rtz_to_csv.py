"""Tkinter desktop interface for converting CIRM RTZ routes to CSV or TXT."""

from __future__ import annotations

import os
import sys
import tkinter as tk
import threading
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app_metadata import APP_NAME, APP_VERSION
from rtz_converter import (
    BatchResult,
    ConversionResult,
    ConversionStatus,
    ExistingFileAction,
    OutputFormat,
    convert_many,
    destination_for,
)
from updater import (
    DownloadedUpdate,
    ReleaseInfo,
    UpdateError,
    apply_update_from_argv,
    check_for_update,
    download_update,
    launch_update,
    open_release_page,
)


APP_TITLE = f"{APP_NAME} v{APP_VERSION} / RTZ to CSV/TXT"
READY_STATUS = "Sẵn sàng / Ready"
PROCESSING_STATUS = "Đang chuyển đổi / Converting"
NOT_PROCESSED_STATUS = "Chưa xử lý / Not processed"


class RtzToCsvApp:
    """Bilingual Windows utility for batch RTZ conversion."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.selected_paths: list[Path] = []
        self._items_by_path: dict[str, str] = {}
        self._busy = False
        self._update_in_progress = False
        self._pending_release: ReleaseInfo | None = None
        self._download_cancel: threading.Event | None = None
        self._download_dialog: tk.Toplevel | None = None
        self._download_progress: ttk.Progressbar | None = None
        self._download_status = tk.StringVar()

        self.selection_count = tk.StringVar()
        self.output_format = tk.StringVar(value=OutputFormat.CSV.value)
        self.footer_status = tk.StringVar(value=READY_STATUS)

        self._configure_window()
        self._configure_styles()
        self._build_interface()
        self._update_controls()

    def _configure_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry("980x680")
        self.root.minsize(820, 560)
        self.root.option_add("*tearOff", False)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10))
        style.configure("Section.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Toolbar.TButton", padding=(13, 8))
        style.configure("Accent.TButton", padding=(15, 8))
        style.configure("Treeview", rowheight=27, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("Status.TLabel", padding=(8, 5))

    def _build_interface(self) -> None:
        main = ttk.Frame(self.root, padding=(20, 16, 20, 12))
        main.grid(row=0, column=0, sticky="nsew")
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)
        main.columnconfigure(0, weight=1)

        header = ttk.Frame(main)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header,
            text="Tuyến hàng hải CIRM RTZ 1.0/1.2 / CIRM RTZ 1.0/1.2 routes",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))

        toolbar = ttk.Frame(main)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(16, 12))
        toolbar.columnconfigure(7, weight=1)

        self.select_button = ttk.Button(
            toolbar,
            text="Chọn RTZ / Select RTZ",
            command=self.select_files,
            style="Toolbar.TButton",
        )
        self.select_button.grid(row=0, column=0, padx=(0, 8))

        self.convert_button = ttk.Button(
            toolbar,
            text="Chuyển đổi / Convert",
            command=self.convert_selected,
            style="Accent.TButton",
        )
        self.convert_button.grid(row=0, column=1, padx=(0, 8))

        self.clear_button = ttk.Button(
            toolbar,
            text="Xóa / Clear",
            command=self.clear_files,
            style="Toolbar.TButton",
        )
        self.clear_button.grid(row=0, column=2)

        self.update_button = ttk.Button(
            toolbar,
            text="Cập nhật / Update",
            command=self.check_updates_manually,
            style="Toolbar.TButton",
        )
        self.update_button.grid(row=0, column=3, padx=(10, 0))

        ttk.Label(toolbar, text="Định dạng / Format:").grid(
            row=0, column=4, padx=(12, 5)
        )
        self.csv_radio = ttk.Radiobutton(
            toolbar,
            text="CSV",
            value=OutputFormat.CSV.value,
            variable=self.output_format,
            command=self._format_changed,
        )
        self.csv_radio.grid(row=0, column=5, padx=(0, 5))
        self.txt_radio = ttk.Radiobutton(
            toolbar,
            text="TXT (Lat/Lon)",
            value=OutputFormat.TXT.value,
            variable=self.output_format,
            command=self._format_changed,
        )
        self.txt_radio.grid(row=0, column=6, padx=(0, 8))

        ttk.Label(toolbar, textvariable=self.selection_count).grid(
            row=0, column=7, sticky="e"
        )

        ttk.Separator(main, orient="horizontal").grid(
            row=2, column=0, sticky="ew", pady=(0, 12)
        )

        panes = ttk.Panedwindow(main, orient="vertical")
        panes.grid(row=3, column=0, sticky="nsew")

        files_panel = ttk.Frame(panes)
        files_panel.rowconfigure(1, weight=1)
        files_panel.columnconfigure(0, weight=1)
        ttk.Label(
            files_panel,
            text="Tệp đã chọn / Selected files",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 7))

        tree_frame = ttk.Frame(files_panel)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.file_tree = ttk.Treeview(
            tree_frame,
            columns=("file", "folder", "status"),
            show="headings",
            selectmode="extended",
        )
        self.file_tree.heading("file", text="Tên tệp / File")
        self.file_tree.heading("folder", text="Thư mục / Folder")
        self.file_tree.heading("status", text="Trạng thái / Status")
        self.file_tree.column("file", width=230, minwidth=140, stretch=False)
        self.file_tree.column("folder", width=440, minwidth=220, stretch=True)
        self.file_tree.column("status", width=210, minwidth=175, stretch=False)
        self.file_tree.grid(row=0, column=0, sticky="nsew")

        tree_y_scroll = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.file_tree.yview
        )
        tree_y_scroll.grid(row=0, column=1, sticky="ns")
        tree_x_scroll = ttk.Scrollbar(
            tree_frame, orient="horizontal", command=self.file_tree.xview
        )
        tree_x_scroll.grid(row=1, column=0, sticky="ew")
        self.file_tree.configure(
            yscrollcommand=tree_y_scroll.set, xscrollcommand=tree_x_scroll.set
        )
        self.file_tree.tag_configure("success", foreground="#176b3a")
        self.file_tree.tag_configure("skipped", foreground="#5c6268")
        self.file_tree.tag_configure("failed", foreground="#a12622")
        self.file_tree.tag_configure("cancelled", foreground="#8a5a00")

        results_panel = ttk.Frame(panes)
        results_panel.rowconfigure(1, weight=1)
        results_panel.columnconfigure(0, weight=1)
        ttk.Label(
            results_panel,
            text="Kết quả và cảnh báo / Results and warnings",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(9, 7))

        result_frame = ttk.Frame(results_panel)
        result_frame.grid(row=1, column=0, sticky="nsew")
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)

        self.result_text = tk.Text(
            result_frame,
            height=10,
            wrap="word",
            relief="solid",
            borderwidth=1,
            padx=10,
            pady=8,
            font=("Segoe UI", 9),
            background="#ffffff",
            foreground="#202124",
            state="disabled",
        )
        self.result_text.grid(row=0, column=0, sticky="nsew")
        result_scroll = ttk.Scrollbar(
            result_frame, orient="vertical", command=self.result_text.yview
        )
        result_scroll.grid(row=0, column=1, sticky="ns")
        self.result_text.configure(yscrollcommand=result_scroll.set)
        self.result_text.tag_configure("success", foreground="#176b3a")
        self.result_text.tag_configure("warning", foreground="#8a5a00")
        self.result_text.tag_configure("error", foreground="#a12622")
        self.result_text.tag_configure("muted", foreground="#5c6268")
        self.result_text.tag_configure("summary", font=("Segoe UI", 9, "bold"))

        panes.add(files_panel, weight=3)
        panes.add(results_panel, weight=2)

        footer = ttk.Frame(main)
        footer.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(
            footer, textvariable=self.footer_status, style="Status.TLabel"
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            footer,
            text="Dữ liệu phải được xác thực trên ECDIS / Validate data in ECDIS",
            style="Status.TLabel",
        ).grid(row=0, column=1, sticky="e")

        self.root.bind("<Control-o>", lambda _event: self.select_files())
        self.root.bind("<Control-O>", lambda _event: self.select_files())

    @staticmethod
    def _path_key(path: Path) -> str:
        return os.path.normcase(os.path.abspath(os.fspath(path)))

    def select_files(self) -> None:
        if self._busy:
            return

        filenames = filedialog.askopenfilenames(
            parent=self.root,
            title="Chọn tệp RTZ / Select RTZ files",
            filetypes=(
                ("Tệp tuyến RTZ / RTZ route files", "*.rtz"),
                ("Tất cả tệp / All files", "*.*"),
            ),
        )
        if not filenames:
            return

        added = 0
        for filename in filenames:
            path = Path(filename)
            key = self._path_key(path)
            if key in self._items_by_path:
                continue

            item = self.file_tree.insert(
                "",
                "end",
                values=(path.name, str(path.parent), READY_STATUS),
            )
            self.selected_paths.append(path)
            self._items_by_path[key] = item
            added += 1

        if added:
            self.footer_status.set(
                f"Đã thêm {added} tệp / Added {added} file(s)"
            )
        else:
            self.footer_status.set(
                "Các tệp đã có trong danh sách / Files are already selected"
            )
        self._update_controls()

    def clear_files(self) -> None:
        if self._busy:
            return
        self.selected_paths.clear()
        self._items_by_path.clear()
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)
        self._clear_results()
        self.footer_status.set(READY_STATUS)
        self._update_controls()

    def _selected_output_format(self) -> OutputFormat:
        return OutputFormat(self.output_format.get())

    def _format_changed(self) -> None:
        if self._busy:
            return
        self._clear_results()
        for path in self.selected_paths:
            self._set_file_status(path, READY_STATUS)
        selected = self._selected_output_format().value.upper()
        self.footer_status.set(
            f"Đã chọn định dạng {selected} / Selected {selected} format"
        )

    def start_auto_update_check(self) -> None:
        if not self._busy and not self._update_in_progress:
            self._start_update_check(manual=False)

    def check_updates_manually(self) -> None:
        if self._busy or self._update_in_progress:
            return
        self._start_update_check(manual=True)

    def _start_update_check(self, *, manual: bool) -> None:
        self._update_in_progress = True
        self._update_controls()
        self.footer_status.set(
            "Đang kiểm tra cập nhật / Checking for updates..."
        )

        def worker() -> None:
            try:
                release = check_for_update()
                error = None
            except Exception as exc:  # Network errors must not kill the app.
                release = None
                error = exc
            self.root.after(
                0,
                lambda: self._finish_update_check(manual, release, error),
            )

        threading.Thread(target=worker, daemon=True, name="rtz-update-check").start()

    def _finish_update_check(
        self,
        manual: bool,
        release: ReleaseInfo | None,
        error: Exception | None,
    ) -> None:
        self._update_in_progress = False
        self._update_controls()
        if error is not None:
            self.footer_status.set("Chưa kiểm tra được / Update check failed")
            if manual:
                messagebox.showerror(
                    "Lỗi cập nhật / Update error",
                    str(error),
                    parent=self.root,
                )
            return

        if release is None:
            self.footer_status.set(
                f"Đang dùng v{APP_VERSION} / Version v{APP_VERSION} is current"
            )
            if manual:
                messagebox.showinfo(
                    "Cập nhật / Updates",
                    f"Bạn đang dùng phiên bản mới nhất v{APP_VERSION}.\n"
                    f"You are using the latest version v{APP_VERSION}.",
                    parent=self.root,
                )
            return

        if self._busy:
            self._pending_release = release
            self.footer_status.set(
                f"Có bản v{release.version_text}; sẽ hỏi sau khi chuyển đổi / "
                "Update available; prompt will wait until conversion finishes"
            )
            return
        self._prompt_update(release)

    def _prompt_update(self, release: ReleaseInfo) -> None:
        body = release.body.strip()
        if len(body) > 1800:
            body = body[:1800].rstrip() + "..."
        message = (
            f"Có phiên bản mới v{release.version_text} / "
            f"Version v{release.version_text} is available.\n\n"
            f"{body}\n\n"
            "Có / Yes: tải và cài đặt / download and install\n"
            "Không / No: mở trang Release / open release page\n"
            "Hủy / Cancel: để sau / later"
        )
        answer = messagebox.askyesnocancel(
            "Có bản cập nhật / Update available",
            message,
            parent=self.root,
        )
        if answer is True:
            if not getattr(sys, "frozen", False):
                open_release_page()
                self.footer_status.set(
                    "Chạy source: mở trang Release / Source mode: release page opened"
                )
                return
            self._start_download(release)
        elif answer is False:
            open_release_page()
            self.footer_status.set(
                "Đã mở trang Release / Release page opened"
            )

    def _start_download(self, release: ReleaseInfo) -> None:
        self._update_in_progress = True
        self._download_cancel = threading.Event()
        self._download_dialog = tk.Toplevel(self.root)
        dialog = self._download_dialog
        dialog.title("Đang cập nhật / Updating")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.protocol("WM_DELETE_WINDOW", self._cancel_download)
        frame = ttk.Frame(dialog, padding=18)
        frame.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            frame,
            text=f"Đang tải v{release.version_text} / Downloading v{release.version_text}",
        ).grid(row=0, column=0, sticky="w")
        self._download_status.set("Đang chuẩn bị / Preparing...")
        ttk.Label(frame, textvariable=self._download_status).grid(
            row=1, column=0, sticky="w", pady=(8, 8)
        )
        self._download_progress = ttk.Progressbar(
            frame, orient="horizontal", length=340, mode="determinate"
        )
        self._download_progress.grid(row=2, column=0, sticky="ew")
        ttk.Button(
            frame,
            text="Hủy / Cancel",
            command=self._cancel_download,
        ).grid(row=3, column=0, sticky="e", pady=(12, 0))
        dialog.grab_set()
        self._update_controls()

        cancel_event = self._download_cancel

        def progress(done: int, total: int | None) -> bool:
            if cancel_event is not None and cancel_event.is_set():
                return False
            self.root.after(0, lambda: self._update_download_progress(done, total))
            return True

        def worker() -> None:
            try:
                downloaded = download_update(release, progress=progress)
                error = None
            except Exception as exc:
                downloaded = None
                error = exc
            self.root.after(
                0,
                lambda: self._finish_download(downloaded, error),
            )

        threading.Thread(target=worker, daemon=True, name="rtz-update-download").start()

    def _update_download_progress(self, done: int, total: int | None) -> None:
        if self._download_progress is None:
            return
        if total:
            self._download_progress.configure(maximum=total, value=done)
            self._download_status.set(
                f"{done / 1048576:.1f} / {total / 1048576:.1f} MB"
            )
        else:
            self._download_progress.configure(mode="indeterminate")
            self._download_progress.start(12)
            self._download_status.set(f"Đã tải {done / 1048576:.1f} MB")

    def _cancel_download(self) -> None:
        if self._download_cancel is not None:
            self._download_cancel.set()

    def _finish_download(
        self,
        downloaded: DownloadedUpdate | None,
        error: Exception | None,
    ) -> None:
        if self._download_dialog is not None:
            try:
                self._download_dialog.grab_release()
                self._download_dialog.destroy()
            except tk.TclError:
                pass
        self._download_dialog = None
        self._download_progress = None
        self._download_cancel = None
        self._update_in_progress = False
        self._update_controls()
        if error is not None:
            self.footer_status.set("Tải cập nhật thất bại / Update download failed")
            if "cancel" not in str(error).lower() and "hủy" not in str(error).lower():
                messagebox.showerror(
                    "Lỗi tải cập nhật / Update download error",
                    str(error),
                    parent=self.root,
                )
            return
        if downloaded is None:
            return
        try:
            launch_update(downloaded)
        except Exception as exc:
            self.footer_status.set("Không thể cài cập nhật / Update install failed")
            messagebox.showerror(
                "Lỗi cài cập nhật / Update install error",
                str(exc),
                parent=self.root,
            )
            open_release_page()
            return
        self.footer_status.set(
            f"Đang khởi động lại với v{downloaded.release.version_text} / "
            "Restarting with the new version"
        )
        self.root.after(250, self.root.destroy)

    def convert_selected(self) -> None:
        if self._busy or not self.selected_paths:
            return

        self._clear_results()
        self._set_busy(True)
        for path in self.selected_paths:
            self._set_file_status(path, PROCESSING_STATUS)
        self._append_result(
            f"Đang xử lý {len(self.selected_paths)} tệp / "
            f"Processing {len(self.selected_paths)} file(s) as "
            f"{self._selected_output_format().value.upper()}...",
            "muted",
        )
        self.root.update_idletasks()

        try:
            batch = convert_many(
                tuple(self.selected_paths),
                overwrite_decider=self._confirm_overwrite,
                output_format=self._selected_output_format(),
            )
        except Exception as exc:  # Defensive boundary for unexpected core failures.
            self._handle_unexpected_error(exc)
        else:
            self._show_batch_result(batch)
        finally:
            self._set_busy(False)

    def _confirm_overwrite(
        self, source: Path, output: Path
    ) -> ExistingFileAction:
        self._set_file_status(source, "Chờ xác nhận / Awaiting confirmation")
        output_kind = output.suffix.lstrip(".").upper() or "FILE"
        self.footer_status.set(
            f"{output_kind} đã tồn tại: {output.name} / "
            f"{output_kind} already exists"
        )
        self.root.update_idletasks()

        answer = messagebox.askyesnocancel(
            title=(
                f"{output_kind} đã tồn tại / {output_kind} already exists"
            ),
            message=(
                f"Tệp đích đã tồn tại / Destination file already exists:\n"
                f"{output}\n\n"
                "Có / Yes: Ghi đè / Overwrite\n"
                "Không / No: Bỏ qua tệp này / Skip this file\n"
                "Hủy / Cancel: Dừng các tệp còn lại / Stop remaining files"
            ),
            parent=self.root,
        )

        if answer is True:
            self._set_file_status(source, PROCESSING_STATUS)
            self._append_result(
                f"[GHI ĐÈ / OVERWRITE] {output}", "warning"
            )
            return ExistingFileAction.OVERWRITE
        if answer is False:
            self._append_result(f"[BỎ QUA / SKIP] {output}", "muted")
            return ExistingFileAction.SKIP

        self._set_file_status(source, "Đã dừng / Stopped", "cancelled")
        self._append_result(
            "[ĐÃ HỦY / CANCELLED] Dừng các tệp còn lại / "
            "Remaining files will not be processed.",
            "warning",
        )
        return ExistingFileAction.CANCEL

    def _show_batch_result(self, batch: BatchResult) -> None:
        handled_paths: set[str] = set()
        warning_count = 0

        for result in batch.results:
            handled_paths.add(self._path_key(result.source))
            warning_count += len(result.warnings)
            self._show_conversion_result(result)

        unprocessed = 0
        for path in self.selected_paths:
            if self._path_key(path) not in handled_paths:
                unprocessed += 1
                status = "Đã dừng / Not processed" if batch.cancelled else NOT_PROCESSED_STATUS
                tag = "cancelled" if batch.cancelled else ""
                self._set_file_status(path, status, tag)

        converted = sum(
            result.status is ConversionStatus.SUCCESS for result in batch.results
        )
        skipped = sum(
            result.status is ConversionStatus.SKIPPED for result in batch.results
        )
        failed = sum(
            result.status is ConversionStatus.FAILED for result in batch.results
        )
        summary = (
            "TỔNG KẾT / SUMMARY\n"
            f"Tổng / Total: {len(self.selected_paths)}  |  "
            f"Thành công / Converted: {converted}  |  "
            f"Bỏ qua / Skipped: {skipped}  |  "
            f"Lỗi / Errors: {failed}  |  "
            f"Cảnh báo / Warnings: {warning_count}"
        )
        if unprocessed:
            summary += f"  |  Chưa xử lý / Not processed: {unprocessed}"

        self._append_result("", "muted")
        self._append_result(summary, "summary")
        if batch.cancelled:
            self.footer_status.set(
                f"Đã dừng / Stopped: {len(batch.results)}/{len(self.selected_paths)}"
            )
        else:
            self.footer_status.set(
                f"Hoàn tất / Complete: {converted} OK, {failed} lỗi / error(s)"
            )

    def _show_conversion_result(self, result: ConversionResult) -> None:
        source_name = result.source.name
        if result.status is ConversionStatus.SUCCESS:
            status = (
                f"Thành công / Converted ({result.waypoint_count})"
            )
            self._set_file_status(result.source, status, "success")
            output = result.output or destination_for(
                result.source, self._selected_output_format()
            )
            self._append_result(
                f"[THÀNH CÔNG / SUCCESS] {source_name} -> {output} "
                f"({result.waypoint_count} điểm / waypoints)",
                "success",
            )
        elif result.status is ConversionStatus.SKIPPED:
            self._set_file_status(
                result.source, "Bỏ qua / Skipped", "skipped"
            )
            output = result.output or destination_for(
                result.source, self._selected_output_format()
            )
            self._append_result(
                f"[BỎ QUA / SKIPPED] {source_name} -> {output}", "muted"
            )
        else:
            self._set_file_status(result.source, "Lỗi / Error", "failed")
            error = result.error or "Lỗi không xác định / Unknown error"
            self._append_result(
                f"[LỖI / ERROR] {source_name}: {error}", "error"
            )

        for warning in result.warnings:
            self._append_result(
                f"[CẢNH BÁO / WARNING] {source_name}: {warning}", "warning"
            )

    def _handle_unexpected_error(self, exc: Exception) -> None:
        error = str(exc) or exc.__class__.__name__
        for path in self.selected_paths:
            self._set_file_status(path, "Lỗi / Error", "failed")
        self._append_result(
            f"[LỖI / ERROR] Không thể hoàn tất chuyển đổi / "
            f"Could not complete conversion: {error}",
            "error",
        )
        self.footer_status.set("Chuyển đổi thất bại / Conversion failed")
        messagebox.showerror(
            title="Lỗi / Error",
            message=(
                "Không thể hoàn tất chuyển đổi / "
                f"Could not complete conversion:\n\n{error}"
            ),
            parent=self.root,
        )

    def _set_file_status(self, path: Path, status: str, tag: str = "") -> None:
        item = self._items_by_path.get(self._path_key(path))
        if item is None:
            return
        values = list(self.file_tree.item(item, "values"))
        values[2] = status
        self.file_tree.item(item, values=values, tags=(tag,) if tag else ())

    def _clear_results(self) -> None:
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.configure(state="disabled")

    def _append_result(self, message: str, tag: str) -> None:
        self.result_text.configure(state="normal")
        self.result_text.insert("end", message + "\n", tag)
        self.result_text.configure(state="disabled")
        self.result_text.see("end")
        self.root.update_idletasks()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_controls()
        if busy:
            self.footer_status.set(PROCESSING_STATUS)
        elif self._pending_release is not None and not self._update_in_progress:
            release = self._pending_release
            self._pending_release = None
            self.root.after(0, lambda: self._prompt_update(release))

    def _update_controls(self) -> None:
        count = len(self.selected_paths)
        locked = self._busy or self._update_in_progress
        self.selection_count.set(f"{count} tệp / file(s)")
        self.select_button.configure(state="disabled" if locked else "normal")
        self.convert_button.configure(
            state="normal" if count and not locked else "disabled"
        )
        self.clear_button.configure(
            state="normal" if count and not locked else "disabled"
        )
        self.update_button.configure(state="disabled" if locked else "normal")
        radio_state = "disabled" if locked else "normal"
        self.csv_radio.configure(state=radio_state)
        self.txt_radio.configure(state=radio_state)


def main() -> None:
    root = tk.Tk()
    app = RtzToCsvApp(root)
    root.after(1200, app.start_auto_update_check)
    root.mainloop()


if __name__ == "__main__":
    if not apply_update_from_argv():
        main()
