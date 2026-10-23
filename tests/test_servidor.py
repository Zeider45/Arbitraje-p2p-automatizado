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


def test_preparar_pago(cliente):
    r = cliente.post("/api/pagos/preparar", json={
        "monto_bs": 1500.0, "telefono": "0414-1234567", "banco": "0134", "concepto": "Pago USDT",
    })
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["ok"] is True
    assert "04141234567" in cuerpo["bloque"]
    assert "1.500,00" in cuerpo["bloque"]
    assert cuerpo["datos"]["banco_nombre"] == "Banesco"


def test_preparar_pago_desde_texto(cliente):
    r = cliente.post("/api/pagos/preparar", json={
        "monto_bs": 0, "texto": "Pago a 0414-1234567 V-12.345.678 Bs 2.300,00 Banesco",
    })
    assert r.status_code == 200
    assert "2.300,00" in r.json()["bloque"]


def test_preparar_pago_sin_destino_falla(cliente):
    r = cliente.post("/api/pagos/preparar", json={"monto_bs": 1000.0})
    assert r.status_code == 422


def test_contrapartes_crud(cliente):
    r = cliente.put("/api/contrapartes", json={"nick": "Yor***", "nombre": "Juan Pérez",
                                               "cedula": "V-12.345.678", "telefonos": "0414-1234567"})
    assert r.status_code == 200
    assert r.json()["nombre"] == "Juan Pérez"
    assert any(c["nick"] == "Yor***" for c in cliente.get("/api/contrapartes").json())
    assert cliente.delete("/api/contrapartes/Yor***").json()["ok"] is True
    assert cliente.delete("/api/contrapartes/Yor***").status_code == 404


def test_preparar_pago_verifica_tercero(cliente):
    # guarda la identidad verificada de una contraparte
    cliente.put("/api/contrapartes", json={"nick": "VendedorX", "cedula": "V12345678",
                                           "telefonos": "04141234567"})
    # pagar a un teléfono distinto -> posible tercero, no se envía sin confirmar
    r = cliente.post("/api/pagos/preparar", json={
        "monto_bs": 1000, "telefono": "0424-9999999", "contraparte": "VendedorX"}).json()
    assert r["ok"] is False
    assert r["requiere_confirmacion"] is True
    assert r["veredicto"]["nivel"] == "difiere"
    # al confirmar, sí arma el bloque
    r2 = cliente.post("/api/pagos/preparar", json={
        "monto_bs": 1000, "telefono": "0424-9999999", "contraparte": "VendedorX", "confirmar": True}).json()
    assert r2["ok"] is True
    # pagar al teléfono correcto -> coincide
    r3 = cliente.post("/api/pagos/preparar", json={
        "monto_bs": 1000, "telefono": "0414-1234567", "contraparte": "VendedorX"}).json()
    assert r3["ok"] is True
    assert r3["veredicto"]["nivel"] == "coincide"


def test_pausar_reanudar_operativa(cliente):
    assert cliente.get("/api/panel?periodo=hoy").json()["estado"]["operativa"]["pausada"] is False
    assert cliente.post("/api/operativa", json={"pausar": True}).json()["pausada"] is True
    # el estado queda pausado y es visible en el panel (lo verían teléfono y PC por igual)
    assert cliente.get("/api/panel?periodo=hoy").json()["estado"]["operativa"]["pausada"] is True
    assert cliente.post("/api/operativa", json={"pausar": False}).json()["pausada"] is False
    assert cliente.get("/api/panel?periodo=hoy").json()["estado"]["operativa"]["pausada"] is False


def test_reposicionar_respeta_pausa(cliente):
    cliente.post("/api/operativa", json={"pausar": True})
    r = cliente.post("/api/anuncios/reposicionar").json()
    assert r.get("pausada") is True
    assert r["acciones"] == []
    cliente.post("/api/operativa", json={"pausar": False})


def test_reposicionar_simulacion(cliente):
    r = cliente.post("/api/anuncios/reposicionar").json()
    assert r["ok"] is True
    assert r["modo"] == "simular"
    assert "plan" in r


def test_sesion_iniciar_cerrar(cliente):
    # el panel ya trae una sesión (la demo); ciérrala primero
    d = cliente.get("/api/panel?periodo=hoy").json()
    assert d["sesion"] is not None
    assert cliente.post("/api/sesion/cerrar", json={}).json()["ok"] is True
    # ahora se puede iniciar una nueva
    r = cliente.post("/api/sesion/iniciar", json={"capital_inicial": 500, "moneda_inicial": "USDT"})
    assert r.status_code == 201
    assert r.json()["moneda_inicial"] == "USDT"
    # no se permite una segunda abierta
    assert cliente.post("/api/sesion/iniciar", json={"capital_inicial": 100, "moneda_inicial": "VES"}).status_code == 409
    # el panel ahora refleja la sesión USDT y su recomendación
    s = cliente.get("/api/panel?periodo=hoy").json()["sesion"]
    assert s["moneda_inicial"] == "USDT"
    assert s["capital_inicial_usdt"] == 500
    assert "recomendacion" in s


def test_sesion_historial(cliente):
    # la demo deja una sesión de ayer ya cerrada
    sesiones = cliente.get("/api/panel?periodo=hoy").json()["sesiones"]
    assert any(x["estado"] == "cerrada" for x in sesiones)


def test_credenciales_validas():
    cab = "Basic " + base64.b64encode(b"admin:clave").decode()
    assert credenciales_validas(cab, "admin", "clave") is True
    assert credenciales_validas(cab, "admin", "otra") is False
    assert credenciales_validas("Bearer xyz", "admin", "clave") is False
    assert credenciales_validas("", "admin", "clave") is False


def test_modo_simulacion_aislado_del_real(tmp_path):
    cfg = cargar(None)
    cfg.general.base_datos = str(tmp_path / "real.db")
    cfg.binance.api_key, cfg.binance.api_secret = "k", "s"
    cfg.alertas.telegram_token, cfg.alertas.telegram_chat_id = "t", "1"
    with TestClient(crear_app(cfg, iniciar_tareas=False)) as c:
        real = c.get("/api/panel").json()
        sim = c.get("/sim/api/panel").json()
        assert real["config"]["demo"] is False and real["config"]["simulacion_disponible"] is True
        assert sim["config"]["demo"] is True
        # la simulación no hereda canales reales
        assert sim["config"]["binance_configurado"] is False and sim["config"]["telegram_configurado"] is False
        assert real["config"]["binance_configurado"] is True
        # bases de datos separadas
        assert real["ordenes"] == [] and len(sim["ordenes"]) > 0
        assert c.get("/sim/").status_code == 200


def test_simulacion_se_puede_apagar(tmp_path):
    cfg = cargar(None)
    cfg.general.base_datos = str(tmp_path / "real.db")
    cfg.panel.simulacion = False
    with TestClient(crear_app(cfg, iniciar_tareas=False)) as c:
        assert c.get("/api/panel").json()["config"]["simulacion_disponible"] is False
        assert c.get("/sim/api/panel").status_code == 404
