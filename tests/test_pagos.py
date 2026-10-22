import pytest

from arbitraje import pagos


def test_normalizar_telefono():
    assert pagos.normalizar_telefono("0414-1234567") == "04141234567"
    assert pagos.normalizar_telefono("+58 414 123 45 67") == "04141234567"
    assert pagos.normalizar_telefono("412-1112233") == "04121112233"
    assert pagos.normalizar_telefono("no hay") is None


def test_normalizar_cedula():
    assert pagos.normalizar_cedula("V-12.345.678") == "V12345678"
    assert pagos.normalizar_cedula("e 8.111.222") == "E8111222"
    assert pagos.normalizar_cedula("12345678") == "V12345678"  # solo dígitos -> V
    assert pagos.normalizar_cedula("ab") is None


def test_normalizar_banco_por_codigo_y_nombre():
    assert pagos.normalizar_banco("0134") == "0134"
    assert pagos.normalizar_banco("Banesco") == "0134"
    assert pagos.normalizar_banco("banco de venezuela") == "0102"
    assert pagos.normalizar_banco("0134 Banesco") == "0134"
    assert pagos.normalizar_banco("inexistente") is None


def test_extraer_de_texto():
    datos = pagos.extraer_de_texto("Banesco 0134 · 0414-1234567 · V-12.345.678 · Bs 1.500,00")
    assert datos["telefono"] == "04141234567"
    assert datos["cedula"] == "V12345678"
    assert datos["banco_codigo"] == "0134"
    assert datos["monto_bs"] == 1500.0


def test_construir_exige_destino():
    with pytest.raises(pagos.ErrorPago):
        pagos.construir(100.0)  # sin teléfono ni cédula
    with pytest.raises(pagos.ErrorPago):
        pagos.construir(0, telefono="0414-1234567")  # monto inválido


def test_construir_usa_banco_por_defecto():
    d = pagos.construir(1500.0, telefono="0414-1234567")
    assert d.banco_codigo == "0102"
    assert d.banco_nombre == "Banco de Venezuela"
    assert d.telefono == "04141234567"


def test_formatear_omite_lineas_vacias():
    d = pagos.construir(1500.0, telefono="0414-1234567", banco="0134", concepto="Pago USDT")
    bloque = pagos.formatear(d)
    assert "0134 Banesco" in bloque
    assert "04141234567" in bloque
    assert "1.500,00" in bloque
    assert "Pago USDT" in bloque
    assert "Cédula" not in bloque  # no se dio cédula -> no aparece la línea


def test_formatear_plantilla_personalizada():
    d = pagos.construir(2000.0, cedula="V12345678")
    bloque = pagos.formatear(d, "{cedula} {monto}")
    assert bloque == "V12345678 2.000,00"
