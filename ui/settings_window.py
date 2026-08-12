"""Centro de Controle do PEEK — janela de configurações e diagnóstico.

Abre como janela independente (não bloqueia a sidebar).
Expõe:
  - Toggle de serviço (pausa/retoma a escuta do KWin hotcorner)
  - Placeholder de atalho de teclado (para implementação futura)
  - Painel de logs: exibe as últimas 50 linhas de peek.log

Design: Catppuccin Mocha — consistente com a sidebar.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
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
        self.setMinimumSize(460, 540)
        self.setMaximumWidth(560)
        self.setStyleSheet(self._global_stylesheet())
        # Qt.Window garante janela de topo raiz independente no Wayland.
        # WindowCloseButtonHint mantém o botão ✕ nativo.
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint)

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

        # ── Header ──────────────────────────────────────────────────────
        root.addLayout(self._build_header())

        # ── Card: Serviço ────────────────────────────────────────────────
        root.addWidget(self._build_service_card())

        # ── Card: Atalho (placeholder) ───────────────────────────────────
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

        self._status_badge = QLabel("● ATIVO")
        self._status_badge.setObjectName("badgeActive")
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

        self._service_toggle = QCheckBox()
        self._service_toggle.setObjectName("toggleSwitch")
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

        desc = QLabel("Atalho global para mostrar a sidebar")
        desc.setObjectName("settingDesc")
        desc_col.addWidget(desc)

        row.addLayout(desc_col)
        row.addStretch()

        badge = QLabel("Gravar Atalho (Em breve)")
        badge.setObjectName("badgeSoon")
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addLayout(row)

        return card

    def _build_logs_card(self) -> QFrame:
        card = self._make_card()
        layout = QVBoxLayout(card)
        layout.setSpacing(12)

        # Header da seção com botão de refresh
        header_row = QHBoxLayout()

        lbl = QLabel("DIAGNÓSTICO")
        lbl.setObjectName("sectionLabel")
        header_row.addWidget(lbl)
        header_row.addStretch()

        self._refresh_btn = QPushButton("↻ Atualizar")
        self._refresh_btn.setObjectName("btnRefresh")
        self._refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._load_logs)
        header_row.addWidget(self._refresh_btn)

        layout.addLayout(header_row)

        # Área de texto para os logs
        self._log_view = QTextEdit()
        self._log_view.setObjectName("logView")
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("JetBrains Mono, Monospace", 9))
        self._log_view.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self._log_view)

        # Carrega os logs na construção
        self._load_logs()

        return card

    def _build_footer(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(12)

        version = QLabel("PEEK v0.1 — KDE Plasma · Wayland")
        version.setObjectName("footerText")
        layout.addWidget(version)
        layout.addStretch()

        close_btn = QPushButton("Fechar")
        close_btn.setObjectName("btnClose")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        return layout

    # ── Helpers ──────────────────────────────────────────────────────────

    def _make_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card.setFrameShape(QFrame.Shape.NoFrame)
        return card

    @Slot()
    def _load_logs(self) -> None:
        """Lê as últimas 50 linhas do peek.log e exibe no log_view."""
        path = Path(LOG_PATH)
        if not path.exists():
            self._log_view.setPlainText(
                f"[PEEK] Arquivo de log não encontrado em:\n{path}\n\n"
                "Execute o PEEK ao menos uma vez para gerar o log."
            )
            return

        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            last_50 = "\n".join(lines[-50:]) if len(lines) > 50 else "\n".join(lines)
            self._log_view.setPlainText(last_50)
            # Rola para o final
            self._log_view.verticalScrollBar().setValue(
                self._log_view.verticalScrollBar().maximum()
            )
        except OSError as e:
            self._log_view.setPlainText(f"[Erro ao ler log]: {e}")

    @Slot(bool)
    def _on_service_toggled(self, active: bool) -> None:
        """Atualiza o badge de status e envia ordem D-Bus para o Daemon."""
        self._service_active = active
        if active:
            self._status_badge.setText("● ATIVO")
            self._status_badge.setObjectName("badgeActive")
        else:
            self._status_badge.setText("● PAUSADO")
            self._status_badge.setObjectName("badgePaused")
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

            /* ── Badges ── */
            #badgeActive {{
                color: {_ACCENT_GREEN};
                font-size: 12px;
                font-weight: bold;
                padding: 4px 10px;
                border-radius: {_RADIUS_SM};
                background-color: rgba(166, 227, 161, 0.15);
            }}
            #badgePaused {{
                color: {_ACCENT_RED};
                font-size: 12px;
                font-weight: bold;
                padding: 4px 10px;
                border-radius: {_RADIUS_SM};
                background-color: rgba(243, 139, 168, 0.15);
            }}
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

            /* ── Toggle ── */
            #toggleSwitch {{
                width: 20px;
                height: 20px;
            }}
            #toggleSwitch::indicator {{
                width: 20px;
                height: 20px;
                border-radius: 6px;
                border: 2px solid {_BG_OVERLAY};
                background-color: {_BG_OVERLAY};
            }}
            #toggleSwitch::indicator:checked {{
                background-color: {_ACCENT_MAUVE};
                border-color: {_ACCENT_MAUVE};
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

            /* ── Footer ── */
            #footerText {{
                color: {_TEXT_MUTED};
                font-size: 11px;
            }}
        """
