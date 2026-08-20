"""Control de grabación en OBS (opcional): deja evidencia en vídeo de cada sesión."""
from __future__ import annotations

import logging

from .config import Obs

log = logging.getLogger("vozbot.obs")


class GrabadorObs:
    def __init__(self, cfg: Obs):
        self.cfg = cfg
        self._cli = None

    def conectar(self) -> bool:
        if not self.cfg.activo:
            return False
        try:
            import obsws_python as obs

            self._cli = obs.ReqClient(
                host=self.cfg.host, port=self.cfg.puerto, password=self.cfg.password, timeout=5
            )
            log.info("OBS conectado")
            return True
        except Exception as e:
            log.warning("OBS no disponible: %s", e)
            self._cli = None
            return False

    def iniciar(self) -> None:
        self._seguro(lambda c: c.start_record(), "iniciar grabación")

    def detener(self) -> None:
        self._seguro(lambda c: c.stop_record(), "detener grabación")

    def _seguro(self, fn, que: str) -> None:
        if not self._cli:
            return
        try:
            fn(self._cli)
        except Exception as e:
            log.debug("OBS: no se pudo %s (%s)", que, e)
