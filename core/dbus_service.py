from PySide6.QtCore import QObject
from PySide6.QtDBus import QDBusConnection

DBUS_SERVICE: str = "org.peek.App"
DBUS_PATH: str = "/App"
DBUS_INTERFACE: str = "org.peek.App"

def register_dbus_service(controller: QObject) -> bool:
    """Registra o controller no D-Bus de sessão.

    Retorna True se tudo ocorreu bem.
    """
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        print("[PEEK] Erro: não foi possível conectar ao D-Bus de sessão.")
        return False

    if not bus.registerService(DBUS_SERVICE):
        print(f"[PEEK] Erro: falha ao registrar serviço '{DBUS_SERVICE}'.")
        print("       Verifique se outra instância do PEEK está rodando.")
        return False

    # Registra o controller diretamente com ExportAllSlots,
    # já que ele agora possui ClassInfo e os métodos D-Bus nativos.
    options = QDBusConnection.RegisterOption.ExportAllSlots
    ok = bus.registerObject(DBUS_PATH, controller, options)
    if not ok:
        print(f"[PEEK] Erro: falha ao registrar objeto em '{DBUS_PATH}'.")
        return False

    print(f"[PEEK] D-Bus registrado: {DBUS_SERVICE} → {DBUS_PATH}")
    return True
