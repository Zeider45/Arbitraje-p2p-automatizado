from zoneinfo import ZoneInfo

from arbitraje.config import Costos
from arbitraje.ganancias import emparejar, resumen, serie_diaria
from arbitraje.utilidades import DIA_MS

CARACAS = ZoneInfo("America/Caracas")
T0 = 1_760_000_000_000  # algún instante fijo


def orden(numero, tipo, cantidad, precio, ts, estado="COMPLETED", comision=0.0, ruta="BDV"):
    return {
        "numero": numero, "origen": "demo", "tipo": tipo, "activo": "USDT", "fiat": "VES",
        "cantidad": cantidad, "precio": precio, "total_bs": round(cantidad * precio, 2),
        "comision": comision, "costo_banco_bs": None, "ruta_pago": ruta if tipo == "COMPRA" else None,
        "metodo_pago": None, "estado": estado, "rol": "MAKER", "contraparte": "x",
        "creada_ms": ts, "nota": None,
    }


def test_emparejar_un_ciclo_simple():
    # compro 100 USDT a 1000, vendo 100 a 1010 -> ganancia 1000 Bs
    ordenes = [
        orden("c1", "COMPRA", 100, 1000, T0),
        orden("v1", "VENTA", 100, 1010, T0 + 3600_000),
    ]
    ciclos, posicion = emparejar(ordenes, Costos(costo_mismo_banco_pct=0), "BDV")
    assert len(ciclos) == 1
    c = ciclos[0]
    assert c.usdt == 100
    assert round(c.ganancia_bs, 2) == 1000.0
    assert abs(posicion.usdt) < 1e-9  # todo emparejado


def test_emparejar_fifo_parcial_y_posicion_abierta():
    ordenes = [
        orden("c1", "COMPRA", 100, 1000, T0),
        orden("c2", "COMPRA", 50, 1002, T0 + 100),
        orden("v1", "VENTA", 120, 1010, T0 + 200),  # consume c1 (100) y 20 de c2
    ]
    ciclos, posicion = emparejar(ordenes, Costos(costo_mismo_banco_pct=0), "BDV")
    assert sum(c.usdt for c in ciclos) == 120
    assert round(posicion.usdt, 2) == 30.0        # quedan 30 USDT comprados sin vender
    assert round(posicion.precio_promedio, 2) == 1002.0


def test_comision_reduce_ganancia():
    base = [orden("c1", "COMPRA", 100, 1000, T0), orden("v1", "VENTA", 100, 1010, T0 + 10)]
    con = [orden("c1", "COMPRA", 100, 1000, T0, comision=0.1),
           orden("v1", "VENTA", 100, 1010, T0 + 10, comision=0.1)]
    g_base = emparejar(base, Costos(costo_mismo_banco_pct=0), "BDV")[0][0].ganancia_bs
    g_con = emparejar(con, Costos(costo_mismo_banco_pct=0), "BDV")[0][0].ganancia_bs
    assert g_con < g_base


def test_costo_interbancario_reduce_ganancia():
    ordenes = [orden("c1", "COMPRA", 100, 1000, T0, ruta="INTERBANCARIO"),
               orden("v1", "VENTA", 100, 1010, T0 + 10)]
    ciclos, _ = emparejar(ordenes, Costos(costo_interbancario_pct=0.3, costo_interbancario_min_bs=14), "INTERBANCARIO")
    # 0.3% de 100000 Bs = 300 Bs de costo, baja la ganancia de 1000 a ~700
    assert round(ciclos[0].ganancia_bs, 2) == 700.0


def test_canceladas_no_cuentan():
    ordenes = [
        orden("c1", "COMPRA", 100, 1000, T0),
        orden("v1", "VENTA", 100, 1010, T0 + 10, estado="CANCELLED"),
    ]
    ciclos, posicion = emparejar(ordenes, Costos(), "BDV")
    assert ciclos == []
    assert round(posicion.usdt, 2) == 100.0  # la compra queda abierta


def test_resumen_y_serie_diaria():
    ordenes = [
        orden("c1", "COMPRA", 100, 1000, T0),
        orden("v1", "VENTA", 100, 1010, T0 + 3600_000),
        orden("c2", "COMPRA", 50, 1000, T0 + DIA_MS),
        orden("v2", "VENTA", 50, 1008, T0 + DIA_MS + 3600_000),
    ]
    costos = Costos(costo_mismo_banco_pct=0)
    ciclos, posicion = emparejar(ordenes, costos, "BDV")
    hasta = T0 + 2 * DIA_MS
    r = resumen(ordenes, ciclos, posicion, capital_usdt=1000, tz=CARACAS, desde_ms=None, hasta_ms=hasta,
                ahora_ms=hasta, ruta_por_defecto="BDV", limites_bs={"BDV": 0, "INTERBANCARIO": 2_500_000})
    assert r["ciclos"] == 2
    assert r["ordenes"]["completadas"] == 4
    assert r["ganancia_bs"] > 0
    assert r["roi_pct"] > 0
    serie = serie_diaria(ordenes, ciclos, CARACAS, None, hasta)
    assert len(serie) >= 2
    # la acumulada del último día es la suma total
    assert round(serie[-1]["acumulada_usdt"], 4) == round(sum(c.ganancia_usdt for c in ciclos), 4)
