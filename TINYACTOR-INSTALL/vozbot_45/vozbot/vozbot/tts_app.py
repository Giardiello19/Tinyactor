"""Puente con TU aplicación .py generadora de voz.

Dos modos, según cómo esté hecha tu app:

  cli  → se ejecuta tu script con el texto y la emoción como argumentos.
         Es el modo estable: nada depende de dónde esté la ventana.

  gui  → se automatiza la ventana: pega el texto (portapapeles + Ctrl+V),
         escribe la emoción y hace clic en «Generar voz». Los campos se
         localizan por OCR, así que funciona sin conocer tu código.

En ambos casos el .wav se recoge de la carpeta de salida que indiques.
"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

import httpx

from .audio import VigilanteDeSalida
from .config import TtsApp
from .ocr import LectorPantalla

log = logging.getLogger("vozbot.tts")


def _escritorio():
    """Carga pyautogui/pyperclip solo si se usa el modo gui."""
    import pyautogui
    import pyperclip

    pyautogui.FAILSAFE = True   # ratón a la esquina = freno de emergencia
    pyautogui.PAUSE = 0.15
    return pyautogui, pyperclip


class GeneradorDeVoz:
    def __init__(self, cfg: TtsApp, lector: LectorPantalla | None = None):
        self.cfg = cfg
        self.lector = lector
        self.vigilante = VigilanteDeSalida(
            cfg.carpeta_salida,
            cfg.extension,
            cfg.antiguedad_maxima_s,
            permitir_reciente=cfg.usar_archivo_mas_reciente,
        )
        self._url_cache = ""
        self._pg = None
        self._pc = None

    # ------------------------------------------------------------------
    def generar(self, texto: str, emocion: str) -> Path | None:
        """Manda a generar y devuelve la ruta del .wav resultante."""
        if not texto.strip():
            log.warning("Nada que generar: el texto llegó vacío")
            return None

        log.info("Generando voz — emoción=%s — «%s»", emocion, texto[:70])

        if self.cfg.modo == "http":
            return self._generar_http(texto, emocion)

        previos = self.vigilante.marca()
        if self.cfg.modo == "cli":
            espera = self._generar_cli(texto, emocion)
        else:
            espera = self._generar_gui(texto, emocion)

        wav = self.vigilante.esperar_nuevo(previos, timeout_s=espera)
        if wav:
            log.info("Audio listo: %s", wav.name)
        else:
            log.error("No apareció ningún %s en %s", self.cfg.extension, self.cfg.carpeta_salida)
        return wav

    # ------------------------------ HTTP -------------------------------
    def _base_url(self) -> str:
        """Devuelve la URL de tu app; si no está fijada, busca el puerto."""
        if self._url_cache:
            return self._url_cache
        if self.cfg.http.base_url:
            self._url_cache = self.cfg.http.base_url.rstrip("/")
            return self._url_cache

        for puerto in range(8730, 8771):
            url = f"http://127.0.0.1:{puerto}"
            try:
                r = httpx.get(url + self.cfg.http.ruta_estado, timeout=0.4)
                if r.status_code == 200 and "motores" in r.text:
                    log.info("Encontré tu app de voz en %s", url)
                    self._url_cache = url
                    return url
            except Exception:
                continue
        raise RuntimeError(
            "No encuentro tu app de voz. Arráncala (python app.py) y comprueba "
            "que la ventana del navegador se abrió en 127.0.0.1"
        )

    def voces_disponibles(self) -> list[str]:
        """Pregunta a tu app de voz qué modelos tiene instalados."""
        try:
            r = httpx.get(self._base_url() + self.cfg.http.ruta_estado, timeout=3)
            r.raise_for_status()
            return list(r.json().get("voces") or [])
        except Exception as e:
            log.debug("No pude listar las voces: %s", e)
            return []

    def _generar_http(self, texto: str, emocion: str) -> Path | None:
        h = self.cfg.http
        cuerpo = {
            "linea": texto,
            "acotacion": emocion,
            "motor": h.motor,
            "out": h.carpeta_out,
            "lufs": h.lufs,
        }
        if h.voz:
            cuerpo["voz"] = h.voz
        if h.intensidad is not None:
            cuerpo["intensidad"] = h.intensidad
        if h.referencia:
            cuerpo["referencia"] = h.referencia

        try:
            r = httpx.post(self._base_url() + h.ruta_generar, json=cuerpo, timeout=h.timeout_s)
            r.raise_for_status()
            datos = r.json()
        except httpx.ConnectError:
            log.error(
                "No pude conectar con tu app de voz. ¿Está corriendo «python app.py»?"
            )
            return None
        except httpx.ReadTimeout:
            log.error(
                "Tu app tardó más de %ss en sintetizar. Sube timeout_s o usa el motor piper.",
                h.timeout_s,
            )
            return None
        except Exception as e:
            log.error("Tu app de voz no respondió: %s: %s", type(e).__name__, e)
            return None

        if datos.get("error"):
            log.error("Tu app devolvió un error: %s", datos["error"])
            return None

        tomas = datos.get("tomas") or []
        buenas = [t for t in tomas if t.get("archivo")]
        if not buenas:
            fallo = tomas[0].get("error") if tomas else "la respuesta no traía ninguna toma"
            log.error("La síntesis falló: %s", fallo)
            log.error(
                "Comprueba en la ventana de tu app de voz: motor=%s. Si usas piper, "
                "necesita un .onnx junto a app.py.",
                h.motor,
            )
            return None

        carpeta = Path(datos.get("carpeta") or self.cfg.carpeta_salida)
        wav = carpeta / buenas[-1]["archivo"]
        if not wav.is_file():
            # la app puede correr en otra ruta base: prueba la carpeta configurada
            alterno = Path(self.cfg.carpeta_salida) / buenas[-1]["archivo"]
            if alterno.is_file():
                wav = alterno
            else:
                log.error("La app dice haber creado %s pero no lo encuentro", wav)
                return None

        if datos.get("voz"):
            log.info("Audio listo: %s (voz: %s)", wav.name, datos["voz"])
        else:
            log.info("Audio listo: %s", wav.name)
        return wav

    # ------------------------------ CLI --------------------------------
    def _generar_cli(self, texto: str, emocion: str) -> float:
        c = self.cfg.cli
        args = [a.replace("{texto}", texto).replace("{emocion}", emocion) for a in c.args]
        comando = [c.python, c.script, *args]
        try:
            proc = subprocess.run(
                comando,
                cwd=c.cwd or None,
                capture_output=True,
                text=True,
                timeout=c.timeout_s,
            )
            if proc.returncode != 0:
                log.error("Tu script devolvió código %s: %s", proc.returncode, proc.stderr[-500:])
            elif proc.stdout.strip():
                log.debug("salida: %s", proc.stdout.strip()[-300:])
        except subprocess.TimeoutExpired:
            log.error("Tu script superó el tiempo límite de %ss", c.timeout_s)
        return 20.0  # el .wav ya debería existir; margen corto de escritura

    # ------------------------------ GUI --------------------------------
    def _generar_gui(self, texto: str, emocion: str) -> float:
        self._pg, self._pc = _escritorio()
        g = self.cfg.gui
        self._enfocar_ventana(g.titulo_ventana)

        self._rellenar(g.campo_texto, texto, limpiar=True)
        if g.campo_emocion.ancla or g.campo_emocion.coord:
            self._rellenar(g.campo_emocion, emocion, limpiar=True)

        if not self._click_ocr(g.boton_generar):
            log.error("No encontré el botón «%s» en pantalla", g.boton_generar)
        return float(g.espera_generacion_s)

    def _enfocar_ventana(self, titulo: str) -> None:
        if not titulo:
            return
        try:
            import pygetwindow as gw

            ventanas = gw.getWindowsWithTitle(titulo)
            if ventanas:
                v = ventanas[0]
                if v.isMinimized:
                    v.restore()
                v.activate()
                time.sleep(0.4)
            else:
                log.warning("No encontré la ventana «%s»", titulo)
        except Exception as e:
            log.debug("Enfoque de ventana no disponible: %s", e)

    def _rellenar(self, campo, valor: str, limpiar: bool = True) -> bool:
        """Pega el valor en un campo. El pegado va por portapapeles para no
        perder acentos ni caracteres especiales."""
        destino = None
        if campo.modo == "coord" and campo.coord:
            destino = tuple(campo.coord)
        elif campo.modo == "ocr" and campo.ancla and self.lector:
            h = self.lector.buscar(campo.ancla)
            if h:
                destino = (h.x + campo.offset[0], h.y + campo.offset[1])

        if destino:
            self._pg.click(destino[0], destino[1])
            time.sleep(0.2)
        elif campo.modo == "tab":
            self._pg.press("tab")
        else:
            log.warning("No pude localizar el campo (ancla=%s)", campo.ancla)
            return False

        if limpiar:
            self._pg.hotkey("ctrl", "a")
            self._pg.press("delete")

        self._pc.copy(valor)
        time.sleep(0.1)
        self._pg.hotkey("ctrl", "v")
        time.sleep(0.2)
        return True

    def _click_ocr(self, texto_boton: str) -> bool:
        if not self.lector:
            return False
        h = self.lector.buscar(texto_boton)
        if not h:
            return False
        self._pg.click(h.x, h.y)
        return True
