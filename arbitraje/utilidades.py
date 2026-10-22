"""Utilidades compartidas: montos en formato venezolano, tiempo y períodos."""
from __future__ import annotations

import re
import time
from datetime import date, datetime, time as hora_del_dia, timedelta
from zoneinfo import ZoneInfo

MINUTO_MS = 60_000
HORA_MS = 60 * MINUTO_MS
DIA_MS = 24 * HORA_MS

# Períodos del panel -> cantidad de días calendario (None = todo el historial)
PERIODOS = {"hoy": 1, "7d": 7, "30d": 30, "90d": 90, "todo": None}


def ahora_ms() -> int:
    return int(time.time() * 1000)


def parsear_monto(texto: str | None) -> float | None:
    """Convierte un monto escrito por un banco o una persona en número.

    Acepta el formato venezolano (1.234.567,89), el anglosajón (1,234,567.89)
    y números simples (1234.5). Devuelve None si no hay dígitos.
    """
    if not texto:
        return None
    t = re.sub(r"[^\d,.\-]", "", texto)
    negativo = t.startswith("-")
    t = t.replace("-", "").strip(".,")
    if not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        # el separador que aparece de último es el decimal
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = _un_solo_separador(t, ",")
    elif "." in t:
        t = _un_solo_separador(t, ".")
    try:
        valor = float(t)
    except ValueError:
        return None
    return -valor if negativo else valor


def _un_solo_separador(t: str, sep: str) -> str:
    partes = t.split(sep)
    # "1.234.567" o "1.500" -> separador de miles; "1,5" o "0.500" -> decimal
    if len(partes) > 2 or (len(partes[-1]) == 3 and partes[0] not in ("", "0")):
        return "".join(partes)
    return t.replace(sep, ".")


def fecha_local(ts_ms: int, tz: ZoneInfo) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, tz).date()


def inicio_dia_ms(dia: date, tz: ZoneInfo) -> int:
    return int(datetime.combine(dia, hora_del_dia.min, tzinfo=tz).timestamp() * 1000)


def rango_periodo(periodo: str, tz: ZoneInfo, ahora: int | None = None) -> tuple[int | None, int]:
    """(desde_ms, hasta_ms) para 'hoy', '7d', '30d', '90d' o 'todo' (desde = None)."""
    if periodo not in PERIODOS:
        raise ValueError(f"Período desconocido: {periodo}")
    hasta = ahora if ahora is not None else ahora_ms()
    dias = PERIODOS[periodo]
    if dias is None:
        return None, hasta
    primer_dia = fecha_local(hasta, tz) - timedelta(days=dias - 1)
    return inicio_dia_ms(primer_dia, tz), hasta


def formato_numero(valor: float | None, decimales: int = 2) -> str:
    """1234567.891 -> '1.234.567,89' (formato venezolano)."""
    if valor is None:
        return "—"
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")
