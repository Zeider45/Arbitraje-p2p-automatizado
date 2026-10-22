import base64

import pytest
from fastapi.testclient import TestClient

from arbitraje.config import cargar
from arbitraje.servidor import crear_app, credenciales_validas


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    monkeypatch.setenv("ARBITRAJE_DB", str(tmp_path / "arbitraje.db"))
    cfg = cargar(None, demo=True)
    cfg.general.base_datos = str(tmp_path / "demo.db")
    with TestClient(crear_app(cfg, iniciar_tareas=False)) as c:
        yield c


def test_salud(cliente):
    assert cliente.get("/api/salud").json()["ok"] is True


def test_panel_todos_los_periodos(cliente):
    for p in ("hoy", "7d", "30d", "90d", "todo"):
        d = cliente.get(f"/api/panel?periodo={p}").json()
        assert "resumen" in d and "mercado" in d and "anuncios" in d
        assert d["resumen"]["capital_usdt"] == 1000.0


def test_panel_periodo_invalido(cliente):
    assert cliente.get("/api/panel?periodo=xx").status_code == 400


def test_crear_editar_borrar_orden_manual(cliente):
    r = cliente.post("/api/ordenes", json={"tipo": "COMPRA", "cantidad": 10, "precio": 1000, "ruta_pago": "BDV"})
    assert r.status_code == 201
    numero = r.json()["numero"]
    assert r.json()["total_bs"] == 10000.0
    assert r.json()["ruta_pago"] == "BDV"
    # editar la ruta
    r = cliente.patch(f"/api/ordenes/{numero}", json={"ruta_pago": "INTERBANCARIO"})
    assert r.json()["ruta_pago"] == "INTERBANCARIO"
    # borrar
    assert cliente.delete(f"/api/ordenes/{numero}").json()["ok"] is True
    assert cliente.delete(f"/api/ordenes/{numero}").status_code == 404


def test_no_borra_orden_no_manual(cliente):
    # las órdenes demo no se pueden borrar desde el panel
    d = cliente.get("/api/panel?periodo=todo").json()
    demo = next(o for o in d["ordenes"] if o["origen"] == "demo")
    assert cliente.delete(f"/api/ordenes/{demo['numero']}").status_code == 404


def test_banco_probar_y_registrar(cliente):
    texto = "BDV: Recibiste un PagomovilBDV por Bs. 12.345,00 del 0414-9998877 Ref: 555000111222"
    r = cliente.post("/api/banco/probar", json={"texto": texto}).json()
    assert r["ok"] is True
    assert r["aviso"]["monto_bs"] == 12345.0
    r = cliente.post("/api/banco/texto", json={"texto": texto}).json()
    assert r["ok"] is True and r["duplicado"] is False
    # el mismo SMS no se guarda dos veces
    assert cliente.post("/api/banco/texto", json={"texto": texto}).json()["duplicado"] is True


def test_webhook_sms_requiere_token(cliente):
    # en demo no hay webhook_token configurado -> 403
    assert cliente.post("/api/banco/sms", json={"texto": "x"}).status_code == 403


def test_exportar_csv(cliente):
    r = cliente.get("/api/exportar/ordenes.csv?periodo=todo")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert r.text.startswith("﻿")  # BOM para Excel
    assert "numero" in r.text.splitlines()[0]
    assert cliente.get("/api/exportar/otra.csv").status_code == 404


def test_reposicionar_simulacion(cliente):
    r = cliente.post("/api/anuncios/reposicionar").json()
    assert r["ok"] is True
    assert r["modo"] == "simular"
    assert "plan" in r


def test_credenciales_validas():
    cab = "Basic " + base64.b64encode(b"admin:clave").decode()
    assert credenciales_validas(cab, "admin", "clave") is True
    assert credenciales_validas(cab, "admin", "otra") is False
    assert credenciales_validas("Bearer xyz", "admin", "clave") is False
    assert credenciales_validas("", "admin", "clave") is False
