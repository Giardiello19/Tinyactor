"""Control del navegador por CDP (Playwright).

Se conecta a un Chrome ya abierto para conservar sesión y permisos de
micrófono: chrome.exe --remote-debugging-port=9222 --user-data-dir=C:/chromebot
"""
from __future__ import annotations

import logging
import re
import threading
import time

from playwright.sync_api import Page, sync_playwright
from tenacity import retry, stop_after_attempt, wait_fixed

from .config import BotonesWeb, CuentaRegresiva

log = logging.getLogger("vozbot.browser")

# Recorre el árbol completo (incluidos shadow roots) y devuelve texto visible
# más los atributos donde suele esconderse el contenido de la interfaz.
JS_TEXTO_PROFUNDO = r"""
() => {
  const ATRIBUTOS = ['aria-label','alt','title','placeholder','value','data-text','data-content'];
  const salida = [];
  const vistos = new Set();

  const visible = (el) => {
    try {
      if (!(el instanceof Element)) return true;
      const s = getComputedStyle(el);
      if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
      return true;
    } catch (e) { return true; }
  };

  const guardar = (t) => {
    if (!t) return;
    t = String(t).replace(/\s+/g, ' ').trim();
    if (t.length < 1 || t.length > 600) return;
    if (vistos.has(t)) return;
    vistos.add(t);
    salida.push(t);
  };

  const recorrer = (nodo) => {
    if (!nodo) return;
    if (nodo.nodeType === Node.TEXT_NODE) {
      if (visible(nodo.parentElement)) guardar(nodo.nodeValue);
      return;
    }
    if (nodo.nodeType !== Node.ELEMENT_NODE) return;
    const etiqueta = nodo.tagName ? nodo.tagName.toLowerCase() : '';
    if (etiqueta === 'script' || etiqueta === 'style' || etiqueta === 'noscript') return;
    if (!visible(nodo)) return;

    for (const a of ATRIBUTOS) {
      const v = nodo.getAttribute && nodo.getAttribute(a);
      if (v) guardar(v);
    }
    if (nodo.shadowRoot) recorrer(nodo.shadowRoot);
    for (const hijo of nodo.childNodes) recorrer(hijo);
  };

  recorrer(document.body || document.documentElement);
  return salida.join('\n');
}
"""


