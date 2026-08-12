"""PEEK — Quick system sidebar for KDE Plasma / Wayland."""

import signal
import sys

# O logger.py será acionado de forma inteligente pelo roteamento Multi-Processo
from core.logger import setup_logging

import subprocess

from PySide6.QtDBus import QDBusConnection
from PySide6.QtWidgets import QApplication

from core.controller import Controller
from core.dbus_service import DBUS_SERVICE
from ui.settings_window import ControlCenter


def _is_service_registered(service_name: str) -> bool:
    """Verifica se um serviço específico já está registrado no D-Bus."""
    bus = QDBusConnection.sessionBus()
    iface = bus.interface()
    if iface is None:
        return False
    reply = iface.registeredServiceNames()
    names = reply.value() or []
    return service_name in names


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # ── ROTEAMENTO MULTI-PROCESSO (Wayland Isolation) ─────────────────
    # No Wayland, KWin agrupa as janelas por Process ID / QApplication.
    # Para a Sidebar não ser amarrada à GUI, precisamos de dois processos.

    if _is_service_registered(DBUS_SERVICE):
        # ── PROCESSO 2: GUI CLIENT ──
        log_path = setup_logging(is_daemon=False)
        app.setApplicationName("peek-gui")
        app.setDesktopFileName("peek-gui")
        # O Daemon já está rodando. Nós viramos a GUI.
        if _is_service_registered("org.peek.GUI"):
            # A GUI já está aberta. Não queremos duas.
            print("[PEEK:GUI] Instância já aberta. Saindo.")
            return 0
        
        print("[PEEK:GUI] Iniciando painel de controle (Client)...")
        bus = QDBusConnection.sessionBus()
        bus.registerService("org.peek.GUI")
        
        # A janela vai se centralizar, desconectar o parentesco transiente
        # nativamente e exibir. Não precisa do Controller.
        gui = ControlCenter()
        gui.show()
        
        # A GUI pode fechar, e isso matará este processo (mas não o daemon).
        app.setQuitOnLastWindowClosed(True)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        return app.exec()

    # ── PROCESSO 1: DAEMON (Sidebar) ──
    log_path = setup_logging(is_daemon=True)
    app.setApplicationName("peek-daemon")
    app.setDesktopFileName("peek-daemon")
    print(f"[PEEK:Daemon] Sessão iniciada. Log: {log_path}")

    controller = Controller()
    controller.start()

    # Primeira execução: nós abrimos a GUI para o usuário ver que funcionou.
    # Mas como queremos isolamento (Multi-Processo), abrimos DE FORA deste PID.
    print("[PEEK:Daemon] Lançando subprocesso da GUI...")
    subprocess.Popen([sys.executable, sys.argv[0]])

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
