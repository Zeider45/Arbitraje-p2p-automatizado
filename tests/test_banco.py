from arbitraje.banco import ENTRADA, SALIDA, conciliar, interpretar, reporte
from arbitraje.config import Banco


def test_interpretar_pago_recibido():
    a = interpretar("BDV: Recibiste un PagomovilBDV por Bs. 15.230,00 del 0414-1234567 Ref: 001234567890")
    assert a is not None
    assert a.tipo == ENTRADA
    assert a.monto_bs == 15230.0
    assert a.referencia == "001234567890"
    assert "0414" in a.contraparte


def test_interpretar_pago_enviado():
    a = interpretar("Realizaste un pago movil por Bs 8.500,50 Referencia 998877")
    assert a.tipo == SALIDA
    assert a.monto_bs == 8500.5
    assert a.referencia == "998877"


def test_interpretar_entrada_gana_si_aparece_primero():
    a = interpretar("Recibiste Bs 1.000,00 enviado por Juan")
    assert a.tipo == ENTRADA


def test_interpretar_detecta_cedula():
    a = interpretar("Recibiste un pago de Bs 500,00 de V-12.345.678 Ref 123456")
    assert a.contraparte and "V12345678" in a.contraparte


def test_interpretar_no_es_pago():
    assert interpretar("Tu clave de acceso es 1234") is None
    assert interpretar("Recibiste un mensaje nuevo") is None  # sin monto


def test_interpretar_patrones_personalizados():
    cfg = Banco(patron_entrada=r"ingreso", patron_monto=r"VES ([\d.,]+)")
    a = interpretar("Ingreso confirmado VES 2.500,00 cod 77", cfg)
    assert a.tipo == ENTRADA
    assert a.monto_bs == 2500.0


def _mov(id, tipo, monto, ts, orden=None):
    return {"id": id, "ts_ms": ts, "tipo": tipo, "monto_bs": monto, "referencia": None,
            "contraparte": None, "texto": "", "origen": "sms", "orden_numero": orden}


def _ord(numero, tipo, total, ts, estado="COMPLETED"):
    return {"numero": numero, "tipo": tipo, "total_bs": total, "cantidad": total / 1000,
            "precio": 1000, "estado": estado, "creada_ms": ts, "origen": "binance"}


def test_conciliar_empareja_por_monto_y_tiempo():
    T = 1_760_000_000_000
    movimientos = [_mov(1, ENTRADA, 50000.0, T + 60_000)]  # te pagaron -> venta
    ordenes = [_ord("v1", "VENTA", 50000.0, T), _ord("v2", "VENTA", 99999.0, T)]
    asign = conciliar(movimientos, ordenes, tolerancia_bs=1.0, ventana_min=180)
    assert asign == [(1, "v1")]


def test_conciliar_no_reutiliza_orden():
    T = 1_760_000_000_000
    movimientos = [_mov(1, ENTRADA, 50000.0, T + 60_000), _mov(2, ENTRADA, 50000.0, T + 120_000)]
    ordenes = [_ord("v1", "VENTA", 50000.0, T)]
    asign = conciliar(movimientos, ordenes, 1.0, 180)
    assert len(asign) == 1  # solo una orden disponible


def test_reporte_por_liberar_y_alertas():
    T = 1_760_000_000_000
    ordenes = [_ord("v1", "VENTA", 50000.0, T, estado="BUYER_PAYED"),
               _ord("v2", "VENTA", 30000.0, T, estado="CANCELLED")]
    movimientos = [_mov(1, ENTRADA, 50000.0, T + 60_000, orden="v1"),
                   _mov(2, ENTRADA, 30000.0, T + 60_000, orden="v2")]
    r = reporte(movimientos, ordenes)
    assert r["activo"] is True
    assert len(r["por_liberar"]) == 1
    assert r["por_liberar"][0]["orden"]["numero"] == "v1"
    assert any("CANCELADA" in a for a in r["alertas"])
