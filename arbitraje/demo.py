"""Datos simulados para probar el panel sin claves: python -m arbitraje --demo

Todo lo que genera este módulo es ficticio (precios, órdenes, apodos y pagos) y
vive en una base de datos aparte (datos/demo.db).
"""
from __future__ import annotations

import itertools
import random
from datetime import timedelta

from .banco import conciliar
from .config import RUTA_BDV, RUTA_INTERBANCARIA, Config
from .db import BaseDatos
from .mercado import Anuncio, Cotizacion, calcular_spreads
from .utilidades import DIA_MS, HORA_MS, MINUTO_MS, ahora_ms, fecha_local, formato_numero, inicio_dia_ms

PASO_MS = 5 * MINUTO_MS
PRECIO_INICIAL = 1000.0
PRECIO_FINAL = 1007.0  # cerca del USDT en Binance P2P a mediados de octubre de 2026
DEVALUACION_DIARIA = 0.0015
APODOS = ["Yor***", "Cam***", "Mar***", "Kel***", "Tas***", "Ant***", "Div***", "Lui***", "Cri***", "Ped***"]


def _precios(rnd: random.Random, medio: float) -> tuple[float, float]:
    """(precio para comprar USDT, precio para venderlos) alrededor de un precio medio."""
    brecha = max(0.3, rnd.gauss(1.45, 0.35))  # % entre el mejor vendedor y el mejor comprador
    return round(medio * (1 + brecha / 200), 2), round(medio * (1 - brecha / 200), 2)


class MercadoSimulado:
    """Continúa la caminata aleatoria del precio mientras el panel demo está abierto."""

    def __init__(self, cfg: Config, db: BaseDatos, semilla: int | None = None):
        self.cfg = cfg
        self.rnd = random.Random(semilla)
        ultima = db.ultima_cotizacion()
        if ultima and ultima["precio_compra"] and ultima["precio_venta"]:
            self.medio = (ultima["precio_compra"] + ultima["precio_venta"]) / 2
        else:
            self.medio = PRECIO_INICIAL

    def cotizar(self) -> Cotizacion:
        self.medio *= 1 + self.rnd.gauss(DEVALUACION_DIARIA / 1440, 0.00015)
        compra, venta = _precios(self.rnd, self.medio)
        return Cotizacion(
            ts_ms=ahora_ms(),
            precio_compra=compra,
            precio_venta=venta,
            **calcular_spreads(compra, venta, self.cfg.costos, self.cfg.banco.ruta_por_defecto),
            anuncios_compra=self.rnd.randint(8, 20),
            anuncios_venta=self.rnd.randint(8, 20),
            mejores_compra=self._anuncios(compra, +1),
            mejores_venta=self._anuncios(venta, -1),
        )

    def _anuncios(self, mejor: float, direccion: int) -> list[Anuncio]:
        anuncios, precio = [], mejor
        for i in range(5):
            anuncios.append(
                Anuncio(
                    numero=f"DEMO{self.rnd.randrange(10**10):010d}",
                    precio=round(precio, 2),
                    disponible=round(self.rnd.uniform(150, 4000), 2),
                    min_bs=5000.0,
                    max_bs=float(self.rnd.choice([150000, 300000, 600000, 1500000])),
                    metodos=["BancoDeVenezuela", "PagoMovil"] if i % 2 == 0 else ["PagoMovil"],
                    anunciante=f"Anunciante demo {i + 1}",
                    ordenes_mes=self.rnd.randint(80, 2500),
                    tasa_completado=round(self.rnd.uniform(0.92, 1.0), 4),
                    comerciante=i % 2 == 0,
                )
            )
            precio += direccion * self.rnd.uniform(0.2, 1.5)
        return anuncios


