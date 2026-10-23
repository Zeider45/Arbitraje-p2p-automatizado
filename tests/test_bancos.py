import pytest
from fastapi.testclient import TestClient

from arbitraje.bancos import CotizacionBanco, analizar, ruta_con
from arbitraje.config import Costos, cargar
from arbitraje.servidor import crear_app

COSTOS = Costos()


def q(banco, compra, venta, n=10):
    return CotizacionBanco(banco, compra, venta, n, n, 1000, 1000)


def test_ruta_mismo_banco_es_gratis_y_otro_banco_no():
    assert ruta_con("Banesco", "Banesco") == "BDV"
    assert ruta_con("Mercantil", "Banesco") == "INTERBANCARIO"
    assert ruta_con("PagoMovil", "PagoMovil") == "INTERBANCARIO"


def test_tu_banco_gana_si_los_precios_son_iguales():
    cots = [q("Banesco", 1010, 1000), q("Mercantil", 1010, 1000)]
    r = analizar(cots, COSTOS, "Banesco", 0.01)
    assert r["bancos"][0]["banco"] == "Banesco" and r["bancos"][0]["es_tu_banco"]
    assert r["bancos"][0]["neto_pct"] > r["bancos"][1]["neto_pct"]  # el otro paga interbancario


def test_otro_banco_con_mejor_precio_puede_superar_al_tuyo():
    cots = [q("Banesco", 1004, 1000), q("Mercantil", 1030, 1000)]
    r = analizar(cots, COSTOS, "Banesco", 0.01)
    assert r["bancos"][0]["banco"] == "Mercantil"
    assert "Mercantil" in r["recomendacion"]


def test_combinaciones_comprar_con_uno_vender_con_otro():
    cots = [q("Banesco", 1004, 1000), q("Mercantil", 1030, 1001)]
    r = analizar(cots, COSTOS, "Banesco", 0.01)
    mejor = r["combinaciones"][0]
    assert mejor["venta_banco"] == "Mercantil"  # el mejor precio de venta está en Mercantil
    assert len(r["combinaciones"]) <= 5


def test_pocos_anuncios_se_marca():
    r = analizar([q("Banesco", 1010, 1000, n=1)], COSTOS, "Banesco", 0.01)
    assert r["bancos"][0]["poca_liquidez"] is True


def test_sin_datos():
    assert analizar([], COSTOS, "Banesco", 0.01)["bancos"] == []


def test_panel_y_cambio_de_banco(tmp_path):
    cfg = cargar(None, demo=True)
    cfg.general.base_datos = str(tmp_path / "d.db")
    with TestClient(crear_app(cfg, iniciar_tareas=False)) as c:
        b = c.get("/api/panel").json()["bancos"]
        assert b["mi_banco"] == "BancoDeVenezuela" and len(b["bancos"]) > 3 and b["recomendacion"]
        r = c.post("/api/bancos/mi-banco", json={"banco": "Banesco"}).json()
        assert r["mi_banco"] == "Banesco"
        assert c.get("/api/panel").json()["bancos"]["mi_banco"] == "Banesco"
        assert c.post("/api/bancos/mi-banco", json={"banco": "Inventado"}).status_code == 422


def test_cambiar_banco_cambia_la_estrategia(tmp_path):
    import asyncio

    from arbitraje.db import BaseDatos
    from arbitraje.tareas import Motor

    cfg = cargar(None, demo=True)
    cfg.general.base_datos = str(tmp_path / "d.db")
    m = Motor(cfg, BaseDatos(cfg.general.base_datos))
    texto = asyncio.run(m.cambiar_banco("Banesco"))
    assert "Banesco" in texto
    assert cfg.mercado.metodos_pago == ["Banesco"] and cfg.anuncios.metodos_compra == ["Banesco"]
    assert cfg.anuncios.ruta == "BDV" and cfg.pagos.banco_por_defecto == "0134"
    assert any("Banesco" in e["mensaje"] for e in m.db.eventos(5, "decision"))
    # al reiniciar el motor, recuerda el banco elegido
    m2 = Motor(cargar(None, demo=True), BaseDatos(cfg.general.base_datos))
    assert m2.cfg.mercado.metodos_pago == ["Banesco"]
    asyncio.run(m.cambiar_banco("PagoMovil"))
    assert cfg.anuncios.ruta == "INTERBANCARIO"


def test_ruta_mismo_banco_segun_mi_banco():
    from arbitraje.binance_api import ruta_desde_metodo

    assert ruta_desde_metodo("Banesco", "Banesco") == "BDV"
    assert ruta_desde_metodo("BancoDeVenezuela", "Banesco") == "INTERBANCARIO"
    assert ruta_desde_metodo("BancoDeVenezuela") == "BDV"
