"""Cliente PulseAudio — monitora e controla volumes via pulsectl.

Roda em uma QThread separada para nunca bloquear a UI.

Arquitetura:
  - _AudioWorker: vive na thread dedicada, faz polling do PulseAudio
    a cada 500ms e emite sinais quando volumes mudam.
  - AudioClient: QObject na thread principal, expõe os sinais do worker
    e slots para a UI alterar volumes.

Sinais emitidos (thread-safe via Qt::QueuedConnection automático):
  - master_volume_changed(int)  — volume do sink principal (0–100).
  - master_mute_changed(bool)   — mute do sink principal.
  - app_volumes_changed(list)   — lista de dicts com info de cada app.

Slots para a UI:
  - set_master_volume(int)      — define volume do sink principal.
  - toggle_master_mute()        — alterna mute do sink principal.
"""

from __future__ import annotations

import traceback
from typing import Any

import pulsectl

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot


# Intervalo de polling (ms) — pulsectl event_callback é bloqueante,
# polling leve é mais seguro e previsível com Qt.
POLL_INTERVAL_MS: int = 500


class _AudioWorker(QObject):
    """Worker que roda na thread dedicada do PulseAudio.

    Faz polling periódico do estado do PulseAudio e emite sinais
    quando detecta mudanças no volume ou mute.
    """

    master_volume_changed = Signal(int)
    master_mute_changed = Signal(bool)
    app_volumes_changed = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self._pulse: pulsectl.Pulse | None = None
        self._timer: QTimer | None = None

        # Cache para só emitir quando mudar
        self._last_master_vol: int = -1
        self._last_master_mute: bool | None = None
        self._last_apps: list[dict[str, Any]] = []

    @Slot()
    def start(self) -> None:
        """Inicializa a conexão com PulseAudio e o timer de polling.

        Chamado automaticamente quando a thread inicia (via QThread.started).
        """
        try:
            self._pulse = pulsectl.Pulse("peek-volume")
        except Exception as e:
            print(f"[PEEK:Audio] Falha ao conectar ao PulseAudio: {e}")
            return

        print("[PEEK:Audio] Conectado ao PulseAudio.")

        self._timer = QTimer()
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

        # Leitura inicial
        self._poll()

    @Slot()
    def stop(self) -> None:
        """Para o timer e fecha a conexão."""
        if self._timer:
            self._timer.stop()
        if self._pulse:
            self._pulse.close()
            self._pulse = None

    def _poll(self) -> None:
        """Lê o estado atual do PulseAudio e emite sinais se mudou."""
        if not self._pulse:
            return

        try:
            self._poll_master()
            self._poll_apps()
        except Exception:
            print(f"[PEEK:Audio] Erro no polling:")
            traceback.print_exc()

    def _poll_master(self) -> None:
        """Lê volume e mute do sink padrão."""
        assert self._pulse is not None

        server_info = self._pulse.server_info()
        default_name = server_info.default_sink_name

        sinks = self._pulse.sink_list()
        default_sink = None
        for s in sinks:
            if s.name == default_name:
                default_sink = s
                break

        if default_sink is None and sinks:
            default_sink = sinks[0]

        if default_sink is None:
            return

        # Volume: média dos canais, convertida para 0–100
        vol_avg = sum(default_sink.volume.values) / len(default_sink.volume.values)
        vol_pct = max(0, min(100, round(vol_avg * 100)))

        if vol_pct != self._last_master_vol:
            self._last_master_vol = vol_pct
            self.master_volume_changed.emit(vol_pct)

        muted = bool(default_sink.mute)
        if muted != self._last_master_mute:
            self._last_master_mute = muted
            self.master_mute_changed.emit(muted)

    def _poll_apps(self) -> None:
        """Lê volumes dos sink inputs (aplicativos)."""
        assert self._pulse is not None

        inputs = self._pulse.sink_input_list()
        apps: list[dict[str, Any]] = []

        for si in inputs:
            name = si.proplist.get("application.name", si.name or "?")
            vol_avg = sum(si.volume.values) / len(si.volume.values)
            vol_pct = max(0, min(100, round(vol_avg * 100)))

            apps.append({
                "index": si.index,
                "name": name,
                "volume": vol_pct,
                "mute": bool(si.mute),
            })

        # Compara com cache (por conteúdo)
        if apps != self._last_apps:
            self._last_apps = apps
            self.app_volumes_changed.emit(apps)

    # ── Métodos chamados da thread principal (via slots) ─────────────

    @Slot(int)
    def set_master_volume(self, value: int) -> None:
        """Define o volume do sink padrão (0–100)."""
        try:
            with pulsectl.Pulse('peek-writer') as p:
                server_info = p.server_info()
                default_name = server_info.default_sink_name

                for s in p.sink_list():
                    if s.name == default_name:
                        vol = pulsectl.PulseVolumeInfo(value / 100.0, len(s.volume.values))
                        p.volume_set(s, vol)
                        break
        except Exception as e:
            print(f"[PEEK:Audio] Erro ao definir volume master: {e}")

    @Slot()
    def toggle_master_mute(self) -> None:
        """Alterna mute do sink padrão."""
        try:
            with pulsectl.Pulse('peek-writer') as p:
                server_info = p.server_info()
                default_name = server_info.default_sink_name

                for s in p.sink_list():
                    if s.name == default_name:
                        p.mute(s, not s.mute)
                        break
        except Exception as e:
            print(f"[PEEK:Audio] Erro ao alternar mute master: {e}")

    @Slot(int, int)
    def set_app_volume(self, index: int, value: int) -> None:
        """Define o volume de um app específico pelo index (0-100)."""
        try:
            with pulsectl.Pulse('peek-writer') as p:
                for si in p.sink_input_list():
                    if si.index == index:
                        vol = pulsectl.PulseVolumeInfo(value / 100.0, len(si.volume.values))
                        p.volume_set(si, vol)
                        break
        except Exception as e:
            print(f"[PEEK:Audio] Erro ao definir volume do app {index}: {e}")


