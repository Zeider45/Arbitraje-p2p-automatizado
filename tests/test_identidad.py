from arbitraje import identidad
from arbitraje.identidad import COINCIDE, DIFIERE, Observado, Perfil, SIN_DATOS, comparar_nombres, verificar


def test_comparar_nombres():
    assert comparar_nombres("Juan Pérez", "JUAN PEREZ") == COINCIDE
    assert comparar_nombres("Juan Pablo Pérez", "Pérez Juan") == COINCIDE   # orden distinto
    assert comparar_nombres("Juan M*** P***", "Carlos Gómez") == SIN_DATOS  # enmascarado: no decide
    assert comparar_nombres("", "Juan") == SIN_DATOS
    # nombres totalmente distintos nunca se marcan como fraude (solo cédula/teléfono lo hacen)
    assert comparar_nombres("Ana Torres", "Luis Díaz") == SIN_DATOS


def test_verificar_cedula_coincide():
    perfil = Perfil(nombre="Juan Pérez", cedula="V-12.345.678", telefonos=["0414-1234567"])
    v = verificar(perfil, Observado(cedula="12345678", telefono="0414-1234567"))
    assert v.nivel == COINCIDE
    assert v.campos["cedula"] == COINCIDE
    assert v.campos["telefono"] == COINCIDE


def test_verificar_cedula_difiere_es_tercero():
    perfil = Perfil(cedula="V12345678", telefonos=["04141234567"])
    v = verificar(perfil, Observado(cedula="V99999999"))
    assert v.nivel == DIFIERE
    assert v.difiere is True
    assert "TERCERO" in v.resumen.upper()


def test_verificar_telefono_difiere():
    perfil = Perfil(telefonos=["04141234567"])
    v = verificar(perfil, Observado(telefono="0424-7654321"))
    assert v.nivel == DIFIERE


def test_verificar_sin_perfil_es_sin_datos():
    v = verificar(Perfil(), Observado(cedula="V12345678"))
    assert v.nivel == SIN_DATOS
    assert "a mano" in v.resumen


def test_verificar_coincide_aunque_falte_un_campo():
    # teléfono coincide; cédula solo está en el perfil -> no contradice
    perfil = Perfil(cedula="V12345678", telefonos=["04141234567"])
    v = verificar(perfil, Observado(telefono="04141234567"))
    assert v.nivel == COINCIDE
    assert v.campos["cedula"] == SIN_DATOS
