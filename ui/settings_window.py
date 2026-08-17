"""Centro de Controle do PEEK — janela de configurações e diagnóstico.

Abre como janela independente (não bloqueia a sidebar).
Expõe:
  - Toggle de serviço (pausa/retoma a escuta do KWin hotcorner)
  - Placeholder de atalho de teclado (para implementação futura)
  - Painel de logs: exibe as últimas 50 linhas de peek.log

Design: Catppuccin Mocha — consistente com a sidebar.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.logger import LOG_PATH

# ── Paleta Catppuccin Mocha ───────────────────────────────────────────────
_BG_BASE     = "#1e1e2e"
_BG_SURFACE  = "#181825"
_BG_MANTLE   = "#11111b"
_BG_OVERLAY  = "#313244"
_TEXT_MAIN   = "#cdd6f4"
_TEXT_SUB    = "#a6adc8"
_TEXT_MUTED  = "#6c7086"
_ACCENT_MAUVE = "#cba6f7"
_ACCENT_BLUE  = "#89b4fa"
_ACCENT_GREEN = "#a6e3a1"
_ACCENT_RED   = "#f38ba8"
_RADIUS       = "12px"
_RADIUS_SM    = "8px"

# ── Custom Slide Switch ───────────────────────────────────────────────────

class SlideSwitch(QWidget):
    """Slide Switch estilo iOS desenhado via QPainter.

    Emite o sinal ``toggled(bool)`` como um QCheckBox normal.
    """

    toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked: bool = False
        self._thumb_x: float = 0.0  # 0.0 = esquerda, 1.0 = direita

        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._anim = QPropertyAnimation(self, b"thumb_pos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    # ── Signal helpers ────────────────────────────────────────────────
    # O sinal Qt ``toggled`` é emitido automaticamente pelo toggle().
    # Use switch.toggled.connect(callback) como qualquer QWidget.

    # ── Property animada ──────────────────────────────────────────────
    def _get_thumb_pos(self) -> float:
        return self._thumb_x

    def _set_thumb_pos(self, value: float) -> None:
        self._thumb_x = value
        self.update()

    thumb_pos = Property(float, _get_thumb_pos, _set_thumb_pos)

    # ── API pública ───────────────────────────────────────────────────
    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        if self._checked == checked:
            return
        self._checked = checked
        self._animate_to(1.0 if checked else 0.0)

    def toggle(self) -> None:
        self._checked = not self._checked
        self._animate_to(1.0 if self._checked else 0.0)
        self.toggled.emit(self._checked)

    # ── Eventos ───────────────────────────────────────────────────────
    def mousePressEvent(self, event) -> None:
        self.toggle()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        r = h / 2
        margin = 3
        thumb_d = h - 2 * margin  # diâmetro da bolinha

        # ── Track (fundo) ─────────────────────────────────────────────
        track_color = QColor("#cba6f7") if self._checked else QColor("#313244")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track_color)
        p.drawRoundedRect(0, 0, w, h, r, r)

        # ── Thumb (bolinha) ───────────────────────────────────────────
        travel = w - 2 * margin - thumb_d
        thumb_x = int(margin + self._thumb_x * travel)
        thumb_y = margin
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(thumb_x, thumb_y, thumb_d, thumb_d)

        p.end()

    # ── Animação ──────────────────────────────────────────────────────
    def _animate_to(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._thumb_x)
        self._anim.setEndValue(target)
        self._anim.start()

class ControlCenter(QWidget):
    """Janela de configurações e diagnóstico do PEEK."""


    def __init__(self, parent: QWidget | None = None) -> None:
        # parent=None é crítico: impede que o Wayland herde geometria de
        # qualquer janela ativa (ex: a sidebar) como origin para esta janela.
        super().__init__(None)
        self._service_active: bool = True
        self._centered: bool = False  # Centraliza apenas na primeira exibição
        self._setup_window()

        # ── Early Severing (Wayland) ──────────────────────────────────────────
        # WA_NativeWindow força a criação imediata do QWindow nativo ANTES do
        # primeiro show(). Isso garante que setTransientParent(None) seja
        # chamado antes que o compositor associe qualquer parent implícito.
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        handle = self.windowHandle()
        if handle is not None:
            handle.setTransientParent(None)

        self._setup_ui()

    # ── Setup ─────────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.setWindowTitle("PEEK — Control Center")
        self.setMinimumWidth(460)
        self.setMaximumWidth(560)
        self.setStyleSheet(self._global_stylesheet())
        # Qt.Window garante janela de topo raiz independente no Wayland.
        # WindowCloseButtonHint mantém o botão ✕ nativo.
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint)
        # ShrinkToFit: deixa o layout controlar o tamanho da janela completamente.
        self.layout().setSizeConstraint(self.layout().SizeConstraint.SetFixedSize) if self.layout() else None

    def showEvent(self, event) -> None:  # noqa: N802
        """Centraliza na tela e corta o parentesco transiente nativo.

        Centralizar aqui (em vez do __init__) garante que o QScreen já esteja
        disponível, e que a geometria seja recalculada mesmo se o monitor mudar.
        O corte de transient parent evita que o KWin ancore esta janela relativa
        a qualquer outra (ex: a sidebar) no protocolo Wayland.
        """
        super().showEvent(event)

        # ── Corte do parentesco transiente nativo ───────────────────────
        handle = self.windowHandle()
        if handle is not None:
            handle.setTransientParent(None)

        # ── Centralizar uma única vez (na primeira exibição) ────────────
        if not self._centered:
            self._centered = True
            screen = self.screen()
            if screen is not None:
                rect = screen.availableGeometry()
                self.move(
                    rect.center().x() - self.width() // 2,
                    rect.center().y() - self.height() // 2,
                )

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(20)
        # SetFixedSize: proíbe espaço vazio — janela esmaga/expande automaticamente
        # no frame exato em que um widget filho recebe .hide() ou .show().
        root.setSizeConstraint(root.SizeConstraint.SetFixedSize)

        # ── Header ──────────────────────────────────────────────────────
        root.addLayout(self._build_header())

        # ── Card: Serviço ─────────────────────────────────────────────────────
        root.addWidget(self._build_service_card())

        # ── Card: Autostart ────────────────────────────────────────────────
        root.addWidget(self._build_autostart_card())

        # ── Card: Atalho ─────────────────────────────────────────────────────
        root.addWidget(self._build_shortcut_card())

        # ── Card: Diagnóstico / Logs ──────────────────────────────────────
        root.addWidget(self._build_logs_card(), stretch=1)

        # ── Footer ──────────────────────────────────────────────────────
        root.addLayout(self._build_footer())

    # ── Builders dos blocos de UI ─────────────────────────────────────────

    def _build_header(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(12)

        icon_lbl = QLabel("◈")
        icon_lbl.setObjectName("headerIcon")
        layout.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        title = QLabel("PEEK")
        title.setObjectName("headerTitle")
        title_col.addWidget(title)

        subtitle = QLabel("Centro de Controle")
        subtitle.setObjectName("headerSubtitle")
        title_col.addWidget(subtitle)

        layout.addLayout(title_col)
        layout.addStretch()

        self._status_badge = QLabel("●")
        self._status_badge.setObjectName("ledStatusActive")
        self._status_badge.setFixedSize(16, 16)
        self._status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_badge)

        return layout

    def _build_service_card(self) -> QFrame:
        card = self._make_card()
        layout = QVBoxLayout(card)
        layout.setSpacing(16)

        # Título da seção
        lbl = QLabel("SERVIÇO")
        lbl.setObjectName("sectionLabel")
        layout.addWidget(lbl)

        # Linha toggle
        row = QHBoxLayout()
        desc_col = QVBoxLayout()

        name = QLabel("PEEK Overlay")
        name.setObjectName("settingName")
        desc_col.addWidget(name)

        desc = QLabel("Ativa a detecção do canto da tela via KWin")
        desc.setObjectName("settingDesc")
        desc_col.addWidget(desc)

        row.addLayout(desc_col)
        row.addStretch()

        self._service_toggle = SlideSwitch()
        self._service_toggle.setChecked(True)
        self._service_toggle.toggled.connect(self._on_service_toggled)
        row.addWidget(self._service_toggle, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addLayout(row)

        return card

    def _build_shortcut_card(self) -> QFrame:
        card = self._make_card()
        layout = QVBoxLayout(card)
        layout.setSpacing(16)

        lbl = QLabel("ATALHO DE TECLADO")
        lbl.setObjectName("sectionLabel")
        layout.addWidget(lbl)

        row = QHBoxLayout()
        desc_col = QVBoxLayout()

        name = QLabel("Abrir / Fechar PEEK")
        name.setObjectName("settingName")
        desc_col.addWidget(name)

        desc = QLabel("Atalho global via KDE (Wayland)")
        desc.setObjectName("settingDesc")
        desc_col.addWidget(desc)

        row.addLayout(desc_col)
        row.addStretch()

        self._shortcut_btn = QPushButton("⌨ Configurar no KDE")
        self._shortcut_btn.setObjectName("btnShortcut")
        self._shortcut_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._shortcut_btn.clicked.connect(self._on_configure_shortcut)
        row.addWidget(self._shortcut_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addLayout(row)

        # Dica de como usar
        hint = QLabel("Clique para copiar o comando D-Bus e abrir as configurações do KDE.")
        hint.setObjectName("infoText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        return card

    def _build_autostart_card(self) -> QFrame:
        """Card de Autostart com padrão XDG (arquivo .desktop em ~/.config/autostart)."""
        card = self._make_card()
        layout = QVBoxLayout(card)
        layout.setSpacing(16)

        lbl = QLabel("INICIALIZAÇÃO")
        lbl.setObjectName("sectionLabel")
        layout.addWidget(lbl)

        row = QHBoxLayout()
        desc_col = QVBoxLayout()

        name = QLabel("Iniciar com o sistema")
        name.setObjectName("settingName")
        desc_col.addWidget(name)

        desc = QLabel("Inicia o PEEK automaticamente com o KDE (XDG Autostart)")
        desc.setObjectName("settingDesc")
        desc_col.addWidget(desc)

        row.addLayout(desc_col)
        row.addStretch()

        self._autostart_toggle = SlideSwitch()
        self._autostart_toggle.setChecked(self._is_autostart_enabled())
        self._autostart_toggle.toggled.connect(self._on_autostart_toggled)
        row.addWidget(self._autostart_toggle, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addLayout(row)
        return card

    def _build_logs_card(self) -> QFrame:
        card = self._make_card()
        layout = QVBoxLayout(card)
        layout.setSpacing(12)

        # Header da seção com LED de saúde, botão expandir e botão refresh
        header_row = QHBoxLayout()

        lbl = QLabel("DIAGNÓSTICO")
        lbl.setObjectName("sectionLabel")
        header_row.addWidget(lbl)

        # LED de saúde — colorido via CSS / objectName dinâmico
        self._health_led = QLabel("●")
        self._health_led.setObjectName("ledOk")
        header_row.addWidget(self._health_led)

        header_row.addStretch()

        # Botão expandir/recolher log
        self._toggle_log_btn = QPushButton("Mostrar Log")
        self._toggle_log_btn.setObjectName("btnRefresh")
        self._toggle_log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_log_btn.clicked.connect(self._toggle_log_view)
        header_row.addWidget(self._toggle_log_btn)

        self._refresh_btn = QPushButton("↻ Atualizar")
        self._refresh_btn.setObjectName("btnRefresh")
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._load_logs)
        header_row.addWidget(self._refresh_btn)

        layout.addLayout(header_row)

        # Área de texto para os logs (começa oculta)
        self._log_view = QTextEdit()
        self._log_view.setObjectName("logView")
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("JetBrains Mono, Monospace", 9))
        self._log_view.setFixedHeight(160)
        self._log_view.hide()  # Começa recolhido
        layout.addWidget(self._log_view)

        # Carrega os logs (e define a cor do LED)
        self._load_logs()

        return card

    def _build_footer(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(12)

        version = QLabel("PEEK v0.2 — KDE Plasma · Wayland")
        version.setObjectName("footerText")
        layout.addWidget(version)
        layout.addStretch()

        close_btn = QPushButton("Fechar")
        close_btn.setObjectName("btnClose")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        return layout

    # ── Autostart ─────────────────────────────────────────────────────────

    _AUTOSTART_DIR  = Path.home() / ".config" / "autostart"
    _AUTOSTART_FILE = _AUTOSTART_DIR / "peek-autostart.desktop"

    @staticmethod
    def _is_autostart_enabled() -> bool:
        """Verifica se o arquivo XDG Autostart existe."""
        return ControlCenter._AUTOSTART_FILE.exists()

    @Slot(bool)
    def _on_autostart_toggled(self, enabled: bool) -> None:
        """Cria ou remove o arquivo XDG Autostart."""
        if enabled:
            self._AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
            # Descobre o caminho absoluto do main.py dinamicamente.
            main_py = Path(os.path.abspath(__file__)).parent.parent / "main.py"
            self._AUTOSTART_FILE.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                "Name=Peek Daemon\n"
                f"Exec=python3 {main_py} --daemon\n"
                "NoDisplay=true\n"
                "X-KDE-autostart-phase=2\n",
                encoding="utf-8",
            )
        else:
            self._AUTOSTART_FILE.unlink(missing_ok=True)

    # ── Atalho / Shortcut ─────────────────────────────────────────────────

    _DESKTOP_APPS_DIR = Path.home() / ".local" / "share" / "applications"
    _DESKTOP_FILE     = _DESKTOP_APPS_DIR / "peek.desktop"

    @Slot()
    def _on_configure_shortcut(self) -> None:
        """Gera o arquivo .desktop com Desktop Action nativa e abre o KDE Shortcut Manager.

        O KDE reconhece nativamente a seção [Desktop Action ToggleSidebar] e
        permite que o usuário associe qualquer tecla a ela sem criar "Comandos
        Personalizados" — é a integração idiomática do FreeDesktop para atalhos.
        """
        self._write_desktop_file()

        # Atualiza o cache de .desktop do KDE para que a ação apareça imediatamente
        for kbuildsycoca in ("kbuildsycoca6", "kbuildsycoca5"):
            try:
                subprocess.Popen([kbuildsycoca, "--noincremental"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                break
            except FileNotFoundError:
                continue

        # Abre o painel de atalhos do KDE
        for cmd in (["kcmshell6", "keys"], ["systemsettings", "kcm_keys"]):
            try:
                subprocess.Popen(cmd)
                break
            except FileNotFoundError:
                continue

        # Instrução clara para o usuário (2s)
        self._shortcut_btn.setText("Busque por PEEK na aba Aplicativos!")
        self._shortcut_btn.setEnabled(False)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(3000, self._restore_shortcut_btn)

    def _write_desktop_file(self) -> None:
        """Cria/sobrescreve ~/.local/share/applications/peek.desktop com Desktop Actions."""
        self._DESKTOP_APPS_DIR.mkdir(parents=True, exist_ok=True)
        main_py = Path(os.path.abspath(__file__)).parent.parent / "main.py"
        self._DESKTOP_FILE.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=PEEK Control Center\n"
            f"Exec=python3 {main_py}\n"
            "Icon=preferences-system\n"
            "Categories=Utility;\n"
            "Actions=ToggleSidebar;\n"
            "\n"
            "[Desktop Action ToggleSidebar]\n"
            "Name=Abrir ou Fechar PEEK (Sidebar)\n"
            "Exec=qdbus org.peek.App /App Toggle\n",
            encoding="utf-8",
        )

    @Slot()
    def _restore_shortcut_btn(self) -> None:
        self._shortcut_btn.setText("⌨ Configurar no KDE")
        self._shortcut_btn.setEnabled(True)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _make_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card.setFrameShape(QFrame.Shape.NoFrame)
        return card

    @Slot()
    def _load_logs(self) -> None:
        """Lê as últimas 50 linhas do peek.log, exibe no log_view e atualiza o LED."""
        path = Path(LOG_PATH)
        if not path.exists():
            self._log_view.setPlainText(
                f"[PEEK] Arquivo de log não encontrado em:\n{path}\n\n"
                "Execute o PEEK ao menos uma vez para gerar o log."
            )
            self._set_led("ok")
            return

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            last_50 = "\n".join(lines[-50:]) if len(lines) > 50 else "\n".join(lines)
            self._log_view.setPlainText(last_50)
            # Rola para o final
            self._log_view.verticalScrollBar().setValue(
                self._log_view.verticalScrollBar().maximum()
            )
            # Atualiza a cor do LED baseada no conteúdo
            combined = last_50.lower()
            if "traceback" in combined or "error" in combined or "exception" in combined:
                self._set_led("error")
            elif "warning" in combined or "warn" in combined or "aviso" in combined:
                self._set_led("warn")
            else:
                self._set_led("ok")
        except OSError as e:
            self._log_view.setPlainText(f"[Erro ao ler log]: {e}")
            self._set_led("error")

    def _set_led(self, state: str) -> None:
        """Atualiza o LED de saúde (ok / warn / error)."""
        names = {"ok": "ledOk", "warn": "ledWarn", "error": "ledError"}
        self._health_led.setObjectName(names.get(state, "ledOk"))
        self._health_led.style().unpolish(self._health_led)
        self._health_led.style().polish(self._health_led)

    @Slot()
    def _toggle_log_view(self) -> None:
        """Expande/recolhe o painel de log e ajusta o tamanho da janela."""
        visible = self._log_view.isVisible()
        self._log_view.setVisible(not visible)
        self._toggle_log_btn.setText("Ocultar Log" if not visible else "Mostrar Log")
        # SetFixedSize no layout garante que a janela encolhe/expande automaticamente.

    @Slot(bool)
    def _on_service_toggled(self, active: bool) -> None:
        """Atualiza o LED de status e envia ordem D-Bus para o Daemon."""
        self._service_active = active
        if active:
            self._status_badge.setObjectName("ledStatusActive")
        else:
            self._status_badge.setObjectName("ledStatusPaused")
        # Força repintura do QSS (mudou objectName)
        self._status_badge.style().unpolish(self._status_badge)
        self._status_badge.style().polish(self._status_badge)
        
        # Envia ordem ao processo Daemon isolado (que roda o Controller)
        val = "true" if active else "false"
        subprocess.run(
            ["qdbus", "org.peek.App", "/App", "org.peek.App.SetServiceActive", val],
            capture_output=True,
            check=False
        )

    # ── Stylesheet ────────────────────────────────────────────────────────

    @staticmethod
    def _global_stylesheet() -> str:
        return f"""
            ControlCenter {{
                background-color: {_BG_BASE};
            }}

            /* ── Header ── */
            #headerIcon {{
                color: {_ACCENT_MAUVE};
                font-size: 28px;
            }}
            #headerTitle {{
                color: {_TEXT_MAIN};
                font-size: 22px;
                font-weight: bold;
                font-family: 'Inter', 'Segoe UI', sans-serif;
            }}
            #headerSubtitle {{
                color: {_TEXT_SUB};
                font-size: 12px;
            }}

            /* ── LED de Status (cabeçalho) ── */
            #ledStatusActive {{
                color: {_ACCENT_GREEN};
                font-size: 16px;
                border-radius: 8px;
            }}
            #ledStatusPaused {{
                color: {_ACCENT_RED};
                font-size: 16px;
                border-radius: 8px;
            }}

            /* ── Badges ── */
            #badgeSoon {{
                color: {_TEXT_MUTED};
                font-size: 11px;
                padding: 3px 8px;
                border-radius: {_RADIUS_SM};
                background-color: {_BG_OVERLAY};
            }}

            /* ── Cards ── */
            #card {{
                background-color: {_BG_SURFACE};
                border-radius: {_RADIUS};
                padding: 16px;
            }}

            /* ── Seções ── */
            #sectionLabel {{
                color: {_ACCENT_MAUVE};
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1.5px;
            }}
            #settingName {{
                color: {_TEXT_MAIN};
                font-size: 14px;
                font-weight: bold;
            }}
            #settingDesc {{
                color: {_TEXT_SUB};
                font-size: 12px;
            }}
            #infoText {{
                color: {_TEXT_MUTED};
                font-size: 11px;
                font-family: 'JetBrains Mono', 'Monospace';
            }}

            /* ── Toggle Slide Switch ── */
            #toggleSwitch {{
                width: 44px;
                height: 24px;
                padding: 0px;
            }}
            #toggleSwitch::indicator {{
                width: 44px;
                height: 24px;
                border-radius: 12px;
                border: none;
                background-color: {_BG_OVERLAY};
                image: none;
            }}
            #toggleSwitch::indicator:unchecked {{
                background-color: {_BG_OVERLAY};
            }}
            #toggleSwitch::indicator:checked {{
                background-color: {_ACCENT_MAUVE};
            }}

            /* ── LED de Saúde ── */
            #ledOk {{
                color: {_ACCENT_GREEN};
                font-size: 10px;
                padding-left: 6px;
            }}
            #ledWarn {{
                color: #f9e2af;
                font-size: 10px;
                padding-left: 6px;
            }}
            #ledError {{
                color: {_ACCENT_RED};
                font-size: 10px;
                padding-left: 6px;
            }}

            /* ── Separador ── */
            #separator {{
                color: {_BG_OVERLAY};
                margin: 0px;
            }}

            /* ── Log view ── */
            #logView {{
                background-color: {_BG_MANTLE};
                color: {_ACCENT_BLUE};
                border: 1px solid {_BG_OVERLAY};
                border-radius: {_RADIUS_SM};
                padding: 8px;
                font-size: 10px;
            }}

            /* ── Botões ── */
            #btnRefresh {{
                background-color: {_BG_OVERLAY};
                color: {_TEXT_MAIN};
                border: none;
                border-radius: {_RADIUS_SM};
                padding: 5px 12px;
                font-size: 12px;
            }}
            #btnRefresh:hover {{
                background-color: #45475a;
            }}
            #btnRefresh:pressed {{
                background-color: #2a2a3c;
                padding-top: 6px;
                padding-bottom: 4px;
            }}
            #btnClose {{
                background-color: {_BG_OVERLAY};
                color: {_TEXT_MAIN};
                border: none;
                border-radius: {_RADIUS_SM};
                padding: 7px 20px;
                font-size: 13px;
            }}
            #btnClose:hover {{
                background-color: #45475a;
            }}
            #btnClose:pressed {{
                background-color: #2a2a3c;
                padding-top: 8px;
                padding-bottom: 6px;
            }}
            #btnShortcut {{
                background-color: rgba(203, 166, 247, 0.15);
                color: {_ACCENT_MAUVE};
                border: 1px solid rgba(203, 166, 247, 0.35);
                border-radius: {_RADIUS_SM};
                padding: 6px 14px;
                font-size: 12px;
                font-weight: bold;
            }}
            #btnShortcut:hover {{
                background-color: rgba(203, 166, 247, 0.28);
            }}
            #btnShortcut:pressed {{
                background-color: rgba(203, 166, 247, 0.12);
                padding-top: 7px;
                padding-bottom: 5px;
            }}
            #btnShortcut:disabled {{
                background-color: rgba(166, 227, 161, 0.15);
                color: {_ACCENT_GREEN};
                border-color: rgba(166, 227, 161, 0.35);
            }}

            /* ── Footer ── */
            #footerText {{
                color: {_TEXT_MUTED};
                font-size: 11px;
            }}
        """
