"""Widget de controle de mídia para a sidebar.

Exibe capa do álbum, título e artista da faixa atual, e botões de controle
(previous, play/pause, next). Emite sinais puros de UI —
a lógica de backend fica no Controller.

Árvore de layout:
    VBox Principal
    ├── Label "REPRODUZINDO"
    ├── HBox de Conteúdo
    │   ├── Capa do Álbum (QLabel 80×80, cantos arredondados)
    │   └── VBox de Detalhes
    │       ├── Título da Música
    │       ├── Artista
    │       └── HBox de Controles [⏮] [▶⏸] [⏭]
    └── (fim — sem stretch, card abraça o conteúdo)
"""

from __future__ import annotations
import time

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QPainter, QPainterPath, QPixmap, QPaintEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QWidget
)
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Property, QTimer
from PySide6.QtGui import QEnterEvent, QMouseEvent

# Tamanho da capa do álbum em pixels
ART_SIZE: int = 80

# Constantes para a barra de progresso (Thin)
THIN_HEIGHT: int = 4
EXPANDED_HEIGHT: int = 14


def _round_pixmap(pixmap: QPixmap, radius: int = 12) -> QPixmap:
    """Aplica cantos arredondados a um QPixmap.

    Cria um novo pixmap com fundo transparente e desenha o original
    recortado por um QPainterPath com cantos arredondados.
    """
    size = pixmap.size()
    rounded = QPixmap(size)
    rounded.fill(Qt.GlobalColor.transparent)

    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    path = QPainterPath()
    path.addRoundedRect(0, 0, size.width(), size.height(), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()

    return rounded


class MediaPlayerWidget(QFrame):
    """Widget de controle de mídia.

    Sinais emitidos (para o Controller conectar ao serviço):
        play_pause_clicked()
        next_clicked()
        previous_clicked()

    Slots públicos (chamados pelo Controller com dados do serviço):
        update_track(title, artist)
        update_playback_state(is_playing)
        update_art(pixmap)
        clear_art()
    """

    play_pause_clicked = Signal()
    next_clicked = Signal()
    previous_clicked = Signal()
    seek_requested = Signal(int) # Emite a posição solicitada

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._is_playing: bool = False
        self._current_pos_s: int = 0
        self._duration_s: int = 0
        self._last_seek_time: float = 0.0  # Marcação temporal do último seek do usuário
        self._setup_ui()
        self._connect_buttons()

    def _setup_ui(self) -> None:
        self.setObjectName("MediaPlayerWidget")
        self.setStyleSheet(self._stylesheet())

        # Card abraça o conteúdo: expande horizontalmente, fixo verticalmente
        self.setMinimumWidth(300)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 12, 10, 12)  # L/R reduzido de 16→10 para ganhar 12px
        main_layout.setSpacing(10)

        # ── Label de seção ───────────────────────────────────────────
        self._section_label = QLabel("REPRODUZINDO")
        self._section_label.setObjectName("sectionLabel")
        self._section_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._section_label.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self._section_label)

        # ── HBox de conteúdo: capa + detalhes ────────────────────────
        content_row = QHBoxLayout()
        content_row.setSpacing(8)  # Reduzido de 12→8 para ganhar 4px

        # Capa do álbum (esquerda)
        self._art_label = QLabel()
        self._art_label.setObjectName("albumArt")
        self._art_label.setFixedSize(ART_SIZE, ART_SIZE)
        self._art_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._art_label.setScaledContents(False)
        self._set_placeholder_art()
        content_row.addWidget(self._art_label, 0, Qt.AlignmentFlag.AlignTop)

        # VBox de detalhes (direita): título, artista, controles
        details_col = QVBoxLayout()
        details_col.setSpacing(4)
        details_col.setContentsMargins(0, 0, 0, 0)

        self._title_label = MarqueeLabel("Nenhum player detectado")
        self._title_label.setObjectName("trackTitle")
        self._title_label.setFixedHeight(20)
        details_col.addWidget(self._title_label)

        self._artist_label = QLabel("—")
        self._artist_label.setObjectName("trackArtist")
        self._artist_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._artist_label.setWordWrap(True)
        self._artist_label.setContentsMargins(0, 0, 0, 4)
        details_col.addWidget(self._artist_label)

        # ── Seek Bar ─────────────────────────────────────────────────
        self._seek_slider = _SeekSlider()
        # sliderMoved: resposta visual imediata durante o drag (atualiza timer)
        self._seek_slider.sliderMoved.connect(self._on_slider_moved)
        # sliderReleased: único ponto de dispar o seek real (drag + clique)
        self._seek_slider.sliderReleased.connect(self._on_slider_released)
        details_col.addWidget(self._seek_slider)

        details_col.addSpacing(8)

        # ── Container Shield dos botões ──────────────────────────────────
        # Orçamento real (após ajuste de margens, Task #16.4):
        #   360px (sidebar) - 20px (card L+R) - 14px (mixer) - 12px (spacing)
        #   - 20px (media L+R) - 80px (art) - 8px (art→details spacing) = 206px
        #
        # Geometria escolhida:
        #   Prev(44) + gap(8) + Play(64) + gap(8) + Next(44) = 168px
        #   Folga para stretchs: 206 - 168 = 38px → 19px por lado ✓
        BTN_H: int = 28
        BTN_SIDE_W: int = 44
        BTN_PLAY_W: int = 64

        self.ctrl_container = QWidget()
        self.ctrl_container.setFixedHeight(BTN_H + 4)
        self.ctrl_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        btn_layout = QHBoxLayout(self.ctrl_container)
        btn_layout.setSpacing(8)
        btn_layout.setContentsMargins(0, 0, 0, 0)

        self._btn_prev = QPushButton("⏮")
        self._btn_prev.setObjectName("btnPrev")
        self._btn_prev.setFixedSize(BTN_SIDE_W, BTN_H)

        self._btn_play = QPushButton("▶")
        self._btn_play.setObjectName("btnPlay")
        self._btn_play.setFixedSize(BTN_PLAY_W, BTN_H)

        self._btn_next = QPushButton("⏭")
        self._btn_next.setObjectName("btnNext")
        self._btn_next.setFixedSize(BTN_SIDE_W, BTN_H)

        btn_layout.addStretch(1)
        btn_layout.addWidget(self._btn_prev, 0, Qt.AlignmentFlag.AlignVCenter)
        btn_layout.addWidget(self._btn_play, 0, Qt.AlignmentFlag.AlignVCenter)
        btn_layout.addWidget(self._btn_next, 0, Qt.AlignmentFlag.AlignVCenter)
        btn_layout.addStretch(1)

        details_col.addWidget(self.ctrl_container)

        content_row.addLayout(details_col)
        main_layout.addLayout(content_row)

    def _connect_buttons(self) -> None:
        """Conecta cliques dos botões aos sinais internos da UI."""
        self._btn_prev.clicked.connect(self.previous_clicked)
        self._btn_play.clicked.connect(self.play_pause_clicked)
        self._btn_next.clicked.connect(self.next_clicked)

    def _format_time(self, seconds: int) -> str:
        m = seconds // 60
        s = seconds % 60
        return f"{m}:{s:02d}"

    # ── Helpers internos ─────────────────────────────────────────────

    def _set_placeholder_art(self) -> None:
        """Exibe um placeholder com ícone de nota musical."""
        self._art_label.setPixmap(QPixmap())
        self._art_label.setText("🎵")
        self._art_label.setStyleSheet(
            """
            #albumArt {
                background-color: #313244;
                border-radius: 12px;
                font-size: 32px;
                color: #585b70;
            }
            """
        )

    # ── Slots públicos (chamados pelo Controller) ────────────────────

    @Slot(str, str)
    def update_track(self, title: str, artist: str) -> None:
        """Atualiza título e artista exibidos."""
        if not title:
            self._title_label.setText("Nenhum player detectado")
            self._artist_label.setText("—")
        else:
            self._title_label.setText(title)
            self._artist_label.setText(artist or "Artista desconhecido")

    @Slot(str)
    def update_player_name(self, name: str) -> None:
        """Atualiza o nome do player exibido na seção superior."""
        if not name:
            self._section_label.setText("MÍDIA")
        else:
            self._section_label.setText(f"REPRODUZINDO NO {name.upper()}")

    @Slot(bool)
    def update_playback_state(self, is_playing: bool) -> None:
        """Atualiza ícone do botão play/pause."""
        self._is_playing = is_playing
        self._btn_play.setText("⏸" if is_playing else "▶")

    @Slot(QPixmap)
    def update_art(self, pixmap: QPixmap) -> None:
        """Exibe a capa do álbum recebida como QPixmap."""
        if pixmap.isNull():
            self._set_placeholder_art()
            return

        # Escala mantendo aspect ratio e aplica cantos arredondados
        scaled = pixmap.scaled(
            ART_SIZE,
            ART_SIZE,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )

        # Crop central caso não seja quadrado
        if scaled.width() != ART_SIZE or scaled.height() != ART_SIZE:
            x = (scaled.width() - ART_SIZE) // 2
            y = (scaled.height() - ART_SIZE) // 2
            scaled = scaled.copy(x, y, ART_SIZE, ART_SIZE)

        rounded = _round_pixmap(scaled, radius=12)

        # Remove o texto do placeholder e aplica a imagem
        self._art_label.setText("")
        self._art_label.setStyleSheet(
            """
            #albumArt {
                background-color: transparent;
                border-radius: 12px;
            }
            """
        )
        self._art_label.setPixmap(rounded)

    @Slot()
    def clear_art(self) -> None:
        """Limpa a capa do álbum, voltando ao placeholder."""
        self._set_placeholder_art()

    @Slot(int)
    def _on_slider_moved(self, val: int) -> None:
        """Durante o drag: atualiza apenas o display de tempo — sem enviar D-Bus."""
        self._seek_slider.set_time_current(self._format_time(val))

    @Slot()
    def _on_slider_released(self) -> None:
        """Ao soltar: dispara o seek real e arma a trava anti-snapback."""
        val = self._seek_slider.value()
        self._last_seek_time = time.time()  # Arma a trava
        self.seek_requested.emit(val)

    @Slot(int)
    def update_position(self, pos_us: int) -> None:
        """Atualiza a posição da seek bar (pos_us vem em microsegundos do MPRIS).
        
        A trava anti-snapback ignora o polling por 1.5s após o usuário soltar a barra,
        dando tempo ao Spotify de aplicar o seek sem que o timer sobrescreva a UI.
        """
        if time.time() - self._last_seek_time < 1.5:
            return
        sec = pos_us // 1_000_000
        self._current_pos_s = sec
        self._seek_slider.set_time_current(self._format_time(sec))
        
        if not self._seek_slider.isSliderDown():
            # Converte microsegundos para segundos para evitar overflow de 32-bit int no QSlider
            self._seek_slider.blockSignals(True)
            self._seek_slider.setValue(sec)
            self._seek_slider.blockSignals(False)
            
    @Slot(int)
    def update_duration(self, length_us: int) -> None:
        """Atualiza o tamanho máximo da seek bar."""
        sec = length_us // 1_000_000
        self._duration_s = sec
        self._seek_slider.set_time_total(self._format_time(sec))
        self._seek_slider.setRange(0, sec)

    # ── Estilo ───────────────────────────────────────────────────────

    @staticmethod
    def _stylesheet() -> str:
        return """
            #MediaPlayerWidget {
                background: transparent;
            }

            #sectionLabel {
                color: #a6adc8;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
            }

            #trackTitle {
                color: #cdd6f4;
                font-size: 14px;
                font-weight: bold;
            }

            #trackArtist {
                color: #a6adc8;
                font-size: 12px;
            }

            #albumArt {
                background-color: #313244;
                border-radius: 12px;
            }

            QPushButton {
                background-color: #313244;
                color: #cdd6f4;
                border: none;
                outline: none;
                border-radius: 14px;   /* BTN_H/2 = 28/2 = 14 — pílula perfeita */
                font-size: 14px;
                text-align: center;
                margin: 0px;
                padding: 0px;
                /* Sem min/max-width — setFixedSize() em Python é a fonte de verdade */
            }

            QPushButton:hover {
                background-color: #45475a;
            }

            QPushButton:pressed {
                background-color: #585b70;
            }

            #btnPlay {
                background-color: #cba6f7;
                color: #1e1e2e;
                font-size: 16px;
                border-radius: 14px;
                padding-left: 3px;  /* Alinhamento ótico do triângulo ▶ */
            }

            #btnPlay:hover {
                background-color: #b4befe;
            }

            #btnPlay:pressed {
                background-color: #89b4fa;
            }
        """

