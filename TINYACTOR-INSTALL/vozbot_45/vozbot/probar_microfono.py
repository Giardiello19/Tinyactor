#!/usr/bin/env python3
"""Mide el audio que realmente le llega al navegador del emulador.

Es la pieza que faltaba: probar_audio.py comprueba el cable en Windows, pero
no dice si BlueStacks se lo está entregando a Chrome. Esto abre el micrófono
DENTRO del navegador, reproduce audio por el cable, y mide el nivel captado.

    python probar_microfono.py
    python probar_microfono.py --wav ruta\\a\\un_audio.wav

Con el emulador conectado (el canal abierto con adb forward).
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import numpy as np
import soundfile as sf

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from vozbot.audio import MicrofonoEmulado  # noqa: E402
from vozbot.config import Config  # noqa: E402

# Escucha el micrófono y guarda el nivel máximo y medio.
MEDIDOR = """
async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const fuente = ctx.createMediaStreamSource(stream);
    const analizador = ctx.createAnalyser();
    analizador.fftSize = 2048;
    fuente.connect(analizador);
    const datos = new Float32Array(analizador.fftSize);

    window.__vbMedida = {pico: 0, suma: 0, n: 0, activo: true};
    const medir = () => {
      if (!window.__vbMedida.activo) { stream.getTracks().forEach(t => t.stop()); return; }
      analizador.getFloatTimeDomainData(datos);
      let p = 0, s = 0;
      for (let i = 0; i < datos.length; i++) {
        const v = Math.abs(datos[i]);
        if (v > p) p = v;
        s += datos[i] * datos[i];
      }
      const m = window.__vbMedida;
      if (p > m.pico) m.pico = p;
      m.suma += Math.sqrt(s / datos.length);
      m.n++;
      requestAnimationFrame(medir);
    };
    medir();
    return {ok: true, etiqueta: (stream.getAudioTracks()[0] || {}).label || "(sin nombre)"};
  } catch (e) {
    return {ok: false, error: e.name + ": " + e.message};
  }
}
"""


def main() -> int:
    p = argparse.ArgumentParser(description="Comprueba el micrófono del navegador")
    p.add_argument("--config", default=str(BASE / "config.yaml"))
    p.add_argument("--wav", help="audio a reproducir; por defecto un tono")
    p.add_argument("--segundos", type=float, default=4.0)
    args = p.parse_args()

    cfg = Config.cargar(args.config)

    from playwright.sync_api import sync_playwright

    print(f"\n=== CONECTANDO A {cfg.navegador.cdp_url} ===")
    with sync_playwright() as pw:
        try:
            navegador = pw.chromium.connect_over_cdp(cfg.navegador.cdp_url)
        except Exception as e:
            print(f"  ✗ No pude conectar: {e}")
            print("    Abre el canal con adb forward y comprueba con curl.")
            return 1

        contexto = navegador.contexts[0]
        pagina = contexto.pages[0] if contexto.pages else contexto.new_page()
        print(f"  ✓ {pagina.url[:70]}")

        print("\n=== ABRIENDO EL MICRÓFONO EN EL NAVEGADOR ===")
        try:
            r = pagina.evaluate(MEDIDOR)
        except Exception as e:
            print(f"  ✗ Fallo al pedir el micrófono: {e}")
            return 1

        if not r.get("ok"):
            print(f"  ✗ El navegador NO dio acceso al micrófono: {r.get('error')}")
            print("\n    Segun el error:")
            print("      NotAllowedError  → la web tiene el permiso denegado.")
            print("                         Toca el candado en la barra y permítelo.")
            print("      NotFoundError    → el emulador no expone ningun microfono.")
            print("                         BlueStacks → Ajustes → Dispositivos → Microfono")
            print("      NotReadableError → otro programa tiene tomado el dispositivo.")
            return 1

        print(f"  ✓ Micrófono abierto: {r.get('etiqueta')}")

        # --- reproducir por el cable ---
        if args.wav and Path(args.wav).is_file():
            ruta = Path(args.wav)
        else:
            ruta = Path("/tmp/vb_tono.wav") if sys.platform != "win32" else Path("vb_tono.wav")
            sr = 22050
            t = np.linspace(0, args.segundos, int(sr * args.segundos), endpoint=False)
            tono = 0.5 * np.sin(2 * np.pi * 300 * t) + 0.3 * np.sin(2 * np.pi * 600 * t)
            sf.write(str(ruta), tono.astype("float32"), sr)

        mic = MicrofonoEmulado(cfg.microfono_virtual)
        if mic.dispositivo is None:
            print(f"\n  ✗ No encuentro «{cfg.microfono_virtual.dispositivo_salida}» en Windows")
            return 1

        print(f"\n=== REPRODUCIENDO {ruta.name} POR EL CABLE ===")
        duracion = mic.duracion(ruta)
        hilo = threading.Thread(target=mic.hablar, args=(ruta, True), daemon=True)
        hilo.start()
        hilo.join(timeout=duracion + 5)
        time.sleep(0.4)

        medida = pagina.evaluate("() => { const m = window.__vbMedida; if (m) m.activo = false; return m; }")

        print("\n=== VEREDICTO ===")
        if not medida or not medida.get("n"):
            print("  ✗ No pude medir. ¿Se cerró la pestaña?")
            return 1

        pico = float(medida["pico"])
        medio = float(medida["suma"]) / max(int(medida["n"]), 1)
        print(f"  Pico captado por el navegador : {pico:.4f}")
        print(f"  Nivel medio                   : {medio:.4f}")

        if pico < 0.001:
            print("\n  ✗ SILENCIO. El navegador del emulador NO recibe el audio.")
            print("    Revisa, en este orden:")
            print("      1. BlueStacks → Ajustes → Dispositivos → Micrófono: CABLE Output")
            print("      2. BlueStacks → Altavoces: tus bocinas REALES, no el cable")
            print("      3. Reinicia BlueStacks (toma el audio al arrancar)")
            print("      4. Dentro de Android: Ajustes → Apps → Chrome → Permisos → Micrófono")
            return 1

        if pico < 0.02:
            print("\n  ~ Llega, pero muy bajo. Sube el volumen:")
            print("      · CABLE Input al 100 en Windows")
            print("      · ganancia_db: 6.0 en config.yaml")
            return 0

        print("\n  ✓ EL NAVEGADOR SÍ RECIBE EL AUDIO.")
        print("    Si el juego aun asi no lo detecta, el problema es del juego:")
        print("    quiza corta la grabacion antes, o exige un clic de confianza")
        print("    para empezar a grabar.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
