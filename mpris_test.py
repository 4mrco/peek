import sys
from PySide6.QtCore import QCoreApplication
from PySide6.QtDBus import QDBusConnection, QDBusInterface

app = QCoreApplication(sys.argv)
bus = QDBusConnection.sessionBus()
iface = bus.interface()
reply = iface.registeredServiceNames()
names = [n for n in reply.value() if n.startswith("org.mpris.MediaPlayer2.")]

for name in names:
    player = QDBusInterface(name, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", bus)
    root = QDBusInterface(name, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2", bus)
    status = player.property("PlaybackStatus")
    identity = root.property("Identity")
    print(f"Service: {name}, Status: {status}, Identity: {identity}")