def sembrar(db: BaseDatos, cfg: Config, dias: int = 45, semilla: int = 7, ahora: int | None = None) -> None:
    """Llena la base de datos demo con `dias` días simulados (solo la primera vez)."""
    if db.leer_estado("demo_sembrado"):
        return
    rnd = random.Random(semilla)
    tz = cfg.tz
    fin = ahora if ahora is not None else ahora_ms()
    inicio = fin - dias * DIA_MS

    # 1) Mercado cada 5 minutos: caminata aleatoria con devaluación lenta del bolívar,
    #    reescalada para terminar cerca del precio real reciente
    pasos = (fin - inicio) // PASO_MS + 1
    camino = [1.0]
    for _ in range(pasos - 1):
        camino.append(camino[-1] * (1 + rnd.gauss(DEVALUACION_DIARIA / 288, 0.0003)))
    escala = PRECIO_FINAL / camino[-1]
    precios: list[tuple[float, float]] = []
    filas = []
    for i, relativo in enumerate(camino):
        compra, venta = _precios(rnd, relativo * escala)
        precios.append((compra, venta))
        filas.append(
            {
                "ts_ms": inicio + i * PASO_MS,
                "precio_compra": compra,
                "precio_venta": venta,
                **calcular_spreads(compra, venta, cfg.costos, cfg.banco.ruta_por_defecto),
                "anuncios_compra": rnd.randint(8, 20),
                "anuncios_venta": rnd.randint(8, 20),
            }
        )
    db.guardar_cotizaciones(filas)

    def mercado_en(ts: int) -> tuple[float, float]:
        return precios[min(max((ts - inicio) // PASO_MS, 0), len(precios) - 1)]

    # 2) Ciclos de arbitraje: casi siempre como anunciante, a veces tomando un anuncio
    secuencia = itertools.count(1)
    tick = cfg.costos.tick_bs
    comision = cfg.costos.comision_anunciante_pct / 100

    def orden(tipo: str, usdt: float, ts: int, anunciante: bool = True, estado: str = "COMPLETED") -> dict:
        compra, venta = mercado_en(ts)
        if tipo == "COMPRA":
            precio = venta + tick if anunciante else compra
        else:
            precio = compra - tick if anunciante else venta
        ruta = (RUTA_BDV if rnd.random() < 0.55 else RUTA_INTERBANCARIA) if tipo == "COMPRA" else None
        return {
            "numero": f"2266{next(secuencia):016d}",
            "origen": "demo",
            "tipo": tipo,
            "cantidad": usdt,
            "precio": round(precio, 2),
            "total_bs": round(usdt * precio, 2),
            "comision": round(usdt * comision, 4) if anunciante else 0.0,
            "ruta_pago": ruta,
            "metodo_pago": {RUTA_BDV: "BancoDeVenezuela", RUTA_INTERBANCARIA: "PagoMovil"}.get(ruta),
            "estado": estado,
            "rol": "MAKER" if anunciante else "TAKER",
            "contraparte": rnd.choice(APODOS),
            "creada_ms": ts,
        }

    ordenes: list[dict] = []
    dia, ultimo_dia = fecha_local(inicio, tz) + timedelta(days=1), fecha_local(fin, tz)
    while dia <= ultimo_dia:
        base = inicio_dia_ms(dia, tz)
        for _ in range(rnd.randint(1, 3) if dia.weekday() == 6 else rnd.randint(3, 7)):
            t1 = base + rnd.randint(8 * 60, 20 * 60) * MINUTO_MS + rnd.randint(0, 59_000)
            t2 = t1 + rnd.randint(6, 150) * MINUTO_MS
            if t2 > fin - 10 * MINUTO_MS:
                continue
            compra_anunciante, venta_anunciante = rnd.random() < 0.8, rnd.random() < 0.8
            usdt_venta = round(rnd.uniform(30, 200), 2)
            # se compra un poco más para cubrir las comisiones y no ir descapitalizándose
            usdt_compra = round(usdt_venta * (1 + comision * venta_anunciante) / (1 - comision * compra_anunciante), 2)
            compra_primero = rnd.random() < 0.65
            ordenes.append(orden("COMPRA", usdt_compra, t1 if compra_primero else t2, compra_anunciante))
            ordenes.append(orden("VENTA", usdt_venta, t2 if compra_primero else t1, venta_anunciante))
        if rnd.random() < 0.3:
            ordenes.append(orden("COMPRA", round(rnd.uniform(30, 120), 2),
                                 base + rnd.randint(9 * 60, 19 * 60) * MINUTO_MS, estado="CANCELLED"))
        dia += timedelta(days=1)
    # una venta en curso, con contraparte conocida: el comprador ya pagó (muestra ✅ al verificar)
    venta_curso = orden("VENTA", 64.5, fin - 4 * MINUTO_MS, estado="BUYER_PAYED")
    venta_curso["contraparte"] = "Kelvin***"
    ordenes.append(venta_curso)
    # una compra en curso: te toca pagarle al vendedor (aparece en "preparar pago")
    compra_curso = orden("COMPRA", 72.0, fin - 6 * MINUTO_MS, estado="TRADING")
    compra_curso["contraparte"] = "Camila***"
    ordenes.append(compra_curso)
    db.guardar_ordenes(ordenes)

    # Contrapartes con identidad verificada (lo que verías en el perfil de Binance)
    db.guardar_contraparte({"nick": "Kelvin***", "nombre": "Kelvin Rodríguez", "cedula": "V18456789",
                            "telefonos": "04141234567", "confianza": "verificada"})
    db.guardar_contraparte({"nick": "Camila***", "nombre": "Camila Fernández", "cedula": "V21987654",
                            "telefonos": "04241112233", "confianza": "verificada"})

    # 3) Avisos de pago del banco de los últimos 3 días (SMS simulados)
    def telefono() -> str:
        return f"0{rnd.choice(['412', '414', '424', '416'])}-555{rnd.randint(0, 9999):04d}"

    for o in ordenes:
        if o["creada_ms"] < fin - 3 * DIA_MS or o["estado"].startswith("CANCEL"):
            continue
        if o is venta_curso:
            continue  # su SMS se crea aparte, con el teléfono que coincide con la contraparte
        monto = formato_numero(o["total_bs"])
        referencia = f"{rnd.randint(10**11, 10**12 - 1)}"
        if o["tipo"] == "VENTA":
            ts, texto = (o["creada_ms"] + rnd.randint(1, 3) * MINUTO_MS,
                         f"DEMO BDV: Recibiste un PagomovilBDV por Bs. {monto} del {telefono()} Ref: {referencia}")
            tipo = "ENTRADA"
        else:
            ts, texto = (o["creada_ms"] + rnd.randint(1, 4) * MINUTO_MS,
                         f"DEMO BDV: Pagaste Bs. {monto} al {telefono()} Ref: {referencia}")
            tipo = "SALIDA"
        db.guardar_movimiento({"ts_ms": min(ts, fin), "tipo": tipo, "monto_bs": o["total_bs"],
                               "referencia": referencia, "texto": texto, "origen": "demo"})
    # el pago de la venta en curso, con el teléfono que SÍ coincide con la contraparte guardada
    db.guardar_movimiento({
        "ts_ms": venta_curso["creada_ms"] + 2 * MINUTO_MS, "tipo": "ENTRADA", "monto_bs": venta_curso["total_bs"],
        "referencia": f"{rnd.randint(10**11, 10**12 - 1)}", "contraparte": "0414-1234567", "origen": "demo",
        "texto": f"DEMO BDV: Recibiste un PagomovilBDV por Bs. {formato_numero(venta_curso['total_bs'])} "
                 f"del 0414-1234567 Ref: pago verificado"})
    # un pago que no corresponde a ninguna orden (el panel lo marca como sospechoso)
    db.guardar_movimiento({"ts_ms": fin - DIA_MS - 3 * HORA_MS, "tipo": "ENTRADA", "monto_bs": 48750.0,
                           "referencia": "998877665544", "contraparte": "0414-5550199", "origen": "demo",
                           "texto": "DEMO BDV: Recibiste un PagomovilBDV por Bs. 48.750,00 del 0414-5550199 Ref: 998877665544"})
    for movimiento_id, numero in conciliar(db.movimientos(), db.ordenes(), cfg.banco.tolerancia_bs, cfg.banco.ventana_min):
        db.asignar_movimiento(movimiento_id, numero)

    # 4) Algunas alertas de oportunidad de los últimos 2 días
    ultima = 0
    for f in filas:
        if f["ts_ms"] < fin - 2 * DIA_MS or f["neto_anunciante_pct"] < cfg.alertas.spread_minimo_pct + 0.3:
            continue
        if f["ts_ms"] - ultima >= 6 * HORA_MS:
            ultima = f["ts_ms"]
            db.registrar_evento(
                "oportunidad",
                f"Spread atractivo: {formato_numero(f['neto_anunciante_pct'])} % neto como anunciante. "
                f"Comprar USDT: {formato_numero(f['precio_compra'])} · Vender USDT: {formato_numero(f['precio_venta'])}.",
                ts_ms=f["ts_ms"],
            )
    # 5) Sesiones de operación: una de ayer ya cerrada (para el historial) y la de hoy abierta
    precio_ref = precios[-1][0]
    cap_ves = round(cfg.general.capital_usdt * precio_ref, 2)
    hoy0 = inicio_dia_ms(fecha_local(fin, tz), tz)
    ayer = db.abrir_sesion({"inicio_ms": hoy0 - DIA_MS, "capital_inicial": cap_ves, "moneda_inicial": "VES",
                            "precio_inicial": precio_ref, "nota": "Jornada de ayer (demo)"})
    db.cerrar_sesion(ayer["id"], round(cap_ves * 1.018, 2), "Cerrada en USDT, +1,8 %")
    db.abrir_sesion({"inicio_ms": hoy0, "capital_inicial": cap_ves, "moneda_inicial": "VES",
                     "precio_inicial": precio_ref, "nota": "Jornada de hoy (demo)"})

    db.registrar_evento("info", "Modo demo: datos simulados de 45 días. Nada de esto es real.", ts_ms=fin)
    db.escribir_estado("demo_sembrado", 1)