class _SeekSlider(QSlider):
    """Barra de progresso horizontal que engrossa no hover."""

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFixedHeight(20)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._groove_height: int = THIN_HEIGHT
        self._time_current_str: str = "0:00"
        self._time_total_str: str = "0:00"
        
        self._apply_style()

        self._anim = QPropertyAnimation(self, b"grooveHeight")
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def set_time_current(self, t: str) -> None:
        self._time_current_str = t
        self.update()

    def set_time_total(self, t: str) -> None:
        self._time_total_str = t
        self.update()

    def get_groove_height(self) -> int:
        return self._groove_height

    def set_groove_height(self, h: int) -> None:
        self._groove_height = h
        self._apply_style()

    grooveHeight = Property(int, get_groove_height, set_groove_height)

    def _animate_to(self, target_height: int) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._groove_height)
        self._anim.setEndValue(target_height)
        self._anim.start()

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self._animate_to(EXPANDED_HEIGHT)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._animate_to(THIN_HEIGHT)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Pula o valor do slider exatamente para onde o mouse clicou."""
        if event.button() == Qt.MouseButton.LeftButton:
            # O X=0 é a base (valor mínimo) e width() é o máximo
            val = self.minimum() + ((self.maximum() - self.minimum()) * event.pos().x()) / self.width()
            self.setValue(int(val))
            # Dispara sliderReleased para acionar o seek e a trava anti-snapback,
            # sem precisar arrastar (clique direto na barra)
            self.sliderReleased.emit()
            event.accept()
        super().mousePressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        
        # Só desenha os timestamps se a barra estiver expandida
        if self._groove_height < EXPANDED_HEIGHT - 2:
            return
            
        from PySide6.QtGui import QPainter, QColor, QFont
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        
        font = painter.font()
        font.setPixelSize(9)
        font.setBold(True)
        painter.setFont(font)
        
        rect = self.rect()
        rect.adjust(8, 0, -8, 0)
        
        # Desenha Tempo Atual à Esquerda (cor escura)
        painter.setPen(QColor("#11111b"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._time_current_str)
        
        # Desenha Tempo Total à Direita (cor clara)
        painter.setPen(QColor("#a6adc8"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._time_total_str)

    def _apply_style(self) -> None:
        h = self._groove_height
        handle_w = h if h > THIN_HEIGHT else 0
        handle_radius = h // 2

        groove_bg = "#313244"
        fill_color = "#cba6f7"
        handle_color = "#cdd6f4"

        self.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background: {groove_bg};
                height: {h}px;
                border-radius: {h // 2}px;
            }}

            QSlider::sub-page:horizontal {{
                background: {fill_color};
                border-radius: {h // 2}px;
            }}

            QSlider::add-page:horizontal {{
                background: {groove_bg};
                border-radius: {h // 2}px;
            }}

            QSlider::handle:horizontal {{
                background: {handle_color};
                width: {handle_w}px;
                height: {h}px;
                margin: 0 0;
                border-radius: {handle_radius}px;
            }}
        """)

