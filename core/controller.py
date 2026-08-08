"""Controller — O cérebro do PEEK.

Registra-se no D-Bus (org.peek.App) e expõe toggle/slide_in/slide_out.
O KWin Script dispara esses métodos quando o mouse encosta na borda.
Gerencia o timer de auto-hide quando o mouse sai da sidebar."""

from PySide6.QtCore import QObject, QTimer, Slot

from core.dbus_service import register_dbus_service
from services.art_downloader import ArtDownloader
from services.audio_client import AudioClient
from services.mpris_client import MprisClient
from ui.sidebar_window import SidebarWindow


class Controller(QObject):
    """Conecta o D-Bus (input externo) com a sidebar (UI).

    Fluxo:
    1. KWin Script detecta borda → chama Toggle via D-Bus.
    2. Controller mostra/esconde a sidebar.
    3. Mouse sai da sidebar → timer de 400ms → slide_out automático.
    4. Mouse re-entra na sidebar antes do timeout → timer cancelado.
    """

    HIDE_DELAY_MS: int = 400

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        self._sidebar = SidebarWindow()
        self._mpris = MprisClient(self)
        self._art_downloader = ArtDownloader(self)
        self._audio = AudioClient(self)

        # Timer com atraso para não esconder imediatamente ao sair com o mouse
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(self.HIDE_DELAY_MS)

        self._connect_signals()

    def _connect_signals(self) -> None:
        # Sidebar hide/show
        self._sidebar.mouse_entered.connect(self._cancel_hide)
        self._sidebar.mouse_left.connect(self._schedule_hide)
        self._hide_timer.timeout.connect(self._do_slide_out)

        # MPRIS serviço → UI
        media = self._sidebar.media_player
        self._mpris.track_changed.connect(media.update_track)
        self._mpris.playback_state_changed.connect(media.update_playback_state)
        self._mpris.player_name_changed.connect(media.update_player_name)
        self._mpris.position_changed.connect(media.update_position)
        self._mpris.duration_changed.connect(media.update_duration)

        # MPRIS → ArtDownloader → UI (pipeline de capa do álbum)
        self._mpris.art_url_changed.connect(self._art_downloader.fetch)
        self._art_downloader.art_ready.connect(media.update_art)
        self._art_downloader.art_cleared.connect(media.clear_art)

        # UI → MPRIS serviço
        media.play_pause_clicked.connect(self._mpris.play_pause)
        media.next_clicked.connect(self._mpris.next_track)
        media.previous_clicked.connect(self._mpris.previous_track)

        # Audio serviço → UI (volume master)
        vol_slider = self._sidebar.volume_slider
        # Audio (Backend) -> UI
        self._audio.master_volume_changed.connect(vol_slider.set_volume)
        self._audio.app_volumes_changed.connect(self._sidebar.update_app_sliders)

        # UI -> Audio (Backend)
        vol_slider.volume_changed.connect(self._audio.set_master_volume)
        self._sidebar.app_volume_changed.connect(self._audio.set_app_volume)
        
        # Força o MPRIS a popular a UI após todas as conexões terem sido feitas
        self._mpris.force_sync_ui()

    # ── Handlers internos (NÃO são @Slot, não aparecem no D-Bus) ────

    def _cancel_hide(self) -> None:
        self._hide_timer.stop()

    def _schedule_hide(self) -> None:
        self._hide_timer.start()

    def _do_slide_out(self) -> None:
        self._sidebar.slide_out()

    # ── API pública — exposta via SidebarAdaptor no D-Bus ────────────

    @Slot()
    def toggle(self) -> None:
        """Alterna visibilidade da sidebar."""
        if self._sidebar.is_visible:
            self._hide_timer.stop()
            self._sidebar.slide_out()
        else:
            self._sidebar.slide_in()

    @Slot()
    def slide_in(self) -> None:
        """Mostra a sidebar (idempotente)."""
        self._hide_timer.stop()
        self._sidebar.slide_in()

    @Slot()
    def slide_out(self) -> None:
        """Esconde a sidebar (idempotente)."""
        self._hide_timer.stop()
        self._sidebar.slide_out()

    # ── Inicialização ────────────────────────────────────────────────

    def start(self) -> None:
        """Registra o D-Bus. A sidebar começa escondida."""
        if not register_dbus_service(self):
            print("[PEEK] AVISO: D-Bus não registrado. Use 'qdbus' para diagnóstico.")
