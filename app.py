#!/usr/bin/env python3
"""WX notification helper for already-split WeChat windows on macOS."""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import zipfile
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


APP_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
APP_NAME = "SSAI-WX 通知小工具"
APP_VERSION = "V1.0.0"
CONTACT_WECHAT = "sanshengya88"


def get_user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    return Path.home() / f".{APP_NAME}"


USER_DATA_DIR = get_user_data_dir()
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = USER_DATA_DIR / "send_log.jsonl"
ICON_FILE = RESOURCE_DIR / "assets" / "app_icon.png"
DOCUMENT_MEDIA_DIR = USER_DATA_DIR / "document_media"
SUPPORTED_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".heic", ".webp"}
SUPPORTED_VIDEOS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
SUPPORTED_MEDIA = SUPPORTED_IMAGES | SUPPORTED_VIDEOS
SUPPORTED_DOCUMENTS = {".txt", ".docx", ".doc"}
WECHAT_PROCESS_NAMES = ("WeChat", "微信")
GENERIC_WECHAT_WINDOW_TITLES = {"微信", "WeChat"}
JOB_START_DELAY_MS = 500
JOB_COOLDOWN_MIN_MS = 1400
JOB_COOLDOWN_MAX_MS = 2200
DUPLICATE_JOB_WINDOW_SECONDS = 1.2
MAX_DOCUMENT_SEGMENT_CHARS = 300
DOCUMENT_CONFIRM_THRESHOLD = 20
MEDIA_SEND_WAIT_SECONDS = 6.0


@dataclass(frozen=True)
class WeChatWindow:
    process_name: str
    index: int
    title: str


@dataclass
class SendTarget:
    window: WeChatWindow
    status: str = "pending"
    error: str | None = None


@dataclass
class SendJob:
    batch_id: str
    text: str
    images: list[Path]
    targets: list[SendTarget]
    dry_run: bool
    shown_in_chat: bool = False
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class DocumentContent:
    text_segments: list[str]
    media_paths: list[Path]


class MissingDependency(RuntimeError):
    pass


def require_module(module_name: str, install_name: str | None = None) -> object:
    try:
        return __import__(module_name)
    except ImportError as exc:
        package = install_name or module_name
        raise MissingDependency(f"缺少依赖 {package}。请先安装项目依赖。") from exc


