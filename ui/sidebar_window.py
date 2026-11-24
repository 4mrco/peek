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

import time
from typing import Any

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
from PySide6.QtGui import QAction, QColor, QEnterEvent, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QSlider,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ui.components.clickable_icon import ClickableIcon
from ui.components.media_player import MediaPlayerWidget
from ui.components.volume_slider import VolumeSlider


class SidebarWindow(QWidget):
    """Painel que desliza da borda direita da tela."""

    mouse_entered      = Signal()
    mouse_left         = Signal()
    hidden_fully       = Signal()
    menu_opened        = Signal()
    menu_closed        = Signal()
    app_volume_changed = Signal(int, int)
    app_mute_toggled   = Signal(int)  # index do stream PulseAudio
    master_mute_requested = Signal()  # toggle do volume master global
    mic_mute_requested = Signal()     # toggle do microfone global
    output_cycle_requested = Signal() # toggle de saída de áudio
    output_menu_requested = Signal(QPoint)
    mic_menu_requested    = Signal(QPoint)
    player_menu_requested = Signal(QPoint)
    sink_selected         = Signal(str)
    source_selected       = Signal(str)
    player_selected       = Signal(str)
    balance_changed       = Signal(float)
    mono_toggled          = Signal(bool)

    WIDTH: int = 360
    ANIMATION_DURATION_MS: int = 250
    LEFT_HOVER_MARGIN: int = 20
    RIGHT_HOVER_MARGIN: int = 20
    TOP_OFFSET: int = 24

    def __init__(self, parent: QWidget | None = None) -> None:
        # Recupera o parent (âncora) passado pelo Controller para satisfazer
        # a restrição de xdg_popup do Wayland quando usamos Qt.ToolTip.
        super().__init__(parent)
        self._is_visible: bool = False
        self._trigger_edge: str = "TopRight"
        self._bg_color: str = "#1e1e2e"
        self._primary_color: str = "#cba6f7"
        self._bg_opacity: int = 92
        self._open_menus: int = 0
        self._menu_closed_at: float = 0.0

        # Ativa o rastreamento nativo do mouse + HoverMove (p/ guarda pós-QMenu no Wayland)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

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
        # O SidebarWindow em si fica transparente
        self.setStyleSheet("SidebarWindow { background: transparent; }")

        # O layout principal da janela (transparente) que segura o card principal
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(self.LEFT_HOVER_MARGIN, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

        # ── Card Principal Unificado (QFrame) ──
        self.main_card = QFrame()
        self.main_card.setObjectName("mainCard")
        self._apply_card_style()
        
        # O layout interno do card principal (contém Mixer na esq. e Player na dir.)
        card_layout = QHBoxLayout(self.main_card)
        card_layout.setContentsMargins(10, 16, 10, 16)  # L/R reduzido de 16→10 (+12px úteis)
        card_layout.setSpacing(12)

        # ── Área do Mixer (Esquerda): sliders + controles globais ──
        mixer_col = QVBoxLayout()
        mixer_col.setContentsMargins(0, 0, 0, 0)
        mixer_col.setSpacing(8)

        # Sliders de app (row horizontal)
        self._mixer_layout = QHBoxLayout()
        self._mixer_layout.setContentsMargins(0, 0, 0, 0)
        self._mixer_layout.setSpacing(8)

        # Slider de volume master (sempre presente)
        self.volume_slider = VolumeSlider()
        self.volume_slider.setMouseTracking(True)
        self.volume_slider.master_mute_toggled.connect(self.master_mute_requested.emit)
        self.volume_slider.context_menu_requested.connect(self.output_menu_requested.emit)
        self._mixer_layout.addWidget(
            self.volume_slider,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )

        mixer_col.addLayout(self._mixer_layout)
        mixer_col.addStretch()
        mixer_col.addLayout(self._build_global_controls())

        card_layout.addLayout(mixer_col, 0)

        # ── Card de controle de mídia (Direita) ──
        self.media_player = MediaPlayerWidget()
        self.media_player.setMouseTracking(True)
        self.media_player.player_menu_requested.connect(self.player_menu_requested.emit)
        card_layout.addWidget(self.media_player, 1, Qt.AlignmentFlag.AlignTop)

        # Adiciona o card principal na janela
        main_layout.addWidget(self.main_card)

    def _setup_animation(self) -> None:
        self._animation = QPropertyAnimation(self, b"geometry")
        self._animation.setDuration(self.ANIMATION_DURATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._on_animation_finished)

    # ── Helpers ──────────────────────────────────────────────────────

    def _build_global_controls(self) -> QHBoxLayout:
        """Linha de controles globais (Mic + Speaker) ancorada ao fundo esquerdo."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        # ── Microfone ──
        self._mic_icon = ClickableIcon()
        self._mic_icon.setFixedSize(18, 18)
        mic_px = QIcon.fromTheme("microphone-sensitivity-high").pixmap(18, 18)
        if mic_px.isNull():
            mic_px = QIcon.fromTheme("audio-input-microphone").pixmap(18, 18)
        self._mic_icon.setPixmap(mic_px)
        self._mic_icon.setToolTip("")  # O KDE as vezes injeta o nome do ícone, forçamos vazio
        self._mic_icon.leftClicked.connect(self._on_mic_left_click)
        self._mic_icon.rightClicked.connect(
            lambda: self.mic_menu_requested.emit(
                self._mic_icon.mapToGlobal(QPoint(0, self._mic_icon.height() + 4))
            )
        )

        # ── Saída de Áudio (Speaker) ──
        self._speaker_icon = ClickableIcon()
        self._speaker_icon.setFixedSize(18, 18)
        # Usa Mode.Normal + State.On para garantir a cor ativa (branca),
        # evitando a renderização cinza do modo Disabled/Off em alguns temas.
        spk_icon = QIcon.fromTheme("audio-volume-high")
        if spk_icon.isNull():
            spk_icon = QIcon.fromTheme("audio-card")
        spk_px = spk_icon.pixmap(18, 18, QIcon.Mode.Normal, QIcon.State.On)
        self._speaker_icon.setPixmap(spk_px)
        self._speaker_icon.setToolTip("")  # Extermínio de tooltip
        self._speaker_icon.leftClicked.connect(self.output_cycle_requested.emit)
        self._speaker_icon.rightClicked.connect(
            lambda: self.output_menu_requested.emit(
                self._speaker_icon.mapToGlobal(QPoint(0, self._speaker_icon.height() + 4))
            )
        )

        row.addWidget(self._mic_icon,     0, Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self._speaker_icon, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch()
        return row

    def _on_mic_left_click(self) -> None:
        """Otimismo visual para o microfone global e emissão do sinal real."""
        effect = self._mic_icon.graphicsEffect()
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        current_opacity = effect.opacity() if isinstance(effect, QGraphicsOpacityEffect) else 1.0
        
        # Otimismo: Se opacidade atual é >= 0.5 (ativo), finge que mutou (passa True), senão False.
        self.on_mic_mute_changed(current_opacity > 0.5)
        self._mic_icon.update()
        self._mic_icon.repaint()
        
        self.mic_mute_requested.emit()


    def _screen_geometry(self) -> QRect:
        """Retorna a geometria disponível do monitor principal.

        Usa QGuiApplication.primaryScreen() para leitura direta do hardware,
        ignorando qualquer contexto de janela Qt ativa. availableGeometry()
        exclui painéis/taskbar do KDE, garantindo posicionamento correto.
        """
        screen = QGuiApplication.primaryScreen()
        assert screen is not None, "Nenhum monitor detectado"
        return screen.availableGeometry()

    def set_trigger_edge(self, edge: str) -> None:
        """Alterna ancoragem e espelhamento de borda entre TopRight e TopLeft."""
        normalized = "TopLeft" if edge == "TopLeft" else "TopRight"
        self._trigger_edge = normalized
        if self.layout():
            if normalized == "TopLeft":
                self.layout().setContentsMargins(0, 0, self.RIGHT_HOVER_MARGIN, 0)
            else:
                self.layout().setContentsMargins(self.LEFT_HOVER_MARGIN, 0, 0, 0)
        self._apply_card_style()
        self._sync_layout_geometry()
        if self._is_visible:
            self._reanchor_edge()

    def apply_appearance(self, bg_color: str, primary_color: str, bg_opacity: int) -> None:
        """Aplica cor de fundo, cor principal, transparência e Magic Gradient."""
        if QColor(bg_color).isValid():
            self._bg_color = bg_color
        if QColor(primary_color).isValid():
            self._primary_color = primary_color
        self._bg_opacity = max(30, min(100, int(bg_opacity)))

        self._apply_card_style()
        if hasattr(self, "media_player"):
            self.media_player.apply_accent_color(self._primary_color)
        if hasattr(self, "volume_slider"):
            self.volume_slider.apply_accent_color(self._primary_color)
        for slider in self._app_sliders.values():
            slider.apply_accent_color(self._primary_color)

    def _apply_card_style(self) -> None:
        """Gera QSS do main_card com Magic Gradient RGBA e bordas espelhadas."""
        base = QColor(self._bg_color)
        if not base.isValid():
            base = QColor("#1e1e2e")
        accent = QColor(self._primary_color)
        if not accent.isValid():
            accent = QColor("#cba6f7")

        # Mistura sutil da cor de destaque no topo (6%) para dar profundidade estilo Breeze/Qt6
        top_lifted = base.lighter(118)
        top_r = round(top_lifted.red() * 0.94 + accent.red() * 0.06)
        top_g = round(top_lifted.green() * 0.94 + accent.green() * 0.06)
        top_b = round(top_lifted.blue() * 0.94 + accent.blue() * 0.06)
        bottom = base.darker(115)

        alpha = round(self._bg_opacity / 100.0, 3)
        alpha_bottom = round(min(1.0, alpha + 0.04), 3)

        stop_top = f"rgba({top_r}, {top_g}, {top_b}, {alpha})"
        stop_mid = f"rgba({base.red()}, {base.green()}, {base.blue()}, {alpha})"
        stop_bot = f"rgba({bottom.red()}, {bottom.green()}, {bottom.blue()}, {alpha_bottom})"

        if self._trigger_edge == "TopLeft":
            radius_css = (
                "border-top-left-radius: 0px;\n"
                "border-bottom-left-radius: 0px;\n"
                "border-top-right-radius: 16px;\n"
                "border-bottom-right-radius: 16px;\n"
                "border: 1px solid rgba(255, 255, 255, 0.08);\n"
                "border-left: none;"
            )
        else:
            radius_css = (
                "border-top-left-radius: 16px;\n"
                "border-bottom-left-radius: 16px;\n"
                "border-top-right-radius: 0px;\n"
                "border-bottom-right-radius: 0px;\n"
                "border: 1px solid rgba(255, 255, 255, 0.08);\n"
                "border-right: none;"
            )

        self.main_card.setStyleSheet(
            f"""
            #mainCard {{
                background-color: qlineargradient(
                    x1:0, y1:0, x2:0.25, y2:1,
                    stop:0 {stop_top},
                    stop:0.55 {stop_mid},
                    stop:1 {stop_bot}
                );
                {radius_css}
            }}
            """
        )

    def _sync_layout_geometry(self) -> None:
        """Força recálculo síncrono do layout e sizeHint mesmo com a janela oculta."""
        if (self.layout() and
                self.layout().sizeConstraint() != QLayout.SizeConstraint.SetFixedSize):
            self.layout().setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self.main_card.updateGeometry()
        self.updateGeometry()
        if self.main_card.layout():
            self.main_card.layout().activate()
        if self.layout():
            self.layout().activate()

    def _reanchor_edge(self) -> None:
        """Mantém a borda ativa (TopRight ou TopLeft) colada na margem da tela."""
        geo = self._screen_geometry()
        parent_pos = self.parentWidget().pos() if self.parentWidget() else QPoint(0, 0)
        if self._trigger_edge == "TopLeft":
            new_x = geo.x() - parent_pos.x()
        else:
            new_x = geo.x() + geo.width() - self.width() - parent_pos.x()
        if self.x() != new_x:
            self.move(new_x, self.y())

    def _reanchor_right(self) -> None:
        """Alias legado mantido para compatibilidade."""
        self._reanchor_edge()

    def _offscreen_rect(self) -> QRect:
        """Retângulo posicionado fora da tela conforme a borda ativa."""
        if (self.layout() and
                self.layout().sizeConstraint() != QLayout.SizeConstraint.SetFixedSize):
            self.layout().setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        geo = self._screen_geometry()
        w   = self.sizeHint().width()
        h   = self.sizeHint().height()
        parent_pos = self.parentWidget().pos() if self.parentWidget() else QPoint(0, 0)
        if self._trigger_edge == "TopLeft":
            x = geo.x() - w - parent_pos.x()
        else:
            x = geo.x() + geo.width() - parent_pos.x()
        return QRect(
            x,
            geo.y() + self.TOP_OFFSET - parent_pos.y(),
            w, h,
        )

    def _onscreen_rect(self) -> QRect:
        """Retângulo posicionado na borda ativa da tela (TopRight ou TopLeft)."""
        if (self.layout() and
                self.layout().sizeConstraint() != QLayout.SizeConstraint.SetFixedSize):
            self.layout().setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        geo = self._screen_geometry()
        w   = self.sizeHint().width()
        h   = self.sizeHint().height()
        parent_pos = self.parentWidget().pos() if self.parentWidget() else QPoint(0, 0)
        if self._trigger_edge == "TopLeft":
            x = geo.x() - parent_pos.x()
        else:
            x = geo.x() + geo.width() - w - parent_pos.x()
        return QRect(
            x,
            geo.y() + self.TOP_OFFSET - parent_pos.y(),
            w, h,
        )

    # ── Public API ───────────────────────────────────────────────────

    def slide_in(self) -> None:
        """Desliza o painel para dentro da tela."""
        if self._is_visible:
            return
        self._is_visible = True

        self._sync_layout_geometry()
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
        layout_changed: bool = False
        
        # Remove sliders de apps que foram fechados
        for idx in list(self._app_sliders.keys()):
            if idx not in current_indices:
                slider = self._app_sliders.pop(idx)
                self._mixer_layout.removeWidget(slider)
                slider.hide()
                slider.setParent(None)
                slider.deleteLater()
                layout_changed = True
                
        # Atualiza ou instancia novos sliders
        for app in apps:
            idx  = app["index"]
            name = app["name"]
            vol  = app["volume"]
            mute = app["mute"]
            
            if idx in self._app_sliders:
                slider = self._app_sliders[idx]
                slider.set_icon(name)
                slider.set_muted(mute)
                # Só atualiza o volume visual se o usuário NÃO estiver segurando o slider
                if not slider.is_dragging():
                    slider.set_volume(vol)
            else:
                slider = VolumeSlider()
                slider.set_icon(name)
                slider.set_volume(vol)
                slider.set_muted(mute)
                slider.setMouseTracking(True)
                slider.set_stream_index(idx)
                slider.apply_accent_color(self._primary_color)
                
                # Usando default argument (idx=idx) para capturar o valor no loop
                slider.volume_changed.connect(lambda v, idx=idx: self.app_volume_changed.emit(idx, v))
                slider.mute_toggled.connect(self.app_mute_toggled)
                
                self._app_sliders[idx] = slider
                self._mixer_layout.addWidget(slider, 0, Qt.AlignmentFlag.AlignVCenter)
                layout_changed = True

        if layout_changed:
            self._sync_layout_geometry()

    def _on_animation_finished(self) -> None:
        """Esconde a janela depois da animação de saída e sinaliza o controlador."""
        if not self._is_visible:
            self.hide()
            self.hidden_fully.emit()
        else:
            self._reanchor_edge()

    # ── Menus de Contexto (Fase 3 — Subtle Menus) ────────────────────

    def _build_context_menu(self) -> QMenu:
        """Cria QMenu nativo estilizado e não-bloqueante com guarda de auto-hide."""
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.setStyleSheet(
            f"""
            QMenu {{
                background-color: #181825;
                color: #cdd6f4;
                border: 1px solid #313244;
                border-radius: 10px;
                padding: 6px;
                font-size: 12px;
            }}
            QMenu::item {{
                padding: 6px 14px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background-color: #313244;
                color: {self._primary_color};
            }}
            QMenu::item:disabled {{
                color: #6c7086;
            }}
            QMenu::separator {{
                height: 1px;
                background: #313244;
                margin: 4px 8px;
            }}
            """
        )
        return menu

    def _popup_menu(self, menu: QMenu, global_pos: QPoint) -> None:
        state = {"open": False}

        def _on_show() -> None:
            if not state["open"]:
                state["open"] = True
                self._on_menu_about_to_show()

        def _on_close() -> None:
            if state["open"]:
                state["open"] = False
                self._on_menu_about_to_hide()

        menu.aboutToShow.connect(_on_show)
        menu.aboutToHide.connect(_on_close)
        menu.destroyed.connect(_on_close)
        menu.popup(global_pos)

    def _on_menu_about_to_show(self) -> None:
        self._open_menus += 1
        self.menu_opened.emit()

    def _on_menu_about_to_hide(self) -> None:
        self._open_menus = max(0, self._open_menus - 1)
        if self._open_menus == 0:
            self._menu_closed_at = time.monotonic()
            self.menu_closed.emit()

    @staticmethod
    def _format_balance_text(val: int) -> str:
        if abs(val) < 3:
            return "Balanço: Centro"
        if val < 0:
            return f"Balanço: L {abs(val)}%"
        return f"Balanço: R {val}%"

    def show_output_menu(
        self,
        sinks: list[dict[str, Any]],
        master_muted: bool,
        balance: float,
        mono_enabled: bool,
        global_pos: QPoint,
    ) -> None:
        """Abre menu de contexto da saída de áudio (sinks, balanço, mono/estéreo e mute)."""
        menu = self._build_context_menu()
        header = menu.addAction("Saída de Áudio")
        header.setEnabled(False)
        menu.addSeparator()

        if sinks:
            for sink in sinks:
                desc = str(sink.get("description") or sink.get("name") or "Desconhecido")
                name = str(sink.get("name") or "")
                action = QAction(desc, menu)
                action.setCheckable(True)
                action.setChecked(bool(sink.get("is_default")))
                action.triggered.connect(lambda _chk=False, n=name: self.sink_selected.emit(n))
                menu.addAction(action)
        else:
            empty = menu.addAction("Nenhuma saída detectada")
            empty.setEnabled(False)

        menu.addSeparator()

        # Controle inline de Balanço L/R (clique no título centraliza em 0)
        bal_container = QWidget(menu)
        bal_layout = QVBoxLayout(bal_container)
        bal_layout.setContentsMargins(12, 6, 12, 6)
        bal_layout.setSpacing(4)

        init_val = max(-100, min(100, round(float(balance) * 100)))

        bal_header = QHBoxLayout()
        bal_header.setContentsMargins(0, 0, 0, 0)
        lbl_l = QLabel("L")
        lbl_l.setStyleSheet("color: #a6adc8; font-size: 11px; font-weight: bold;")
        lbl_center = ClickableIcon()
        lbl_center.setText(self._format_balance_text(init_val))
        lbl_center.setStyleSheet("color: #cdd6f4; font-size: 11px;")
        lbl_r = QLabel("R")
        lbl_r.setStyleSheet("color: #a6adc8; font-size: 11px; font-weight: bold;")
        bal_header.addWidget(lbl_l)
        bal_header.addStretch()
        bal_header.addWidget(lbl_center)
        bal_header.addStretch()
        bal_header.addWidget(lbl_r)
        bal_layout.addLayout(bal_header)

        bal_slider = QSlider(Qt.Orientation.Horizontal, bal_container)
        bal_slider.setObjectName("balanceMenuSlider")
        bal_slider.setRange(-100, 100)
        bal_slider.setSingleStep(5)
        bal_slider.setValue(init_val)
        bal_slider.setFixedWidth(180)
        bal_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        bal_slider.setStyleSheet(
            f"""
            QSlider::groove:horizontal {{
                background: #313244;
                height: 5px;
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {self._primary_color};
                width: 12px;
                height: 12px;
                margin: -4px 0;
                border-radius: 6px;
            }}
            """
        )

        def _on_bal_changed(val: int) -> None:
            lbl_center.setText(self._format_balance_text(val))
            self.balance_changed.emit(val / 100.0)

        bal_slider.valueChanged.connect(_on_bal_changed)
        lbl_center.leftClicked.connect(lambda: bal_slider.setValue(0))
        bal_layout.addWidget(bal_slider)

        bal_action = QWidgetAction(menu)
        bal_action.setDefaultWidget(bal_container)
        menu.addAction(bal_action)

        act_mono = QAction("Áudio Mono (Downmix)", menu)
        act_mono.setCheckable(True)
        act_mono.setChecked(bool(mono_enabled))
        act_mono.triggered.connect(lambda checked=False: self.mono_toggled.emit(bool(checked)))
        menu.addAction(act_mono)

        menu.addSeparator()
        act_mute_out = menu.addAction("Desmutar Saída Master" if master_muted else "Mutar Saída Master")
        act_mute_out.triggered.connect(self.master_mute_requested.emit)

        self._popup_menu(menu, global_pos)

    def show_mic_menu(
        self,
        sources: list[dict[str, Any]],
        mic_muted: bool,
        global_pos: QPoint,
    ) -> None:
        """Abre menu de contexto com seleção direta de entrada (microfone)."""
        menu = self._build_context_menu()
        header = menu.addAction("Entrada (Microfone)")
        header.setEnabled(False)
        menu.addSeparator()

        if sources:
            for src in sources:
                desc = str(src.get("description") or src.get("name") or "Desconhecido")
                name = str(src.get("name") or "")
                action = QAction(desc, menu)
                action.setCheckable(True)
                action.setChecked(bool(src.get("is_default")))
                action.triggered.connect(lambda _chk=False, n=name: self.source_selected.emit(n))
                menu.addAction(action)
        else:
            empty = menu.addAction("Nenhum microfone detectado")
            empty.setEnabled(False)

        menu.addSeparator()
        act_mute_mic = menu.addAction("Desmutar Microfone" if mic_muted else "Mutar Microfone")
        act_mute_mic.triggered.connect(self.mic_mute_requested.emit)

        self._popup_menu(menu, global_pos)

    def show_player_menu(self, players: list[dict[str, Any]], global_pos: QPoint) -> None:
        """Abre menu de contexto com seleção direta do player MPRIS ativo."""
        menu = self._build_context_menu()
        header = menu.addAction("Players MPRIS")
        header.setEnabled(False)
        menu.addSeparator()

        if players:
            for p in players:
                identity = str(p.get("identity") or "Player")
                status = str(p.get("status") or "")
                service = str(p.get("service") or "")
                suffix = " (▶)" if status == "Playing" else " (⏸)" if status == "Paused" else ""
                action = QAction(f"{identity}{suffix}", menu)
                action.setCheckable(True)
                action.setChecked(bool(p.get("is_active")))
                action.triggered.connect(lambda _chk=False, s=service: self.player_selected.emit(s))
                menu.addAction(action)
        else:
            empty = menu.addAction("Nenhum player ativo")
            empty.setEnabled(False)

        self._popup_menu(menu, global_pos)

    @Slot(bool)
    def on_mic_mute_changed(self, muted: bool) -> None:
        """Atualiza o visual do botão do Mic quando o estado de mute mudar.

        Mutado  → ativo com ícone muted + opacidade 0.4
        Ativo   → ícone normal + opacidade 1.0
        """
        if muted:
            icon_name = "microphone-sensitivity-muted"
            fallback  = "audio-input-microphone-muted"
            opacity   = 0.4
        else:
            icon_name = "microphone-sensitivity-high"
            fallback  = "audio-input-microphone"
            opacity   = 1.0

        from PySide6.QtGui import QIcon
        px = QIcon.fromTheme(icon_name).pixmap(18, 18, QIcon.Mode.Normal, QIcon.State.On)
        if px.isNull():
            px = QIcon.fromTheme(fallback).pixmap(18, 18, QIcon.Mode.Normal, QIcon.State.On)
        if not px.isNull():
            self._mic_icon.setPixmap(px)

        from PySide6.QtWidgets import QGraphicsOpacityEffect
        effect = self._mic_icon.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self._mic_icon)
            self._mic_icon.setGraphicsEffect(effect)
        effect.setOpacity(opacity)
        self._mic_icon.update()
        self._mic_icon.repaint()

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
        self._reanchor_edge()

    def event(self, event: QEvent) -> bool:  # noqa: N802
        if (
            event.type() == QEvent.Type.HoverMove
            and self._open_menus == 0
            and self._is_visible
        ):
            self.mouse_entered.emit()
        return super().event(event)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        # Ignora Enter sintético disparado pelo Wayland/Qt logo após fechar um QMenu popup
        if (time.monotonic() - self._menu_closed_at) >= 0.15:
            self.mouse_entered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        if self._open_menus == 0:
            self.mouse_left.emit()
        super().leaveEvent(event)

