"""Downloader assíncrono de capa de álbum.

Usa QNetworkAccessManager para baixar a imagem da capa de álbum
informada pelo MPRIS (mpris:artUrl). O QNAM é nativo do Qt e opera
via event loop — NUNCA bloqueia a thread principal.

Fluxo:
    1. MprisClient emite art_url_changed(url).
    2. Controller repassa a URL para ArtDownloader.fetch(url).
    3. ArtDownloader verifica:
       - Se a URL é file:// → carrega direto do disco (QPixmap.load).
       - Se a URL é https:// → faz GET assíncrono via QNAM.
    4. Ao completar, emite art_ready(QPixmap).
    5. MediaPlayerWidget recebe o QPixmap e exibe no QLabel.

Cache simples: se a URL não mudou, não re-baixa.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


class ArtDownloader(QObject):
    """Baixa a capa do álbum de forma assíncrona.

    Sinais:
        art_ready(QPixmap) — emitido quando a imagem foi baixada e decodificada.
        art_cleared()      — emitido quando não há capa (URL vazia).
    """

    art_ready = Signal(QPixmap)
    art_cleared = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        self._nam = QNetworkAccessManager(self)
        self._nam.finished.connect(self._on_download_finished)

        self._current_url: str = ""
        self._pending_reply: QNetworkReply | None = None

    # ── API pública ──────────────────────────────────────────────────

    @Slot(str)
    def fetch(self, url: str) -> None:
        """Inicia o download da capa se a URL mudou.

        Args:
            url: URL da capa (https://, file://, ou string vazia).
        """
        # Mesma URL → nada a fazer
        if url == self._current_url:
            return

        self._current_url = url

        # Cancela download anterior se ainda estiver em andamento
        if self._pending_reply is not None:
            self._pending_reply.abort()
            self._pending_reply.deleteLater()
            self._pending_reply = None

        # URL vazia → limpa a capa
        if not url:
            self.art_cleared.emit()
            return

        qurl = QUrl(url)

        # file:// → carrega direto do disco (sem rede)
        if qurl.isLocalFile():
            pixmap = QPixmap(qurl.toLocalFile())
            if pixmap.isNull():
                print(f"[PEEK:Art] Falha ao carregar arquivo local: {qurl.toLocalFile()}")
                self.art_cleared.emit()
            else:
                self.art_ready.emit(pixmap)
            return

        # https:// (ou http://) → download assíncrono via QNAM
        request = QNetworkRequest(qurl)
        request.setMaximumRedirectsAllowed(5)
        # Segue redirects automaticamente (Spotify usa CDN com redirects)
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        self._pending_reply = self._nam.get(request)

    # ── Callback do QNAM ─────────────────────────────────────────────

    @Slot(QNetworkReply)
    def _on_download_finished(self, reply: QNetworkReply) -> None:
        """Chamado quando o download da imagem termina."""
        # Ignora respostas de requests antigos (URL já mudou)
        if reply is not self._pending_reply:
            reply.deleteLater()
            return

        self._pending_reply = None

        if reply.error() != QNetworkReply.NetworkError.NoError:
            print(f"[PEEK:Art] Erro no download: {reply.errorString()}")
            reply.deleteLater()
            self.art_cleared.emit()
            return

        data = reply.readAll()
        reply.deleteLater()

        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            print("[PEEK:Art] Falha ao decodificar imagem da rede.")
            self.art_cleared.emit()
            return

        self.art_ready.emit(pixmap)
