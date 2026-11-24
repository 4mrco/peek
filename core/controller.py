"""Controller — O cérebro do PEEK.

Registra-se no D-Bus (org.peek.App) e expõe toggle/slide_in/slide_out.
O KWin Script dispara esses métodos quando o mouse encosta na borda.
Gerencia o timer de auto-hide quando o mouse sai da sidebar."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import ClassInfo, QObject, QPoint, QTimer, Slot, Qt

from core.config import load_config
from core.dbus_service import register_dbus_service

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

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

    HIDE_DELAY_MS: int = 350
    SURVIVAL_MS: int = 3_500  # Fecha sozinha se o mouse não entrar após abertura via D-Bus

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        # Lazy-init: UI e workers ficam em None no boot --daemon até o primeiro
        # Toggle/SlideIn para minimizar consumo de RAM em idle.
        self._initialized: bool = False
        self._anchor: QWidget | None = None
        self._sidebar: SidebarWindow | None = None
        self._mpris: MprisClient | None = None
        self._art_downloader: ArtDownloader | None = None
        self._audio: AudioClient | None = None
        self._is_service_active: bool = True  # Estado do serviço (pode ser pausado via GUI)
        self._menu_open: bool = False
        self._config: dict = load_config()

        # Timer de auto-hide: ativado quando o mouse SAI da sidebar
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(self.HIDE_DELAY_MS)
        self._hide_timer.timeout.connect(self._do_slide_out)

        # Survival timer: ativado quando a sidebar ABRE via D-Bus.
        # Se o mouse não entrar na janela antes do timeout, ela fecha sozinha.
        self._survival_timer = QTimer(self)
        self._survival_timer.setSingleShot(True)
        self._survival_timer.setInterval(self.SURVIVAL_MS)
        self._survival_timer.timeout.connect(self._do_slide_out)

    def _ensure_initialized(self) -> None:
        """Instancia âncora, sidebar, workers e conecta sinais na primeira demanda."""
        if self._initialized:
            return

        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QWidget

        from services.art_downloader import ArtDownloader
        from services.audio_client import AudioClient
        from services.mpris_client import MprisClient
        from ui.sidebar_window import SidebarWindow

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
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self._anchor.setGeometry(screen.availableGeometry())
        self._anchor.hide()  # Inicia oculta no background (taskbar limpa)

        self._sidebar = SidebarWindow(self._anchor)
        self._sidebar.hidden_fully.connect(self._anchor.hide)
        self._apply_config_to_sidebar()
        self._mpris = MprisClient(self)
        self._art_downloader = ArtDownloader(self)
        self._audio = AudioClient(self)

        self._connect_signals()
        self._initialized = True
        print("[PEEK:Daemon] UI e workers inicializados sob demanda.")

    def _connect_signals(self) -> None:
        assert self._sidebar is not None
        assert self._mpris is not None
        assert self._art_downloader is not None
        assert self._audio is not None

        # Sidebar hide/show & guarda de menus Wayland
        self._sidebar.mouse_entered.connect(self._on_mouse_entered)
        self._sidebar.mouse_left.connect(self._schedule_hide)
        self._sidebar.menu_opened.connect(self._on_menu_opened)
        self._sidebar.menu_closed.connect(self._on_menu_closed)
        self._sidebar.output_menu_requested.connect(self._on_output_menu_requested)
        self._sidebar.mic_menu_requested.connect(self._on_mic_menu_requested)
        self._sidebar.player_menu_requested.connect(self._on_player_menu_requested)

        # MPRIS serviço → UI (Conectados! O fluxo está ativo)
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

        # Audio (Backend) → UI
        vol_slider = self._sidebar.volume_slider
        self._audio.master_volume_changed.connect(vol_slider.set_volume)
        self._audio.master_mute_changed.connect(vol_slider.set_muted)
        self._audio.app_volumes_changed.connect(self._sidebar.update_app_sliders)
        self._audio.mic_mute_changed.connect(self._sidebar.on_mic_mute_changed)

        # UI → Audio (Backend)
        vol_slider.volume_changed.connect(self._audio.set_master_volume)
        self._sidebar.app_volume_changed.connect(self._on_app_volume_changed)
        self._sidebar.app_mute_toggled.connect(self._audio.toggle_stream_mute)
        self._sidebar.master_mute_requested.connect(self._audio.toggle_master_mute)
        self._sidebar.mic_mute_requested.connect(self._audio.toggle_mic_mute)
        self._sidebar.output_cycle_requested.connect(self._audio.cycle_default_sink)
        self._sidebar.sink_selected.connect(self._audio.set_default_sink)
        self._sidebar.source_selected.connect(self._audio.set_default_source)
        self._sidebar.player_selected.connect(self._mpris.select_player)
        
        # Força o MPRIS a popular a UI após todas as conexões terem sido feitas
        self._mpris.force_sync_ui()

    # ── Handlers internos (NÃO são @Slot, não aparecem no D-Bus) ────

    def _on_app_volume_changed(self, stream_index: int, vol_percent: int) -> None:
        """Sincroniza o volume do app no PulseAudio e, se for o player ativo, no MPRIS."""
        if self._audio is None or self._mpris is None:
            return
        self._audio.set_app_volume(stream_index, vol_percent)
        
        stream_name = self._audio.get_stream_name(stream_index)
        active_player = self._mpris.active_player_name
        
        if stream_name and active_player:
            # Comparação case-insensitive (ex: "spotify" in "Spotify")
            if stream_name.lower() in active_player.lower() or active_player.lower() in stream_name.lower():
                self._mpris.set_volume(vol_percent / 100.0)

    def _apply_config_to_sidebar(self) -> None:
        if self._sidebar is None:
            return
        self._sidebar.set_trigger_edge(self._config["trigger_edge"])
        self._sidebar.apply_appearance(
            self._config["bg_color"],
            self._config["primary_color"],
            self._config["bg_opacity"],
        )

    def _on_output_menu_requested(self, global_pos: QPoint) -> None:
        if self._sidebar is None or self._audio is None:
            return
        self._sidebar.show_output_menu(
            self._audio.get_sinks(),
            self._audio.is_master_muted(),
            self._audio.is_mic_muted(),
            global_pos,
        )

    def _on_mic_menu_requested(self, global_pos: QPoint) -> None:
        if self._sidebar is None or self._audio is None:
            return
        self._sidebar.show_mic_menu(
            self._audio.get_sources(),
            self._audio.is_mic_muted(),
            global_pos,
        )

    def _on_player_menu_requested(self, global_pos: QPoint) -> None:
        if self._sidebar is None or self._mpris is None:
            return
        self._sidebar.show_player_menu(self._mpris.list_players(), global_pos)

    def _on_menu_opened(self) -> None:
        """Trava o auto-hide enquanto um QMenu de contexto estiver aberto no Wayland."""
        self._menu_open = True
        self._hide_timer.stop()
        self._survival_timer.stop()

    def _on_menu_closed(self) -> None:
        """Libera a trava após fechar o QMenu e agenda hide se ainda visível."""
        self._menu_open = False
        if self._sidebar is not None and self._sidebar.is_visible:
            self._hide_timer.start()

    def _on_mouse_entered(self) -> None:
        """Mouse entrou na sidebar: cancela hide timer E survival timer."""
        self._hide_timer.stop()
        self._survival_timer.stop()

    def _cancel_hide(self) -> None:
        self._hide_timer.stop()

    def _schedule_hide(self) -> None:
        if self._menu_open:
            return
        self._hide_timer.start()

    def _do_slide_out(self) -> None:
        if self._menu_open:
            return
        if self._sidebar is not None:
            self._sidebar.slide_out()

    # ── API pública — exposta diretamente no D-Bus ────────────────────

    @Slot()
    def Toggle(self) -> None:
        """Alterna visibilidade da sidebar (bloqueado se serviço pausado)."""
        if not self._is_service_active:
            return
        self._ensure_initialized()
        assert self._sidebar is not None and self._anchor is not None
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
        self._ensure_initialized()
        assert self._sidebar is not None and self._anchor is not None
        self._hide_timer.stop()
        self._anchor.show()  # Mostra a âncora antes da animação
        self._sidebar.slide_in()
        self._survival_timer.start()  # Arma o timer de sobrevivência

    @Slot()
    def SlideOut(self) -> None:
        """Esconde a sidebar (idempotente)."""
        self._hide_timer.stop()
        self._survival_timer.stop()
        if self._sidebar is not None:
            self._sidebar.slide_out()

    @Slot(bool)
    def SetServiceActive(self, active: bool) -> None:  # noqa: N802
        """Pausa ou retoma a detecção de borda. Chamado pelo Control Center via D-Bus."""
        self._is_service_active = active
        if not active:
            # Fecha a sidebar imediatamente ao pausar
            self._hide_timer.stop()
            self._survival_timer.stop()
            if self._sidebar is not None:
                self._sidebar.slide_out()

    @Slot()
    def ReloadConfig(self) -> None:  # noqa: N802
        """Recarrega config.json e reaplica aparência/borda na sidebar."""
        self._config = load_config()
        self._apply_config_to_sidebar()

    @Slot(str, str, int)
    def PreviewAppearance(self, bg_color: str, primary_color: str, bg_opacity: int) -> None:  # noqa: N802
        """Aplica prévia em tempo real de aparência sem gravar em disco."""
        if self._sidebar is not None:
            self._sidebar.apply_appearance(bg_color, primary_color, bg_opacity)

    @Slot(result=bool)
    def IsServiceActive(self) -> bool:  # noqa: N802
        """Retorna se a detecção de borda está ativa (usado pelo Control Center)."""
        return self._is_service_active

    # ── Inicialização ────────────────────────────────────────────

    def start(self) -> None:
        """Registra o D-Bus. A sidebar começa escondida."""
        if not register_dbus_service(self):
            print("[PEEK] AVISO: D-Bus não registrado. Use 'qdbus' para diagnóstico.")
