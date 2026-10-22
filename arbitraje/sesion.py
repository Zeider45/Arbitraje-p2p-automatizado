"""Sesiones de operación: empiezas con un capital (en Bs o USDT), el sistema te
recomienda el próximo movimiento para sacar rendimiento y, al cerrar, ves cuánto ganaste.

El rendimiento se mide contra tu capital inicial. Todo se calcula en USDT por dentro y
se muestra también en bolívares al precio del momento. Es una estimación: asume que
todas las operaciones de la sesión salieron de ese capital.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

from . import ganancias
from .config import Costos
from .mercado import calcular_spreads
from .utilidades import ahora_ms, fecha_local, formato_numero

# si más de este % de tu capital sigue en bolívares, conviene convertirlo a USDT antes de cerrar
EXPOSICION_MAX_PCT = 15.0

EPS = 1e-9
MIN_POSICION_USDT = 1.0   # menos de esto es polvo: se considera que estás en bolívares
COMPRAR = "COMPRAR"
VENDER = "VENDER"
ESPERAR = "ESPERAR"


def precio_referencia(cotizacion: dict | None, moneda: str) -> float | None:
    """Precio Bs/USDT para convertir el capital inicial a USDT."""
    if not cotizacion:
        return None
    # si empiezas con Bs, comprarás USDT (usa el precio de compra); si empiezas con USDT, el de venta
    return cotizacion.get("precio_compra") if moneda == "VES" else cotizacion.get("precio_venta")


def capital_en_usdt(capital: float, moneda: str, precio: float | None) -> float | None:
    if moneda == "USDT":
        return capital
    if not precio:
        return None
    return capital / precio


def recomendar(moneda_inicial: str, posicion, cotizacion: dict | None, costos: Costos, ruta: str,
               margen_minimo_pct: float, tick: float) -> dict:
    """Qué conviene hacer ahora según lo que tengas en mano (Bs o USDT)."""
    pc = cotizacion.get("precio_compra") if cotizacion else None  # comprar USDT
    pv = cotizacion.get("precio_venta") if cotizacion else None   # vender USDT
    if not pc or not pv:
        return {"accion": ESPERAR, "precio": None, "margen_pct": None,
                "razon": "Esperando precios del mercado."}

    tiene_usdt = posicion.usdt > MIN_POSICION_USDT
    margen = calcular_spreads(pc, pv, costos, ruta)["neto_anunciante_pct"]

    if tiene_usdt:
        objetivo = round(pc - tick, 2)
        costo = posicion.precio_promedio
        if costo and pv <= costo:
            return {"accion": ESPERAR, "precio": objetivo, "margen_pct": margen,
                    "razon": f"Tienes {posicion.usdt:.2f} USDT a un costo de {costo:.2f}. "
                             f"El mejor precio de venta ({pv:.2f}) no supera tu costo: espera a que suba."}
        ganancia = (pv / costo - 1) * 100 if costo else None
        razon = f"Tienes {posicion.usdt:.2f} USDT" + (f" comprados a {costo:.2f}" if costo else "") + ". "
        razon += f"Véndelos: publica tu anuncio de venta a {objetivo:.2f} Bs"
        razon += f" (ganas ~{ganancia:.2f}% sobre tu costo)." if ganancia is not None else "."
        return {"accion": VENDER, "precio": objetivo, "margen_pct": margen, "razon": razon}

    # tienes bolívares: conviene comprar solo si la vuelta deja margen
    objetivo = round(pv + tick, 2)
    if margen is None or margen < margen_minimo_pct:
        return {"accion": ESPERAR, "precio": objetivo, "margen_pct": margen,
                "razon": f"El margen de reventa ahora es {margen:.2f}% (tu mínimo es {margen_minimo_pct:g}%). "
                         "Espera a que se abra la brecha antes de comprar."}
    return {"accion": COMPRAR, "precio": objetivo, "margen_pct": margen,
            "razon": f"Compra USDT: publica tu anuncio de compra a {objetivo:.2f} Bs. "
                     f"Podrás revender con ~{margen:.2f}% de margen."}


def resumen(sesion: dict, ordenes: list[dict], costos: Costos, ruta: str, cotizacion: dict | None,
            tz: ZoneInfo, margen_minimo_pct: float, tick: float, ahora: int | None = None) -> dict:
    """Estado de la sesión: capital, equity actual, rendimiento y recomendación."""
    ahora = ahora if ahora is not None else ahora_ms()
    inicio = sesion["inicio_ms"]
    hasta = sesion["cierre_ms"] or ahora
    moneda = sesion["moneda_inicial"]

    # solo las órdenes de la sesión (desde que abriste)
    propias = [o for o in ordenes if inicio <= o["creada_ms"] <= hasta]
    ciclos, posicion = ganancias.emparejar(propias, costos, ruta)
    realizado = sum(c.ganancia_usdt for c in ciclos)
    valor_pos = ganancias.valorar_posicion(posicion, cotizacion)
    no_realizado = valor_pos["no_realizado_usdt"] or 0.0

    precio_ini = sesion.get("precio_inicial") or precio_referencia(cotizacion, moneda)
    capital_usdt = capital_en_usdt(sesion["capital_inicial"], moneda, precio_ini)
    equity_usdt = (capital_usdt + realizado + no_realizado) if capital_usdt is not None else None

    precio_actual = (cotizacion or {}).get("precio_venta") or (cotizacion or {}).get("precio_compra")
    dias = (fecha_local(hasta, tz) - fecha_local(inicio, tz)).days + 1

    def pct(x):
        return (x / capital_usdt * 100) if capital_usdt else None

    datos = {
        "id": sesion["id"],
        "estado": sesion["estado"],
        "moneda_inicial": moneda,
        "capital_inicial": sesion["capital_inicial"],
        "precio_inicial": precio_ini,
        "inicio_ms": inicio,
        "cierre_ms": sesion["cierre_ms"],
        "dias": dias,
        "capital_inicial_usdt": capital_usdt,
        "realizado_usdt": realizado,
        "no_realizado_usdt": no_realizado,
        "equity_usdt": equity_usdt,
        "rendimiento_realizado_pct": pct(realizado),
        "rendimiento_total_pct": pct(realizado + no_realizado),
        "ciclos": len(ciclos),
        "ordenes": sum(1 for o in propias if o["estado"] == ganancias.COMPLETADA),
        "posicion": valor_pos,
        "nota": sesion.get("nota"),
    }
    # equivalencias en la moneda inicial para mostrar
    if equity_usdt is not None and precio_actual:
        datos["equity_ves"] = equity_usdt * precio_actual
        datos["ganancia_usdt"] = realizado + no_realizado
        datos["ganancia_ves"] = (realizado + no_realizado) * precio_actual

    # composición actual: cuánto tienes en USDT (resguardado) vs en Bs (expuesto a la devaluación)
    if equity_usdt is not None and equity_usdt > EPS:
        usdt_en_mano = max(posicion.usdt, 0.0)
        ves_en_mano_usdt = max(equity_usdt - usdt_en_mano, 0.0)
        expuesto_pct = ves_en_mano_usdt / equity_usdt * 100
        datos["usdt_en_mano"] = usdt_en_mano
        datos["ves_en_mano_usdt"] = ves_en_mano_usdt
        datos["expuesto_ves_pct"] = expuesto_pct
        datos["resguardado"] = expuesto_pct <= EXPOSICION_MAX_PCT
        if expuesto_pct <= EXPOSICION_MAX_PCT:
            datos["consejo_cierre"] = "Estás mayormente en USDT: puedes cerrar el día resguardado de la devaluación."
        else:
            bs = f" (≈ Bs {formato_numero(ves_en_mano_usdt * precio_actual, 0)})" if precio_actual else ""
            datos["consejo_cierre"] = (
                f"Te quedan ≈ {formato_numero(ves_en_mano_usdt)} USDT en bolívares{bs}. El bolívar se devalúa: "
                "conviene cerrar en USDT. Compra USDT con ese saldo antes de cerrar."
            )

    if sesion["estado"] == "cerrada":
        datos["capital_final"] = sesion.get("capital_final")
        datos["nota_cierre"] = sesion.get("nota_cierre")
    else:
        datos["recomendacion"] = recomendar(moneda, posicion, cotizacion, costos, ruta, margen_minimo_pct, tick)
    return datos


def valor_cierre(resumen_sesion: dict) -> float:
    """Capital final a guardar al cerrar, en la moneda inicial de la sesión."""
    if resumen_sesion["moneda_inicial"] == "VES":
        return resumen_sesion.get("equity_ves") or resumen_sesion["capital_inicial"]
    return resumen_sesion.get("equity_usdt") or resumen_sesion["capital_inicial"]
