"""Controller — O cérebro do PEEK.

Registra-se no D-Bus (org.peek.App) e expõe toggle/slide_in/slide_out.
O KWin Script dispara esses métodos quando o mouse encosta na borda.
Gerencia o timer de auto-hide quando o mouse sai da sidebar."""

from PySide6.QtCore import ClassInfo, QObject, QTimer, Slot, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from core.dbus_service import register_dbus_service
from services.art_downloader import ArtDownloader
from services.audio_client import AudioClient
from services.mpris_client import MprisClient
from ui.sidebar_window import SidebarWindow


@ClassInfo({"D-Bus Interface": "org.peek.App"})
class Controller(QObject):
    """Conecta o D-Bus (input externo) com a sidebar (UI).

    Fluxo:
    1. KWin Script detecta borda → chama Toggle via D-Bus.
    2. Controller mostra/esconde a sidebar.
    3. Mouse sai da sidebar → timer de 400ms → slide_out automático.
    4. Mouse re-entra na sidebar antes do timeout → timer cancelado.
    """

    HIDE_DELAY_MS: int = 400
    SURVIVAL_MS: int = 3_500  # Fecha sozinha se o mouse não entrar após abertura via D-Bus

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        # Workaround para Qt.ToolTip no Wayland: A sidebar precisa de uma janela
        # raiz real (xdg-toplevel) para agir como âncora (transient parent),
        # senão o QPA falha em mapear o popup ("Failed to create popup").
        # SplashScreen: o protocolo XDG Wayland garante que splash screens
        # nunca geram entrada na taskbar — sem fallback de gerenciamento.
        self._anchor = QWidget()
        self._anchor.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowTransparentForInput
        )
        self._anchor.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._anchor.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._anchor.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Força a âncora a ter o tamanho exato da tela.
        # Assim, quando o KWin a centralizar, o (0,0) local será o (0,0) global.
        screen_rect = QGuiApplication.primaryScreen().availableGeometry()
        self._anchor.setGeometry(screen_rect)
        self._anchor.hide()  # Inicia oculta no background (taskbar limpa)

        self._sidebar = SidebarWindow(self._anchor)
        self._sidebar.hidden_fully.connect(self._anchor.hide)
        self._mpris = MprisClient(self)
        self._art_downloader = ArtDownloader(self)
        self._audio = AudioClient(self)
        self._is_service_active: bool = True  # Estado do serviço (pode ser pausado via GUI)

        # Timer de auto-hide: ativado quando o mouse SAI da sidebar
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(self.HIDE_DELAY_MS)

        # Survival timer: ativado quando a sidebar ABRE via D-Bus.
        # Se o mouse não entrar na janela antes do timeout, ela fecha sozinha.
        self._survival_timer = QTimer(self)
        self._survival_timer.setSingleShot(True)
        self._survival_timer.setInterval(self.SURVIVAL_MS)
        self._survival_timer.timeout.connect(self._do_slide_out)

        self._connect_signals()

    def _connect_signals(self) -> None:
        # Sidebar hide/show
        self._sidebar.mouse_entered.connect(self._on_mouse_entered)
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
        media.seek_requested.connect(self._mpris.set_position)

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

    def _on_mouse_entered(self) -> None:
        """Mouse entrou na sidebar: cancela hide timer E survival timer."""
        self._hide_timer.stop()
        self._survival_timer.stop()

    def _cancel_hide(self) -> None:
        self._hide_timer.stop()

    def _schedule_hide(self) -> None:
        self._hide_timer.start()

    def _do_slide_out(self) -> None:
        self._sidebar.slide_out()

    # ── API pública — exposta diretamente no D-Bus ────────────────────

    @Slot()
    def Toggle(self) -> None:
        """Alterna visibilidade da sidebar (bloqueado se serviço pausado)."""
        if not self._is_service_active:
            return
        if self._sidebar.is_visible:
            self._hide_timer.stop()
            self._survival_timer.stop()
            self._sidebar.slide_out()
        else:
            self._anchor.show()  # Mostra a âncora antes da animação
            self._sidebar.slide_in()
            self._survival_timer.start()  # Arma o timer de sobrevivência

    @Slot()
    def SlideIn(self) -> None:
        """Mostra a sidebar (bloqueado se serviço pausado)."""
        if not self._is_service_active:
            return
        self._hide_timer.stop()
        self._anchor.show()  # Mostra a âncora antes da animação
        self._sidebar.slide_in()
        self._survival_timer.start()  # Arma o timer de sobrevivência

    @Slot()
    def SlideOut(self) -> None:
        """Esconde a sidebar (idempotente)."""
        self._hide_timer.stop()
        self._survival_timer.stop()
        self._sidebar.slide_out()

    @Slot(bool)
    def SetServiceActive(self, active: bool) -> None:  # noqa: N802
        """Pausa ou retoma a detecção de borda. Chamado pelo Control Center via D-Bus."""
        self._is_service_active = active
        if not active:
            # Fecha a sidebar imediatamente ao pausar
            self._hide_timer.stop()
            self._survival_timer.stop()
            self._sidebar.slide_out()

    # ── Inicialização ────────────────────────────────────────────

    def start(self) -> None:
        """Registra o D-Bus. A sidebar começa escondida."""
        if not register_dbus_service(self):
            print("[PEEK] AVISO: D-Bus não registrado. Use 'qdbus' para diagnóstico.")
