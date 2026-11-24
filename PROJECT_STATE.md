# PROJECT_STATE.md — PEEK

> **Última atualização:** 2026-11-24 — Task #36 (Control Center Resizable/Fit, Output Menu Balance + Mono/Stereo & Wayland Post-Menu Auto-Hide Fix)  
> **Regra:** Este arquivo DEVE ser atualizado ao final de cada funcionalidade grande.

---

## Objetivo (MVP)

Painel lateral (sidebar) rápido para Linux/KDE Plasma 6 + Wayland.  
Companheiro invisível: você só olha para ele (PEEK) quando precisa. Sem bloat.

**Funcionalidades do MVP:**
- Controle de mídia (MPRIS/Spotify) — ✅ Completo
- Controle de volume (Master + por App via PipeWire/PulseAudio) — ✅ Completo

---

## Stack Tecnológica

| Componente       | Tecnologia                          |
|------------------|-------------------------------------|
| Linguagem        | Python 3                            |
| UI / Framework   | PySide6 (Qt6)                       |
| D-Bus            | QtDBus (nativo do PySide6)          |
| Áudio            | pulsectl (PulseAudio/PipeWire)      |
| Edge Detection   | KWin Script (JavaScript)            |
| Window Placement | KWin Rules (kwinrulesrc)            |
| Ambiente Alvo    | Wayland — KDE Plasma 6 (Nobara 44)  |

---

## Arquitetura

### Estrutura de Pastas

```
Kslide/
├── main.py                      # Entrypoint
├── PROJECT_STATE.md             # ← Este arquivo
├── core/
│   ├── controller.py            # O cérebro — conecta D-Bus com UI
│   └── dbus_service.py          # QDBusAbstractAdaptor (org.kslide.App)
├── ui/
│   ├── sidebar_window.py        # Painel principal (QPropertyAnimation)
│   └── components/
│       ├── media_player.py      # Widget de controle de mídia (Task #02)
│       └── volume_slider.py     # Slider vertical customizado (Task #03)
├── services/
│   ├── mpris_client.py          # Cliente MPRIS via QtDBus (Task #02)
│   ├── art_downloader.py        # Download assíncrono de capa via QNAM (Task #02.4)
│   └── audio_client.py          # Cliente PulseAudio via pulsectl (Task #03)
├── kwin-script/
│   └── kslide-edge-trigger/     # KWin Script — detecta borda (TopRight, hardcoded)
│       ├── metadata.json
│       └── contents/code/main.js
└── scripts/
    ├── install-kwin-script.sh   # Instala e ativa o KWin Script
    └── apply-kwin-rule.sh       # Injeta regra de posicionamento no KWin
```

### Regra de Ouro: Event-Driven via Qt Signals

```
Backend (services/)  ──Signal──►  Controller (core/)  ──Signal──►  UI (ui/)
                                       ▲
                                  D-Bus (externo)
                                       ▲
                                  KWin Script (borda)
```

- **Services** emitem sinais. NUNCA tocam na UI.
- **Controller** orquestra. Conecta sinais de backend com métodos da UI.
- **UI** escuta sinais e se atualiza. NUNCA chama backend diretamente.
- **D-Bus** é o ponto de entrada externo (KWin Script chama `Toggle`).

---

## Decisões Técnicas / Quirks do Wayland

### 1. Detecção de Borda: KWin Script + D-Bus

**Problema:** No Wayland, aplicações não podem posicionar janelas livremente.
Uma janela invisível de 1px (trigger_area.py) virou uma janela normal com ícone na taskbar.

**Solução:** Delegamos a detecção de borda para o KWin nativo.

| Componente | Responsabilidade |
|---|---|
| `kwin-script/main.js` | Registra `ElectricTopRight` (hardcoded) via `registerScreenEdge(1, ...)` |
| `kwin-script/main.js` | Chama `callDBus("org.peek.App", "/App", "org.peek.App", "Toggle")` |
| `core/dbus_service.py` | `QDBusAbstractAdaptor` (org.peek.App) com `@ClassInfo({"D-Bus Interface": "org.peek.App"})` |
| `core/controller.py` | Recebe `Toggle` via D-Bus → chama `sidebar.slide_in()` ou `slide_out()` |

**Instalação:** `bash scripts/install-kwin-script.sh`

