from arbitraje.config import RUTA_BDV, RUTA_INTERBANCARIA, Costos, Mercado
from arbitraje.mercado import Anuncio, calcular_spreads, filtrar, neto_entre_anuncios, parsear_anuncios


def anuncio(precio=1000.0, anunciante="X", ordenes=200, tasa=0.99, min_bs=0, max_bs=0, disponible=1000, numero="A1", comerciante=True):
    return Anuncio(numero, precio, disponible, min_bs, max_bs, ["BancoDeVenezuela"], anunciante, ordenes, tasa, comerciante)


def test_spread_y_neto_basico():
    r = calcular_spreads(1013.0, 1000.0, Costos(), RUTA_BDV)
    assert round(r["spread_pct"], 2) == 1.30
    # como anunciante da menos que la brecha bruta por las comisiones
    assert r["neto_anunciante_pct"] < r["spread_pct"]
    # tomar al instante (comprar caro, vender barato) es negativo
    assert r["neto_tomador_pct"] < 0


def test_spread_sin_precios():
    r = calcular_spreads(None, 1000.0, Costos(), RUTA_BDV)
    assert r == {"spread_pct": None, "neto_anunciante_pct": None, "neto_tomador_pct": None}


def test_ruta_interbancaria_resta_margen():
    bdv = neto_entre_anuncios(1000.0, 1013.0, Costos(), RUTA_BDV)
    inter = neto_entre_anuncios(1000.0, 1013.0, Costos(), RUTA_INTERBANCARIA)
    assert bdv > inter  # el costo interbancario reduce el margen


def test_filtrar_excluye_propios():
    anuncios = [anuncio(numero="MIO"), anuncio(numero="AJENO")]
    validos = filtrar(anuncios, Mercado(min_ordenes_mes=0, min_tasa_completado=0), propios={"MIO"})
    assert [a.numero for a in validos] == ["AJENO"]


def test_filtrar_por_calidad_y_apodo():
    cfg = Mercado(min_ordenes_mes=50, min_tasa_completado=90, ignorar_apodos=["malo"])
    anuncios = [
        anuncio(anunciante="bueno", ordenes=100, tasa=0.95),
        anuncio(anunciante="malo", ordenes=100, tasa=0.95),       # en la lista de ignorados
        anuncio(anunciante="pocas", ordenes=10, tasa=0.99),       # pocas órdenes
        anuncio(anunciante="baja", ordenes=100, tasa=0.80),       # tasa baja
    ]
    validos = filtrar(anuncios, cfg)
    assert [a.anunciante for a in validos] == ["bueno"]


def test_filtrar_respeta_limites_de_monto():
    cfg = Mercado(min_ordenes_mes=0, min_tasa_completado=0, monto_bs=100000)
    anuncios = [
        anuncio(numero="ok", min_bs=5000, max_bs=200000, disponible=1000, precio=1000),
        anuncio(numero="min_alto", min_bs=150000, max_bs=300000),
        anuncio(numero="max_bajo", min_bs=1000, max_bs=50000),
    ]
    validos = filtrar(anuncios, cfg)
    assert [a.numero for a in validos] == ["ok"]


def test_parsear_anuncios_rechaza_error():
    import pytest

    from arbitraje.mercado import ErrorMercado

    with pytest.raises(ErrorMercado):
        parsear_anuncios({"success": False, "message": "fallo"})


def test_parsear_anuncios_lee_campos():
    datos = {
        "code": "000000",
        "data": [{
            "adv": {"advNo": "123", "price": "1005.5", "surplusAmount": "500",
                    "minSingleTransAmount": "1000", "maxSingleTransAmount": "50000",
                    "tradeMethods": [{"identifier": "BancoDeVenezuela"}]},
            "advertiser": {"nickName": "Juan", "monthOrderCount": 300, "monthFinishRate": 0.98, "userType": "merchant"},
        }],
    }
    anuncios = parsear_anuncios(datos)
    assert len(anuncios) == 1
    assert anuncios[0].numero == "123"
    assert anuncios[0].precio == 1005.5
    assert anuncios[0].comerciante is True


def test_descarta_anuncios_sin_stock_o_que_no_cumplen_su_minimo():
    from arbitraje.config import Mercado
    from arbitraje.mercado import Anuncio, filtrar

    def anuncio(precio, disponible, min_bs, max_bs):
        return Anuncio("n", precio, disponible, min_bs, max_bs, ["BancoDeVenezuela"], "x", 500, 0.99, False)

    cfg = Mercado()
    anuncios = [
        anuncio(996.1, 0, 300, 398),            # sin USDT disponible
        anuncio(995.0, 1, 300, 626),            # 1 USDT: no alcanza para ninguna orden útil pero cumple su mínimo
        anuncio(990.0, 100, 500_000, 99_000),   # máximo menor que el mínimo
        anuncio(999.9, 500, 50_000, 499_000),   # bueno
    ]
    precios = [a.precio for a in filtrar(anuncios, cfg)]
    assert 996.1 not in precios and 990.0 not in precios and 999.9 in precios


def test_monto_se_envia_a_binance_sin_perder_ceros():
    from arbitraje.config import Mercado
    from arbitraje.mercado import cuerpo_busqueda

    for monto, esperado in ((50000, "50000"), (50000.0, "50000"), (100000.0, "100000"), (1234.5, "1234.5"), (1_000_000, "1000000")):
        assert cuerpo_busqueda(Mercado(monto_bs=monto), "BUY")["transAmount"] == esperado
