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
    QPoint,
    QRect,
    Qt,
    QTimer,
    Signal,
    Slot
)
from PySide6.QtGui import QEnterEvent, QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLayout, QVBoxLayout, QWidget, QFrame
from typing import Any

from ui.components.media_player import MediaPlayerWidget
from ui.components.volume_slider import VolumeSlider


class SidebarWindow(QWidget):
    """Painel que desliza da borda direita da tela."""

    mouse_entered = Signal()
    mouse_left = Signal()
    hidden_fully = Signal()
    app_volume_changed = Signal(int, int)

    WIDTH: int = 360
    ANIMATION_DURATION_MS: int = 250

    def __init__(self, parent: QWidget | None = None) -> None:
        # Recupera o parent (âncora) passado pelo Controller para satisfazer
        # a restrição de xdg_popup do Wayland quando usamos Qt.ToolTip.
        super().__init__(parent)
        self._is_visible: bool = False

        # Ativa o rastreamento nativo do mouse (inclusive nos filhos principais)
        self.setMouseTracking(True)

        self._app_sliders: dict[int, VolumeSlider] = {}

        self._setup_window_flags()

        # ── Early Severing (Wayland) ──────────────────────────────────
        # WA_NativeWindow força a criação imediata do QWindow nativo ANTES do
        # primeiro show().
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)

        # Título distinto: o KWin faz cache de geometria por WM_CLASS/title.
        # "PeekSidebar" isola completamente o cache da sidebar do Control Center.
        self.setWindowTitle("PeekSidebar")

        self._setup_ui()
        self._setup_animation()

    # ── Properties ───────────────────────────────────────────────────

    @property
    def is_visible(self) -> bool:
        return self._is_visible

    # ── Setup ────────────────────────────────────────────────────────

    def _setup_window_flags(self) -> None:
        """Configura as flags Wayland da sidebar.

        A arquitetura multi-processo permite o uso seguro do BypassWindowManagerHint 
        junto com ToolTip sem risco de herdar janelas pai erradas.
        Essa combinação liberta a janela do "Smart Placement" do KWin e 
        permite usar posicionamento X/Y absoluto de tela (evitando a centralização forçada).
        """
        self.setWindowFlags(
            Qt.WindowType.ToolTip
            | Qt.WindowType.BypassWindowManagerHint
            | Qt.WindowType.FramelessWindowHint
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
        """Retorna a geometria disponível do monitor principal.

        Usa QGuiApplication.primaryScreen() para leitura direta do hardware,
        ignorando qualquer contexto de janela Qt ativa. availableGeometry()
        exclui painéis/taskbar do KDE, garantindo posicionamento correto.
        """
        screen = QGuiApplication.primaryScreen()
        assert screen is not None, "Nenhum monitor detectado"
        return screen.availableGeometry()

    def _offscreen_rect(self) -> QRect:
        """Retângulo posicionado fora da tela (à direita).

        SetFixedSize trava a janela ao tamanho exato do layout, impedindo
        o "gap transparente" que causava leaveEvent falso na borda direita.
        """
        if (self.layout() and
                self.layout().sizeConstraint() != QLayout.SizeConstraint.SetFixedSize):
            self.layout().setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        geo = self._screen_geometry()
        w   = self.sizeHint().width()
        h   = self.sizeHint().height()
        parent_pos = self.parentWidget().pos() if self.parentWidget() else QPoint(0, 0)
        return QRect(
            geo.x() + geo.width() - parent_pos.x(),
            geo.y() - parent_pos.y(),
            w, h,
        )

    def _onscreen_rect(self) -> QRect:
        """Retângulo posicionado na borda direita da tela.

        O crescimento dinâmico (novos sliders) expande para a esquerda,
        mantendo a borda direita colada na margem da tela.
        """
        if (self.layout() and
                self.layout().sizeConstraint() != QLayout.SizeConstraint.SetFixedSize):
            self.layout().setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        geo = self._screen_geometry()
        w   = self.sizeHint().width()
        h   = self.sizeHint().height()
        parent_pos = self.parentWidget().pos() if self.parentWidget() else QPoint(0, 0)
        return QRect(
            geo.x() + geo.width() - w - parent_pos.x(),
            geo.y() - parent_pos.y(),
            w, h,
        )

    # ── Public API ───────────────────────────────────────────────────

    def slide_in(self) -> None:
        """Desliza o painel para dentro da tela."""
        if self._is_visible:
            return
        self._is_visible = True

        off_rect = self._offscreen_rect()
        on_rect = self._onscreen_rect()

        # Posiciona fora da tela ANTES de mostrar, para evitar flash visual
        self.setGeometry(off_rect)
        self.show()

        # [WAYLAND/KWIN BYPASS]
        # O KWin tem um bug agressivo de Smart Placement que pode sobrescrever a 
        # geometria no exato instante em que o XDG Toplevel é mapeado.
        # Empurrar a definição para o próximo ciclo de eventos anula isso.
        QTimer.singleShot(50, lambda: self.setGeometry(off_rect))

        self._animation.stop()
        self._animation.setStartValue(off_rect)
        self._animation.setEndValue(on_rect)
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
        """Esconde a janela depois da animação de saída e sinaliza o controlador."""
        if not self._is_visible:
            self.hide()
            self.hidden_fully.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Reancora a borda direita quando o layout expande dinamicamente.

        Só age quando o painel está visível e a animação não está rodando,
        evitando conflito com os setGeometry() frame-a-frame da QPropertyAnimation.
        """
        super().resizeEvent(event)
        if not getattr(self, '_is_visible', False):
            return
        if hasattr(self, '_animation') and self._animation.state() == self._animation.State.Running:
            return
        geo        = self._screen_geometry()
        parent_pos = self.parentWidget().pos() if self.parentWidget() else QPoint(0, 0)
        new_x      = geo.x() + geo.width() - self.width() - parent_pos.x()
        self.move(new_x, self.y())

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self.mouse_entered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self.mouse_left.emit()
        super().leaveEvent(event)

