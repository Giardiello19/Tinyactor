"""Abre Chrome con aspecto de móvil, sin necesitar las herramientas de desarrollador.

DevTools sirve para activar la vista móvil, pero deja una pestaña extra que
confunde a la automatización. Aquí se consigue lo mismo de dos maneras:

  · lanzando Chrome con el tamaño y el user-agent del dispositivo
  · o aplicando la emulación por CDP sobre una pestaña ya abierta
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("vozbot.lanzador")

UA_ANDROID = (
    "Mozilla/5.0 (Linux; Android 13; {modelo}) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)
UA_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
UA_IPAD = (
    "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# ancho, alto, densidad, user-agent, táctil
DISPOSITIVOS: dict[str, dict] = {
    "Escritorio": {
        "ancho": 1280, "alto": 800, "escala": 1.0, "ua": "", "tactil": False, "movil": False,
    },
    "Pixel 7 (Android)": {
        "ancho": 412, "alto": 915, "escala": 2.625,
        "ua": UA_ANDROID.format(modelo="Pixel 7"), "tactil": True, "movil": True,
    },
    "Galaxy S20 (Android)": {
        "ancho": 360, "alto": 800, "escala": 3.0,
        "ua": UA_ANDROID.format(modelo="SM-G981B"), "tactil": True, "movil": True,
    },
    "Xiaomi (Android)": {
        "ancho": 393, "alto": 873, "escala": 2.75,
        "ua": UA_ANDROID.format(modelo="M2101K6G"), "tactil": True, "movil": True,
    },
    "iPhone 14": {
        "ancho": 390, "alto": 844, "escala": 3.0,
        "ua": UA_IPHONE, "tactil": True, "movil": True,
    },
    "iPhone SE": {
        "ancho": 375, "alto": 667, "escala": 2.0,
        "ua": UA_IPHONE, "tactil": True, "movil": True,
    },
    "iPad": {
        "ancho": 820, "alto": 1180, "escala": 2.0,
        "ua": UA_IPAD, "tactil": True, "movil": True,
    },
}

RUTAS_CHROME = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]


def buscar_chrome(ruta: str = "") -> str | None:
    if ruta and Path(ruta).is_file():
        return ruta
    for r in RUTAS_CHROME:
        if Path(r).is_file():
            return r
    encontrado = shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium")
    return encontrado


def medidas(dispositivo: str, orientacion: str = "vertical") -> tuple[int, int]:
    """Ancho y alto según la orientación. En horizontal se intercambian."""
    d = DISPOSITIVOS.get(dispositivo, DISPOSITIVOS["Escritorio"])
    ancho, alto = d["ancho"], d["alto"]
    if orientacion.lower().startswith("horiz") and d.get("movil"):
        return alto, ancho
    return ancho, alto


def abrir_chrome(
    dispositivo: str = "Escritorio",
    puerto: int = 9222,
    perfil: str = "",
    url: str = "",
    ruta_chrome: str = "",
    orientacion: str = "vertical",
) -> tuple[bool, str]:
    """Lanza Chrome listo para automatizar, con el aspecto del dispositivo."""
    exe = buscar_chrome(ruta_chrome)
    if not exe:
        return False, "No encuentro Chrome. Indica su ruta en los ajustes."

    d = DISPOSITIVOS.get(dispositivo, DISPOSITIVOS["Escritorio"])
    ancho, alto = medidas(dispositivo, orientacion)
    carpeta = perfil or str(Path.home() / ".chromebot")

    comando = [
        exe,
        f"--remote-debugging-port={puerto}",
        f"--user-data-dir={carpeta}",
        f"--window-size={ancho},{alto}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if d["ua"]:
        comando.append(f"--user-agent={d['ua']}")
    if d["tactil"]:
        comando.append("--touch-events=enabled")
    if url:
        comando.append(url)

    try:
        subprocess.Popen(comando)
    except Exception as e:
        return False, f"No pude abrir Chrome: {e}"

    return True, f"{dispositivo} · {ancho}x{alto} · puerto {puerto}"


def _sesion_cdp(page):
    """Una sola sesión CDP por pestaña.

    Los ajustes de emulación son POR SESIÓN: si se abre una nueva en cada
    llamada, no se pueden deshacer los de la anterior. Por eso se guarda.
    """
    sesion = getattr(page, "_vb_cdp", None)
    if sesion is None:
        sesion = page.context.new_cdp_session(page)
        try:
            page._vb_cdp = sesion
            page._vb_ua = page.evaluate("() => navigator.userAgent")
        except Exception:
            pass
    return sesion


def emular_dispositivo(page, dispositivo: str, orientacion: str = "vertical") -> tuple[bool, str]:
    """Aplica la vista de un dispositivo a una pestaña YA conectada, por CDP.

    Es lo mismo que hacen las herramientas de desarrollador al activar la
    vista de móvil, pero sin abrir su ventana: así el bot no se confunde de
    pestaña y no hace falta relanzar el navegador.
    """
    d = DISPOSITIVOS.get(dispositivo)
    if not d:
        return False, f"Dispositivo desconocido: {dispositivo}"

    try:
        sesion = _sesion_cdp(page)

        if dispositivo == "Escritorio":
            sesion.send("Emulation.clearDeviceMetricsOverride")
            sesion.send("Emulation.setTouchEmulationEnabled", {"enabled": False})
            try:
                sesion.send("Emulation.setEmitTouchEventsForMouse", {"enabled": False})
            except Exception:
                pass
            original = getattr(page, "_vb_ua", "")
            if original:
                sesion.send("Emulation.setUserAgentOverride", {"userAgent": original})
            try:
                page.reload(wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            return True, "Vista de escritorio restaurada"

        ancho, alto = medidas(dispositivo, orientacion)
        sesion.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": ancho,
                "height": alto,
                "deviceScaleFactor": d["escala"],
                "mobile": d["movil"],
                "screenOrientation": {
                    "type": "landscapePrimary" if ancho > alto else "portraitPrimary",
                    "angle": 90 if ancho > alto else 0,
                },
            },
        )
        sesion.send(
            "Emulation.setTouchEmulationEnabled",
            {"enabled": d["tactil"], "maxTouchPoints": 5},
        )
        if d["tactil"]:
            try:
                sesion.send(
                    "Emulation.setEmitTouchEventsForMouse",
                    {"enabled": True, "configuration": "mobile"},
                )
            except Exception:
                pass
        if d["ua"]:
            sesion.send("Emulation.setUserAgentOverride", {"userAgent": d["ua"]})

        # Algunas comprobaciones (como «ontouchstart») solo se actualizan al
        # recargar: se hace aquí para que la web sirva su diseño móvil real.
        try:
            page.reload(wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            log.debug("No pude recargar tras emular: %s", e)

        log.info("Vista de %s aplicada (%sx%s)", dispositivo, ancho, alto)
        return True, f"{dispositivo} · {ancho}x{alto} · {orientacion}"
    except Exception as e:
        return False, f"No pude aplicar la vista móvil: {e}"
