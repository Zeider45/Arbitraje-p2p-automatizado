"""Servidor web: API del panel y webhook para los avisos de pago del banco."""
from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import mimetypes
import secrets
import copy
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (
    __version__, anuncios as mod_anuncios, banco, ganancias, mercado, pagos as mod_pagos, sesion as mod_sesion,
    simulador as mod_simulador,
)
from .config import Config
from .db import BaseDatos
from .tareas import Motor
from .utilidades import DIA_MS, MINUTO_MS, PERIODOS, ahora_ms, rango_periodo

WEB = Path(__file__).parent / "web"
# En Windows el registro a veces asocia .js a text/plain y el navegador lo rechaza
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

RUTAS_PUBLICAS = ("/api/salud", "/api/banco/sms")  # el webhook usa su propio token
CUBETAS_MIN = (1, 5, 15, 30, 60, 120, 240, 360, 720, 1440)
MAX_PUNTOS = 300
FILAS_TABLA = 300
CLAVES_TEXTO = ("texto", "text", "message", "mensaje", "msg", "body", "sms", "content")


class OrdenManual(BaseModel):
    tipo: Literal["COMPRA", "VENTA"]
    cantidad: float = Field(gt=0)
    precio: float = Field(gt=0)
    comision: float = Field(default=0, ge=0)
    ruta_pago: Literal["BDV", "INTERBANCARIO"] | None = None
    costo_banco_bs: float | None = Field(default=None, ge=0)
    fecha_ms: int | None = None
    contraparte: str | None = Field(default=None, max_length=80)
    nota: str | None = Field(default=None, max_length=300)


class CambioOrden(BaseModel):
    ruta_pago: Literal["BDV", "INTERBANCARIO"] | None = None
    costo_banco_bs: float | None = Field(default=None, ge=0)
    nota: str | None = Field(default=None, max_length=300)


class TextoBanco(BaseModel):
    texto: str = Field(min_length=3, max_length=2000)
    fecha_ms: int | None = None


class PrepararPago(BaseModel):
    monto_bs: float = Field(default=0, ge=0)
    telefono: str | None = Field(default=None, max_length=40)
    cedula: str | None = Field(default=None, max_length=40)
    banco: str | None = Field(default=None, max_length=60)
    concepto: str | None = Field(default=None, max_length=120)
    orden: str | None = Field(default=None, max_length=40)
    contraparte: str | None = Field(default=None, max_length=80)
    texto: str | None = Field(default=None, max_length=2000)
    confirmar: bool = False


class Operativa(BaseModel):
    pausar: bool


class IniciarSesion(BaseModel):
    capital_inicial: float = Field(gt=0)
    moneda_inicial: Literal["VES", "USDT"]
    nota: str | None = Field(default=None, max_length=200)


class CerrarSesion(BaseModel):
    nota_cierre: str | None = Field(default=None, max_length=200)


class Simulacion(BaseModel):
    capital: float = Field(gt=0)
    moneda: Literal["VES", "USDT"] = "USDT"
    ciclos: int = Field(default=10, ge=1, le=mod_simulador.MAX_CICLOS)
    ciclos_por_dia: float = Field(default=1, gt=0, le=100)
    rol: Literal["anunciante", "tomador"] = "anunciante"
    ruta: Literal["BDV", "INTERBANCARIO"] | None = None
    reinvertir: bool = True
    precio_compra: float | None = Field(default=None, gt=0)  # lo que pagas por USDT; vacío = mercado actual
    precio_venta: float | None = Field(default=None, gt=0)   # lo que recibes por USDT; vacío = mercado actual


class ReiniciarSimulacion(BaseModel):
    capital: float = Field(default=1000, gt=0)
    con_ejemplo: bool = False   # True = vuelve a sembrar 45 días de datos de ejemplo


