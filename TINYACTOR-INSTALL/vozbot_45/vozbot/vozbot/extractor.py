"""Lee la página y saca dos cosas: la emoción pedida y el texto entre comillas.

Trabaja sobre el texto renderizado y, si no encuentra nada, sobre el HTML
crudo (atributos data-*, JSON embebido, etc.). Si aun así falla, el
orquestador delega en OCR + modelo de razonamiento.
"""
from __future__ import annotations

import html
import logging
import re
import unicodedata

from .config import Extraccion

log = logging.getLogger("vozbot.extractor")

# frases típicas de instrucción en juegos de lectura
PATRONES_INSTRUCCION = [
    r"(?:lee|leé|lea|repite|repita|di|pronuncia|graba)[^.\n]{0,120}",
    r"(?:read|say|repeat)[^.\n]{0,120}",
]

# Todas las variantes de comillas que se ven en la práctica.
PATRONES_COMILLAS = [
    r"[\u201C\u201E]([^\u201C\u201D\u201E]{2,400})[\u201D\u201C]",  # “ ” „
    r'"([^"]{2,400})"',                                              # rectas dobles
    r"[\u2018]([^\u2018\u2019]{2,400})[\u2019]",                     # ' '
    r"[\u00AB]([^\u00AB\u00BB]{2,400})[\u00BB]",                     # « »
    r"[\u300C]([^\u300C\u300D]{2,400})[\u300D]",                     # 「 」
    r"'([^']{4,400})'",                                              # rectas simples (más estricto)
]

# Cuando no hay comillas: frases que introducen el texto a leer.
PATRONES_TRAS_DOS_PUNTOS = [
    r"(?:lee|leé|lea|repite|repita|di|pronuncia|dilo|léelo)[^:\n]{0,60}:\s*(.+)",
    r"(?:read|say|repeat)[^:\n]{0,60}:\s*(.+)",
    r"(?:frase|texto|oración|palabra)[^:\n]{0,30}:\s*(.+)",
]


