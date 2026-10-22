"""Punto de entrada: python -m arbitraje [comando]."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import httpx

from . import __version__
from .config import cargar
from .utilidades import formato_numero

LOCALES = ("127.0.0.1", "localhost", "::1")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m arbitraje",
        description="Arbitraje P2P USDT/VES: escáner de Binance P2P, contabilidad y panel de ganancias.",
    )
    parser.add_argument("--config", default="config.toml", help="archivo de configuración (por defecto: config.toml)")
    parser.add_argument("--demo", action="store_true", help="datos simulados para probar el panel sin claves")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="comando", metavar="comando")
    sub.add_parser("servir", help="inicia el panel y los bots (comando por defecto)")
    sub.add_parser("mercado", help="consulta Binance P2P una vez y muestra precios y spread")
    sub.add_parser("metodos", help="lista los identificadores de métodos de pago en Binance P2P")
    sub.add_parser("sincronizar", help="descarga tus órdenes P2P de Binance una vez")
    probar = sub.add_parser("probar-sms", help="muestra cómo se interpreta un SMS del banco")
    probar.add_argument("texto", help="el texto del SMS, entre comillas")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    try:
        cfg = cargar(args.config, demo=args.demo)
    except ValueError as e:
        print(f"Error en la configuración: {e}", file=sys.stderr)
        return 2

    comando = args.comando or "servir"
    if comando == "servir":
        return servir(cfg)
    if comando == "probar-sms":
        from .banco import interpretar

        aviso = interpretar(args.texto, cfg.banco)
        if aviso is None:
            print("No parece un aviso de pago: no encontré si es entrada/salida y el monto.")
            print("Ajusta banco.patron_entrada / patron_salida / patron_monto en config.toml.")
            return 1
        print(f"Tipo:        {aviso.tipo}")
        print(f"Monto:       Bs {formato_numero(aviso.monto_bs)}")
        print(f"Referencia:  {aviso.referencia or '-'}")
        print(f"Contraparte: {aviso.contraparte or '-'}")
        return 0
    return asyncio.run(_comando_red(comando, cfg))


def servir(cfg) -> int:
    import uvicorn

    from .servidor import crear_app

    if cfg.panel.host not in LOCALES and not cfg.panel.clave and not cfg.demo:
        print(
            f"El panel escucharía en {cfg.panel.host} sin clave: cualquiera en la red vería tus datos.\n"
            "Define panel.clave en config.toml (o la variable PANEL_CLAVE) antes de exponerlo.",
            file=sys.stderr,
        )
        return 2
    if not cfg.binance.tiene_claves and not cfg.demo:
        logging.info("Sin API key de Binance: el escáner funciona, pero las órdenes se cargan a mano.")
    host = "localhost" if cfg.panel.host in LOCALES + ("0.0.0.0",) else cfg.panel.host
    print(f"\n  Panel listo en  http://{host}:{cfg.panel.puerto}\n  (Ctrl+C para detener)\n")
    uvicorn.run(crear_app(cfg), host=cfg.panel.host, port=cfg.panel.puerto, log_level="warning")
    return 0


async def _comando_red(comando: str, cfg) -> int:
    from . import mercado
    from .binance_api import ClienteBinance, ErrorBinance, sincronizar
    from .db import BaseDatos

    async with httpx.AsyncClient(timeout=20) as http:
        try:
            if comando == "metodos":
                print(f"Métodos de pago en Binance P2P para {cfg.mercado.fiat} (identificador -> nombre):")
                for identificador, nombre in await mercado.listar_metodos(http, cfg):
                    print(f"  {identificador:<28} {nombre}")
                return 0
            if comando == "mercado":
                c = await mercado.consultar(http, cfg)
                print(f"Comprar USDT (mejor vendedor): {formato_numero(c.precio_compra)} Bs  [{c.anuncios_compra} anuncios]")
                print(f"Vender USDT (mejor comprador): {formato_numero(c.precio_venta)} Bs  [{c.anuncios_venta} anuncios]")
                print(f"Brecha del mercado:            {formato_numero(c.spread_pct)} %")
                for ruta, neto in mercado.netos_por_ruta(c.precio_compra, c.precio_venta, cfg.costos).items():
                    print(f"Neto como anunciante ({ruta}): {formato_numero(neto)} %")
                print(f"Neto como tomador:             {formato_numero(c.neto_tomador_pct)} %")
                return 0
            if comando == "sincronizar":
                if not cfg.binance.tiene_claves:
                    print("Configura binance.api_key y binance.api_secret en config.toml.", file=sys.stderr)
                    return 2
                cliente = ClienteBinance(cfg.binance.api_key, cfg.binance.api_secret, cfg.binance.url_api, http)
                nuevas, actualizadas = await sincronizar(BaseDatos(cfg.general.base_datos), cfg, cliente)
                print(f"Listo: {nuevas} órdenes nuevas, {actualizadas} actualizadas.")
                return 0
        except (httpx.HTTPError, mercado.ErrorMercado, ErrorBinance) as e:
            print(f"Error: {e or e.__class__.__name__}", file=sys.stderr)
            return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
