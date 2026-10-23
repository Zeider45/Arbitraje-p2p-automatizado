from fastapi.testclient import TestClient

from arbitraje.bancos import CotizacionBanco, analizar, ruta_modalidad
from arbitraje.config import Costos, cargar
from arbitraje.servidor import crear_app

COSTOS = Costos()
TICK = 0.01


def q(banco, compra, venta, n=10):
    return CotizacionBanco(banco, compra, venta, n, n, 1000, 1000)


def test_mismo_banco_es_gratis_y_distintos_pagan_interbancario():
    assert ruta_modalidad("Banesco", "Banesco") == "BDV"
    assert ruta_modalidad("BancoDelTesoro", "BancoPlaza") == "INTERBANCARIO"
    assert ruta_modalidad("PagoMovil", "PagoMovil") == "INTERBANCARIO"


def test_con_precios_iguales_gana_el_mismo_banco_porque_no_paga_traslado():
    cots = [q("Banesco", 1010, 1000), q("Mercantil", 1010, 1000)]
    r = analizar(cots, COSTOS, TICK, "Banesco", "Banesco")
    mejor = r["combinaciones"][0]
    assert mejor["compra_banco"] == mejor["venta_banco"]          # sin costo interbancario
    cruzada = next(c for c in r["combinaciones"] if c["compra_banco"] != c["venta_banco"])
    assert mejor["neto_pct"] > cruzada["neto_pct"]


def test_comprar_con_un_banco_y_vender_con_otro_puede_ganarle_al_mismo_banco():
    # Tesoro: los compradores pagan poco (barato para comprar ahí). Plaza: los vendedores cobran mucho (caro para vender ahí).
    cots = [q("BancoDelTesoro", 1001, 980), q("BancoPlaza", 1020, 1010)]
    r = analizar(cots, COSTOS, TICK, "BancoDelTesoro", "BancoDelTesoro")
    mejor = r["combinaciones"][0]
    assert (mejor["compra_banco"], mejor["venta_banco"]) == ("BancoDelTesoro", "BancoPlaza")
    assert mejor["ruta"] == "INTERBANCARIO"                      # incluye el traslado entre tus dos cuentas
    assert mejor["neto_pct"] > r["actual"]["neto_pct"]
    assert "mejoraría" in r["recomendacion"]


def test_la_modalidad_elegida_se_marca_y_se_compara_con_la_mejor():
    cots = [q("BancoDelTesoro", 1004, 990), q("BancoPlaza", 1030, 1001), q("Banesco", 1010, 995)]
    r = analizar(cots, COSTOS, TICK, "Banesco", "Banesco")
    assert r["actual"]["compra_banco"] == "Banesco" and r["actual"]["venta_banco"] == "Banesco"
    assert "La tuya (compras con Banesco, vendes con Banesco)" in r["recomendacion"] or "Es la que tienes elegida" in r["recomendacion"]
    assert sum(1 for c in r["combinaciones"] if c["en_uso"]) <= 1


def test_con_tesoro_para_comprar_y_plaza_para_vender_se_calcula_el_traslado():
    cots = [q("BancoDelTesoro", 1000, 990), q("BancoPlaza", 1010, 995)]
    r = analizar(cots, COSTOS, TICK, "BancoDelTesoro", "BancoPlaza")
    a = r["actual"]
    assert (a["compra_banco"], a["venta_banco"], a["ruta"]) == ("BancoDelTesoro", "BancoPlaza", "INTERBANCARIO")
    # compro al mejor precio del comprador de Tesoro (+tick) y vendo al del vendedor de Plaza (−tick)
    assert a["precio_compra"] == 990 + TICK and a["precio_venta"] == 1010 - TICK


def test_un_banco_con_un_solo_anuncio_no_encabeza_el_ranking():
    cots = [q("BFC", 1012, 981, n=1), q("BNCBancoNacional", 1002, 990, n=20), q("Banesco", 1000, 991, n=20)]
    r = analizar(cots, COSTOS, TICK, "Banesco", "Banesco")
    assert r["bancos"][0]["banco"] != "BFC" and r["bancos"][-1]["banco"] == "BFC"
    assert not r["combinaciones"][0]["poca_liquidez"]
    assert "BFC" not in r["recomendacion"]


def test_sin_datos():
    r = analizar([], COSTOS, TICK, "Banesco", "Banesco")
    assert r["bancos"] == [] and r["actual"] is None and "Todavía no hay datos" in r["recomendacion"]


def test_panel_con_estrategia_y_cambio_de_modalidad(tmp_path):
    cfg = cargar(None, demo=True)
    cfg.general.base_datos = str(tmp_path / "d.db")
    with TestClient(crear_app(cfg, iniciar_tareas=False)) as c:
        d = c.get("/api/panel").json()
        assert d["estrategia"]["banco_compra"] == "BancoDeVenezuela" and len(d["bancos"]["bancos"]) > 3
        r = c.post("/api/estrategia", json={"banco_compra": "BancoDelTesoro", "banco_venta": "BancoPlaza"}).json()
        assert r["estrategia"]["banco_compra"] == "BancoDelTesoro" and r["estrategia"]["banco_venta"] == "BancoPlaza"
        assert r["estrategia"]["ruta"] == "INTERBANCARIO" and "compras con Banco del Tesoro y vendes con Banco Plaza" in r["mensaje"]
        assert c.get("/api/panel").json()["bancos"]["actual"]["venta_banco"] == "BancoPlaza"
        assert c.post("/api/estrategia", json={"banco_compra": "Inventado"}).status_code == 422