### 2. Posicionamento da Janela: KWin Rules

**Problema:** `QWidget.setGeometry()` / `move()` são ignorados pelo compositor Wayland.
A sidebar abria no canto superior esquerdo como janela genérica.

**Solução:** KWin Rule injetada via `kwriteconfig6` no `~/.config/kwinrulesrc`.

| Propriedade   | Valor                         | Tipo de Regra          |
|---------------|-------------------------------|------------------------|
| wmclass match | `peek` (exact)                | —                      |
| Position      | `(screen_w - 360), 0`        | Force (SetRule = 4)    |
| Size          | `360 × screen_h`             | Force (SetRule = 4)    |
| Keep Above    | true                          | Force (ForceRule = 1)  |
| No Titlebar   | true                          | Force (ForceRule = 1)  |
| Skip Taskbar  | true                          | Force (ForceRule = 1)  |
| Skip Pager    | true                          | Force (ForceRule = 1)  |

**Matching:** `main.py` define `app.setDesktopFileName("peek")` + `os.environ["QT_WAYLAND_APP_ID"] = "peek"` → Wayland `app_id` = `"peek"`.

**Instalação:** `bash scripts/apply-kwin-rule.sh`  
**Remoção:** System Settings → Window Management → Window Rules → "PEEK Sidebar"

**Caveat:** A KWin Rule com `positionrule=4` (Force) impede animação de slide
via `QPropertyAnimation` no geometry (KWin sobrescreve a posição a cada frame).
A sidebar aparece/desaparece instantaneamente. Animação de slide pode ser
recuperada no futuro usando um container interno + clip animation.

### 3. Flags de Janela (Qt)

```python
Qt.WindowType.FramelessWindowHint    # Sem decoração
Qt.WindowType.Tool                   # Sem taskbar, sem alt-tab
Qt.WindowType.WindowStaysOnTopHint   # Acima de tudo
```

No Wayland/KDE, essas flags são "best effort". A KWin Rule é o que realmente
garante o comportamento. As flags servem como fallback para X11.

### 4. Warning Inofensivo do Portal

```
qt.qpa.services: Failed to register with host portal ... "App info not found for 'kslide'"
```

Acontece porque não existe um `.desktop` file instalado para "peek".
Não afeta o D-Bus nem o funcionamento. Resolvido quando criarmos o `.desktop` file.

### 5. MPRIS: Abordagem Híbrida de Leitura (Task #02–#02.2)

**Problema 1 (Polling vs. PropertiesChanged):** Monitorar propriedades dinâmicas
via o signal nativo `PropertiesChanged` é verboso e frágil no PySide6.

