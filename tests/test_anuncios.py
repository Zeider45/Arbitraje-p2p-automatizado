from arbitraje.anuncios import planear
from arbitraje.config import Anuncios, Costos
from arbitraje.mercado import Cotizacion, neto_entre_anuncios


def cot(pc, pv):
    return Cotizacion(ts_ms=0, precio_compra=pc, precio_venta=pv, anuncios_compra=5, anuncios_venta=5)


def test_planear_mercado_holgado_lidera():
    cfg = Anuncios(margen_minimo_pct=0.5, ruta="BDV")
    p = planear(cot(1015.0, 1000.0), Costos(costo_mismo_banco_pct=0), cfg)
    assert p.competitivo is True
    # tu compra queda sobre el mejor comprador, tu venta bajo el mejor vendedor
    assert p.compra.precio > 1000.0 and p.compra.precio < 1015.0
    assert p.venta.precio < 1015.0 and p.venta.precio > 1000.0
    assert p.margen_pct >= 0.5


def test_planear_respeta_margen_minimo_al_ensanchar():
    cfg = Anuncios(margen_minimo_pct=0.8, ruta="BDV")
    costos = Costos(costo_mismo_banco_pct=0)
    # mercado muy ajustado: liderar ambos lados perdería el margen
    p = planear(cot(1001.0, 1000.0), costos, cfg)
    assert p.competitivo is False
    assert p.compra.publicar and p.venta.publicar
    # el margen entre los precios propuestos nunca baja del mínimo
    m = neto_entre_anuncios(p.compra.precio, p.venta.precio, costos, "BDV")
    assert m >= cfg.margen_minimo_pct - 1e-6
    # para dar margen, tu venta queda por encima del mejor vendedor
    assert p.venta.precio > 1001.0


def test_planear_sin_mercado_no_publica():
    p = planear(cot(None, None), Costos(), Anuncios())
    assert p.compra.publicar is False
    assert p.venta.publicar is False


def test_planear_solo_un_lado():
    cfg = Anuncios(lados=["VENTA"], margen_minimo_pct=0.5, ruta="BDV")
    p = planear(cot(1015.0, 1000.0), Costos(costo_mismo_banco_pct=0), cfg)
    assert p.compra is None
    assert p.venta is not None and p.venta.publicar


def test_planear_margen_mayor_sube_precios_de_venta():
    costos = Costos(costo_mismo_banco_pct=0)
    bajo = planear(cot(1005.0, 1000.0), costos, Anuncios(margen_minimo_pct=0.3, ruta="BDV"))
    alto = planear(cot(1005.0, 1000.0), costos, Anuncios(margen_minimo_pct=2.0, ruta="BDV"))
    # exigir más margen aleja tus precios (compras más barato, vendes más caro)
    assert alto.venta.precio >= bajo.venta.precio
    assert alto.compra.precio <= bajo.compra.precio
