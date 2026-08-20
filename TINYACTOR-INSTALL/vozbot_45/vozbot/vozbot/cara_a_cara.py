"""Comparador de dos audios, para elegir sin oírlos.

Captura lo que suena por el cable virtual mientras se reproduce cada opción,
y lo dibuja: la forma de onda, los tramos de habla y las medidas de ambos,
lado a lado. La elección la haces tú, con la información delante.

    python -m vozbot.cara_a_cara
"""
from __future__ import annotations

import sys
import threading
import time

import numpy as np
import sounddevice as sd
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .analisis import Analisis, analizar, comparar

ESTILO = """
QWidget { background:#12141a; color:#e9e5dc; font-family:'Segoe UI',system-ui,sans-serif; font-size:13px; }
QLabel#titulo { color:#7d8496; font-size:11px; letter-spacing:.10em; text-transform:uppercase; }
QLabel#opcion { font-size:19px; font-weight:700; }
QLabel#nota   { color:#5eead4; font-size:12px; }
QPushButton { background:#1d212b; border:1px solid #333949; border-radius:8px; padding:10px 16px; }
QPushButton:hover { border-color:#5eead4; }
QPushButton#principal { background:#5eead4; color:#0b0d12; font-weight:700; border:none; padding:12px 20px; }
QComboBox { background:#0b0d12; border:1px solid #2b303c; border-radius:6px; padding:6px; }
"""


class Onda(QWidget):
    """Dibuja la envolvente y marca cada tramo de habla."""

    def __init__(self, color: str = "#5eead4"):
        super().__init__()
        self.analisis: Analisis | None = None
        self.color = QColor(color)
        self.setMinimumHeight(150)

    def mostrar(self, a: Analisis) -> None:
        self.analisis = a
        self.update()

    def paintEvent(self, evento) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        ancho, alto = self.width(), self.height()
        p.fillRect(0, 0, ancho, alto, QColor("#0b0d12"))

        a = self.analisis
        if not a or not len(a.envolvente):
            p.setPen(QColor("#3a4050"))
            p.drawText(self.rect(), Qt.AlignCenter, "sin audio")
            return

        medio = alto // 2
        piso = -60.0                       # dB que se dibujan como silencio

        # tramos de habla, en bandas de fondo
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.color.red(), self.color.green(), self.color.blue(), 28))
        for s in a.segmentos:
            x1 = int(s.inicio / max(a.duracion, 0.01) * ancho)
            x2 = int(s.fin / max(a.duracion, 0.01) * ancho)
            p.drawRect(x1, 8, max(2, x2 - x1), alto - 16)

        # envolvente reflejada, como una forma de onda
        p.setPen(QPen(self.color, 1))
        n = len(a.envolvente)
        for i, db in enumerate(a.envolvente):
            x = int(i / n * ancho)
            h = int(max(0.0, (db - piso) / -piso) * (alto / 2 - 10))
            p.drawLine(x, medio - h, x, medio + h)

        # numeración de los tramos, para poder señalarlos al hablar de ellos
        p.setPen(QColor("#7d8496"))
        p.setFont(QFont("Segoe UI", 8))
        for i, s in enumerate(a.segmentos, 1):
            x = int((s.inicio + s.duracion / 2) / max(a.duracion, 0.01) * ancho)
            p.drawText(x - 6, alto - 4, str(i))


class Capturador(QThread):
    """Graba del cable mientras suena cada opción."""

    listo = Signal(int, object, int)

    def __init__(self, dispositivo: int | None, segundos: float, cual: int):
        super().__init__()
        self.dispositivo = dispositivo
        self.segundos = segundos
        self.cual = cual

    def run(self) -> None:
        sr = 48000
        trozos: list[np.ndarray] = []
        try:
            with sd.InputStream(
                samplerate=sr, device=self.dispositivo, channels=1, dtype="float32",
                callback=lambda d, f, t, e: trozos.append(d.copy()),
            ):
                time.sleep(self.segundos)
        except Exception as e:
            print(f"No pude capturar: {e}")
        audio = np.concatenate(trozos).reshape(-1) if trozos else np.zeros(1, "float32")
        self.listo.emit(self.cual, audio, sr)


