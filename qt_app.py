#!/usr/bin/env python3
"""Phone-style desktop player for MTS Music (PySide6)."""

from __future__ import annotations

import sys

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
    QObject,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from media import (
    load_history,
    record_history,
    track_album,
    track_artist,
    track_cover_url,
)
from mtsmusic import (
    USER_AGENT,
    Api,
    fetch_mix_batch,
    fmt_duration,
    force_ipv4,
    get_uid,
    lrclib_lyrics,
    lyrics_response,
    lyrics_text,
    resolve_stream,
)

APP_QSS = """
QWidget {
    background: #0e0e14;
    color: #f2f2f7;
    font-size: 13px;
}
QLabel { background: transparent; }
#brand { font-size: 15px; font-weight: 600; }
#topStatus { color: #8b8b9e; font-size: 11px; }
#menuBtn {
    background: transparent; border: none; color: #f2f2f7;
    font-size: 20px; padding: 2px 6px; border-radius: 8px;
}
#menuBtn:hover { background: rgba(255,255,255,0.07); }
#titleLabel { font-size: 23px; font-weight: 700; }
#artistLabel { color: #a7a7b8; font-size: 14px; }
#albumLabel { color: #6f6f82; font-size: 12px; }
#timeLabel { color: #8b8b9e; font-size: 11px; }
#queueLabel { color: #6f6f82; font-size: 11px; }
QSlider::groove:horizontal { height: 4px; background: #2a2a36; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #ff3b5c; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 12px; margin: -4px 0; border-radius: 6px; background: #ffffff;
}
QSlider::handle:horizontal:hover { background: #ffd9df; }
#playBtn { background: #ff3b5c; border: none; border-radius: 38px; }
#playBtn:hover { background: #ff5574; }
#prevBtn, #nextBtn { background: rgba(255,255,255,0.05); border: none; border-radius: 22px; }
#prevBtn:hover, #nextBtn:hover { background: rgba(255,255,255,0.11); }
#actionBtn {
    background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.08); border-radius: 16px;
    padding: 8px 18px; font-size: 12px;
}
#actionBtn:hover { background: rgba(255,255,255,0.09); }
#actionBtn[liked="true"] { color: #ff8296; border-color: rgba(255,59,92,0.40); }
#menuBtn:focus, #prevBtn:focus, #nextBtn:focus, #playBtn:focus,
#actionBtn:focus, #backBtn:focus, #searchBtn:focus {
    border: 2px solid #ffffff;
}
QSlider:focus { border: 1px solid #ffffff; border-radius: 4px; }
#drawer { background: #16161f; border-right: 1px solid rgba(255,255,255,0.06); }
#drawerTitle { font-size: 18px; font-weight: 700; padding: 4px 2px 10px 2px; }
#drawerBrand { color: #6f6f82; font-size: 11px; padding: 0 2px 12px 2px; }
#drawer QPushButton {
    text-align: left; background: transparent; border: none; border-radius: 10px;
    padding: 11px 12px; font-size: 14px; color: #e8e8f0;
}
#drawer QPushButton:hover { background: rgba(255,255,255,0.06); }
#drawerQuit { color: #ff7d90; }
#pageTitle { font-size: 17px; font-weight: 700; }
#backBtn {
    background: transparent; border: none; border-radius: 10px;
    font-size: 20px; padding: 2px 10px;
}
#backBtn:hover { background: rgba(255,255,255,0.06); }
#pageStatus { color: #8b8b9e; font-size: 12px; }
QListWidget { background: transparent; border: none; outline: 0; }
QListWidget::item { border: none; }
QLineEdit {
    background: #1b1b25; border: 1px solid rgba(255,255,255,0.07); border-radius: 13px;
    padding: 9px 12px; font-size: 14px; selection-background-color: #ff3b5c;
}
QLineEdit:focus { border-color: rgba(255,59,92,0.53); }
#searchBtn {
    background: #ff3b5c; border: none; border-radius: 13px;
    padding: 9px 16px; font-weight: 600;
}
#searchBtn:hover { background: #ff5574; }
QTextEdit {
    background: transparent; border: none; font-size: 14px; color: #dcdce6;
    selection-background-color: #ff3b5c;
}
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical {
    background: rgba(255,255,255,0.13); border-radius: 4px; min-height: 30px;
}
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
#toast {
    background: #23232f; border: 1px solid rgba(255,255,255,0.09); border-radius: 14px;
    padding: 10px 16px; color: #f2f2f7; font-size: 12px;
}
"""


