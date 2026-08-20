#!/usr/bin/env python3
"""Punto de entrada.

    python run.py            # abre el panel de control
    python run.py --consola  # ejecuta el bucle sin ventana, con config.yaml
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

RUTA_CONFIG = Path(__file__).resolve().parent / "config.yaml"


def main() -> None:
    p = argparse.ArgumentParser(description="vozbot")
    p.add_argument("--consola", action="store_true", help="sin interfaz gráfica")
    p.add_argument("--config", default=str(RUTA_CONFIG))
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.consola:
        from vozbot.config import Config
        from vozbot.orchestrator import Orquestador

        cfg = Config.cargar(args.config)
        Orquestador(cfg, on_evento=lambda t, m: print(f"[{t}] {m}")).ejecutar()
    else:
        from vozbot.gui import main as abrir_panel

        abrir_panel()


if __name__ == "__main__":
    main()