def _sin_acentos(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


class ExtractorGuion:
    def __init__(self, cfg: Extraccion):
        self.cfg = cfg
        self._emociones = [(e, _sin_acentos(e.lower())) for e in cfg.emociones]

    # ------------------------------------------------------------------
    def emocion(self, texto: str, ultima: bool = False) -> str:
        """Emoción del catálogo que aparezca como palabra completa.

        Por defecto la primera; con `ultima=True`, la más cercana al final,
        que es la que importa cuando se mira el trozo justo encima de la frase.
        """
        plano = _sin_acentos(texto.lower())
        mejor = self.cfg.emocion_por_defecto
        pos_mejor = -1 if ultima else len(plano) + 1
        for original, normal in self._emociones:
            for m in re.finditer(rf"\b{re.escape(normal)}\b", plano):
                if (ultima and m.start() > pos_mejor) or (not ultima and m.start() < pos_mejor):
                    mejor, pos_mejor = original, m.start()
        return mejor

    def texto_entre_comillas(self, texto: str) -> str:
        """Devuelve el fragmento entrecomillado más largo y plausible.

        Acepta comillas rectas, tipográficas, angulares y japonesas, porque
        cada juego usa las suyas y a veces las mezcla.
        """
        candidatos: list[str] = []
        patrones = list(self.cfg.patrones_comillas) + PATRONES_COMILLAS
        for patron in patrones:
            try:
                candidatos += [m.group(1).strip() for m in re.finditer(patron, texto)]
            except re.error:
                continue

        candidatos = [
            html.unescape(c)
            for c in candidatos
            if len(c.strip()) >= self.cfg.min_caracteres and not c.strip().startswith("<")
        ]
        if not candidatos:
            return ""
        # el más largo suele ser la frase a leer, no una palabra suelta de UI
        return max(candidatos, key=len)

    def instruccion(self, texto: str) -> str:
        for patron in PATRONES_INSTRUCCION:
            m = re.search(patron, texto, re.IGNORECASE)
            if m:
                return m.group(0).strip()
        return texto.strip().split("\n")[0][:160]

    # ------------------------------------------------------------------
    def tras_dos_puntos(self, texto: str) -> str:
        """Respaldo: «Lee esta frase: el barco zarpó» → «el barco zarpó»."""
        for patron in PATRONES_TRAS_DOS_PUNTOS:
            m = re.search(patron, texto, re.IGNORECASE)
            if m:
                frase = m.group(1).strip().strip("\"“”'‘’«»")
                if len(frase) >= max(self.cfg.min_caracteres, 3):
                    return frase.split("\n")[0].strip()
        return ""

    def emocion_cerca_de(self, texto: str, frase: str) -> str:
        """Emoción escrita JUSTO ANTES de una frase dada.

        En las rondas de repetición el juego muestra la misma línea sin
        comillas y encima la nueva acotación. Mirar solo el trozo anterior
        evita quedarse con la emoción de la ronda pasada.
        """
        if not frase:
            return self.emocion(texto)
        pos = texto.lower().find(frase[:60].lower())
        if pos <= 0:
            return self.emocion(texto)
        anterior = texto[max(0, pos - 400) : pos]
        encontrada = self.emocion(anterior, ultima=True)
        # si no había nada arriba, se busca en toda la pantalla
        return encontrada if encontrada != self.cfg.emocion_por_defecto else self.emocion(texto)

    def es_frase_completa(self, texto: str) -> bool:
        """¿Es una línea de guion, o solo una palabra entrecomillada?

        El juego marca palabras sueltas entre comillas para pedir énfasis
        («dilo alargando "nunca"»). Eso no debe sustituir a la frase que se
        está trabajando.
        """
        if not texto:
            return False
        return len(texto.split()) >= self.cfg.min_palabras_frase_nueva

    # Textos de interfaz que nunca son la frase a leer.
    UI_CONOCIDA = [
        "entendido", "vamos", "grabar", "continuar", "siguiente", "listo",
        "emocion", "emoción", "toma", "salir", "silencio", "estoy listo",
        "tu voz natural", "voz natural", "seguir ganando", "monedas",
        "regrabar", "quizas la proxima", "quizás la próxima", "como funciona",
        "cómo funciona", "lecturas", "escenas para actuar",
    ]

    def emocion_etiquetada(self, texto: str) -> str:
        """Lee la emoción de la pantalla que la anuncia.

        El juego muestra una pantalla con la palabra EMOCIÓN y debajo el
        nombre —«Sereno», «Nostálgico»— que puede no estar en el catálogo.
        Se toma lo que diga la pantalla, sin exigir que lo conozcamos.
        """
        lineas = [l.strip() for l in (texto or "").splitlines() if l.strip()]
        plano = [_sin_acentos(l.lower()) for l in lineas]

        # «Tu Voz Natural» = las dos primeras tomas, sin actuar
        for l in plano:
            if "voz natural" in l:
                return "neutral"

        # Cabecera de la pantalla de grabación: «🎭 Sereno» sobre la
        # descripción de la escena. Se busca tras el marcador de toma.
        if self.numero_toma(texto):
            for i, l in enumerate(plano):
                if not re.search(r"\d\s*/\s*\d|toma", l):
                    continue
                for j in range(i + 1, min(i + 3, len(lineas))):
                    limpia = re.sub(r"^[^\w¿¡]+|[^\w.!?]+$", "", lineas[j]).strip()
                    if not limpia or len(limpia.split()) > 3 or len(limpia) > 26:
                        continue
                    if any(u in _sin_acentos(limpia.lower())
                           for u in (_sin_acentos(x) for x in self.UI_CONOCIDA)):
                        continue
                    return limpia
                break

        for i, l in enumerate(plano):
            if l in ("emocion", "la emocion", "emocion:") or l.startswith("emocion"):
                # el nombre va en esa misma línea tras los dos puntos, o debajo
                if ":" in lineas[i]:
                    valor = lineas[i].split(":", 1)[1].strip()
                    if valor:
                        return valor
                for j in range(i + 1, min(i + 3, len(lineas))):
                    valor = lineas[j].strip()
                    if 1 <= len(valor.split()) <= 3 and len(valor) <= 30:
                        return valor
        return ""

    def frase_sin_comillas(self, texto: str) -> str:
        """La línea larga de la pantalla de grabación.

        El juego ya no entrecomilla el guion: es simplemente el bloque de
        texto más largo, por debajo de la tarjeta con la emoción y la
        situación. Se descartan los textos de interfaz y las descripciones
        cortas.
        """
        candidatas = []
        for linea in (texto or "").splitlines():
            linea = " ".join(linea.split())
            if not linea or len(linea.split()) < max(self.cfg.min_palabras_frase_nueva, 6):
                continue
            plano = _sin_acentos(linea.lower())
            if any(u in plano for u in (_sin_acentos(x) for x in self.UI_CONOCIDA)):
                continue
            candidatas.append(linea)
        return max(candidatas, key=len) if candidatas else ""

    def frase_de_grabacion(self, inventario: list[dict]) -> str:
        """El guion de la pantalla de grabación, por su tamaño de letra.

        En esta pantalla hay tres textos: la emoción (arriba, en la tarjeta),
        la situación (debajo, en cursiva y pequeña) y el guion (en el centro,
        en letra grande). El guion es el de mayor tamaño de fuente; a igualdad,
        el más largo. Así se reconoce aunque sea corto y no lleve comillas.
        """
        candidatos = []
        for c in inventario or []:
            texto = " ".join((c.get("etiqueta") or "").split())
            if not texto or len(texto.split()) < 3 or len(texto) > 500:
                continue
            if not c.get("hojas", True):        # solo el nodo que tiene el texto
                continue
            if c.get("cursiva"):                # la situación va en cursiva
                continue
            plano = _sin_acentos(texto.lower())
            if any(u in plano for u in (_sin_acentos(x) for x in self.UI_CONOCIDA)):
                continue
            candidatos.append((float(c.get("tam") or 0), len(texto), texto))

        if not candidatos:
            return ""
        candidatos.sort(reverse=True)
        return candidatos[0][2]

    def numero_toma(self, texto: str) -> tuple[int, int] | None:
        """Lee «Toma 1 de 6» y devuelve (1, 6). None si no aparece."""
        plano = _sin_acentos(texto.lower())
        patrones = [
            r"toma\s*(\d+)\s*(?:de|/)\s*(\d+)",
            r"(\d+)\s*(?:de|/)\s*(\d+)\s*tomas?",
            r"take\s*(\d+)\s*(?:of|/)\s*(\d+)",
            r"ronda\s*(\d+)\s*(?:de|/)\s*(\d+)",
            r"\b([1-9]\d?)\s*/\s*([1-9]\d?)\b",   # el marcador «2/6» suelto
        ]
        for patron in patrones:
            m = re.search(patron, plano)
            if m:
                try:
                    return int(m.group(1)), int(m.group(2))
                except ValueError:
                    continue
        return None

    def es_pantalla_de_instruccion(self, texto: str) -> bool:
        """La palabra «emoción» anuncia el guion: hay que avanzar, no leer."""
        plano = _sin_acentos(texto.lower())
        claves = ["emocion", "acotacion", "instruccion", "interpreta", "actua",
                  "tono", "sentimiento"]
        return any(re.search(rf"\b{c}", plano) for c in claves)

    def contiene_frase(self, texto: str, frase: str, minimo: int = 12) -> bool:
        """¿Sigue esa misma frase en pantalla, aunque ya no lleve comillas?"""
        if not frase or len(frase) < minimo:
            return False
        muestra = _sin_acentos(frase[:80].lower()).strip()
        return muestra in _sin_acentos(texto.lower())

    def extraer(self, texto_visible: str, html_crudo: str = "", texto_profundo: str = "") -> dict:
        """Resultado: {instruccion, emocion, texto_a_leer, fuente}.

        Prueba en cascada: texto visible → recolección profunda (shadow DOM,
        iframes, atributos) → HTML crudo → frase tras dos puntos.
        """
        frase = self.texto_entre_comillas(texto_visible)
        fuente = "dom-texto"

        if not frase and texto_profundo:
            frase = self.texto_entre_comillas(texto_profundo)
            fuente = "dom-profundo"

        if not frase and html_crudo:
            limpio = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html_crudo, flags=re.S)
            limpio = re.sub(r"<[^>]+>", " ", limpio)
            frase = self.texto_entre_comillas(html.unescape(limpio))
            fuente = "dom-html"

        if not frase:
            for origen, etiqueta in (
                (texto_visible, "sin-comillas"),
                (texto_profundo, "sin-comillas-profundo"),
            ):
                frase = self.tras_dos_puntos(origen or "")
                if frase:
                    fuente = etiqueta
                    break

        # Formato nuevo del juego: el guion va suelto, sin comillas ni
        # «lee esto:» delante. Solo se acepta en la pantalla de grabación,
        # que es la única con marcador de toma; así las pantallas de ayuda
        # no se confunden con el guion.
        if not frase and self.numero_toma(f"{texto_visible}\n{texto_profundo}"):
            for origen, etiqueta in (
                (texto_visible, "linea-larga"),
                (texto_profundo, "linea-larga-profunda"),
            ):
                frase = self.frase_sin_comillas(origen or "")
                if frase:
                    fuente = etiqueta
                    break

        completo = "\n".join(t for t in (texto_visible, texto_profundo) if t)

        # La emoción anunciada en pantalla manda sobre la del catálogo.
        etiquetada = self.emocion_etiquetada(completo)

        if not frase:
            return {
                "instruccion": self.instruccion(completo),
                "emocion": etiquetada or self.emocion(completo),
                "texto_a_leer": "",
                "fuente": "vacio",
            }

        return {
            "instruccion": self.instruccion(completo),
            "emocion": etiquetada or self.emocion(completo),
            "texto_a_leer": frase,
            "fuente": fuente,
        }
