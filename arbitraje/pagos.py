"""Prepara los datos de un pago móvil para pegarlos en la BDVApp ("Pegar Datos").

El bot NUNCA paga: solo arma el bloque de datos del beneficiario (banco, teléfono,
cédula, monto y concepto) y te lo manda por Telegram. Tú lo pegas en tu BDVApp,
confirmas con tu clave y envías. Los datos del vendedor los tomas de la orden de
Binance (puedes pegar ahí su bloque y el panel extrae lo que necesita).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .utilidades import formato_numero, parsear_monto

# Códigos de los bancos venezolanos (para el pago móvil interbancario).
BANCOS_VE = {
    "0102": "Banco de Venezuela",
    "0104": "Venezolano de Crédito",
    "0105": "Mercantil",
    "0108": "BBVA Provincial",
    "0114": "Bancaribe",
    "0115": "Banco Exterior",
    "0128": "Banco Caroní",
    "0134": "Banesco",
    "0137": "Sofitasa",
    "0138": "Banco Plaza",
    "0146": "Bangente",
    "0151": "BFC Banco Fondo Común",
    "0156": "100% Banco",
    "0157": "DelSur",
    "0163": "Banco del Tesoro",
    "0166": "Banco Agrícola de Venezuela",
    "0168": "Bancrecer",
    "0169": "Mi Banco",
    "0171": "Banco Activo",
    "0172": "Bancamiga",
    "0174": "Banplus",
    "0175": "Banco Digital de los Trabajadores",
    "0177": "BANFANB",
    "0191": "Banco Nacional de Crédito (BNC)",
}

PLANTILLA_POR_DEFECTO = (
    "Banco: {banco_codigo} {banco_nombre}\n"
    "Teléfono: {telefono}\n"
    "Cédula: {cedula}\n"
    "Monto: {monto}\n"
    "Concepto: {concepto}"
)

PATRON_TELEFONO = r"(?:\+?58[\s-]?|0)?(4(?:12|14|16|24|26|22))[\s.-]?(\d{3})[\s.-]?(\d{2})[\s.-]?(\d{2})"
PATRON_CEDULA = r"\b([VEJPG])[\s.-]?(\d{1,3}(?:\.?\d{3}){1,2})\b"
PATRON_BANCO_CODIGO = r"\b(0\d{3})\b"


@dataclass
class DatosPago:
    monto_bs: float
    telefono: str | None = None
    cedula: str | None = None
    banco_codigo: str | None = None
    concepto: str | None = None

    @property
    def banco_nombre(self) -> str:
        return BANCOS_VE.get(self.banco_codigo or "", "")


class ErrorPago(ValueError):
    """Faltan datos para preparar el pago."""


def normalizar_telefono(valor: str | None) -> str | None:
    if not valor:
        return None
    m = re.search(PATRON_TELEFONO, valor)
    if not m:
        return None
    return f"0{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}"


def normalizar_cedula(valor: str | None) -> str | None:
    if not valor:
        return None
    m = re.search(PATRON_CEDULA, valor, re.I)
    if not m:
        # ¿solo dígitos? asúmela cédula venezolana (V)
        soloss = re.sub(r"\D", "", valor)
        return f"V{soloss}" if 6 <= len(soloss) <= 9 else None
    return f"{m.group(1).upper()}{m.group(2).replace('.', '')}"


def normalizar_banco(valor: str | None) -> str | None:
    """Acepta un código (0102), un nombre o parte del nombre y devuelve el código."""
    if not valor:
        return None
    valor = valor.strip()
    if valor in BANCOS_VE:
        return valor
    m = re.search(PATRON_BANCO_CODIGO, valor)
    if m and m.group(1) in BANCOS_VE:
        return m.group(1)
    objetivo = valor.lower()
    for codigo, nombre in BANCOS_VE.items():
        if objetivo in nombre.lower() or nombre.lower() in objetivo:
            return codigo
    return None


def extraer_de_texto(texto: str) -> dict:
    """Extrae teléfono, cédula, banco y monto de un bloque pegado (el de la orden)."""
    datos: dict = {}
    if tel := normalizar_telefono(texto):
        datos["telefono"] = tel
    if ced := normalizar_cedula(texto):
        datos["cedula"] = ced
    if banco := normalizar_banco(texto):
        datos["banco_codigo"] = banco
    if m := re.search(r"(?:Bs\.?|VES|monto)\s*:?\s*(\d[\d.,]*)", texto, re.I):
        if (monto := parsear_monto(m.group(1))):
            datos["monto_bs"] = monto
    return datos


def construir(
    monto_bs: float,
    *,
    telefono: str | None = None,
    cedula: str | None = None,
    banco: str | None = None,
    concepto: str | None = None,
    banco_por_defecto: str = "0102",
) -> DatosPago:
    if not monto_bs or monto_bs <= 0:
        raise ErrorPago("El monto debe ser mayor que 0.")
    tel = normalizar_telefono(telefono)
    ced = normalizar_cedula(cedula)
    if not tel and not ced:
        raise ErrorPago("Hace falta al menos el teléfono o la cédula del beneficiario.")
    codigo = normalizar_banco(banco) or banco_por_defecto
    return DatosPago(monto_bs=round(monto_bs, 2), telefono=tel, cedula=ced,
                     banco_codigo=codigo, concepto=(concepto or "").strip() or None)


def formatear(datos: DatosPago, plantilla: str = "") -> str:
    """Arma el bloque de texto listo para pegar. Omite las líneas sin dato."""
    valores = {
        "banco_codigo": datos.banco_codigo or "",
        "banco_nombre": datos.banco_nombre,
        "telefono": datos.telefono or "",
        "cedula": datos.cedula or "",
        "monto": formato_numero(datos.monto_bs),
        "concepto": datos.concepto or "",
    }
    lineas = []
    for linea in (plantilla or PLANTILLA_POR_DEFECTO).splitlines():
        texto = linea.format(**valores).rstrip()
        # si la línea tenía un campo y quedó vacío (p. ej. "Cédula:"), se omite
        if texto and not texto.endswith(":"):
            lineas.append(texto)
    return "\n".join(lineas)
