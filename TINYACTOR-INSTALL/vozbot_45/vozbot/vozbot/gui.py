"""Panel de control: aquí escribes los textos de los botones, eliges la carpeta
de salida de tu app de voz y el cable de audio, y arrancas el bot."""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .audio import MicrofonoEmulado, VigilanteDeSalida, listar_dispositivos_salida
from .config import Config
from .orchestrator import Orquestador

RUTA_CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"

ESTILO = """
QWidget { background:#12141a; color:#e6e1d8; font-family:'Segoe UI',system-ui,sans-serif; font-size:13px; }
QGroupBox { border:1px solid #262a35; border-radius:8px; margin-top:14px; padding:12px 10px 10px; }
QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 6px; color:#f0a441; font-weight:600;
                   letter-spacing:.06em; text-transform:uppercase; font-size:11px; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {
    background:#0c0e13; border:1px solid #2b303c; border-radius:6px; padding:6px 8px; color:#e6e1d8; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color:#f0a441; }
QPushButton { background:#1d212b; border:1px solid #333949; border-radius:6px; padding:8px 16px; }
QPushButton:hover { border-color:#f0a441; }
QPushButton#primario { background:#f0a441; color:#12141a; font-weight:700; border:none; }
QPushButton#peligro  { background:#3a1f22; color:#ffb4ab; border:1px solid #5a2f33; }
QPlainTextEdit#log { font-family:'JetBrains Mono','Consolas',monospace; font-size:12px; color:#b9d4c4; }
QTabBar::tab { background:transparent; padding:8px 14px; color:#8b93a5; }
QTabBar::tab:selected { color:#f0a441; border-bottom:2px solid #f0a441; }
QTabWidget::pane { border:none; }
QLabel#pista { color:#7d8496; font-size:11px; }
"""


class PuenteDeLog(logging.Handler):
    """Lleva al panel lo que los módulos escriben con logging."""

    def __init__(self, emitir):
        super().__init__(level=logging.INFO)
        self.emitir = emitir

    def emit(self, registro: logging.LogRecord) -> None:
        # el orquestador ya envía sus propios eventos: no los repetimos
        if registro.name == "vozbot.bucle":
            return
        try:
            tipo = {
                logging.ERROR: "error",
                logging.WARNING: "aviso",
                logging.CRITICAL: "error",
            }.get(registro.levelno, registro.name.replace("vozbot.", ""))
            self.emitir(tipo, registro.getMessage())
        except Exception:
            pass


class HiloBot(QThread):
    evento = Signal(str, str)

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.orq: Orquestador | None = None

    def run(self) -> None:
        puente = PuenteDeLog(lambda t, m: self.evento.emit(t, m))
        raiz = logging.getLogger("vozbot")
        raiz.setLevel(logging.INFO)
        raiz.addHandler(puente)

        self.orq = Orquestador(self.cfg, on_evento=lambda t, m: self.evento.emit(t, m))
        try:
            self.orq.ejecutar()
        except Exception as e:  # el bot nunca debe tumbar la ventana
            self.evento.emit("error", f"{type(e).__name__}: {e}")
        finally:
            raiz.removeHandler(puente)

    def parar(self) -> None:
        if self.orq:
            self.orq.detener()