def run_osascript(script: str) -> str:
    proc = subprocess.run(
        ["osascript", "-e", script],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "unknown AppleScript error"
        raise RuntimeError(detail)
    return proc.stdout.strip()


def escape_applescript_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def detect_wechat_process() -> str | None:
    for process_name in WECHAT_PROCESS_NAMES:
        script = f'tell application "System Events" to exists process "{process_name}"'
        try:
            if run_osascript(script).lower() == "true":
                return process_name
        except RuntimeError:
            continue
    return None


def list_wechat_windows() -> list[WeChatWindow]:
    windows: list[WeChatWindow] = []
    for process_name in WECHAT_PROCESS_NAMES:
        script = f'''
tell application "System Events"
    if exists process "{process_name}" then
        tell process "{process_name}"
            set outputLines to {{}}
            repeat with i from 1 to count of windows
                set w to window i
                try
                    set windowName to name of w as text
                    set end of outputLines to "{process_name}|||§IDX§|||" & i & "|||§TITLE§|||" & windowName
                end try
            end repeat
        end tell
        set AppleScript's text item delimiters to linefeed
        return outputLines as text
    end if
end tell
return ""
'''
        try:
            output = run_osascript(script)
        except RuntimeError:
            continue
        for line in output.splitlines():
            parts = line.split("|||§IDX§|||", 1)
            if len(parts) != 2:
                continue
            proc = parts[0]
            idx_title = parts[1].split("|||§TITLE§|||", 1)
            if len(idx_title) != 2:
                continue
            try:
                index = int(idx_title[0])
            except ValueError:
                continue
            title = idx_title[1].strip()
            if not title or title in GENERIC_WECHAT_WINDOW_TITLES:
                continue
            windows.append(WeChatWindow(proc, index, title))
        if windows:
            break
    return windows


def raise_wechat_window(window: WeChatWindow) -> None:
    process_name = escape_applescript_text(window.process_name)
    script = f'''
tell application "{process_name}" to activate
delay 0.2
tell application "System Events"
    tell process "{process_name}"
        set frontmost to true
        try
            perform action "AXRaise" of window {window.index}
        on error
            set value of attribute "AXMain" of window {window.index} to true
        end try
    end tell
end tell
'''
    run_osascript(script)


def set_text_clipboard(text: str) -> None:
    subprocess.run(["pbcopy"], input=text, text=True, check=True)


def clear_clipboard() -> None:
    subprocess.run(["pbcopy"], input="", text=True, check=True)


def set_files_clipboard(paths: Iterable[Path]) -> None:
    path_list = list(paths)
    try:
        require_module("AppKit", "pyobjc-framework-Cocoa")
        from AppKit import NSPasteboard, NSURL

        urls = [NSURL.fileURLWithPath_(str(path)) for path in path_list]
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        if not pasteboard.writeObjects_(urls):
            raise RuntimeError("无法把图片写入系统剪贴板")
    except MissingDependency:
        aliases = []
        for path in path_list:
            escaped_path = escape_applescript_text(str(path))
            aliases.append(f'(POSIX file "{escaped_path}" as alias)')
        run_osascript("set the clipboard to {" + ", ".join(aliases) + "}")


def press_paste_and_enter() -> None:
    run_osascript(
        '''
tell application "System Events"
    keystroke "v" using command down
    delay 0.35
    key code 36
end tell
'''
    )


def append_log(entry: dict) -> None:
    entry = {"logged_at": datetime.now().isoformat(timespec="seconds"), **entry}
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def text_summary(text: str, limit: int = 80) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[:limit] + "..."


def normalize_message_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def split_long_text(text: str, max_chars: int = MAX_DOCUMENT_SEGMENT_CHARS) -> list[str]:
    normalized = normalize_message_text(text)
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    pieces = re.split(r"([。！？!?；;，,\n])", normalized)
    units: list[str] = []
    for index in range(0, len(pieces), 2):
        unit = pieces[index]
        if index + 1 < len(pieces):
            unit += pieces[index + 1]
        unit = unit.strip()
        if unit:
            units.append(unit)

    segments: list[str] = []
    current = ""
    for unit in units:
        while len(unit) > max_chars:
            if current:
                segments.append(current)
                current = ""
            segments.append(unit[:max_chars])
            unit = unit[max_chars:].strip()
        if not unit:
            continue
        candidate = current + unit if not current else current + unit
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                segments.append(current)
            current = unit
    if current:
        segments.append(current)
    return segments


def split_paragraphs(paragraphs: list[str], max_chars: int = MAX_DOCUMENT_SEGMENT_CHARS) -> list[str]:
    segments: list[str] = []
    for paragraph in paragraphs:
        segments.extend(split_long_text(paragraph, max_chars))
    return [segment for segment in segments if segment]


def read_txt_paragraphs(path: Path) -> list[str]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = path.read_text(encoding=encoding)
            return [item.strip() for item in re.split(r"\n\s*\n+", text) if item.strip()]
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error:
        raise RuntimeError(f"无法识别 TXT 编码：{last_error}") from last_error
    return []


def read_docx_paragraphs(path: Path) -> list[str]:
    try:
        from docx import Document
    except ImportError as exc:
        raise MissingDependency("缺少依赖 python-docx。请先安装项目依赖。") from exc
    document = Document(str(path))
    return [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]


def extract_docx_media(path: Path) -> list[Path]:
    target_root = DOCUMENT_MEDIA_DIR / f"{path.stem}_{time.time_ns()}"
    target_root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        media_names = [
            name
            for name in names
            if name.startswith("word/media/") and Path(name).suffix.lower() in SUPPORTED_MEDIA
        ]
        if not media_names:
            return []

        rel_targets: dict[str, str] = {}
        if "word/_rels/document.xml.rels" in names:
            rels_xml = archive.read("word/_rels/document.xml.rels").decode("utf-8", errors="ignore")
            for rel_id, target in re.findall(r'Id="([^"]+)".*?Target="([^"]+)"', rels_xml):
                if target.startswith("media/"):
                    rel_targets[rel_id] = "word/" + target

        ordered_names: list[str] = []
        if "word/document.xml" in names:
            document_xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
            for rel_id in re.findall(r'r:(?:embed|link)="([^"]+)"', document_xml):
                media_name = rel_targets.get(rel_id)
                if media_name in media_names and media_name not in ordered_names:
                    ordered_names.append(media_name)

        for media_name in media_names:
            if media_name not in ordered_names:
                ordered_names.append(media_name)

        extracted: list[Path] = []
        for index, media_name in enumerate(ordered_names, start=1):
            suffix = Path(media_name).suffix.lower()
            output_path = target_root / f"{index:03d}_{Path(media_name).name}"
            with archive.open(media_name) as source, output_path.open("wb") as target:
                shutil.copyfileobj(source, target)
            if suffix in SUPPORTED_MEDIA:
                extracted.append(output_path)
        return extracted


def load_document_content(path: Path) -> DocumentContent:
    suffix = path.suffix.lower()
    if suffix == ".doc":
        raise RuntimeError("暂不支持 .doc 老格式，请先另存为 .docx 后再上传。")
    media_paths: list[Path] = []
    if suffix == ".txt":
        paragraphs = read_txt_paragraphs(path)
    elif suffix == ".docx":
        paragraphs = read_docx_paragraphs(path)
        media_paths = extract_docx_media(path)
    else:
        raise RuntimeError(f"不支持的文档格式：{suffix}")
    segments = split_paragraphs(paragraphs)
    if not segments and not media_paths:
        raise RuntimeError("文档里没有可发送的文字或媒体内容。")
    return DocumentContent(segments, media_paths)


def load_document_segments(path: Path) -> list[str]:
    return load_document_content(path).text_segments


def send_to_current_window(text: str, images: list[Path]) -> None:
    try:
        if text.strip():
            set_text_clipboard(text)
            time.sleep(0.2)
            press_paste_and_enter()
            time.sleep(1.0)
        if images:
            set_files_clipboard(images)
            time.sleep(0.3)
            press_paste_and_enter()
            wait_seconds = MEDIA_SEND_WAIT_SECONDS if any(
                path.suffix.lower() in SUPPORTED_VIDEOS for path in images
            ) else 1.8
            time.sleep(wait_seconds)
    finally:
        clear_clipboard()
        time.sleep(0.2)


def log_window_result(
    window: WeChatWindow,
    status: str,
    text: str,
    images: list[Path],
    error: str | None = None,
    batch_id: str | None = None,
) -> None:
    append_log(
        {
            "batch_id": batch_id,
            "status": status,
            "process_name": window.process_name,
            "window_index": window.index,
            "window_title": window.title,
            "text_summary": text_summary(text),
            "image_files": [path.name for path in images],
            "error": error,
        }
    )


class SendWorker(QObject):
    log_message = Signal(str)
    target_status = Signal(int, str)
    job_finished = Signal(object, int, int, int)
    finished = Signal()

    def __init__(self, job: SendJob) -> None:
        super().__init__()
        self.job = job
        self.stop_requested = False
        self.pause_requested = False

    def request_stop(self) -> None:
        self.stop_requested = True

    def request_pause(self) -> None:
        self.pause_requested = True

    def run(self) -> None:
        sent = skipped = failed = 0
        self.log_message.emit(f"开始批次 {self.job.batch_id}")

        try:
            for index, target in enumerate(self.job.targets):
                if self.stop_requested:
                    self.log_message.emit("已收到停止指令，当前任务停止继续处理")
                    break
                if self.pause_requested:
                    self.log_message.emit("已收到暂停指令，当前任务暂停继续处理")
                    break
                target.status = "sending"
                self.target_status.emit(index, "sending")

                try:
                    raise_wechat_window(target.window)
                    time.sleep(0.45)
                    if self.pause_requested:
                        target.status = "pending"
                        self.target_status.emit(index, "pending")
                        break
                    if self.stop_requested:
                        target.status = "skipped"
                        self.target_status.emit(index, "skipped")
                        break
                except Exception as exc:
                    target.status = "failed"
                    target.error = str(exc)
                    failed += 1
                    self.target_status.emit(index, "failed")
                    log_window_result(
                        target.window,
                        "raise_window_failed",
                        self.job.text,
                        self.job.images,
                        str(exc),
                        self.job.batch_id,
                    )
                    self.log_message.emit(f"失败：{target.window.title} - {exc}")
                    continue

                try:
                    if not self.job.dry_run:
                        send_to_current_window(self.job.text, self.job.images)
                    target.status = "dry_run" if self.job.dry_run else "sent"
                    sent += 1
                    self.target_status.emit(index, target.status)
                    log_window_result(
                        target.window,
                        target.status,
                        self.job.text,
                        self.job.images,
                        batch_id=self.job.batch_id,
                    )
                    self.log_message.emit(
                        f"{'试运行通过' if self.job.dry_run else '已发送'}：{target.window.title}"
                    )
                    if index < len(self.job.targets) - 1:
                        wait_seconds = random.uniform(1.0, 4.0)
                        self.log_message.emit(f"等待 {wait_seconds:.1f} 秒后继续")
                        wait_until = time.monotonic() + wait_seconds
                        while time.monotonic() < wait_until:
                            if self.stop_requested or self.pause_requested:
                                break
                            time.sleep(0.1)
                except Exception as exc:
                    target.status = "failed"
                    target.error = str(exc)
                    failed += 1
                    self.target_status.emit(index, "failed")
                    log_window_result(
                        target.window,
                        "send_failed",
                        self.job.text,
                        self.job.images,
                        str(exc),
                        self.job.batch_id,
                    )
                    self.log_message.emit(f"失败：{target.window.title} - {exc}")
        finally:
            self.job_finished.emit(self.job, sent, skipped, failed)
            self.finished.emit()


class TargetRow(QFrame):
    STATUS_LABELS = {
        "pending": "待发送",
        "sending": "发送中",
        "sent": "成功",
        "dry_run": "试运行",
        "skipped": "跳过",
        "failed": "失败",
    }

    def __init__(self, title: str, status: str = "pending") -> None:
        super().__init__()
        self.setObjectName("targetRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        self.dot = QLabel("✓")
        self.dot.setObjectName("targetDot")
        self.dot.setAlignment(Qt.AlignCenter)
        self.dot.setFixedSize(18, 18)
        layout.addWidget(self.dot)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("targetTitle")
        self.title_label.setToolTip(title)
        self.title_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout.addWidget(self.title_label, 1)

        self.status_label = QLabel()
        self.status_label.setObjectName("targetStatus")
        layout.addWidget(self.status_label)
        self.set_status(status)

    def set_status(self, status: str) -> None:
        self.setProperty("status", status)
        self.dot.setProperty("status", status)
        self.status_label.setText(self.STATUS_LABELS.get(status, status))
        self.style().unpolish(self)
        self.style().polish(self)
        self.dot.style().unpolish(self.dot)
        self.dot.style().polish(self.dot)


class ChatBubble(QFrame):
    def __init__(
        self,
        text: str,
        images: list[Path],
        target_count: int,
        dry_run: bool,
        status_text: str | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName("bubbleWrap")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 12)
        outer.setSpacing(6)

        time_label = QLabel(datetime.now().strftime("%H:%M"))
        time_label.setObjectName("timeLabel")
        time_label.setAlignment(Qt.AlignCenter)
        outer.addWidget(time_label)

        row = QHBoxLayout()
        row.addStretch(1)
        bubble = QFrame()
        bubble.setObjectName("outBubble")
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(14, 10, 14, 10)
        bubble_layout.setSpacing(8)

        if text.strip():
            message = QLabel(text.strip())
            message.setObjectName("bubbleText")
            message.setWordWrap(True)
            message.setMaximumWidth(390)
            bubble_layout.addWidget(message)

        if images:
            image_row = QHBoxLayout()
            image_row.setSpacing(6)
            for path in images[:4]:
                thumb = QLabel()
                thumb.setObjectName("imageThumb")
                thumb.setFixedSize(76, 58)
                pix = QPixmap(str(path))
                if not pix.isNull():
                    thumb.setPixmap(pix.scaled(76, 58, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
                else:
                    thumb.setText(path.name[:8])
                    thumb.setAlignment(Qt.AlignCenter)
                image_row.addWidget(thumb)
            if len(images) > 4:
                more = QLabel(f"+{len(images) - 4}")
                more.setObjectName("imageMore")
                more.setFixedSize(42, 58)
                more.setAlignment(Qt.AlignCenter)
                image_row.addWidget(more)
            bubble_layout.addLayout(image_row)

        row.addWidget(bubble)
        outer.addLayout(row)

        status = status_text or ("试运行：不会真正发送" if dry_run else f"正在同步至 {target_count} 个窗口")
        status_label = QLabel(f"✓ {status}")
        status_label.setObjectName("bubbleStatus")
        status_label.setAlignment(Qt.AlignRight)
        outer.addWidget(status_label)


class SystemMessage(QFrame):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.setObjectName("systemMsgWrap")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 8)
        outer.setSpacing(0)
        outer.addStretch(1)

        label = QLabel(text)
        label.setObjectName("systemMsg")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        label.setMinimumWidth(260)
        label.setMaximumWidth(420)
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        label.adjustSize()
        outer.addWidget(label)
        outer.addStretch(1)


class SendTextEdit(QTextEdit):
    def __init__(self, send_callback, image_callback) -> None:
        super().__init__()
        self.send_callback = send_callback
        self.image_callback = image_callback
        self.setAcceptDrops(True)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
            event.modifiers() & Qt.ShiftModifier
        ):
            self.send_callback()
            return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event) -> None:
        if self.extract_image_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        paths = self.extract_image_paths(event.mimeData())
        if paths:
            self.image_callback(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    @staticmethod
    def extract_image_paths(mime_data) -> list[Path]:
        if not mime_data.hasUrls():
            return []
        paths: list[Path] = []
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.suffix.lower() in SUPPORTED_IMAGES:
                paths.append(path)
        return paths


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(680, 486)
        self.setMinimumSize(640, 460)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        self.image_paths: list[Path] = []
        self.document_path: Path | None = None
        self.document_segments: list[str] = []
        self.document_media_paths: list[Path] = []
        self.detected_windows: list[WeChatWindow] = []
        self.target_rows: list[TargetRow] = []
        self.send_queue: deque[SendJob] = deque()
        self.worker_thread: QThread | None = None
        self.worker: SendWorker | None = None
        self.is_sending = False
        self.is_creating_job = False
        self.queue_start_pending = False
        self.stop_requested = False
        self.stop_cleared_count = 0
        self.is_paused = False
        self.last_job_signature: tuple | None = None
        self.last_job_created_at = 0.0

        self.build_ui()
        self.apply_styles()
        self.render_targets([])
        self.add_system_message("输入通知后点击“同步发送”，消息会同步到拆分窗口。")

    def build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QFrame()
        self.header.setObjectName("header")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(10, 6, 10, 6)
        header_layout.setSpacing(8)

        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("greenDot")
        header_layout.addWidget(self.status_dot)

        self.meta_label = QLabel(f"{APP_VERSION} · 微信:{CONTACT_WECHAT}")
        self.meta_label.setObjectName("brandLabel")
        header_layout.addWidget(self.meta_label)

        self.target_count_label = QLabel("已检测 0 个拆分群窗口")
        self.target_count_label.setObjectName("targetCount")
        header_layout.addWidget(self.target_count_label, 1)

        self.refresh_button = QPushButton("↻ 刷新窗口")
        self.refresh_button.clicked.connect(self.refresh_windows)
        header_layout.addWidget(self.refresh_button)

        self.topmost_check = QCheckBox("置顶")
        self.topmost_check.setChecked(True)
        self.topmost_check.toggled.connect(self.setWindowStaysOnTop)
        header_layout.addWidget(self.topmost_check)
        root.addWidget(self.header)

        main = QHBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        root.addLayout(main, 1)

        left = QFrame()
        left.setObjectName("leftPane")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setObjectName("chatScroll")
        self.chat_body = QWidget()
        self.chat_body.setObjectName("chatBody")
        self.chat_layout = QVBoxLayout(self.chat_body)
        self.chat_layout.setContentsMargins(16, 12, 16, 12)
        self.chat_layout.setSpacing(8)
        self.chat_layout.addStretch(1)
        self.chat_scroll.setWidget(self.chat_body)
        left_layout.addWidget(self.chat_scroll, 1)

        composer = QFrame()
        composer.setObjectName("composer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(10, 8, 10, 8)
        composer_layout.setSpacing(8)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        input_box = QFrame()
        input_box.setObjectName("inputBox")
        input_box_layout = QVBoxLayout(input_box)
        input_box_layout.setContentsMargins(10, 8, 10, 8)
        input_box_layout.setSpacing(6)

        text_row = QHBoxLayout()
        text_row.setSpacing(8)
        self.add_attachment_button = QPushButton("+")
        self.add_attachment_button.setObjectName("attachButton")
        self.add_attachment_button.setToolTip("添加图片或文档")
        self.add_attachment_button.clicked.connect(self.add_attachments)
        self.add_attachment_button.setFixedSize(30, 30)
        text_row.addWidget(self.add_attachment_button, 0, Qt.AlignTop)

        self.message_edit = SendTextEdit(self.start_send, self.add_image_paths)
        self.message_edit.setObjectName("messageEdit")
        self.message_edit.setPlaceholderText("输入通知内容... Enter 发送，Shift+Enter 换行")
        self.message_edit.setFixedHeight(60)
        text_row.addWidget(self.message_edit, 1)
        input_box_layout.addLayout(text_row)

        self.preview_frame = QFrame()
        self.preview_frame.setObjectName("previewFrame")
        self.preview_layout = QHBoxLayout(self.preview_frame)
        self.preview_layout.setContentsMargins(38, 0, 0, 0)
        self.preview_layout.setSpacing(6)
        self.preview_frame.hide()
        input_box_layout.addWidget(self.preview_frame)
        input_row.addWidget(input_box, 1)

        action_column = QVBoxLayout()
        action_column.setSpacing(6)
        action_column.addStretch(1)

        self.send_button = QPushButton("同步发送")
        self.send_button.setObjectName("sendButton")
        self.send_button.clicked.connect(self.start_send)
        self.send_button.setFixedSize(78, 30)
        action_column.addWidget(self.send_button)

        self.pause_button = QPushButton("暂停")
        self.pause_button.setObjectName("pauseButton")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.pause_button.setFixedSize(78, 30)
        self.pause_button.setEnabled(False)
        action_column.addWidget(self.pause_button)

        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.clicked.connect(self.stop_sending)
        self.stop_button.setFixedSize(78, 30)
        self.stop_button.setEnabled(False)
        action_column.addWidget(self.stop_button)
        action_column.addStretch(1)
        input_row.addLayout(action_column)
        composer_layout.addLayout(input_row)

        self.attachment_label = QLabel("未选择图片")
        self.attachment_label.setObjectName("mutedLabel")
        composer_layout.addWidget(self.attachment_label)

        self.document_frame = QFrame()
        self.document_frame.setObjectName("documentFrame")
        document_layout = QHBoxLayout(self.document_frame)
        document_layout.setContentsMargins(8, 6, 8, 6)
        document_layout.setSpacing(8)
        self.document_label = QLabel("")
        self.document_label.setObjectName("documentLabel")
        self.document_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        document_layout.addWidget(self.document_label, 1)
        self.remove_document_button = QPushButton("×")
        self.remove_document_button.setObjectName("removePreviewButton")
        self.remove_document_button.setToolTip("移除文档")
        self.remove_document_button.setFixedSize(18, 18)
        self.remove_document_button.clicked.connect(self.clear_document)
        document_layout.addWidget(self.remove_document_button)
        self.document_frame.hide()
        composer_layout.addWidget(self.document_frame)

        self.queue_label = QLabel("队列：空")
        self.queue_label.setObjectName("mutedLabel")
        composer_layout.addWidget(self.queue_label)

        self.log_title = QLabel("同步记录")
        self.log_title.setObjectName("sideTitle")
        composer_layout.addWidget(self.log_title)
        self.log_list = QListWidget()
        self.log_list.setObjectName("logList")
        self.log_list.setFixedHeight(78)
        self.log_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        composer_layout.addWidget(self.log_list)
        left_layout.addWidget(composer)
        main.addWidget(left, 1)

        right = QFrame()
        right.setObjectName("rightPane")
        right.setFixedWidth(190)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(10)

        self.target_title_label = QLabel("目标窗口（0/0）")
        self.target_title_label.setObjectName("sideTitle")
        right_layout.addWidget(self.target_title_label)

        self.target_list = QVBoxLayout()
        self.target_list.setSpacing(0)
        right_layout.addLayout(self.target_list, 1)

        settings = QFrame()
        settings.setObjectName("settingsBox")
        settings_layout = QVBoxLayout(settings)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(8)

        random_interval_label = QLabel("发送间隔：自动随机 1-4 秒")
        random_interval_label.setObjectName("mutedLabel")
        settings_layout.addWidget(random_interval_label)

        self.dry_run_check = QCheckBox("试运行，不真正发送")
        settings_layout.addWidget(self.dry_run_check)

        right_layout.addWidget(settings)

        main.addWidget(right)

    def apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                font-family: Arial, "PingFang SC", "Microsoft YaHei";
                color: #111111;
                background: #f6f6f6;
            }
            #header {
                background: #fbfbfb;
                border-bottom: 1px solid #dedede;
            }
            #greenDot {
                color: #07C160;
                font-size: 13px;
                font-weight: 700;
            }
            #targetCount {
                font-size: 13px;
                color: #222222;
            }
            #brandLabel {
                color: #111111;
                font-size: 12px;
                font-weight: 700;
                background: transparent;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #d9d9d9;
                border-radius: 7px;
                padding: 5px 10px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #f1f3f5;
            }
            QPushButton:disabled {
                color: #9aa0a6;
                background: #f0f0f0;
            }
            #sendButton {
                background: #07C160;
                color: white;
                border: none;
                border-radius: 7px;
                font-size: 12px;
                font-weight: 700;
                padding: 0;
            }
            #sendButton:hover {
                background: #05a853;
            }
            #stopButton {
                background: #ffffff;
                color: #b42318;
                border: 1px solid #f3b4b4;
                border-radius: 7px;
                font-size: 12px;
                font-weight: 700;
                padding: 0;
            }
            #stopButton:hover {
                background: #fff1f1;
            }
            #stopButton:disabled {
                color: #c7c7c7;
                background: #f5f5f5;
                border: 1px solid #e1e1e1;
            }
            #pauseButton {
                background: #ffffff;
                color: #945b00;
                border: 1px solid #f1d49b;
                border-radius: 7px;
                font-size: 12px;
                font-weight: 700;
                padding: 0;
            }
            #pauseButton:hover {
                background: #fff8e8;
            }
            #pauseButton:disabled {
                color: #c7c7c7;
                background: #f5f5f5;
                border: 1px solid #e1e1e1;
            }
            #attachButton {
                background: #ffffff;
                border: 1px solid #d9d9d9;
                border-radius: 18px;
                color: #4b5563;
                font-size: 22px;
                font-weight: 400;
                padding: 0;
            }
            #attachButton:hover {
                background: #f1f3f5;
            }
            QCheckBox {
                background: transparent;
                font-size: 12px;
            }
            #leftPane {
                background: #f5f5f5;
            }
            #rightPane {
                background: #ffffff;
                border-left: 1px solid #e6e6e6;
            }
            #chatScroll {
                border: none;
                background: #f5f5f5;
            }
            #chatBody {
                background: #f5f5f5;
            }
            #composer {
                background: #ffffff;
                border-top: 1px solid #e5e5e5;
            }
            #inputBox {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
            }
            #messageEdit {
                background: #ffffff;
                border: none;
                padding: 0;
                font-size: 13px;
            }
            #messageEdit:focus {
                border: none;
            }
            #mutedLabel {
                color: #8b929c;
                font-size: 12px;
                background: transparent;
            }
            #previewFrame {
                background: transparent;
            }
            #documentFrame {
                background: #f7faf8;
                border: 1px solid #d9f0df;
                border-radius: 8px;
            }
            #documentLabel {
                background: transparent;
                color: #1f7a3f;
                font-size: 12px;
            }
            #previewItem {
                background: transparent;
            }
            #inputPreviewThumb, #inputPreviewMore {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                color: #6b7280;
                font-size: 11px;
            }
            #removePreviewButton {
                background: #f2f4f7;
                border: 1px solid #e5e7eb;
                border-radius: 9px;
                color: #6b7280;
                padding: 0;
                font-size: 12px;
            }
            #removePreviewButton:hover {
                background: #fee2e2;
                color: #b42318;
            }
            #sideTitle {
                font-size: 13px;
                font-weight: 700;
                background: transparent;
            }
            #targetRow {
                background: #ffffff;
                border-bottom: 1px solid #eeeeee;
            }
            #targetTitle {
                font-size: 12px;
                background: transparent;
            }
            #targetStatus {
                color: #8b929c;
                font-size: 11px;
                background: transparent;
            }
            #targetDot {
                background: #07C160;
                color: white;
                border-radius: 9px;
                font-size: 10px;
                font-weight: 700;
            }
            #targetDot[status="failed"] {
                background: #e5484d;
            }
            #targetDot[status="sending"] {
                background: #f59f00;
            }
            #targetDot[status="pending"] {
                background: #d1d5db;
            }
            #settingsBox {
                background: transparent;
            }
            #logList {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
                font-size: 11px;
                padding: 4px;
                outline: none;
            }
            #logList::item {
                border-radius: 6px;
                padding: 4px 6px;
                margin: 1px 2px;
                color: #555555;
            }
            #logList::item:selected {
                background: #eef8f1;
                color: #1f7a3f;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 6px 2px 6px 0;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: #d6dbe1;
                min-height: 24px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical:hover {
                background: #bfc6cf;
            }
            QScrollBar:horizontal {
                background: transparent;
                height: 0;
                border: none;
            }
            QScrollBar::handle:horizontal {
                background: transparent;
                height: 0;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
                background: transparent;
                border: none;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: transparent;
            }
            #systemMsg {
                background: #e4e4e4;
                color: #666666;
                border-radius: 8px;
                padding: 5px 12px;
                font-size: 11px;
                min-height: 20px;
            }
            #systemMsgWrap {
                background: transparent;
            }
            #outBubble {
                background: #95ec69;
                border-radius: 8px;
            }
            #bubbleText {
                background: transparent;
                font-size: 13px;
            }
            #bubbleStatus, #timeLabel {
                color: #8a8f98;
                background: transparent;
                font-size: 11px;
            }
            #imageThumb, #imageMore {
                background: #ffffff;
                border: 1px solid rgba(0,0,0,0.08);
                border-radius: 6px;
                color: #4b5563;
            }
            """
        )

    def setWindowStaysOnTop(self, enabled: bool) -> None:
        self.setWindowFlag(Qt.WindowStaysOnTopHint, enabled)
        self.show()

    def render_targets(self, windows: list[WeChatWindow]) -> None:
        while self.target_list.count():
            item = self.target_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.target_rows = []
        if not windows:
            empty = QLabel("点击“刷新窗口”读取已拆分群聊")
            empty.setObjectName("mutedLabel")
            empty.setWordWrap(True)
            self.target_list.addWidget(empty)
            self.target_count_label.setText("已检测 0 个拆分群窗口")
            self.target_title_label.setText("目标窗口（0/0）")
            return

        for window in windows:
            row = TargetRow(window.title)
            self.target_rows.append(row)
            self.target_list.addWidget(row)
        self.target_list.addStretch(1)
        self.target_count_label.setText(f"已检测 {len(windows)} 个拆分群窗口")
        self.target_title_label.setText(f"目标窗口（{len(windows)}/{len(windows)}）")

    def add_system_message(self, text: str) -> None:
        label = SystemMessage(text)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, label, alignment=Qt.AlignCenter)
        self.scroll_chat_to_bottom()

    def add_chat_bubble(
        self,
        text: str,
        images: list[Path],
        target_count: int,
        dry_run: bool,
        status_text: str | None = None,
    ) -> None:
        bubble = ChatBubble(text, images, target_count, dry_run, status_text)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        self.scroll_chat_to_bottom()

    def scroll_chat_to_bottom(self) -> None:
        QApplication.processEvents()
        scrollbar = self.chat_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def add_log(self, message: str) -> None:
        self.log_list.addItem(f"{datetime.now().strftime('%H:%M:%S')}  {message}")
        self.log_list.scrollToBottom()
        QApplication.processEvents()

    def update_attachment_summary(self) -> None:
        if not self.image_paths:
            self.attachment_label.setText("未选择图片或文档")
        elif len(self.image_paths) == 1:
            self.attachment_label.setText(f"已选择 1 张图片：{self.image_paths[0].name}")
        else:
            self.attachment_label.setText(f"已选择 {len(self.image_paths)} 张图片")

    def update_queue_label(self) -> None:
        waiting_count = len(self.send_queue)
        if self.stop_requested:
            self.queue_label.setText("队列：正在停止")
        elif self.is_paused:
            self.queue_label.setText(f"队列：已暂停，{waiting_count} 条等待")
        elif self.is_sending and waiting_count:
            self.queue_label.setText(f"队列：当前发送中，{waiting_count} 条等待")
        elif waiting_count:
            self.queue_label.setText(f"队列：{waiting_count} 条等待发送")
        elif self.is_sending:
            self.queue_label.setText("队列：当前发送中")
        else:
            self.queue_label.setText("队列：空")

    def update_document_card(self) -> None:
        if not self.document_path:
            self.document_frame.hide()
            return
        self.document_label.setText(
            f"文档：{self.document_path.name} · 文字 {len(self.document_segments)} 条 · 媒体 {len(self.document_media_paths)} 个"
        )
        self.document_label.setToolTip(str(self.document_path))
        self.document_frame.show()

    def add_attachments(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图片或文档",
            str(Path.home()),
            "图片或文档 (*.png *.jpg *.jpeg *.gif *.bmp *.tiff *.heic *.webp *.txt *.docx *.doc);;图片文件 (*.png *.jpg *.jpeg *.gif *.bmp *.tiff *.heic *.webp);;文档 (*.txt *.docx *.doc);;所有文件 (*.*)",
        )
        paths = [Path(file_name) for file_name in files]
        document_paths = [path for path in paths if path.suffix.lower() in SUPPORTED_DOCUMENTS]
        image_paths = [path for path in paths if path.suffix.lower() in SUPPORTED_IMAGES]
        if document_paths:
            self.add_document(document_paths[0])
            if len(document_paths) > 1:
                QMessageBox.information(self, "已选择一个文档", "一次只处理一个文档，已使用第一个文档。")
            return
        self.add_image_paths(image_paths)

    def add_document(self, path: Path) -> None:
        try:
            content = load_document_content(path)
        except MissingDependency as exc:
            QMessageBox.critical(self, "缺少依赖", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, "文档读取失败", str(exc))
            return
        self.clear_images()
        self.document_path = path
        self.document_segments = content.text_segments
        self.document_media_paths = content.media_paths
        self.update_document_card()
        self.add_log(
            f"已读取文档：{path.name}，文字 {len(content.text_segments)} 条，媒体 {len(content.media_paths)} 个"
        )

    def clear_document(self) -> None:
        self.document_path = None
        self.document_segments = []
        self.document_media_paths = []
        self.update_document_card()

    def add_image_paths(self, paths: list[Path]) -> None:
        if paths and self.document_path:
            self.clear_document()
        for path in paths:
            if path.suffix.lower() not in SUPPORTED_IMAGES:
                QMessageBox.warning(self, "不支持的文件", f"已跳过：{path.name}")
                continue
            if path not in self.image_paths:
                self.image_paths.append(path)
        self.update_attachment_summary()
        self.render_image_previews()

    def clear_images(self) -> None:
        self.image_paths.clear()
        self.update_attachment_summary()
        self.render_image_previews()

    def render_image_previews(self) -> None:
        while self.preview_layout.count():
            item = self.preview_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.image_paths:
            self.preview_frame.hide()
            return

        self.preview_frame.show()
        for path in self.image_paths[:6]:
            item = QFrame()
            item.setObjectName("previewItem")
            item_layout = QHBoxLayout(item)
            item_layout.setContentsMargins(0, 0, 0, 0)
            item_layout.setSpacing(0)

            thumb = QLabel()
            thumb.setObjectName("inputPreviewThumb")
            thumb.setFixedSize(44, 34)
            thumb.setToolTip(path.name)
            pix = QPixmap(str(path))
            if pix.isNull():
                thumb.setText(path.name[:5])
                thumb.setAlignment(Qt.AlignCenter)
            else:
                thumb.setPixmap(pix.scaled(44, 34, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
            item_layout.addWidget(thumb)

            remove_button = QPushButton("×")
            remove_button.setObjectName("removePreviewButton")
            remove_button.setToolTip("移除这张图片")
            remove_button.setFixedSize(18, 18)
            remove_button.clicked.connect(lambda _checked=False, p=path: self.remove_image_path(p))
            item_layout.addWidget(remove_button, 0, Qt.AlignTop)
            self.preview_layout.addWidget(item)
        if len(self.image_paths) > 6:
            more = QLabel(f"+{len(self.image_paths) - 6}")
            more.setObjectName("inputPreviewMore")
            more.setFixedSize(34, 34)
            more.setAlignment(Qt.AlignCenter)
            self.preview_layout.addWidget(more)
        self.preview_layout.addStretch(1)

    def remove_image_path(self, path: Path) -> None:
        self.image_paths = [item for item in self.image_paths if item != path]
        self.update_attachment_summary()
        self.render_image_previews()

    def refresh_windows(self) -> None:
        try:
            self.detected_windows = list_wechat_windows()
        except RuntimeError as exc:
            self.detected_windows = []
            self.render_targets([])
            self.add_log(f"读取窗口失败：{exc}")
            return
        self.render_targets(self.detected_windows)
        if self.detected_windows:
            self.add_log(f"检测到 {len(self.detected_windows)} 个拆分窗口")
        else:
            self.add_log("未检测到拆分窗口")

    def validate_before_send(
        self,
        text: str,
        images: list[Path],
        refresh_windows: bool = True,
    ) -> bool:
        if not text.strip() and not images:
            QMessageBox.warning(self, "没有内容", "请输入文字或添加至少一张图片。")
            return False
        missing = [str(path) for path in images if not path.exists()]
        if missing:
            QMessageBox.critical(self, "图片不存在", "以下图片不存在：\n" + "\n".join(missing))
            return False
        if refresh_windows and not detect_wechat_process():
            QMessageBox.critical(self, "微信未运行", "请先打开并登录微信 Mac 客户端。")
            return False
        if refresh_windows:
            self.refresh_windows()
        if not self.detected_windows:
            QMessageBox.warning(self, "没有微信窗口", "请先把目标群聊拆分成独立窗口，再刷新。")
            return False
        titles = [window.title for window in self.detected_windows]
        if refresh_windows and len(titles) != len(set(titles)):
            return QMessageBox.question(
                self,
                "发现重复窗口标题",
                "检测到重复窗口标题，可能会影响确认。仍然继续吗？",
            ) == QMessageBox.Yes
        return True

    def start_send(self) -> None:
        if self.is_creating_job:
            return
        self.is_creating_job = True
        self.send_button.setEnabled(False)

        try:
            text_snapshot = self.message_edit.toPlainText().strip()
            images_snapshot = list(self.image_paths)
            document_path_snapshot = self.document_path
            document_segments_snapshot = list(self.document_segments)
            document_media_snapshot = list(self.document_media_paths)
            has_document = bool(document_path_snapshot and (document_segments_snapshot or document_media_snapshot))
            validation_text = document_segments_snapshot[0] if document_segments_snapshot else text_snapshot
            validation_images = document_media_snapshot if has_document and not validation_text else images_snapshot
            if not self.validate_before_send(
                validation_text,
                validation_images,
                refresh_windows=not self.is_sending,
            ):
                self.release_job_creation_lock(delay_ms=0)
                return

            dry_run_snapshot = self.dry_run_check.isChecked()
            target_windows_snapshot = list(self.detected_windows)
            jobs: list[SendJob] = []
            batch_prefix = datetime.now().strftime("%Y%m%d%H%M%S%f")

            if has_document:
                outgoing_texts = [segment for segment in document_segments_snapshot if segment.strip()]
                if text_snapshot:
                    outgoing_texts.insert(0, text_snapshot)
                total_document_tasks = len(outgoing_texts) + len(document_media_snapshot)
                if total_document_tasks > DOCUMENT_CONFIRM_THRESHOLD:
                    decision = QMessageBox.question(
                        self,
                        "文档拆分确认",
                        f"文档将拆分为 {total_document_tasks} 条任务并依次发送，是否继续？",
                        QMessageBox.Yes | QMessageBox.No,
                    )
                    if decision != QMessageBox.Yes:
                        self.release_job_creation_lock(delay_ms=0)
                        return
                for index, segment in enumerate(outgoing_texts, start=1):
                    jobs.append(
                        SendJob(
                            batch_id=f"{batch_prefix}-{index:03d}",
                            text=segment,
                            images=[],
                            targets=[SendTarget(window) for window in target_windows_snapshot],
                            dry_run=dry_run_snapshot,
                        )
                    )
                for media_index, media_path in enumerate(document_media_snapshot, start=1):
                    jobs.append(
                        SendJob(
                            batch_id=f"{batch_prefix}-m{media_index:03d}",
                            text="",
                            images=[media_path],
                            targets=[SendTarget(window) for window in target_windows_snapshot],
                            dry_run=dry_run_snapshot,
                        )
                    )
            else:
                targets_snapshot = [SendTarget(window) for window in target_windows_snapshot]
                job_signature = self.make_job_signature(
                    text_snapshot,
                    images_snapshot,
                    targets_snapshot,
                    dry_run_snapshot,
                )
                now = time.monotonic()
                if (
                    job_signature == self.last_job_signature
                    and now - self.last_job_created_at < DUPLICATE_JOB_WINDOW_SECONDS
                ):
                    self.add_log("已忽略重复点击：同一条内容刚刚加入过队列")
                    self.release_job_creation_lock()
                    return
                self.last_job_signature = job_signature
                self.last_job_created_at = now
                jobs.append(
                    SendJob(
                        batch_id=batch_prefix,
                        text=text_snapshot,
                        images=images_snapshot,
                        targets=targets_snapshot,
                        dry_run=dry_run_snapshot,
                    )
                )
            self.message_edit.clear()
            self.clear_images()
            if has_document:
                self.clear_document()
            self.send_queue.extend(jobs)
            if self.is_sending:
                self.update_queue_label()
                self.add_system_message(
                    f"已加入队列 {len(jobs)} 条，等待同步至 {len(target_windows_snapshot)} 个窗口"
                )
                self.add_log(f"已加入队列：{len(jobs)} 条（等待 {len(self.send_queue)} 条）")
                self.release_job_creation_lock()
                return
            self.add_system_message(
                f"已创建 {len(jobs)} 条任务，准备同步至 {len(target_windows_snapshot)} 个窗口"
            )
            self.schedule_queue_processing(JOB_START_DELAY_MS)
            self.release_job_creation_lock()
        except Exception:
            self.release_job_creation_lock(delay_ms=0)
            raise

    def make_job_signature(
        self,
        text: str,
        images: list[Path],
        targets: list[SendTarget],
        dry_run: bool,
    ) -> tuple:
        return (
            text,
            tuple(str(path.resolve()) for path in images),
            tuple((target.window.process_name, target.window.index, target.window.title) for target in targets),
            dry_run,
        )

    def release_job_creation_lock(self, delay_ms: int = 300) -> None:
        QTimer.singleShot(delay_ms, self.finish_job_creation_lock)

    def finish_job_creation_lock(self) -> None:
        self.is_creating_job = False
        self.send_button.setEnabled(True)
        self.send_button.setText("加入队列" if self.is_sending else "同步发送")
        self.stop_button.setEnabled(self.is_sending or self.queue_start_pending or bool(self.send_queue))
        self.pause_button.setEnabled(self.is_sending or self.queue_start_pending or bool(self.send_queue))
        self.pause_button.setText("继续" if self.is_paused else "暂停")

    def schedule_queue_processing(self, delay_ms: int) -> None:
        if self.stop_requested or self.is_paused or self.queue_start_pending or self.worker_thread is not None:
            return
        self.queue_start_pending = True
        self.is_sending = True
        self.send_button.setEnabled(True)
        self.send_button.setText("加入队列")
        self.stop_button.setEnabled(True)
        self.pause_button.setEnabled(True)
        self.pause_button.setText("暂停")
        self.update_queue_label()
        QTimer.singleShot(delay_ms, self.start_scheduled_queue_job)

    def start_scheduled_queue_job(self) -> None:
        self.queue_start_pending = False
        if self.stop_requested:
            self.finish_stop()
            return
        if self.is_paused:
            self.update_queue_label()
            return
        self.process_next_queued_job()

    def process_next_queued_job(self) -> None:
        if self.stop_requested:
            self.finish_stop()
            return
        if self.is_paused:
            self.update_queue_label()
            return
        if self.worker_thread is not None or self.queue_start_pending:
            return
        if not self.send_queue:
            self.is_sending = False
            self.send_button.setEnabled(True)
            self.send_button.setText("同步发送")
            self.stop_button.setEnabled(False)
            self.pause_button.setEnabled(False)
            self.pause_button.setText("暂停")
            self.update_queue_label()
            return

        job = self.send_queue.popleft()
        self.is_sending = True
        self.send_button.setEnabled(True)
        self.send_button.setText("加入队列")
        self.stop_button.setEnabled(True)
        self.pause_button.setEnabled(True)
        self.pause_button.setText("暂停")
        self.render_targets([target.window for target in job.targets])
        self.update_queue_label()
        if not job.shown_in_chat:
            self.add_chat_bubble(job.text, job.images, len(job.targets), job.dry_run)
            job.shown_in_chat = True

        thread = QThread(self)
        worker = SendWorker(job)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log_message.connect(self.add_log)
        worker.target_status.connect(self.update_target_status)
        worker.job_finished.connect(self.on_worker_job_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self.on_worker_thread_finished)
        self.worker_thread = thread
        self.worker = worker
        thread.start()

    def toggle_pause(self) -> None:
        if self.is_paused:
            self.resume_sending()
            return
        self.pause_sending()

    def pause_sending(self) -> None:
        if not (self.is_sending or self.queue_start_pending or self.send_queue):
            return
        self.is_paused = True
        if self.worker:
            self.worker.request_pause()
        self.pause_button.setText("继续")
        self.pause_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.add_log("已请求暂停，当前动作结束后暂停队列")
        self.add_system_message("已请求暂停，当前动作结束后暂停队列")
        self.update_queue_label()

    def resume_sending(self) -> None:
        self.is_paused = False
        self.pause_button.setText("暂停")
        self.add_log("已继续队列")
        self.add_system_message("已继续队列")
        self.update_queue_label()
        if self.worker_thread is None and not self.queue_start_pending and self.send_queue:
            self.schedule_queue_processing(300)

    def stop_sending(self) -> None:
        if not (self.is_sending or self.queue_start_pending or self.send_queue):
            return
        cleared_count = len(self.send_queue)
        self.send_queue.clear()
        self.stop_cleared_count = cleared_count
        self.stop_requested = True
        if self.worker:
            self.worker.request_stop()
        self.add_log(f"已请求停止，正在清空等待任务 {cleared_count} 条")
        self.add_system_message(f"已请求停止，正在清空等待任务 {cleared_count} 条")
        self.update_queue_label()
        self.stop_button.setEnabled(False)
        if self.queue_start_pending and self.worker_thread is None:
            self.finish_stop()

    def finish_stop(self) -> None:
        self.send_queue.clear()
        cleared_count = self.stop_cleared_count
        self.stop_cleared_count = 0
        self.queue_start_pending = False
        self.stop_requested = False
        self.is_paused = False
        self.is_sending = False
        if self.worker_thread is None:
            self.worker = None
        self.send_button.setEnabled(True)
        self.send_button.setText("同步发送")
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.pause_button.setText("暂停")
        self.update_queue_label()
        self.add_log(f"停止完成，队列已清空（清空 {cleared_count} 条等待任务）")
        self.add_system_message("停止完成，队列已清空")

    def update_target_status(self, index: int, status: str) -> None:
        if index < len(self.target_rows):
            self.target_rows[index].set_status(status)

    def on_worker_job_finished(self, job: SendJob, sent: int, skipped: int, failed: int) -> None:
        if self.is_paused and not self.stop_requested:
            remaining_targets = [
                SendTarget(target.window)
                for target in job.targets
                if target.status in {"pending", "sending"}
            ]
            if remaining_targets:
                resumed_job = SendJob(
                    batch_id=f"{job.batch_id}-resume",
                    text=job.text,
                    images=job.images,
                    targets=remaining_targets,
                    dry_run=job.dry_run,
                    shown_in_chat=True,
                )
                self.send_queue.appendleft(resumed_job)
            self.add_system_message(f"已暂停，当前任务已完成 {sent} 个窗口，剩余 {len(remaining_targets)} 个窗口")
            self.add_log(f"已暂停：当前任务完成 {sent}，剩余 {len(remaining_targets)}")
            self.update_queue_label()
            return
        verb = "试运行完成" if job.dry_run else "已同步发送"
        self.add_system_message(f"{verb}至 {sent} 个窗口，跳过 {skipped} 个，失败 {failed} 个")
        self.add_log(f"完成：成功/试运行 {sent}，跳过 {skipped}，失败 {failed}")

    def on_worker_thread_finished(self) -> None:
        self.worker_thread = None
        self.worker = None
        if self.stop_requested:
            self.finish_stop()
            return
        if self.is_paused:
            if not self.send_queue:
                self.is_paused = False
                self.is_sending = False
                self.send_button.setEnabled(True)
                self.send_button.setText("同步发送")
                self.pause_button.setEnabled(False)
                self.pause_button.setText("暂停")
                self.stop_button.setEnabled(False)
                self.update_queue_label()
                return
            self.is_sending = bool(self.send_queue)
            self.send_button.setEnabled(True)
            self.send_button.setText("加入队列" if self.send_queue else "同步发送")
            self.pause_button.setEnabled(True)
            self.pause_button.setText("继续")
            self.stop_button.setEnabled(bool(self.send_queue))
            self.update_queue_label()
            return
        if self.send_queue:
            cooldown_ms = random.randint(JOB_COOLDOWN_MIN_MS, JOB_COOLDOWN_MAX_MS)
            self.add_log(
                f"任务间冷却 {cooldown_ms / 1000:.1f} 秒后继续（剩余 {len(self.send_queue)} 条）"
            )
            self.schedule_queue_processing(cooldown_ms)
            return
        self.is_sending = False
        self.send_button.setEnabled(True)
        self.send_button.setText("同步发送")
        self.stop_button.setEnabled(False)
        self.pause_button.setEnabled(False)
        self.pause_button.setText("暂停")
        self.update_queue_label()


def main() -> int:
    if sys.platform != "darwin":
        print("此工具第一版仅支持 macOS。")
        return 1
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    if ICON_FILE.exists():
        app.setWindowIcon(QIcon(str(ICON_FILE)))
    window = MainWindow()
    if ICON_FILE.exists():
        window.setWindowIcon(QIcon(str(ICON_FILE)))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
