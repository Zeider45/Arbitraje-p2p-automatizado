import asyncio

import pytest

from arbitraje import estrategia as mod
from arbitraje.config import cargar
from arbitraje.db import BaseDatos
from arbitraje.mercado import cuerpo_busqueda, metodos_del_lado
from arbitraje.tareas import Motor


@pytest.fixture
def motor(tmp_path):
    cfg = cargar(None, demo=True)
    return Motor(cfg, BaseDatos(str(tmp_path / "d.db")))


def test_estrategia_inicial_sale_del_config():
    cfg = cargar(None)
    cfg.mercado.mi_banco = "Banesco"
    e = mod.inicial(cfg)
    assert e.banco_compra == e.banco_venta == "Banesco" and e.mismo_banco and e.ruta == "BDV"


def test_describir():
    e = mod.Estrategia("BancoDelTesoro", "BancoPlaza", 50000, 0.3)
    assert e.describir() == "compras con Banco del Tesoro y vendes con Banco Plaza · Bs 50.000 por orden · margen mínimo 0.3 %"
    assert mod.Estrategia("Banesco", "Banesco").describir().startswith("compras y vendes con Banesco")


def test_cada_lado_busca_con_su_banco():
    cfg = cargar(None)
    mod.aplicar(cfg, mod.Estrategia("BancoDelTesoro", "BancoPlaza"))
    assert metodos_del_lado(cfg.mercado, "BUY") == ["BancoDelTesoro"]     # compro: vendedores que aceptan Tesoro
    assert metodos_del_lado(cfg.mercado, "SELL") == ["BancoPlaza"]        # vendo: compradores que aceptan Plaza
    assert cuerpo_busqueda(cfg.mercado, "BUY")["payTypes"] == ["BancoDelTesoro"]
    assert cuerpo_busqueda(cfg.mercado, "SELL")["payTypes"] == ["BancoPlaza"]


def test_aplicar_ajusta_costos_anuncios_pagos_y_limites():
    cfg = cargar(None)
    mod.aplicar(cfg, mod.Estrategia("BancoDelTesoro", "BancoPlaza", 200_000, 0.4))
    assert cfg.anuncios.ruta == cfg.banco.ruta_por_defecto == "INTERBANCARIO"
    assert cfg.anuncios.metodos_compra == ["BancoDelTesoro"] and cfg.anuncios.metodos_venta == ["BancoPlaza"]
    assert cfg.pagos.banco_por_defecto == "0163"              # pagas desde Tesoro
    assert (cfg.anuncios.min_bs, cfg.anuncios.max_bs, cfg.mercado.monto_bs) == (100_000, 200_000, 200_000)
    assert cfg.anuncios.margen_minimo_pct == 0.4
    mod.aplicar(cfg, mod.Estrategia("Banesco", "Banesco"))
    assert cfg.anuncios.ruta == "BDV" and cfg.mercado.metodos_pago == ["Banesco"] and cfg.anuncios.max_bs == 0


def test_cambiar_estrategia_guarda_aplica_y_deja_decision(motor):
    texto = asyncio.run(motor.cambiar_estrategia(
        {"banco_compra": "BancoDelTesoro", "banco_venta": "BancoPlaza", "monto_bs": 100_000}))
    assert "compras con Banco del Tesoro y vendes con Banco Plaza" in texto and "Bs 100.000" in texto
    assert motor.cfg.mercado.banco_compra == "BancoDelTesoro" and motor.cfg.mercado.banco_venta == "BancoPlaza"
    assert any("Banco Plaza" in e["mensaje"] for e in motor.db.eventos(5, "decision"))
    # al reiniciar el programa se acuerda de todo
    m2 = Motor(cargar(None, demo=True), BaseDatos(motor.db.ruta))
    e = m2.estrategia
    assert (e.banco_compra, e.banco_venta, e.monto_bs) == ("BancoDelTesoro", "BancoPlaza", 100_000)
    assert m2.cfg.mercado.banco_venta == "BancoPlaza"


def test_sin_cambios_y_banco_invalido(motor):
    assert asyncio.run(motor.cambiar_estrategia({"banco_compra": motor.estrategia.banco_compra})).startswith("Sin cambios")
    with pytest.raises(ValueError):
        asyncio.run(motor.cambiar_estrategia({"banco_venta": "Inventado"}))


def test_migra_las_claves_sueltas_de_versiones_anteriores(tmp_path):
    db = BaseDatos(str(tmp_path / "v.db"))
    db.escribir_estado("mi_banco", "Banesco")
    db.escribir_estado("monto_objetivo_bs", 50000)
    db.escribir_estado("monto_auto", 1)
    e = mod.cargar(db, cargar(None))
    assert (e.banco_compra, e.banco_venta, e.monto_bs, e.monto_auto) == ("Banesco", "Banesco", 50000.0, True)


def test_margen_minimo_y_modo_automatico(motor):
    asyncio.run(motor.cambiar_estrategia({"margen_minimo_pct": 0.3}))
    assert motor.cfg.anuncios.margen_minimo_pct == 0.3
    texto = asyncio.run(motor.cambiar_estrategia({"monto_auto": True}))
    assert "activado" in texto and motor.estrategia.monto_auto
