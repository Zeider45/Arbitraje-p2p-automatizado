from zoneinfo import ZoneInfo

from arbitraje import sesion
from arbitraje.config import Costos
from arbitraje.ganancias import Posicion
from arbitraje.sesion import COMPRAR, ESPERAR, VENDER, capital_en_usdt, recomendar

CARACAS = ZoneInfo("America/Caracas")
COT = {"precio_compra": 1013.0, "precio_venta": 1000.0}


def test_capital_en_usdt():
    assert capital_en_usdt(1000, "USDT", None) == 1000
    assert capital_en_usdt(1_000_000, "VES", 1000.0) == 1000.0
    assert capital_en_usdt(1_000_000, "VES", None) is None  # sin precio no se puede convertir


def test_recomendar_con_usdt_vender():
    pos = Posicion(usdt=50.0, precio_promedio=990.0, lotes=1)
    r = recomendar("VES", pos, COT, Costos(costo_mismo_banco_pct=0), "BDV", 0.8, 0.01)
    assert r["accion"] == VENDER
    assert r["precio"] < 1013.0


def test_recomendar_con_usdt_espera_si_no_supera_costo():
    pos = Posicion(usdt=50.0, precio_promedio=1050.0, lotes=1)  # costo por encima del mercado
    r = recomendar("VES", pos, COT, Costos(), "BDV", 0.8, 0.01)
    assert r["accion"] == ESPERAR


def test_recomendar_con_bolivares_compra_si_hay_margen():
    pos = Posicion(usdt=0.0, precio_promedio=None, lotes=0)
    r = recomendar("VES", pos, COT, Costos(costo_mismo_banco_pct=0), "BDV", 0.5, 0.01)
    assert r["accion"] == COMPRAR
    assert r["precio"] > 1000.0


def test_recomendar_con_bolivares_espera_si_margen_bajo():
    pos = Posicion(usdt=0.0, precio_promedio=None, lotes=0)
    cot = {"precio_compra": 1001.0, "precio_venta": 1000.0}  # brecha mínima
    r = recomendar("VES", pos, cot, Costos(), "BDV", 1.0, 0.01)
    assert r["accion"] == ESPERAR


def test_recomendar_sin_mercado():
    pos = Posicion(usdt=0.0, precio_promedio=None, lotes=0)
    r = recomendar("VES", pos, None, Costos(), "BDV", 0.8, 0.01)
    assert r["accion"] == ESPERAR


def _orden(numero, tipo, cantidad, precio, ts, estado="COMPLETED"):
    return {"numero": numero, "origen": "demo", "tipo": tipo, "cantidad": cantidad, "precio": precio,
            "total_bs": round(cantidad * precio, 2), "comision": 0.0, "costo_banco_bs": None,
            "ruta_pago": "BDV" if tipo == "COMPRA" else None, "estado": estado, "creada_ms": ts}


def test_resumen_calcula_rendimiento_y_exposicion():
    s = {"id": 1, "inicio_ms": 1000, "capital_inicial": 1_000_000.0, "moneda_inicial": "VES",
         "precio_inicial": 1000.0, "estado": "abierta", "cierre_ms": None, "nota": None,
         "capital_final": None, "nota_cierre": None}
    ordenes = [_orden("c1", "COMPRA", 100, 1000, 2000), _orden("v1", "VENTA", 100, 1010, 3000)]
    r = sesion.resumen(s, ordenes, Costos(costo_mismo_banco_pct=0), "BDV", COT, CARACAS, 0.8, 0.01, ahora=4000)
    assert r["capital_inicial_usdt"] == 1000.0
    assert r["realizado_usdt"] > 0
    assert r["rendimiento_total_pct"] is not None
    assert "recomendacion" in r
    # sin posición abierta -> el capital quedó en Bs -> expuesto y aconseja pasar a USDT
    assert r["expuesto_ves_pct"] > 50
    assert "USDT" in r["consejo_cierre"]
