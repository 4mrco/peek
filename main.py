"""PEEK — Quick system sidebar for KDE Plasma / Wayland."""

import os
import signal
import sys

# Força o app_id no protocolo Wayland ANTES de instanciar QApplication.
# Sem isso, o Qt6 pode não propagar setDesktopFileName() para o compositor,
# e a KWin Rule (wmclass=peek, skiptaskbar=true) não faz match.
os.environ["QT_WAYLAND_APP_ID"] = "peek"

from PySide6.QtWidgets import QApplication

from core.controller import Controller


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("peek")
    # Redundância intencional: setDesktopFileName reforça o app_id no Wayland
    # e define o identificador para X11/XWayland (fallback).
    app.setDesktopFileName("peek")
    # Sem janela principal: o app vive em background.
    app.setQuitOnLastWindowClosed(False)

    controller = Controller()
    controller.start()

    # Permite matar com Ctrl+C no terminal
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
