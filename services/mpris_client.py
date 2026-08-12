"""Cliente MPRIS — monitora players de mídia via D-Bus.

Usa QtDBus para comunicar com players que implementam a interface
org.mpris.MediaPlayer2.Player (Spotify, VLC, Firefox, etc.).

Abordagem event-driven híbrida:
- Escuta o signal PropertiesChanged do D-Bus como trigger de atualização.
  O payload do signal é IGNORADO (PySide6 não desempacota a{sv}).
  O signal serve apenas como notificação para re-ler o estado.
- PlaybackStatus: lido via QDBusInterface.property() (retorna str nativo).
- Metadata: lido via `busctl --json=short` (subprocess), workaround para
  o bug do PySide6 com QDBusArgument a{sv}.
- Controles (PlayPause, Next, Previous): QDBusInterface.call() + atualização
  otimista da UI (sem esperar o signal de volta do player).
- QTimer de fallback (10s) para redescoberta de player caso ele desconecte.
"""

from __future__ import annotations

import json
import subprocess

from PySide6.QtCore import QObject, QTimer, Signal, Slot, SLOT
from PySide6.QtDBus import QDBusConnection, QDBusInterface


# ── Constantes D-Bus MPRIS ───────────────────────────────────────────

MPRIS_PREFIX: str = "org.mpris.MediaPlayer2."
MPRIS_PLAYER_IFACE: str = "org.mpris.MediaPlayer2.Player"
MPRIS_PATH: str = "/org/mpris/MediaPlayer2"
DBUS_PROPERTIES_IFACE: str = "org.freedesktop.DBus.Properties"

# Player preferido (tentamos primeiro)
PREFERRED_PLAYER: str = f"{MPRIS_PREFIX}spotify"

# Fallback: re-descobre player a cada 10s (só quando sem player ativo)
REDISCOVER_INTERVAL_MS: int = 10_000