class NavegadorWeb:
    def __init__(self, cfg_nav, botones: BotonesWeb, cuenta: CuentaRegresiva):
        self.cfg = cfg_nav
        self.botones = botones
        self.cuenta = cuenta
        self._pw = None
        self._browser = None
        self._parar_espera = threading.Event()
        # Con vista de móvil, la web suele escuchar toques y no clics de ratón.
        self.tactil = False
        self.page: Page | None = None

    # ------------------------------------------------------------------
    def conectar(self) -> Page:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.connect_over_cdp(self.cfg.cdp_url)
        contexto = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self.page = self._elegir_pestana(contexto)
        # Sin esto, cualquier operación espera hasta 30 s sin avisar de nada.
        try:
            self.page.set_default_timeout(4000)
            self.page.set_default_navigation_timeout(15000)
        except Exception:
            pass

        # Vista móvil sin abrir las herramientas de desarrollador
        if getattr(self.cfg, "emular_al_conectar", False) and self.cfg.dispositivo != "Escritorio":
            from .lanzador import emular_dispositivo

            ok, detalle = emular_dispositivo(
                self.page, self.cfg.dispositivo, getattr(self.cfg, "orientacion", "vertical")
            )
            log.info("Vista: %s", detalle) if ok else log.warning(detalle)
            if ok:
                from .lanzador import DISPOSITIVOS

                self.tactil = bool(DISPOSITIVOS.get(self.cfg.dispositivo, {}).get("tactil"))
                if self.tactil:
                    log.info("Modo táctil: enviaré toques en vez de clics de ratón")

        if self.cfg.url_juego and self.cfg.url_juego not in self.page.url:
            self.page.goto(self.cfg.url_juego, wait_until="domcontentloaded")
        log.info("Conectado a %s — pestaña: %s", self.cfg.cdp_url, self.page.url)
        return self.page

    def _elegir_pestana(self, contexto):
        """Elige la pestaña del juego, no la primera que aparezca.

        Con las herramientas de desarrollador abiertas (F12), Playwright puede
        quedarse con la pestaña de DevTools y el bot acabaría leyendo código
        CSS y pulsando la consola. Se descartan las pestañas internas del
        navegador y se prefiere la del juego.
        """
        paginas = list(contexto.pages)
        if not paginas:
            return contexto.new_page()

        internas = ("devtools://", "chrome://", "chrome-extension://", "about:blank", "edge://")
        utiles = [p for p in paginas if not p.url.lower().startswith(internas)]

        if not utiles:
            log.warning(
                "Solo veo pestañas internas del navegador (%s). "
                "Cierra las herramientas de desarrollador (F12) y abre el juego.",
                [p.url[:40] for p in paginas],
            )
            return paginas[0]

        # si la URL del juego está configurada, se prefiere esa pestaña
        if self.cfg.url_juego:
            clave = self.cfg.url_juego.split("//")[-1].split("/")[0].lower()
            for p in utiles:
                if clave in p.url.lower():
                    return p

        for p in utiles:                    # cualquier pestaña web normal
            if p.url.lower().startswith(("http://", "https://")):
                if len(utiles) > 1:
                    log.info("Elegida la pestaña: %s", p.url[:70])
                return p
        return utiles[0]

    def cerrar(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    # ------------------------------------------------------------------
    def texto_pagina(self) -> str:
        """Texto visible del frame principal (rápido)."""
        raiz = self.cfg.selector_raiz or "body"
        try:
            return self.page.inner_text(raiz, timeout=2500)
        except Exception:
            try:
                return self.page.inner_text("body", timeout=2500)
            except Exception as e:
                log.debug("No pude leer el texto de la página: %s", e)
                return ""

    def texto_profundo(self) -> str:
        """Todo lo legible, venga de donde venga.

        Recorre el frame principal y cada iframe, atraviesa shadow DOM abiertos
        y recoge además los atributos donde suele esconderse el texto de las
        interfaces hechas con React Native Web o con canvas accesible:
        aria-label, alt, title, placeholder, value y data-text.
        """
        piezas: list[str] = []
        for frame in self.page.frames[:6]:        # no recorrer decenas de iframes
            try:
                piezas.append(frame.evaluate(JS_TEXTO_PROFUNDO))
            except Exception:
                continue
        return "\n".join(p for p in piezas if p)

    def html_pagina(self) -> str:
        return self.page.content()

    def captura(self, ruta: str) -> str:
        self.page.screenshot(path=ruta)
        return ruta

    # ------------------------------------------------------------------
    def _prohibido(self, entrada: str) -> bool:
        """¿Es un control que nunca debemos pulsar (salir, cancelar…)?"""
        e = entrada.lower()
        for veto in self.botones.prohibidos:
            v = veto.lower().strip()
            if not v:
                continue
            # coincide con el texto suelto o dentro de un selector aria-label
            if v == e or f'"{v}"' in e or f"'{v}'" in e or f">{v}<" in e:
                return True
            if v in e and len(e) < len(v) + 12:
                return True
        return False

    @retry(stop=stop_after_attempt(2), wait=wait_fixed(0.4), reraise=False)
    def click_texto(self, textos: list[str] | str, timeout_ms: int = 900) -> bool:
        """Hace clic en el primer control que coincida.

        Cada entrada puede ser:
          · texto visible          →  Detener
          · aria-label             →  Entendido
          · selector CSS           →  css=.btn-record   |   .btn-record   |   #rec
          · XPath                  →  xpath=//div[@data-id='rec']
          · coordenadas del viewport → coord=640,480
        Los controles de la lista `prohibidos` se ignoran siempre.
        """
        if isinstance(textos, str):
            textos = [textos]

        for entrada in textos:
            entrada = (entrada or "").strip()
            if not entrada:
                continue
            if self._prohibido(entrada):
                log.warning("Me niego a pulsar «%s»: está en la lista de prohibidos", entrada)
                continue
            if self._click_especial(entrada, timeout_ms):
                return True
            if self._click_por_texto(entrada, timeout_ms):
                return True

        log.warning("No encontré ningún control con: %s", textos)
        return False

    def _click_especial(self, entrada: str, timeout_ms: int) -> bool:
        """Atiende selectores y coordenadas; devuelve False si no es de ese tipo."""
        if entrada.lower().startswith("coord="):
            try:
                x, y = (int(v) for v in entrada.split("=", 1)[1].split(","))
            except ValueError:
                log.error("Coordenadas mal escritas: %s (usa coord=640,480)", entrada)
                return False
            return self.click_coord(x, y)

        selector = ""
        if entrada.lower().startswith(("css=", "xpath=")):
            selector = entrada
        elif entrada.startswith((".", "#", "[")) or entrada.startswith("//"):
            selector = entrada

        if not selector:
            return False

        try:
            elemento = self.page.locator(selector).first
            elemento.wait_for(state="visible", timeout=timeout_ms)
            elemento.scroll_into_view_if_needed(timeout=1000)
            elemento.click(timeout=timeout_ms)
            log.info("Clic por selector «%s»", selector)
            return True
        except Exception as e:
            log.debug("Selector %s sin resultado: %s", selector, e)
            return False

    def _click_por_texto(self, texto: str, timeout_ms: int) -> bool:
        patron = re.compile(re.escape(texto), re.IGNORECASE)
        candidatos = [
            self.page.get_by_role("button", name=patron),
            self.page.get_by_role("link", name=patron),
            self.page.locator(f'[aria-label*="{texto}" i]'),
            self.page.locator(f'[title*="{texto}" i]'),
            self.page.get_by_text(patron),
        ]
        for loc in candidatos:
            try:
                # count() es instantáneo: descarta sin gastar el timeout entero
                if loc.count() == 0:
                    continue
                elemento = loc.first
                elemento.wait_for(state="visible", timeout=timeout_ms)
                elemento.scroll_into_view_if_needed(timeout=1000)
                elemento.click(timeout=timeout_ms)
                log.info("Clic en «%s»", texto)
                return True
            except Exception:
                continue
        return False

    def listar_controles(self) -> list[dict]:
        """Todos los controles pulsables visibles, con su texto y posición."""
        try:
            return self.page.evaluate(
                """() => {
                    const sel = 'button,[role=button],a,[onclick],[aria-label],div[tabindex]';
                    const vistos = new Set();
                    const salida = [];
                    for (const e of document.querySelectorAll(sel)) {
                      if (e.offsetParent === null) continue;
                      const r = e.getBoundingClientRect();
                      if (r.width < 12 || r.height < 12) continue;
                      const texto = (e.innerText || '').replace(/\\s+/g,' ').trim();
                      const aria = e.getAttribute('aria-label') || '';
                      const etiqueta = (texto || aria).trim();
                      if (!etiqueta || etiqueta.length > 60) continue;
                      if (vistos.has(etiqueta)) continue;
                      vistos.add(etiqueta);
                      salida.push({
                        etiqueta,
                        aria,
                        tiene_texto: texto.length > 0,
                        x: Math.round(r.left + r.width/2),
                        y: Math.round(r.top + r.height/2),
                      });
                    }
                    return salida;
                }"""
            )
        except Exception as e:
            log.debug("No pude listar controles: %s", e)
            return []

    def _localizar(self, entrada: str):
        """Devuelve el locator de una entrada, sea selector o texto."""
        entrada = (entrada or "").strip()
        if not entrada:
            return None
        if entrada.lower().startswith(("css=", "xpath=")) or entrada.startswith(
            (".", "#", "[", "//")
        ):
            return self.page.locator(entrada).first
        return self.page.get_by_text(re.compile(re.escape(entrada), re.I)).first

    def buscar_por_palabras(self, palabras: list[str]) -> dict | None:
        """Busca en TODO el DOM un elemento cuyo texto contenga alguna palabra.

        Recorre también los contenedores, no solo los botones: en muchas apps
        el texto vive en un div interior y el elemento pulsable es el padre.
        Normaliza acentos, mayúsculas y espacios, así que «SEGUIR GANANDO
        MONEDAS» se encuentra aunque venga partido en varias líneas o con un
        icono en medio.
        """
        claves = [p.lower().strip() for p in palabras if p and p.strip()]
        if not claves:
            return None
        try:
            return self.page.evaluate(
                """(claves) => {
                    const quita = s => s.normalize('NFD').replace(/[\\u0300-\\u036f]/g,'')
                                        .toLowerCase().replace(/\\s+/g,' ').trim();
                    const normClaves = claves.map(quita);
                    let mejor = null;
                    for (const el of document.querySelectorAll('*')) {
                      if (el.offsetParent === null) continue;
                      const r = el.getBoundingClientRect();
                      if (r.width < 20 || r.height < 12) continue;
                      if (r.width > innerWidth * 0.98 && r.height > innerHeight * 0.8) continue;
                      const t = quita(el.innerText || el.textContent || '');
                      if (!t || t.length > 120) continue;
                      if (!normClaves.some(c => t.includes(c))) continue;
                      // el más pequeño que contenga el texto = el botón real
                      const area = r.width * r.height;
                      if (!mejor || area < mejor.area) {
                        mejor = {texto: (el.innerText||'').trim(), area,
                                 x: Math.round(r.left + r.width/2),
                                 y: Math.round(r.top + r.height/2),
                                 w: Math.round(r.width), h: Math.round(r.height)};
                      }
                    }
                    return mejor;
                }""",
                claves,
            )
        except Exception as e:
            log.debug("Búsqueda por palabras falló: %s", e)
            return None

    def click_por_palabras(self, palabras: list[str]) -> bool:
        """Encuentra por palabras clave y pulsa por coordenadas.

        El clic va al centro del elemento con el ratón real, así que funciona
        aunque el texto esté en un hijo y el manejador en el padre.
        """
        hallado = self.buscar_por_palabras(palabras)
        if not hallado:
            return False
        if self._prohibido(hallado.get("texto", "")):
            log.warning("Encontré «%s» pero está prohibido", hallado["texto"])
            return False
        if self._pulsar_punto(hallado["x"], hallado["y"]):
            log.info(
                "Pulsé «%s» por palabras clave en (%d, %d)",
                hallado["texto"][:40], hallado["x"], hallado["y"],
            )
            return True
        return False

    def buscar_opciones_letra(self, letras: list[str]) -> list[dict]:
        """Encuentra los círculos de elección («A» a la izquierda, «B» a la derecha).

        No sirve buscarlos entre los controles normales: son divs sin onclick
        ni role, con la letra en un nodo interior. Aquí se busca por dos vías:
        el texto exacto de una letra, y la forma circular (alto igual al ancho
        con borde redondeado). Se devuelven ordenados de izquierda a derecha.
        """
        validas = [l.strip().upper() for l in letras if l and l.strip()]
        try:
            hallados = self.page.evaluate(
                """(validas) => {
                    const salida = [];
                    const vistos = new Set();
                    for (const el of document.querySelectorAll('*')) {
                      if (el.offsetParent === null) continue;
                      const r = el.getBoundingClientRect();
                      if (r.width < 24 || r.height < 24 || r.width > 400) continue;

                      const propio = (el.innerText || el.textContent || '')
                                      .replace(/\\s+/g,'').toUpperCase();
                      if (!validas.includes(propio)) continue;

                      const est = getComputedStyle(el);
                      const radio = parseFloat(est.borderRadius) || 0;
                      const redondo = radio >= Math.min(r.width, r.height) * 0.3
                                      || Math.abs(r.width - r.height) < 12;

                      const clave = propio + '@' + Math.round(r.left) + ',' + Math.round(r.top);
                      if (vistos.has(clave)) continue;
                      vistos.add(clave);

                      salida.push({
                        letra: propio,
                        redondo,
                        area: r.width * r.height,
                        x: Math.round(r.left + r.width/2),
                        y: Math.round(r.top + r.height/2),
                        w: Math.round(r.width), h: Math.round(r.height),
                      });
                    }
                    // el contenedor y su hijo dan la misma letra: quedarse con
                    // el más pequeño de cada posición aproximada
                    salida.sort((a,b) => a.area - b.area);
                    const finales = [];
                    for (const c of salida) {
                      if (!finales.some(f => Math.abs(f.x - c.x) < 40 && Math.abs(f.y - c.y) < 40)) {
                        finales.push(c);
                      }
                    }
                    return finales.sort((a,b) => a.x - b.x);
                }""",
                validas,
            )
            return hallados or []
        except Exception as e:
            log.debug("Búsqueda de opciones falló: %s", e)
            return []

    def buscar_elemento_grande(self) -> dict | None:
        """El elemento pulsable más grande, sin contar el fondo de pantalla.

        Las pantallas de premio suelen ser una tarjeta o un cofre enorme que
        hay que tocar varias veces. No tienen texto útil ni aria-label, así
        que se localizan por tamaño.
        """
        try:
            return self.page.evaluate(
                """() => {
                    const W = innerWidth, H = innerHeight;
                    let mejor = null;
                    for (const el of document.querySelectorAll('*')) {
                      if (el.offsetParent === null) continue;
                      const r = el.getBoundingClientRect();
                      if (r.width < 60 || r.height < 60) continue;
                      // descartar contenedores que ocupan casi todo
                      if (r.width > W * 0.95 && r.height > H * 0.85) continue;
                      if (r.top > H || r.left > W || r.bottom < 0) continue;
                      const est = getComputedStyle(el);
                      const pulsable =
                        el.onclick !== null ||
                        el.getAttribute('role') === 'button' ||
                        el.tagName === 'BUTTON' ||
                        el.hasAttribute('tabindex') ||
                        est.cursor === 'pointer';
                      if (!pulsable) continue;
                      const area = r.width * r.height;
                      if (!mejor || area > mejor.area) {
                        mejor = {texto: (el.innerText||'').replace(/\\s+/g,' ').trim().slice(0,50),
                                 area,
                                 x: Math.round(r.left + r.width/2),
                                 y: Math.round(r.top + r.height/2),
                                 w: Math.round(r.width), h: Math.round(r.height)};
                      }
                    }
                    return mejor;
                }"""
            )
        except Exception as e:
            log.debug("No pude buscar el elemento grande: %s", e)
            return None

    def coords_de(self, entrada: str) -> tuple[int, int] | None:
        """Centro del control, para poder volver a pulsar el mismo punto.

        Si el juego cambia de diseño o el botón cambia de etiqueta, las
        coordenadas siguen siendo válidas mientras no se mueva.
        """
        loc = self._localizar(entrada)
        if loc is None:
            return None
        try:
            if loc.count() == 0:
                return None
            caja = loc.bounding_box(timeout=500)
            if not caja:
                return None
            return (
                int(caja["x"] + caja["width"] / 2),
                int(caja["y"] + caja["height"] / 2),
            )
        except Exception:
            return None

    def coords_de_cualquiera(self, entradas: list[str]) -> tuple[int, int] | None:
        for e in entradas:
            if not e:
                continue
            c = self.coords_de(e)
            if c:
                return c
        return None

    def inventario(self) -> list[dict]:
        """Todos los controles de la pantalla, con sus rasgos, en UNA consulta.

        Una sola llamada al navegador en vez de una por botón: además del
        texto trae forma, color, tamaño y posición, que es lo que permite
        reconocer un botón por lo que ES y no por cómo se llama. Así el bot
        se adapta solo al diseño de escritorio o al de móvil.
        """
        try:
            return self.page.evaluate(
                """() => {
                    const W = innerWidth, H = innerHeight;
                    const salida = [];
                    const vistos = new Set();

                    for (const el of document.querySelectorAll('*')) {
                      if (el.offsetParent === null && el.tagName !== 'BODY') continue;
                      const r = el.getBoundingClientRect();
                      if (r.width < 16 || r.height < 12) continue;
                      if (r.width > W * 0.98 && r.height > H * 0.9) continue;
                      if (r.bottom < 0 || r.top > H * 2) continue;

                      const est = getComputedStyle(el);
                      if (est.visibility === 'hidden' || est.opacity === '0') continue;

                      const texto = (el.innerText || '').replace(/\\s+/g,' ').trim();
                      const aria = el.getAttribute('aria-label') || '';
                      const etiqueta = (texto || aria).trim();
                      // hasta 400: el guion del juego es un bloque largo y
                      // tiene que aparecer en el inventario para poder leerlo
                      if (etiqueta.length > 400) continue;

                      // ¿parece pulsable?
                      const pulsable =
                        el.tagName === 'BUTTON' || el.tagName === 'A' ||
                        el.getAttribute('role') === 'button' ||
                        el.hasAttribute('onclick') || el.onclick !== null ||
                        el.hasAttribute('tabindex') || est.cursor === 'pointer' ||
                        aria.length > 0;
                      if (!pulsable && !etiqueta) continue;

                      const clave = etiqueta + '@' + Math.round(r.left) + ',' + Math.round(r.top)
                                    + ',' + Math.round(r.width);
                      if (vistos.has(clave)) continue;
                      vistos.add(clave);

                      const radio = parseFloat(est.borderRadius) || 0;
                      const tam = parseFloat(est.fontSize) || 0;
                      const cursiva = est.fontStyle === 'italic';
                      const hijos = el.children.length;
                      const fondo = est.backgroundColor || '';
                      const m = fondo.match(/\\d+/g);
                      const rgb = m ? m.slice(0,3).map(Number) : [0,0,0];

                      salida.push({
                        etiqueta, texto, aria,
                        pulsable,
                        tam, cursiva, hojas: hijos === 0,
                        redondo: Math.abs(r.width - r.height) < 14 && radio >= Math.min(r.width, r.height) * 0.25,
                        rojizo: rgb[0] > 130 && rgb[0] > rgb[1] * 1.5 && rgb[0] > rgb[2] * 1.5,
                        destacado: rgb[0] + rgb[1] + rgb[2] > 240 && (rgb[0] > 180 || rgb[1] > 180),
                        x: Math.round(r.left + r.width/2),
                        y: Math.round(r.top + r.height/2),
                        w: Math.round(r.width), h: Math.round(r.height),
                        area: Math.round(r.width * r.height),
                        rel_y: +(r.top / H).toFixed(2),
                        centrado: Math.abs((r.left + r.width/2) - W/2) < W * 0.18,
                      });
                    }
                    return salida;
                }"""
            )
        except Exception as e:
            log.debug("No pude hacer inventario: %s", e)
            return []

    def click_en(self, control: dict) -> bool:
        """Pulsa un control del inventario, con la posición recién comprobada.

        Los diálogos entran con animación, así que entre medir un botón y
        pulsarlo puede haberse desplazado. Pulsar donde ya no está suele caer
        en el fondo y cerrar el diálogo, devolviendo el juego atrás. Por eso
        la posición se refresca justo antes del toque.
        """
        etiqueta = (control.get("etiqueta") or "").strip()
        x, y = int(control["x"]), int(control["y"])

        if etiqueta:
            fresco = self.buscar_por_palabras([etiqueta[:40]])
            if fresco:
                if abs(fresco["x"] - x) > 6 or abs(fresco["y"] - y) > 6:
                    log.info(
                        "«%s» se movió de (%d, %d) a (%d, %d)",
                        etiqueta[:28], x, y, fresco["x"], fresco["y"],
                    )
                x, y = fresco["x"], fresco["y"]

        ok = self._pulsar_punto(x, y)
        if ok:
            log.info(
                "%s en «%s» (%d, %d)",
                "Toque" if self.tactil else "Clic",
                etiqueta[:34] or "sin texto", x, y,
            )
        return ok

    def click_robusto(self, entrada: str) -> bool:
        """Pulsa por todos los medios, del más real al más artificial.

        1. Ratón de verdad sobre el centro del elemento (evento de confianza
           enviado por el navegador; es lo que más se parece a tu clic).
        2. Clic forzado de Playwright, saltándose las comprobaciones.
        3. dispatch_event nativo.
        4. Secuencia manual de eventos de puntero.

        Hace falta esta escalera porque durante la grabación suele haber una
        capa encima del botón que bloquea el clic normal.
        """
        if self._prohibido(entrada):
            log.warning("Me niego a pulsar «%s»: está prohibido", entrada)
            return False

        loc = self._localizar(entrada)
        if loc is None:
            return False
        try:
            if loc.count() == 0:          # no está en pantalla: no perder tiempo
                return False
        except Exception:
            return False

        # 1) PRIMERO: coordenadas del propio navegador + ratón real.
        #    getBoundingClientRect siempre da la posición, aunque Playwright
        #    considere el elemento "no visible", y el clic del ratón es un
        #    evento de confianza (el que exigen permisos como el micrófono).
        #    Es el que acierta en la práctica, así que va antes que nada.
        try:
            loc.scroll_into_view_if_needed(timeout=400)
        except Exception:
            pass

        try:
            caja = loc.evaluate(
                """el => { const r = el.getBoundingClientRect();
                           return {x: r.left + r.width/2, y: r.top + r.height/2,
                                   w: r.width, h: r.height}; }"""
            )
            if caja and caja["w"] > 0 and caja["h"] > 0:
                if self._pulsar_punto(caja["x"], caja["y"]):
                    log.info(
                        "%s real sobre «%s» en (%d, %d)",
                        "Toque" if self.tactil else "Clic",
                        entrada, caja["x"], caja["y"],
                    )
                    return True
        except Exception as e:
            log.debug("Clic real falló: %s", e)

        # 2) clic forzado de Playwright
        try:
            loc.click(timeout=700, force=True)
            log.info("Clic forzado sobre «%s»", entrada)
            return True
        except Exception as e:
            log.debug("Clic forzado falló: %s", e)

        # 3) dispatch nativo: evento sintético, puede no valer para el micrófono
        try:
            loc.dispatch_event("click", timeout=400)
            log.warning(
                "Pulsé «%s» con un evento sintético. Si el micrófono no graba, "
                "puede ser porque el navegador exige un clic de confianza.",
                entrada,
            )
            return True
        except Exception as e:
            log.debug("dispatch_event falló: %s", e)

        # 4) secuencia de eventos a mano
        return self.click_forzado(entrada)

    def click_forzado(self, entrada: str) -> bool:
        """Clic a la brava: eventos de puntero disparados sobre el elemento.

        Sirve cuando algo tapa el botón o cuando la app usa su propio sistema
        de eventos (React Native Web) y el clic normal no cala.
        """
        entrada = (entrada or "").strip()
        if not entrada:
            return False
        try:
            if entrada.lower().startswith(("css=", "xpath=")) or entrada.startswith(
                (".", "#", "[", "//")
            ):
                loc = self.page.locator(entrada).first
            else:
                loc = self.page.get_by_text(re.compile(re.escape(entrada), re.I)).first

            loc.wait_for(state="attached", timeout=400)
            loc.evaluate(
                """el => {
                    const r = el.getBoundingClientRect();
                    const x = r.left + r.width / 2, y = r.top + r.height / 2;
                    const base = {bubbles: true, cancelable: true, clientX: x, clientY: y,
                                  pointerId: 1, isPrimary: true, button: 0, buttons: 1};
                    for (const tipo of ['pointerdown','mousedown','pointerup','mouseup','click']) {
                      const Ctor = tipo.startsWith('pointer') ? PointerEvent : MouseEvent;
                      el.dispatchEvent(new Ctor(tipo, tipo.endsWith('up') || tipo === 'click'
                        ? {...base, buttons: 0} : base));
                    }
                }"""
            )
            log.info("Clic forzado sobre «%s»", entrada)
            return True
        except Exception as e:
            log.debug("Clic forzado falló en %s: %s", entrada, e)
            return False

    def _pulsar_punto(self, x: float, y: float) -> bool:
        """Pulsa un punto: toque si la vista es de móvil, ratón si no.

        En vista de móvil la web escucha touchstart/touchend, y un clic de
        ratón puede no activarla. El toque se envía como evento real, así que
        vale también para permisos que exigen gesto de confianza.
        """
        x, y = int(x), int(y)
        if self.tactil:
            try:
                self.page.touchscreen.tap(x, y)
                return True
            except Exception as e:
                log.debug("El toque falló (%s); pruebo con el ratón", e)
        try:
            self.page.mouse.move(x, y)
            self.page.mouse.down()
            self.page.wait_for_timeout(60)
            self.page.mouse.up()
            return True
        except Exception as e:
            log.error("No pude pulsar en (%d, %d): %s", x, y, e)
            return False

    def click_coord(self, x: int, y: int) -> bool:
        """Pulsa unas coordenadas del viewport."""
        return self._pulsar_punto(x, y)

    def existe_texto(self, texto: str) -> bool:
        try:
            return self.page.get_by_text(re.compile(re.escape(texto), re.I)).first.is_visible(
                timeout=800
            )
        except Exception:
            return False

    def existe_control(self, entrada: str, timeout_ms: int = 600) -> bool:
        """¿Está visible este control? Acepta texto o selector CSS/XPath."""
        entrada = (entrada or "").strip()
        if not entrada or entrada.lower().startswith("coord="):
            return False
        try:
            if entrada.lower().startswith(("css=", "xpath=")) or entrada.startswith(
                (".", "#", "[", "//")
            ):
                return self.page.locator(entrada).first.is_visible(timeout=timeout_ms)
            return self.page.get_by_text(
                re.compile(re.escape(entrada), re.I)
            ).first.is_visible(timeout=timeout_ms)
        except Exception:
            return False

    def alguno_visible(self, entradas: list[str]) -> bool:
        return any(self.existe_control(e) for e in entradas)

    def esperar_control(self, entradas: list[str], timeout_s: float = 60.0) -> bool:
        """Espera a que reaparezca alguno de estos controles.

        Sirve para los juegos que no tienen botón de detener: la grabación
        termina sola y el botón de grabar vuelve a aparecer.
        """
        limite = time.time() + timeout_s
        while time.time() < limite:
            if self._parar_espera.is_set():
                return False
            if self.alguno_visible(entradas):
                return True
            time.sleep(0.4)
        return False

    def cerrar_modales(self) -> None:
        for t in self.botones.cerrar_modal:
            if self.existe_texto(t):
                self.click_texto([t], timeout_ms=800)

    # ------------------------------------------------------------------
    def iniciar_microfono(self) -> bool:
        return self.click_texto(self.botones.iniciar_microfono)

    def detener_microfono(self) -> bool:
        return self.click_texto(self.botones.detener_microfono)

    def siguiente(self) -> bool:
        return self.click_texto(self.botones.siguiente)

    # ------------------------------------------------------------------
    def esperar_cuenta_regresiva(self) -> float:
        """Espera a que la web termine su «3 · 2 · 1».

        Sin selector configurado no se rastrea nada: leer toda la página en
        bucle es lento y retrasa el audio. Con fallback en 0 se habla al
        instante, que es lo que quieres si el juego graba desde el primer
        momento.
        """
        selector = (self.cuenta.selector or "").strip()

        # Si aquí hay un número, es que se quiso poner una espera, no un
        # selector. Se respeta como segundos en vez de buscar un elemento
        # inexistente durante todo el tiempo de espera.
        if selector:
            try:
                segundos = float(selector.replace(",", "."))
                log.info("«%s» no es un selector: lo tomo como %.1fs de espera", selector, segundos)
                if segundos > 0:
                    time.sleep(min(segundos, 15))
                return time.time()
            except ValueError:
                pass

        # Sin selector válido: hablar en cuanto se pulsa grabar.
        if not selector or not selector.startswith((".", "#", "[", "//")) and " " not in selector and not selector.isalpha():
            if self.cuenta.fallback_s > 0:
                time.sleep(self.cuenta.fallback_s)
            return time.time()

        patron = re.compile(self.cuenta.patron)
        limite = time.time() + self.cuenta.timeout_s
        vista = False

        # Si el selector no existe en la página, no tiene sentido rastrear:
        # se comprueba una vez y se sigue. Antes se esperaba el tiempo entero.
        try:
            if self.page.locator(selector).count() == 0:
                log.info("El selector «%s» no existe aquí; hablo sin esperar", selector)
                if self.cuenta.fallback_s > 0:
                    time.sleep(self.cuenta.fallback_s)
                return time.time()
        except Exception:
            log.info("El selector «%s» no es válido; hablo sin esperar", selector)
            return time.time()

        while time.time() < limite:
            texto = self._texto_cuenta()
            if patron.search(texto or ""):
                vista = True
                time.sleep(0.08)
                continue
            if vista:
                log.info("Cuenta regresiva terminada")
                if self.cuenta.margen_previo_ms:
                    time.sleep(self.cuenta.margen_previo_ms / 1000)
                return time.time()
            time.sleep(0.08)

        if not vista and self.cuenta.fallback_s > 0:
            time.sleep(self.cuenta.fallback_s)
        return time.time()

    def _texto_cuenta(self) -> str:
        try:
            loc = self.page.locator(self.cuenta.selector).first
            if loc.is_visible(timeout=150):
                return loc.inner_text(timeout=150)
        except Exception:
            pass
        return ""