class MarqueeLabel(QWidget):
    """Widget de texto deslizante (Marquee) para títulos longos."""
    
    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._text = text
        self._offset = 0
        self._state = "PAUSED_START"
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_marquee)
        
        QTimer.singleShot(3000, self._start_scrolling)
        
    def setText(self, text: str) -> None: # noqa: N802
        if self._text != text:
            self._text = text
            self._offset = 0
            self._state = "PAUSED_START"
            self._timer.stop()
            self.update()
            QTimer.singleShot(3000, self._start_scrolling)
            
    def _start_scrolling(self) -> None:
        self._state = "SCROLLING"
        self._timer.start(50)
        
    def _update_marquee(self) -> None:
        if self._state == "SCROLLING":
            from PySide6.QtGui import QFontMetrics
            fm = QFontMetrics(self.font())
            text_width = fm.horizontalAdvance(self._text)
            
            if text_width <= self.width():
                self._timer.stop()
                self._offset = 0
                self.update()
                return
                
            self._offset -= 1
            if abs(self._offset) > (text_width - self.width() + 20):
                self._state = "PAUSED_END"
                self._timer.stop()
                QTimer.singleShot(3000, self._reset_position)
                
            self.update()
            
    def _reset_position(self) -> None:
        self._offset = 0
        self.update()
        self._state = "PAUSED_START"
        QTimer.singleShot(3000, self._start_scrolling)
        
    def paintEvent(self, event: QPaintEvent) -> None: # noqa: N802
        from PySide6.QtGui import QPainter, QColor
        from PySide6.QtWidgets import QStyleOption, QStyle
        
        opt = QStyleOption()
        opt.initFrom(self)
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        
        self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, painter, self)
        
        painter.setFont(self.font())
        painter.setPen(QColor("#cdd6f4")) # Cor clara para título
        painter.setClipRect(self.rect())
        
        fm = painter.fontMetrics()
        y_baseline = (self.height() + fm.ascent() - fm.descent()) // 2
        painter.drawText(int(self._offset), y_baseline, self._text)