def media_icon(kind: str, size: int = 64) -> QIcon:
    """Рисует белые иконки плеера, чтобы они выглядели одинаково в любой теме."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#ffffff"))
    s = size / 64
    if kind == "play":
        path = QPainterPath()
        path.moveTo(23 * s, 15 * s)
        path.lineTo(52 * s, 32 * s)
        path.lineTo(23 * s, 49 * s)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "pause":
        painter.drawRoundedRect(QRectF(20 * s, 16 * s, 9 * s, 32 * s), 2 * s, 2 * s)
        painter.drawRoundedRect(QRectF(35 * s, 16 * s, 9 * s, 32 * s), 2 * s, 2 * s)
    elif kind == "prev":
        painter.drawRoundedRect(QRectF(15 * s, 14 * s, 7 * s, 36 * s), 2 * s, 2 * s)
        path = QPainterPath()
        path.moveTo(52 * s, 14 * s)
        path.lineTo(52 * s, 50 * s)
        path.lineTo(25 * s, 32 * s)
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "next":
        painter.drawRoundedRect(QRectF(42 * s, 14 * s, 7 * s, 36 * s), 2 * s, 2 * s)
        path = QPainterPath()
        path.moveTo(12 * s, 14 * s)
        path.lineTo(12 * s, 50 * s)
        path.lineTo(39 * s, 32 * s)
        path.closeSubpath()
        painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def fmt_millis(value: int | float | None) -> str:
    return fmt_duration(int(max(0, value or 0)))


class Task(QRunnable):
    """Небольшая обёртка, чтобы сетевые вызовы не блокировали интерфейс."""

    class Signals(QObject):
        done = Signal(object, object)
        failed = Signal(object, str)

    def __init__(self, fn) -> None:
        super().__init__()
        self.fn = fn
        self.signals = Task.Signals()
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            result = self.fn()
        except SystemExit as exc:
            self._emit_failed(str(exc) or "ошибка")
        except Exception as exc:  # noqa: BLE001 - сообщение показываем в UI
            self._emit_failed(str(exc) or exc.__class__.__name__)
        else:
            try:
                self.signals.done.emit(self, result)
            except RuntimeError:
                # Приложение уже закрылось, сигнал отправлять некуда.
                pass

    def _emit_failed(self, message: str) -> None:
        try:
            self.signals.failed.emit(self, message)
        except RuntimeError:
            pass


class CoverView(QWidget):
    """Обложка со скруглениями и аккуратным плейсхолдером."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self.setMinimumSize(220, 220)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        x = (self.width() - side) / 2
        y = (self.height() - side) / 2
        rect = QRectF(x, y, side, side)

        clip = QPainterPath()
        clip.addRoundedRect(rect, 20, 20)
        painter.setClipPath(clip)

        if self._pixmap and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                int(side),
                int(side),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            dx = x - (scaled.width() - side) / 2
            dy = y - (scaled.height() - side) / 2
            painter.drawPixmap(int(dx), int(dy), scaled)
        else:
            gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
            gradient.setColorAt(0.0, QColor("#252533"))
            gradient.setColorAt(1.0, QColor("#15151d"))
            painter.fillRect(rect, gradient)
            painter.setPen(QColor(255,255,255,48))
            font = QFont()
            font.setPointSize(64)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "♪")

        painter.setClipping(False)
        painter.setPen(QPen(QColor(255,255,255,31), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 20, 20)
        painter.end()


class ClickSlider(QSlider):
    """Перематывает по клику в любую точку полосы, а не шагами."""

    seekRequested = Signal(int)
    scrubStarted = Signal()
    scrubFinished = Signal()

    def _value_at(self, x: int) -> int:
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), x, max(1, self.width())
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.maximum() > 0:
            self.scrubStarted.emit()
            value = self._value_at(event.position().toPoint().x())
            self.setValue(value)
            self.seekRequested.emit(value)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton and self.maximum() > 0:
            value = self._value_at(event.position().toPoint().x())
            self.setValue(value)
            self.seekRequested.emit(value)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.scrubFinished.emit()
        super().mouseReleaseEvent(event)


