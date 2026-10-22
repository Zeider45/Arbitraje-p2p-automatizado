"""Pagos del banco (BDV): lectura de SMS o notificaciones y conciliación con órdenes.

El bot NUNCA mueve dinero. Solo lee los avisos de pago que tu teléfono le reenvía,
los registra y te dice a qué orden de Binance corresponden. Antes de liberar USDT
verifica siempre en la BDVApp que el pago llegó y que el titular coincide con el
comprador de Binance (protección contra la estafa de triangulación).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Banco
from .utilidades import MINUTO_MS, formato_numero, parsear_monto

ENTRADA = "ENTRADA"
SALIDA = "SALIDA"

PATRON_ENTRADA = (
    r"recibiste|has recibido|te (?:han |ha )?(?:enviado|transferido|pagado)|abono|abonad[oa]|"
    r"acreditad[oa]|acredit[oó]|ingreso|cr[eé]dito"
)
PATRON_SALIDA = (
    r"enviaste|pagaste|transferiste|realizaste|has (?:enviado|pagado|transferido)|"
    r"ha realizado (?:un|una) (?:pago|transferencia)|pago enviado|d[eé]bito|debitad[oa]|cargo|egreso"
)
PATRON_MONTO = r"(?:\bBs\.?\s*[SD]?\.?|\bVES)\s*:?\s*(\d[\d.,]*\d|\d)"
PATRON_MONTO_ALTERNO = r"\bmonto\s*(?:de\s*)?:?\s*(?:Bs\.?\s*)?(\d[\d.,]*\d|\d)"
PATRON_REFERENCIA = r"\bref(?:erencia)?\.?\s*(?:n(?:ro|[°º])?\.?\s*)?[:#]?\s*(\d{4,})"
PATRON_TELEFONO = r"(?<!\d)(?:\+?58[\s-]?|0)?(4(?:12|14|16|24|26|22))[\s.-]?(\d{3})[\s.-]?(\d{2})[\s.-]?(\d{2})(?!\d)"
PATRON_CEDULA = r"\b([VEJPG])[\s.-]?(\d{1,3}(?:\.?\d{3}){1,2})\b"
ADELANTO_MS = 15 * MINUTO_MS  # tolera relojes desfasados entre el teléfono y Binance


@dataclass
class AvisoPago:
    tipo: str  # ENTRADA (te pagaron) | SALIDA (pagaste)
    monto_bs: float
    referencia: str | None
    contraparte: str | None


def interpretar(texto: str, cfg: Banco | None = None) -> AvisoPago | None:
    """Extrae tipo, monto, referencia y contraparte de un SMS bancario. None si no es un pago."""
    cfg = cfg or Banco()
    tipo = _tipo(texto, cfg.patron_entrada or PATRON_ENTRADA, cfg.patron_salida or PATRON_SALIDA)
    if tipo is None:
        return None
    m_ref = re.search(cfg.patron_referencia or PATRON_REFERENCIA, texto, re.I)
    referencia = m_ref.group(1) if m_ref else None
    resto = f"{texto[:m_ref.start()]} {texto[m_ref.end():]}" if m_ref else texto

    monto = None
    for patron in ([cfg.patron_monto] if cfg.patron_monto else [PATRON_MONTO, PATRON_MONTO_ALTERNO]):
        m = re.search(patron, resto, re.I)
        if m:
            monto = parsear_monto(m.group(1))
            break
    if not monto or monto <= 0:
        return None

    partes = []
    if m := re.search(PATRON_TELEFONO, resto):
        partes.append(f"0{m.group(1)}-{m.group(2)}{m.group(3)}{m.group(4)}")
    if m := re.search(PATRON_CEDULA, resto, re.I):
        partes.append(f"{m.group(1).upper()}{m.group(2).replace('.', '')}")
    return AvisoPago(tipo, monto, referencia, " · ".join(partes) or None)


def _tipo(texto: str, patron_entrada: str, patron_salida: str) -> str | None:
    # Si aparecen ambas palabras clave, manda la que sale primero ("Recibiste... enviado por...")
    e = re.search(patron_entrada, texto, re.I)
    s = re.search(patron_salida, texto, re.I)
    if e and s:
        return ENTRADA if e.start() <= s.start() else SALIDA
    if e:
        return ENTRADA
    return SALIDA if s else None


def conciliar(movimientos: list[dict], ordenes: list[dict], tolerancia_bs: float, ventana_min: float) -> list[tuple[int, str]]:
    """Asocia cada pago sin orden a la orden con el mismo monto más cercana en el tiempo.

    ENTRADA (te pagaron) -> orden de VENTA de USDT; SALIDA (pagaste) -> orden de COMPRA.
    Devuelve [(id_movimiento, numero_orden)].
    """
    usadas = {m["orden_numero"] for m in movimientos if m.get("orden_numero")}
    asignaciones = []
    for m in sorted((m for m in movimientos if not m.get("orden_numero")), key=lambda m: m["ts_ms"]):
        tipo_orden = "VENTA" if m["tipo"] == ENTRADA else "COMPRA"
        candidatas = [
            o for o in ordenes
            if o["tipo"] == tipo_orden
            and o["numero"] not in usadas
            and abs(o["total_bs"] - m["monto_bs"]) <= tolerancia_bs
            and o["creada_ms"] - ADELANTO_MS <= m["ts_ms"] <= o["creada_ms"] + ventana_min * MINUTO_MS
        ]
        if not candidatas:
            continue
        mejor = min(candidatas, key=lambda o: (abs(o["total_bs"] - m["monto_bs"]), abs(m["ts_ms"] - o["creada_ms"])))
        usadas.add(mejor["numero"])
        asignaciones.append((m["id"], mejor["numero"]))
    return asignaciones


ESTADOS_POR_LIBERAR = {"BUYER_PAYED", "TRADING", "DISTRIBUTING"}


def reporte(movimientos: list[dict], ordenes: list[dict]) -> dict:
    """Cuadre banco <-> Binance desde el primer pago registrado."""
    if not movimientos:
        return {"activo": False, "conciliados": 0, "pagos_sin_orden": [], "ordenes_sin_pago": [],
                "por_liberar": [], "alertas": []}
    desde = min(m["ts_ms"] for m in movimientos)
    por_numero = {o["numero"]: o for o in ordenes}
    conciliadas = {m["orden_numero"] for m in movimientos if m.get("orden_numero")}
    alertas = []
    por_liberar = []
    for m in movimientos:
        o = por_numero.get(m.get("orden_numero"))
        if not o:
            continue
        if o["estado"].startswith("CANCEL"):
            alertas.append(
                f"Pago de Bs {formato_numero(m['monto_bs'])} asociado a la orden {o['numero']} que está CANCELADA: "
                "no liberes nada y revisa el caso."
            )
        # venta tuya, ya pagada por el comprador y conciliada: lista para liberar TRAS verificar al titular
        elif m["tipo"] == ENTRADA and o["tipo"] == "VENTA" and o["estado"] in ESTADOS_POR_LIBERAR:
            por_liberar.append({"orden": o, "pago": m})
    return {
        "activo": True,
        "conciliados": len(conciliadas),
        "pagos_sin_orden": [m for m in movimientos if not m.get("orden_numero")],
        "ordenes_sin_pago": [
            o for o in ordenes
            if o["estado"] == "COMPLETED" and o["creada_ms"] >= desde - ADELANTO_MS and o["numero"] not in conciliadas
        ],
        "por_liberar": por_liberar,
        "alertas": alertas,
    }
