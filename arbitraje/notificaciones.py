"""Avisos por Telegram (opcionales)."""
from __future__ import annotations

import html
import logging

import httpx

log = logging.getLogger(__name__)


class Telegram:
    def __init__(self, token: str, chat_id: str, http: httpx.AsyncClient):
        self.token = token
        self.chat_id = chat_id
        self.http = http

    @property
    def activo(self) -> bool:
        return bool(self.token and self.chat_id)

    async def enviar(self, titulo: str, cuerpo: str = "", pre: bool = False) -> bool:
        """Envía un aviso. Nunca lanza excepciones: un aviso fallido no debe frenar al bot.

        pre=True envuelve el cuerpo en <pre>, que Telegram muestra en monoespaciado y
        deja tocar para copiar (útil para el bloque de "Pegar Datos").
        """
        if not self.activo:
            return False
        texto = f"<b>{html.escape(titulo)}</b>"
        if cuerpo:
            texto += "\n" + (f"<pre>{html.escape(cuerpo)}</pre>" if pre else html.escape(cuerpo))
        try:
            r = await self.http.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": texto, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=15,
            )
        except httpx.HTTPError as e:
            log.warning("No pude enviar el aviso a Telegram: %s", str(e).replace(self.token, "***"))
            return False
        if r.status_code != 200:
            log.warning("Telegram respondió HTTP %s: %s", r.status_code, r.text[:200])
            return False
        return True


class Avisos:
    """Manda cada aviso por todos los canales configurados."""

    def __init__(self, *canales):
        self.canales = canales

    @property
    def activo(self) -> bool:
        return any(c.activo for c in self.canales)

    async def enviar(self, titulo: str, cuerpo: str = "", pre: bool = False) -> bool:
        resultados = [await c.enviar(titulo, cuerpo, pre=pre) for c in self.canales if c.activo]
        return any(resultados)