class Panel(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = Config.cargar(RUTA_CONFIG) if RUTA_CONFIG.exists() else Config()
        self.hilo: HiloBot | None = None
        self.setWindowTitle("vozbot — automatización de lectura")
        self.resize(940, 720)
        self.setStyleSheet(ESTILO)
        self._construir()
        self._volcar_config()

    # ------------------------------------------------------------------
    def _construir(self) -> None:
        raiz = QVBoxLayout(self)
        pestanas = QTabWidget()
        pestanas.addTab(self._tab_web(), "Web")
        pestanas.addTab(self._tab_voz(), "Voz y micrófono")
        pestanas.addTab(self._tab_ia(), "Razonamiento")
        raiz.addWidget(pestanas)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setObjectName("log")
        self.log.setMinimumHeight(200)
        raiz.addWidget(self.log)

        barra = QHBoxLayout()
        self.btn_guardar = QPushButton("Guardar configuración")
        self.btn_probar = QPushButton("Probar micrófono virtual")
        self.btn_audio = QPushButton("Ver dispositivos de audio")
        self.btn_arrancar = QPushButton("Empezar a jugar")
        self.btn_arrancar.setObjectName("primario")
        self.btn_parar = QPushButton("Detener")
        self.btn_parar.setObjectName("peligro")
        self.btn_parar.setEnabled(False)

        self.btn_guardar.clicked.connect(self.guardar)
        self.btn_probar.clicked.connect(self.probar_micro)
        self.btn_audio.clicked.connect(self.ver_dispositivos)
        self.btn_arrancar.clicked.connect(self.arrancar)
        self.btn_parar.clicked.connect(self.parar)

        barra.addWidget(self.btn_guardar)
        barra.addWidget(self.btn_probar)
        barra.addWidget(self.btn_audio)
        barra.addStretch()
        barra.addWidget(self.btn_parar)
        barra.addWidget(self.btn_arrancar)
        raiz.addLayout(barra)

    # ------------------------------------------------------------------
    def _tab_web(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.cdp = QLineEdit()
        self.url = QLineEdit()
        f.addRow("Chrome (CDP)", self.cdp)
        f.addRow("URL del juego", self.url)

        from .lanzador import DISPOSITIVOS

        fila_disp = QHBoxLayout()
        self.dispositivo_vista = QComboBox()
        self.dispositivo_vista.addItems(list(DISPOSITIVOS))
        self.btn_abrir_chrome = QPushButton("Abrir navegador")
        self.btn_abrir_chrome.setObjectName("primario")
        self.btn_abrir_chrome.clicked.connect(self.abrir_navegador)
        self.orientacion = QComboBox()
        self.orientacion.addItems(["vertical", "horizontal"])
        fila_disp.addWidget(self.dispositivo_vista, 1)
        fila_disp.addWidget(self.orientacion)
        fila_disp.addWidget(self.btn_abrir_chrome)
        cont_disp = QWidget()
        cont_disp.setLayout(fila_disp)
        f.addRow("Vista del dispositivo", cont_disp)

        self.emular = QCheckBox("Aplicar la vista al conectarse (sin abrir F12)")
        f.addRow(self.emular)

        pista_disp = QLabel(
            "«Abrir navegador» lanza Chrome ya listo para automatizar, con el "
            "tamaño y el user-agent del dispositivo elegido. No necesitas las "
            "herramientas de desarrollador, que además confunden al bot."
        )
        pista_disp.setObjectName("pista")
        pista_disp.setWordWrap(True)
        f.addRow(pista_disp)

        g = QGroupBox("Textos de los botones — uno por línea, se prueban en orden")
        rejilla = QGridLayout(g)
        self.campos_boton: dict[str, QPlainTextEdit] = {}
        etiquetas = {
            "iniciar_microfono": "Iniciar micrófono",
            "detener_microfono": "Detener",
            "siguiente": "Siguiente",
            "reintentar": "Reintentar",
            "cerrar_modal": "Cerrar aviso",
        }
        for fila, (clave, etiqueta) in enumerate(etiquetas.items()):
            caja = QPlainTextEdit()
            caja.setMaximumHeight(64)
            self.campos_boton[clave] = caja
            rejilla.addWidget(QLabel(etiqueta), fila, 0, Qt.AlignTop)
            rejilla.addWidget(caja, fila, 1)
        f.addRow(g)

        self.sel_cuenta = QLineEdit()
        self.fallback = QSpinBox()
        self.fallback.setRange(0, 30)
        f.addRow("Selector de la cuenta regresiva", self.sel_cuenta)
        f.addRow("Espera si no hay cuenta (s)", self.fallback)
        pista = QLabel("Deja el selector vacío para buscar el 3·2·1 en toda la página.")
        pista.setObjectName("pista")
        f.addRow(pista)
        return w

    def _tab_voz(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)

        self.modo = QComboBox()
        self.modo.addItems(["http", "cli", "gui"])
        f.addRow("Modo de tu app de voz", self.modo)

        self.url_tts = QLineEdit()
        self.url_tts.setPlaceholderText("vacío = busca el puerto solo (8730-8770)")
        f.addRow("URL de tu app (modo http)", self.url_tts)
        self.motor = QComboBox()
        self.motor.addItems(["piper", "chatterbox", "xtts"])
        f.addRow("Motor de síntesis", self.motor)

        fila_voz = QHBoxLayout()
        self.voz = QComboBox()
        self.voz.setEditable(True)          # permite escribirla si no está en la lista
        self.voz.addItem("— la primera disponible —", "")
        btn_voces = QPushButton("Buscar voces")
        btn_voces.clicked.connect(self.cargar_voces)
        fila_voz.addWidget(self.voz, 1)
        fila_voz.addWidget(btn_voces)
        cont_voz = QWidget()
        cont_voz.setLayout(fila_voz)
        f.addRow("Voz de Piper", cont_voz)

        pista_voz = QLabel(
            "Pulsa «Buscar voces» con tu app de voz abierta. "
            "Las voces viven en la carpeta voces/ de tu generador."
        )
        pista_voz.setObjectName("pista")
        pista_voz.setWordWrap(True)
        f.addRow(pista_voz)

        fila = QHBoxLayout()
        self.script = QLineEdit()
        b = QPushButton("Elegir .py")
        b.clicked.connect(self._elegir_script)
        fila.addWidget(self.script)
        fila.addWidget(b)
        cont = QWidget()
        cont.setLayout(fila)
        f.addRow("Script generador", cont)

        self.args = QLineEdit()
        f.addRow("Argumentos", self.args)
        pista = QLabel("Usa {texto} y {emocion}. Ej: --texto {texto} --emocion {emocion}")
        pista.setObjectName("pista")
        f.addRow(pista)

        self.ventana = QLineEdit()
        self.boton_generar = QLineEdit()
        f.addRow("Título de ventana (modo gui)", self.ventana)
        f.addRow("Botón de generar (modo gui)", self.boton_generar)

        fila2 = QHBoxLayout()
        self.salida = QLineEdit()
        b2 = QPushButton("Elegir carpeta")
        b2.clicked.connect(self._elegir_salida)
        fila2.addWidget(self.salida)
        fila2.addWidget(b2)
        cont2 = QWidget()
        cont2.setLayout(fila2)
        f.addRow("Carpeta de salida (.wav)", cont2)

        self.dispositivo = QComboBox()
        self.dispositivo.addItem("— elige el cable virtual —", "")
        for idx, nombre in listar_dispositivos_salida():
            self.dispositivo.addItem(f"{idx}: {nombre}", nombre)
        f.addRow("Micrófono virtual (salida)", self.dispositivo)

        self.monitor = QComboBox()
        self.monitor.addItem("— sin monitor —", "")
        for idx, nombre in listar_dispositivos_salida():
            self.monitor.addItem(f"{idx}: {nombre}", nombre)
        f.addRow("Escucharlo también en", self.monitor)

        self.lufs = QSpinBox()
        self.lufs.setRange(-40, 0)
        f.addRow("Normalizar a (LUFS)", self.lufs)

        fila3 = QHBoxLayout()
        self.volumen = QSlider(Qt.Horizontal)
        self.volumen.setRange(-18, 12)
        self.volumen.setTickInterval(3)
        self.volumen.setTickPosition(QSlider.TicksBelow)
        self.etiq_volumen = QLabel("0 dB")
        self.etiq_volumen.setMinimumWidth(56)
        self.volumen.valueChanged.connect(self._cambiar_volumen)
        fila3.addWidget(self.volumen)
        fila3.addWidget(self.etiq_volumen)
        cont3 = QWidget()
        cont3.setLayout(fila3)
        f.addRow("Volumen del micrófono", cont3)

        fila_vel = QHBoxLayout()
        self.velocidad = QSlider(Qt.Horizontal)
        self.velocidad.setRange(70, 180)          # 0.70x a 1.80x
        self.velocidad.setTickInterval(10)
        self.velocidad.setTickPosition(QSlider.TicksBelow)
        self.etiq_velocidad = QLabel("1.00x")
        self.etiq_velocidad.setMinimumWidth(56)
        self.velocidad.valueChanged.connect(self._cambiar_velocidad)
        fila_vel.addWidget(self.velocidad)
        fila_vel.addWidget(self.etiq_velocidad)
        cont_vel = QWidget()
        cont_vel.setLayout(fila_vel)
        f.addRow("Velocidad de la voz", cont_vel)

        pista_vel = QLabel(
            "Acelera los diálogos sin que la voz suba de tono. Se aplica a la "
            "toma siguiente. Por encima de 1.5x puede perder naturalidad."
        )
        pista_vel.setObjectName("pista")
        pista_vel.setWordWrap(True)
        f.addRow(pista_vel)

        pista_vol = QLabel(
            "Se aplica al instante, incluso con el bot en marcha. "
            "Si el juego pide que te alejes del micrófono, el bot lo baja solo."
        )
        pista_vol.setObjectName("pista")
        pista_vol.setWordWrap(True)
        f.addRow(pista_vol)
        return w

    def _tab_ia(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.ia_activa = QCheckBox("Usar modelo de razonamiento cuando el DOM no baste")
        self.solo_fallo = QCheckBox("Solo si falla la lectura del DOM (más rápido y barato)")
        self.proveedor = QComboBox()
        self.proveedor.addItems(["anthropic", "openai", "compatible"])
        self.modelo = QLineEdit()
        self.base_url = QLineEdit()
        self.env_key = QLineEdit()
        f.addRow(self.ia_activa)
        f.addRow(self.solo_fallo)
        f.addRow("Proveedor", self.proveedor)
        f.addRow("Modelo", self.modelo)
        f.addRow("Base URL (local/compatible)", self.base_url)
        f.addRow("Variable con la API key", self.env_key)
        self.emociones = QPlainTextEdit()
        self.emociones.setMaximumHeight(80)
        f.addRow("Emociones reconocidas", self.emociones)
        return w

    # ------------------------------------------------------------------
    def _elegir_script(self) -> None:
        ruta, _ = QFileDialog.getOpenFileName(self, "Tu generador de voz", "", "Python (*.py)")
        if ruta:
            self.script.setText(ruta)

    def _elegir_salida(self) -> None:
        ruta = QFileDialog.getExistingDirectory(self, "Carpeta donde tu app deja los .wav")
        if ruta:
            self.salida.setText(ruta)

    # ------------------------------------------------------------------
    def _volcar_config(self) -> None:
        c = self.cfg
        self.cdp.setText(c.navegador.cdp_url)
        self.url.setText(c.navegador.url_juego)
        self.dispositivo_vista.setCurrentText(c.navegador.dispositivo)
        self.orientacion.setCurrentText(c.navegador.orientacion)
        self.emular.setChecked(c.navegador.emular_al_conectar)
        for clave, caja in self.campos_boton.items():
            caja.setPlainText("\n".join(getattr(c.botones_web, clave)))
        self.sel_cuenta.setText(c.cuenta_regresiva.selector)
        self.fallback.setValue(int(c.cuenta_regresiva.fallback_s))

        self.modo.setCurrentText(c.tts_app.modo)
        self.url_tts.setText(c.tts_app.http.base_url)
        self.motor.setCurrentText(c.tts_app.http.motor)
        self._seleccionar_voz(c.tts_app.http.voz)
        self.script.setText(c.tts_app.cli.script)
        self.args.setText(" ".join(c.tts_app.cli.args))
        self.ventana.setText(c.tts_app.gui.titulo_ventana)
        self.boton_generar.setText(c.tts_app.gui.boton_generar)
        self.salida.setText(c.tts_app.carpeta_salida)
        self._seleccionar(self.dispositivo, c.microfono_virtual.dispositivo_salida)
        self._seleccionar(self.monitor, c.microfono_virtual.monitor_local)
        self.lufs.setValue(int(c.microfono_virtual.normalizar_lufs or -18))
        self.velocidad.setValue(int(round(c.microfono_virtual.velocidad * 100)))
        self.etiq_velocidad.setText(f"{c.microfono_virtual.velocidad:.2f}x")
        self.volumen.setValue(int(c.microfono_virtual.ganancia_db))
        self.etiq_volumen.setText(f"{int(c.microfono_virtual.ganancia_db):+d} dB")

        self.ia_activa.setChecked(c.razonamiento.activo)
        self.solo_fallo.setChecked(c.razonamiento.solo_si_falla_dom)
        self.proveedor.setCurrentText(c.razonamiento.proveedor)
        self.modelo.setText(c.razonamiento.modelo)
        self.base_url.setText(c.razonamiento.base_url)
        self.env_key.setText(c.razonamiento.api_key_env)
        self.emociones.setPlainText(", ".join(c.extraccion.emociones))

    @staticmethod
    def _seleccionar(combo: QComboBox, fragmento: str) -> None:
        if not fragmento:
            return
        for i in range(combo.count()):
            if fragmento.lower() in (combo.itemData(i) or "").lower():
                combo.setCurrentIndex(i)
                return

    def _recoger_config(self) -> Config:
        c = self.cfg
        c.navegador.cdp_url = self.cdp.text().strip()
        c.navegador.url_juego = self.url.text().strip()
        c.navegador.dispositivo = self.dispositivo_vista.currentText()
        c.navegador.orientacion = self.orientacion.currentText()
        c.navegador.emular_al_conectar = self.emular.isChecked()
        for clave, caja in self.campos_boton.items():
            valores = [l.strip() for l in caja.toPlainText().splitlines() if l.strip()]
            setattr(c.botones_web, clave, valores)
        c.cuenta_regresiva.selector = self.sel_cuenta.text().strip()
        c.cuenta_regresiva.fallback_s = float(self.fallback.value())

        c.tts_app.modo = self.modo.currentText()
        c.tts_app.http.base_url = self.url_tts.text().strip()
        c.tts_app.http.motor = self.motor.currentText()
        elegida = self.voz.currentData()
        if elegida is None:                # escrita a mano
            texto = self.voz.currentText().strip()
            elegida = "" if texto.startswith("—") else texto
        c.tts_app.http.voz = elegida
        c.tts_app.cli.script = self.script.text().strip()
        c.tts_app.cli.args = self.args.text().split()
        c.tts_app.gui.titulo_ventana = self.ventana.text().strip()
        c.tts_app.gui.boton_generar = self.boton_generar.text().strip()
        c.tts_app.carpeta_salida = self.salida.text().strip()
        # Se guarda el nombre corto («CABLE Input»): Windows recorta los
        # nombres a 31 caracteres y guardar el largo daba problemas.
        elegido = self.dispositivo.currentData() or ""
        if elegido:
            partes = elegido.split(" (")[0].strip()
            c.microfono_virtual.dispositivo_salida = partes or elegido
        elif not c.microfono_virtual.dispositivo_salida:
            c.microfono_virtual.dispositivo_salida = ""
        c.microfono_virtual.monitor_local = self.monitor.currentData() or ""
        c.microfono_virtual.normalizar_lufs = float(self.lufs.value())
        c.microfono_virtual.ganancia_db = float(self.volumen.value())
        c.microfono_virtual.velocidad = self.velocidad.value() / 100

        c.razonamiento.activo = self.ia_activa.isChecked()
        c.razonamiento.solo_si_falla_dom = self.solo_fallo.isChecked()
        c.razonamiento.proveedor = self.proveedor.currentText()
        c.razonamiento.modelo = self.modelo.text().strip()
        c.razonamiento.base_url = self.base_url.text().strip()
        c.razonamiento.api_key_env = self.env_key.text().strip()
        c.extraccion.emociones = [e.strip() for e in self.emociones.toPlainText().split(",") if e.strip()]
        return c

    # ------------------------------------------------------------------
    def abrir_navegador(self) -> None:
        """Lanza Chrome con la vista del dispositivo elegido."""
        from .lanzador import abrir_chrome

        cfg = self._recoger_config()
        try:
            puerto = int(cfg.navegador.cdp_url.rsplit(":", 1)[-1].strip("/"))
        except ValueError:
            puerto = 9222

        ok, detalle = abrir_chrome(
            dispositivo=cfg.navegador.dispositivo,
            puerto=puerto,
            perfil=cfg.navegador.perfil_chrome,
            url=cfg.navegador.url_juego,
            ruta_chrome=cfg.navegador.ruta_chrome,
            orientacion=cfg.navegador.orientacion,
        )
        if ok:
            self.escribir("navegador", f"Chrome abierto: {detalle}")
            self.escribir("navegador", "Inicia sesión y elige el micrófono del sitio")
        else:
            self.escribir("error", detalle)

    def cargar_voces(self) -> None:
        """Pregunta a tu app de voz qué modelos tiene y llena el desplegable."""
        from .tts_app import GeneradorDeVoz

        cfg = self._recoger_config()
        actual = self.voz.currentData() or self.voz.currentText()
        try:
            voces = GeneradorDeVoz(cfg.tts_app).voces_disponibles()
        except Exception as e:
            self.escribir("error", f"No pude consultar las voces: {e}")
            return

        if not voces:
            self.escribir(
                "aviso",
                "No encontré voces. ¿Está corriendo tu app de voz? "
                "¿Hay archivos .onnx con su .onnx.json en la carpeta voces/?",
            )
            return

        self.voz.clear()
        self.voz.addItem("— la primera disponible —", "")
        for v in voces:
            self.voz.addItem(v, v)
        self._seleccionar_voz(actual)
        self.escribir("config", f"{len(voces)} voz(ces) encontrada(s): {', '.join(voces)}")

    def _seleccionar_voz(self, nombre: str) -> None:
        if not nombre:
            self.voz.setCurrentIndex(0)
            return
        for i in range(self.voz.count()):
            if (self.voz.itemData(i) or "") == nombre:
                self.voz.setCurrentIndex(i)
                return
        self.voz.setEditText(nombre)       # no está instalada, pero se respeta

    def _cambiar_velocidad(self, valor: int) -> None:
        factor = valor / 100
        self.etiq_velocidad.setText(f"{factor:.2f}x")
        self.cfg.microfono_virtual.velocidad = factor
        if self.hilo and self.hilo.orq:
            self.hilo.orq.mic.cfg.velocidad = factor

    def _cambiar_volumen(self, valor: int) -> None:
        self.etiq_volumen.setText(f"{valor:+d} dB")
        self.cfg.microfono_virtual.ganancia_db = float(valor)
        # si el bot está corriendo, el cambio surte efecto en la toma siguiente
        if self.hilo and self.hilo.orq:
            self.hilo.orq.mic.fijar_ganancia(float(valor))

    def escribir(self, tipo: str, mensaje: str) -> None:
        self.log.appendPlainText(f"[{tipo}] {mensaje}")

    def guardar(self) -> None:
        self._recoger_config().guardar(RUTA_CONFIG)
        self.escribir("config", f"Guardado en {RUTA_CONFIG}")

    def ver_dispositivos(self) -> None:
        """Lista las salidas de audio y marca la elegida."""
        cfg = self._recoger_config()
        buscado = (cfg.microfono_virtual.dispositivo_salida or "").lower()
        self.escribir("audio", "Dispositivos de salida disponibles:")
        encontrado = False
        for idx, nombre in listar_dispositivos_salida():
            marca = ""
            if buscado and buscado in nombre.lower():
                marca = "   <<< el elegido"
                encontrado = True
            self.escribir("audio", f"   {idx}: {nombre}{marca}")
        if buscado and not encontrado:
            self.escribir("error", f"«{cfg.microfono_virtual.dispositivo_salida}» no está en la lista")
        elif not buscado:
            self.escribir("aviso", "No has elegido ningún dispositivo de salida")

    def probar_micro(self) -> None:
        cfg = self._recoger_config()
        vig = VigilanteDeSalida(cfg.tts_app.carpeta_salida, cfg.tts_app.extension)
        archivos = sorted(vig._candidatos(), key=lambda p: p.stat().st_mtime, reverse=True)
        if not archivos:
            self.escribir("error", "No hay .wav en la carpeta de salida")
            return
        mic = MicrofonoEmulado(cfg.microfono_virtual)
        if mic.dispositivo is None:
            self.escribir("error", "No encontré el cable virtual seleccionado")
            return
        self.escribir("micro", f"Reproduciendo {archivos[0].name} por el micrófono virtual")
        threading.Thread(target=mic.hablar, args=(archivos[0], True), daemon=True).start()

    def arrancar(self) -> None:
        cfg = self._recoger_config()
        cfg.guardar(RUTA_CONFIG)
        self.log.clear()
        self.hilo = HiloBot(cfg)
        self.hilo.evento.connect(self.escribir)
        self.hilo.finished.connect(self._al_terminar)
        self.hilo.start()
        self.btn_arrancar.setEnabled(False)
        self.btn_parar.setEnabled(True)

    def parar(self) -> None:
        if self.hilo:
            self.hilo.parar()
            self.escribir("bot", "Deteniendo al final de la acción en curso…")

    def _al_terminar(self) -> None:
        self.btn_arrancar.setEnabled(True)
        self.btn_parar.setEnabled(False)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    app = QApplication(sys.argv)
    panel = Panel()
    panel.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
