"""Painel lateral (sidebar) com animação de slide.

O painel começa fora da tela e desliza para dentro via QPropertyAnimation.
Emite sinais de mouse_entered/mouse_left para que o Controller saiba
quando o cursor está sobre ele.

Auto-hide robusto:
  - leaveEvent emite mouse_left normalmente (saída pela esquerda).
  - mouseMoveEvent detecta se o usuário desceu o mouse além do conteúdo visível,
    contornando o bug do Wayland onde o mouse fica preso no limite físico da tela.

Wayland/KDE Plasma:
  - FramelessWindowHint + Tool + WindowStaysOnTopHint: remove decoração,
    não aparece no alt-tab nem taskbar, fica acima das outras janelas.
  - desktopFileName("peek") no main.py define o app_id para matching
    de KWin Rules (caso as flags sejam insuficientes no seu setup).
  - Se o KWin não respeitar o setGeometry() para posicionamento,
    forneça uma KWin Rule para forçar a posição (veja scripts/).
"""

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QRect,
    Qt,
    Signal,
    Slot
)
from PySide6.QtGui import QEnterEvent, QMouseEvent
from PySide6.QtGui import QEnterEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QVBoxLayout, QWidget, QFrame
from typing import Any

from ui.components.media_player import MediaPlayerWidget
from ui.components.volume_slider import VolumeSlider


class SidebarWindow(QWidget):
    """Painel que desliza da borda direita da tela."""

    mouse_entered = Signal()
    mouse_left = Signal()
    app_volume_changed = Signal(int, int)

    WIDTH: int = 360
    ANIMATION_DURATION_MS: int = 250

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._is_visible: bool = False
        
        # Ativa o rastreamento nativo do mouse (inclusive nos filhos principais)
        self.setMouseTracking(True)
        
        self._app_sliders: dict[int, VolumeSlider] = {}
        
        self._setup_window_flags()
        self._setup_ui()
        self._setup_animation()

    # ── Properties ───────────────────────────────────────────────────

    @property
    def is_visible(self) -> bool:
        return self._is_visible

    # ── Setup ────────────────────────────────────────────────────────

    def _setup_window_flags(self) -> None:
        """Configura a janela para flutuar sem decoração no Wayland."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

    def _setup_ui(self) -> None:
        """Layout principal da sidebar com widgets de funcionalidade."""
        self.setFixedWidth(self.WIDTH)
        # O SidebarWindow em si fica transparente
        self.setStyleSheet("SidebarWindow { background: transparent; }")

        # O layout principal da janela (transparente) que segura o card principal
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 24, 0, 24)
        main_layout.setSpacing(0)

        # ── Card Principal Unificado (QFrame) ──
        self.main_card = QFrame()
        self.main_card.setObjectName("mainCard")
        self.main_card.setStyleSheet(
            """
            #mainCard {
                background-color: #1e1e2e;
                border-top-left-radius: 16px;
                border-bottom-left-radius: 16px;
            }
            """
        )
        
        # O layout interno do card principal (contém Mixer na esq. e Player na dir.)
        card_layout = QHBoxLayout(self.main_card)
        card_layout.setContentsMargins(10, 16, 10, 16)  # L/R reduzido de 16→10 (+12px úteis)
        card_layout.setSpacing(12)

        # ── Área do Mixer (Esquerda) ──
        self._mixer_layout = QHBoxLayout()
        self._mixer_layout.setContentsMargins(0, 0, 0, 0)
        self._mixer_layout.setSpacing(8)

        # Slider de volume master (sempre presente)
        self.volume_slider = VolumeSlider()
        self.volume_slider.setMouseTracking(True)
        self._mixer_layout.addWidget(
            self.volume_slider,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )

        card_layout.addLayout(self._mixer_layout, 0)

        # ── Card de controle de mídia (Direita) ──
        self.media_player = MediaPlayerWidget()
        self.media_player.setMouseTracking(True)
        card_layout.addWidget(self.media_player, 1)

        # Adiciona o card principal na janela
        main_layout.addWidget(self.main_card)

    def _setup_animation(self) -> None:
        self._animation = QPropertyAnimation(self, b"geometry")
        self._animation.setDuration(self.ANIMATION_DURATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._on_animation_finished)

    # ── Helpers ──────────────────────────────────────────────────────

    def _screen_geometry(self) -> QRect:
        screen = QApplication.primaryScreen()
        assert screen is not None
        return screen.geometry()

    def _offscreen_rect(self) -> QRect:
        """Retângulo posicionado fora da tela (à direita)."""
        geo = self._screen_geometry()
        
        # Garante que os widgets estão calculados
        self.adjustSize()
        content_height = self.sizeHint().height()

        return QRect(
            geo.x() + geo.width(),
            geo.y(),
            self.WIDTH,
            content_height,
        )

    def _onscreen_rect(self) -> QRect:
        """Retângulo posicionado dentro da tela (borda direita)."""
        geo = self._screen_geometry()
        
        self.adjustSize()
        content_height = self.sizeHint().height()

        return QRect(
            geo.x() + geo.width() - self.WIDTH,
            geo.y(),
            self.WIDTH,
            content_height,
        )

    # ── Public API ───────────────────────────────────────────────────

    def slide_in(self) -> None:
        """Desliza o painel para dentro da tela."""
        if self._is_visible:
            return
        self._is_visible = True

        # Posiciona fora da tela ANTES de mostrar, para evitar flash visual
        self.setGeometry(self._offscreen_rect())
        self.show()

        self._animation.stop()
        self._animation.setStartValue(self._offscreen_rect())
        self._animation.setEndValue(self._onscreen_rect())
        self._animation.start()

    def slide_out(self) -> None:
        """Desliza o painel para fora da tela."""
        if not self._is_visible:
            return
        self._is_visible = False

        self._animation.stop()
        self._animation.setStartValue(self.geometry())
        self._animation.setEndValue(self._offscreen_rect())
        self._animation.start()

    # ── Events / Slots ───────────────────────────────────────────────

    @Slot(list)
    def update_app_sliders(self, apps: list[dict[str, Any]]) -> None:
        """Instancia ou limpa dinamicamente os sliders dos aplicativos, mantendo o drag intacto."""
        current_indices = {app["index"] for app in apps}
        
        # Remove sliders de apps que foram fechados
        for idx in list(self._app_sliders.keys()):
            if idx not in current_indices:
                slider = self._app_sliders.pop(idx)
                self._mixer_layout.removeWidget(slider)
                slider.deleteLater()
                
        # Atualiza ou instancia novos sliders
        for app in apps:
            idx = app["index"]
            name = app["name"]
            vol = app["volume"]
            
            if idx in self._app_sliders:
                slider = self._app_sliders[idx]
                slider.set_icon(name)
                # Só atualiza o volume visual se o usuário NÃO estiver segurando o slider
                if not slider.is_dragging():
                    slider.set_volume(vol)
            else:
                slider = VolumeSlider()
                slider.set_icon(name)
                slider.set_volume(vol)
                slider.setMouseTracking(True)
                
                # Usando default argument (idx=idx) para capturar o valor no loop
                slider.volume_changed.connect(lambda v, idx=idx: self.app_volume_changed.emit(idx, v))
                
                self._app_sliders[idx] = slider
                self._mixer_layout.addWidget(slider, 0, Qt.AlignmentFlag.AlignVCenter)

    def _on_animation_finished(self) -> None:
        """Esconde a janela depois da animação de saída."""
        if not self._is_visible:
            self.hide()

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self.mouse_entered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self.mouse_left.emit()
        super().leaveEvent(event)

