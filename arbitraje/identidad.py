"""Verificación de identidad de la contraparte (protección anti-triangulación).

Compara los datos del beneficiario/pagador del BANCO con la identidad que tienes
guardada de esa contraparte en BINANCE (su perfil/orden). Objetivo: no pagarle a un
tercero y no liberar USDT cuando quien pagó no es el comprador.

Principio de diseño, pensado para no bloquear operaciones legítimas por error:
  - La **cédula** y el **teléfono** son identificadores exactos: si están en ambos
    lados y NO coinciden, es "posible tercero" (difiere) → frena y avisa.
  - El **nombre** solo puede CONFIRMAR (coincide), nunca contradecir: Binance suele
    enmascararlo ("Jose M*** P***"), así que un nombre que no calza no se toma como
    fraude, solo como "sin datos suficientes".
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .pagos import normalizar_cedula, normalizar_telefono

COINCIDE = "coincide"
DIFIERE = "difiere"
SIN_DATOS = "sin_datos"
UMBRAL_NOMBRE = 0.84


def _normalizar_texto(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.upper()


def tokens_nombre(nombre: str | None) -> set[str]:
    """Palabras significativas del nombre: sin acentos, sin iniciales ni partes enmascaradas."""
    if not nombre:
        return set()
    palabras = re.split(r"\s+", _normalizar_texto(nombre))
    return {p for p in (re.sub(r"[^A-ZÑ]", "", x) for x in palabras if "*" not in x) if len(p) >= 2}


def comparar_nombres(a: str | None, b: str | None) -> str:
    """COINCIDE si comparten apellidos/nombres claros; nunca DIFIERE (los nombres se enmascaran)."""
    ta, tb = tokens_nombre(a), tokens_nombre(b)
    if not ta or not tb:
        return SIN_DATOS
    comunes = ta & tb
    menor = min(len(ta), len(tb))
    if len(comunes) >= 2 or (len(comunes) >= 1 and menor <= 2):
        return COINCIDE
    ratio = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return COINCIDE if ratio >= UMBRAL_NOMBRE else SIN_DATOS


def _comparar_exacto(a: str | None, b: str | None) -> str:
    if not a or not b:
        return SIN_DATOS
    return COINCIDE if a == b else DIFIERE


@dataclass
class Perfil:
    """Identidad verificada de la contraparte, tomada de su perfil/orden en Binance."""
    nombre: str | None = None
    cedula: str | None = None
    telefonos: list[str] = field(default_factory=list)

    @property
    def tiene_datos(self) -> bool:
        return bool(self.nombre or self.cedula or self.telefonos)


@dataclass
class Observado:
    """Lo que trae el banco (a quién pagas, o quién te pagó)."""
    nombre: str | None = None
    cedula: str | None = None
    telefono: str | None = None


@dataclass
class Veredicto:
    nivel: str                 # coincide | difiere | sin_datos
    campos: dict               # {"cedula": nivel, "telefono": nivel, "nombre": nivel}
    resumen: str

    @property
    def difiere(self) -> bool:
        return self.nivel == DIFIERE

    @property
    def coincide(self) -> bool:
        return self.nivel == COINCIDE


def verificar(perfil: Perfil, obs: Observado) -> Veredicto:
    cedula = _comparar_exacto(normalizar_cedula(perfil.cedula), normalizar_cedula(obs.cedula))
    tel_obs = normalizar_telefono(obs.telefono)
    tels = [normalizar_telefono(t) for t in perfil.telefonos]
    tels = [t for t in tels if t]
    if not tel_obs or not tels:
        telefono = SIN_DATOS
    else:
        telefono = COINCIDE if tel_obs in tels else DIFIERE
    nombre = comparar_nombres(perfil.nombre, obs.nombre)

    campos = {"cedula": cedula, "telefono": telefono, "nombre": nombre}
    if not perfil.tiene_datos:
        return Veredicto(SIN_DATOS, campos, "No tienes guardada la identidad de esta contraparte: verifícala a mano.")
    if DIFIERE in (cedula, telefono):
        cuales = [n for n, v in (("cédula", cedula), ("teléfono", telefono)) if v == DIFIERE]
        return Veredicto(DIFIERE, campos,
                         f"⚠️ POSIBLE TERCERO: {' y '.join(cuales)} del banco NO coincide(n) con el perfil de Binance.")
    if COINCIDE in (cedula, telefono, nombre):
        confirmados = [n for n, v in (("cédula", cedula), ("teléfono", telefono), ("nombre", nombre)) if v == COINCIDE]
        return Veredicto(COINCIDE, campos, f"Coincide con la contraparte de Binance ({', '.join(confirmados)}).")
    return Veredicto(SIN_DATOS, campos, "No hay datos suficientes para comparar: verifica el titular a mano.")
