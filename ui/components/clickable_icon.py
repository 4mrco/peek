"""Componentes reutilizáveis de UI do PEEK.

Contém:
  - ClickableIcon: QLabel clicável com sinais leftClicked / rightClicked.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QEnterEvent, QMouseEvent
from PySide6.QtWidgets import QLabel, QWidget


class ClickableIcon(QLabel):
    """QLabel que emite sinais ao ser clicado com o botão esquerdo ou direito.

    Uso::

        icon = ClickableIcon(parent)
        icon.leftClicked.connect(on_left_click)
        icon.rightClicked.connect(on_right_click)
    """

    leftClicked  = Signal()
    rightClicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.leftClicked.emit()
        elif event.button() == Qt.MouseButton.RightButton:
            self.rightClicked.emit()
        super().mousePressEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        self.setStyleSheet("QLabel { opacity: 0.7; }")
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet("")
        super().leaveEvent(event)


class ClickableLabel(QLabel):
    """QLabel que emite sinais ao ser clicado com o botão esquerdo ou direito.
    Muda a aparência de opacidade no hover.
    """

    leftClicked  = Signal()
    rightClicked = Signal()

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.leftClicked.emit()
        elif event.button() == Qt.MouseButton.RightButton:
            self.rightClicked.emit()
        super().mousePressEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        # Diminui opacidade usando QGraphicsOpacityEffect ou style? O label de texto
        # reage melhor à propriedade de color se estivéssemos estilizando texto, mas
        # opacity no stylesheet funciona nas versões mais recentes.
        self.setStyleSheet("QLabel { opacity: 0.7; }")
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.setStyleSheet("")
        super().leaveEvent(event)
