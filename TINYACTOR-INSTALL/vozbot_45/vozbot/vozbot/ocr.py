"""OCR de pantalla (mss + PaddleOCR).

Dos usos:
  1. Fallback cuando el DOM no entrega la instrucción (canvas, iframes, juegos).
  2. Localizar campos y botones dentro de TU app .py de generación de voz.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import mss
import numpy as np

from .config import Ocr

log = logging.getLogger("vozbot.ocr")


@dataclass
class Hallazgo:
    texto: str
    x: int  # centro, en píxeles de pantalla
    y: int
    confianza: float


class LectorPantalla:
    def __init__(self, cfg: Ocr):
        self.cfg = cfg
        self._ocr = None
        self._sin_ocr = False        # ya avisamos de que no está instalado

    def _motor(self):
        """Carga PaddleOCR tolerando los cambios entre sus versiones.

        La 3.x quitó parámetros que la 2.x exigía (use_gpu, show_log,
        use_angle_cls), así que se prueban varias combinaciones de la más
        completa a la más simple.
        """
        if self._ocr is not None:
            return self._ocr

        from paddleocr import PaddleOCR  # import perezoso: tarda en cargar

        intentos = [
            {"use_angle_cls": True, "lang": self.cfg.idioma,
             "use_gpu": self.cfg.usar_gpu, "show_log": False},   # 2.x
            {"use_angle_cls": True, "lang": self.cfg.idioma},     # intermedia
            {"lang": self.cfg.idioma},                            # 3.x
        ]
        ultimo = None
        for kw in intentos:
            try:
                self._ocr = PaddleOCR(**kw)
                log.info("PaddleOCR listo (%s)", ", ".join(kw))
                return self._ocr
            except (TypeError, ValueError) as e:
                ultimo = e
                continue
        raise RuntimeError(f"No pude iniciar PaddleOCR: {ultimo}")

    # ------------------------------------------------------------------
    def capturar(self, region: dict | None = None, guardar_en: str | Path | None = None):
        with mss.mss() as sct:
            objetivo = region or sct.monitors[self.cfg.monitor]
            img = np.array(sct.grab(objetivo))[:, :, :3][:, :, ::-1]  # BGRA → RGB
        if guardar_en:
            from PIL import Image

            Image.fromarray(img).save(str(guardar_en))
        return img, (objetivo["left"], objetivo["top"])

    def leer(self, region: dict | None = None) -> list[Hallazgo]:
        if self._sin_ocr:
            return []
        try:
            img, (ox, oy) = self.capturar(region)
        except Exception as e:
            self._sin_ocr = True
            log.info("No puedo capturar la pantalla (%s); sigo sin OCR", type(e).__name__)
            return []
        if self._sin_ocr:
            return []
        try:
            motor = self._motor()
            # la 3.x quitó el parámetro cls y renombró el método
            try:
                crudo = motor.ocr(img, cls=True)
            except TypeError:
                try:
                    crudo = motor.ocr(img)
                except (TypeError, AttributeError):
                    crudo = motor.predict(img)
        except ImportError:
            # No está instalado. Es opcional: se avisa UNA vez y se sigue.
            self._sin_ocr = True
            log.info(
                "OCR no instalado (opcional). Si lo necesitas: "
                "pip install -r requirements-ocr.txt"
            )
            return []
        except Exception as e:
            self._sin_ocr = True
            log.warning("OCR no disponible (%s); sigo sin él", e)
            return []

        hallazgos: list[Hallazgo] = []
        for bloque in crudo or []:
            # la versión 3.x devuelve diccionarios en vez de listas
            if isinstance(bloque, dict):
                cajas = bloque.get("dt_polys") or bloque.get("boxes") or []
                textos = bloque.get("rec_texts") or []
                confs = bloque.get("rec_scores") or []
                for caja, texto, conf in zip(cajas, textos, confs):
                    xs = [p[0] for p in caja]
                    ys = [p[1] for p in caja]
                    hallazgos.append(
                        Hallazgo(
                            texto=str(texto).strip(),
                            x=int(ox + sum(xs) / len(xs)),
                            y=int(oy + sum(ys) / len(ys)),
                            confianza=float(conf),
                        )
                    )
                continue
            for caja, (texto, conf) in bloque or []:
                xs = [p[0] for p in caja]
                ys = [p[1] for p in caja]
                hallazgos.append(
                    Hallazgo(
                        texto=texto.strip(),
                        x=int(ox + sum(xs) / 4),
                        y=int(oy + sum(ys) / 4),
                        confianza=float(conf),
                    )
                )
        return hallazgos

    def texto_completo(self, region: dict | None = None) -> str:
        return "\n".join(h.texto for h in self.leer(region))

    def buscar(self, objetivo: str, region: dict | None = None, minimo: float = 0.55) -> Hallazgo | None:
        obj = objetivo.strip().lower()
        mejores = [
            h for h in self.leer(region) if obj in h.texto.lower() and h.confianza >= minimo
        ]
        if not mejores:
            return None
        return min(mejores, key=lambda h: len(h.texto))

    def esperar(
        self, objetivo: str, timeout_s: float = 30.0, region: dict | None = None
    ) -> Hallazgo | None:
        limite = time.time() + timeout_s
        while time.time() < limite:
            h = self.buscar(objetivo, region)
            if h:
                return h
            time.sleep(0.6)
        return None
