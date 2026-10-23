import asyncio

import pytest
from fastapi.testclient import TestClient

from arbitraje.config import cargar
from arbitraje.db import BaseDatos
from arbitraje.mercado import Cotizacion
from arbitraje.servidor import crear_app
from arbitraje.tareas import Motor
from arbitraje.utilidades import ahora_ms


def cot(compra, venta):
    return Cotizacion(ts_ms=ahora_ms(), precio_compra=compra, precio_venta=venta)


@pytest.fixture
def motor(tmp_path):
    cfg = cargar(None)
    cfg.anuncios.modo = "manual"
    cfg.anuncios.automatico = True
    return Motor(cfg, BaseDatos(str(tmp_path / "t.db")))


def decisiones(m):
    return [e["mensaje"] for e in m.db.eventos(20, "decision")]


def test_manual_calcula_y_avisa_una_vez(motor):
    r = asyncio.run(motor.reposicionar(cot(1012.0, 1000.0)))
    assert r["ok"] and r["modo"] == "manual"
    assert any("Pon tu anuncio de compra" in a["texto"] for a in r["acciones"])
    n = len(decisiones(motor))
    assert n == 2
    # mismo precio de nuevo: no repite el aviso
    asyncio.run(motor.reposicionar(cot(1012.0, 1000.0)))
    assert len(decisiones(motor)) == n


def test_manual_no_llama_a_binance(motor):
    assert motor.agente is None
    asyncio.run(motor.reposicionar(cot(1012.0, 1000.0)))


def test_manual_avisa_si_el_precio_cambia_mucho(motor):
    motor.cfg.anuncios.aviso_cada_min = 0
    asyncio.run(motor.reposicionar(cot(1012.0, 1000.0)))
    asyncio.run(motor.reposicionar(cot(1030.0, 1018.0)))
    assert len(decisiones(motor)) == 4


def test_guia_manual_en_el_panel(tmp_path):
    cfg = cargar(None, demo=True)
    cfg.general.base_datos = str(tmp_path / "d.db")
    cfg.anuncios.modo = "manual"
    with TestClient(crear_app(cfg, iniciar_tareas=False)) as c:
        g = c.get("/api/panel").json()["anuncios"]["guia_manual"]
        assert g["lados"]["compra"]["tipo_binance"] == "Comprar" and g["lados"]["venta"]["tipo_binance"] == "Vender"
        assert g["lados"]["compra"]["precio"] and g["lados"]["compra"]["usdt"] > 0


def test_textos_del_anuncio_usan_banco_y_minutos(tmp_path):
    from arbitraje.terminos import TERMINOS_COMPRA, TERMINOS_VENTA, textos

    cfg = cargar(None)
    cfg.anuncios.pay_time_limit = 20
    t = textos(cfg.anuncios, "Banesco")
    assert "Banesco" in t["compra"] and "20 minutos" in t["venta"] and "{" not in t["respuesta"]
    assert "titular" in t["venta"].lower() and "terceros" in t["compra"].lower() and "cancel" in t["venta"].lower()
    assert len(t["compra"]) < 1000 and len(t["venta"]) < 1000
    cfg.anuncios.terminos_venta = "Solo {banco}, {minutos} min."
    assert textos(cfg.anuncios, "Banesco")["venta"] == "Solo Banesco, 20 min."
    assert "{banco}" in TERMINOS_COMPRA and "{banco}" in TERMINOS_VENTA


def test_guia_incluye_textos(tmp_path):
    cfg = cargar(None, demo=True)
    cfg.general.base_datos = str(tmp_path / "d.db")
    cfg.anuncios.modo = "manual"
    with TestClient(crear_app(cfg, iniciar_tareas=False)) as c:
        t = c.get("/api/panel").json()["anuncios"]["guia_manual"]["textos"]
        assert t["compra"] and t["venta"] and t["respuesta"] and t["condiciones"]