class Contraparte(BaseModel):
    nick: str = Field(min_length=1, max_length=80)
    nombre: str | None = Field(default=None, max_length=120)
    cedula: str | None = Field(default=None, max_length=40)
    telefonos: str | None = Field(default=None, max_length=200)
    bancos: str | None = Field(default=None, max_length=200)
    confianza: Literal["nueva", "verificada", "sospechosa"] = "verificada"
    nota: str | None = Field(default=None, max_length=300)


def credenciales_validas(cabecera: str, usuario: str, clave: str) -> bool:
    if not cabecera.lower().startswith("basic "):
        return False
    try:
        decodificado = base64.b64decode(cabecera[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    u, _, c = decodificado.partition(":")
    # & en vez de "and": compara ambos siempre, sin atajos que filtren información por tiempo
    return secrets.compare_digest(u.encode(), usuario.encode()) & secrets.compare_digest(c.encode(), clave.encode())


def cubeta_para(rango_ms: int) -> int:
    """Tamaño de intervalo para que la serie de mercado tenga como máximo MAX_PUNTOS puntos."""
    for minutos in CUBETAS_MIN:
        if rango_ms / (minutos * MINUTO_MS) <= MAX_PUNTOS:
            return minutos * MINUTO_MS
    return CUBETAS_MIN[-1] * MINUTO_MS


async def extraer_texto(request: Request) -> str:
    """Los reenviadores de SMS mandan el texto de formas distintas: JSON, formulario o texto plano."""
    tipo = request.headers.get("content-type", "")
    crudo = await request.body()
    if "json" in tipo:
        try:
            datos = json.loads(crudo or b"null")
        except ValueError:
            return ""
        if isinstance(datos, str):
            return datos.strip()
        if isinstance(datos, dict):
            for clave in CLAVES_TEXTO:
                if isinstance(datos.get(clave), str) and datos[clave].strip():
                    return datos[clave].strip()
        return ""
    if "x-www-form-urlencoded" in tipo:
        datos = parse_qs(crudo.decode("utf-8", "replace"))
        for clave in CLAVES_TEXTO:
            if datos.get(clave) and datos[clave][0].strip():
                return datos[clave][0].strip()
        return ""
    return crudo.decode("utf-8", "replace").strip()


def datos_panel(cfg: Config, db: BaseDatos, motor: Motor, periodo: str) -> dict:
    """Todo lo que muestra el panel para un período, calculado de una sola vez para que cuadre."""
    tz = cfg.tz
    ahora = ahora_ms()
    desde, hasta = rango_periodo(periodo, tz, ahora)
    ruta = cfg.banco.ruta_por_defecto

    def en_periodo(ts: int) -> bool:
        return (desde is None or ts >= desde) and ts <= hasta

    ordenes = db.ordenes()
    ciclos, posicion = ganancias.emparejar(ordenes, cfg.costos, ruta)
    cotizacion = db.ultima_cotizacion()
    resumen = ganancias.resumen(
        ordenes, ciclos, posicion,
        capital_usdt=cfg.general.capital_usdt, tz=tz, desde_ms=desde, hasta_ms=hasta, ahora_ms=ahora,
        ruta_por_defecto=ruta, limites_bs=cfg.banco.limites(), cotizacion=cotizacion,
    )

    actual = None
    if cotizacion:
        compra, venta, tick = cotizacion["precio_compra"], cotizacion["precio_venta"], cfg.costos.tick_bs
        actual = {
            **cotizacion,
            "netos_por_ruta": mercado.netos_por_ruta(compra, venta, cfg.costos),
            "sugerido_compra": venta + tick if venta else None,
            "sugerido_venta": compra - tick if compra else None,
        }
    anuncios = datos_anuncios(cfg, motor)
    sesion_activa = datos_sesion(cfg, db, cotizacion, ordenes)
    inicio_mercado = desde if desde is not None else (db.primera_cotizacion_ms() or hasta - DIA_MS)

    ordenes_periodo = [o for o in ordenes if en_periodo(o["creada_ms"])]
    movimientos = [m for m in db.movimientos() if en_periodo(m["ts_ms"])]
    conciliacion = banco.reporte(movimientos, ordenes_periodo)
    conciliacion["pagos_sin_orden"] = conciliacion["pagos_sin_orden"][-50:]
    conciliacion["ordenes_sin_pago"] = conciliacion["ordenes_sin_pago"][-50:]
    # verifica el titular de cada cobro listo para liberar contra el perfil de Binance
    for item in conciliacion["por_liberar"]:
        item["verificacion"] = motor.verificar_pago_recibido(item["pago"], item["orden"])

    return {
        "generado_ms": ahora,
        "periodo": periodo,
        "desde_ms": desde,
        "hasta_ms": hasta,
        "config": {
            "demo": cfg.demo,
            "simulacion_disponible": cfg.demo or cfg.panel.simulacion,
            "zona_horaria": cfg.general.zona_horaria,
            "activo": cfg.mercado.activo,
            "fiat": cfg.mercado.fiat,
            "metodos_pago": cfg.mercado.metodos_pago,
            "capital_usdt": cfg.general.capital_usdt,
            "spread_minimo_pct": cfg.alertas.spread_minimo_pct,
            "ruta_por_defecto": ruta,
            "binance_configurado": cfg.binance.tiene_claves,
            "telegram_configurado": motor.telegram.activo,
            "pagos_automaticos": cfg.pagos.automatico,
            "whatsapp_configurado": motor.whatsapp.activo,
            "webhook_configurado": bool(cfg.banco.webhook_token),
            "intervalo_mercado_seg": cfg.mercado.intervalo_seg,
            "bancos": mod_pagos.BANCOS_VE,
            "banco_por_defecto": cfg.pagos.banco_por_defecto,
        },
        "estado": motor.estado,
        "resumen": resumen,
        "diaria": ganancias.serie_diaria(ordenes, ciclos, tz, desde, hasta),
        "mercado": {
            "actual": actual,
            "serie": db.serie_mercado(inicio_mercado, hasta, cubeta_para(hasta - inicio_mercado)),
        },
        "anuncios": anuncios,
        "sesion": sesion_activa,
        "sesiones": db.sesiones(20),
        "contrapartes": db.contrapartes(),
        "ordenes": list(reversed(ordenes_periodo))[:FILAS_TABLA],
        "ciclos": [asdict(c) for c in reversed(ciclos) if en_periodo(c.ts_cierre)][:FILAS_TABLA],
        "banco": {"movimientos": list(reversed(movimientos))[:FILAS_TABLA], "conciliacion": conciliacion},
        "eventos": db.eventos(60),
        "decisiones": db.eventos(40, "decision"),
    }


def guia_manual(cfg: Config, plan) -> dict:
    """Valores exactos para crear tus anuncios a mano en Binance P2P (cuando no hay API de escritura)."""
    a = cfg.anuncios
    monto = lambda propio: propio or cfg.general.capital_usdt  # noqa: E731
    lados = {}
    for lado, propio, metodos in (
        ("compra", a.monto_compra_usdt, a.metodos_compra),
        ("venta", a.monto_venta_usdt, cfg.mercado.metodos_pago),
    ):
        pl = getattr(plan, lado) if plan else None
        lados[lado] = {
            "tipo_binance": "Comprar" if lado == "compra" else "Vender",
            "precio": pl.precio if pl and pl.publicar else None,
            "motivo": pl.motivo if pl else "Aún no hay precios del mercado.",
            "usdt": monto(propio), "min_bs": a.min_bs, "max_bs": a.max_bs,
            "metodos": metodos, "tiempo_pago_min": a.pay_time_limit,
        }
    return {"activo": cfg.mercado.activo, "fiat": cfg.mercado.fiat, "lados": lados}


def datos_anuncios(cfg: Config, motor: Motor) -> dict:
    """Estado del repositor de anuncios + el plan que daría con el precio actual."""
    a = cfg.anuncios
    estado = dict(motor.estado["anuncios"])
    plan = None
    if motor.ultima_cotizacion is not None:
        p = mod_anuncios.planear(motor.ultima_cotizacion, cfg.costos, a)
        plan = {
            "margen_pct": p.margen_pct,
            "competitivo": p.competitivo,
            "lados": {
                lado.lower(): {"publicar": pl.publicar, "precio": pl.precio, "competitivo": pl.competitivo, "motivo": pl.motivo}
                for lado in ("COMPRA", "VENTA") if (pl := p.lado(lado))
            },
        }
    guia = guia_manual(cfg, p) if motor.ultima_cotizacion is not None else guia_manual(cfg, None)
    return {
        "guia_manual": guia,
        "config": {
            "automatico": a.automatico, "modo": a.modo, "lados": a.lados,
            "margen_minimo_pct": a.margen_minimo_pct, "desfase_tick": a.desfase_tick,
            "intervalo_seg": a.intervalo_seg, "ruta": a.ruta, "binance_configurado": cfg.binance.tiene_claves,
        },
        "estado": estado,
        "plan": plan,
    }


def datos_sesion(cfg: Config, db: BaseDatos, cotizacion: dict | None, ordenes: list[dict]) -> dict | None:
    """Resumen de la sesión abierta (capital, rendimiento y qué hacer ahora), o None."""
    abierta = db.sesion_abierta()
    if not abierta:
        return None
    return mod_sesion.resumen(
        abierta, ordenes, cfg.costos, cfg.banco.ruta_por_defecto, cotizacion, cfg.tz,
        cfg.anuncios.margen_minimo_pct, cfg.costos.tick_bs,
    )


def _celda(valor):
    # coma decimal: Excel en español abre el archivo sin desordenar los números
    if isinstance(valor, float):
        return f"{valor:.8f}".rstrip("0").rstrip(".").replace(".", ",")
    return "" if valor is None else valor


def config_simulacion(cfg: Config) -> Config:
    """Copia de la configuración para el modo Simulación: datos de ejemplo y ningún canal real.

    Sin claves de Binance ni tokens de avisos, la simulación no puede tocar tu cuenta ni mandarte mensajes.
    """
    sim = copy.deepcopy(cfg)
    sim.demo = True
    sim.general.base_datos = str(Path(cfg.general.base_datos).with_name("demo.db"))
    sim.binance.api_key = sim.binance.api_secret = ""
    sim.alertas.telegram_token = sim.alertas.telegram_chat_id = ""
    sim.alertas.whatsapp_telefono = sim.alertas.whatsapp_apikey = ""
    sim.banco.webhook_token = ""
    sim.anuncios.automatico, sim.anuncios.modo = True, "simular"
    return sim


def crear_app(cfg: Config, iniciar_tareas: bool = True, con_simulacion: bool = True) -> FastAPI:
    db = BaseDatos(cfg.general.base_datos)
    if cfg.demo:
        from .demo import sembrar

        sembrar(db, cfg)
    motor = Motor(cfg, db)
    if cfg.demo and motor.simulador is not None:
        motor.ultima_cotizacion = motor.simulador.cotizar()  # primer plan visible al abrir el panel

    sim_app = None
    if con_simulacion and not cfg.demo and cfg.panel.simulacion:
        sim_app = crear_app(config_simulacion(cfg), iniciar_tareas=False, con_simulacion=False)

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        # una sub-aplicación montada no ejecuta su propio lifespan: lo arrancamos aquí
        if iniciar_tareas:
            motor.iniciar()
            if sim_app:
                sim_app.state.motor.iniciar()
        try:
            yield
        finally:
            await motor.detener()
            if sim_app:
                await sim_app.state.motor.detener()

    app = FastAPI(title="Arbitraje P2P", version=__version__, lifespan=ciclo_de_vida,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cfg, app.state.db, app.state.motor = cfg, db, motor

    @app.middleware("http")
    async def seguridad(request: Request, call_next):
        if cfg.panel.clave and not request.url.path.startswith(RUTAS_PUBLICAS):
            if not credenciales_validas(request.headers.get("authorization", ""), cfg.panel.usuario, cfg.panel.clave):
                return Response(
                    "Se requiere usuario y clave.", status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Arbitraje P2P", charset="UTF-8"'},
                )
        respuesta = await call_next(request)
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["X-Frame-Options"] = "DENY"
        respuesta.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    @app.get("/", include_in_schema=False)
    def inicio():
        return FileResponse(WEB / "index.html")

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    if sim_app:
        app.mount("/sim", sim_app, name="simulacion")

    @app.get("/api/salud")
    def salud():
        return {"ok": True, "version": __version__}

    @app.get("/api/panel")
    def panel(periodo: str = "30d"):
        if periodo not in PERIODOS:
            raise HTTPException(400, f"Período inválido. Usa uno de: {', '.join(PERIODOS)}")
        return datos_panel(cfg, db, motor, periodo)

    @app.post("/api/ordenes", status_code=201)
    def crear_orden(datos: OrdenManual):
        ts = datos.fecha_ms or ahora_ms()
        numero = f"M{ts}{secrets.randbelow(1000):03d}"
        db.guardar_ordenes([{
            "numero": numero, "origen": "manual", "tipo": datos.tipo,
            "activo": cfg.mercado.activo, "fiat": cfg.mercado.fiat,
            "cantidad": datos.cantidad, "precio": datos.precio, "total_bs": round(datos.cantidad * datos.precio, 2),
            "comision": datos.comision, "costo_banco_bs": datos.costo_banco_bs,
            "ruta_pago": datos.ruta_pago if datos.tipo == "COMPRA" else None,
            "estado": "COMPLETED", "contraparte": datos.contraparte, "creada_ms": ts, "nota": datos.nota,
        }])
        motor.conciliar()
        return db.orden(numero)

    @app.patch("/api/ordenes/{numero}")
    def cambiar_orden(numero: str, cambios: CambioOrden):
        if not db.orden(numero):
            raise HTTPException(404, "Orden no encontrada")
        campos = cambios.model_dump(exclude_unset=True)
        if campos:
            db.actualizar_orden(numero, **campos)
        return db.orden(numero)

    @app.delete("/api/ordenes/{numero}")
    def borrar_orden(numero: str):
        if not db.borrar_orden_manual(numero):
            raise HTTPException(404, "Solo se pueden borrar órdenes cargadas a mano")
        return {"ok": True}

    @app.post("/api/sincronizar")
    async def sincronizar_ahora():
        return await motor.sincronizar()

    @app.post("/api/mercado/actualizar")
    async def actualizar_mercado():
        cotizacion = await motor.actualizar_mercado()
        return {"ok": cotizacion is not None, "error": motor.estado["mercado"]["error"]}

    @app.post("/api/operativa")
    async def cambiar_operativa(datos: Operativa):
        return await (motor.pausar_operativa() if datos.pausar else motor.reanudar_operativa())

    @app.post("/api/anuncios/reposicionar")
    async def reposicionar_anuncios():
        if not cfg.binance.tiene_claves and cfg.anuncios.modo == "real":
            raise HTTPException(400, "El modo real necesita una API key de comerciante con permiso de escritura.")
        return await motor.reposicionar()

    @app.post("/api/banco/probar")
    def probar_texto(datos: TextoBanco):
        aviso = banco.interpretar(datos.texto, cfg.banco)
        return {"ok": aviso is not None, "aviso": asdict(aviso) if aviso else None}

    @app.post("/api/banco/texto")
    async def registrar_texto(datos: TextoBanco):
        return await motor.procesar_aviso_banco(datos.texto, "manual", datos.fecha_ms)

    @app.post("/api/pagos/preparar")
    async def preparar_pago(datos: PrepararPago):
        resultado = await motor.preparar_pago(
            datos.monto_bs, telefono=datos.telefono, cedula=datos.cedula, banco=datos.banco,
            concepto=datos.concepto, orden=datos.orden, contraparte=datos.contraparte,
            texto=datos.texto, confirmar=datos.confirmar,
        )
        # falta de datos -> 422; diferencia de identidad -> 200 con el veredicto para que el panel avise
        if not resultado["ok"] and not resultado.get("requiere_confirmacion"):
            raise HTTPException(422, resultado["error"])
        return resultado

    @app.post("/api/sesion/iniciar", status_code=201)
    def iniciar_sesion(datos: IniciarSesion):
        cotizacion = db.ultima_cotizacion()
        precio = mod_sesion.precio_referencia(cotizacion, datos.moneda_inicial)
        try:
            sesion = db.abrir_sesion({
                "capital_inicial": datos.capital_inicial, "moneda_inicial": datos.moneda_inicial,
                "precio_inicial": precio, "nota": datos.nota,
            })
        except ValueError as e:
            raise HTTPException(409, str(e))
        db.registrar_evento("info", f"Sesión iniciada con {datos.capital_inicial:g} {datos.moneda_inicial}.")
        return sesion

    @app.post("/api/sesion/cerrar")
    def cerrar_sesion(datos: CerrarSesion):
        abierta = db.sesion_abierta()
        if not abierta:
            raise HTTPException(404, "No hay ninguna sesión abierta.")
        resumen = datos_sesion(cfg, db, db.ultima_cotizacion(), db.ordenes())
        capital_final = mod_sesion.valor_cierre(resumen)
        sesion = db.cerrar_sesion(abierta["id"], capital_final, datos.nota_cierre)
        ganancia = resumen.get("ganancia_usdt")
        db.registrar_evento("info", f"Sesión cerrada. Rendimiento: {resumen.get('rendimiento_total_pct') or 0:.2f}% "
                            f"({(ganancia or 0):+.2f} USDT).")
        return {"ok": True, "sesion": sesion, "resumen": resumen}

    def correr_simulacion(datos: Simulacion) -> dict:
        ruta = (datos.ruta or cfg.anuncios.ruta).upper()
        sug_compra, sug_venta = mod_simulador.precios_sugeridos(db.ultima_cotizacion(), cfg.costos, datos.rol)
        p_compra, p_venta = datos.precio_compra or sug_compra, datos.precio_venta or sug_venta
        if not p_compra or not p_venta:
            raise HTTPException(422, "Aún no hay precios de mercado: escribe el precio de compra y de venta a mano.")
        try:
            resultado = mod_simulador.simular(
                datos.capital, datos.moneda, datos.ciclos, p_compra, p_venta, cfg.costos, ruta=ruta, rol=datos.rol,
                reinvertir=datos.reinvertir, ciclos_por_dia=datos.ciclos_por_dia,
                limite_interbancario_bs=cfg.banco.limite_interbancario_bs,
            )
        except ValueError as e:
            raise HTTPException(422, str(e))
        resultado.update(ruta=ruta, rol=datos.rol, reinvertir=datos.reinvertir,
                         precios_de_mercado=not (datos.precio_compra and datos.precio_venta))
        return resultado

    @app.post("/api/simulador")
    def simular(datos: Simulacion):
        """Proyección de ciclos con tu capital. No guarda nada ni toca Binance."""
        return correr_simulacion(datos)

    @app.post("/api/simulacion/ciclos")
    def aplicar_ciclos(datos: Simulacion):
        """Solo en modo Simulación: convierte los ciclos en órdenes simuladas para verlas en el panel."""
        if not cfg.demo:
            raise HTTPException(403, "Esto solo existe en el modo Simulación.")
        r = correr_simulacion(datos)
        ordenes = mod_simulador.ordenes_de_ciclos(
            r["filas"], datos.moneda, r["precio_compra"], r["precio_venta"], cfg.costos, r["ruta"], datos.rol,
            ahora_ms(), datos.ciclos_por_dia,
        )
        db.guardar_ordenes(ordenes)
        db.registrar_evento("decision", f"Simulación: apliqué {datos.ciclos} ciclos con {datos.capital:g} {datos.moneda} "
                            f"({r['ganancia']:+.2f} {datos.moneda}).")
        return {**{k: v for k, v in r.items() if k != "filas"}, "ordenes_creadas": len(ordenes)}

    @app.post("/api/simulacion/reiniciar")
    def reiniciar_simulacion(datos: ReiniciarSimulacion):
        """Solo en modo Simulación: borra todo lo simulado y empieza de cero con el capital que elijas."""
        if not cfg.demo:
            raise HTTPException(403, "Esto solo existe en el modo Simulación.")
        db.vaciar()
        cfg.general.capital_usdt = datos.capital
        if datos.con_ejemplo:
            from .demo import sembrar

            sembrar(db, cfg)
        if motor.simulador is not None:
            from .demo import MercadoSimulado

            motor.simulador = MercadoSimulado(cfg, db)
            motor.ultima_cotizacion = motor.simulador.cotizar()
            db.guardar_cotizacion(motor.ultima_cotizacion.como_fila())
        db.registrar_evento("info", f"Simulación reiniciada con capital {datos.capital:g} USDT.")
        return {"ok": True, "capital": datos.capital, "con_ejemplo": datos.con_ejemplo}

    @app.get("/api/contrapartes")
    def listar_contrapartes():
        return db.contrapartes()

    @app.put("/api/contrapartes")
    def guardar_contraparte(datos: Contraparte):
        db.guardar_contraparte(datos.model_dump())
        return db.contraparte(datos.nick)

    @app.delete("/api/contrapartes/{nick}")
    def borrar_contraparte(nick: str):
        if not db.borrar_contraparte(nick):
            raise HTTPException(404, "Contraparte no encontrada")
        return {"ok": True}

    @app.post("/api/banco/sms")
    async def webhook_sms(request: Request):
        if not cfg.banco.webhook_token:
            raise HTTPException(403, "Webhook desactivado: define banco.webhook_token en config.toml")
        token = request.headers.get("x-token") or request.query_params.get("token") or ""
        if not secrets.compare_digest(token.encode(), cfg.banco.webhook_token.encode()):
            raise HTTPException(401, "Token inválido")
        texto = await extraer_texto(request)
        if not texto:
            raise HTTPException(422, "No encontré el texto del SMS en la petición")
        return await motor.procesar_aviso_banco(texto[:2000], "sms")

    @app.get("/api/exportar/{tabla}.csv")
    def exportar(tabla: str, periodo: str = "todo"):
        if tabla not in ("ordenes", "ciclos") or periodo not in PERIODOS:
            raise HTTPException(404, "Exportación no disponible")
        tz = cfg.tz
        desde, hasta = rango_periodo(periodo, tz)

        def fecha(ts: int) -> str:
            return datetime.fromtimestamp(ts / 1000, tz).strftime("%Y-%m-%d %H:%M:%S")

        ordenes = db.ordenes()
        if tabla == "ordenes":
            columnas = ["fecha", "numero", "tipo", "cantidad", "precio", "total_bs", "comision", "costo_banco_bs",
                        "ruta_pago", "estado", "rol", "contraparte", "origen", "nota"]
            filas = [{**o, "fecha": fecha(o["creada_ms"])} for o in ordenes
                     if (desde is None or o["creada_ms"] >= desde) and o["creada_ms"] <= hasta]
        else:
            columnas = ["fecha_cierre", "apertura", "cierre", "sentido", "usdt", "precio_compra", "precio_venta",
                        "spread_pct", "ganancia_usdt", "ganancia_bs"]
            ciclos, _ = ganancias.emparejar(ordenes, cfg.costos, cfg.banco.ruta_por_defecto)
            filas = [{**asdict(c), "fecha_cierre": fecha(c.ts_cierre)} for c in ciclos
                     if (desde is None or c.ts_cierre >= desde) and c.ts_cierre <= hasta]
        salida = io.StringIO()
        salida.write("﻿")  # BOM: Excel reconoce los acentos
        escritor = csv.writer(salida, delimiter=";")
        escritor.writerow(columnas)
        for fila in filas:
            escritor.writerow([_celda(fila.get(c)) for c in columnas])
        return Response(
            salida.getvalue(), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{tabla}-{periodo}.csv"'},
        )

    return app
