#!/usr/bin/env python3
"""Diagnóstico: enseña qué ve el bot en la pantalla actual del juego.

Con el Chrome de depuración abierto en la pantalla que falla:

    python diagnostico.py

Imprime el texto por cada fuente, los botones detectados y el resultado de la
extracción. Si «texto_a_leer» sale vacío, aquí ves por qué.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vozbot.browser import NavegadorWeb
from vozbot.config import Config
from vozbot.extractor import ExtractorGuion


def separador(titulo: str) -> None:
    print(f"\n{'=' * 60}\n{titulo}\n{'=' * 60}")


def main() -> None:
    cfg = Config.cargar(Path(__file__).resolve().parent / "config.yaml")
    nav = NavegadorWeb(cfg.navegador, cfg.botones_web, cfg.cuenta_regresiva)
    nav.conectar()

    try:
        visible = nav.texto_pagina()
        profundo = nav.texto_profundo()

        separador("1. TEXTO VISIBLE (inner_text del frame principal)")
        print(visible[:3000] or "(vacío)")

        separador("2. RECOLECCIÓN PROFUNDA (shadow DOM + iframes + atributos)")
        print(profundo[:5000] or "(vacío)")

        separador("3. FRAMES DETECTADOS")
        for f in nav.page.frames:
            print(f"  - {f.url[:100]}")

        separador("4. CONTROLES CLICABLES")
        botones = nav.page.evaluate(
            """() => [...document.querySelectorAll('button,[role=button],a,[onclick]')]
                 .filter(e => e.offsetParent !== null)
                 .map(e => ({
                    texto: (e.innerText||'').replace(/\\s+/g,' ').trim(),
                    aria: e.getAttribute('aria-label')||'',
                    tag: e.tagName.toLowerCase()
                 }))"""
        )
        for b in botones:
            etiqueta = b["texto"] or b["aria"] or "(sin texto)"
            print(f"  <{b['tag']}>  {etiqueta!r}   aria={b['aria']!r}")

        separador("5. BOTONES SIN TEXTO (iconos) — copia el selector sugerido")
        mudos = nav.page.evaluate(
            """() => {
                const salida = [];
                const elems = [...document.querySelectorAll('button,[role=button],a,[onclick],svg,[class*=record],[class*=mic],[class*=rec]')];
                for (const e of elems) {
                  if (e.offsetParent === null) continue;
                  const t = (e.innerText||'').replace(/\\s+/g,' ').trim();
                  if (t.length > 0) continue;                 // este ya se detecta por texto
                  const r = e.getBoundingClientRect();
                  if (r.width < 8 || r.height < 8) continue;  // ruido invisible
                  let sel = '';
                  if (e.id) sel = '#' + CSS.escape(e.id);
                  else {
                    for (const a of ['data-testid','data-test','data-id','name','aria-label']) {
                      const v = e.getAttribute(a);
                      if (v) { sel = `[${a}="${v}"]`; break; }
                    }
                    if (!sel && e.className && typeof e.className === 'string') {
                      const clases = e.className.trim().split(/\\s+/)
                        .filter(c => !/^(css-|r-)/.test(c));   // clases generadas, poco estables
                      const utiles = clases.length ? clases : e.className.trim().split(/\\s+/);
                      if (utiles.length) sel = '.' + utiles.map(c => CSS.escape(c)).join('.');
                    }
                  }
                  salida.push({
                    sel,
                    tag: e.tagName.toLowerCase(),
                    clases: String(e.className || '').slice(0, 80),
                    x: Math.round(r.left + r.width/2),
                    y: Math.round(r.top + r.height/2),
                    w: Math.round(r.width), h: Math.round(r.height),
                    coincidencias: sel ? document.querySelectorAll(sel).length : 0
                  });
                }
                return salida;
            }"""
        )
        if not mudos:
            print("  (ninguno)")
        for m in mudos:
            unico = "ÚNICO" if m["coincidencias"] == 1 else f"{m['coincidencias']} coincidencias"
            print(f"  <{m['tag']}> {m['w']}x{m['h']} en ({m['x']},{m['y']})  [{unico}]")
            if m["sel"]:
                print(f"      pega esto:  {m['sel']}")
            print(f"      alternativa: coord={m['x']},{m['y']}")
            if m["clases"]:
                print(f"      clases: {m['clases']}")

        separador("6. RESULTADO DE LA EXTRACCIÓN")
        extractor = ExtractorGuion(cfg.extraccion)
        datos = extractor.extraer(visible, nav.html_pagina(), profundo)
        for clave, valor in datos.items():
            print(f"  {clave:15} = {valor!r}")

        if not datos["texto_a_leer"]:
            separador("QUÉ HACER")
            print(
                "No encontré frase que leer. Mira los bloques 1 y 2:\n"
                "  · Si la frase aparece ahí pero con otras comillas → dímelas y las añado.\n"
                "  · Si aparece sin comillas → usa el patrón «tras dos puntos» o el OCR.\n"
                "  · Si NO aparece en ningún bloque → está en un canvas: activa el OCR\n"
                "    (pip install -r requirements-ocr.txt) y el razonamiento en el panel."
            )
    finally:
        nav.cerrar()


if __name__ == "__main__":
    main()
