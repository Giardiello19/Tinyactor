"""Esquemas estrictos (pydantic) para la configuración y el guion del VLM."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


# --------------------------- configuración ---------------------------
class Navegador(BaseModel):
    cdp_url: str = "http://localhost:9222"
    url_juego: str = ""
    selector_raiz: str = "body"
    # Vista de dispositivo. Con esto no hacen falta las herramientas de
    # desarrollador para que la web se sirva en formato móvil.
    dispositivo: str = "Escritorio"
    orientacion: str = "vertical"      # vertical | horizontal
    # Desactivado por defecto: cambiar la vista altera el diseño que sirve la
    # web, y con él los botones. Actívalo solo si quieres el formato móvil.
    emular_al_conectar: bool = False
    ruta_chrome: str = ""              # vacío = se busca sola
    perfil_chrome: str = ""            # vacío = ~/.chromebot


class BotonesWeb(BaseModel):
    iniciar_microfono: list[str] = Field(default_factory=list)
    detener_microfono: list[str] = Field(default_factory=list)
    siguiente: list[str] = Field(default_factory=list)
    reintentar: list[str] = Field(default_factory=list)
    cerrar_modal: list[str] = Field(default_factory=list)
    avanzar: list[str] = Field(
        default_factory=lambda: ["Entendido", "Continuar", "Vamos", "Listo", "Aceptar"]
    )
    # Botones de elección (círculos con una letra). El bot toma cualquiera:
    # el juego solo necesita que elijas para seguir.
    opciones: list[str] = Field(default_factory=lambda: ["A", "B", "C", "D"])
    # Palabras sueltas que delatan un botón de avance aunque el texto exacto
    # no coincida (iconos, saltos de línea, mayúsculas raras).
    palabras_avance: list[str] = Field(
        default_factory=lambda: [
            "seguir ganando", "ganando monedas", "monedas", "seguir jugando",
            "entendido", "continuar", "siguiente", "estoy listo",
        ]
    )
    # Para salir de pantallas de promoción sin entrar en lo que ofrecen.
    # Se prueban ANTES que cualquier otro botón de avance.
    descartar: list[str] = Field(
        default_factory=lambda: [
            "Quizás la próxima", "Quizas la proxima", "QUIZÁS LA PRÓXIMA",
            "Ahora no", "Más tarde", "Mas tarde", "No, gracias", "Omitir", "Saltar",
        ]
    )
    # NUNCA se pulsan, aunque aparezcan en otra lista o los proponga el
    # detector de sinónimos. Salir de la partida no tiene vuelta atrás.
    # OJO: aquí NO va nada que continúe la partida. «SEGUIR GANANDO MONEDAS»
    # se pulsa siempre, aunque sea el botón destacado.
    prohibidos: list[str] = Field(
        default_factory=lambda: [
            # salir de la partida
            "salir", "abandonar", "cancelar", "cerrar sesión", "cerrar sesion",
            "volver", "atrás", "atras", "regresar", "menú principal",
            "rendirse", "exit", "quit",
            # otro modo de juego
            "conversacion", "conversación", "conversaciones",
            "échale un vistazo", "echale un vistazo", "un vistazo",
            # no avanzan nada
            "silencio", "mute",
        ]
    )


class CuentaRegresiva(BaseModel):
    selector: str = ""
    patron: str = r"\b([1-5])\b"
    timeout_s: float = 12.0
    margen_previo_ms: int = 250
    fallback_s: float = 3.0


class Extraccion(BaseModel):
    emociones: list[str] = Field(default_factory=list)
    emocion_por_defecto: str = "neutral"
    patrones_comillas: list[str] = Field(default_factory=list)
    min_caracteres: int = 2
    # Una cita más corta que esto NO reemplaza la frase en curso: el juego
    # entrecomilla palabras sueltas para pedir énfasis, no líneas nuevas.
    min_palabras_frase_nueva: int = 10


class TtsCli(BaseModel):
    python: str = "python"
    script: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str = ""
    timeout_s: int = 120


class CampoGui(BaseModel):
    modo: Literal["ocr", "coord", "tab"] = "ocr"
    ancla: str = ""
    offset: tuple[int, int] = (0, 40)
    coord: tuple[int, int] | None = None


class TtsGui(BaseModel):
    titulo_ventana: str = ""
    campo_texto: CampoGui = Field(default_factory=CampoGui)
    campo_emocion: CampoGui = Field(default_factory=CampoGui)
    boton_generar: str = "Generar voz"
    espera_generacion_s: int = 90


class TtsHttp(BaseModel):
    """Para apps que exponen una API local, como Director de Voz Emocional."""

    base_url: str = ""            # vacío = busca el puerto solo (8730-8770)
    ruta_generar: str = "/api/generate"
    ruta_estado: str = "/api/estado"
    motor: str = "piper"          # piper | chatterbox | xtts
    voz: str = ""                 # nombre del .onnx; vacío = la primera
    carpeta_out: str = "salida"   # la que le pide a tu app (relativa a tu app)
    lufs: float = -18.0
    intensidad: float | None = None
    referencia: str = ""          # wav de voz de referencia, opcional
    timeout_s: int = 180


class TtsApp(BaseModel):
    modo: Literal["http", "cli", "gui"] = "http"
    http: TtsHttp = Field(default_factory=TtsHttp)
    cli: TtsCli = Field(default_factory=TtsCli)
    gui: TtsGui = Field(default_factory=TtsGui)
    carpeta_salida: str = ""
    extension: str = ".wav"
    usar_archivo_mas_reciente: bool = False
    antiguedad_maxima_s: int = 180


class MicrofonoVirtual(BaseModel):
    # "cable"   → reproduce por un cable de audio virtual (VB-CABLE)
    # "archivo" → escribe el wav donde Chrome lo lee como micrófono falso.
    #             Permite varias cuentas a la vez: cada una con su archivo.
    modo: Literal["cable", "archivo"] = "cable"
    archivo_destino: str = ""      # ruta del wav que lee Chrome (modo archivo)
    samplerate_destino: int = 48000
    dispositivo_salida: str = "CABLE Input"
    monitor_local: str = ""
    ganancia_db: float = 0.0
    # 1.0 = velocidad original · 1.3 = un 30 % más rápido · 0.8 = más lento
    velocidad: float = 1.0
    normalizar_lufs: float | None = -18.0
    silencio_inicial_ms: int = 150
    silencio_final_ms: int = 250


class Android(BaseModel):
    """LDPlayer u otro emulador, controlado por ADB."""

    adb: str = "adb"                 # ruta a adb.exe si no está en el PATH
    serial: str = "127.0.0.1:5555"   # instancia de LDPlayer
    paquete: str = ""                # com.tuapp.juego: se lanza al conectar
    espera_arranque_s: float = 6.0
    cache_ms: int = 400              # cuánto reutilizar el último volcado


class Ocr(BaseModel):
    idioma: str = "es"
    usar_gpu: bool = False
    monitor: int = 1


class Razonamiento(BaseModel):
    activo: bool = True
    proveedor: Literal["anthropic", "openai", "compatible"] = "anthropic"
    base_url: str = ""
    modelo: str = "claude-sonnet-4-6"
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_reintentos: int = 3
    solo_si_falla_dom: bool = True


class Obs(BaseModel):
    activo: bool = False
    host: str = "localhost"
    puerto: int = 4455
    password: str = ""


class Retroalimentacion(BaseModel):
    """Lo que el juego responde tras una toma, y cómo reacciona el bot."""

    activa: bool = True
    saturado: list[str] = Field(
        default_factory=lambda: [
            "aléjate del micrófono", "alejate del microfono", "satur",
            "demasiado fuerte", "muy fuerte", "baja la voz", "distorsion", "distorsión",
        ]
    )
    bajo: list[str] = Field(
        default_factory=lambda: [
            "acércate al micrófono", "acercate al microfono", "no te escuch",
            "habla más fuerte", "habla mas fuerte", "muy bajo", "sube la voz",
        ]
    )
    repetir: list[str] = Field(
        default_factory=lambda: ["intenta de nuevo", "vuelve a intentar", "no entendí", "no entendi"]
    )
    paso_db: float = 3.0        # cuánto sube o baja por aviso
    ganancia_min_db: float = -18.0
    ganancia_max_db: float = 12.0


class Bucle(BaseModel):
    max_rondas: int = 50
    pausa_entre_rondas_s: float = 1.5
    detener_si_repite_texto: bool = True
    max_reintentos_toma: int = 3
    reusar_audio_misma_frase: bool = True   # misma frase + misma emoción = mismo wav
    espera_fin_grabacion_s: float = 60.0
    # Tor añade latencia: si sales por proxy, sube los tiempos de espera.
    factor_lentitud: float = 1.0
    ocr_de_respaldo: bool = False   # el DOM basta; paddle solo añade lentitud
    toques_para_premios: int = 6    # toques máximos sobre una tarjeta de premio    # si el juego no tiene botón de detener
    captura_por_ronda: bool = True
    carpeta_logs: str = "./logs"


class Config(BaseModel):
    # "web" → Chrome por CDP · "android" → app en LDPlayer por ADB
    plataforma: Literal["web", "android"] = "web"
    navegador: Navegador = Field(default_factory=Navegador)
    android: Android = Field(default_factory=Android)
    botones_web: BotonesWeb = Field(default_factory=BotonesWeb)
    cuenta_regresiva: CuentaRegresiva = Field(default_factory=CuentaRegresiva)
    extraccion: Extraccion = Field(default_factory=Extraccion)
    tts_app: TtsApp = Field(default_factory=TtsApp)
    microfono_virtual: MicrofonoVirtual = Field(default_factory=MicrofonoVirtual)
    ocr: Ocr = Field(default_factory=Ocr)
    razonamiento: Razonamiento = Field(default_factory=Razonamiento)
    obs: Obs = Field(default_factory=Obs)
    retroalimentacion: Retroalimentacion = Field(default_factory=Retroalimentacion)
    bucle: Bucle = Field(default_factory=Bucle)

    @classmethod
    def cargar(cls, ruta: str | Path) -> "Config":
        data = yaml.safe_load(Path(ruta).read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    def guardar(self, ruta: str | Path) -> None:
        Path(ruta).write_text(
            yaml.safe_dump(self.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )


# ------------------------- guion del VLM/DOM -------------------------
class Accion(BaseModel):
    """Una acción atómica que el bot sabe ejecutar."""

    tipo: Literal[
        "click_texto",      # clic en un botón por su texto
        "click_coord",      # clic por coordenadas (OCR/VLM)
        "generar_y_hablar", # TTS + reproducir por micrófono virtual
        "esperar",
        "scroll",
        "fin",
    ]
    texto: str = ""
    x: int | None = None
    y: int | None = None
    segundos: float = 1.0
    motivo: str = ""


class Guion(BaseModel):
    """Salida estricta del modelo de razonamiento: pantalla → plan."""

    instruccion_detectada: str = ""
    emocion: str = "neutral"
    texto_a_leer: str = ""
    listo_para_hablar: bool = False
    acciones: list[Accion] = Field(default_factory=list)
    confianza: float = 0.0

    @field_validator("emocion")
    @classmethod
    def _limpia(cls, v: str) -> str:
        return (v or "neutral").strip().lower()