class Ventana(QWidget):
    def __init__(self):
        super().__init__()
        self.analisis: dict[int, Analisis] = {}
        self.hilo: Capturador | None = None
        self.setWindowTitle("Cara a cara — comparar dos audios")
        self.resize(1000, 720)
        self.setStyleSheet(ESTILO)
        self._construir()

    def _construir(self) -> None:
        raiz = QVBoxLayout(self)

        # --- barra superior ---
        barra = QHBoxLayout()
        self.entrada = QComboBox()
        self.entrada.addItem("— dispositivo de captura —", None)
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                self.entrada.addItem(f"{i}: {d['name']}", i)
                if "cable" in d["name"].lower():
                    self.entrada.setCurrentIndex(self.entrada.count() - 1)
        self.segundos = QComboBox()
        self.segundos.addItems(["4", "6", "8", "10", "15"])
        self.segundos.setCurrentText("8")

        b_a = QPushButton("Capturar A")
        b_a.setObjectName("principal")
        b_a.clicked.connect(lambda: self.capturar(0))
        b_b = QPushButton("Capturar B")
        b_b.setObjectName("principal")
        b_b.clicked.connect(lambda: self.capturar(1))

        barra.addWidget(QLabel("Escuchar por"))
        barra.addWidget(self.entrada, 1)
        barra.addWidget(QLabel("Segundos"))
        barra.addWidget(self.segundos)
        barra.addWidget(b_a)
        barra.addWidget(b_b)
        cont = QWidget()
        cont.setLayout(barra)
        raiz.addWidget(cont)

        self.estado = QLabel("Pulsa «Capturar A», y en el juego dale al play de la opción A.")
        self.estado.setObjectName("nota")
        raiz.addWidget(self.estado)

        # --- las dos ondas ---
        for i, (nombre, color) in enumerate([("Opción A", "#5eead4"), ("Opción B", "#f0a441")]):
            etiqueta = QLabel(nombre)
            etiqueta.setObjectName("opcion")
            etiqueta.setStyleSheet(f"color:{color}")
            raiz.addWidget(etiqueta)
            onda = Onda(color)
            raiz.addWidget(onda)
            setattr(self, f"onda_{i}", onda)

        # --- tabla comparativa ---
        t = QLabel("Comparación")
        t.setObjectName("titulo")
        raiz.addWidget(t)
        self.tabla = QGridLayout()
        self.tabla.setSpacing(4)
        cont_t = QWidget()
        cont_t.setLayout(self.tabla)
        raiz.addWidget(cont_t)

    def capturar(self, cual: int) -> None:
        if self.hilo and self.hilo.isRunning():
            return
        letra = "A" if cual == 0 else "B"
        self.estado.setText(f"Grabando {self.segundos.currentText()}s… dale al play de {letra} AHORA")
        self.hilo = Capturador(
            self.entrada.currentData(), float(self.segundos.currentText()), cual
        )
        self.hilo.listo.connect(self._analizado)
        self.hilo.start()

    def _analizado(self, cual: int, audio, sr: int) -> None:
        a = analizar(audio, sr)
        self.analisis[cual] = a
        getattr(self, f"onda_{cual}").mostrar(a)
        letra = "A" if cual == 0 else "B"

        if len(a.segmentos) == 0:
            self.estado.setText(
                f"{letra}: no capté nada. ¿Es el dispositivo correcto? ¿Sonó el audio?"
            )
        else:
            self.estado.setText(
                f"{letra} listo: {len(a.segmentos)} tramos, {a.duracion:.1f}s. "
                + ("Ahora captura la otra." if len(self.analisis) < 2 else "Compara abajo y elige.")
            )
        if len(self.analisis) == 2:
            self._pintar_tabla()

    def _pintar_tabla(self) -> None:
        while self.tabla.count():
            w = self.tabla.takeAt(0).widget()
            if w:
                w.deleteLater()

        cabecera = ["Medida", "A", "B", "Diferencia"]
        for c, texto in enumerate(cabecera):
            l = QLabel(texto)
            l.setObjectName("titulo")
            self.tabla.addWidget(l, 0, c)

        for f, (medida, va, vb, nota) in enumerate(
            comparar(self.analisis[0], self.analisis[1]), start=1
        ):
            self.tabla.addWidget(QLabel(medida), f, 0)
            la, lb = QLabel(va), QLabel(vb)
            la.setStyleSheet("color:#5eead4")
            lb.setStyleSheet("color:#f0a441")
            self.tabla.addWidget(la, f, 1)
            self.tabla.addWidget(lb, f, 2)
            ln = QLabel(nota)
            ln.setObjectName("nota")
            self.tabla.addWidget(ln, f, 3)


def main() -> None:
    app = QApplication(sys.argv)
    v = Ventana()
    v.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
