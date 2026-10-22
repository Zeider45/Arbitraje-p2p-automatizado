import asyncio

import pytest

from arbitraje.config import cargar
from arbitraje.db import BaseDatos
from arbitraje.tareas import Motor
from arbitraje.utilidades import ahora_ms


@pytest.fixture
def motor(tmp_path):
    cfg = cargar(None)
    db = BaseDatos(str(tmp_path / "t.db"))
    return Motor(cfg, db)


def orden(numero, nick="pedro", estado="PENDING", tipo="COMPRA", hace_min=1):
    return {"numero": numero, "origen": "binance", "tipo": tipo, "activo": "USDT", "fiat": "VES", "cantidad": 10,
            "precio": 100.0, "total_bs": 1000.0, "comision": 0, "estado": estado, "contraparte": nick,
            "creada_ms": ahora_ms() - hace_min * 60_000}


def correr(motor):
    return asyncio.run(motor.revisar_pagos_pendientes())


def decisiones(motor):
    return [e["mensaje"] for e in motor.db.eventos(20, "decision")]


def test_sin_datos_de_contraparte_avisa_y_no_inventa(motor):
    motor.db.guardar_ordenes([orden("A1")])
    assert len(correr(motor)) == 1
    assert "no tengo teléfono ni cédula" in decisiones(motor)[0]


def test_con_perfil_arma_el_bloque_una_sola_vez(motor):
    motor.db.guardar_contraparte({"nick": "pedro", "cedula": "V12345678", "telefonos": "04141234567"})
    motor.db.guardar_ordenes([orden("A2")])
    assert "Armé y envié el bloque" in correr(motor)[0]
    assert correr(motor) == []  # no repite el aviso de la misma orden
    assert len(decisiones(motor)) == 1


def test_contraparte_sospechosa_bloquea(motor):
    motor.db.guardar_contraparte({"nick": "pedro", "cedula": "V1", "telefonos": "04141234567", "confianza": "sospechosa"})
    motor.db.guardar_ordenes([orden("A3")])
    assert "BLOQUEÉ" in correr(motor)[0]


def test_ignora_ventas_completadas_y_viejas(motor):
    motor.db.guardar_ordenes([orden("V1", tipo="VENTA"), orden("C1", estado="COMPLETED"), orden("O1", hace_min=600)])
    assert correr(motor) == []


def test_en_pausa_no_prepara(motor):
    motor.db.guardar_contraparte({"nick": "pedro", "cedula": "V12345678", "telefonos": "04141234567"})
    motor.db.escribir_estado("operativa_pausada", 1)
    motor.db.guardar_ordenes([orden("P1")])
    assert "en pausa" in correr(motor)[0]
