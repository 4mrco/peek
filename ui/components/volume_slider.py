"""Slider vertical customizado para controle de volume com ícone de app.

Design "linha fina" minimalista:
  - Estado normal: groove de 4px, handle invisível (0px).
  - Estado hover: groove expande para 14px, handle aparece (14px).
  - Transição suave via animação de largura.

Agora estruturado como um QWidget que contém um QLabel (ícone) e o QSlider.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    Property,
    Signal,
)
from PySide6.QtGui import QEnterEvent, QPaintEvent, QPainter, QColor, QFont, QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QLabel,
    QSlider,
    QSizePolicy,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)
from ui.components.clickable_icon import ClickableIcon


# ── Constantes de design ─────────────────────────────────────────────

THIN_WIDTH: int = 4         # Largura no estado normal (px)
EXPANDED_WIDTH: int = 14    # Largura no estado hover (px)
SLIDER_HEIGHT: int = 120    # Altura padrão do slider (px)
ANIMATION_MS: int = 150     # Duração da transição thin ↔ expanded


class _ThinSlider(QSlider):
    """Slider vertical base com estética 'linha fina' expansível."""

    volume_changed = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)

        self.setRange(0, 100)
        self.setValue(50)
        self.setFixedHeight(SLIDER_HEIGHT)
        self.setFixedWidth(EXPANDED_WIDTH)  # Reserva espaço max para não deslocar layout
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        # Inverte para que topo = 100%, base = 0% (comportamento padrão do QSlider)
        self.setInvertedAppearance(False)

        # Largura animável do groove
        self._groove_width: int = THIN_WIDTH
        self._setup_animation()
        self._apply_style()

        # Conecta o valueChanged interno ao sinal público
        self.valueChanged.connect(self.volume_changed)

    # ── Propriedade animável: groove_width ───────────────────────────

    def _get_groove_width(self) -> int:
        return self._groove_width

    def _set_groove_width(self, width: int) -> None:
        self._groove_width = width
        self._apply_style()

    groove_width = Property(int, _get_groove_width, _set_groove_width)

    # ── Animação ─────────────────────────────────────────────────────

    def _setup_animation(self) -> None:
        self._anim = QPropertyAnimation(self, b"groove_width")
        self._anim.setDuration(ANIMATION_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _animate_to(self, target_width: int) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._groove_width)
        self._anim.setEndValue(target_width)
        self._anim.start()

    # ── Eventos de hover e pintura ───────────────────────────────────

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self._animate_to(EXPANDED_WIDTH)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._animate_to(THIN_WIDTH)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Pula o valor do slider exatamente para onde o mouse clicou."""
        if event.button() == Qt.MouseButton.LeftButton:
            # O Y=0 é o topo (valor máximo) e height() é a base (valor mínimo)
            val = self.minimum() + ((self.maximum() - self.minimum()) * (self.height() - event.pos().y())) / self.height()
            self.setValue(int(val))
            event.accept()
        super().mousePressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)

        # Desenhar texto apenas se o slider estiver minimamente expandido
        if self._groove_width < EXPANDED_WIDTH - 2:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Obter o retângulo do handle
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        handle_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self
        )

        # Texto e formatação
        text = f"{self.value()}"
        font = painter.font()
        font.setPixelSize(8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#1e1e2e"))  # Cor escura para contrastar com #cdd6f4

        painter.drawText(handle_rect, Qt.AlignmentFlag.AlignCenter, text)

    # ── API pública ──────────────────────────────────────────────────

    def set_volume(self, value: int) -> None:
        """Define o volume sem emitir volume_changed (para updates do backend)."""
        self.blockSignals(True)
        self.setValue(value)
        self.blockSignals(False)

    # ── Estilo dinâmico ──────────────────────────────────────────────

    def _apply_style(self) -> None:
        """Regenera o QSS com a largura atual do groove."""
        w = self._groove_width
        handle_h = w if w > THIN_WIDTH else 0
        handle_radius = w // 2

        # Cores Catppuccin Mocha
        groove_bg = "#313244"       # Base escura
        fill_color = "#cba6f7"      # Mauve (cor de destaque do PEEK)
        handle_color = "#cdd6f4"    # Text claro

        self.setStyleSheet(f"""
            QSlider::groove:vertical {{
                background: {groove_bg};
                width: {w}px;
                border-radius: {w // 2}px;
            }}

            QSlider::sub-page:vertical {{
                background: {groove_bg};
                border-radius: {w // 2}px;
            }}

            QSlider::add-page:vertical {{
                background: {fill_color};
                border-radius: {w // 2}px;
            }}

            QSlider::handle:vertical {{
                background: {handle_color};
                height: {handle_h}px;
                width: {w}px;
                margin: 0 0;
                border-radius: {handle_radius}px;
            }}
        """)


class VolumeSlider(QWidget):
    """Container que agrupa o ícone do aplicativo e o slider de volume.
    
    Expõe a mesma API do antigo VolumeSlider (volume_changed, set_volume).
    """
    
    volume_changed = Signal(int)
    mute_toggled   = Signal(int)  # Emite o index do stream PulseAudio
    
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        
        self._stream_index: int = -1  # -1 = master, outro = sink input index
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        
        # Ícone clicável do app (ClickableIcon: leftClicked = toggle mute)
        self._icon_label = ClickableIcon()
        self._icon_label.setFixedSize(16, 16)
        self._app_name: str = ""
        self._icon_label.leftClicked.connect(self._on_icon_left_click)
        
        # Slider
        self._slider = _ThinSlider()
        
        layout.addWidget(self._icon_label)
        layout.addWidget(self._slider, 0, Qt.AlignmentFlag.AlignHCenter)
        
        # Conecta sinais
        self._slider.volume_changed.connect(self.volume_changed)
        
        # Configura ícone padrão (Master)
        self.set_icon("audio-volume-high")

    def set_volume(self, value: int) -> None:
        self._slider.set_volume(value)

    def is_dragging(self) -> bool:
        """Retorna True se o usuário estiver arrastando/pressionando o slider."""
        return self._slider.isSliderDown()

    def set_icon(self, app_name: str) -> None:
        """Tenta buscar um ícone de tema pelo nome do app. Falha segura para volume padrão."""
        self._app_name = app_name
        icon = QIcon.fromTheme(app_name.lower())
        if icon.isNull():
            icon = QIcon.fromTheme("audio-volume-high")
        pixmap = icon.pixmap(16, 16)
        self._icon_label.setPixmap(pixmap)

    def set_stream_index(self, index: int) -> None:
        """Registra o index do sink input PulseAudio para mute toggle."""
        self._stream_index = index

    def set_muted(self, muted: bool) -> None:
        """Atualiza o feedback visual de mute via opacidade.

        Mutado  → 40% de opacidade (palído, claramente silenciado).
        Ativo   → 100% de opacidade.
        """
        effect = self.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(effect)
        effect.setOpacity(0.4 if muted else 1.0)
        self.update()
        self.repaint()

    def _on_icon_left_click(self) -> None:
        """Emite mute_toggled com o index do stream (substitui print placeholder)."""
        if self._stream_index >= 0:
            # Otimismo visual imediato (sem delay do polling do PulseAudio)
            effect = self.graphicsEffect()
            if isinstance(effect, QGraphicsOpacityEffect):
                # Se está 1.0 (não mutado), fingimos que mutou. Se está 0.4, fingimos que desmutou.
                current_is_muted = effect.opacity() < 0.5
                self.set_muted(not current_is_muted)
                
            self.mute_toggled.emit(self._stream_index)
