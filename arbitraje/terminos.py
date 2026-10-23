"""Textos listos para pegar al crear tus anuncios en Binance P2P: términos, cancelación y respuesta automática.

Binance limita el texto de los términos (~1000 caracteres), así que cada plantilla es corta. Se pueden
reemplazar en config.toml ([anuncios] terminos_compra, terminos_venta, respuesta_automatica) usando
{banco} y {minutos}.
"""
from __future__ import annotations

from .bancos import BANCOS

TERMINOS_COMPRA = """COMPRO USDT — PAGO POR {banco}
1) Paso desde mi cuenta de {banco}. Envíame tus datos de {banco} (cuenta o teléfono, cédula y nombre): deben ser de TU cuenta y coincidir con el nombre verificado en tu perfil de Binance. No acepto cuentas de terceros.
2) Pago dentro de {minutos} minutos desde que me envías tus datos. Te mando el comprobante.
3) Si los datos son de otra persona, cancelo la orden sin penalidad para mí.
4) No escribas "USDT", "cripto" ni "Binance" en el concepto del pago.
5) Una vez pagado, libera tus USDT en cuanto confirmes el pago. Si no los liberas, abro apelación con el comprobante."""

TERMINOS_VENTA = """VENDO USDT — COBRO POR {banco}
1) Solo pago desde {banco}, desde una cuenta A NOMBRE del titular verificado en Binance. NO acepto pagos de terceros: si el titular no coincide, no libero y devuelvo el pago.
2) Paga el monto exacto en {minutos} minutos o menos. Si no pagas en ese tiempo, la orden se cancela. No marques "pagado" sin haber pagado.
3) Si cancelas después de pagar, escríbeme: no se libera hasta aclarar el pago.
4) Sin referencias en el concepto: no escribas "USDT", "cripto" ni "Binance".
5) Libero cuando el dinero ya está en mi cuenta de {banco}; puede tardar unos minutos. Envía el comprobante en el chat."""

RESPUESTA_AUTOMATICA = """Hola. Pago/cobro solo por {banco} y únicamente de cuentas a nombre del titular verificado en Binance (sin terceros). Tienes {minutos} minutos para completar el pago y enviar el comprobante en este chat. Gracias."""

CONDICIONES = [
    "Verificación de identidad (KYC) completada.",
    "Registrado en Binance hace más de 7 días (o los que prefieras).",
    "Opcional: mínimo de órdenes completadas para evitar cuentas nuevas.",
]


def _llenar(plantilla: str, banco: str, minutos: int) -> str:
    return plantilla.format_map({"banco": banco, "minutos": minutos})


def textos(cfg_anuncios, banco_id: str) -> dict:
    """Términos de compra y venta, respuesta automática y condiciones sugeridas para el banco con el que operas."""
    nombre = BANCOS.get(banco_id, banco_id)
    minutos = cfg_anuncios.pay_time_limit
    return {
        "compra": _llenar(cfg_anuncios.terminos_compra or TERMINOS_COMPRA, nombre, minutos),
        "venta": _llenar(cfg_anuncios.terminos_venta or TERMINOS_VENTA, nombre, minutos),
        "respuesta": _llenar(cfg_anuncios.respuesta_automatica or RESPUESTA_AUTOMATICA, nombre, minutos),
        "condiciones": CONDICIONES,
    }
