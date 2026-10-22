from arbitraje.binance_api import convertir_orden, firmar, ruta_desde_metodo, ventanas
from arbitraje.config import RUTA_BDV, RUTA_INTERBANCARIA
from arbitraje.utilidades import DIA_MS


def test_firmar_hmac_sha256_conocido():
    # vector reproducible: misma entrada -> misma firma
    assert firmar("a=1&b=2", "secreto") == firmar("a=1&b=2", "secreto")
    assert firmar("a=1&b=2", "secreto") != firmar("a=1&b=3", "secreto")
    assert len(firmar("x", "y")) == 64  # hex de 256 bits


def test_ventanas_respeta_30_dias():
    desde = 0
    hasta = 95 * DIA_MS
    tramos = ventanas(desde, hasta)
    assert len(tramos) == 4  # 30+30+30+5
    assert all(fin - ini < 30 * DIA_MS for ini, fin in tramos)
    assert tramos[0][0] == desde
    assert tramos[-1][1] == hasta
    # sin solapamiento ni huecos
    for (_, fin), (ini, _) in zip(tramos, tramos[1:]):
        assert ini == fin + 1


def test_ruta_desde_metodo():
    assert ruta_desde_metodo("BancoDeVenezuela") == RUTA_BDV
    assert ruta_desde_metodo("BDV") == RUTA_BDV
    assert ruta_desde_metodo("Banesco") == RUTA_INTERBANCARIA
    assert ruta_desde_metodo("PagoMovil") == RUTA_INTERBANCARIA
    assert ruta_desde_metodo("Bank Transfer") is None  # genérico: no sabemos a qué banco
    assert ruta_desde_metodo(None) is None


def test_convertir_orden_compra():
    cruda = {
        "orderNumber": "22660001", "tradeType": "BUY", "asset": "USDT", "fiat": "VES",
        "amount": "100.5", "unitPrice": "1000.25", "totalPrice": "100525.13", "commission": "0.1",
        "orderStatus": "COMPLETED", "advertisementRole": "TAKER", "counterPartNickName": "Ana",
        "payMethodName": "BancoDeVenezuela", "createTime": 1_760_000_000_000,
    }
    o = convertir_orden(cruda)
    assert o["numero"] == "22660001"
    assert o["tipo"] == "COMPRA"
    assert o["cantidad"] == 100.5
    assert o["ruta_pago"] == RUTA_BDV
    assert o["estado"] == "COMPLETED"


def test_convertir_orden_venta_sin_ruta():
    cruda = {"orderNumber": "x", "tradeType": "SELL", "amount": "10", "unitPrice": "1000",
             "totalPrice": "10000", "createTime": 1, "orderStatus": "COMPLETED",
             "payMethodName": "BancoDeVenezuela"}
    o = convertir_orden(cruda)
    assert o["tipo"] == "VENTA"
    assert o["ruta_pago"] is None  # la ruta solo aplica a compras (pagos salientes)