class MprisClient(QObject):
    """Monitora e controla o player de mídia ativo via MPRIS/D-Bus.

    Sinais:
        track_changed(title, artist) — emitido quando a faixa muda.
        playback_state_changed(is_playing) — emitido quando play/pause muda.
        art_url_changed(url) — emitido quando a URL da capa do álbum muda.

    Slots:
        play_pause() — alterna reprodução.
        next_track() — próxima faixa.
        previous_track() — faixa anterior.
    """

    track_changed = Signal(str, str)
    playback_state_changed = Signal(bool)
    art_url_changed = Signal(str)
    player_name_changed = Signal(str)
    position_changed = Signal(int)
    duration_changed = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        self._bus = QDBusConnection.sessionBus()
        self._player_service: str = ""
        self._player_iface: QDBusInterface | None = None
        self._signal_connected: bool = False

        # Cache para só emitir sinais quando houver mudança real
        self._last_title: str = ""
        self._last_artist: str = ""
        self._last_art_url: str = ""
        self._last_is_playing: bool = False
        self._last_track_id: str = ""  # ObjectPath MPRIS da faixa atual

        # Timer de fallback para redescoberta (não para polling de estado)
        self._rediscover_timer = QTimer(self)
        self._rediscover_timer.setInterval(REDISCOVER_INTERVAL_MS)
        self._rediscover_timer.timeout.connect(self._try_rediscover)
        self._rediscover_timer.start()
        
        self._position_timer = QTimer(self)
        self._position_timer.setInterval(1000)
        self._position_timer.timeout.connect(self._poll_position)

        # Procura um player logo na inicialização
        self._discover_player()
        if not self._player_service:
            self._rediscover_timer.start()

    # ── Descoberta de player ─────────────────────────────────────────

    def _discover_player(self) -> None:
        """Encontra um player MPRIS ativo no D-Bus de sessão.

        Prioriza o Spotify. Se não encontrar, usa o primeiro disponível.
        Se nenhum for encontrado, limpa o estado e a UI mostrará
        'Nenhum player detectado'.
        """
        names = self._list_bus_names()
        mpris_names = [n for n in names if n.startswith(MPRIS_PREFIX)]

        if not mpris_names:
            self._clear_player()
            return

        # Prioriza Spotify
        if PREFERRED_PLAYER in mpris_names:
            service = PREFERRED_PLAYER
        else:
            service = mpris_names[0]

        # Só reconecta se o player mudou
        if service != self._player_service:
            self._connect_to_player(service)

    def _list_bus_names(self) -> list[str]:
        """Lista todos os nomes registrados no D-Bus de sessão."""
        iface = self._bus.interface()
        if iface is None:
            return []

        reply = iface.registeredServiceNames()
        names = reply.value()
        if isinstance(names, list):
            return [str(n) for n in names]
        return []

    def _connect_to_player(self, service: str) -> None:
        """Conecta ao player MPRIS: cria interface + escuta PropertiesChanged."""
        # Desconecta signal anterior, se houver
        self._disconnect_properties_signal()

        self._player_service = service
        self._player_iface = QDBusInterface(
            service,
            MPRIS_PATH,
            MPRIS_PLAYER_IFACE,
            self._bus,
        )
        
        # Emite nome do player (ex: spotify)
        name = service.split(".")[-1] if service else ""
        self.player_name_changed.emit(name)

        self._refresh_state(force_emit=True)

        # Conecta ao signal PropertiesChanged do player.
        # O payload (interface, changed_props, invalidated) é IGNORADO
        # porque PySide6 não desempacota a{sv}. Usamos o signal apenas
        # como trigger para re-ler via property() e busctl.
        self._signal_connected = self._bus.connect(
            service,
            MPRIS_PATH,
            DBUS_PROPERTIES_IFACE,
            "PropertiesChanged",
            self,
            SLOT("_on_properties_changed()"),
        )

        player_name = service.removeprefix(MPRIS_PREFIX).capitalize()
        self.player_name_changed.emit(player_name)

        signal_status = "signal OK" if self._signal_connected else "sem signal, fallback timer"
        print(f"[PEEK:MPRIS] Conectado ao player: {player_name} ({signal_status})")

    def force_sync_ui(self) -> None:
        """Força a emissão do estado atual do player, usado logo após a UI conectar os slots."""
        if self._player_service:
            name = self._player_service.removeprefix(MPRIS_PREFIX).capitalize()
            self.player_name_changed.emit(name)
        self._refresh_state(force_emit=True)

        # Para o timer de redescoberta — temos um player ativo
        self._rediscover_timer.stop()

        # Leitura inicial do estado
        self._refresh_state()

    def _disconnect_properties_signal(self) -> None:
        """Desconecta o signal PropertiesChanged do player anterior."""
        if self._signal_connected and self._player_service:
            self._bus.disconnect(
                self._player_service,
                MPRIS_PATH,
                DBUS_PROPERTIES_IFACE,
                "PropertiesChanged",
                self._on_properties_changed,
            )
            self._signal_connected = False

    def _clear_player(self) -> None:
        """Limpa referências ao player (desconectou ou não existe)."""
        self._disconnect_properties_signal()

        if self._player_service:
            print("[PEEK:MPRIS] Player desconectado.")
            self.player_name_changed.emit("")
        self._player_service = ""
        self._player_iface = None

        # Emite estado vazio para a UI limpar
        if self._last_title or self._last_artist:
            self._last_title = ""
            self._last_artist = ""
            self.track_changed.emit("", "")

        if self._last_art_url:
            self._last_art_url = ""
            self.art_url_changed.emit("")

        if self._last_is_playing:
            self._last_is_playing = False
            self.playback_state_changed.emit(False)

        self._last_track_id = ""

        # Inicia redescoberta periódica
        self._rediscover_timer.start()

    def _try_rediscover(self) -> None:
        """Callback do timer de fallback: tenta encontrar um novo player."""
        self._discover_player()

    # ── Atualização de estado (event-driven) ─────────────────────────

    @Slot()
    def _on_properties_changed(self) -> None:
        """Callback do signal PropertiesChanged do D-Bus.
        Ignora o payload (PySide6 não desempacota a{sv}).
        Apenas re-lê o estado via os métodos que funcionam.
        """
        self._refresh_state()

    def _refresh_state(self, force_emit: bool = False) -> None:
        """Lê PlaybackStatus e Metadata do player ativo.

        Chamado quando PropertiesChanged dispara ou após conexão inicial.
        """
        if not self._player_iface or not self._player_iface.isValid():
            self._clear_player()
            return

        # ── PlaybackStatus (via QDBusInterface.property) ─────────────
        try:
            status = self._player_iface.property("PlaybackStatus")
            if status is None:
                self._clear_player()
                return

            is_playing = str(status) == "Playing"
            if force_emit or is_playing != self._last_is_playing:
                self._last_is_playing = is_playing
                self.playback_state_changed.emit(is_playing)
                
                if is_playing:
                    self._position_timer.start()
                    self._poll_position()  # Atualiza logo no start
                else:
                    self._position_timer.stop()
        except Exception as e:
            print(f"[PEEK:MPRIS] Erro ao ler PlaybackStatus: {e}")

        # ── Metadata (via busctl --json) ─────────────────────────────
        try:
            metadata = self._read_metadata_busctl()
            if metadata is None:
                return

            title = str(metadata.get("xesam:title", ""))
            artists_raw = metadata.get("xesam:artist", [])
            if isinstance(artists_raw, list) and artists_raw:
                artist = str(artists_raw[0])
            elif isinstance(artists_raw, str):
                artist = artists_raw
            else:
                artist = ""

            if force_emit or title != self._last_title or artist != self._last_artist:
                self._last_title = title
                self._last_artist = artist
                self.track_changed.emit(title, artist)

            # ── Art URL (mpris:artUrl) ───────────────────────────────
            art_url = str(metadata.get("mpris:artUrl", "") or "")
            if force_emit or art_url != self._last_art_url:
                self._last_art_url = art_url
                self.art_url_changed.emit(art_url)

            # ── Track ID (ObjectPath MPRIS) ───────────────────────────
            # busctl retorna string pura do ObjectPath (ex: /com/spotify/track/...)
            self._last_track_id = str(metadata.get("mpris:trackid", ""))

            length_us = metadata.get("mpris:length", 0)
            if isinstance(length_us, (int, float)):
                self.duration_changed.emit(int(length_us))

        except Exception as e:
            print(f"[PEEK:MPRIS] Erro ao ler Metadata: {e}")
            
    @Slot()
    def _poll_position(self) -> None:
        """Lê a propriedade Position (microssegundos) do MPRIS a cada segundo."""
        if not self._player_iface or not self._player_iface.isValid():
            self._position_timer.stop()
            return
            
        try:
            pos = self._player_iface.property("Position")
            if pos is not None:
                self.position_changed.emit(int(pos))
        except Exception as e:
            pass # Pode dar erro se o player estiver fechando

    def _read_metadata_busctl(self) -> dict[str, object] | None:
        """Lê Metadata via `busctl --json=short get-property`.

        Retorna dict com valores já desembrulhados, ex:
          {"xesam:title": "Song", "xesam:artist": ["Artist"], ...}

        busctl está disponível em todo sistema com systemd (inclui Nobara/Fedora).
        Overhead: ~5ms por chamada local.
        """
        if not self._player_service:
            return None

        try:
            result = subprocess.run(
                [
                    "busctl", "--user", "--json=short", "get-property",
                    self._player_service,
                    MPRIS_PATH,
                    MPRIS_PLAYER_IFACE,
                    "Metadata",
                ],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

        if result.returncode != 0:
            return None

        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

        # busctl --json=short retorna: {"type":"a{sv}","data":{...}}
        # Cada valor dentro de "data" é {"type":"s","data":"value"}
        raw_data = parsed.get("data", {})
        metadata: dict[str, object] = {}
        for key, entry in raw_data.items():
            if isinstance(entry, dict):
                metadata[key] = entry.get("data")
            else:
                metadata[key] = entry
        return metadata

    # ── Controles (Slots públicos) ───────────────────────────────────

    @Slot()
    def play_pause(self) -> None:
        """Alterna entre play e pause no player ativo.

        Atualização otimista: inverte o estado na UI imediatamente,
        sem esperar o signal PropertiesChanged do player.
        """
        self._call_player_method("PlayPause")
        # Otimismo: inverte o estado local e emite
        self._last_is_playing = not self._last_is_playing
        self.playback_state_changed.emit(self._last_is_playing)

    @Slot()
    def next_track(self) -> None:
        """Avança para a próxima faixa."""
        self._call_player_method("Next")

    @Slot()
    def previous_track(self) -> None:
        """Volta para a faixa anterior."""
        self._call_player_method("Previous")

    @Slot(int)
    def set_position(self, pos_s: int) -> None:
        """Envia SetPosition via busctl com assinatura ox (ObjectPath + Int64).

        Motivo do subprocess:
        O PySide6 serializa int Python como D-Bus 'i' (Int32). A interface MPRIS
        exige 'x' (Int64). A única forma confiável de garantir o tipo correto no
        PySide6 é delegar ao busctl, que aceita a assinatura explícita 'ox'.
        Overhead: ~5ms por chamada local, idêntico ao que já usamos no busctl de Metadata.
        """
        if not self._player_service or not self._last_track_id:
            return

        pos_us = pos_s * 1_000_000  # Converter segundos → microssegundos (Int64)
        try:
            subprocess.run(
                [
                    "busctl", "--user", "call",
                    self._player_service,
                    MPRIS_PATH,
                    MPRIS_PLAYER_IFACE,
                    "SetPosition", "ox",
                    self._last_track_id,
                    str(pos_us),
                ],
                capture_output=True,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            print(f"[PEEK:MPRIS] Erro ao enviar SetPosition: {e}")

    def _call_player_method(self, method: str) -> None:
        """Chama um método void na interface do player MPRIS."""
        if not self._player_iface or not self._player_iface.isValid():
            return
        self._player_iface.call(method)
