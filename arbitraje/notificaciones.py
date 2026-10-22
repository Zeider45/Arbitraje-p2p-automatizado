"""Avisos por Telegram y WhatsApp (opcionales)."""
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


class WhatsApp:
    """Avisos a tu propio WhatsApp con CallMeBot (gratis, sin cuenta de empresa).

    Activación única: desde tu WhatsApp escribe "I allow callmebot to send me messages" al
    +34 644 51 95 23; te responde con tu apikey.
    """

    URL = "https://api.callmebot.com/whatsapp.php"

    def __init__(self, telefono: str, apikey: str, http: httpx.AsyncClient):
        self.telefono = telefono
        self.apikey = apikey
        self.http = http

    @property
    def activo(self) -> bool:
        return bool(self.telefono and self.apikey)

    async def enviar(self, titulo: str, cuerpo: str = "", pre: bool = False) -> bool:
        if not self.activo:
            return False
        texto = f"*{titulo}*" + (f"\n{cuerpo}" if cuerpo else "")
        try:
            r = await self.http.get(
                self.URL, params={"phone": self.telefono, "text": texto, "apikey": self.apikey}, timeout=20
            )
        except httpx.HTTPError as e:
            log.warning("No pude enviar el aviso a WhatsApp: %s", str(e).replace(self.apikey, "***"))
            return False
        if r.status_code != 200:
            log.warning("CallMeBot respondió HTTP %s: %s", r.status_code, r.text[:200])
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