**Problema 2 (Descoberta de players, #02.1):** `QDBusInterface.call('ListNames')`
retornava `QStringList` que o PySide6 não convertia para `list[str]`.
Resolvido com `bus.interface().registeredServiceNames().value()`.

**Problema 3 (Metadata a{sv}, #02.2):** `Properties.Get` para Metadata retorna
`QDBusVariant` → `QDBusArgument` com signature `a{sv}`. O método `asVariant()`
do PySide6 **não funciona** para mapas read-only (retorna `None` com warning
"write from a read-only object"). Esse é um bug do binding Shiboken/PySide6.

**Solução atual (abordagem híbrida):**

| Propriedade       | Método de Leitura                              | Motivo                                    |
|-------------------|-------------------------------------------------|-------------------------------------------|
| `PlaybackStatus`  | `QDBusInterface.property()` → retorna `str`     | Tipo simples, PySide6 desembrulha sozinho |
| `Metadata`        | `busctl --user --json=short get-property` (subprocess) | PySide6 não desempacota `a{sv}`     |
| Controles         | `QDBusInterface.call("PlayPause")` etc.          | Métodos void, funcionam perfeitamente     |
| Descoberta        | `bus.interface().registeredServiceNames()`       | API canônica do PySide6                   |

**busctl** está disponível em todo sistema com systemd. Overhead: ~5ms por chamada
local, desprezível a cada 2s de polling.

### 6. Wayland app_id: QT_WAYLAND_APP_ID (Task #02.2)

**Problema:** No Qt6 Wayland, `app.setDesktopFileName("kslide")` nem sempre
propaga o `app_id` para o compositor. A KWin Rule (`wmclass=kslide`,
`skiptaskbar=true`) não fazia match, e a janela vazava para a taskbar.

**Solução:** `os.environ["QT_WAYLAND_APP_ID"] = "kslide"` no topo de `main.py`,
**antes** de instanciar `QApplication`. Isso força o plugin Wayland do Qt a
emitir o `app_id` correto no protocolo `wl_surface`.

### 7. MPRIS: D-Bus Signal Connection (Task #02.3)

**Problema:** `QDBusConnection.connect()` no PySide6 exige `self` como receiver
e `SLOT("method()")` como macro de string. O payload `a{sv}` do signal
`PropertiesChanged` é ignorado — o slot é decorado com `@Slot()` sem argumentos
e serve apenas como trigger para re-ler o estado via `_refresh_state()`.

**Solução:**
```python
self._bus.connect(service, path, iface, "PropertiesChanged", self, SLOT("_on_properties_changed()"))
```

### 8. Album Art: Download Assíncrono (Task #02.4)

**Abordagem:** `QNetworkAccessManager` (nativo do Qt, opera via event loop).
Nunca bloqueia a thread principal. Suporta `file://` (load direto) e `https://`
(GET assíncrono com redirects automáticos para CDN do Spotify).

| Componente | Responsabilidade |
|---|---|
| `services/art_downloader.py` | Recebe URL, baixa imagem, emite `art_ready(QPixmap)` |
| `ui/components/media_player.py` | Exibe QPixmap em QLabel 80×80 com cantos arredondados |
| `core/controller.py` | Conecta pipeline: `art_url_changed → fetch → art_ready → update_art` |

### 9. Auto-Hide: Cursor Guard Timer (Task #02.6)

**Problema:** No Wayland, quando o cursor encosta na borda da tela (topo/base),
o compositor não gera `leaveEvent` — o cursor fica clamped no limite do monitor.

**Solução:** 
- O app captura `mouseMoveEvent` (requer `setMouseTracking(True)`).
- Usa temporizador dinâmico. Retorna usando limites de janela corretos ajustados para Wayland.

### 10. **Refinamento de UI e Componentes**
- **Layout SidebarWindow**: QFrame único (`main_card`) englobando `Mixer` (esquerda) e `MediaPlayerWidget` (direita). Tamanho dinâmico `Shrink-to-Fit` no Wayland.
- **MediaPlayerWidget**: Min-width estendido para 300px. Espaçamentos (respiro vertical) de 12px entre a Seek Bar e os botões. O motor de controles (botões) foi encapsulado no padrão **Container Shield** (um QWidget isolado de `height: 35px` operando com `setSizePolicy(Expanding, Fixed)`), forçando a expansão horizontal total pelo painel. O layout interno dos botões delega o distanciamento físico puramente ao layout (`setSpacing(16)`) e usa um emparelhamento rígido de `addStretch(1)` nas pontas, mantendo o Play perfeitamente engastado no centro magnético.
- **Botões Achatados**: Botões de controle em formato de pílula restaurados (`border-radius: 14px;`) e alinhados via QSS com `min-width/max-width`. O ícone do botão de Play recebeu um `padding-left: 4px` no QSS para alinhamento ótico do triângulo.
- **Mixer Compacto**: Ícones 16x16 e margens reduzidas para otimizar espaço horizontal.
- **Volume Slider**: Dragging preciso com tracking `is_dragging()` para não pular enquanto o usuário manipula o volume e backend atualiza. Cores e contraste ajustados no QPainter nativo.
- **Seek Slider**: Barra horizontal com espessura animada (4px -> 14px) no hover. Altura física restaurada para `20px` para não asfixiar o QPainter, que renderiza nativamente (com cores dinâmicas) os timestamps **dentro** da barra quando expandida.
- **Sincronia Inicial**: O `Controller` agora força a UI a buscar os estados iniciais (`force_sync_ui`) diretamente do D-Bus após terminar as conexões de Signal, eliminando atrasos ("milissegundo zero").
- **MarqueeLabel**: Título da música substituído por um Widget customizado com efeito "Marquee" lento (1px/50ms) e pausas de 3s nas bordas (via `QPainter` e `QTimer`), lidando perfeitamente com títulos muito longos.

### 10. Auto-Hide: Cursor Guard Timer (Task #02.6)

**Problema:** No Wayland, quando o cursor encosta na borda da tela (topo/base),
o compositor não gera `leaveEvent` — o cursor fica clamped no limite do monitor.
Resultado: o painel não retrai quando o mouse sai pelo eixo Y.

**Solução:** QTimer de guarda (200ms) que verifica periodicamente se o widget sob
o cursor (`QApplication.widgetAt(QCursor.pos())`) é a nossa janela ou um de seus
filhos. O timer só roda enquanto o painel estiver visível e o mouse estiver
"logicamente dentro".

### 10. Volume: PulseAudio em Thread Dedicada (Task #03)

**Problema:** Operações do PulseAudio (via `pulsectl`) são bloqueantes, congelando a UI do Wayland e causando crash no cliente Wayland se a main thread demorar.

**Solução:** 
- `AudioClient` e `_AudioWorker`. O Worker roda em uma `QThread` separada, faz polling leve e emite sinais thread-safe.
- Slider customizado `VolumeSlider` refatorado como `QWidget` (Task #03.2) contendo ícones nativos dinâmicos (QIcon.fromTheme).
- Painel "Mixer de Áudio" instanciado num `QFrame` na `SidebarWindow` (Catppuccin Mocha), expondo o controle Master fixo à esquerda e renderizando dinamicamente controles para cada Sink Input (app).
- Sinal cruzado `app_volume_changed` da UI encaminha via `AudioClient.set_app_volume` com index do app.

**Detalhe importante:** `set_volume()` no slider usa `blockSignals(True)` para
evitar loop infinito (backend → UI → backend → ...).

### 11. **Preparação Open-Source (GitHub)**
O projeto PEEK recebeu seu framework inicial de documentação. Foram criados:
- `requirements.txt`: Isolando `PySide6` e `pulsectl`.
- `README.md`: Definindo os escopos experimentais (Wayland/KDE), filosofia "Zero Bloat" e "Progressive Disclosure", além de um panorama claro da Tech Stack (Python, Qt6, D-Bus, Multithreading de Áudio) e instruções de execução local.

### 12. Lazy Initialization do Daemon (Task #33)

**Problema:** No boot com `--daemon`, o `Controller` instanciava imediatamente a `SidebarWindow`, a janela âncora (`_anchor`), o `MprisClient` (com timer de redescoberta de 2s), o `ArtDownloader` (`QNetworkAccessManager`) e o `AudioClient` (com `QThread` dedicada + conexão `pulsectl` fazendo polling a cada 500ms), consumindo RAM e ciclos de CPU antes mesmo da primeira abertura.

**Solução:**
- **`Controller.__init__` minimalista:** No boot (`--daemon`), apenas `QApplication`, `QDBusConnection` (`org.peek.App`), `_hide_timer` e `_survival_timer` são criados; `_anchor`, `_sidebar`, `_mpris`, `_art_downloader` e `_audio` iniciam como `None` (com imports também postergados para `_ensure_initialized()`, e `ControlCenter` importado apenas no processo GUI em `main.py`).
- **Inicialização sob demanda (`_ensure_initialized()`):** Executada uma única vez na primeira chamada ativa de `Toggle()` ou `SlideIn()`. Constrói `_anchor` e `SidebarWindow`, inicia `MprisClient`, `ArtDownloader` e `AudioClient` (e sua `QThread`), conecta todos os sinais via `_connect_signals()` e executa `force_sync_ui()` antes de exibir o painel.
- **Persistência após primeira abertura:** Após inicializados, a sidebar e os workers permanecem vivos em memória nos `slide_out()` subsequentes (sem destruir/recriar widgets ou threads).
- **Segurança pré-inicialização:** `SlideOut()`, `SetServiceActive(False)` e `_do_slide_out()` validam `self._sidebar is not None` para evitar `AttributeError` e evitar inicializar a UI sem necessidade antes do primeiro `Toggle`.

### 13. UX/UI Bugfixes: Hidden Layout Sync, Int64 Seek, Left Hover Margin & Native Click-Through (Task #34)

- **Sincronização de Layout Oculto (Right-Anchor Fix):** Quando sliders dinâmicos de app são removidos enquanto `SidebarWindow` está oculta, Qt suprime a propagação de `updateGeometry()` e mantém `sizeHint()`/`SetFixedSize` presos à largura antiga. `update_app_sliders()` agora oculta e desanexa (`hide()`, `setParent(None)`) sliders removidos e chama `_sync_layout_geometry()` (`updateGeometry()` + `layout().activate()` no `main_card` e na janela), também executado no início de `slide_in()`, além de `_reanchor_right()` ao fim da animação para capturar mudanças ocorridas durante o slide.
- **Seek Bar Int64 & Formatação Longa:** `MprisClient.position_changed` e `duration_changed` (além dos slots `MediaPlayerWidget.update_position` e `update_duration`) migraram de `int` (32-bit, overflow em `2,147,483,647 µs` ≈ 35m 47s) para `"qlonglong"` (64-bit). `_format_time()` ganhou clamp `max(0, seconds)` e suporte a `H:MM:SS` para mídias >= 1h.
- **Margem de Tolerância Esquerda (`LEFT_HOVER_MARGIN = 20`):** Adicionados 20px transparentes à esquerda de `main_card` via `main_layout.setContentsMargins(20, 0, 0, 0)` (mantendo margem direita 0px) para evitar `leaveEvent` imediato em pequenos desvios do cursor sem deslocar `main_card` da borda direita. `HIDE_DELAY_MS` ajustado para `350ms`.
- **Click-Through Nativo Acima/Abaixo (`TOP_OFFSET = 24`):** Em vez de `setMask()` (frágil para input region e recálculo dinâmico de sliders no KDE Plasma 6 Wayland), as margens superior/inferior do `main_layout` foram zeradas e `_offscreen_rect()`/`_onscreen_rect()` deslocam `y` por `TOP_OFFSET = 24`. Assim a `wl_surface` tem exatamente a altura do `main_card`, deixando as áreas acima e abaixo 100% click-through nativamente.

### 14. Fase 3 Subtle Menus, Config Persistence, TopRight/TopLeft & Magic Gradient (Task #35)

- **Configuração Persistente & Sync D-Bus (`core/config.py`):** Salva `~/.config/peek/config.json` atomicamente (`primary_color`, `bg_color`, `bg_opacity` 30–100%, `trigger_edge` `"TopRight"` ou `"TopLeft"`). `Controller` expõe `ReloadConfig()`, `PreviewAppearance(bg, primary, opacity)` e `IsServiceActive()` via `org.peek.App` para sincronizar `peek-gui` com `peek-daemon` em tempo real.
- **Fase 3 — Subtle Menus (`QMenu.popup()` + Wayland Guardrail):**
  - **Saída / Master (`_speaker_icon` + Master `VolumeSlider`):** Lista sinks cacheados no `_AudioWorker` e permite selecionar saída padrão via `pulsectl` efêmero (movendo streams ativos), além de atalhos de mute Master/Mic. Left-click no ícone Master também aciona `toggle_master_mute()`.
  - **Microfone (`_mic_icon`):** Lista sources não-monitor cacheadas e alterna entrada padrão + mute.
  - **Media Player (`_section_player` click ou right-click no `MediaPlayerWidget`):** Lista players MPRIS via `MprisClient.list_players()` e fixa `_preferred_service` via `select_player()`.
  - **Guarda Anti-Auto-Hide no Wayland:** Menus usam `QMenu.popup()` (`WA_DeleteOnClose`) com contador `_open_menus` (`aboutToShow` / `aboutToHide`), suprimindo `mouse_left` em `leaveEvent()` e travando `_hide_timer`/`_survival_timer` no `Controller` enquanto qualquer menu está aberto.
- **Borda Configurável (Cantos Superiores `TopRight` = 1 / `TopLeft` = 7):** `kwin-script/kslide-edge-trigger` lê `TriggerEdge` via `contents/config/main.xml` + `readConfig("TriggerEdge", 1)` e atualiza via `kwriteconfig6` + `reconfigure`. No `TopLeft`, `SidebarWindow` espelha horizontalmente a âncora (`x = 0`), a margem de tolerância (`RIGHT_HOVER_MARGIN = 20`) e os raios/borda do `main_card` sem reordenar os widgets internos.
- **Magic Gradient:** `SidebarWindow.apply_appearance()` aplica `qlineargradient` vertical/diagonal com paradas `rgba(...)` controladas por `bg_opacity`, borda sutil translúcida e cor de destaque sincronizada nos botões e sliders.

### 15. Control Center Sizing, Áudio Balanço + Mono/Estéreo & Fix Pós-Menu Wayland (Task #36)

- **Control Center Redimensionável e Sem Scroll Horizontal:** Removidos `setMaximumWidth(560)` e `setFixedHeight(650)`; janela abre com largura ajustada ao conteúdo (`min 600px`, redimensionável `540×500`+), `ScrollBarAlwaysOff` horizontal, `wordWrap` nas descrições e `AdjustToContents` no combo de borda.
- **Menu de Saída Focado + Balanço L/R & Mono/Estéreo:** Removida a opção de mutar microfone do menu de saída de áudio (permanece apenas no menu/ícone do Mic). Adicionados slider inline de **Balanço L/R** (`QWidgetAction` com clique no rótulo para centralizar, preservando pico e balanço ao ajustar Master volume) e toggle **Áudio Mono (Downmix)** via `module-remap-sink` (`peek_mono_sink`) usando `pulsectl` efêmero.
- **Fix de Auto-Hide Pós-QMenu no Wayland:** Ao fechar um `QMenu.popup()` clicando fora no Wayland, o Qt dispara `enterEvent` sintético imediato que cancelava o `_hide_timer`. `SidebarWindow` agora suprime `enterEvent` nos `150ms` após fechar o menu (`_menu_closed_at`) e usa `HoverMove` (`WA_Hover`) apenas quando há movimento real sobre o painel.
- **Reload Dinâmico do KWin Script via `busctl`:** Como Fedora/Nobara fornece `busctl`/`qdbus-qt6` (e não `qdbus6`), a troca de canto de ativação e `install-kwin-script.sh` usam `busctl --user call org.kde.KWin /Scripting org.kde.kwin.Scripting unloadScript` seguido de `reconfigure` para desregistrar imediatamente a borda antiga.

---

## Componentes e Sinais

| Componente | Sinais Emitidos | Slots Expostos |
|---|---|---|
| `MprisClient` | `track_changed(str,str)`, `playback_state_changed(bool)`, `art_url_changed(str)`, `player_name_changed(str)`, `position_changed(qlonglong)`, `duration_changed(qlonglong)` | `play_pause()`, `next_track()`, `previous_track()`, `select_player(str)` |
| `ArtDownloader` | `art_ready(QPixmap)`, `art_cleared()` | `fetch(str)` |
| `AudioClient` | `master_volume_changed(int)`, `master_mute_changed(bool)`, `app_volumes_changed(list)`, `mic_mute_changed(bool)` | `set_master_volume(int)`, `toggle_master_mute()`, `set_app_volume(int, int)`, `toggle_stream_mute(int)`, `toggle_mic_mute()`, `cycle_default_sink()`, `set_default_sink(str)`, `set_default_source(str)`, `set_master_balance(float)`, `set_mono_mode(bool)` |
| `MediaPlayerWidget` | `play_pause_clicked()`, `next_clicked()`, `previous_clicked()`, `seek_requested(int)`, `player_menu_requested(QPoint)` | `update_track(str,str)`, `update_playback_state(bool)`, `update_art(QPixmap)`, `clear_art()`, `update_player_name(str)`, `update_position(qlonglong)`, `update_duration(qlonglong)` |
| `VolumeSlider` | `volume_changed(int)`, `mute_toggled(int)`, `master_mute_toggled()`, `context_menu_requested(QPoint)` | `set_volume(int)` |
| `SidebarWindow` | `mouse_entered()`, `mouse_left()`, `menu_opened()`, `menu_closed()`, `app_volume_changed(int, int)`, `master_mute_requested()`, `mic_mute_requested()`, `output_menu_requested(QPoint)`, `mic_menu_requested(QPoint)`, `player_menu_requested(QPoint)`, `sink_selected(str)`, `source_selected(str)`, `player_selected(str)`, `balance_changed(float)`, `mono_toggled(bool)` | `slide_in()`, `slide_out()`, `update_app_sliders(list)` |

---

## Status das Tasks

| Task   | Descrição                              | Status |
|--------|----------------------------------------|--------|
| #01    | Esqueleto visual + mecânica de slide   | ✅     |
| #01.5  | Migração: KWin Script + D-Bus          | ✅     |
| #01.6  | Fix posicionamento via KWin Rules      | ✅     |
| #01.7  | Documentação de estado                 | ✅     |
| #01.8  | TopRight Edge & Roadmap                | ✅     |
| #02    | Controle de mídia (MPRIS/Spotify)      | ✅     |
| #02.1  | Bugfix: MPRIS discovery + window flags | ✅     |
| #02.2  | Bugfix: Metadata parsing + app_id      | ✅     |
| #02.3  | Bugfix: D-Bus signal + Wayland Tool    | ✅     |
| #02.4  | Album art (download assíncrono)        | ✅     |
| #02.5  | UI refactoring (layout compacto)       | ✅     |
| #02.6  | Bugfix: auto-hide cursor guard         | ✅     |
| #03    | Controle de volume (Master slider)     | ✅     |
| #14    | Rebranding global: KSlide → PEEK       | ✅     |
| #16    | Bug Smash: assimetria/overlap botões   | ✅     |
| #16.2  | Debug visual (bordas coloridas)        | ✅     |
| #16.3  | Correção definitiva: geometria botões  | ✅     |
| #16.4  | Ajuste fino: margens → botões imponentes | ✅   |
| #17    | Bug Smash: Seek bar — diagnóstico e infra | ✅  |
| #17.3  | Correção definitiva: MPRIS seek Int64 ox | ✅  |
| #18    | Mega Update: Logger + Survival Timer + Control Center | ✅ |
| #18.1  | Control Center Visibility & Single Instance (D-Bus Fix) | ✅ |
| #18.2  | UX Polish: Wayland transient fix, toggle funcional, :pressed CSS | ✅ |
| #18.3  | Wayland geometry fix: setTransientParent(None) + centralização | ✅ |
| #18.4  | Early Severing: WA_NativeWindow + QGuiApplication.primaryScreen | ✅ |
| #18.5  | Architecture Pivot: Multi-Process Wayland Fix (Daemon & GUI) | ✅ |
| #18.6  | Wayland App ID Isolation & KWin Bypass (QTimer Move) | ✅ |
| #18.7  | Wayland Absolute Positioning (Qt.ToolTip) & Debug Cleanup | ✅ |
| #18.8  | Autonomous Wayland Fix: Transient Parent Anchor (1x1 Qt.Tool) | ✅ |
| #18.9  | Phantom Anchor Full-Screen Geometry Match | ✅ |
| #18.10 | Smart Logging & Pre-Phase 3 Polish | ✅ |
| #18.11 | Final Pre-Commit Code Audit (imports, prints) | ✅ |
| #19.1  | UI Polish: Slide Switch, LED Diagnóstico, Log Colapsável, v0.2 | ✅ |
| #19.2  | Custom Paint SlideSwitch + Exorcismo Definitivo da Âncora | ✅ |
| #19.3  | Shrink Bug Fix, LED Status Minimalista, Ghost Hunting (desktopFileName="") | ✅ |
| #19.4  | Brute Force Fixes: SetFixedSize + SplashScreen Protocol | ✅ |
| #19.5  | Desktop Entry Bypass: peek-daemon-ghost.desktop (NoDisplay=true) | ✅ |
| #19.6  | KDE Taskbar Clone Wars Fix: X-KDE-SkipTaskbar=true in .desktop | ✅ |
| #19.7  | Revert 1x1 Anchor Disaster & Plasmashell Spoofing | ✅ |
| #19.8  | The KISS Principle: Dynamic Show/Hide Anchor Sync | ✅ |
| #20    | XDG Autostart, --daemon flag & Shortcut UI ("Configurar no KDE") | ✅ |
| #20.1  | Native KDE Shortcut: FreeDesktop Desktop Actions (.desktop file + kbuildsycoca6) | ✅ |
| #21    | Dynamic Width Right-Anchor Fix (resizeEvent + sizeHint width) | ✅ |
| #21.1  | Ghost Gap & Edge Trigger Fix (SetFixedSize + parent_pos offset) | ✅ |
| #22    | Audio Interactivity: ClickableIcon, App Mute Toggle, Global Controls (Mic+Speaker) | ✅ |
| #22.1  | Wire Up Native PulseAudio Mute (toggle_stream_mute + toggle_mic_mute) | ✅ |
| #22.2  | Visual Mute Feedback (QGraphicsOpacityEffect + mic icon swap + speaker icon fix) | ✅ |
| #22.3  | Bugfix: MPRIS Regression (Int64 busctl) & Optimistic UI for Mute & Tooltips | ✅ |
| #22.4  | Bugfix: UI Wiring, repaint() injection & Controller check | ✅ |
| #23    | Workspace Cleanup & Audio Routing (cycle_default_sink) | ✅ |
| #24    | Universal MPRIS Support (Dynamic Discovery & Identity Extraction) | ✅ |
| #24.1  | Bugfix: DBus Disconnect Signature & UI Sync | ✅ |
| #25    | Clickable Player Header & Chevron | ✅ |
| #25.1  | UI Polish & MPRIS Extrapolation (No Polling) | ✅ |
| #25.2  | Pixel Perfect: Layout Alignment & Spacing | ✅ |
| #26.1  | Investigation Failed: Fix UI Paint Event & Volume Sync | ✅ |
| #26.2  | Fix Your Lie: Implement get_stream_name | ✅ |
| #26.4  | Implementation: Fix Snapback & Double-Seek | ✅ |
| #26.5  | Fix Your Mess: Restore QSlider Mouse Tracking | ✅ |
| #26.7  | Implementation: Manual Mouse Tracking Override | ✅ |
| #27    | Control Center UI Polish & Theme Skeleton | ✅ |
| #27.1  | UI Fix: Scroll Area & True Color Customization | ✅ |
| #28    | Professional Color Picker UI | ✅ |
| #29    | File Consolidation, Log Fix & Live Theme Engine | ✅ |
| #30    | UI Declutter & Unified Color Buttons | ✅ |
| #31    | Fix Qt Border Bevel Glitch (The Dent) | ✅ |
| #32    | Bump Version to v0.4 & Project Inventory | ✅ |
| #33    | Lazy UI & Worker Initialization for Daemon Idle RAM Reduction | ✅ |
| #34    | UX/UI Bugfixes: Hidden Layout Sync, Int64 Seek, Left Hover Margin & Native Click-Through | ✅ |
| #35    | Fase 3 Subtle Menus, Config Persistence, Opacity, TopRight/TopLeft & Magic Gradient | ✅ |
| #36    | Control Center Resizable/Fit, Output Menu Balance + Mono/Stereo & Wayland Post-Menu Fix | ✅ |

---

> **Nota de Transição (Fase 3):** As otimizações estruturais do Wayland (Multi-Processo, IPC D-Bus e Âncora Transiente) e a Fase 3 (Subtle Menus de contexto com guarda Wayland, persistência em `config.json`, transparência, borda TopRight/TopLeft e Magic Gradient) foram concluídas com sucesso.

## Backlog Futuro (Adiado / Postponed)

- **Sincronização dinâmica de cor de destaque do KDE Plasma** — *Adiado* (Control Center já possui toggle placeholder; sync automático com `kdeglobals` ficará para versão futura).
- **Gravação in-app de atalho global** — *Adiado* (Wayland exige KGlobalAccel / GlobalShortcuts Portal; integração atual via Desktop Action + `kcmshell6 keys` atende o fluxo nativo).
- **Empacotamento (`.kwinscript`, `Makefile`, `.rpm`)** — *Adiado*.

---

## Dicas de UX Nativa

**Para remover o brilho azul (glow) da borda no Plasma:**
Vá em System Settings → Workspace Behavior → Screen Edges → Desmarque "Remain active when windows are fullscreen" (se aplicável, ou desative o Edge Highlight nas configurações do tema visual) para garantir que a sidebar pareça 100% nativa sem o glow do KDE.

---

## Setup para Desenvolvimento

```bash
# 1. Instalar dependências
python3 -m pip install --user PySide6 pulsectl

# 2. Instalar KWin Script (detecção de borda)
bash scripts/install-kwin-script.sh

# 3. Aplicar KWin Rule (posicionamento)
bash scripts/apply-kwin-rule.sh

# 4. Rodar
python3 main.py

# 5. Testar manualmente
qdbus org.peek.App /App org.peek.App.Toggle
```
