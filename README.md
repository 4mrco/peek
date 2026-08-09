# PEEK (KSlide)

> [!WARNING]
> **Disclaimer:** Este é um projeto experimental projetado especificamente para o ecossistema Wayland e KDE Plasma. Ele faz uso ostensivo de chamadas nativas de D-Bus e integração direta com o PulseAudio. Compatibilidade com X11 ou outros compositores/ambientes não é garantida e pode apresentar comportamentos inesperados.

PEEK (anteriormente KSlide) é um painel lateral dinâmico de controle de mídia e volume, focado em minimalismo e eficiência máxima. 

## Filosofia do Projeto

- **Zero Bloat**: Foco estrito em controle de mídia e áudio. Sem funcionalidades infladas ou integrações desnecessárias. O painel deve ser uma ferramenta afiada e não um canivete suíço.
- **Progressive Disclosure**: A interface segue o princípio de que a complexidade deve ser revelada sob demanda. Ações primárias e imediatas ocorrem no *Left Click*, enquanto a profundidade e ajustes finos ficam escondidos no *Right Click* ou *Hover*.

## Tech Stack

O PEEK é construído sobre uma base robusta que prioriza responsividade e integração nativa com o sistema:
- **Core**: Python 3 operando como a cola lógica.
- **UI Engine**: Qt6 via `PySide6` (C++ por baixo dos panos), permitindo renderização nativa complexa (QPainter), layouts fluidos e estilização via QSS.
- **Áudio**: Multithreading nativo integrado ao PulseAudio (`pulsectl`) para garantir operações I/O sem bloquear a thread principal da UI.
- **Mídia**: Integração híbrida MPRIS, capturando o estado real de reprodutores como Spotify, VLC e browsers via signals D-Bus, complementada por chamadas ao `busctl`.

## Instalação e Uso

### Dependências do Sistema

Certifique-se de que o seu sistema Linux possui os seguintes pacotes essenciais:
- Python 3.10+
- `libpulse` (Geralmente incluído no PulseAudio/PipeWire-Pulse)
- `systemd` (Necessário para a CLI `busctl` usada na extração de metadata do MPRIS)

### Configuração do Ambiente Python

Recomendamos o uso de um ambiente virtual para isolar as dependências do PEEK:

```bash
# Clone o repositório
git clone https://github.com/4mrco/peek.git
cd peek

# Crie e ative o ambiente virtual
python3 -m venv .venv
source .venv/bin/activate

# Instale as dependências
pip install -r requirements.txt
```

### Execução

Inicie o PEEK chamando o script principal:

```bash
python3 main.py
```

O aplicativo será registrado no D-Bus como `org.peek.App` operando na interface `/App`. Ele pode ser convocado na tela por chamadas D-Bus associadas aos *Edge Triggers* ou *Shortcuts* do KDE Plasma.
