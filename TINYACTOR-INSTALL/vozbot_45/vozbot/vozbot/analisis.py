"""Análisis acústico: convierte lo que suena en algo que se puede ver.

Pensado para decidir entre dos interpretaciones sin oírlas. Cada medida está
elegida porque distingue rasgos concretos del habla:

  · energía        → volumen y su recorrido
  · sonoridad      → si vibran las cuerdas vocales (un susurro casi no)
  · cruces por cero→ proporción de aire y siseo
  · tono           → grave/agudo y cuánto se mueve
  · ritmo          → sílabas por segundo, pausas, duración

La decisión la toma quien mira: aquí solo se mide.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Segmento:
    """Un tramo continuo de habla, entre silencios."""

    inicio: float
    fin: float
    energia: float
    tono: float

    @property
    def duracion(self) -> float:
        return self.fin - self.inicio


@dataclass
class Analisis:
    duracion: float = 0.0
    energia_media: float = 0.0        # dB
    energia_pico: float = 0.0         # dB
    rango_dinamico: float = 0.0       # dB entre lo más flojo y lo más fuerte
    sonoridad: float = 0.0            # 0-1: cuánta voz con vibración
    aire: float = 0.0                 # 0-1: siseo y aspiración
    tono_medio: float = 0.0           # Hz
    tono_variacion: float = 0.0       # Hz de desviación
    habla: float = 0.0                # 0-1: proporción hablando
    silencio_total: float = 0.0       # segundos
    velocidad: float = 0.0            # segmentos por segundo
    segmentos: list[Segmento] = field(default_factory=list)
    envolvente: np.ndarray = field(default_factory=lambda: np.zeros(0))
    sr: int = 48000

    def resumen(self) -> dict[str, str]:
        """Las medidas en palabras, para leerlas de un vistazo."""
        return {
            "Duración": f"{self.duracion:.1f} s",
            "Volumen medio": f"{self.energia_media:+.1f} dB",
            "Volumen máximo": f"{self.energia_pico:+.1f} dB",
            "Rango dinámico": f"{self.rango_dinamico:.1f} dB",
            "Voz con vibración": f"{self.sonoridad * 100:.0f} %",
            "Aire / siseo": f"{self.aire * 100:.0f} %",
            "Tono medio": f"{self.tono_medio:.0f} Hz" if self.tono_medio else "—",
            "Movimiento del tono": f"±{self.tono_variacion:.0f} Hz" if self.tono_medio else "—",
            "Tiempo hablando": f"{self.habla * 100:.0f} %",
            "Silencio": f"{self.silencio_total:.1f} s",
            "Ritmo": f"{self.velocidad:.1f} tramos/s",
            "Tramos de habla": str(len(self.segmentos)),
        }


def _db(x: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0
    return 20 * np.log10(max(rms, 1e-9))


def _tono(trozo: np.ndarray, sr: int) -> float:
    """Frecuencia fundamental por autocorrelación. 0 si no hay voz sonora."""
    if len(trozo) < sr // 40:
        return 0.0
    x = trozo - np.mean(trozo)
    if np.max(np.abs(x)) < 1e-4:
        return 0.0

    corr = np.correlate(x, x, mode="full")[len(x) - 1 :]
    if corr[0] <= 0:
        return 0.0
    corr = corr / corr[0]

    minimo = int(sr / 400)      # 400 Hz, lo más agudo que buscamos
    maximo = int(sr / 60)       # 60 Hz, lo más grave
    if maximo >= len(corr):
        return 0.0

    ventana = corr[minimo:maximo]
    if not len(ventana):
        return 0.0
    pico = int(np.argmax(ventana)) + minimo
    # sin un pico claro no hay periodicidad: es aire, no voz sonora
    if corr[pico] < 0.3:
        return 0.0
    return sr / pico


def analizar(audio: np.ndarray, sr: int = 48000, umbral_db: float = -45.0) -> Analisis:
    """Mide un fragmento de voz y lo parte en tramos de habla."""
    a = Analisis(sr=sr)
    if audio is None or not len(audio):
        return a

    x = audio.astype(np.float32).reshape(-1)
    a.duracion = len(x) / sr

    # --- envolvente en ventanas de 20 ms ---
    salto = max(1, int(sr * 0.02))
    marcos = [x[i : i + salto] for i in range(0, len(x) - salto + 1, salto)]
    if not marcos:
        return a

    energias = np.array([_db(m) for m in marcos])
    a.envolvente = energias

    fuertes = energias[energias > umbral_db]
    a.energia_media = float(np.mean(fuertes)) if len(fuertes) else -90.0
    a.energia_pico = float(np.max(energias))
    a.rango_dinamico = float(np.percentile(fuertes, 95) - np.percentile(fuertes, 5)) if len(fuertes) > 4 else 0.0

    # --- tramos de habla: dónde hay energía por encima del umbral ---
    activo = energias > umbral_db
    segmentos: list[Segmento] = []
    i = 0
    while i < len(activo):
        if not activo[i]:
            i += 1
            continue
        j = i
        while j < len(activo) and activo[j]:
            j += 1
        ini, fin = i * salto / sr, j * salto / sr
        if fin - ini >= 0.08:            # menos de 80 ms es un chasquido
            trozo = x[i * salto : j * salto]
            segmentos.append(
                Segmento(ini, fin, float(np.mean(energias[i:j])), _tono(trozo, sr))
            )
        i = j

    a.segmentos = segmentos
    hablado = sum(s.duracion for s in segmentos)
    a.habla = hablado / a.duracion if a.duracion else 0.0
    a.silencio_total = max(0.0, a.duracion - hablado)
    a.velocidad = len(segmentos) / a.duracion if a.duracion else 0.0

    # --- tono: media y movimiento, solo de los tramos con voz sonora ---
    tonos = [s.tono for s in segmentos if s.tono > 0]
    if tonos:
        a.tono_medio = float(np.mean(tonos))
        a.tono_variacion = float(np.std(tonos))

    # --- sonoridad: cuánto del habla tiene vibración de cuerdas ---
    con_tono = sum(s.duracion for s in segmentos if s.tono > 0)
    a.sonoridad = con_tono / hablado if hablado else 0.0

    # --- aire: cruces por cero altos = siseo, aspiración, susurro ---
    if hablado:
        trozos = [x[int(s.inicio * sr) : int(s.fin * sr)] for s in segmentos]
        habla = np.concatenate(trozos) if trozos else np.zeros(1)
        cruces = np.mean(np.abs(np.diff(np.sign(habla)))) / 2 if len(habla) > 1 else 0.0
        # calibrado para que la voz normal quede en torno al 25-40 % y un
        # susurro se vaya por encima del 70 %
        a.aire = float(min(1.0, cruces / 0.35))

    return a


def comparar(a: Analisis, b: Analisis) -> list[tuple[str, str, str, str]]:
    """Tabla de comparación: (medida, valor A, valor B, quién destaca)."""
    filas = []
    ra, rb = a.resumen(), b.resumen()

    # para cada medida, hacia dónde apunta la diferencia
    interpretacion = {
        "Volumen medio": ("A más fuerte", "B más fuerte", 2.0, lambda x: x.energia_media),
        "Rango dinámico": ("A más expresiva", "B más expresiva", 3.0, lambda x: x.rango_dinamico),
        "Voz con vibración": ("A más sonora", "B más sonora", 0.12, lambda x: x.sonoridad),
        "Aire / siseo": ("A más susurrada", "B más susurrada", 0.12, lambda x: x.aire),
        "Tono medio": ("A más aguda", "B más aguda", 15.0, lambda x: x.tono_medio),
        "Movimiento del tono": ("A más variada", "B más variada", 10.0, lambda x: x.tono_variacion),
        "Ritmo": ("A más rápida", "B más rápida", 0.4, lambda x: x.velocidad),
        "Silencio": ("A más pausada", "B más pausada", 0.4, lambda x: x.silencio_total),
    }

    for clave in ra:
        nota = ""
        if clave in interpretacion:
            texto_a, texto_b, minimo, sacar = interpretacion[clave]
            va, vb = sacar(a), sacar(b)
            if abs(va - vb) >= minimo:
                nota = texto_a if va > vb else texto_b
        filas.append((clave, ra[clave], rb[clave], nota))
    return filas
