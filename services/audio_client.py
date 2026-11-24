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
  - toggle_stream_mute(index)   — alterna mute de um sink input específico.
  - toggle_mic_mute()           — alterna mute da source padrão (microfone).
"""

from __future__ import annotations

import subprocess
import time
import traceback
from typing import Any

import pulsectl

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot


# Intervalo de polling (ms) — pulsectl event_callback é bloqueante,
# polling leve é mais seguro e previsível com Qt.
POLL_INTERVAL_MS: int = 500
MONO_SINK_NAME: str = "peek_mono_sink"


def _channels_to_balance(values: list[float]) -> float:
    """Converte volumes por canal [L, R, ...] em balanço normalizado [-1.0, 1.0]."""
    if len(values) < 2:
        return 0.0
    left = float(values[0])
    right = float(values[1])
    peak = max(left, right)
    if peak <= 1e-6:
        return 0.0
    if left >= right:
        bal = -(1.0 - (right / left))
    else:
        bal = 1.0 - (left / right)
    return max(-1.0, min(1.0, bal))


def _apply_balance_to_volume(vol_norm: float, balance: float, n_channels: int) -> list[float]:
    """Calcula os volumes por canal preservando pico (vol_norm) e balanço [-1.0, 1.0]."""
    vol = max(0.0, min(1.0, float(vol_norm)))
    bal = max(-1.0, min(1.0, float(balance)))
    if n_channels < 2:
        return [vol] * max(1, n_channels)
    if bal <= 0.0:
        left = vol
        right = vol * (1.0 + bal)
    else:
        left = vol * (1.0 - bal)
        right = vol
    if n_channels == 2:
        return [left, right]
    return [left, right] + [vol] * (n_channels - 2)


class _AudioWorker(QObject):
    """Worker que roda na thread dedicada do PulseAudio.

    Faz polling periódico do estado do PulseAudio e emite sinais
    quando detecta mudanças no volume ou mute.
    """

    master_volume_changed = Signal(int)
    master_mute_changed   = Signal(bool)
    app_volumes_changed   = Signal(list)
    mic_mute_changed      = Signal(bool)  # estado de mute do microfone padrão

    def __init__(self) -> None:
        super().__init__()
        self._pulse: pulsectl.Pulse | None = None
        self._timer: QTimer | None = None

        # Cache para só emitir quando mudar
        self._last_master_vol:  int            = -1
        self._last_master_mute: bool | None    = None
        self._last_apps:        list[dict[str, Any]] = []
        self._last_mic_mute:    bool | None    = None
        self._sinks_cache:      list[dict[str, Any]] = []
        self._sources_cache:    list[dict[str, Any]] = []
        self._last_balance:     float                = 0.0
        self._is_mono:          bool                 = False
        self._mono_base_sink:   str                  = ""

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
            self._poll_mic()
        except Exception:
            print(f"[PEEK:Audio] Erro no polling:")
            traceback.print_exc()

    def _poll_master(self) -> None:
        """Lê volume e mute do sink padrão."""
        assert self._pulse is not None

        server_info = self._pulse.server_info()
        default_name = server_info.default_sink_name
        self._is_mono = (default_name == MONO_SINK_NAME)
        if default_name and default_name != MONO_SINK_NAME:
            self._mono_base_sink = default_name

        sinks = self._pulse.sink_list()
        active_hw_name = self._mono_base_sink if self._is_mono else default_name
        self._sinks_cache = [
            {
                "name": s.name,
                "description": s.description or s.name,
                "is_default": s.name == active_hw_name,
            }
            for s in sinks
            if s.name != MONO_SINK_NAME
        ]
        default_sink = None
        for s in sinks:
            if s.name == default_name:
                default_sink = s
                break

        if default_sink is None and sinks:
            default_sink = sinks[0]

        if default_sink is None:
            return

        # Pico dos canais preserva o balanço L/R sem derrubar o slider
        ch_vals = list(default_sink.volume.values)
        peak = max(ch_vals) if ch_vals else 0.0
        vol_pct = max(0, min(100, round(peak * 100)))
        self._last_balance = _channels_to_balance(ch_vals)

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

    def _poll_mic(self) -> None:
        """Lê o estado de mute da source padrão (microfone)."""
        assert self._pulse is not None
        try:
            server_info = self._pulse.server_info()
            default_src = server_info.default_source_name
            sources = self._pulse.source_list()
            self._sources_cache = [
                {
                    "name": src.name,
                    "description": src.description or src.name,
                    "is_default": src.name == default_src,
                }
                for src in sources
                if getattr(src, "monitor_of_sink_name", None) is None
                and not src.name.endswith(".monitor")
            ]
            for src in sources:
                if src.name == default_src:
                    muted = bool(src.mute)
                    if muted != self._last_mic_mute:
                        self._last_mic_mute = muted
                        self.mic_mute_changed.emit(muted)
                    break
        except Exception:
            pass  # source pode não estar disponível em todos os sistemas

    # ── Métodos chamados da thread principal (via slots) ─────────────

    @Slot(int)
    def set_master_volume(self, value: int) -> None:
        """Define o volume do sink padrão (0–100), preservando o balanço L/R."""
        try:
            with pulsectl.Pulse('peek-writer') as p:
                server_info = p.server_info()
                default_name = server_info.default_sink_name

                for s in p.sink_list():
                    if s.name == default_name:
                        cur_vals = list(s.volume.values)
                        bal = _channels_to_balance(cur_vals)
                        target_vals = _apply_balance_to_volume(value / 100.0, bal, len(cur_vals))
                        p.volume_set(s, pulsectl.PulseVolumeInfo(target_vals))
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

    def get_stream_name(self, index: int) -> str | None:
        """Recupera o nome do app a partir do seu index no PulseAudio."""
        for stream in self._last_apps:
            if stream.get("index") == index:
                return stream.get("name")
        return None

    @Slot(int)
    def toggle_stream_mute(self, index: int) -> None:
        """Alterna o mute de um sink input específico pelo index."""
        try:
            with pulsectl.Pulse('peek-mute-writer') as p:
                for si in p.sink_input_list():
                    if si.index == index:
                        p.mute(si, not bool(si.mute))
                        break
        except Exception as e:
            print(f"[PEEK:Audio] Erro ao alternar mute do stream {index}: {e}")

    @Slot()
    def toggle_mic_mute(self) -> None:
        """Alterna o mute da source padrão (microfone)."""
        try:
            with pulsectl.Pulse('peek-mic-writer') as p:
                server_info = p.server_info()
                default_src = server_info.default_source_name
                for src in p.source_list():
                    if src.name == default_src:
                        p.mute(src, not bool(src.mute))
                        break
        except Exception as e:
            print(f"[PEEK:Audio] Erro ao alternar mute do mic: {e}")

    @Slot()
    def cycle_default_sink(self) -> None:
        """Alterna a saída de áudio para o próximo Sink disponível e move os streams ativos."""
        try:
            with pulsectl.Pulse('peek-route-writer') as p:
                sinks = p.sink_list()
                if len(sinks) <= 1:
                    return

                server_info = p.server_info()
                current_default = server_info.default_sink_name

                # Encontra o index do Sink atual na lista
                current_idx = -1
                for i, sink in enumerate(sinks):
                    if sink.name == current_default:
                        current_idx = i
                        break

                # Próximo Sink na lista (circular)
                next_idx = (current_idx + 1) % len(sinks)
                next_sink = sinks[next_idx]

                # Define como padrão
                p.default_set(next_sink)

                # Move todos os Sink Inputs ativos para o novo Sink
                for si in p.sink_input_list():
                    try:
                        p.sink_input_move(si.index, next_sink.index)
                    except Exception as e:
                        print(f"[PEEK:Audio] Erro ao mover stream {si.index} para sink {next_sink.index}: {e}")

                print(f"[PEEK:Audio] Saída de áudio alterada para: {next_sink.description}")
        except Exception as e:
            print(f"[PEEK:Audio] Erro ao alternar a saída de áudio: {e}")

    def get_sinks(self) -> list[dict[str, Any]]:
        """Retorna cópia rasa da lista cacheada de saídas (sinks)."""
        return [dict(item) for item in self._sinks_cache]

    def get_sources(self) -> list[dict[str, Any]]:
        """Retorna cópia rasa da lista cacheada de entradas (sources sem monitors)."""
        return [dict(item) for item in self._sources_cache]

    def is_master_muted(self) -> bool:
        return bool(self._last_master_mute)

    def is_mic_muted(self) -> bool:
        return bool(self._last_mic_mute)

    def get_master_balance(self) -> float:
        return self._last_balance

    def is_mono_enabled(self) -> bool:
        return self._is_mono

    @Slot(float)
    def set_master_balance(self, balance: float) -> None:
        """Define o balanço L/R (-1.0 a 1.0) no sink padrão sem perder o volume de pico."""
        bal = max(-1.0, min(1.0, float(balance)))
        self._last_balance = bal
        try:
            with pulsectl.Pulse("peek-balance-writer") as p:
                default_name = p.server_info().default_sink_name
                for s in p.sink_list():
                    if s.name == default_name:
                        cur_vals = list(s.volume.values)
                        peak = max(cur_vals) if cur_vals else 0.5
                        target_vals = _apply_balance_to_volume(peak, bal, len(cur_vals))
                        p.volume_set(s, pulsectl.PulseVolumeInfo(target_vals))
                        break
        except Exception as e:
            print(f"[PEEK:Audio] Erro ao definir balanço master: {e}")

    def _find_mono_module_index(self, p: pulsectl.Pulse) -> int | None:
        for mod in p.module_list():
            arg = getattr(mod, "argument", "") or ""
            if MONO_SINK_NAME in arg:
                return mod.index
        return None

    @Slot(bool)
    def set_mono_mode(self, enabled: bool) -> None:
        """Ativa ou desativa downmix Mono via module-remap-sink mantendo volume/mute."""
        try:
            with pulsectl.Pulse("peek-mono-writer") as p:
                server_info = p.server_info()
                default_name = server_info.default_sink_name
                sinks = p.sink_list()

                if enabled:
                    base_name = default_name if default_name and default_name != MONO_SINK_NAME else self._mono_base_sink
                    if not base_name:
                        hw = next((s for s in sinks if s.name != MONO_SINK_NAME), None)
                        base_name = hw.name if hw is not None else ""
                    if not base_name:
                        return
                    self._mono_base_sink = base_name
                    base_sink = next((s for s in sinks if s.name == base_name), None)

                    mono_sink = next((s for s in sinks if s.name == MONO_SINK_NAME), None)
                    if mono_sink is None:
                        args = (
                            f"sink_name={MONO_SINK_NAME} master={base_name} "
                            "channels=2 channel_map=mono,mono remix=yes "
                            "sink_properties=\"device.description='Mono (PEEK)'\""
                        )
                        try:
                            p.module_load("module-remap-sink", args)
                        except Exception:
                            subprocess.run(
                                ["pactl", "load-module", "module-remap-sink", *args.split()],
                                capture_output=True,
                                check=False,
                            )
                        for _ in range(10):
                            time.sleep(0.03)
                            mono_sink = next((s for s in p.sink_list() if s.name == MONO_SINK_NAME), None)
                            if mono_sink is not None:
                                break

                    if mono_sink is None:
                        return

                    if base_sink is not None:
                        base_vals = list(base_sink.volume.values)
                        p.volume_set(mono_sink, pulsectl.PulseVolumeInfo(base_vals[:len(mono_sink.volume.values)]))
                        p.mute(mono_sink, bool(base_sink.mute))

                    p.default_set(mono_sink)
                    for si in p.sink_input_list():
                        try:
                            p.sink_input_move(si.index, mono_sink.index)
                        except Exception:
                            pass
                    self._is_mono = True
                    print(f"[PEEK:Audio] Áudio Mono ativado sobre {base_name}")
                else:
                    target_name = self._mono_base_sink
                    target_sink = next((s for s in sinks if s.name == target_name and s.name != MONO_SINK_NAME), None)
                    if target_sink is None:
                        target_sink = next((s for s in sinks if s.name != MONO_SINK_NAME), None)

                    mono_sink = next((s for s in sinks if s.name == MONO_SINK_NAME), None)
                    if target_sink is not None:
                        if mono_sink is not None:
                            mono_vals = list(mono_sink.volume.values)
                            p.volume_set(target_sink, pulsectl.PulseVolumeInfo(mono_vals[:len(target_sink.volume.values)]))
                            p.mute(target_sink, bool(mono_sink.mute))
                        p.default_set(target_sink)
                        for si in p.sink_input_list():
                            try:
                                p.sink_input_move(si.index, target_sink.index)
                            except Exception:
                                pass

                    mod_idx = self._find_mono_module_index(p)
                    if mod_idx is not None:
                        p.module_unload(mod_idx)
                    self._is_mono = False
                    print("[PEEK:Audio] Áudio Estéreo restaurado")
        except Exception as e:
            print(f"[PEEK:Audio] Erro ao alternar modo Mono/Estéreo: {e}")

    @Slot(str)
    def set_default_sink(self, sink_name: str) -> None:
        """Define um sink específico como padrão e move os streams ativos para ele."""
        if not sink_name or sink_name == MONO_SINK_NAME:
            return
        was_mono = self._is_mono
        if was_mono:
            self.set_mono_mode(False)
        self._mono_base_sink = sink_name
        try:
            with pulsectl.Pulse("peek-route-writer") as p:
                target = next((s for s in p.sink_list() if s.name == sink_name), None)
                if target is None:
                    return
                p.default_set(target)
                for si in p.sink_input_list():
                    try:
                        p.sink_input_move(si.index, target.index)
                    except Exception as e:
                        print(f"[PEEK:Audio] Erro ao mover stream {si.index}: {e}")
                print(f"[PEEK:Audio] Saída selecionada: {target.description}")
        except Exception as e:
            print(f"[PEEK:Audio] Erro ao selecionar saída '{sink_name}': {e}")
        if was_mono:
            self.set_mono_mode(True)

    @Slot(str)
    def set_default_source(self, source_name: str) -> None:
        """Define uma source específica como padrão e move source outputs ativos."""
        try:
            with pulsectl.Pulse("peek-route-writer") as p:
                target = next((s for s in p.source_list() if s.name == source_name), None)
                if target is None:
                    return
                p.default_set(target)
                for so in p.source_output_list():
                    try:
                        p.source_output_move(so.index, target.index)
                    except Exception as e:
                        print(f"[PEEK:Audio] Erro ao mover entrada {so.index}: {e}")
                print(f"[PEEK:Audio] Microfone selecionado: {target.description}")
        except Exception as e:
            print(f"[PEEK:Audio] Erro ao selecionar entrada '{source_name}': {e}")


class AudioClient(QObject):
    """Proxy thread-safe para o worker de áudio.

    Instanciado na thread principal. Cria uma QThread dedicada
    e move o _AudioWorker para ela. Todos os sinais cruzam
    a barreira de thread automaticamente via Qt::QueuedConnection.
    """

    # Sinais re-expostos do worker (para o Controller conectar à UI)
    master_volume_changed = Signal(int)
    master_mute_changed   = Signal(bool)
    app_volumes_changed   = Signal(list)
    mic_mute_changed      = Signal(bool)  # estado de mute do microfone

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
        self._worker.mic_mute_changed.connect(self.mic_mute_changed)

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

    @Slot(int)
    def toggle_stream_mute(self, index: int) -> None:
        """Alterna mute de um sink input específico. Thread-safe."""
        self._worker.toggle_stream_mute(index)

    @Slot()
    def toggle_mic_mute(self) -> None:
        """Alterna mute da source padrão (microfone). Thread-safe."""
        self._worker.toggle_mic_mute()

    @Slot()
    def cycle_default_sink(self) -> None:
        """Avança para a próxima saída de áudio e move os streams. Thread-safe."""
        self._worker.cycle_default_sink()

    @Slot(str)
    def set_default_sink(self, sink_name: str) -> None:
        """Seleciona a saída de áudio padrão pelo nome. Thread-safe."""
        self._worker.set_default_sink(sink_name)

    @Slot(str)
    def set_default_source(self, source_name: str) -> None:
        """Seleciona a entrada de áudio (mic) padrão pelo nome. Thread-safe."""
        self._worker.set_default_source(source_name)

    def get_sinks(self) -> list[dict[str, Any]]:
        """Retorna os sinks cacheados pelo worker."""
        return self._worker.get_sinks()

    def get_sources(self) -> list[dict[str, Any]]:
        """Retorna as sources (sem monitors) cacheadas pelo worker."""
        return self._worker.get_sources()

    def is_master_muted(self) -> bool:
        return self._worker.is_master_muted()

    def is_mic_muted(self) -> bool:
        return self._worker.is_mic_muted()

    def get_master_balance(self) -> float:
        return self._worker.get_master_balance()

    def is_mono_enabled(self) -> bool:
        return self._worker.is_mono_enabled()

    @Slot(float)
    def set_master_balance(self, balance: float) -> None:
        """Define o balanço L/R master (-1.0 a 1.0). Thread-safe."""
        self._worker.set_master_balance(balance)

    @Slot(bool)
    def set_mono_mode(self, enabled: bool) -> None:
        """Ativa/desativa o downmix Mono via module-remap-sink. Thread-safe."""
        self._worker.set_mono_mode(enabled)

    def stop(self) -> None:
        """Para a thread de áudio graciosamente."""
        self._thread.quit()
        self._thread.wait(2000)

    def get_stream_name(self, index: int) -> str | None:
        """Expõe a busca do nome do stream a partir do worker."""
        return self._worker.get_stream_name(index)
