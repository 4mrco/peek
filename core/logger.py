"""Redirecionador de logs para arquivo físico.

Ao chamar setup_logging(), todo print() e erro do Python vão para
~/.local/share/peek/peek.log (sobrescrito a cada sessão — sem acúmulo de lixo).

O log_path é exposto como constante pública para que o Control Center
possa abri-lo na UI de diagnóstico.
"""

from __future__ import annotations

import sys
from pathlib import Path


# Caminho canônico do arquivo de log
LOG_DIR: Path = Path.home() / ".local" / "share" / "peek"
LOG_PATH: Path = LOG_DIR / "peek.log"


class _Tee:
    """Espelha escrita para dois file-like objects (original + arquivo)."""

    def __init__(self, original, file_obj) -> None:
        self._original = original
        self._file = file_obj

    def write(self, data: str) -> int:
        self._file.write(data)
        self._file.flush()
        return len(data)

    def flush(self) -> None:
        self._file.flush()

    def fileno(self):
        return self._original.fileno()


def setup_logging(is_daemon: bool = True) -> Path:
    """Redireciona stdout e stderr para LOG_PATH.

    Parameters
    ----------
    is_daemon : bool
        Se True, usa mode='w' para limpar o log anterior. Se False, usa mode='a'.

    Returns
    -------
    Path
        Caminho do arquivo de log criado.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    mode = "w" if is_daemon else "a"
    log_file = open(LOG_PATH, mode, encoding="utf-8", buffering=1)  # noqa: SIM115

    sys.stdout = _Tee(sys.__stdout__, log_file)
    sys.stderr = _Tee(sys.__stderr__, log_file)

    return LOG_PATH
