import pytest
from fastapi.testclient import TestClient

from arbitraje.config import Costos, cargar
from arbitraje.mercado import neto_entre_anuncios
from arbitraje.servidor import crear_app
from arbitraje.simulador import precios_sugeridos, simular

COSTOS = Costos()


def test_una_vuelta_coincide_con_el_neto_del_mercado():
    r = simular(1000, "USDT", 1, 100.0, 103.0, COSTOS, ruta="BDV")
    esperado = neto_entre_anuncios(100.0, 103.0, COSTOS, "BDV")
    assert r["ganancia_por_ciclo_pct"] == pytest.approx(esperado)


def test_interes_compuesto_supera_al_simple():
    compuesto = simular(1000, "USDT", 20, 100.0, 103.0, COSTOS, reinvertir=True)
    simple = simular(1000, "USDT", 20, 100.0, 103.0, COSTOS, reinvertir=False)
    assert compuesto["ganancia"] > simple["ganancia"]
    assert simple["ganancia"] == pytest.approx(20 * simple["filas"][0]["ganancia"])


def test_empezar_en_bolivares_mide_en_bolivares():
    r = simular(100_000, "VES", 5, 100.0, 103.0, COSTOS)
    assert r["moneda"] == "VES" and r["ganancia"] > 0
    assert r["ganancia_usdt"] == pytest.approx(r["ganancia"] / 103.0)


def test_sin_margen_avisa():
    r = simular(1000, "USDT", 3, 100.0, 100.0, COSTOS)
    assert r["ganancia"] < 0 and r["avisos"]


def test_dias_segun_ciclos_por_dia():
    assert simular(1000, "USDT", 10, 100.0, 103.0, COSTOS, ciclos_por_dia=2.5)["dias"] == 4


def test_limite_interbancario_avisa():
    r = simular(1000, "USDT", 2, 100.0, 103.0, COSTOS, ruta="INTERBANCARIO", ciclos_por_dia=1,
                limite_interbancario_bs=1000)
    assert any("límite" in a for a in r["avisos"])


def test_parametros_invalidos():
    with pytest.raises(ValueError):
        simular(0, "USDT", 5, 100.0, 103.0, COSTOS)
    with pytest.raises(ValueError):
        simular(10, "USDT", 0, 100.0, 103.0, COSTOS)


def test_precios_sugeridos_por_rol():
    cot = {"precio_compra": 105.0, "precio_venta": 100.0}
    assert precios_sugeridos(cot, COSTOS, "anunciante") == (100.0 + COSTOS.tick_bs, 105.0 - COSTOS.tick_bs)
    assert precios_sugeridos(cot, COSTOS, "tomador") == (105.0, 100.0)
    assert precios_sugeridos(None, COSTOS, "tomador") == (None, None)


@pytest.fixture
def cliente(tmp_path):
    cfg = cargar(None, demo=True)
    cfg.general.base_datos = str(tmp_path / "demo.db")
    with TestClient(crear_app(cfg, iniciar_tareas=False)) as c:
        yield c


def test_endpoint_simulador(cliente):
    r = cliente.post("/api/simulador", json={"capital": 500, "moneda": "USDT", "ciclos": 5,
                                              "precio_compra": 100, "precio_venta": 104})
    assert r.status_code == 200
    d = r.json()
    assert len(d["filas"]) == 5 and d["precios_de_mercado"] is False and d["ganancia"] > 0
    assert cliente.post("/api/simulador", json={"capital": -1}).status_code == 422


def test_ordenes_de_ciclos_son_compra_y_venta_completadas():
    from arbitraje.simulador import ordenes_de_ciclos

    r = simular(1000, "USDT", 3, 100.0, 103.0, COSTOS)
    ords = ordenes_de_ciclos(r["filas"], "USDT", 100.0, 103.0, COSTOS, "BDV", "anunciante", 1_800_000_000_000, 2)
    assert len(ords) == 6 and {o["tipo"] for o in ords} == {"COMPRA", "VENTA"}
    assert all(o["estado"] == "COMPLETED" and o["origen"] == "demo" for o in ords)
    assert len({o["numero"] for o in ords}) == 6


def test_simulacion_aplicar_y_reiniciar(tmp_path):
    cfg = cargar(None, demo=True)
    cfg.general.base_datos = str(tmp_path / "demo.db")
    with TestClient(crear_app(cfg, iniciar_tareas=False)) as c:
        c.post("/api/simulacion/reiniciar", json={"capital": 500})
        assert c.get("/api/panel").json()["ordenes"] == []
        r = c.post("/api/simulacion/ciclos", json={"capital": 500, "ciclos": 4, "precio_compra": 100, "precio_venta": 104})
        assert r.json()["ordenes_creadas"] == 8
        d = c.get("/api/panel").json()
        assert len(d["ordenes"]) == 8 and d["resumen"]["capital_usdt"] == 500
        c.post("/api/simulacion/reiniciar", json={"capital": 800, "con_ejemplo": True})
        assert len(c.get("/api/panel").json()["ordenes"]) > 8


def test_reiniciar_no_existe_en_modo_real(tmp_path):
    cfg = cargar(None)
    cfg.general.base_datos = str(tmp_path / "real.db")
    cfg.panel.simulacion = False
    with TestClient(crear_app(cfg, iniciar_tareas=False)) as c:
        assert c.post("/api/simulacion/reiniciar", json={"capital": 500}).status_code == 403
        assert c.post("/api/simulacion/ciclos", json={"capital": 500, "precio_compra": 100, "precio_venta": 104}).status_code == 403
