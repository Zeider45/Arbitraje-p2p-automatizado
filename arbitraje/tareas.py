"""Bucles en segundo plano: mercado, sincronización con Binance, avisos y resumen diario."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import datetime, timedelta

import httpx

from . import anuncios as mod_anuncios
from . import banco, binance_api, ganancias, mercado, pagos as mod_pagos
from .config import Config
from .db import BaseDatos
from .notificaciones import Telegram
from .utilidades import DIA_MS, MINUTO_MS, ahora_ms, formato_numero, rango_periodo

log = logging.getLogger(__name__)


def _describir(e: Exception) -> str:
    return str(e) or e.__class__.__name__


class Motor:
    """Coordina el escáner de mercado, la sincronización de órdenes y los avisos del banco."""

    def __init__(self, cfg: Config, db: BaseDatos, http: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self.db = db
        self.http = http or httpx.AsyncClient(timeout=20)
        self.telegram = Telegram(cfg.alertas.telegram_token, cfg.alertas.telegram_chat_id, self.http)
        self.binance = (
            binance_api.ClienteBinance(cfg.binance.api_key, cfg.binance.api_secret, cfg.binance.url_api, self.http)
            if cfg.binance.tiene_claves
            else None
        )
        self.agente = (
            mod_anuncios.ClienteAgente(cfg.binance.api_key, cfg.binance.api_secret, cfg.binance.url_api, self.http)
            if cfg.binance.tiene_claves
            else None
        )
        self.simulador = None
        if cfg.demo:
            from .demo import MercadoSimulado

            self.simulador = MercadoSimulado(cfg, db)
        self.estado = {
            "mercado": {"ok": None, "ts_ms": None, "ok_ms": None, "error": None, "aviso": None},
            "sincronizacion": {"ok": None, "ts_ms": None, "ok_ms": None, "error": None, "nuevas": 0, "actualizadas": 0},
            "anuncios": {"ok": None, "ts_ms": None, "ok_ms": None, "error": None, "modo": cfg.anuncios.modo,
                         "automatico": cfg.anuncios.automatico, "margen_pct": None, "competitivo": False,
                         "lados": {}, "acciones": [], "advertencia": None},
        }
        self.anuncios_propios: set[str] = set()  # advNo de tus anuncios, para no competir contra ti
        self.ultima_cotizacion: mercado.Cotizacion | None = None
        self._ultima_alerta: dict[str, int] = {}
        self._reposicion_previa: dict[str, float] = {}
        self._ultima_republicacion: dict[str, int] = {}
        self._tareas: list[asyncio.Task] = []
        self._candado_sync = asyncio.Lock()
        self._candado_anuncios = asyncio.Lock()

    # ---- ciclo de vida -------------------------------------------------------------

    def iniciar(self) -> None:
        if not self.cfg.demo:
            self.db.podar_mercado(ahora_ms() - 180 * DIA_MS)
        self._tareas.append(asyncio.create_task(self._repetir(self.cfg.mercado.intervalo_seg, self.actualizar_mercado)))
        if self.binance:
            self._tareas.append(
                asyncio.create_task(self._repetir(self.cfg.binance.intervalo_sync_min * 60, self.sincronizar))
            )
        if self.cfg.anuncios.automatico:
            self._tareas.append(asyncio.create_task(self._repetir(self.cfg.anuncios.intervalo_seg, self.reposicionar)))
        if self.telegram.activo and self.cfg.alertas.resumen_diario_hora:
            self._tareas.append(asyncio.create_task(self._bucle_resumen_diario()))

    async def detener(self) -> None:
        for tarea in self._tareas:
            tarea.cancel()
        await asyncio.gather(*self._tareas, return_exceptions=True)
        self._tareas.clear()
        await self.http.aclose()

    async def _repetir(self, intervalo_seg: float, trabajo) -> None:
        while True:
            try:
                await trabajo()
            except Exception:  # un error inesperado no debe detener el bot
                log.exception("Error inesperado en %s", trabajo.__name__)
            await asyncio.sleep(intervalo_seg)

    def _exito(self, trabajo: str, **extra) -> None:
        ahora = ahora_ms()
        self.estado[trabajo].update(ok=True, ts_ms=ahora, ok_ms=ahora, error=None, **extra)

    def _fallo(self, trabajo: str, mensaje: str) -> None:
        anterior = self.estado[trabajo].get("error")
        self.estado[trabajo].update(ok=False, ts_ms=ahora_ms(), error=mensaje)
        if mensaje != anterior:  # no llenar el registro con el mismo error cada minuto
            log.warning(mensaje)
            self.db.registrar_evento("error", mensaje)

    # ---- mercado -------------------------------------------------------------------

    async def actualizar_mercado(self) -> mercado.Cotizacion | None:
        try:
            if self.simulador:
                cotizacion = self.simulador.cotizar()
            else:
                cotizacion = await mercado.consultar(self.http, self.cfg, frozenset(self.anuncios_propios))
        except (httpx.HTTPError, mercado.ErrorMercado) as e:
            self._fallo("mercado", f"No pude consultar Binance P2P: {_describir(e)}")
            return None
        self.ultima_cotizacion = cotizacion
        self.db.guardar_cotizacion(cotizacion.como_fila())
        aviso = None
        if not cotizacion.anuncios_compra or not cotizacion.anuncios_venta:
            aviso = (
                "No hay anuncios que pasen tus filtros. Revisa mercado.metodos_pago "
                "(python -m arbitraje metodos) o baja min_ordenes_mes / monto_bs."
            )
        self._exito("mercado", aviso=aviso)
        await self._revisar_oportunidad(cotizacion)
        return cotizacion

    async def _revisar_oportunidad(self, c: mercado.Cotizacion) -> None:
        if c.precio_compra is None or c.precio_venta is None:
            return
        compra, venta = formato_numero(c.precio_compra), formato_numero(c.precio_venta)
        if c.neto_tomador_pct is not None and c.neto_tomador_pct > 0:
            await self._alertar(
                "tomador",
                "Arbitraje inmediato",
                f"Compra USDT a {compra} y véndelos a {venta}: {formato_numero(c.neto_tomador_pct)} % neto. "
                "Revisa los límites de ambos anuncios antes de entrar.",
            )
        elif c.neto_anunciante_pct is not None and c.neto_anunciante_pct >= self.cfg.alertas.spread_minimo_pct:
            tick = self.cfg.costos.tick_bs
            await self._alertar(
                "anunciante",
                "Spread atractivo",
                f"{formato_numero(c.neto_anunciante_pct)} % neto como anunciante. "
                f"Comprar USDT: {compra} · Vender USDT: {venta}. Publica compra a "
                f"{formato_numero(c.precio_venta + tick)} y venta a {formato_numero(c.precio_compra - tick)}.",
            )

    async def _alertar(self, clave: str, titulo: str, cuerpo: str) -> None:
        ahora = ahora_ms()
        if ahora - self._ultima_alerta.get(clave, 0) < self.cfg.alertas.enfriamiento_min * MINUTO_MS:
            return
        self._ultima_alerta[clave] = ahora
        self.db.registrar_evento("oportunidad", f"{titulo}: {cuerpo}")
        await self.telegram.enviar(titulo, cuerpo)

    # ---- repositor de anuncios -----------------------------------------------------

    async def reposicionar(self, cotizacion: mercado.Cotizacion | None = None) -> dict:
        """Calcula y (en modo real) aplica los precios de tus anuncios de compra y venta."""
        cfg = self.cfg.anuncios
        cotizacion = cotizacion or self.ultima_cotizacion
        if cotizacion is None and self.simulador is not None:
            cotizacion = self.simulador.cotizar()
        if cotizacion is None:
            return {"ok": False, "error": "Todavía no tengo precios del mercado."}

        plan = mod_anuncios.planear(cotizacion, self.cfg.costos, cfg)
        acciones: list[dict] = []
        snapshot_lados = {}
        for lado in ("COMPRA", "VENTA"):
            pl = plan.lado(lado)
            if pl:
                snapshot_lados[lado.lower()] = {"publicar": pl.publicar, "precio": pl.precio,
                                                "competitivo": pl.competitivo, "motivo": pl.motivo}

        async with self._candado_anuncios:
            if cfg.en_real and self.agente:
                acciones = await self._aplicar_plan(plan)
            else:
                acciones = self._plan_simulado(plan)

        self.estado["anuncios"].update(
            ok=self.estado["anuncios"].get("error") is None or bool(acciones),
            ts_ms=ahora_ms(), modo=cfg.modo, automatico=cfg.automatico,
            margen_pct=plan.margen_pct, competitivo=plan.competitivo,
            lados=snapshot_lados, acciones=acciones,
        )
        if self.estado["anuncios"].get("error") is None:
            self.estado["anuncios"]["ok_ms"] = ahora_ms()
        return {"ok": True, "modo": cfg.modo, "plan": snapshot_lados, "acciones": acciones, "margen_pct": plan.margen_pct}

    def _plan_simulado(self, plan: mod_anuncios.Plan) -> list[dict]:
        acciones = []
        for lado in self.cfg.anuncios.lados:
            pl = plan.lado(lado)
            if not pl:
                continue
            if pl.publicar and pl.precio:
                anterior = self._reposicion_previa.get(lado)
                verbo = "Mantendría" if anterior == pl.precio else "Publicaría"
                acciones.append({"lado": lado, "accion": "simular", "precio": pl.precio,
                                 "texto": f"{verbo} {lado.lower()} a {formato_numero(pl.precio)} Bs"
                                          + (" (lideras)" if pl.competitivo else "")})
                self._reposicion_previa[lado] = pl.precio
            else:
                acciones.append({"lado": lado, "accion": "pausar", "precio": None, "texto": f"{lado.lower()}: {pl.motivo}"})
        self.estado["anuncios"]["error"] = None
        if acciones and not self.cfg.anuncios.en_real:
            self.db.registrar_evento("info", "Simulación de anuncios: " + " · ".join(a["texto"] for a in acciones))
        return acciones

    async def _aplicar_plan(self, plan: mod_anuncios.Plan) -> list[dict]:
        try:
            vivos = {a.lado: a for a in await self.agente.listar()}
        except (httpx.HTTPError, binance_api.ErrorBinance) as e:
            self._fallo("anuncios", f"No pude leer tus anuncios en Binance: {_describir(e)}")
            return []
        self.anuncios_propios = {a.numero for a in vivos.values()}
        acciones = []
        tick = self.cfg.costos.tick_bs
        ahora = ahora_ms()
        for lado in self.cfg.anuncios.lados:
            pl = plan.lado(lado)
            if not pl:
                continue
            vivo = vivos.get(lado)
            try:
                if not pl.publicar or pl.precio is None:
                    if vivo and vivo.estado == mod_anuncios.EN_LINEA:
                        await self.agente.cambiar_estado([vivo.numero], mod_anuncios.FUERA_LINEA)
                        acciones.append({"lado": lado, "accion": "pausar", "precio": None,
                                         "texto": f"Bajé tu anuncio de {lado.lower()}: {pl.motivo}"})
                    continue
                if vivo is None:
                    acciones.append({"lado": lado, "accion": "falta", "precio": pl.precio,
                                     "texto": f"No tienes un anuncio de {lado.lower()} que reposicionar; créalo una vez en Binance."})
                    continue
                vencido = (self.cfg.anuncios.reponer_segundos > 0
                           and ahora - self._ultima_republicacion.get(lado, 0) > self.cfg.anuncios.reponer_segundos * 1000)
                if abs(vivo.precio - pl.precio) < tick / 2 and vivo.estado == mod_anuncios.EN_LINEA and not vencido:
                    continue
                await self.agente.actualizar_precio(vivo.numero, pl.precio)
                self._ultima_republicacion[lado] = ahora
                acciones.append({"lado": lado, "accion": "actualizar", "precio": pl.precio,
                                 "texto": f"Moví tu {lado.lower()} de {formato_numero(vivo.precio)} a {formato_numero(pl.precio)} Bs"})
            except (httpx.HTTPError, binance_api.ErrorBinance) as e:
                self._fallo("anuncios", f"No pude actualizar tu anuncio de {lado.lower()}: {_describir(e)}")
        if acciones:
            self.estado["anuncios"]["error"] = None
            self.db.registrar_evento("oportunidad", "Anuncios: " + " · ".join(a["texto"] for a in acciones))
        return acciones

    async def _actualizar_propios(self) -> None:
        """Refresca la lista de tus anuncios (para que el escáner no compita contra ti)."""
        if not self.agente:
            return
        try:
            self.anuncios_propios = {a.numero for a in await self.agente.listar()}
        except (httpx.HTTPError, binance_api.ErrorBinance):
            pass

    # ---- órdenes de Binance --------------------------------------------------------

    async def sincronizar(self) -> dict:
        if not self.binance:
            return {"ok": False, "error": "Configura binance.api_key y binance.api_secret (permiso solo lectura)."}
        async with self._candado_sync:
            try:
                nuevas, actualizadas = await binance_api.sincronizar(self.db, self.cfg, self.binance)
            except (httpx.HTTPError, binance_api.ErrorBinance) as e:
                mensaje = f"No pude sincronizar con Binance: {_describir(e)}"
                self._fallo("sincronizacion", mensaje)
                return {"ok": False, "error": mensaje}
        self._exito("sincronizacion", nuevas=nuevas, actualizadas=actualizadas)
        if nuevas:
            self.db.registrar_evento("info", f"Sincronización: {nuevas} órdenes nuevas, {actualizadas} actualizadas.")
        self.conciliar()
        return {"ok": True, "nuevas": nuevas, "actualizadas": actualizadas}

    # ---- banco ---------------------------------------------------------------------

    def conciliar(self) -> int:
        """Asocia los pagos del banco sin orden con órdenes de Binance."""
        movimientos = self.db.movimientos()
        pendientes = [m for m in movimientos if not m["orden_numero"]]
        if not pendientes:
            return 0
        desde = min(m["ts_ms"] for m in pendientes) - int(self.cfg.banco.ventana_min * MINUTO_MS)
        ordenes = self.db.ordenes(desde_ms=desde)
        asignaciones = banco.conciliar(movimientos, ordenes, self.cfg.banco.tolerancia_bs, self.cfg.banco.ventana_min)
        for movimiento_id, numero in asignaciones:
            self.db.asignar_movimiento(movimiento_id, numero)
        return len(asignaciones)

    async def procesar_aviso_banco(self, texto: str, origen: str, ts_ms: int | None = None) -> dict:
        """Registra un SMS/notificación del banco, lo concilia y avisa por Telegram."""
        aviso = banco.interpretar(texto, self.cfg.banco)
        if aviso is None:
            return {"ok": False, "motivo": "El texto no parece un aviso de pago (no encontré tipo y monto)."}
        movimiento_id = self.db.guardar_movimiento(
            {
                "ts_ms": ts_ms or ahora_ms(),
                "tipo": aviso.tipo,
                "monto_bs": aviso.monto_bs,
                "referencia": aviso.referencia,
                "contraparte": aviso.contraparte,
                "texto": texto[:1000],
                "origen": origen,
            }
        )
        if movimiento_id is None:
            return {"ok": True, "duplicado": True, "aviso": asdict(aviso), "orden": None}
        if self.binance and aviso.tipo == banco.ENTRADA:
            await self.sincronizar()  # trae la orden si se creó después de la última sincronización
        self.conciliar()
        movimiento = self.db.movimiento(movimiento_id)
        orden = self.db.orden(movimiento["orden_numero"]) if movimiento and movimiento["orden_numero"] else None
        await self._avisar_pago(aviso, orden)
        return {"ok": True, "duplicado": False, "aviso": asdict(aviso), "orden": orden}

    async def _avisar_pago(self, aviso: banco.AvisoPago, orden: dict | None) -> None:
        lineas = []
        if aviso.referencia:
            lineas.append(f"Ref: {aviso.referencia}")
        if aviso.contraparte:
            lineas.append(f"Contraparte: {aviso.contraparte}")
        if aviso.tipo == banco.ENTRADA:
            titulo = f"Pago recibido: Bs {formato_numero(aviso.monto_bs)}"
            if orden:
                lineas.append(
                    f"Coincide con tu orden de venta {orden['numero']} "
                    f"({formato_numero(orden['cantidad'])} USDT, estado {orden['estado']})."
                )
            else:
                lineas.append("Todavía no lo asocio a ninguna orden de Binance.")
            lineas.append("Antes de liberar: confírmalo en tu BDVApp y verifica que el titular sea el comprador.")
        else:
            titulo = f"Pago enviado: Bs {formato_numero(aviso.monto_bs)}"
            if orden:
                lineas.append(f"Corresponde a tu orden de compra {orden['numero']} ({formato_numero(orden['cantidad'])} USDT).")
        cuerpo = "\n".join(lineas)
        self.db.registrar_evento("pago", f"{titulo}. {' '.join(lineas)}")
        await self.telegram.enviar(titulo, cuerpo)

    # ---- preparar pago (BDVApp "Pegar Datos") --------------------------------------

    async def preparar_pago(
        self,
        monto_bs: float,
        *,
        telefono: str | None = None,
        cedula: str | None = None,
        banco: str | None = None,
        concepto: str | None = None,
        orden: str | None = None,
        texto: str | None = None,
    ) -> dict:
        """Arma el bloque para pegar en la BDVApp y lo manda por Telegram. No paga nada."""
        extra = mod_pagos.extraer_de_texto(texto) if texto else {}
        monto_bs = monto_bs or extra.get("monto_bs") or 0.0
        if concepto is None and orden:
            concepto = self.cfg.pagos.concepto_por_defecto.format(orden=orden)
        try:
            datos = mod_pagos.construir(
                monto_bs,
                telefono=telefono or extra.get("telefono"),
                cedula=cedula or extra.get("cedula"),
                banco=banco or extra.get("banco_codigo"),
                concepto=concepto,
                banco_por_defecto=self.cfg.pagos.banco_por_defecto,
            )
        except mod_pagos.ErrorPago as e:
            return {"ok": False, "error": str(e)}
        bloque = mod_pagos.formatear(datos, self.cfg.pagos.plantilla)
        enviado = False
        if self.cfg.pagos.enviar_telegram:
            enviado = await self.telegram.enviar(
                f"Pago por preparar: Bs {formato_numero(datos.monto_bs)}",
                "Pega esto en “Pegar Datos” de tu BDVApp, confirma con tu clave y envía:\n\n" + bloque,
                pre=True,
            )
        self.db.registrar_evento(
            "pago",
            f"Pago preparado: Bs {formato_numero(datos.monto_bs)} a "
            + (datos.telefono or datos.cedula or "?") + (f" (orden {orden})" if orden else ""),
        )
        return {"ok": True, "bloque": bloque, "enviado": enviado,
                "datos": {"monto_bs": datos.monto_bs, "telefono": datos.telefono, "cedula": datos.cedula,
                          "banco_codigo": datos.banco_codigo, "banco_nombre": datos.banco_nombre,
                          "concepto": datos.concepto}}

    # ---- resumen diario ------------------------------------------------------------

    def texto_resumen(self) -> tuple[str, str]:
        tz = self.cfg.tz
        desde, hasta = rango_periodo("hoy", tz)
        ordenes = self.db.ordenes()
        ruta = self.cfg.banco.ruta_por_defecto
        ciclos, posicion = ganancias.emparejar(ordenes, self.cfg.costos, ruta)
        r = ganancias.resumen(
            ordenes, ciclos, posicion,
            capital_usdt=self.cfg.general.capital_usdt, tz=tz, desde_ms=desde, hasta_ms=hasta, ahora_ms=hasta,
            ruta_por_defecto=ruta, limites_bs=self.cfg.banco.limites(), cotizacion=self.db.ultima_cotizacion(),
        )
        titulo = f"Resumen del día: {formato_numero(r['ganancia_usdt'])} USDT"
        cuerpo = (
            f"Ganancia: Bs {formato_numero(r['ganancia_bs'])} · ROI {formato_numero(r['roi_pct'], 3)} %\n"
            f"Ciclos: {r['ciclos']} · Órdenes completadas: {r['ordenes']['completadas']}\n"
            f"Volumen: {formato_numero(r['volumen_usdt'])} USDT\n"
            f"Pagado hoy BDV→BDV: Bs {formato_numero(r['pagado_hoy']['bdv']['usado_bs'])} · "
            f"interbancario: Bs {formato_numero(r['pagado_hoy']['interbancario']['usado_bs'])}"
        )
        return titulo, cuerpo

    async def _bucle_resumen_diario(self) -> None:
        horas, minutos = (int(x) for x in self.cfg.alertas.resumen_diario_hora.split(":"))
        while True:
            ahora = datetime.now(self.cfg.tz)
            objetivo = ahora.replace(hour=horas, minute=minutos, second=0, microsecond=0)
            if objetivo <= ahora:
                objetivo += timedelta(days=1)
            await asyncio.sleep((objetivo - ahora).total_seconds())
            try:
                await self.telegram.enviar(*self.texto_resumen())
            except Exception:
                log.exception("No pude enviar el resumen diario")