class TrackDelegate(QStyledItemDelegate):
    """Две строки: название и исполнитель/альбом, длительность справа."""

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        track = index.data(Qt.ItemDataRole.UserRole) or {}
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected:
            painter.fillRect(option.rect, QColor(255, 59, 92, 38))
        elif hovered:
            painter.fillRect(option.rect, QColor(255, 255, 255, 10))

        rect = option.rect.adjusted(14, 9, -14, -9)
        duration = fmt_millis(track.get("durationMs"))
        duration_width = 52
        text_width = max(40, rect.width() - duration_width - 12)

        title_font = QFont(option.font)
        title_font.setPointSizeF(11.5)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor("#f2f2f7"))
        title = QFontMetrics(title_font).elidedText(
            str(track.get("title") or "?"),
            Qt.TextElideMode.ElideRight,
            text_width,
        )
        painter.drawText(
            QRectF(rect.left(), rect.top(), text_width, 22),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            title,
        )

        sub_font = QFont(option.font)
        sub_font.setPointSizeF(10)
        painter.setFont(sub_font)
        painter.setPen(QColor("#8b8b9e"))
        line = f"{track_artist(track)} · {track_album(track)}"
        sub = QFontMetrics(sub_font).elidedText(
            line, Qt.TextElideMode.ElideRight, text_width
        )
        painter.drawText(
            QRectF(rect.left(), rect.top() + 24, text_width, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            sub,
        )

        painter.setPen(QColor("#6f6f82"))
        painter.drawText(
            QRectF(rect.right() - duration_width, rect.top(), duration_width, 22),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            duration,
        )
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(0, 62)


class Scrim(QFrame):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(event)


class QtMusicApp(QMainWindow):
    def __init__(self, api: Api) -> None:
        super().__init__()
        self.api = api
        self.uid = ""
        self.wave: list[dict] = []
        self.queue: list[dict] = []
        self.queue_name = "Моя волна"
        self.current: dict | None = None
        self.current_index = -1
        self.liked_ids: set[str] = set()
        self.favorites: list[dict] = []
        self.history: list[dict] = load_history()
        self.cover_cache: dict[str, QPixmap] = {}
        self.current_cover_url = ""
        self.play_token = 0
        self.lyrics_token = 0
        self._seeking = False
        self._drawer_open = False
        self._favorites_loading = False
        self._like_busy: set[str] = set()
        self._tasks: set[Task] = set()
        self._preview = False

        self.setWindowTitle("MTS Music")
        self.resize(430, 840)
        self.setMinimumSize(390, 720)
        self.setStyleSheet(APP_QSS)

        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)
        self.covers = QNetworkAccessManager(self)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_player_duration)
        self.player.playbackStateChanged.connect(self.update_play_button)
        self.player.mediaStatusChanged.connect(self.on_media_status)
        self.player.errorOccurred.connect(self.on_player_error)

        self._build_ui()
        self._build_drawer()
        self._build_toast()
        self._update_cover_size()
        self.play_btn.setFocus()

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._update_idle_time)
        self._timer.start()

        QTimer.singleShot(0, self.load_initial)

    # -- построение интерфейса -------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.pages = QStackedWidget()
        root.addWidget(self.pages)

        self.main_page = self._build_main_page()
        self.pages.addWidget(self.main_page)

        (
            self.favorites_page,
            self.favorites_list,
            self.favorites_status,
        ) = self._build_list_page("Избранное")
        self.favorites_list._queue_name = "Избранное"
        self.favorites_list._tracks = []
        self.favorites_list.itemDoubleClicked.connect(
            lambda item: self.play_from_list(self.favorites_list, item)
        )
        self.pages.addWidget(self.favorites_page)

        (
            self.history_page,
            self.history_list,
            self.history_status,
        ) = self._build_list_page("История")
        self.history_list._queue_name = "История"
        self.history_list._tracks = []
        self.history_list.itemDoubleClicked.connect(
            lambda item: self.play_from_list(self.history_list, item)
        )
        self.pages.addWidget(self.history_page)

        (
            self.wave_page,
            self.wave_list,
            self.wave_status,
        ) = self._build_list_page("Моя волна")
        self.wave_list._queue_name = "Моя волна"
        self.wave_list._tracks = []
        self.wave_list.itemDoubleClicked.connect(
            lambda item: self.play_from_list(self.wave_list, item)
        )
        self.pages.addWidget(self.wave_page)

        self.search_page, self.search_input, self.search_list, self.search_status = (
            self._build_search_page()
        )
        self.search_list._queue_name = "Поиск"
        self.search_list._tracks = []
        self.search_list.itemDoubleClicked.connect(
            lambda item: self.play_from_list(self.search_list, item)
        )
        self.pages.addWidget(self.search_page)

        self.lyrics_page, self.lyrics_title, self.lyrics_status, self.lyrics_body = (
            self._build_lyrics_page()
        )
        self.pages.addWidget(self.lyrics_page)

        self._setup_tab_order()

    def _setup_tab_order(self) -> None:
        """Явный порядок обхода по Tab для главного экрана."""
        for widget in (
            self.menu_btn,
            self.slider,
            self.prev_btn,
            self.play_btn,
            self.next_btn,
            self.lyrics_btn,
            self.like_btn,
        ):
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        order = [
            self.menu_btn,
            self.slider,
            self.prev_btn,
            self.play_btn,
            self.next_btn,
            self.lyrics_btn,
            self.like_btn,
        ]
        for first, second in zip(order, order[1:]):
            self.setTabOrder(first, second)

    def _build_main_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 10, 22, 18)
        layout.setSpacing(0)

        top = QHBoxLayout()
        top.setSpacing(6)
        menu = QToolButton()
        menu.setObjectName("menuBtn")
        menu.setText("☰")
        menu.setCursor(Qt.CursorShape.PointingHandCursor)
        menu.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        menu.clicked.connect(self.open_drawer)
        self.menu_btn = menu
        brand = QLabel("MTS Music")
        brand.setObjectName("brand")
        self.top_status = QLabel("")
        self.top_status.setObjectName("topStatus")
        self.top_status.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        top.addWidget(menu)
        top.addWidget(brand)
        top.addStretch(1)
        top.addWidget(self.top_status)
        layout.addLayout(top)

        layout.addStretch(1)
        self.cover = CoverView()
        self.cover.setFixedSize(330, 330)
        layout.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(16)

        self.title_label = QLabel("Загрузка…")
        self.title_label.setObjectName("titleLabel")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setWordWrap(True)
        self.title_label.setMaximumHeight(58)
        layout.addWidget(self.title_label)

        self.artist_label = QLabel("")
        self.artist_label.setObjectName("artistLabel")
        self.artist_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.artist_label)

        self.album_label = QLabel("")
        self.album_label.setObjectName("albumLabel")
        self.album_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.album_label)
        layout.addSpacing(14)

        self.slider = ClickSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.seekRequested.connect(self.on_slider_seek)
        self.slider.actionTriggered.connect(self.on_slider_action)
        self.slider.scrubStarted.connect(lambda: setattr(self, "_seeking", True))
        self.slider.scrubFinished.connect(lambda: setattr(self, "_seeking", False))
        layout.addWidget(self.slider)
        layout.addSpacing(3)

        times = QHBoxLayout()
        self.elapsed_label = QLabel("0:00")
        self.elapsed_label.setObjectName("timeLabel")
        self.duration_label = QLabel("0:00")
        self.duration_label.setObjectName("timeLabel")
        times.addWidget(self.elapsed_label)
        times.addStretch(1)
        times.addWidget(self.duration_label)
        layout.addLayout(times)
        layout.addSpacing(10)

        controls = QHBoxLayout()
        controls.setSpacing(20)
        controls.addStretch(1)
        self.prev_btn = QToolButton()
        self.prev_btn.setObjectName("prevBtn")
        self.prev_btn.setIcon(media_icon("prev"))
        self.prev_btn.setIconSize(QSize(22, 22))
        self.prev_btn.setFixedSize(44, 44)
        self.prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.prev_btn.setToolTip("Предыдущий трек (P)")
        self.prev_btn.clicked.connect(lambda: self.advance(-1))
        self.play_btn = QToolButton()
        self.play_btn.setObjectName("playBtn")
        self.play_btn.setIcon(media_icon("play", 80))
        self.play_btn.setIconSize(QSize(38, 38))
        self.play_btn.setFixedSize(76, 76)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setToolTip("Пауза / продолжить (Space)")
        self.play_btn.clicked.connect(self.toggle_play)
        self.next_btn = QToolButton()
        self.next_btn.setObjectName("nextBtn")
        self.next_btn.setIcon(media_icon("next"))
        self.next_btn.setIconSize(QSize(22, 22))
        self.next_btn.setFixedSize(44, 44)
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.setToolTip("Следующий трек (N)")
        self.next_btn.clicked.connect(lambda: self.advance(1))
        controls.addWidget(self.prev_btn)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.next_btn)
        controls.addStretch(1)
        layout.addLayout(controls)
        layout.addSpacing(12)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addStretch(1)
        self.lyrics_btn = QPushButton("Текст")
        self.lyrics_btn.setObjectName("actionBtn")
        self.lyrics_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lyrics_btn.setToolTip("Текст песни (T)")
        self.lyrics_btn.clicked.connect(self.show_lyrics)
        self.like_btn = QPushButton("♡ В избранное")
        self.like_btn.setObjectName("actionBtn")
        self.like_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.like_btn.setToolTip("Добавить / убрать из избранного (L)")
        self.like_btn.clicked.connect(self.toggle_like)
        actions.addWidget(self.lyrics_btn)
        actions.addWidget(self.like_btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        layout.addStretch(1)
        return page

    def _page_header(self, title: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        back = QToolButton()
        back.setObjectName("backBtn")
        back.setText("←")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self.show_main)
        label = QLabel(title)
        label.setObjectName("pageTitle")
        row.addWidget(back)
        row.addWidget(label)
        row.addStretch(1)
        return row

    def _build_list_page(self, title: str) -> tuple[QWidget, QListWidget, QLabel]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(6)
        layout.addLayout(self._page_header(title))
        status = QLabel("")
        status.setObjectName("pageStatus")
        layout.addWidget(status)
        widget = QListWidget()
        widget.setItemDelegate(TrackDelegate(widget))
        widget.setUniformItemSizes(True)
        widget.setMouseTracking(True)
        widget.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        layout.addWidget(widget, 1)
        return page, widget, status

    def _build_search_page(self) -> tuple[QWidget, QLineEdit, QListWidget, QLabel]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(8)
        layout.addLayout(self._page_header("Поиск"))
        row = QHBoxLayout()
        row.setSpacing(8)
        edit = QLineEdit()
        edit.setPlaceholderText("Исполнитель или трек…")
        edit.returnPressed.connect(self.run_search)
        button = QPushButton("Найти")
        button.setObjectName("searchBtn")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self.run_search)
        row.addWidget(edit, 1)
        row.addWidget(button)
        layout.addLayout(row)
        status = QLabel("")
        status.setObjectName("pageStatus")
        layout.addWidget(status)
        widget = QListWidget()
        widget.setItemDelegate(TrackDelegate(widget))
        widget.setUniformItemSizes(True)
        widget.setMouseTracking(True)
        widget.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        layout.addWidget(widget, 1)
        return page, edit, widget, status

    def _build_lyrics_page(self) -> tuple[QWidget, QLabel, QLabel, QTextEdit]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(6)
        layout.addLayout(self._page_header("Текст песни"))
        title = QLabel("")
        title.setObjectName("artistLabel")
        layout.addWidget(title)
        status = QLabel("")
        status.setObjectName("pageStatus")
        layout.addWidget(status)
        body = QTextEdit()
        body.setReadOnly(True)
        body.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(body, 1)
        return page, title, status, body

    def _build_drawer(self) -> None:
        central = self.centralWidget()
        self.scrim = Scrim(central)
        self.scrim.setStyleSheet("background: rgba(0, 0, 0, 150);")
        self.scrim.hide()
        self.scrim.clicked.connect(self.close_drawer)

        self.drawer = QFrame(central)
        self.drawer.setObjectName("drawer")
        self.drawer.hide()
        layout = QVBoxLayout(self.drawer)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(2)
        title = QLabel("Меню")
        title.setObjectName("drawerTitle")
        brand = QLabel("MTS Music")
        brand.setObjectName("drawerBrand")
        layout.addWidget(title)
        layout.addWidget(brand)
        for text, handler in (
            ("Моя волна", self.show_wave),
            ("Избранное", self.show_favorites),
            ("История", self.show_history),
            ("Поиск", self.show_search),
            ("Текст песни", self.show_lyrics),
        ):
            button = QPushButton(text)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(handler)
            layout.addWidget(button)
        layout.addStretch(1)
        quit_button = QPushButton("Выход")
        quit_button.setObjectName("drawerQuit")
        quit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_button.clicked.connect(self.close)
        layout.addWidget(quit_button)

        self.drawer_anim = QPropertyAnimation(self.drawer, b"pos", self)
        self.drawer_anim.setDuration(180)
        self.drawer_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.drawer_anim.finished.connect(self._drawer_anim_finished)

    def _build_toast(self) -> None:
        self.toast_label = QLabel("", self.centralWidget())
        self.toast_label.setObjectName("toast")
        self.toast_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toast_label.setWordWrap(True)
        self.toast_label.hide()
        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(self.toast_label.hide)

    # -- геометрия --------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not hasattr(self, "drawer"):
            return
        central = self.centralWidget()
        width = min(300, max(240, central.width() - 60))
        height = central.height()
        if self._drawer_open:
            self.drawer.setGeometry(0, 0, width, height)
        else:
            self.drawer.setGeometry(-width, 0, width, height)
        self.scrim.setGeometry(0, 0, central.width(), height)
        self._update_cover_size()
        if self.toast_label.isVisible():
            self._place_toast()

    def _update_cover_size(self) -> None:
        """Обложка — насколько позволяет окно, но не мельче разумного."""
        central = self.centralWidget()
        side = min(
            central.width() - 64,
            int(central.height() * 0.46),
            380,
        )
        side = max(240, side)
        if self.cover.width() != side:
            self.cover.setFixedSize(side, side)

    def _place_toast(self) -> None:
        central = self.centralWidget()
        self.toast_label.setMaximumWidth(max(200, central.width() - 40))
        self.toast_label.adjustSize()
        x = (central.width() - self.toast_label.width()) // 2
        y = central.height() - self.toast_label.height() - 28
        self.toast_label.move(max(12, x), max(12, y))

    def toast(self, text: str) -> None:
        self.toast_label.setText(text)
        self._place_toast()
        self.toast_label.show()
        self.toast_label.raise_()
        self.toast_timer.start(2800)

    def set_top_status(self, text: str) -> None:
        self.top_status.setText(text)

    def open_drawer(self) -> None:
        if self._drawer_open:
            self.close_drawer()
            return
        central = self.centralWidget()
        width = min(300, max(240, central.width() - 60))
        self.scrim.setGeometry(0, 0, central.width(), central.height())
        self.scrim.show()
        self.scrim.raise_()
        self.drawer.setGeometry(-width, 0, width, central.height())
        self.drawer.show()
        self.drawer.raise_()
        self._drawer_open = True
        self.drawer_anim.stop()
        self.drawer_anim.setStartValue(QPoint(-width, 0))
        self.drawer_anim.setEndValue(QPoint(0, 0))
        self.drawer_anim.start()

    def close_drawer(self) -> None:
        if not self._drawer_open:
            return
        self._drawer_open = False
        width = self.drawer.width()
        self.drawer_anim.stop()
        self.drawer_anim.setStartValue(self.drawer.pos())
        self.drawer_anim.setEndValue(QPoint(-width, 0))
        self.drawer_anim.start()

    def _drawer_anim_finished(self) -> None:
        if not self._drawer_open:
            self.drawer.hide()
            self.scrim.hide()

    # -- загрузка данных --------------------------------------------------

    def run_task(self, fn, done, failed) -> None:
        task = Task(fn)
        task.done_cb = done
        task.failed_cb = failed
        task.signals.done.connect(self._task_done)
        task.signals.failed.connect(self._task_failed)
        self._tasks.add(task)
        self.pool.start(task)

    def _task_done(self, task: Task, result) -> None:
        if task not in self._tasks:
            return
        self._tasks.discard(task)
        task.done_cb(result)

    def _task_failed(self, task: Task, message: str) -> None:
        if task not in self._tasks:
            return
        self._tasks.discard(task)
        task.failed_cb(message)

    def on_error(self, message: str) -> None:
        self.set_top_status("Ошибка")
        self.toast(message)

    def load_initial(self) -> None:
        self.set_top_status("Загрузка…")
        self.run_task(self._fetch_initial, self.apply_initial, self.on_error)

    def _fetch_initial(self) -> tuple[str, list[dict], set[str]]:
        api = self.api
        uid = self.uid or get_uid(api)
        # Продолжаем волну с последних прослушанных треков, а не с начала.
        seed = [str(track.get("id")) for track in self.history[:5]]
        batch = fetch_mix_batch(api, seed or None)
        wave = [
            item["track"]
            for item in (batch.get("sequence") or [])
            if item.get("type") == "track" and item.get("track")
        ]
        payload = api.get(f"/users/{uid}/likes/tracks")
        refs = ((payload.get("result") or {}).get("library") or {}).get("tracks") or []
        liked = {str(ref.get("id")) for ref in refs}
        return uid, wave, liked

    def apply_initial(self, result: tuple[str, list[dict], set[str]]) -> None:
        self.uid, self.wave, self.liked_ids = result
        self.queue = list(self.wave)
        self.queue_name = "Моя волна"
        self.set_top_status(f"Волна · {len(self.wave)} · ♥ {len(self.liked_ids)}")
        if self.wave:
            self.current = self.wave[0]
            self.current_index = 0
            self.update_now_playing()
            self.load_cover(self.wave[0])
        else:
            self.title_label.setText("Ничего не найдено")

    # -- текущий трек -----------------------------------------------------

    def update_now_playing(self) -> None:
        track = self.current
        if not track:
            self.title_label.setText("Ничего не играет")
            return
        self.title_label.setText(str(track.get("title") or "?"))
        self.artist_label.setText(track_artist(track))
        self.album_label.setText(track_album(track))
        duration = int(track.get("durationMs") or 0)
        self.slider.setRange(0, max(duration, 1))
        self.slider.setValue(0)
        self.elapsed_label.setText("0:00")
        self.duration_label.setText(fmt_millis(duration))
        self.update_like_button()
        self.update_play_button()

    def update_like_button(self, liked: bool | None = None) -> None:
        if liked is None:
            liked = bool(self.current and str(self.current.get("id")) in self.liked_ids)
        self.like_btn.setText("♥ В избранном" if liked else "♡ В избранное")
        self.like_btn.setProperty("liked", "true" if liked else "false")
        self.like_btn.style().unpolish(self.like_btn)
        self.like_btn.style().polish(self.like_btn)

    def update_play_button(self, *args) -> None:
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setIcon(media_icon("pause" if playing else "play", 80))

    def _update_idle_time(self) -> None:
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        if self._seeking:
            return
        self.on_position(self.player.position())

    # -- обложка ----------------------------------------------------------

    def load_cover(self, track: dict) -> None:
        url = track_cover_url(track)
        self.current_cover_url = url or ""
        if not url:
            self.cover.set_pixmap(None)
            return
        cached = self.cover_cache.get(url)
        if cached is not None:
            self.cover.set_pixmap(cached)
            return
        self.cover.set_pixmap(None)
        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, USER_AGENT)
        reply = self.covers.get(request)

        def done() -> None:
            try:
                data = bytes(reply.readAll().data())
                pixmap = QPixmap()
                if not data or not pixmap.loadFromData(data):
                    return
                self.cover_cache[url] = pixmap
                if len(self.cover_cache) > 30:
                    self.cover_cache.pop(next(iter(self.cover_cache)))
                if url == self.current_cover_url:
                    self.cover.set_pixmap(pixmap)
            finally:
                reply.deleteLater()

        reply.finished.connect(done)

    # -- воспроизведение --------------------------------------------------

    def play_from_list(self, widget: QListWidget, item: QListWidgetItem) -> None:
        tracks = getattr(widget, "_tracks", [])
        index = widget.row(item)
        if not (0 <= index < len(tracks)):
            return
        self.queue = list(tracks)
        self.queue_name = getattr(widget, "_queue_name", "Список")
        self.play_track(tracks[index], self.queue, index)
        self.show_main()

    def play_track(
        self,
        track: dict,
        queue: list[dict] | None = None,
        index: int | None = None,
    ) -> None:
        if queue is not None:
            self.queue = queue
        if index is None:
            index = next(
                (
                    i
                    for i, item in enumerate(self.queue)
                    if str(item.get("id")) == str(track.get("id"))
                ),
                -1,
            )
        self.current = track
        self.current_index = index
        self.play_token += 1
        token = self.play_token
        self.update_now_playing()
        self.load_cover(track)
        self.set_top_status("Загрузка…")

        self.run_task(
            lambda: resolve_stream(self.api, str(track.get("id"))),
            lambda result: self.start_playback(token, track, result),
            self.on_error,
        )

    def start_playback(
        self, token: int, track: dict, result: tuple[str, dict]
    ) -> None:
        if token != self.play_token:
            return
        url, item = result
        self._preview = bool(item.get("preview"))
        self.player.setSource(QUrl(url))
        self.player.play()
        self.history = record_history(track)
        title = str(track.get("title") or "")
        self.set_top_status(("preview · " if self._preview else "") + title)
        if self.pages.currentWidget() is self.history_page:
            self.fill_history()
        self.update_play_button()

    def toggle_play(self) -> None:
        state = self.player.playbackState()
        if self.player.source().isEmpty():
            if self.current:
                self.play_track(self.current, self.queue, self.current_index)
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()
        self.update_play_button()

    def on_position(self, position: int) -> None:
        if self._seeking:
            return
        duration = self.slider.maximum()
        if duration > 1:
            self.slider.setValue(min(position, duration))
        self.elapsed_label.setText(fmt_millis(position))

    def on_player_duration(self, duration: int) -> None:
        track_duration = int((self.current or {}).get("durationMs") or 0)
        if track_duration <= 0 and duration > 0:
            self.slider.setRange(0, duration)
            self.duration_label.setText(fmt_millis(duration))

    def on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.advance(1)

    def on_player_error(self, error, message: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        self.toast(message or "Не удалось воспроизвести трек")

    def on_slider_seek(self, value: int) -> None:
        self._seeking = False
        self.player.setPosition(value)
        self.elapsed_label.setText(fmt_millis(value))

    def on_slider_action(self, action: int) -> None:
        # Колесо и стрелки, когда слайдер в фокусе (клик обрабатывает ClickSlider).
        if self.slider.maximum() > 0:
            value = self.slider.value()
            self.player.setPosition(value)
            self.elapsed_label.setText(fmt_millis(value))

    def seek(self, delta_ms: int) -> None:
        if self.player.source().isEmpty():
            return
        duration = self.slider.maximum()
        position = max(0, min(self.player.position() + delta_ms, max(0, duration - 500)))
        self.player.setPosition(position)
        self.slider.setValue(position)
        self.elapsed_label.setText(fmt_millis(position))

    def advance(self, direction: int) -> None:
        queue = self.queue or self.wave
        if not queue:
            return
        index = self.current_index + direction
        if index >= len(queue):
            if self.queue_name == "Моя волна":
                self.load_more_wave(index)
                return
            index = 0
        if index < 0:
            index = len(queue) - 1
        self.play_track(queue[index], queue, index)

    def load_more_wave(self, then_play: int) -> None:
        self.set_top_status("Догружаю волну…")

        def fetch() -> list[dict]:
            ids = [str(track.get("id")) for track in self.wave[-5:]]
            batch = fetch_mix_batch(self.api, ids or None)
            return [
                item["track"]
                for item in (batch.get("sequence") or [])
                if item.get("type") == "track" and item.get("track")
            ]

        self.run_task(fetch, lambda tracks: self.apply_more_wave(tracks, then_play), self.on_error)

    def apply_more_wave(self, tracks: list[dict], then_play: int) -> None:
        known = {str(track.get("id")) for track in self.wave}
        added = [track for track in tracks if str(track.get("id")) not in known]
        self.wave.extend(added)
        self.set_top_status(f"Волна · {len(self.wave)} · ♥ {len(self.liked_ids)}")
        if self.queue_name == "Моя волна":
            self.queue = list(self.wave)
        if then_play < len(self.queue) and added:
            self.play_track(self.queue[then_play], self.queue, then_play)
        elif not added:
            self.toast("Волна закончилась")

    # -- избранное --------------------------------------------------------

    def toggle_like(self) -> None:
        track = self.current
        if not track:
            return
        track_id = str(track.get("id"))
        if track_id in self._like_busy:
            return
        albums = track.get("albums") or []
        album = str(albums[0].get("id")) if albums else ""
        if not album:
            self.toast("У трека нет альбома")
            return
        liked = track_id in self.liked_ids
        self._like_busy.add(track_id)

        def request() -> str:
            uid = self.uid or get_uid(self.api)
            if liked:
                self.api.post(
                    f"/users/{uid}/likes/tracks/remove",
                    **{"track-ids": f"{track_id}:{album}"},
                )
            else:
                self.api.post(
                    f"/users/{uid}/likes/tracks/add",
                    **{"track-id": f"{track_id}:{album}"},
                )
            return track_id

        self.run_task(request, lambda _: self.apply_like(track, not liked), self.like_failed)

    def apply_like(self, track: dict, liked: bool) -> None:
        track_id = str(track.get("id"))
        self._like_busy.discard(track_id)
        if liked:
            self.liked_ids.add(track_id)
        else:
            self.liked_ids.discard(track_id)
        if self.current and str(self.current.get("id")) == track_id:
            self.update_like_button(liked)
        self.set_top_status(f"{self.queue_name} · ♥ {len(self.liked_ids)}")
        self.toast("♥ Добавлено в избранное" if liked else "♡ Убрано из избранного")
        if self.pages.currentWidget() is self.favorites_page:
            self.load_favorites(force=True)

    def like_failed(self, message: str) -> None:
        if self.current:
            self._like_busy.discard(str(self.current.get("id")))
        self.on_error(message)

    # -- экраны -----------------------------------------------------------

    def show_main(self) -> None:
        self.close_drawer()
        self.pages.setCurrentWidget(self.main_page)

    def _show(self, page: QWidget) -> None:
        self.close_drawer()
        self.pages.setCurrentWidget(page)

    def show_favorites(self) -> None:
        self._show(self.favorites_page)
        self.load_favorites()

    def show_history(self) -> None:
        self.history = load_history()
        self.fill_history()
        self._show(self.history_page)

    def show_wave(self) -> None:
        self.fill_track_list(self.wave_list, self.wave, "Моя волна")
        self.wave_status.setText(
            f"{len(self.wave)} трек(ов) · догружается автоматически"
            if self.wave
            else "Пока пусто"
        )
        self._show(self.wave_page)

    def show_search(self) -> None:
        self._show(self.search_page)
        self.search_input.setFocus()
        self.search_input.selectAll()

    def show_lyrics(self) -> None:
        track = self.current
        if not track:
            self.toast("Сначала выбери трек")
            return
        self.close_drawer()
        self.pages.setCurrentWidget(self.lyrics_page)
        self.lyrics_title.setText(str(track.get("title") or ""))
        self.lyrics_status.setText("Загружаю текст…")
        self.lyrics_body.setPlainText("")
        self.lyrics_token += 1
        token = self.lyrics_token
        self.run_task(
            lambda: self._fetch_lyrics(track),
            lambda result: self.apply_lyrics(token, result),
            lambda message: self.lyrics_failed(token, message),
        )

    def _fetch_lyrics(self, track: dict) -> tuple[str, str]:
        text = ""
        source = "MTS"
        try:
            response = lyrics_response(self.api, str(track.get("id")), "TEXT")
            if response.status_code == 200:
                text = lyrics_text(self.api, response)
                if "Подключите подписку" in text:
                    text = ""
        except Exception:  # noqa: BLE001 - просто уходим в резервный источник
            text = ""
        if not text.strip():
            text = lrclib_lyrics(track, want_sync=False) or ""
            source = "LRCLIB"
        return text, source

    def apply_lyrics(self, token: int, result: tuple[str, str]) -> None:
        if token != self.lyrics_token:
            return
        text, source = result
        if not text.strip():
            self.lyrics_status.setText("Текст не найден")
            return
        self.lyrics_status.setText(f"Источник: {source}")
        self.lyrics_body.setPlainText(text)

    def lyrics_failed(self, token: int, message: str) -> None:
        if token != self.lyrics_token:
            return
        self.lyrics_status.setText("Текст не найден")
        self.toast(message)

    # -- списки -----------------------------------------------------------

    def fill_track_list(
        self, widget: QListWidget, tracks: list[dict], queue_name: str
    ) -> None:
        widget.clear()
        widget._tracks = list(tracks)
        widget._queue_name = queue_name
        for track in tracks:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, track)
            item.setSizeHint(QSize(0, 62))
            widget.addItem(item)

    def fill_history(self) -> None:
        self.fill_track_list(self.history_list, self.history, "История")
        self.history_status.setText(
            f"{len(self.history)} трек(ов)" if self.history else "Пока пусто"
        )

    def load_favorites(self, force: bool = False) -> None:
        if self._favorites_loading:
            return
        if self.favorites and not force:
            self.fill_track_list(self.favorites_list, self.favorites, "Избранное")
            self.favorites_status.setText(f"{len(self.favorites)} трек(ов)")
            return
        self._favorites_loading = True
        self.favorites_status.setText("Загружаю…")
        if not self.favorites:
            self.favorites_list.clear()
        self.run_task(self._fetch_favorites, self.apply_favorites, self.favorites_failed)

    def _fetch_favorites(self) -> list[dict]:
        uid = self.uid or get_uid(self.api)
        payload = self.api.get(f"/users/{uid}/likes/tracks")
        refs = ((payload.get("result") or {}).get("library") or {}).get("tracks") or []
        ids = [str(ref.get("id")) for ref in refs[:300]]
        tracks: list[dict] = []
        for start in range(0, len(ids), 50):
            chunk = ids[start : start + 50]
            if chunk:
                tracks.extend(
                    self.api.get("/tracks", trackIds=",".join(chunk)).get("result") or []
                )
        return tracks

    def apply_favorites(self, tracks: list[dict]) -> None:
        self._favorites_loading = False
        self.favorites = tracks
        self.fill_track_list(self.favorites_list, tracks, "Избранное")
        self.favorites_status.setText(
            f"{len(tracks)} трек(ов)" if tracks else "Пока пусто"
        )

    def favorites_failed(self, message: str) -> None:
        self._favorites_loading = False
        self.favorites_status.setText("Не удалось загрузить")
        self.toast(message)

    def run_search(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            return
        self.search_status.setText("Ищу…")

        def search() -> list[dict]:
            payload = self.api.get(
                "/search",
                text=query,
                type="track",
                page=0,
                **{"page-size": 25},
                nocorrect="true",
            )
            return (
                ((payload.get("result") or {}).get("tracks") or {}).get("results")
                or []
            )

        self.run_task(search, self.apply_search, self.search_failed)

    def apply_search(self, tracks: list[dict]) -> None:
        self.fill_track_list(self.search_list, tracks, "Поиск")
        self.search_status.setText(
            f"{len(tracks)} результат(ов)" if tracks else "Ничего не найдено"
        )

    def search_failed(self, message: str) -> None:
        self.search_status.setText("Ошибка поиска")
        self.toast(message)

    # -- клавиатура -------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        focus = QApplication.focusWidget()
        if isinstance(focus, QLineEdit) and event.key() != Qt.Key.Key_Escape:
            super().keyPressEvent(event)
            return

        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_Q:
                self.close()
                return
            super().keyPressEvent(event)
            return

        key = event.key()
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            super().keyPressEvent(event)
            return
        if isinstance(focus, QAbstractButton) and key in (
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ):
            super().keyPressEvent(event)
            return
        if isinstance(focus, QSlider) and key in (
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
        ):
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_Escape:
            if self._drawer_open:
                self.close_drawer()
            elif self.pages.currentWidget() is not self.main_page:
                self.show_main()
            return
        if key == Qt.Key.Key_Space:
            self.toggle_play()
        elif key == Qt.Key.Key_Left:
            self.seek(-5000)
        elif key == Qt.Key.Key_Right:
            self.seek(5000)
        elif key == Qt.Key.Key_N:
            self.advance(1)
        elif key == Qt.Key.Key_P:
            self.advance(-1)
        elif key == Qt.Key.Key_L:
            self.toggle_like()
        elif key == Qt.Key.Key_T:
            self.show_lyrics()
        elif key == Qt.Key.Key_M:
            self.open_drawer()
        elif key == Qt.Key.Key_F:
            self.show_favorites()
        elif key == Qt.Key.Key_H:
            self.show_history()
        elif key == Qt.Key.Key_S:
            self.show_search()
        elif key == Qt.Key.Key_W:
            self.show_wave()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.player.stop()
        self.pool.clear()
        super().closeEvent(event)


def main() -> int:
    force_ipv4()
    app = QApplication(sys.argv)
    app.setApplicationName("MTS Music")
    app.setApplicationDisplayName("MTS Music")
    app.setStyle("Fusion")
    try:
        api = Api()
    except SystemExit:
        return 1
    if not api.token:
        QMessageBox.warning(
            None,
            "MTS Music",
            "Сначала войди в аккаунт:\n\n.venv/bin/python mtsmusic.py login",
        )
        return 1
    window = QtMusicApp(api)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