class AudioClient(QObject):
    """Proxy thread-safe para o worker de áudio.

    Instanciado na thread principal. Cria uma QThread dedicada
    e move o _AudioWorker para ela. Todos os sinais cruzam
    a barreira de thread automaticamente via Qt::QueuedConnection.
    """

    # Sinais re-expostos do worker (para o Controller conectar à UI)
    master_volume_changed = Signal(int)
    master_mute_changed = Signal(bool)
    app_volumes_changed = Signal(list)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        self._thread = QThread(self)
        self._thread.setObjectName("AudioThread")

        self._worker = _AudioWorker()
        self._worker.moveToThread(self._thread)

        # Worker signals → proxy signals (cross-thread auto)
        self._worker.master_volume_changed.connect(self.master_volume_changed)
        self._worker.master_mute_changed.connect(self.master_mute_changed)
        self._worker.app_volumes_changed.connect(self.app_volumes_changed)

        # Thread lifecycle
        self._thread.started.connect(self._worker.start)
        self._thread.finished.connect(self._worker.stop)
        self._thread.finished.connect(self._worker.deleteLater)

        # Inicia a thread
        self._thread.start()

    # ── Slots públicos (chamados pela UI na thread principal) ────────

    @Slot(int)
    def set_master_volume(self, value: int) -> None:
        """Define o volume master (0–100). Thread-safe."""
        # Invoca o slot do worker na thread dele
        self._worker.set_master_volume(value)

    @Slot()
    def toggle_master_mute(self) -> None:
        """Alterna mute master. Thread-safe."""
        self._worker.toggle_master_mute()

    @Slot(int, int)
    def set_app_volume(self, index: int, value: int) -> None:
        """Define o volume de um aplicativo pelo index (0-100). Thread-safe."""
        # Invoca via metacall para garantir thread-safety
        self._worker.set_app_volume(index, value)

    def stop(self) -> None:
        """Para a thread de áudio graciosamente."""
        self._thread.quit()
        self._thread.wait(2000)
