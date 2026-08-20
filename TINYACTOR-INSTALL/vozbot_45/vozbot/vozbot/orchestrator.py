"""El bucle que juega solo.

Ronda típica:
  1. Lee la pantalla (DOM; OCR + razonamiento si el DOM no basta).
  2. Saca la emoción y la frase entre comillas.
  3. Se la pasa a tu app .py y espera el .wav.
  4. Pulsa «iniciar micrófono» en la web.
  5. Espera la cuenta regresiva.
  6. Reproduce el audio por el micrófono virtual.
  7. Pulsa «detener» al terminar y avanza a la siguiente.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from .audio import MicrofonoEmulado
from .browser import NavegadorWeb
from .config import Accion, Config, Guion
from .extractor import ExtractorGuion
from .ocr import LectorPantalla
from .recorder import GrabadorObs
from .tts_app import GeneradorDeVoz
from .vlm import Razonador

log = logging.getLogger("vozbot.bucle")


class Orquestador:
    def __init__(self, cfg: Config, on_evento=None):
        self.cfg = cfg
        self.on_evento = on_evento or (lambda *_: None)
        self._parar = threading.Event()

        if cfg.plataforma == "android":
            from .android import NavegadorAndroid

            self.nav = NavegadorAndroid(cfg.android, cfg.botones_web, cfg.cuenta_regresiva)
        else:
            self.nav = NavegadorWeb(cfg.navegador, cfg.botones_web, cfg.cuenta_regresiva)
        self.extractor = ExtractorGuion(cfg.extraccion)
        self.lector = LectorPantalla(cfg.ocr)
        self.razonador = Razonador(cfg.razonamiento)
        self.tts = GeneradorDeVoz(cfg.tts_app, self.lector)
        self.mic = MicrofonoEmulado(cfg.microfono_virtual)
        self.obs = GrabadorObs(cfg.obs)

        self._lento = max(1.0, float(getattr(cfg.bucle, "factor_lentitud", 1.0)))
        self._coord_grabar: tuple[int, int] | None = None
        # Qué método funcionó la última vez: se prueba primero, así a partir
        # de la segunda ronda el clic es casi instantáneo.
        self._via_grabar = ""
        self._via_detener = ""
        self._cache_audio: dict[tuple[str, str], Path] = {}
        self._ultima_frase = ""
        self.logs = Path(cfg.bucle.carpeta_logs)
        self.logs.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def _avisar(self, tipo: str, mensaje: str) -> None:
        log.info("%s | %s", tipo, mensaje)
        try:
            self.on_evento(tipo, mensaje)
        except Exception:
            pass

    def detener(self) -> None:
        self._parar.set()
        self.mic.silenciar()

    # ------------------------------------------------------------------
    def ejecutar(self) -> None:
        self.nav.conectar()
        self.obs.conectar()
        self.obs.iniciar()
        self._avisar("inicio", f"Conectado a {self.nav.page.url}")

        sin_avance = 0

        try:
            for ronda in range(1, self.cfg.bucle.max_rondas + 1):
                if self._parar.is_set():
                    break

                # Recorre las pantallas de guion e instrucción hasta llegar al
                # micrófono, quedándose con la frase y la emoción de cada una.
                estado = self._recolectar_hasta_grabar()
                if self._parar.is_set():
                    break

                if not estado:
                    sin_avance += 1
                    if sin_avance >= 3:
                        self._avisar("fin", "No consigo llegar a la pantalla de grabación")
                        break
                    continue
                sin_avance = 0

                etiqueta = f"Toma {estado['toma']} de {estado['total']}" if estado["total"] else f"Ronda {ronda}"
                self._avisar("ronda", f"— {etiqueta} · {estado['emocion']} —")

                self.ronda_de_lectura(
                    Guion(
                        instruccion_detectada=etiqueta,
                        emocion=estado["emocion"],
                        texto_a_leer=estado["frase"],
                        listo_para_hablar=True,
                        confianza=0.9,
                    )
                )

                if estado["total"] and estado["toma"] >= estado["total"]:
                    self._avisar("juego", "Última toma de esta frase completada")
        finally:
            self.obs.detener()
            self.nav.cerrar()
            self._avisar("fin", "Sesión terminada")

    # ------------------------------------------------------------------
    def _recolectar_hasta_grabar(self, timeout_s: float = 90.0) -> dict | None:
        """Avanza pantalla a pantalla hasta el micrófono.

        El juego reparte la información: primero la frase entre comillas con su
        «Entendido», después la acotación de la emoción con otro «Entendido», y
        al final la pantalla de grabación con «Toma N de M». Aquí se recoge
        cada pieza a medida que aparece y se pulsa avanzar hasta llegar.
        """
        frase = self._ultima_frase
        emocion = ""
        toma = total = 0
        limite = time.time() + timeout_s
        huella_previa = ""
        vuelta = 0

        while time.time() < limite and not self._parar.is_set():
            vuelta += 1
            if vuelta == 1:
                self._avisar("paso", "Leyendo la pantalla…")
            texto = self.nav.texto_pagina()
            profundo = self.nav.texto_profundo()
            completo = f"{texto}\n{profundo}"
            huella = self._huella_pantalla()

            datos = self.extractor.extraer(texto, "", profundo)

            # En la pantalla de grabación manda el tamaño de letra: el guion
            # es el texto grande del centro. Es más fiable que cualquier
            # heurística de longitud, y funciona con frases cortas.
            if self._hay_boton_grabar():
                grande = self.extractor.frase_de_grabacion(self.nav.inventario())
                if grande:
                    datos["texto_a_leer"] = grande
                    datos["fuente"] = "pantalla-grabacion"

            # Respaldo: si el texto de la página no trajo el guion, buscarlo
            # entre los elementos del inventario (a veces inner_text falla en
            # apps que pintan por capas).
            if not datos["texto_a_leer"] and self.extractor.numero_toma(completo):
                inv = self.nav.inventario()
                suelta = self.extractor.frase_de_grabacion(inv)
                if not suelta:
                    largos = "\n".join(
                        c.get("etiqueta", "") for c in inv if c.get("etiqueta")
                    )
                    suelta = self.extractor.frase_sin_comillas(largos)
                if suelta:
                    datos["texto_a_leer"] = suelta
                    datos["fuente"] = "pantalla-grabacion"

            # --- número de toma: es lo que marca cuándo empieza una escena
            #     nueva. La emoción llega en su propia pantalla ANTES que la
            #     frase, así que no puede borrarse al aparecer el guion.
            marcador = self.extractor.numero_toma(completo)
            if marcador:
                nueva_toma, nuevo_total = marcador
                if toma and nueva_toma != toma:
                    frase, emocion = "", ""       # escena nueva: todo limpio
                toma, total = nueva_toma, nuevo_total

            candidata = datos["texto_a_leer"]
            if candidata and candidata != frase:
                if self.extractor.es_frase_completa(candidata) or not frase:
                    frase = candidata
                    self._ultima_frase = frase
                    self._avisar("guion", f"Frase: «{frase[:70]}»")
                else:
                    # palabra suelta entre comillas: es una indicación de
                    # énfasis sobre la MISMA frase, no una línea nueva
                    self._avisar("guion", f"Énfasis en «{candidata}» · mantengo la frase")

            # --- emoción: manda la que anuncia la pantalla («EMOCIÓN: Sereno»
            #     o «Tu Voz Natural»); si no la hay, se busca en el catálogo.
            candidata = self.extractor.emocion_etiquetada(completo)
            if not candidata:
                if frase and self.extractor.contiene_frase(completo, frase):
                    candidata = self.extractor.emocion_cerca_de(completo, frase)
                else:
                    candidata = self.extractor.emocion(completo, ultima=True)
                if candidata == self.cfg.extraccion.emocion_por_defecto:
                    candidata = ""
            if candidata and candidata != emocion:
                emocion = candidata
                self._avisar("guion", f"Emoción: {emocion}")

            # --- ¿ya estamos en la pantalla de grabar?
            if self._hay_boton_grabar():
                if frase:
                    if not emocion:
                        emocion = self.cfg.extraccion.emocion_por_defecto
                        self._avisar("aviso", "No vi acotación; uso neutral")
                    return {
                        "frase": frase,
                        "emocion": emocion,
                        "toma": toma or 1,
                        "total": total,
                    }
                vistos = [
                    (c.get("etiqueta") or "")[:40]
                    for c in self.nav.inventario()
                    if c.get("etiqueta") and c.get("hojas", True)
                ][:6]
                self._avisar("aviso", f"Veo el micrófono pero no leo la frase. Textos: {vistos}")

            # --- avanzar una pantalla
            if huella == huella_previa:
                vueltas_iguales = getattr(self, "_vueltas_iguales", 0) + 1
                self._vueltas_iguales = vueltas_iguales
                if vueltas_iguales in (3, 6, 9):
                    self._avisar(
                        "aviso",
                        f"Llevo {vueltas_iguales} intentos sin que cambie la pantalla. "
                        f"Frase={'sí' if frase else 'no'} · emoción={emocion or '—'}",
                    )
                if vueltas_iguales >= 12:
                    self._avisar("error", "La pantalla no avanza; me detengo para no dar vueltas")
                    return None
            else:
                self._vueltas_iguales = 0

            huella_previa = huella
            if not self._avanzar(huella, timeout_s=15.0):
                time.sleep(0.8)

        self._avisar("aviso", "No llegué a la pantalla de grabación a tiempo")
        return None

    def leer_pantalla(self, ronda: int) -> Guion:
        """DOM primero; OCR + modelo de razonamiento solo si hace falta."""
        self.nav.cerrar_modales()
        texto = self.nav.texto_pagina()
        profundo = self.nav.texto_profundo()
        datos = self.extractor.extraer(texto, self.nav.html_pagina(), profundo)

        guion = Guion(
            instruccion_detectada=datos["instruccion"],
            emocion=datos["emocion"],
            texto_a_leer=datos["texto_a_leer"],
            listo_para_hablar=bool(datos["texto_a_leer"]),
            confianza=0.9 if datos["texto_a_leer"] else 0.0,
        )

        # Ronda de repetición: la misma línea vuelve a salir, ya sin comillas,
        # y encima trae la acotación nueva. Sin esto el bot se queda parado.
        if not guion.texto_a_leer and self._ultima_frase:
            completo = f"{texto}\n{profundo}"
            if self.extractor.contiene_frase(completo, self._ultima_frase):
                emocion = self.extractor.emocion_cerca_de(completo, self._ultima_frase)
                # Sin acotación nueva encima no es una ronda: es la pantalla de
                # grabación o de espera. No hay que generar nada.
                if emocion == self.cfg.extraccion.emocion_por_defecto:
                    self._avisar("paso", "La frase sigue en pantalla pero sin acotación nueva")
                else:
                    guion = Guion(
                        instruccion_detectada=f"Repetir con emoción: {emocion}",
                        emocion=emocion,
                        texto_a_leer=self._ultima_frase,
                        listo_para_hablar=True,
                        confianza=0.75,
                    )
                    datos["fuente"] = "frase-repetida"
                    self._avisar("juego", f"Misma frase, ahora en «{emocion}»")

        if guion.texto_a_leer:
            self._ultima_frase = guion.texto_a_leer

        necesita_ia = self.cfg.razonamiento.activo and (
            not guion.texto_a_leer or not self.cfg.razonamiento.solo_si_falla_dom
        )
        if necesita_ia:
            captura = str(self.logs / f"ronda_{ronda:03d}.png")
            self.nav.captura(captura)
            contexto = (texto or profundo)[:4000] or self.lector.texto_completo()
            propuesta = self.razonador.analizar(captura, contexto)
            if propuesta.texto_a_leer:
                self._avisar("ia", f"Razonamiento: {propuesta.instruccion_detectada[:80]}")
                guion = propuesta
            elif propuesta.acciones:
                guion.acciones = propuesta.acciones

        self._avisar(
            "lectura",
            f"[{datos['fuente']}] emoción={guion.emocion} · texto=«{guion.texto_a_leer[:60]}»",
        )
        if datos["fuente"] == "vacio":
            (self.logs / f"visto_{ronda:03d}.txt").write_text(
                f"--- texto visible ---\n{texto}\n\n--- profundo ---\n{profundo}",
                encoding="utf-8",
            )
        return guion

    # ------------------------------------------------------------------
    def ronda_de_lectura(self, guion: Guion) -> None:
        # El audio se sintetiza AHORA, cuando ya conocemos frase y emoción:
        # el juego las entrega en pantallas distintas.
        wav = self._audio_para(guion.texto_a_leer, guion.emocion)
        if not wav:
            self._avisar("error", "Sin audio; salto esta ronda")
            self.nav.siguiente()
            return

        self._avisar("audio", f"{wav.name} ({self.mic.duracion(wav):.1f}s)")

        # El mismo audio sirve para los reintentos: no hay que regenerarlo.
        for intento in range(1, self.cfg.bucle.max_reintentos_toma + 2):
            if self._parar.is_set():
                return
            if intento > 1:
                self._avisar("reintento", f"Repitiendo la toma ({intento})")

            if not self._grabar_una_toma(wav):
                return

            respuesta = self._leer_respuesta()
            if respuesta == "ok":
                break
            if intento > self.cfg.bucle.max_reintentos_toma:
                self._avisar("aviso", "Agoté los reintentos; sigo adelante")
                break

            self._reaccionar(respuesta)

            # El audio ya está listo (mismo guion, mismo wav en memoria), así
            # que al pulsar «regrabar» se puede grabar de inmediato.
            from . import roles

            adelante = roles.mejor(self.nav.inventario(), "avanzar", minimo=18)
            if adelante:
                self._avisar("juego", f"Sigo adelante con «{adelante.get('etiqueta','')[:30]}»")
                self.nav.click_en(adelante)
                break

            boton = self._boton_regrabar()
            if boton:
                etiqueta = boton.get("etiqueta", "")[:34]
                self._esperar_boton_listo(timeout_s=6.0)
                if self.nav.click_en(boton):
                    self._avisar("juego", f"Repito la toma con «{etiqueta}» (audio ya listo)")
                else:
                    self.nav.click_robusto(etiqueta)
            else:
                for b in self.cfg.botones_web.reintentar:
                    if b and self.nav.existe_control(b, timeout_ms=200):
                        self.nav.click_robusto(b)
                        break
            time.sleep(0.8)

        huella = self._huella_pantalla()
        self._avisar("paso", "Avanzando a la siguiente pantalla…")
        self._avanzar(huella)
        self._registrar(guion, wav)

    # ------------------------------------------------------------------
    def _audio_para(self, texto: str, emocion: str) -> Path | None:
        """Genera el audio, o reusa el de esta misma frase y emoción.

        El juego pide la misma línea varias veces con emociones distintas: cada
        combinación se sintetiza una sola vez y se guarda.
        """
        clave = (texto.strip(), emocion.strip().lower())
        if self.cfg.bucle.reusar_audio_misma_frase:
            guardado = self._cache_audio.get(clave)
            if guardado and guardado.is_file():
                self._avisar("audio", f"Reuso el audio de «{emocion}» (ya lo tenía)")
                return guardado

        wav = self.tts.generar(texto, emocion)
        if wav:
            self._cache_audio[clave] = wav
        return wav

    def _leer_respuesta(self) -> str:
        """Qué dice el juego tras la toma: ok | saturado | bajo | repetir."""
        r = self.cfg.retroalimentacion
        if not r.activa:
            return "ok" if not self._toma_rechazada() else "repetir"

        texto = (self.nav.texto_pagina() + "\n" + self.nav.texto_profundo()).lower()
        for etiqueta, frases in (("saturado", r.saturado), ("bajo", r.bajo), ("repetir", r.repetir)):
            for frase in frases:
                if frase and frase.lower() in texto:
                    self._avisar("juego", f"El juego dice: {frase}")
                    return etiqueta
        return "repetir" if self._toma_rechazada() else "ok"

    def _reaccionar(self, respuesta: str) -> None:
        """Ajusta el volumen según lo que pidió el juego."""
        r = self.cfg.retroalimentacion
        if respuesta == "saturado":
            nueva = self.mic.ajustar_ganancia(-r.paso_db, r.ganancia_min_db, r.ganancia_max_db)
            self._avisar("volumen", f"Bajo el volumen a {nueva:+.1f} dB")
        elif respuesta == "bajo":
            nueva = self.mic.ajustar_ganancia(+r.paso_db, r.ganancia_min_db, r.ganancia_max_db)
            self._avisar("volumen", f"Subo el volumen a {nueva:+.1f} dB")

    def _grabar_una_toma(self, wav: Path) -> bool:
        """Prepara el audio, graba y detiene.

        En modo «archivo» el audio no se reproduce: se escribe donde Chrome lo
        lee como micrófono y el navegador lo entrega solo al empezar a grabar.
        Por eso el archivo debe estar en su sitio ANTES de pulsar.
        """
        modo_archivo = self.cfg.microfono_virtual.modo == "archivo"

        if modo_archivo:
            duracion = self.mic.escribir_para_chrome(wav)
            if not duracion:
                self._avisar("error", "No pude preparar el micrófono falso")
                return False
            datos = sr = None
        else:
            duracion = self.mic.duracion(wav)
            try:
                datos, sr = self.mic.preparar(wav)
            except Exception as e:
                self._avisar("error", f"No pude preparar el audio: {e}")
                return False

        self._despejar_hasta_microfono()

        # Guardamos DÓNDE está el botón antes de pulsarlo: si el juego cambia
        # de diseño o de etiqueta al grabar, el punto sigue siendo el mismo.
        self._coord_grabar = self.nav.coords_de_cualquiera(
            self.cfg.botones_web.iniciar_microfono
        )

        self._avisar("paso", "Pulsando «grabar»…")
        if self._coord_grabar:
            self._avisar("paso", f"Botón de grabar en {self._coord_grabar}")
        if not self._pulsar_grabar():
            return False

        # Si se pulsó por una vía distinta al selector, refrescar la posición
        # para que el «detener» tenga las coordenadas buenas.
        nuevas = self.nav.coords_de_cualquiera(self.cfg.botones_web.iniciar_microfono)
        if nuevas:
            self._coord_grabar = nuevas

        self.nav.esperar_cuenta_regresiva()

        inicio = time.time()
        if modo_archivo:
            # Chrome ya está inyectando el archivo: basta con esperar.
            self._avisar("paso", f"Chrome reproduce el micrófono falso ({duracion:.1f}s)…")
            fin_previsto = inicio + duracion + 0.4
            while time.time() < fin_previsto and not self._parar.is_set():
                time.sleep(0.15)
        else:
            self._avisar("paso", f"Hablando {duracion:.1f}s…")
            hilo = threading.Thread(
                target=self.mic.reproducir_preparado, args=(datos, sr), daemon=True
            )
            hilo.start()
            hilo.join(timeout=duracion + 10)
            if hilo.is_alive():
                self._avisar("error", "La reproducción se colgó; revisa el dispositivo")
                self.mic.silenciar()
                hilo.join(timeout=3)
                return False

        transcurrido = time.time() - inicio
        self._avisar("micro", f"Toma de {transcurrido:.1f}s")
        if not modo_archivo and transcurrido < duracion * 0.5:
            self._avisar("aviso", "Terminó demasiado rápido: revisa el dispositivo de audio")

        time.sleep(0.25)
        return self._detener_de_verdad()

    LEXICO_DETENER = [
        "detener grabacion", "detener grabación", "detener", "parar grabacion",
        "parar grabación", "parar", "terminar grabacion", "terminar grabación",
        "terminar", "finalizar", "stop recording", "stop", "listo",
    ]

    def _buscar_boton_detener(self) -> str | None:
        """Encuentra en pantalla cualquier botón que sirva para detener.

        El botón blanco que dice «DETENER GRABACION» es un div con texto, no
        lleva aria-label, así que hay que localizarlo por lo que dice.
        """
        mejor = None
        mejor_puntos = 0
        for c in self.nav.listar_controles():
            etiqueta = c["etiqueta"]
            if self.nav._prohibido(etiqueta):
                continue
            plano = etiqueta.lower().strip()
            for i, palabra in enumerate(self.LEXICO_DETENER):
                if palabra in plano:
                    # las frases largas y específicas puntúan más
                    puntos = len(self.LEXICO_DETENER) - i
                    if puntos > mejor_puntos:
                        mejor, mejor_puntos = etiqueta, puntos
                    break
        if mejor:
            self._avisar("ia", f"Botón de detener detectado: «{mejor}»")
        return mejor

    # Palabras que delatan el botón de grabar, para buscarlo por texto o
    # descripción cuando el selector no cala.
    PALABRAS_GRABAR = ["grabar", "grabación", "grabacion", "record", "micrófono",
                       "microfono", "hablar", "empezar a grabar"]

    def _hay_boton_grabar(self) -> bool:
        """¿Estamos en la pantalla de grabación?

        No basta con los selectores configurados: en el formato nuevo el
        botón es un círculo sin etiqueta, así que también se reconoce por su
        forma. Sin esto el bot no se daba cuenta de que ya había llegado y
        seguía buscando botones de avance que no existen.
        """
        if self.nav.alguno_visible(self.cfg.botones_web.iniciar_microfono):
            return True
        from . import roles

        return bool(roles.clasificar(self.nav.inventario(), "grabar", minimo=10))

    def _pulsar_grabar(self) -> bool:
        """Pulsa grabar y comprueba que arrancó, en el menor tiempo posible.

        Se descartan de antemano las vías cuyo control no está en pantalla, y
        se recuerda la que funcionó para probarla primero la próxima vez.
        """
        antes = self._huella_pantalla()
        botones = [b for b in self.cfg.botones_web.iniciar_microfono if b]
        estaba_visible = self.nav.alguno_visible(botones)

        vias: list[tuple[str, object]] = []

        # Solo se encolan los botones que EXISTEN ahora mismo: comprobarlo
        # cuesta milisegundos y evita gastar un ciclo entero en cada ausente.
        # Directo al clic robusto: empieza por coordenadas del DOM + ratón
        # real, que es el que acierta. Probar antes click_texto solo gastaba
        # tiempo en localizadores que no encuentran nada.
        presentes = [b for b in botones if self.nav.existe_control(b, timeout_ms=200)]
        for b in presentes:
            vias.append((f"«{b}»", lambda x=b: self.nav.click_robusto(x)))

        if self.nav.buscar_por_palabras(self.PALABRAS_GRABAR):
            vias.append(
                ("palabras clave", lambda: self.nav.click_por_palabras(self.PALABRAS_GRABAR))
            )
        if self._coord_grabar:
            x, y = self._coord_grabar
            vias.append((f"coordenadas ({x}, {y})", lambda: self.nav.click_coord(x, y)))
        vias.append(("círculo", self._click_control_circular))

        # la que funcionó la última vez, primero
        recordada = getattr(self, "_via_grabar", "")
        if recordada:
            vias.sort(key=lambda v: v[0] != recordada)

        for descripcion, accion in vias:
            if self._parar.is_set():
                return False
            try:
                if not accion():
                    continue
            except Exception:
                continue

            for _ in range(int(8 * self._lento)):
                time.sleep(0.15)
                if (
                    self._huella_pantalla() != antes
                    or self._buscar_boton_detener()
                    or (estaba_visible and not self.nav.alguno_visible(botones))
                ):
                    self._avisar("paso", f"Grabar: {descripcion}")
                    self._via_grabar = descripcion
                    return True

            # El clic SÍ se ejecutó, aunque no veamos el cambio. Aquí no se
            # prueba otra vía: pulsar el mismo botón dos veces iniciaría y
            # detendría la grabación en el mismo instante.
            self._avisar(
                "paso", f"Grabar: {descripcion} (sin cambio visible, sigo igualmente)"
            )
            self._via_grabar = descripcion
            return True

        self._avisar("error", "No conseguí pulsar «grabar» por ningún medio")
        return False

    def _grabacion_terminada(self, huella_antes: str) -> bool:
        """Señales fiables de que la grabación paró.

        Solo dos cuentan: que la pantalla cambie de verdad (la huella ignora
        cronómetros) o que aparezca un botón de avance, que únicamente existe
        cuando la toma ya terminó. La ausencia del botón de detener NO sirve
        como prueba: en muchos diseños ese botón no tiene texto y «no
        encontrarlo» no significa que haya parado.
        """
        if self._huella_pantalla() != huella_antes:
            return True
        return self.nav.alguno_visible(
            self.cfg.botones_web.avanzar + self.cfg.botones_web.siguiente
        )

    def _detener_de_verdad(self) -> bool:
        """Detiene la grabación y lo comprueba, sin perder tiempo.

        Igual que al grabar: se descarta lo que no está en pantalla y se
        recuerda el método que funcionó.
        """
        antes = self._huella_pantalla()
        vias = self._vias_para_detener()

        recordada = getattr(self, "_via_detener", "")
        if recordada:
            vias.sort(key=lambda v: v[0] != recordada)

        for vuelta in range(1, 3):
            for descripcion, accion in vias:
                if self._parar.is_set():
                    return False
                try:
                    if not accion():
                        continue
                except Exception:
                    continue

                for _ in range(int(8 * self._lento)):
                    time.sleep(0.15)
                    if self._grabacion_terminada(antes):
                        self._avisar("paso", f"Detener: {descripcion}")
                        self._via_detener = descripcion
                        return True

            if vuelta == 1:
                self._avisar("aviso", "Sigue grabando; repito la ronda de intentos")
                time.sleep(0.4)

        self._avisar("aviso", "No pude detenerla; espero a que termine sola")
        self.nav.esperar_control(
            self.cfg.botones_web.avanzar + self.cfg.botones_web.siguiente,
            timeout_s=self.cfg.bucle.espera_fin_grabacion_s,
        )
        return True

    def _vias_para_detener(self):
        """Formas de pulsar detener, solo las que existen ahora en pantalla."""
        from . import roles

        vias: list[tuple[str, object]] = []
        ya_vistos: set[str] = set()          # no probar la misma etiqueta dos veces

        # Reconocimiento por papel, igual que al grabar
        for candidato in roles.clasificar(self.nav.inventario(), "detener")[:2]:
            etiqueta = candidato.get("etiqueta") or f"{candidato['w']}x{candidato['h']}"
            ya_vistos.add(etiqueta.strip().lower())
            vias.append(
                (f"reconocido «{etiqueta[:28]}» ({candidato['puntos']}p)",
                 lambda c=candidato: self.nav.click_en(c))
            )

        detectado = self._buscar_boton_detener()
        if detectado and detectado.strip().lower() not in ya_vistos:
            ya_vistos.add(detectado.strip().lower())
            vias.append(
                (f"detectado «{detectado}»", lambda d=detectado: self.nav.click_robusto(d))
            )

        for boton in self.cfg.botones_web.detener_microfono:
            if not boton or boton.strip().lower() in ya_vistos:
                continue
            if self.nav.existe_control(boton, timeout_ms=200):
                ya_vistos.add(boton.strip().lower())
                vias.append((f"configurado «{boton}»", lambda b=boton: self.nav.click_robusto(b)))

        for boton in self.cfg.botones_web.iniciar_microfono:
            if boton and self.nav.existe_control(boton, timeout_ms=200):
                vias.append(
                    (f"mismo botón «{boton}»", lambda b=boton: self.nav.click_robusto(b))
                )

        if self._coord_grabar:
            x, y = self._coord_grabar
            vias.append((f"coordenadas ({x}, {y})", lambda: self.nav.click_coord(x, y)))

        vias.append(("círculo", self._click_control_circular))
        return vias

    def _click_control_circular(self) -> bool:
        """Último recurso: el círculo del micrófono, buscado por su forma.

        No se descarta por posición: se puntúa. Los botones de salir suelen ser
        pequeños y estar arriba a la derecha, así que puntúan bajo; el del
        micrófono es grande y centrado, y puntúa alto.
        """
        try:
            hallado = self.nav.page.evaluate(
                """() => {
                    const W = innerWidth, H = innerHeight;
                    const veto = /salir|cerrar|exit|volver|atras|atrás|men[uú]|silencio|mute/i;
                    let mejor = null;
                    for (const el of document.querySelectorAll('*')) {
                      if (el.offsetParent === null) continue;
                      const r = el.getBoundingClientRect();
                      if (r.width < 40 || r.width > 260) continue;
                      if (Math.abs(r.width - r.height) > 14) continue;   // ancho ≈ alto
                      const est = getComputedStyle(el);
                      const radio = parseFloat(est.borderRadius) || 0;
                      if (radio < r.width * 0.25) continue;              // redondeado

                      const etiqueta = (el.getAttribute('aria-label') || '') + ' ' +
                                       (el.innerText || '');
                      if (veto.test(etiqueta)) continue;

                      const cx = r.left + r.width / 2;
                      const cy = r.top + r.height / 2;
                      // grande, centrado horizontalmente y hacia abajo = más probable
                      const puntos = r.width
                                   - Math.abs(cx - W / 2) * 0.6
                                   + (cy / H) * 60;
                      if (!mejor || puntos > mejor.puntos) {
                        mejor = {x: Math.round(cx), y: Math.round(cy),
                                 w: Math.round(r.width), puntos: Math.round(puntos)};
                      }
                    }
                    return mejor;
                }"""
            )
        except Exception:
            return False

        if not hallado:
            return False
        self._avisar(
            "ia",
            f"Círculo de {hallado['w']}px en ({hallado['x']}, {hallado['y']})",
        )
        return self.nav.click_coord(hallado["x"], hallado["y"])

    def _despejar_hasta_microfono(self, timeout_s: float = 12.0) -> None:
        """Si el botón de grabar no está, pulsa «Entendido» y compañía.

        Se comprueba con el reconocimiento por forma, no solo con los
        selectores: si no, un botón como «Empezar a grabar» se pulsaba aquí
        como avance y otra vez después como grabar.
        """
        if self._hay_boton_grabar():
            return
        self._avisar("paso", "El micrófono no está a la vista; despejo la pantalla")
        limite = time.time() + timeout_s
        while time.time() < limite and not self._parar.is_set():
            for texto in (
                self.cfg.botones_web.avanzar
                + self.cfg.botones_web.siguiente
                + self.cfg.botones_web.cerrar_modal
            ):
                if texto and self.nav.existe_control(texto, timeout_ms=200):
                    self.nav.click_robusto(texto)
                    time.sleep(0.8)
                    break
            else:
                self._avanzar_por_sinonimos()
            if self._hay_boton_grabar():
                self._avisar("paso", "Ya veo el botón de grabar")
                return
            time.sleep(0.5)

    def _boton_regrabar(self) -> dict | None:
        """Encuentra el aviso de repetir la toma, se llame como se llame.

        «REGRABAR ESTA TOMA», «Grabar de nuevo», «Volver a grabar»… todas las
        variantes se reconocen por significado, no por texto exacto.
        """
        from . import roles

        candidatos = roles.clasificar(self.nav.inventario(), "reintentar", minimo=10)
        return candidatos[0] if candidatos else None

    def _toma_rechazada(self) -> bool:
        """¿Apareció el aviso de repetir la toma?"""
        if self._boton_regrabar():
            return True
        for texto in self.cfg.botones_web.reintentar:
            if not texto:
                continue
            if texto.startswith((".", "#", "[", "//")) or texto.lower().startswith(
                ("css=", "xpath=", "coord=")
            ):
                continue
            if self.nav.existe_texto(texto):
                return True
        return False

    # ------------------------------------------------------------------
    def _acciones_alternativas(self, guion: Guion) -> bool:
        """Ejecuta el plan que propuso el modelo cuando no hay frase que leer."""
        hecho = False
        for a in guion.acciones:
            if self._parar.is_set():
                break
            hecho |= self._ejecutar(a)
        return hecho

    def _ejecutar(self, a: Accion) -> bool:
        if a.tipo == "click_texto" and a.texto:
            return self.nav.click_texto([a.texto])
        if a.tipo == "click_coord" and a.x is not None and a.y is not None:
            return self.nav.click_coord(a.x, a.y)
        if a.tipo == "esperar":
            time.sleep(min(a.segundos, 10))
            return True
        if a.tipo == "scroll":
            self.nav.page.mouse.wheel(0, 600)
            return True
        if a.tipo == "fin":
            self.detener()
            return True
        return False

    def _huella_pantalla(self) -> str:
        """Resumen de la pantalla, para saber si cambió DE VERDAD.

        Se quitan los números: los cronómetros y contadores de la pantalla de
        grabación cambian a cada segundo y harían creer al bot que avanzó
        cuando en realidad sigue en el mismo sitio.
        """
        try:
            texto = (self.nav.texto_pagina() or "")[:1500]
        except Exception:
            texto = ""
        texto = re.sub(r"[\d:.,%]+", "", texto)
        return " ".join(texto.split())

    # Palabras que significan «seguir adelante», por familias. Cuanto más alta
    # la puntuación, más seguro es que ese botón avance la partida.
    # «ENTENDIDO… 2S», «LISTO 3s», «Continuar (1)»: el botón está bloqueado
    # hasta que la cuenta llega a cero.
    PATRON_ESPERA = re.compile(r"(\d+)\s*[sS]\b|\((\d+)\)\s*$|…\s*(\d+)")

    def _buscar_avance_por_palabras(self) -> bool:
        """Busca botones de avance por palabras sueltas, no por texto exacto.

        «SEGUIR GANANDO MONEDAS» suele venir partido en varias líneas o con
        un icono dentro, así que la coincidencia exacta falla.
        """
        return self.nav.click_por_palabras(self.cfg.botones_web.palabras_avance)

    # Señales de que hay un premio que hay que tocar varias veces.
    PISTAS_PREMIO = [
        "premio", "recompensa", "desbloque", "reclama", "felicidades", "ganaste",
        "conseguiste", "sigue tocando", "cofre", "reward", "claim",
    ]

    def _hay_premio(self) -> bool:
        texto = self._huella_pantalla().lower()
        return any(p in texto for p in self.PISTAS_PREMIO)

    def _desbloquear_premio(self) -> bool:
        """Toca el elemento más grande hasta que la pantalla de premio ceda."""
        grande = self.nav.buscar_elemento_grande()
        if not grande:
            return False

        etiqueta = grande.get("texto") or grande.get("etiqueta") or f"{grande['w']}x{grande['h']}"
        self._avisar("juego", f"Toco «{etiqueta}» para desbloquear el premio")

        for toque in range(1, self.cfg.bucle.toques_para_premios + 1):
            if self._parar.is_set():
                return False
            self.nav.click_coord(grande["x"], grande["y"])
            time.sleep(0.5)

            if self.nav.alguno_visible(
                self.cfg.botones_web.avanzar + self.cfg.botones_web.siguiente
            ) or self.nav.buscar_por_palabras(self.cfg.botones_web.palabras_avance):
                self._avisar("juego", f"Desbloqueado tras {toque} toque(s)")
                return True
            if not self._hay_premio():
                return True

            nuevo = self.nav.buscar_elemento_grande()
            if nuevo:
                grande = nuevo
        return False

    PISTAS_ELECCION = [
        "elige una escena", "elige la escena", "escoge una escena", "elige",
        "escoge", "selecciona", "escena",
    ]

    def _pantalla_de_eleccion(self) -> bool:
        texto = self._huella_pantalla().lower()
        return any(p in texto for p in self.PISTAS_ELECCION)

    def _elegir_opcion(self) -> bool:
        """Pantalla «Elige una escena»: dos círculos con A y B.

        Solo actúa si la pantalla DICE que hay que elegir: sin esa condición,
        cualquier letra suelta se tomaba por una opción.
        """
        if not self._pantalla_de_eleccion():
            return False

        opciones = self.nav.buscar_opciones_letra(self.cfg.botones_web.opciones)
        if len(opciones) < 2:
            return False
        anchos = [o["w"] for o in opciones]
        if max(anchos) > min(anchos) * 1.6:
            return False

        elegida = opciones[0]
        self._avisar(
            "juego",
            f"Elijo la opción «{elegida['letra']}» de {len(opciones)} "
            f"({elegida['w']}x{elegida['h']} en {elegida['x']},{elegida['y']})",
        )
        antes = self._huella_pantalla()
        for _ in range(3):
            self.nav.click_coord(elegida["x"], elegida["y"])
            time.sleep(0.6)
            if self._huella_pantalla() != antes:
                return True
        return False

    def _esperar_boton_listo(self, timeout_s: float = 8.0) -> bool:
        """Si algún botón muestra una cuenta atrás, espera a que termine.

        El juego desactiva los botones un par de segundos. Pulsarlos antes no
        hace nada, y el bot los daba por inservibles.
        """
        from . import roles

        limite = time.time() + timeout_s
        aviso = False

        while time.time() < limite and not self._parar.is_set():
            contando = None
            for c in self.nav.inventario():
                etiqueta = (c.get("etiqueta") or "").strip()
                if not etiqueta or len(etiqueta) > 40:
                    continue
                if not self.PATRON_ESPERA.search(etiqueta):
                    continue
                if roles.puntuar({"etiqueta": etiqueta}, "avanzar") > 0:
                    contando = etiqueta
                    break

            if not contando:
                return aviso
            if not aviso:
                self._avisar("paso", f"«{contando}» aún está bloqueado; espero")
                aviso = True
            time.sleep(0.4)
        return aviso

    def _descartar_promocion(self) -> bool:
        """Sale de las pantallas que ofrecen otro modo de juego.

        Tras la última toma aparece «Te presentamos conversaciones» con dos
        botones: uno destacado que entra al modo nuevo, y uno discreto para
        seguir. Aquí se pulsa siempre el discreto.
        """
        for texto in self.cfg.botones_web.descartar:
            if not texto or not self.nav.existe_control(texto, timeout_ms=250):
                continue
            antes = self._huella_pantalla()
            self.nav.click_robusto(texto)
            self._avisar("juego", f"Salgo de la promoción con «{texto}»")
            for _ in range(8):
                time.sleep(0.2)
                if self._huella_pantalla() != antes:
                    return True
        return False

    def _puntuar_avance(self, etiqueta: str) -> int:
        """Cuánto se parece a un botón de «seguir adelante»."""
        from . import roles

        return roles.puntuar({"etiqueta": etiqueta}, "avanzar")

    def _avanzar_por_sinonimos(self) -> bool:
        """Busca en pantalla cualquier botón que signifique «continuar».

        Se usa cuando ninguno de los botones configurados aparece. Nunca elige
        uno de la lista de prohibidos, aunque puntúe alto por otra palabra.
        """
        from . import roles

        # minimo alto: un sinónimo dudoso pulsado en bucle es peor que no
        # pulsar nada. «Reiniciar» y compañía no llegan a este umbral.
        candidatos = [
            (c["puntos"], c) for c in roles.clasificar(self.nav.inventario(), "avanzar", minimo=8)
        ]

        if not candidatos:
            return False

        # a igualdad de puntos, el que esté más abajo en la pantalla: los
        # botones de avance suelen ir al pie
        candidatos.sort(key=lambda t: (t[0], t[1]["y"]), reverse=True)
        puntos, elegido = candidatos[0]
        etiqueta = (elegido.get("etiqueta") or "").strip()
        if not etiqueta:
            return self.nav.click_en(elegido)
        self._avisar("ia", f"Sinónimo de avance detectado: «{etiqueta}» ({puntos} pts)")

        # por coordenadas: más fiable que buscarlo otra vez por su texto
        if self.nav.click_en(elegido):
            return True
        return self.nav.click_robusto(etiqueta)

    def _avanzar(self, huella_previa: str | None = None, timeout_s: float = 30.0) -> bool:
        timeout_s *= self._lento
        """Pulsa hasta que la pantalla cambie de verdad.

        La huella se toma SIEMPRE con el mismo método, aquí dentro: comparar
        huellas calculadas de formas distintas hacía creer al bot que había
        avanzado sin haber pulsado nada.
        """
        antes = self._huella_pantalla() if huella_previa is None else huella_previa
        botones = [
            b
            for b in (
                self.cfg.botones_web.avanzar
                + self.cfg.botones_web.siguiente
                + self.cfg.botones_web.cerrar_modal
            )
            if b
        ]
        limite = time.time() + timeout_s
        pulsados = 0
        # Si un botón no hace nada, no insistir con él: se prueba el siguiente.
        inutiles: set[str] = set()

        while time.time() < limite and not self._parar.is_set():
            # Solo cuenta como avance si YA hubo al menos un clic. Sin esta
            # condición, cualquier repintado se tomaba por pantalla nueva.
            if pulsados and self._huella_pantalla() != antes:
                self._avisar("paso", f"Pantalla nueva tras {pulsados} clic(s)")
                return True

            # Antes que nada: si hay una promoción de otro modo, salir de ahí.
            if self._descartar_promocion():
                pulsados += 1
                continue

            # Aviso de toma rechazada («La calidad de tu audio fue demasiado
            # baja»): se pulsa VOLVER A GRABAR y el ciclo sigue solo.
            # Si junto al aviso hay una salida hacia adelante, se prefiere:
            # repetir la toma cuesta tiempo y el juego ya la dio por buena.
            from . import roles

            seguir = roles.mejor(self.nav.inventario(), "avanzar", minimo=18)
            rehacer = None if seguir else self._boton_regrabar()
            if seguir and self._boton_regrabar():
                self._avisar("juego", f"Prefiero «{seguir.get('etiqueta','')[:30]}» a regrabar")
            if rehacer:
                etiqueta = (rehacer.get("etiqueta") or "")[:34]
                self._esperar_boton_listo(timeout_s=6.0)
                if self.nav.click_en(rehacer):
                    pulsados += 1
                    self._avisar("juego", f"Toma rechazada: pulso «{etiqueta}»")
                    for _ in range(8):
                        time.sleep(0.25)
                        if self._huella_pantalla() != antes:
                            return True
                    continue

            # Reconocimiento por papel: encuentra el botón de avanzar aunque
            # el texto no esté en la lista configurada.
            from . import roles

            inventario = self.nav.inventario()
            reconocido = roles.mejor(inventario, "avanzar")

            objetivo = None
            for texto in botones:
                if texto.strip().lower() in inutiles:
                    continue
                if self.nav.existe_control(texto, timeout_ms=350):
                    objetivo = texto
                    break

            if reconocido and reconocido.get("etiqueta", "").strip().lower() in inutiles:
                reconocido = None

            if reconocido and not objetivo:
                etiqueta = reconocido.get("etiqueta", "")[:30]
                if self.nav.click_en(reconocido):
                    pulsados += 1
                    self._avisar("paso", f"Avanzo por «{etiqueta}» ({reconocido['puntos']}p)")
                    cambio = False
                    for _ in range(8):
                        time.sleep(0.25)
                        if self._huella_pantalla() != antes:
                            cambio = True
                            break
                    if cambio:
                        self._avisar("paso", f"Pantalla nueva tras {pulsados} clic(s)")
                        return True
                    inutiles.add(reconocido.get("etiqueta", "").strip().lower())
                    continue

            if objetivo:
                self._esperar_boton_listo()
                self.nav.click_robusto(objetivo)
                pulsados += 1
                self._avisar("paso", f"Pulsé «{objetivo}»")
            elif self._elegir_opcion():
                pulsados += 1
            elif self._buscar_avance_por_palabras():
                pulsados += 1
            elif self._hay_premio() and self._desbloquear_premio():
                pulsados += 1
            elif self._avanzar_por_sinonimos():
                pulsados += 1
            elif self._desbloquear_premio():
                pulsados += 1
            else:
                etiquetas = [
                    c.get("etiqueta", "")[:18]
                    for c in self.nav.inventario()
                    if c.get("etiqueta")
                ][:8]
                self._avisar(
                    "aviso",
                    f"Nada que pulsar. En pantalla veo: {etiquetas or 'nada con texto'}",
                )
                time.sleep(1.0)
                continue

            # Comprobar el efecto del clic antes de volver a pulsar
            cambio = False
            for _ in range(8):
                time.sleep(0.3)
                if self._huella_pantalla() != antes:
                    cambio = True
                    break
            if cambio:
                self._avisar("paso", f"Pantalla nueva tras {pulsados} clic(s)")
                return True

            # No sirvió. Si había una cuenta atrás, no es culpa del botón:
            # se espera y se le da otra oportunidad.
            if objetivo:
                if self._esperar_boton_listo(timeout_s=6.0):
                    self._avisar("paso", f"Reintento «{objetivo}» ya desbloqueado")
                    continue
                inutiles.add(objetivo.strip().lower())
                self._avisar("paso", f"«{objetivo}» no hizo nada; pruebo otro")

        if pulsados and self._huella_pantalla() != antes:
            return True
        self._avisar(
            "aviso",
            f"Pulsé {pulsados} botón(es) y la pantalla no cambió"
            if pulsados
            else "No encontré ningún botón para avanzar",
        )
        return False

    def _registrar(self, guion: Guion, wav: Path) -> None:
        linea = (
            f"{datetime.now().isoformat(timespec='seconds')}\t{guion.emocion}\t"
            f"{wav.name}\t{guion.texto_a_leer}\n"
        )
        (self.logs / "sesion.tsv").open("a", encoding="utf-8").write(linea)
