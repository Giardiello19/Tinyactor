"""Control de una app Android en LDPlayer, vía ADB.

Sustituye a NavegadorWeb sin que el orquestador note la diferencia: expone los
mismos métodos. En vez de leer el DOM, vuelca el árbol de accesibilidad de
Android (uiautomator), que también trae el texto y la posición de cada
elemento. En vez de mover el ratón, envía toques con «input tap».

El audio no cambia: LDPlayer toma CABLE Output como micrófono, así que el modo
«cable» sigue funcionando igual.
"""
from __future__ import annotations

import logging
import re
import subprocess
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger("vozbot.android")


def _sin_acentos(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


class _PantallaFalsa:
    """Sustituto mínimo de page, para el scroll del orquestador."""

    def __init__(self, driver: "NavegadorAndroid"):
        self._d = driver
        self.mouse = self
        self.url = "android://ldplayer"

    def wheel(self, dx: int, dy: int) -> None:
        self._d.deslizar(arriba=dy > 0)


class NavegadorAndroid:
    """Misma interfaz que NavegadorWeb, pero contra un emulador Android."""

    def __init__(self, cfg_android, botones, cuenta):
        self.cfg = cfg_android
        self.botones = botones
        self.cuenta = cuenta
        self.serial = cfg_android.serial
        self.page = _PantallaFalsa(self)

        self._cache: list[dict] = []
        self._cache_ts = 0.0
        self._ancho = 0
        self._alto = 0

    # ------------------------------------------------------------------
    # ADB
    # ------------------------------------------------------------------
    def _adb(self, *args: str, binario: bool = False, timeout: int = 20):
        comando = [self.cfg.adb, "-s", self.serial, *args]
        try:
            r = subprocess.run(comando, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            log.error("ADB no respondió: %s", " ".join(args[:2]))
            return b"" if binario else ""
        if r.returncode != 0:
            log.debug("ADB devolvió %s: %s", r.returncode, r.stderr[:200])
        return r.stdout if binario else r.stdout.decode("utf-8", "replace")

    def conectar(self):
        """Conecta con la instancia de LDPlayer y mide la pantalla."""
        subprocess.run([self.cfg.adb, "connect", self.serial], capture_output=True, timeout=15)
        dispositivos = subprocess.run(
            [self.cfg.adb, "devices"], capture_output=True, timeout=10
        ).stdout.decode("utf-8", "replace")

        if self.serial not in dispositivos or "device" not in dispositivos:
            raise RuntimeError(
                f"No encuentro {self.serial} en ADB. ¿Está LDPlayer abierto y la "
                f"depuración USB activada en Ajustes de desarrollador?"
            )

        tamano = self._adb("shell", "wm", "size")
        m = re.search(r"(\d+)x(\d+)", tamano)
        if m:
            self._ancho, self._alto = int(m.group(1)), int(m.group(2))
        log.info("Conectado a %s · pantalla %sx%s", self.serial, self._ancho, self._alto)

        if self.cfg.paquete:
            self._adb("shell", "monkey", "-p", self.cfg.paquete, "-c",
                      "android.intent.category.LAUNCHER", "1")
            time.sleep(self.cfg.espera_arranque_s)
        return self.page

    def cerrar(self) -> None:
        log.info("Sesión Android terminada (el emulador sigue abierto)")

    # ------------------------------------------------------------------
    # Lectura de pantalla
    # ------------------------------------------------------------------
    def _volcar(self, forzar: bool = False) -> list[dict]:
        """Árbol de accesibilidad: el equivalente al DOM en Android."""
        if not forzar and time.time() - self._cache_ts < self.cfg.cache_ms / 1000:
            return self._cache

        xml = self._adb("exec-out", "uiautomator", "dump", "/dev/tty", binario=True)
        texto = xml.decode("utf-8", "replace")
        inicio = texto.find("<?xml")
        if inicio == -1:
            # algunas versiones no aceptan /dev/tty: pasar por archivo
            self._adb("shell", "uiautomator", "dump", "/sdcard/vb.xml")
            texto = self._adb("shell", "cat", "/sdcard/vb.xml")
            inicio = texto.find("<?xml")
        if inicio == -1:
            log.warning("No pude volcar la pantalla")
            return self._cache

        try:
            raiz = ET.fromstring(texto[inicio:].split("UI hierchary")[0].strip())
        except ET.ParseError as e:
            log.debug("XML mal formado: %s", e)
            return self._cache

        nodos: list[dict] = []
        for nodo in raiz.iter("node"):
            etiqueta = (nodo.get("text") or "").strip()
            desc = (nodo.get("content-desc") or "").strip()
            visible = nodo.get("visible-to-user", "true") == "true"
            if not visible:
                continue

            caja = nodo.get("bounds") or ""
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", caja)
            if not m:
                continue
            x1, y1, x2, y2 = (int(v) for v in m.groups())
            ancho, alto = x2 - x1, y2 - y1
            if ancho <= 0 or alto <= 0:
                continue

            nodos.append({
                "etiqueta": etiqueta or desc,
                "texto": etiqueta,
                "aria": desc,
                "clickable": nodo.get("clickable") == "true",
                "clase": nodo.get("class", ""),
                "x": x1 + ancho // 2,
                "y": y1 + alto // 2,
                "w": ancho,
                "h": alto,
                "area": ancho * alto,
            })

        self._cache = nodos
        self._cache_ts = time.time()
        return nodos

    def texto_pagina(self) -> str:
        return "\n".join(n["texto"] for n in self._volcar() if n["texto"])

    def texto_profundo(self) -> str:
        partes = []
        for n in self._volcar():
            if n["texto"]:
                partes.append(n["texto"])
            if n["aria"] and n["aria"] != n["texto"]:
                partes.append(n["aria"])
        return "\n".join(partes)

    def html_pagina(self) -> str:
        return ""          # en Android no hay HTML: el árbol ya es el contenido

    def captura(self, ruta: str) -> str:
        datos = self._adb("exec-out", "screencap", "-p", binario=True)
        if datos:
            Path(ruta).write_bytes(datos)
        return ruta

    def listar_controles(self) -> list[dict]:
        vistos = set()
        salida = []
        for n in sorted(self._volcar(), key=lambda n: n["area"]):
            etiqueta = n["etiqueta"]
            if not etiqueta or len(etiqueta) > 60 or etiqueta in vistos:
                continue
            vistos.add(etiqueta)
            salida.append(n)
        return salida

    # ------------------------------------------------------------------
    # Toques
    # ------------------------------------------------------------------
    def _prohibido(self, entrada: str) -> bool:
        e = entrada.lower()
        for veto in self.botones.prohibidos:
            v = veto.lower().strip()
            if not v:
                continue
            if v == e or (v in e and len(e) < len(v) + 12):
                return True
        return False

    def click_coord(self, x: int, y: int) -> bool:
        self._adb("shell", "input", "tap", str(int(x)), str(int(y)))
        self._cache_ts = 0.0          # la pantalla cambió: invalidar caché
        return True

    def deslizar(self, arriba: bool = False) -> None:
        medio_x = self._ancho // 2 or 360
        alto = self._alto or 640
        if arriba:
            self._adb("shell", "input", "swipe", str(medio_x), str(int(alto * 0.3)),
                      str(medio_x), str(int(alto * 0.7)), "300")
        else:
            self._adb("shell", "input", "swipe", str(medio_x), str(int(alto * 0.7)),
                      str(medio_x), str(int(alto * 0.3)), "300")
        self._cache_ts = 0.0

    def _buscar_nodo(self, texto: str) -> dict | None:
        """Nodo cuyo texto o descripción contenga lo buscado."""
        objetivo = _sin_acentos(texto.lower()).strip()
        if not objetivo:
            return None
        candidatos = []
        for n in self._volcar():
            etiqueta = _sin_acentos(n["etiqueta"].lower())
            if objetivo == etiqueta:
                candidatos.append((0, n))
            elif objetivo in etiqueta:
                candidatos.append((1, n))
        if not candidatos:
            return None
        # exactos primero; a igualdad, el clicable y más pequeño
        candidatos.sort(key=lambda t: (t[0], not t[1]["clickable"], t[1]["area"]))
        return candidatos[0][1]

    def click_texto(self, textos, timeout_ms: int = 2500) -> bool:
        if isinstance(textos, str):
            textos = [textos]
        for entrada in textos:
            entrada = (entrada or "").strip()
            if not entrada:
                continue
            if self._prohibido(entrada):
                log.warning("Me niego a tocar «%s»: está prohibido", entrada)
                continue
            # los selectores CSS no aplican en Android: se ignoran en silencio
            if entrada.startswith((".", "#", "[", "//")) or entrada.lower().startswith(
                ("css=", "xpath=")
            ):
                continue
            if entrada.lower().startswith("coord="):
                try:
                    x, y = (int(v) for v in entrada.split("=", 1)[1].split(","))
                    return self.click_coord(x, y)
                except ValueError:
                    continue

            nodo = self._buscar_nodo(entrada)
            if nodo:
                log.info("Toque en «%s» (%d, %d)", entrada, nodo["x"], nodo["y"])
                return self.click_coord(nodo["x"], nodo["y"])
        return False

    def click_forzado(self, entrada: str) -> bool:
        return self.click_texto([entrada])

    def click_robusto(self, entrada: str) -> bool:
        return self.click_texto([entrada])

    # ------------------------------------------------------------------
    # Búsquedas de alto nivel
    # ------------------------------------------------------------------
    def buscar_por_palabras(self, palabras: list[str]) -> dict | None:
        claves = [_sin_acentos(p.lower().strip()) for p in palabras if p and p.strip()]
        mejor = None
        for n in self._volcar():
            etiqueta = _sin_acentos(n["etiqueta"].lower())
            if not etiqueta or len(etiqueta) > 120:
                continue
            if not any(c in etiqueta for c in claves):
                continue
            if mejor is None or n["area"] < mejor["area"]:
                mejor = n
        return mejor

    def click_por_palabras(self, palabras: list[str]) -> bool:
        hallado = self.buscar_por_palabras(palabras)
        if not hallado or self._prohibido(hallado["etiqueta"]):
            return False
        log.info("Toque por palabras en «%s»", hallado["etiqueta"][:40])
        return self.click_coord(hallado["x"], hallado["y"])

    def buscar_opciones_letra(self, letras: list[str]) -> list[dict]:
        validas = {l.strip().upper() for l in letras if l and l.strip()}
        salida = []
        for n in self._volcar():
            limpia = n["etiqueta"].strip().rstrip(".)-").upper()
            if limpia in validas and len(n["etiqueta"].strip()) <= 3:
                salida.append({**n, "letra": limpia, "redondo": abs(n["w"] - n["h"]) < 20})
        return sorted(salida, key=lambda n: n["x"])

    def buscar_elemento_grande(self) -> dict | None:
        ancho = self._ancho or 1
        alto = self._alto or 1
        mejor = None
        for n in self._volcar():
            if n["w"] < 60 or n["h"] < 60:
                continue
            if n["w"] > ancho * 0.95 and n["h"] > alto * 0.85:
                continue
            if not n["clickable"]:
                continue
            if mejor is None or n["area"] > mejor["area"]:
                mejor = n
        return mejor

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------
    def existe_control(self, entrada: str, timeout_ms: int = 600) -> bool:
        entrada = (entrada or "").strip()
        if not entrada or entrada.lower().startswith("coord="):
            return False
        if entrada.startswith((".", "#", "[", "//")) or entrada.lower().startswith(
            ("css=", "xpath=")
        ):
            return False
        return self._buscar_nodo(entrada) is not None

    def existe_texto(self, texto: str) -> bool:
        return self.existe_control(texto)

    def alguno_visible(self, entradas: list[str]) -> bool:
        return any(self.existe_control(e) for e in entradas)

    def esperar_control(self, entradas: list[str], timeout_s: float = 60.0) -> bool:
        limite = time.time() + timeout_s
        while time.time() < limite:
            self._cache_ts = 0.0
            if self.alguno_visible(entradas):
                return True
            time.sleep(0.5)
        return False

    def cerrar_modales(self) -> None:
        for t in self.botones.cerrar_modal:
            if t and self.existe_control(t):
                self.click_texto([t])

    def iniciar_microfono(self) -> bool:
        return self.click_texto(self.botones.iniciar_microfono)

    def detener_microfono(self) -> bool:
        return self.click_texto(self.botones.detener_microfono)

    def siguiente(self) -> bool:
        return self.click_texto(self.botones.siguiente)

    def esperar_cuenta_regresiva(self) -> float:
        if self.cuenta.fallback_s > 0:
            time.sleep(self.cuenta.fallback_s)
        return time.time()
