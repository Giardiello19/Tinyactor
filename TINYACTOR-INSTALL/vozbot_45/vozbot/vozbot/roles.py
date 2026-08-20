"""Reconoce qué papel cumple cada control de la pantalla.

En vez de buscar textos exactos, se puntúa cada control por lo que ES: lo que
dice, su forma, su color y dónde está. Así el mismo código sirve para el
diseño de escritorio y para el de móvil, aunque cambien las etiquetas y las
posiciones.
"""
from __future__ import annotations

import logging
import unicodedata

log = logging.getLogger("vozbot.roles")


def _plano(t: str) -> str:
    t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    return " " + " ".join(t.lower().split()) + " "


# Palabras por papel, con su peso. Las frases largas y específicas pesan más
# que las palabras sueltas, porque son menos ambiguas.
LEXICO: dict[str, list[tuple[str, int]]] = {
    "grabar": [
        ("empezar a grabar", 10), ("iniciar grabacion", 10), ("grabar", 8),
        ("record", 8), ("microfono", 7), ("hablar", 6), ("empezar", 4),
    ],
    "detener": [
        ("detener grabacion", 12), ("terminar grabacion", 12), ("parar grabacion", 12),
        ("stop recording", 11), ("detener", 8), ("terminar", 7), ("parar", 7),
        ("finalizar", 7), ("stop", 6), ("pausa", 5),
    ],
    # Todo lo que continúa la partida. Las de monedas van primero: son las
    # que aparecen al cerrar una ronda y hay que pulsarlas siempre.
    "avanzar": [
        # «Seguir adelante» aparece junto a «Volver a grabar» cuando el juego
        # da la toma por válida: se prefiere siempre continuar.
        ("seguir adelante", 22), ("sigue adelante", 22), ("sigamos adelante", 22),
        ("seguir", 22), ("sigue", 22), ("sigamos", 22), ("adelante", 22),
        ("continuar de todos modos", 22), ("de todos modos", 22),
        ("continuar igual", 22), ("continuemos", 22), ("avanzar", 22),
        ("seguir asi", 21), ("sigue asi", 21), ("asi esta bien", 21),
        ("dejarlo asi", 21), ("esta bien asi", 21), ("me quedo con esta", 21),
        ("aceptar toma", 21), ("usar esta toma", 21), ("quedarme con esta", 21),
        ("seguir ganando monedas", 20), ("seguir ganando", 19),
        ("continua ganando", 19), ("continuar ganando", 19),
        ("sigue ganando", 19), ("quiero ganar", 18), ("vamos a ganar", 18),
        ("ganar mas monedas", 18), ("mas monedas", 17), ("ganar monedas", 18),
        ("monedas", 16),
        ("seguir jugando", 15), ("continuar jugando", 15), ("sigue jugando", 15),
        ("continuar juego", 15), ("seguir con el juego", 15), ("volver a jugar", 14),
        ("jugar otra vez", 14), ("siguiente ronda", 14), ("otra ronda", 14),
        ("estoy listo", 11), ("entendido", 11), ("continuar", 10),
        ("siguiente", 10), ("comenzar", 9), ("adelante", 9), ("vamos", 9),
        ("empezar", 8), ("listo", 7), ("de acuerdo", 6), ("aceptar", 5),
        ("continue", 8), ("next", 7), ("keep playing", 14),
    ],
    "descartar": [
        ("quizas la proxima", 12), ("ahora no", 10), ("mas tarde", 10),
        ("no gracias", 10), ("omitir", 8), ("saltar", 8), ("cerrar", 4),
    ],
    # El aviso de repetir la toma, en todas sus formas.
    "reintentar": [
        ("regrabar esta toma", 20), ("regrabar esta ronda", 20),
        ("grabar de nuevo", 19), ("volver a grabar", 19), ("grabar otra vez", 19),
        ("repetir grabacion", 18), ("repetir la toma", 18), ("repetir toma", 18),
        ("intentar de nuevo", 17), ("volver a intentar", 17), ("otra toma", 16),
        ("regrabar", 15), ("reintentar", 14), ("repetir", 12),
        ("try again", 15), ("record again", 15), ("retry", 13),
    ],
}

