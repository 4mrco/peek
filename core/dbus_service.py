"""Adaptador D-Bus para expor controles da sidebar na sessão do usuário.

Usa QDBusAbstractAdaptor com ClassInfo para definir a interface como
"org.peek.App". Isso permite que o KWin Script (ou qualquer outro
caller externo) controle a sidebar via:

    qdbus6 org.peek.App /App org.peek.App.Toggle
"""

from PySide6.QtCore import ClassInfo, QObject, Slot
from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusConnection

DBUS_SERVICE: str = "org.peek.App"
DBUS_PATH: str = "/App"
DBUS_INTERFACE: str = "org.peek.App"


@ClassInfo({"D-Bus Interface": DBUS_INTERFACE})
class SidebarAdaptor(QDBusAbstractAdaptor):
    """Expõe Toggle / SlideIn / SlideOut sobre o D-Bus de sessão."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)

    @Slot()
    def Toggle(self) -> None:
        """Alterna visibilidade da sidebar."""
        self.parent().toggle()

    @Slot()
    def SlideIn(self) -> None:
        """Mostra a sidebar."""
        self.parent().slide_in()

    @Slot()
    def SlideOut(self) -> None:
        """Esconde a sidebar."""
        self.parent().slide_out()


def register_dbus_service(controller: QObject) -> bool:
    """Registra o controller no D-Bus de sessão.

    Retorna True se tudo ocorreu bem.
    O adaptador é parented ao controller (ciclo de vida Qt), então não
    precisa de referência Python extra.
    """
    # O adaptador se liga ao controller como child (Qt parent ownership)
    SidebarAdaptor(controller)

    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        print("[PEEK] Erro: não foi possível conectar ao D-Bus de sessão.")
        return False

    if not bus.registerService(DBUS_SERVICE):
        print(f"[PEEK] Erro: falha ao registrar serviço '{DBUS_SERVICE}'.")
        print("       Verifique se outra instância do PEEK está rodando.")
        return False

    if not bus.registerObject(DBUS_PATH, controller):
        print(f"[PEEK] Erro: falha ao registrar objeto em '{DBUS_PATH}'.")
        return False

    print(f"[PEEK] D-Bus registrado: {DBUS_SERVICE} → {DBUS_PATH}")
    return True
