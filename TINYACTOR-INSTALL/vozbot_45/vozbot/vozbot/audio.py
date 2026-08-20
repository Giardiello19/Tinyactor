"""Micrófono emulado.

No inventa un driver: envía el .wav al dispositivo de SALIDA de un cable de
audio virtual (VB-CABLE en Windows, null-sink en PulseAudio, BlackHole en
macOS). En el navegador se elige la ENTRADA de ese mismo cable como micrófono,
así que la web "oye" el audio como si alguien hablara.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

try:
    import pyloudnorm as pyln
except Exception:  # pyloudnorm es opcional
    pyln = None

from .config import MicrofonoVirtual

log = logging.getLogger("vozbot.audio")


def listar_dispositivos_salida() -> list[tuple[int, str]]:
    """Devuelve (índice, nombre) de cada dispositivo capaz de reproducir."""
    salidas = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            salidas.append((i, d["name"]))
    return salidas


def _cambiar_velocidad(datos: np.ndarray, sr: int, factor: float) -> np.ndarray:
    """Acelera o ralentiza sin cambiar el tono de la voz.

    Trabaja por trozos solapados: se recortan fragmentos de unos 40 ms y se
    recolocan más juntos (o más separados) con un cruce suave entre ellos.
    Así la voz suena igual de grave o aguda, solo que más rápida.
    """
    factor = max(0.5, min(2.0, float(factor)))
    if abs(factor - 1.0) < 0.02 or not len(datos):
        return datos

    canales = datos.shape[1] if datos.ndim > 1 else 1
    x = datos.reshape(-1, canales)

    ventana = max(256, int(sr * 0.04))          # ~40 ms
    solape = ventana // 4
    salto_lectura = int((ventana - solape) * factor)
    if salto_lectura < 1:
        return datos

    rampa = np.linspace(0.0, 1.0, solape, dtype=np.float32).reshape(-1, 1)
    trozos: list[np.ndarray] = []
    cola = np.zeros((solape, canales), dtype=np.float32)
    pos = 0

    while pos + ventana <= len(x):
        trozo = x[pos : pos + ventana].astype(np.float32)
        # cruce suave: el final del anterior se funde con el principio de este
        mezcla = cola * (1.0 - rampa) + trozo[:solape] * rampa
        trozos.append(mezcla)
        trozos.append(trozo[solape : ventana - solape])
        cola = trozo[ventana - solape :].copy()
        pos += salto_lectura

    trozos.append(cola)
    salida = np.concatenate(trozos) if trozos else x
    return salida.astype("float32", copy=False)


def buscar_dispositivo(fragmento: str) -> int | None:
    """Busca la salida cuyo nombre encaje con `fragmento`.

    Windows recorta los nombres de dispositivo a 31 caracteres, así que la
    coincidencia se intenta en varios niveles antes de rendirse: exacta,
    contenida en ambos sentidos, y por último solo las dos primeras palabras
    («CABLE Input» encuentra «CABLE Input (VB-Audio Virtual C»).
    """
    if not fragmento:
        return None
    frag = fragmento.lower().strip()
    salidas = listar_dispositivos_salida()

    for idx, nombre in salidas:                     # exacta
        if nombre.lower().strip() == frag:
            return idx
    for idx, nombre in salidas:                     # el buscado está dentro
        if frag in nombre.lower():
            return idx
    for idx, nombre in salidas:                     # el nombre está dentro (recortado)
        if nombre.lower().strip() and nombre.lower().strip() in frag:
            return idx

    corto = " ".join(frag.split()[:2])              # «cable input»
    if corto:
        for idx, nombre in salidas:
            if corto in nombre.lower():
                log.info("Dispositivo encontrado por «%s»: %s", corto, nombre)
                return idx
    return None


class MicrofonoEmulado:
    def __init__(self, cfg: MicrofonoVirtual):
        self.cfg = cfg
        self._parar = threading.Event()
        self.dispositivo = buscar_dispositivo(cfg.dispositivo_salida)
        self.monitor = buscar_dispositivo(cfg.monitor_local)
        if self.dispositivo is None:
            log.error(
                "AUDIO: no encuentro «%s». El sonido saldrá por los altavoces. "
                "Elige el dispositivo en el panel. Disponibles: %s",
                cfg.dispositivo_salida or "(ninguno configurado)",
                [n for _, n in listar_dispositivos_salida()],
            )
        else:
            import sounddevice as _sd

            try:
                log.info("AUDIO: hablaré por «%s»", _sd.query_devices(self.dispositivo)["name"])
            except Exception:
                pass

    # ------------------------------------------------------------------
    def preparar(self, ruta_wav: str | Path) -> tuple[np.ndarray, int]:
        """Lee, normaliza y acolcha el audio para que suene natural en la web."""
        datos, sr = sf.read(str(ruta_wav), dtype="float32", always_2d=True)

        # --- velocidad ---
        # Se acorta el audio manteniendo el tono: el sonido se corta en trozos
        # cortos y se solapan al recomponerlo, en vez de reproducirlo más
        # rápido (que subiría la voz y sonaría a ardilla).
        vel = float(getattr(self.cfg, "velocidad", 1.0) or 1.0)
        if abs(vel - 1.0) > 0.02:
            datos = _cambiar_velocidad(datos, sr, vel)

        if self.cfg.normalizar_lufs is not None and pyln is not None:
            mono = datos.mean(axis=1)
            try:
                medidor = pyln.Meter(sr)
                loudness = medidor.integrated_loudness(mono)
                if np.isfinite(loudness):
                    datos = pyln.normalize.loudness(datos, loudness, self.cfg.normalizar_lufs)
            except Exception as e:  # audio muy corto para el medidor
                log.debug("Normalización LUFS omitida: %s", e)

        if self.cfg.ganancia_db:
            datos = datos * (10 ** (self.cfg.ganancia_db / 20.0))

        # pyloudnorm y la ganancia devuelven float64: el stream exige float32.
        datos = np.clip(datos, -1.0, 1.0).astype("float32", copy=False)

        pre = int(sr * self.cfg.silencio_inicial_ms / 1000)
        post = int(sr * self.cfg.silencio_final_ms / 1000)
        canales = datos.shape[1]
        datos = np.vstack(
            [np.zeros((pre, canales), "float32"), datos, np.zeros((post, canales), "float32")]
        ).astype("float32", copy=False)
        return datos, sr

    def duracion(self, ruta_wav: str | Path) -> float:
        info = sf.info(str(ruta_wav))
        extra = (self.cfg.silencio_inicial_ms + self.cfg.silencio_final_ms) / 1000
        return info.frames / info.samplerate + extra

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def escribir_para_chrome(self, ruta_wav: str | Path) -> float:
        """Deja el audio donde Chrome lo lee como micrófono falso.

        Chrome exige PCM de 16 bits; se convierte a 48 kHz mono, que es lo que
        usa internamente para la captura. La escritura es atómica (archivo
        temporal y renombrado) para que el navegador nunca lea un wav a medias.

        Devuelve la duración en segundos.
        """
        destino = Path(self.cfg.archivo_destino)
        if not self.cfg.archivo_destino:
            log.error("Modo archivo sin archivo_destino configurado")
            return 0.0

        datos, sr = self.preparar(ruta_wav)

        # a mono: Chrome toma el primer canal si le das estéreo, pero mezclar
        # conserva mejor el nivel
        if datos.ndim > 1 and datos.shape[1] > 1:
            datos = datos.mean(axis=1)
        else:
            datos = datos.reshape(-1)

        objetivo = int(self.cfg.samplerate_destino)
        if sr != objetivo:
            muestras = int(round(len(datos) * objetivo / sr))
            viejo = np.linspace(0.0, 1.0, num=len(datos), endpoint=False)
            nuevo = np.linspace(0.0, 1.0, num=muestras, endpoint=False)
            datos = np.interp(nuevo, viejo, datos).astype("float32")
            sr = objetivo

        datos = np.clip(datos, -1.0, 1.0)

        destino.parent.mkdir(parents=True, exist_ok=True)
        temporal = destino.with_suffix(".tmp.wav")
        sf.write(str(temporal), datos, sr, subtype="PCM_16")
        temporal.replace(destino)   # atómico en el mismo disco

        duracion = len(datos) / sr
        log.info("Micrófono falso listo: %s (%.1fs, %d Hz)", destino.name, duracion, sr)
        return duracion

    def hablar(self, ruta_wav: str | Path, bloquear: bool = True) -> float:
        """Reproduce el wav por el micrófono virtual. Devuelve su duración."""
        if self.dispositivo is None:
            log.error(
                "No hay micrófono virtual: no encuentro «%s». Elige el dispositivo "
                "correcto en el panel (suele llamarse CABLE Input).",
                self.cfg.dispositivo_salida,
            )
            return 0.0

        datos, sr = self.preparar(ruta_wav)
        self._parar.clear()

        if self.monitor is not None:
            threading.Thread(
                target=self._reproducir, args=(datos, sr, self.monitor), daemon=True
            ).start()

        if bloquear:
            self._reproducir(datos, sr, self.dispositivo)
        else:
            threading.Thread(
                target=self._reproducir, args=(datos, sr, self.dispositivo), daemon=True
            ).start()
        return len(datos) / sr

    def reproducir_preparado(self, datos: np.ndarray, sr: int) -> None:
        """Reproduce audio ya normalizado y en memoria: latencia mínima."""
        if self.dispositivo is None:
            log.error(
                "No hay micrófono virtual: no encuentro «%s»", self.cfg.dispositivo_salida
            )
            return
        self._parar.clear()
        if self.monitor is not None:
            threading.Thread(
                target=self._reproducir, args=(datos, sr, self.monitor), daemon=True
            ).start()
        self._reproducir(datos, sr, self.dispositivo)

    def _reproducir(self, datos: np.ndarray, sr: int, dispositivo: int | None) -> None:
        bloque = 1024
        pos = 0
        canales = datos.shape[1]
        try:
            with sd.OutputStream(
                samplerate=sr, device=dispositivo, channels=canales, dtype="float32"
            ) as stream:
                while pos < len(datos) and not self._parar.is_set():
                    stream.write(datos[pos : pos + bloque])
                    pos += bloque
        except Exception as e:
            nombre = "desconocido"
            try:
                nombre = sd.query_devices(dispositivo)["name"]
            except Exception:
                pass
            log.error(
                "No pude reproducir en «%s» (%s Hz, %s canales): %s",
                nombre,
                sr,
                canales,
                e,
            )

    def ajustar_ganancia(self, delta_db: float, minimo: float = -18.0, maximo: float = 12.0) -> float:
        """Sube o baja el volumen sin reiniciar nada. Devuelve la ganancia nueva."""
        self.cfg.ganancia_db = max(minimo, min(maximo, self.cfg.ganancia_db + delta_db))
        return self.cfg.ganancia_db

    def fijar_ganancia(self, db: float) -> float:
        self.cfg.ganancia_db = db
        return db

    def silenciar(self) -> None:
        self._parar.set()


# ---------------------------------------------------------------------
class VigilanteDeSalida:
    """Encuentra el .wav recién generado por tu app de TTS."""

    def __init__(
        self,
        carpeta: str,
        extension: str = ".wav",
        antiguedad_max_s: int = 180,
        permitir_reciente: bool = False,
    ):
        self.carpeta = Path(carpeta)
        self.extension = extension
        self.antiguedad_max_s = antiguedad_max_s
        self.permitir_reciente = permitir_reciente

    def _candidatos(self) -> list[Path]:
        if not self.carpeta.is_dir():
            return []
        return [p for p in self.carpeta.glob(f"*{self.extension}") if p.is_file()]

    def marca(self) -> set[Path]:
        """Foto del estado actual, para saber después qué es nuevo."""
        return set(self._candidatos())

    def esperar_nuevo(self, previos: set[Path], timeout_s: float = 90.0) -> Path | None:
        """Espera a que aparezca un archivo nuevo y termine de escribirse."""
        limite = time.time() + timeout_s
        while time.time() < limite:
            nuevos = [p for p in self._candidatos() if p not in previos]
            if nuevos:
                reciente = max(nuevos, key=lambda p: p.stat().st_mtime)
                if self._estable(reciente):
                    return reciente
            time.sleep(0.25)

        # Respaldo desactivado por defecto: reproducir un audio viejo es peor
        # que no reproducir nada, porque el juego lo da por bueno.
        if not self.permitir_reciente:
            log.error(
                "No apareció ningún %s nuevo en %s tras %.0fs",
                self.extension,
                self.carpeta,
                timeout_s,
            )
            return None

        todos = self._candidatos()
        if todos:
            reciente = max(todos, key=lambda p: p.stat().st_mtime)
            if time.time() - reciente.stat().st_mtime <= self.antiguedad_max_s:
                log.warning("Sin archivo nuevo; uso el más reciente: %s", reciente.name)
                return reciente
        return None

    @staticmethod
    def _estable(ruta: Path, intentos: int = 8) -> bool:
        """El archivo dejó de crecer = tu app terminó de escribirlo."""
        anterior = -1
        for _ in range(intentos):
            try:
                actual = ruta.stat().st_size
            except OSError:
                return False
            if actual == anterior and actual > 0:
                return True
            anterior = actual
            time.sleep(0.2)
        return False
