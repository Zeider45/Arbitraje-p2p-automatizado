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

    async def enviar(self, titulo: str, cuerpo: str = "") -> bool:
        """Envía un aviso. Nunca lanza excepciones: un aviso fallido no debe frenar al bot."""
        if not self.activo:
            return False
        texto = f"<b>{html.escape(titulo)}</b>"
        if cuerpo:
            texto += "\n" + html.escape(cuerpo)
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
