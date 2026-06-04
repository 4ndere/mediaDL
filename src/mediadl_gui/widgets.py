from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class DropFrame(QFrame):
    file_dropped = Signal(str)
    clicked = Signal()

    def __init__(self, title: str) -> None:
        super().__init__()
        self.setObjectName("DropFrame")
        self.setAcceptDrops(True)
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(6)

        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setObjectName("DropIcon")

        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setObjectName("DropTitle")

        self.sub_label = QLabel("")
        self.sub_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_label.setObjectName("DropSub")

        v.addWidget(self.icon_label)
        v.addWidget(self.title_label)
        v.addWidget(self.sub_label)

    def set_icon(self, icon: QIcon) -> None:
        pix = icon.pixmap(48, 48)
        self.icon_label.setPixmap(pix)

    def set_subtitle(self, text: str) -> None:
        self.sub_label.setText(text)

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls:
            p = urls[0].toLocalFile()
            if p:
                self.file_dropped.emit(p)
        event.acceptProposedAction()

