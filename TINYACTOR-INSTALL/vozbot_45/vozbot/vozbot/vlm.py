"""Modelo de razonamiento con visión: captura + texto → Guion (JSON estricto).

Solo se invoca cuando el DOM no basta (o si `solo_si_falla_dom: false`).
Soporta Anthropic, OpenAI y cualquier endpoint compatible con OpenAI
(LM Studio, Ollama, vLLM) para trabajar 100% en local si lo prefieres.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path

import httpx
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import Guion, Razonamiento

log = logging.getLogger("vozbot.vlm")

SISTEMA = """Eres el copiloto de un bot que automatiza un juego de lectura en el navegador.
Recibes una captura de pantalla y el texto extraído (DOM y/u OCR).

Tu trabajo:
1. Identificar la instrucción que la pantalla le da al jugador.
2. Detectar la EMOCIÓN que se pide (alegre, triste, enojado, sorprendido, neutral...).
3. Extraer EXACTAMENTE el texto que va entre comillas: es la frase que hay que leer.
   Cópialo literal, sin las comillas, sin corregir ni traducir nada.
4. Proponer las acciones siguientes para avanzar.

Responde SOLO con un objeto JSON, sin markdown ni explicaciones, con esta forma:
{
  "instruccion_detectada": "string",
  "emocion": "string",
  "texto_a_leer": "string",
  "listo_para_hablar": true/false,
  "acciones": [
    {"tipo": "click_texto|click_coord|generar_y_hablar|esperar|scroll|fin",
     "texto": "", "x": null, "y": null, "segundos": 1.0, "motivo": ""}
  ],
  "confianza": 0.0
}
Usa "click_texto" siempre que el control tenga texto legible; "click_coord" solo
si no hay más remedio. "generar_y_hablar" significa: generar el audio con la app
de voz y reproducirlo por el micrófono. Si la pantalla no pide leer nada todavía,
pon listo_para_hablar en false."""


def _b64(ruta: str | Path) -> str:
    return base64.b64encode(Path(ruta).read_bytes()).decode()


def _json_del_texto(texto: str) -> dict:
    texto = re.sub(r"^```(?:json)?|```$", "", texto.strip(), flags=re.M).strip()
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fin == -1:
        raise ValueError(f"La respuesta no contiene JSON: {texto[:200]}")
    return json.loads(texto[inicio : fin + 1])


class Razonador:
    def __init__(self, cfg: Razonamiento):
        self.cfg = cfg
        self.api_key = os.environ.get(cfg.api_key_env, "")

    # ------------------------------------------------------------------
    def analizar(self, captura: str | Path, texto_contexto: str) -> Guion:
        if not self.cfg.activo:
            return Guion()
        try:
            return self._pedir(str(captura), texto_contexto)
        except Exception as e:
            log.error("El modelo de razonamiento falló: %s", e)
            return Guion(confianza=0.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=8), reraise=True)
    def _pedir(self, captura: str, contexto: str) -> Guion:
        img = _b64(captura)
        prompt = f"Texto extraído de la pantalla:\n---\n{contexto[:6000]}\n---"

        if self.cfg.proveedor == "anthropic":
            crudo = self._anthropic(img, prompt)
        else:
            crudo = self._openai_compatible(img, prompt)

        try:
            return Guion.model_validate(_json_del_texto(crudo))
        except ValidationError as e:
            log.warning("JSON fuera de esquema, reintento: %s", e)
            raise

    # ------------------------------------------------------------------
    def _anthropic(self, img_b64: str, prompt: str) -> str:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.cfg.modelo,
                "max_tokens": 1200,
                "system": SISTEMA,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": img_b64,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            },
            timeout=90,
        )
        r.raise_for_status()
        partes = r.json().get("content", [])
        return "".join(p.get("text", "") for p in partes)

    def _openai_compatible(self, img_b64: str, prompt: str) -> str:
        base = self.cfg.base_url or "https://api.openai.com/v1"
        r = httpx.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.cfg.modelo,
                "messages": [
                    {"role": "system", "content": SISTEMA},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                            },
                        ],
                    },
                ],
                "temperature": 0,
            },
            timeout=90,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
