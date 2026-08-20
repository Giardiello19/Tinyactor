#!/usr/bin/env python3
"""Comprueba si el audio llega de verdad al micrófono virtual.

Reproduce un tono por CABLE Input y graba al mismo tiempo desde CABLE Output.
Si el nivel grabado es alto, el cable funciona y el problema está del emulador
hacia adentro. Si es silencio, el problema está en Windows.

    python probar_audio.py
    python probar_audio.py --wav salida/mi_audio.wav
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf


def buscar(fragmento: str, entrada: bool) -> int | None:
    campo = "max_input_channels" if entrada else "max_output_channels"
    frag = fragmento.lower()
    for i, d in enumerate(sd.query_devices()):
        if d[campo] > 0 and frag in d["name"].lower():
            return i
    return None


def nivel_db(x: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(x.astype("float64") ** 2)))
    return 20 * np.log10(max(rms, 1e-9))


def main() -> int:
    p = argparse.ArgumentParser(description="Diagnóstico del cable de audio")
    p.add_argument("--salida", default="CABLE Input", help="dispositivo por donde habla el bot")
    p.add_argument("--entrada", default="CABLE Output", help="dispositivo que oye el emulador")
    p.add_argument("--wav", help="usar este archivo en vez de un tono")
    p.add_argument("--segundos", type=float, default=3.0)
    args = p.parse_args()

    print("\n=== DISPOSITIVOS ===")
    idx_out = buscar(args.salida, entrada=False)
    idx_in = buscar(args.entrada, entrada=True)

    if idx_out is None:
        print(f"  ✗ No encuentro la SALIDA «{args.salida}»")
        print("    Salidas disponibles:")
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0:
                print(f"      {i}: {d['name']}")
        return 1
    print(f"  ✓ Salida  : {sd.query_devices(idx_out)['name']}")

    if idx_in is None:
        print(f"  ✗ No encuentro la ENTRADA «{args.entrada}»")
        print("    Entradas disponibles:")
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                print(f"      {i}: {d['name']}")
        return 1
    print(f"  ✓ Entrada : {sd.query_devices(idx_in)['name']}")

    # --- material de prueba ---
    if args.wav and Path(args.wav).is_file():
        datos, sr = sf.read(args.wav, dtype="float32", always_2d=True)
        datos = datos.mean(axis=1)
        print(f"\n  Usando {Path(args.wav).name} ({len(datos)/sr:.1f}s, {sr} Hz)")
    else:
        sr = 48000
        t = np.linspace(0, args.segundos, int(sr * args.segundos), endpoint=False)
        datos = (0.5 * np.sin(2 * np.pi * 440 * t)).astype("float32")
        print(f"\n  Usando un tono de 440 Hz ({args.segundos:.0f}s)")

    print(f"  Nivel de lo que se envía: {nivel_db(datos):+.1f} dB")

    # --- grabar mientras se reproduce ---
    # Streams independientes: sd.rec() y sd.play() comparten estado interno y
    # se estorban entre sí, devolviendo memoria sin inicializar.
    capturado: list[np.ndarray] = []

    def entrante(trozo, marcos, tiempo, estado):
        capturado.append(trozo.copy())

    print("\n=== PRUEBA ===")
    try:
        micro = sd.InputStream(
            samplerate=sr, device=idx_in, channels=1, dtype="float32", callback=entrante
        )
    except Exception as e:
        print(f"  ✗ No pude abrir la entrada: {e}")
        return 1

    try:
        with micro:
            time.sleep(0.3)                      # margen para que arranque
            altavoz = sd.OutputStream(
                samplerate=sr, device=idx_out, channels=1, dtype="float32"
            )
            with altavoz:
                bloque = 1024
                for i in range(0, len(datos), bloque):
                    altavoz.write(datos[i : i + bloque])
            time.sleep(0.4)                      # cola: latencia del cable
    except Exception as e:
        print(f"  ✗ Fallo durante la prueba: {e}")
        return 1

    if not capturado:
        print("  ✗ No llegó ni una muestra desde el cable")
        return 1

    captado = np.concatenate(capturado).reshape(-1)
    captado = np.nan_to_num(captado, nan=0.0, posinf=0.0, neginf=0.0)
    captado = np.clip(captado, -1.0, 1.0)        # descarta valores imposibles

    db = nivel_db(captado)
    pico = float(np.max(np.abs(captado)))
    print(f"  Muestras grabadas: {len(captado)} ({len(captado)/sr:.1f}s)")

    print(f"  Nivel captado en el cable: {db:+.1f} dB (pico {pico:.3f})")

    print("\n=== VEREDICTO ===")

    # Cordura: el audio real varía. Una señal plana o pegada al tope no es
    # sonido, es un buffer mal leído.
    variacion = float(np.std(captado))
    if variacion < 1e-6 and pico > 0.5:
        print("  ✗ Lo captado no es audio válido (señal plana).")
        print("    Cierra BlueStacks y cualquier programa que use el cable,")
        print("    y vuelve a ejecutar esta prueba.")
        return 1
    if db < -60:
        print("  ✗ SILENCIO. El audio no está llegando al cable.")
        print("    Revisa en Windows:")
        print("      · Sonido → Salida: el volumen de CABLE Input debe estar al 100")
        print("      · Que ningún otro programa tenga tomado el dispositivo")
        print("      · Que VB-CABLE esté bien instalado (reinstala y reinicia)")
        return 1

    if db < -40:
        print("  ~ Llega, pero MUY BAJO. El juego puede no detectarlo.")
        print("    Sube el volumen de CABLE Input al 100 en Windows,")
        print("    y en config.yaml pon  ganancia_db: 6.0")
        return 0

    print("  ✓ EL CABLE FUNCIONA. El audio llega con buen nivel.")
    print("\n  Si aun así el juego no lo detecta, el problema está en el emulador:")
    print("    · BlueStacks → Ajustes → Dispositivos → Micrófono: CABLE Output")
    print("    · Dentro de Android: Ajustes → Apps → Chrome → Permisos → Micrófono")
    print("    · En la web del juego: acepta el permiso de micrófono")
    print("    · Reinicia BlueStacks tras cambiar el micrófono de Windows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
