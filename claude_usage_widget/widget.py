"""The always-on-top panel itself: a single custom-painted, frameless window."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
)
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication, QWidget

from .usage_api import UsageSnapshot

WIDTH = 380
PAD_X = 16
PAD_TOP = 14
PAD_BOTTOM = 14
HEADER_HEIGHT = 22
HEADER_GAP = 12
LABEL_HEIGHT = 18
LABEL_BAR_GAP = 9
BAR_HEIGHT = 5
ROW_GAP = 15
FOOTER_HEIGHT = 20

PANEL_BG = QColor(32, 32, 35, 240)
PANEL_BORDER = QColor(255, 255, 255, 22)
HEADER_FG = QColor("#9A9AA1")
LABEL_FG = QColor("#F2F2F4")
DETAIL_FG = QColor("#8E8E93")
TRACK = QColor(255, 255, 255, 28)

FILL_NORMAL = QColor("#2F80F5")
FILL_WARN = QColor("#E0A32E")
FILL_CRITICAL = QColor("#E5484D")

STATUS_FG = QColor("#8E8E93")
STATUS_ALERT_FG = QColor("#E0A32E")
ERROR_FG = QColor("#E5484D")

USAGE_SETTINGS_URL = "https://claude.ai/settings/usage"

# Wrap at word boundaries, but break mid-token rather than overflow — error
# messages carry long unbroken paths like C:\Users\...\.credentials.json.
WRAP_FLAGS = Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap | Qt.TextWrapAnywhere


@dataclass
class ViewState:
    snapshot: UsageSnapshot | None = None
    status: str = ""
    status_is_alert: bool = False
    error: str = ""
    loading: bool = False


class UsageWidget(QWidget):
    """Frameless panel. Drag to move, double-click to refresh, right-click for the menu."""

    refresh_requested = Signal()
    menu_requested = Signal(QPoint)
    position_changed = Signal(QPoint)

    def __init__(self, warn_percent: int, critical_percent: int) -> None:
        super().__init__(None)
        self.warn_percent = warn_percent
        self.critical_percent = critical_percent
        self.state = ViewState(loading=True)
        self._drag_offset: QPoint | None = None
        self._header_arrow = QRect()

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # keeps it off the taskbar and Alt-Tab
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Claude Usage")
        self.setFixedWidth(WIDTH)
        self._recalculate_height()

    # -- fonts ---------------------------------------------------------------

    def _font(self, size: float, bold: bool = False) -> QFont:
        font = QFont(self.font())
        # Qt walks this list and uses the first family present on the system.
        font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "sans-serif"])
        font.setPointSizeF(size)
        font.setBold(bold)
        return font

    # -- state ---------------------------------------------------------------

    def set_thresholds(self, warn: int, critical: int) -> None:
        self.warn_percent = warn
        self.critical_percent = critical
        self.update()

    def set_snapshot(self, snapshot: UsageSnapshot) -> None:
        self.state.snapshot = snapshot
        self.state.error = ""
        self.state.loading = False
        self._recalculate_height()
        self.update()

    def set_status(self, message: str, *, alert: bool = False) -> None:
        self.state.status = message
        self.state.status_is_alert = alert
        self._recalculate_height()
        self.update()

    def set_error(self, message: str) -> None:
        """A hard failure. Existing bars stay visible; the message goes in the footer."""
        self.state.loading = False
        if self.state.snapshot is None:
            self.state.error = message
            self.state.status = ""
        else:
            self.state.error = ""
            self.state.status = message
            self.state.status_is_alert = True
        self._recalculate_height()
        self.update()

    def _placeholder_text(self) -> str:
        if self.state.error:
            return self.state.error
        return "Loading usage…" if self.state.loading else "No usage data."

    def _placeholder_height(self) -> int:
        """Wrapped message height, so a long error is not clipped."""
        metrics = QFontMetrics(self._font(9.5))
        bounds = metrics.boundingRect(
            QRect(0, 0, WIDTH - PAD_X * 2, 0), WRAP_FLAGS, self._placeholder_text()
        )
        return max(24, bounds.height())

    def _footer_height(self) -> int:
        """Wrapped status height, capped at two lines.

        Eliding a status to one line hides the part that says what to do about
        it, which is the only part that matters.
        """
        if not self.state.status:
            return 0
        metrics = QFontMetrics(self._font(8.5))
        bounds = metrics.boundingRect(
            QRect(0, 0, WIDTH - PAD_X * 2, 0), WRAP_FLAGS, self.state.status
        )
        return min(bounds.height(), metrics.lineSpacing() * 2) + 6

    def _recalculate_height(self) -> None:
        height = PAD_TOP + HEADER_HEIGHT + HEADER_GAP
        rows = self.state.snapshot.rows if self.state.snapshot else []
        if rows:
            row_height = LABEL_HEIGHT + LABEL_BAR_GAP + BAR_HEIGHT
            height += len(rows) * row_height + (len(rows) - 1) * ROW_GAP
        else:
            height += self._placeholder_height()
        # The footer is for status alongside bars; a bare error owns the body instead.
        if self.state.status and rows:
            height += self._footer_height()
        height += PAD_BOTTOM
        self.setFixedHeight(int(height))

    # -- colour --------------------------------------------------------------

    def fill_colour(self, percent: float) -> QColor:
        """Only the bar fill changes colour; the panel chrome stays dark."""
        if percent >= self.critical_percent:
            return FILL_CRITICAL
        if percent >= self.warn_percent:
            return FILL_WARN
        return FILL_NORMAL

    # -- painting ------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(PANEL_BG)
        painter.drawRoundedRect(rect, 12, 12)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(PANEL_BORDER, 1))
        painter.drawRoundedRect(rect, 12, 12)

        y = PAD_TOP
        y = self._paint_header(painter, y)
        y += HEADER_GAP

        rows = self.state.snapshot.rows if self.state.snapshot else []
        if rows:
            for index, row in enumerate(rows):
                y = self._paint_row(painter, y, row)
                if index < len(rows) - 1:
                    y += ROW_GAP
        else:
            y = self._paint_placeholder(painter, y)

        if self.state.status and rows:
            self._paint_footer(painter)
        painter.end()

    def _paint_header(self, painter: QPainter, y: int) -> int:
        plan = self.state.snapshot.plan if self.state.snapshot else None
        title = f"Plan usage limits · {plan}" if plan else "Plan usage limits"

        painter.setFont(self._font(9.5))
        painter.setPen(HEADER_FG)
        text_rect = QRect(PAD_X, y, WIDTH - PAD_X * 2 - 20, HEADER_HEIGHT)
        metrics = QFontMetrics(painter.font())
        painter.drawText(
            text_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            metrics.elidedText(title, Qt.ElideRight, text_rect.width()),
        )

        self._header_arrow = QRect(WIDTH - PAD_X - 18, y, 18, HEADER_HEIGHT)
        painter.setFont(self._font(11))
        painter.drawText(self._header_arrow, Qt.AlignRight | Qt.AlignVCenter, "→")
        return y + HEADER_HEIGHT

    def _paint_row(self, painter: QPainter, y: int, row) -> int:
        label_font = self._font(10, bold=True)
        detail_font = self._font(9.5)

        percent_text = f"{row.percent:.0f}%"
        painter.setFont(detail_font)
        percent_width = QFontMetrics(painter.font()).horizontalAdvance(percent_text) + 2
        detail_width = (
            QFontMetrics(painter.font()).horizontalAdvance(row.detail) if row.detail else 0
        )

        right_edge = WIDTH - PAD_X
        percent_rect = QRect(right_edge - percent_width, y, percent_width, LABEL_HEIGHT)
        painter.setPen(LABEL_FG)
        painter.drawText(percent_rect, Qt.AlignRight | Qt.AlignVCenter, percent_text)

        if row.detail:
            detail_rect = QRect(
                right_edge - percent_width - 10 - detail_width, y, detail_width, LABEL_HEIGHT
            )
            painter.setPen(DETAIL_FG)
            painter.drawText(detail_rect, Qt.AlignRight | Qt.AlignVCenter, row.detail)
            label_limit = detail_rect.left() - PAD_X - 10
        else:
            label_limit = percent_rect.left() - PAD_X - 10

        painter.setFont(label_font)
        painter.setPen(LABEL_FG)
        label_rect = QRect(PAD_X, y, max(40, label_limit), LABEL_HEIGHT)
        painter.drawText(
            label_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            QFontMetrics(painter.font()).elidedText(
                row.label, Qt.ElideRight, label_rect.width()
            ),
        )

        bar_y = y + LABEL_HEIGHT + LABEL_BAR_GAP
        bar_width = WIDTH - PAD_X * 2
        radius = BAR_HEIGHT / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(TRACK)
        painter.drawRoundedRect(
            QRectF(PAD_X, bar_y, bar_width, BAR_HEIGHT), radius, radius
        )

        fraction = max(0.0, min(row.percent / 100.0, 1.0))
        if fraction > 0:
            # Keep a visible nub for tiny values rather than a sliver.
            fill_width = max(BAR_HEIGHT, bar_width * fraction)
            painter.setBrush(self.fill_colour(row.percent))
            painter.drawRoundedRect(
                QRectF(PAD_X, bar_y, fill_width, BAR_HEIGHT), radius, radius
            )
        return bar_y + BAR_HEIGHT

    def _paint_placeholder(self, painter: QPainter, y: int) -> int:
        painter.setFont(self._font(9.5))
        painter.setPen(ERROR_FG if self.state.error else DETAIL_FG)
        height = self._placeholder_height()
        rect = QRect(PAD_X, y, WIDTH - PAD_X * 2, height)
        painter.drawText(rect, WRAP_FLAGS, self._placeholder_text())
        return y + height

    def _paint_footer(self, painter: QPainter) -> None:
        painter.setFont(self._font(8.5))
        painter.setPen(STATUS_ALERT_FG if self.state.status_is_alert else STATUS_FG)
        height = self._footer_height()
        rect = QRect(
            PAD_X, self.height() - PAD_BOTTOM - height, WIDTH - PAD_X * 2, height
        )
        painter.drawText(rect, WRAP_FLAGS, self.state.status)

    # -- interaction ---------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if event.button() == Qt.LeftButton:
            position = event.position().toPoint()
            if self._header_arrow.contains(position):
                QDesktopServices.openUrl(QUrl(USAGE_SETTINGS_URL))
                return
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if self._drag_offset is not None:
            self._drag_offset = None
            self.position_changed.emit(self.pos())
            event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt signature
        if event.button() == Qt.LeftButton:
            self.refresh_requested.emit()
            event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt signature
        self.menu_requested.emit(event.globalPos())
        event.accept()

    # -- placement -----------------------------------------------------------

    def restore_position(self, saved: list[int] | None) -> None:
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        available = screen.availableGeometry()
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            point = QPoint(int(saved[0]), int(saved[1]))
            # Only honour a saved spot that is still on a connected screen.
            if available.adjusted(-40, -40, 40, 40).contains(point):
                self.move(point)
                return
        self.move(
            available.right() - self.width() - 24,
            available.top() + 48,
        )
