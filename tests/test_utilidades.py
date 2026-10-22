from datetime import date
from zoneinfo import ZoneInfo

import pytest

from arbitraje.utilidades import DIA_MS, formato_numero, inicio_dia_ms, parsear_monto, rango_periodo

CARACAS = ZoneInfo("America/Caracas")


@pytest.mark.parametrize("texto,esperado", [
    ("1.234.567,89", 1234567.89),   # formato venezolano
    ("Bs. 15.230,00", 15230.0),
    ("1,234,567.89", 1234567.89),   # formato anglosajón
    ("1234.5", 1234.5),
    ("1.500", 1500.0),              # miles sin decimales
    ("1,5", 1.5),                   # decimal con coma
    ("0,500", 0.5),
    ("250", 250.0),
    ("Bs 48.750,00", 48750.0),
    ("-12,50", -12.5),
    ("", None),
    ("sin numeros", None),
])
def test_parsear_monto(texto, esperado):
    assert parsear_monto(texto) == esperado


def test_formato_numero_venezolano():
    assert formato_numero(1234567.891) == "1.234.567,89"
    assert formato_numero(1000) == "1.000,00"
    assert formato_numero(None) == "—"
    assert formato_numero(5.1, 3) == "5,100"


def test_rango_periodo_hoy_cubre_el_dia_local():
    ahora = inicio_dia_ms(date(2026, 10, 22), CARACAS) + 15 * 3600 * 1000  # 15:00 Caracas
    desde, hasta = rango_periodo("hoy", CARACAS, ahora)
    assert desde == inicio_dia_ms(date(2026, 10, 22), CARACAS)
    assert hasta == ahora


def test_rango_periodo_7d_incluye_siete_dias():
    ahora = inicio_dia_ms(date(2026, 10, 22), CARACAS) + 10 * 3600 * 1000
    desde, hasta = rango_periodo("7d", CARACAS, ahora)
    assert desde == inicio_dia_ms(date(2026, 10, 16), CARACAS)  # 22,21,20,19,18,17,16 = 7 días


def test_rango_periodo_todo_sin_inicio():
    desde, _ = rango_periodo("todo", CARACAS)
    assert desde is None


def test_rango_periodo_invalido():
    with pytest.raises(ValueError):
        rango_periodo("semana", CARACAS)
