"""The application's animated background: a looping clip behind the whole window.

Frames are pulled from a QVideoSink and painted by this widget, rather than
using a native video surface. That keeps the background an ordinary QWidget, so
every other widget stacks above it predictably and nothing has to fight a
native window for z-order.

The widget is purely decorative: it is transparent to mouse events, and any
failure to play leaves a static gradient in place rather than an error.
"""

from pathlib import Path
import time

from PyQt6.QtCore import QRectF, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import QWidget

from gui import theme
from gui.resources import background_video_path


class BackgroundVideo(QWidget):
    """A cover-scaled, looping, muted clip with a translucent scrim on top."""

    #: Emitted once the widget settles on video or on its static fallback.
    ready = pyqtSignal(bool)

    #: Opacity of the dark scrim painted over the video so text stays readable.
    SCRIM_OPACITY = 0.70

    #: Repaint ceiling. Ambient motion reads as smooth well below the clip's
    #: frame rate, and a full-window repaint is the expensive part, so frames
    #: arriving faster than this are dropped rather than converted and drawn.
    MAX_REPAINTS_PER_SECOND = 15

    def __init__(self, source: Path | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("backgroundVideo")
        # Decoration must never intercept a click meant for a control.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._frame_image = None
        self._last_paint = 0.0
        self._player = None
        self._audio = None
        self._sink = None
        self._failed = False

        self._source = source if source is not None else background_video_path()
        self._start()

    # ------------------------------------------------------------------
    def _start(self) -> None:
        if self._source is None or not Path(self._source).is_file():
            self._failed = True
            self.ready.emit(False)
            return
        try:
            from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
        except ImportError:
            # No multimedia support in this installation: the gradient stands in.
            self._failed = True
            self.ready.emit(False)
            return

        try:
            self._sink = QVideoSink(self)
            self._sink.videoFrameChanged.connect(self._on_frame)

            self._player = QMediaPlayer(self)
            # An explicitly muted output guarantees silence even if the clip
            # were ever replaced with one carrying an audio track.
            self._audio = QAudioOutput(self)
            self._audio.setMuted(True)
            self._audio.setVolume(0.0)
            self._player.setAudioOutput(self._audio)
            self._player.setVideoSink(self._sink)

            if hasattr(self._player, "setLoops"):
                self._player.setLoops(QMediaPlayer.Loops.Infinite)
            else:  # pragma: no cover - older Qt: restart at end of media
                self._player.mediaStatusChanged.connect(self._restart_at_end)
            self._player.errorOccurred.connect(self._on_error)

            self._player.setSource(QUrl.fromLocalFile(str(Path(self._source).resolve())))
            self._player.play()
            self.ready.emit(True)
        except Exception:  # noqa: BLE001 - decoration must never break startup
            self._teardown()
            self._failed = True
            self.ready.emit(False)

    # ------------------------------------------------------------------
    def _on_frame(self, frame) -> None:
        """Accept a decoded frame, dropping any that arrive too close together.

        Nothing here runs on a worker: the conversion is skipped entirely for
        dropped frames, so the saving is real rather than cosmetic.
        """
        if not frame.isValid() or not self.isVisible():
            return
        now = time.monotonic()
        if now - self._last_paint < 1.0 / self.MAX_REPAINTS_PER_SECOND:
            return
        image = frame.toImage()
        if not image.isNull():
            self._last_paint = now
            self._frame_image = image
            self.update()

    def _restart_at_end(self, status) -> None:  # pragma: no cover - Qt < 6.4
        from PyQt6.QtMultimedia import QMediaPlayer

        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._player:
            self._player.setPosition(0)
            self._player.play()

    def _on_error(self, *_arguments) -> None:
        """Retire the clip for this session; the gradient takes over."""
        self._teardown()
        self._failed = True
        self._frame_image = None
        self.update()
        self.ready.emit(False)

    def _teardown(self) -> None:
        if self._player is not None:
            self._player.stop()
            self._player.setVideoSink(None)
            self._player.setSource(QUrl())

    # ------------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rectangle = self.rect()

        if self._frame_image is not None and not self._frame_image.isNull():
            painter.drawImage(self._cover_rect(), self._frame_image)
        else:
            painter.fillRect(rectangle, self._fallback_brush())

        # A scrim keeps every overlaid control legible over moving imagery.
        scrim = QColor(theme.SCRIM)
        scrim.setAlphaF(self.SCRIM_OPACITY)
        painter.fillRect(rectangle, scrim)

    def _cover_rect(self) -> QRectF:
        """Scale the frame to cover the widget, cropping the overflow.

        Cover rather than fit, so the background never shows empty bars
        whatever shape the window is dragged into.
        """
        image = self._frame_image
        widget_width, widget_height = self.width(), self.height()
        if image.width() == 0 or image.height() == 0 or widget_height == 0:
            return QRectF(self.rect())
        scale = max(widget_width / image.width(), widget_height / image.height())
        width, height = image.width() * scale, image.height() * scale
        return QRectF((widget_width - width) / 2, (widget_height - height) / 2, width, height)

    def _fallback_brush(self) -> QBrush:
        """A calm vertical gradient used whenever no frame is available."""
        gradient = QLinearGradient(0, 0, 0, max(1, self.height()))
        gradient.setColorAt(0.0, QColor(theme.SURFACE))
        gradient.setColorAt(0.55, QColor(theme.INK))
        gradient.setColorAt(1.0, QColor(theme.ACCENT_SOFT))
        return QBrush(gradient)

    # ------------------------------------------------------------------
    @property
    def is_playing(self) -> bool:
        """Whether the clip is genuinely playing, not merely requested."""
        if self._player is None or self._failed:
            return False
        from PyQt6.QtMultimedia import QMediaPlayer

        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    @property
    def is_muted(self) -> bool:
        return self._audio.isMuted() if self._audio is not None else True

    @property
    def loops_forever(self) -> bool:
        if self._player is None or not hasattr(self._player, "loops"):
            return self._player is not None
        from PyQt6.QtMultimedia import QMediaPlayer

        return self._player.loops() == QMediaPlayer.Loops.Infinite.value

    @property
    def has_frame(self) -> bool:
        return self._frame_image is not None and not self._frame_image.isNull()

    def stop(self) -> None:
        """Release decoding resources when the window closes."""
        self._teardown()