# Nunca se pulsan. OJO: solo lo que saca del juego o lleva a OTRO modo.
# El color no cuenta: «SEGUIR GANANDO MONEDAS» es amarillo y hay que pulsarlo
# siempre, porque continúa la partida.
VETADOS = [
    # salir de la partida
    "salir", "abandonar", "cancelar", "cerrar sesion", "volver", "atras",
    "regresar", "menu principal", "rendirse", "exit", "quit",
    # otro modo de juego
    "conversacion",            # cubre «conversaciones», «modo conversaciones»
    "echale un vistazo", "un vistazo",
    # controles que no avanzan nada
    "silencio", "mute",
    # aparece junto al botón de grabar: reinicia la toma en curso, no avanza
    "reiniciar", "restart", "empezar de nuevo",
]


# «Volver» normalmente saca de la partida, pero «volver a grabar» o «volver a
# jugar» la continúan. Estas frases mandan sobre el veto.
EXCEPCIONES = [
    "volver a grabar", "volver a intentar", "volver a jugar", "volver a empezar",
    "volver a la partida", "volver a probar", "volver a leer",
]


def vetado(etiqueta: str) -> bool:
    """¿Es un control que nunca debemos pulsar?

    La comparación es por contenido, no por palabra exacta, para que
    «Te presentamos Conversaciones» o «Probar Conversaciones» queden
    cubiertos sin listar cada variante. Las excepciones se comprueban
    primero: son casos donde una palabra vetada forma parte de una acción
    que sí continúa el juego.
    """
    e = _plano(etiqueta)
    if any(x in e for x in EXCEPCIONES):
        return False
    return any(v in e for v in VETADOS)


def puntuar(control: dict, papel: str) -> int:
    """Cuánto encaja este control con ese papel. Cero o menos = descartado."""
    etiqueta = control.get("etiqueta", "")
    if vetado(etiqueta):
        return -100

    e = _plano(etiqueta)
    puntos = 0

    for palabra, peso in LEXICO.get(papel, []):
        if f" {palabra} " in e:
            puntos = max(puntos, peso + 4)      # coincidencia limpia
        elif palabra in e:
            puntos = max(puntos, peso)

    # --- rasgos visuales, útiles cuando no hay texto ---
    if papel == "grabar":
        # el botón del micrófono es un círculo grande, centrado y abajo.
        # Puede ser rojo entero o blanco con un punto rojo dentro.
        if control.get("redondo") and control.get("w", 0) >= 40:
            puntos += 6
            if control.get("rojizo") or control.get("destacado"):
                puntos += 4
            if control.get("centrado"):
                puntos += 3
            if control.get("rel_y", 0) > 0.5:
                puntos += 3
    elif papel == "detener":
        if control.get("redondo") and control.get("w", 0) >= 40 and control.get("centrado"):
            puntos += 4
    elif papel == "avanzar":
        # «Empezar a grabar» NO es avanzar: es el propio botón de grabar.
        # Tratarlo como avance lo pulsaba dos veces, iniciando y deteniendo
        # la grabación en el mismo instante.
        if any(x in e for x in (" grabar ", " grabacion ", " microfono ", " record ")):
            return 0
        # los de avance suelen ir abajo y ser anchos
        if puntos and control.get("rel_y", 0) > 0.6:
            puntos += 2
        if puntos and control.get("w", 0) > 140:
            puntos += 1
        # cualquier cosa con «monedas» o «ganar» continúa la partida: prioridad
        if "monedas" in e or " ganar " in e or " ganando " in e:
            puntos += 6
    elif papel == "reintentar":
        # el aviso de repetir suele ser un botón grande y centrado
        if puntos and control.get("w", 0) > 140:
            puntos += 2

    return puntos


def clasificar(inventario: list[dict], papel: str, minimo: int = 5) -> list[dict]:
    """Controles que pueden cumplir ese papel, del más probable al menos."""
    candidatos = []
    for c in inventario:
        p = puntuar(c, papel)
        if p >= minimo:
            candidatos.append({**c, "puntos": p})
    candidatos.sort(key=lambda c: (-c["puntos"], -c.get("rel_y", 0)))
    return candidatos


def mejor(inventario: list[dict], papel: str, minimo: int = 5) -> dict | None:
    lista = clasificar(inventario, papel, minimo)
    return lista[0] if lista else None
