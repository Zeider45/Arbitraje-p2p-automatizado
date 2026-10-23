import asyncio

import pytest
from fastapi.testclient import TestClient

from arbitraje.config import Costos, cargar
from arbitraje.db import BaseDatos
from arbitraje.montos import CotizacionMonto, analizar, tramos_para
from arbitraje.servidor import crear_app
from arbitraje.tareas import Motor

COSTOS = Costos()


def test_tramos_no_superan_el_capital():
    t = tramos_para(300_000)
    assert t[-1] == 300_000 and all(x <= 300_000 for x in t) and t == sorted(t)


def test_rinde_mas_el_monto_con_mejor_brecha_y_marca_el_mejor():
    cots = [CotizacionMonto(50_000, 1010, 990, 10, 10), CotizacionMonto(1_000_000, 1004, 1000, 10, 10)]
    r = analizar(cots, COSTOS, "BDV", 0.01)
    assert r["mejor_monto_bs"] == 50_000
    assert [t["mejor"] for t in r["tramos"]] == [True, False]
    assert "50.000" in r["recomendacion"] and "Sí se nota" in r["recomendacion"]


def test_si_los_montos_grandes_rinden_mas_lo_dice():
    cots = [CotizacionMonto(50_000, 1004, 1000, 10, 10), CotizacionMonto(1_000_000, 1010, 990, 10, 10)]
    r = analizar(cots, COSTOS, "BDV", 0.01)
    assert r["mejor_monto_bs"] == 1_000_000 and "montos grandes rinden más" in r["recomendacion"]


def test_el_minimo_interbancario_castiga_los_montos_chicos():
    mismo = [CotizacionMonto(5_000, 1010, 990, 10, 10), CotizacionMonto(500_000, 1010, 990, 10, 10)]
    r = analizar(mismo, COSTOS, "INTERBANCARIO", 0.01)
    assert r["tramos"][0]["neto_pct"] < r["tramos"][1]["neto_pct"]


def test_ignora_tramos_con_pocos_anuncios_al_elegir_el_mejor():
    cots = [CotizacionMonto(50_000, 1030, 980, 1, 1), CotizacionMonto(100_000, 1010, 995, 10, 10)]
    r = analizar(cots, COSTOS, "BDV", 0.01)
    assert r["mejor_monto_bs"] == 100_000


def test_sin_datos():
    assert analizar([], COSTOS, "BDV", 0.01)["mejor_monto_bs"] is None


def test_cambiar_monto_ajusta_filtro_y_limites(tmp_path):
    cfg = cargar(None, demo=True)
    m = Motor(cfg, BaseDatos(str(tmp_path / "d.db")))
    texto = asyncio.run(m.cambiar_estrategia({"monto_bs": 200_000}))
    assert cfg.mercado.monto_bs == 200_000 and cfg.anuncios.max_bs == 200_000 and cfg.anuncios.min_bs == 100_000
    assert "200.000" in texto and m.db.eventos(3, "decision")
    m2 = Motor(cargar(None, demo=True), BaseDatos(str(tmp_path / "d.db")))
    assert m2.cfg.mercado.monto_bs == 200_000   # recuerda el monto al reiniciar
    asyncio.run(m.cambiar_estrategia({"monto_bs": 0}))
    assert cfg.mercado.monto_bs == 0


def test_panel_y_api_de_montos(tmp_path):
    cfg = cargar(None, demo=True)
    cfg.general.base_datos = str(tmp_path / "d.db")
    with TestClient(crear_app(cfg, iniciar_tareas=False)) as c:
        d = c.get("/api/panel").json()["montos"]
        assert len(d["tramos"]) >= 3 and d["recomendacion"] and d["mejor_monto_bs"]
        r = c.post("/api/estrategia", json={"monto_auto": True}).json()
        assert r["estrategia"]["monto_auto"] is True and r["montos"]["auto"] is True
        r = c.post("/api/estrategia", json={"monto_bs": d["mejor_monto_bs"]}).json()
        assert r["montos"]["monto_actual_bs"] == d["mejor_monto_bs"] and "Estrategia" in r["mensaje"]


def test_con_margen_minimo_elige_el_que_mas_bs_deja_entre_los_que_cumplen():
    cots = [CotizacionMonto(20_000, 1010, 990, 10, 10), CotizacionMonto(250_000, 1009, 991, 10, 10),
            CotizacionMonto(1_000_000, 1000, 999, 10, 10)]
    r = analizar(cots, COSTOS, "BDV", 0.01, margen_minimo_pct=0.8)
    assert r["cumple_minimo"] and r["mejor_monto_bs"] == 250_000   # cumple el mínimo y deja más Bs que el de 20.000


def test_si_ninguno_cumple_el_minimo_lo_advierte():
    cots = [CotizacionMonto(20_000, 1003, 1000, 10, 10), CotizacionMonto(500_000, 1004, 1000, 10, 10)]
    r = analizar(cots, COSTOS, "BDV", 0.01, margen_minimo_pct=0.8)
    assert not r["cumple_minimo"] and "ningún monto alcanza" in r["recomendacion"]
