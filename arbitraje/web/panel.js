/* Lógica del panel: pide /api/panel y dibuja indicadores, gráficos y tablas. */
(function () {
  "use strict";

  const G = window.Graficos;
  // la simulación vive en /sim: mismas pantallas, otra base de datos y ningún canal real
  const BASE = location.pathname.startsWith("/sim") ? "/sim" : "";
  const $ = (selector) => document.querySelector(selector);
  const NOMBRES_METODO = { BancoDeVenezuela: "Banco de Venezuela", PagoMovil: "Pago Móvil" };
  const ESTADOS = {
    COMPLETED: "Completada", CANCELLED: "Cancelada", CANCELLED_BY_SYSTEM: "Cancelada (sistema)",
    PENDING: "Pendiente", TRADING: "En curso", BUYER_PAYED: "Pagada, por liberar",
    DISTRIBUTING: "Liberando", IN_APPEAL: "En apelación",
  };
  const NIVELES = { info: "Info", oportunidad: "Oportunidad", pago: "Pago", decision: "Decisión", error: "Error" };
  const VEREDICTO = {
    coincide: { clase: "estado-ok", texto: "Titular coincide" },
    difiere: { clase: "estado-alerta", texto: "⚠️ Posible tercero" },
    sin_datos: { clase: "estado-neutro", texto: "Sin verificar" },
  };
  const PERIODOS = ["hoy", "7d", "30d", "90d", "todo"];
  const ESTADOS_POR_PAGAR = new Set(["PENDING", "TRADING"]);  // compras tuyas aún por pagar
  const DIA_MS = 86400000;

  let periodo = PERIODOS.includes(leer("periodo")) ? leer("periodo") : "30d";
  let zona = "America/Caracas";
  let ocupado = false;
  const dibujos = {};
  const formatosNumero = {};
  const formatosFecha = {};

  // ---- utilidades ------------------------------------------------------------

  function leer(clave) { try { return localStorage.getItem(clave); } catch (e) { return null; } }
  function guardar(clave, valor) { try { localStorage.setItem(clave, valor); } catch (e) { /* sin almacenamiento */ } }

  function num(v, dec = 2) {
    if (v == null || !isFinite(v)) return "—";
    const f = formatosNumero[dec] || (formatosNumero[dec] = new Intl.NumberFormat("es-VE", { minimumFractionDigits: dec, maximumFractionDigits: dec }));
    return f.format(v);
  }
  function conSigno(v, dec = 2) {
    if (v == null || !isFinite(v)) return "—";
    const abs = num(Math.abs(v), dec);
    if (abs === num(0, dec)) return abs;
    return (v > 0 ? "+" : "−") + abs;
  }
  function compacto(v) {
    const a = Math.abs(v);
    if (a >= 1e6) return num(v / 1e6, a >= 1e7 ? 0 : 1) + " M";
    if (a >= 1e4) return num(v / 1e3, 0) + " k";
    return num(v, 0);
  }
  function fecha(ts, opciones) {
    const clave = zona + JSON.stringify(opciones);
    const f = formatosFecha[clave] || (formatosFecha[clave] = new Intl.DateTimeFormat("es-VE", { timeZone: zona, ...opciones }));
    return f.format(new Date(ts));
  }
  const fechaHora = (ts) => fecha(ts, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const soloHora = (ts) => fecha(ts, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const diaCorto = (ts) => fecha(ts, { day: "numeric", month: "short" });
  function partesISO(iso) { const [a, m, d] = iso.split("-").map(Number); return Date.UTC(a, m - 1, d, 12); }
  function diaDeISO(iso) {
    return new Intl.DateTimeFormat("es-VE", { timeZone: "UTC", day: "numeric", month: "short" }).format(new Date(partesISO(iso)));
  }
  function hace(ts) {
    if (!ts) return "sin datos";
    const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
    if (s < 60) return `hace ${s} s`;
    const m = Math.round(s / 60);
    if (m < 60) return `hace ${m} min`;
    const h = Math.round(m / 60);
    return h < 48 ? `hace ${h} h` : `hace ${Math.round(h / 24)} días`;
  }
  function el(tag, props, ...hijos) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    for (const h of hijos) if (h != null) n.append(h);
    return n;
  }
  function poner(id, texto) { const n = document.getElementById(id); if (n) n.textContent = texto; }
  const rutaTexto = (ruta) => (ruta === "BDV" ? "BDV → BDV" : "interbancario");

  function badgeVeredicto(v) {
    const m = VEREDICTO[(v && v.nivel) || "sin_datos"] || VEREDICTO.sin_datos;
    return el("span", { class: m.clase, title: (v && v.resumen) || "", text: m.texto });
  }
  const ejeNumero = (v, paso) => num(v, paso < 0.1 ? 2 : paso < 1 ? 1 : 0);

  async function api(ruta, opciones) {
    const r = await fetch(BASE + ruta, { cache: "no-store", headers: { "Content-Type": "application/json" }, ...opciones });
    let cuerpo = null;
    try { cuerpo = await r.json(); } catch (e) { /* sin cuerpo JSON */ }
    if (!r.ok) {
      const detalle = cuerpo && cuerpo.detail;
      throw new Error(typeof detalle === "string" ? detalle : `el servidor respondió ${r.status}`);
    }
    return cuerpo;
  }

  // ---- carga ----------------------------------------------------------------

  async function cargar(conAtenuado) {
    if (ocupado) return;
    ocupado = true;
    if (conAtenuado) document.body.classList.add("cargando");
    try {
      render(await api(`/api/panel?periodo=${encodeURIComponent(periodo)}`));
    } catch (e) {
      avisos([`No pude actualizar el panel: ${e.message}. Reintento en unos segundos.`]);
    } finally {
      ocupado = false;
      document.body.classList.remove("cargando");
    }
  }

  function render(d) {
    zona = d.config.zona_horaria || zona;
    renderCabecera(d);
    renderOperativa(d);
    renderKpis(d);
    renderSesion(d);
    renderDecisiones(d);
    renderMercado(d);
    renderOperacion(d);
    renderAnuncios(d);
    renderGraficos(d);
    renderOrdenes(d);
    renderCiclos(d);
    renderBanco(d);
    renderContrapartes(d);
    renderEventos(d);
  }

  // ---- cabecera y avisos -----------------------------------------------------

  function chip(selector, estado, texto, titulo) {
    const n = $(selector);
    n.dataset.estado = estado;
    n.querySelector(".texto").textContent = texto;
    n.title = titulo || "";
  }

  function avisos(lista) {
    const n = $("#aviso-global");
    n.textContent = "";
    for (const t of lista) n.append(el("p", { text: t }));
    n.hidden = !lista.length;
  }

  function renderCabecera(d) {
    const c = d.config;
    const em = d.estado.mercado;
    const es = d.estado.sincronizacion;
    poner("par", `${c.activo}/${c.fiat}`);
    poner("metodos", c.metodos_pago.map((m) => NOMBRES_METODO[m] || m).join(" + "));
    $("#insignia-demo").hidden = !c.demo;
    $("#selector-modo").hidden = !c.simulacion_disponible;
    $("#modo-real").setAttribute("aria-current", BASE ? "false" : "page");
    $("#modo-sim").setAttribute("aria-current", BASE ? "page" : "false");
    $("#tarjeta-simulador").hidden = !c.demo;
    document.title = c.demo ? "Simulación · Arbitraje P2P" : "Arbitraje P2P";
    if (em.ok === true) chip("#estado-mercado", em.aviso ? "aviso" : "ok", `Mercado ${hace(em.ok_ms)}`, em.aviso || "");
    else if (em.ok === false) chip("#estado-mercado", "error", "Mercado: error", em.error);
    else chip("#estado-mercado", "apagado", "Mercado: esperando…", "");
    if (!c.binance_configurado) {
      chip("#estado-sync", "apagado", c.demo ? "Binance: modo demo" : "Binance: sin API key",
        "Agrega una API key de solo lectura para importar tus órdenes automáticamente");
    } else if (es.ok === true) chip("#estado-sync", "ok", `Binance ${hace(es.ok_ms)}`, "");
    else if (es.ok === false) chip("#estado-sync", "error", "Binance: error", es.error);
    else chip("#estado-sync", "apagado", "Binance: esperando…", "");
    $("#btn-sync").hidden = !c.binance_configurado;
    $("#exp-ordenes").href = `${BASE}/api/exportar/ordenes.csv?periodo=${periodo}`;
    $("#exp-ciclos").href = `${BASE}/api/exportar/ciclos.csv?periodo=${periodo}`;
    avisos([em.error, em.aviso, es.error].filter(Boolean));
  }

  // ---- pausar / reanudar operatividad ----------------------------------------

  function operativaPausada(d) {
    return !!(d.estado && d.estado.operativa && d.estado.operativa.pausada);
  }

  function renderOperativa(d) {
    const pausada = operativaPausada(d);
    const btn = $("#btn-operativa");
    btn.dataset.pausada = String(pausada);
    btn.textContent = pausada ? "▶ Reanudar operatividad" : "⏸ Pausar operatividad";
    $("#banner-pausa").hidden = !pausada;
  }

  async function cambiarOperativa(pausar) {
    const btn = $("#btn-operativa");
    btn.disabled = true;
    try {
      await api("/api/operativa", { method: "POST", body: JSON.stringify({ pausar }) });
      await cargar(false);
    } catch (err) {
      avisos([`No pude ${pausar ? "pausar" : "reanudar"} la operatividad: ${err.message}`]);
    } finally {
      btn.disabled = false;
    }
  }

  // ---- indicadores -----------------------------------------------------------

  function renderKpis(d) {
    const r = d.resumen;
    poner("k-ganancia", num(r.ganancia_usdt));
    poner("k-ganancia-bs", `≈ Bs ${num(r.ganancia_bs, 0)} en ${r.dias} ${r.dias === 1 ? "día" : "días"}`);
    const delta = $("#k-delta");
    delta.textContent = "";
    if (r.ganancia_anterior_usdt != null) {
      const diferencia = r.ganancia_usdt - r.ganancia_anterior_usdt;
      delta.append(
        el("span", { class: "flecha " + (diferencia >= 0 ? "sube" : "baja"), "aria-hidden": "true", text: diferencia >= 0 ? "▲" : "▼" }),
        `${conSigno(diferencia)} USDT frente al período anterior`,
      );
    }
    poner("k-hoy", conSigno(r.ganancia_hoy_usdt));
    poner("k-hoy-det", `${r.ciclos_hoy} ${r.ciclos_hoy === 1 ? "ciclo" : "ciclos"} · Bs ${conSigno(r.ganancia_hoy_bs, 0)}`);
    poner("k-roi", num(r.roi_pct));
    poner("k-roi-det", `≈ ${num(r.roi_diario_pct)} % por día · capital ${num(r.capital_usdt, 0)} USDT`);
    poner("k-spread", num(r.spread_promedio_pct));
    poner("k-volumen", num(r.volumen_usdt, 0));
    poner("k-volumen-det", `Bs ${compacto(r.volumen_bs)} en órdenes completadas`);
    poner("k-ciclos", num(r.ciclos, 0));
    poner("k-ciclos-det", r.ciclos ? `≈ ${num(r.ganancia_usdt / r.ciclos)} USDT por ciclo` : "aún sin ciclos cerrados");
    poner("k-ordenes", num(r.ordenes.completadas, 0));
    poner("k-ordenes-det", `${r.ordenes.compras} compras · ${r.ordenes.ventas} ventas · ${r.ordenes.canceladas} canceladas`);
  }

  // ---- sesión de operación ---------------------------------------------------

  function metrica(etiqueta, valor, sub, clase) {
    return el("div", { class: "sesion-metrica" },
      el("span", { class: "etiqueta", text: etiqueta }),
      el("span", { class: "valor" + (clase ? " " + clase : ""), text: valor }),
      sub ? el("span", { class: "sub", text: sub }) : null);
  }

  function formularioSesion() {
    const cont = $("#sesion-cuerpo");
    cont.textContent = "";
    const f = el("form", { class: "sesion-inicio", id: "f-sesion" });
    const capital = el("input", { name: "capital", type: "number", step: "0.01", min: "0.01", required: "", placeholder: "1000" });
    const moneda = el("select", { name: "moneda" }, el("option", { value: "VES", text: "Bolívares (Bs)" }), el("option", { value: "USDT", text: "USDT" }));
    const nota = el("input", { name: "nota", maxlength: "200", placeholder: "opcional" });
    f.append(
      el("label", {}, "¿Con cuánto empiezas?", capital),
      el("label", {}, "¿En qué moneda?", moneda),
      el("label", {}, "Nota", nota),
      el("button", { type: "submit", class: "boton", text: "Iniciar operación" }));
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await api("/api/sesion/iniciar", { method: "POST", body: JSON.stringify({
          capital_inicial: Number(capital.value), moneda_inicial: moneda.value, nota: nota.value || null }) });
        cargar(false);
      } catch (err) {
        avisos([`No pude iniciar la sesión: ${err.message}`]);
      }
    });
    cont.append(f);
  }

  async function cerrarSesion() {
    if (!window.confirm("¿Cerrar la sesión y guardar el rendimiento?")) return;
    try {
      const r = await api("/api/sesion/cerrar", { method: "POST", body: JSON.stringify({}) });
      const rend = r.resumen && r.resumen.rendimiento_total_pct;
      avisos([`Sesión cerrada. Rendimiento: ${conSigno(rend)} %.`]);
      cargar(false);
    } catch (err) {
      avisos([`No pude cerrar la sesión: ${err.message}`]);
    }
  }

  async function descartarSesion() {
    if (!window.confirm("¿Descartar la sesión? Se elimina sin guardar en el historial. Las órdenes no se borran.")) return;
    try {
      await api("/api/sesion/descartar", { method: "POST" });
      avisos(["Sesión descartada."]);
      cargar(false);
    } catch (err) {
      avisos([`No pude descartar la sesión: ${err.message}`]);
    }
  }

  function panelSesion(s, moneda, precioActual) {
    const cont = $("#sesion-cuerpo");
    cont.textContent = "";
    const esVes = s.moneda_inicial === "VES";
    const capTxt = esVes ? `Bs ${num(s.capital_inicial, 0)}` : `${num(s.capital_inicial)} USDT`;
    const capSub = s.capital_inicial_usdt != null && esVes ? `≈ ${num(s.capital_inicial_usdt)} USDT` : "";
    const equity = esVes ? (s.equity_ves != null ? `Bs ${num(s.equity_ves, 0)}` : "—") : (s.equity_usdt != null ? `${num(s.equity_usdt)} USDT` : "—");
    const equitySub = s.equity_usdt != null ? (esVes ? `≈ ${num(s.equity_usdt)} USDT` : (s.equity_ves != null ? `≈ Bs ${num(s.equity_ves, 0)}` : "")) : "";
    const rend = s.rendimiento_total_pct;
    const signo = rend == null ? "" : rend >= 0 ? "pos" : "neg";
    const gan = s.ganancia_usdt;
    cont.append(el("div", { class: "sesion-metricas" },
      metrica("Capital inicial", capTxt, capSub),
      metrica("Equity actual", equity, equitySub),
      metrica("Rendimiento", rend == null ? "—" : `${conSigno(rend)} %`, `${s.ciclos} ciclos · ${s.dias} ${s.dias === 1 ? "día" : "días"}`, signo),
      metrica("Ganancia", gan == null ? "—" : `${conSigno(gan)} USDT`, s.ganancia_ves != null ? `Bs ${conSigno(s.ganancia_ves, 0)}` : "", signo)));

    const rec = s.recomendacion;
    if (rec) {
      const caja = el("div", { class: "recomendacion", "data-accion": rec.accion });
      caja.append(el("span", { class: "accion", text: rec.accion }));
      const t = el("div", { class: "texto" }, el("strong", { text: "Qué hacer ahora: " }), rec.razon);
      if (rec.precio && rec.accion !== "ESPERAR") t.append(el("div", { class: "sub", text: `Precio objetivo: ${num(rec.precio)} Bs` }));
      caja.append(t);
      cont.append(caja);
    }

    // composición: cuánto está resguardado en USDT vs expuesto en Bs
    if (s.expuesto_ves_pct != null) {
      const usdtPct = Math.max(0, Math.min(100, 100 - s.expuesto_ves_pct));
      const barra = el("div", { class: "barra-composicion", title: `${num(usdtPct, 0)} % en USDT` },
        el("div", { class: "parte-usdt", style: `width:${usdtPct}%` }));
      const consejo = el("div", { class: "consejo-cierre" + (s.resguardado ? " ok" : "") },
        el("strong", { text: s.resguardado ? "✅ " : "⚠️ Cierre del día: " }), s.consejo_cierre || "");
      cont.append(el("div", {}, el("span", { class: "etiqueta", text: "Composición actual (USDT resguardado vs Bs expuesto)" }), barra), consejo);
    }
  }

  function renderSesion(d) {
    const s = d.sesion;
    const acciones = $("#sesion-acciones");
    acciones.textContent = "";
    if (s) {
      poner("sesion-sub", `Operando desde ${fechaHora(s.inicio_ms)} · cierre típico: diario.`);
      acciones.append(
        el("button", { type: "button", class: "boton secundario", text: "Descartar", title: "Eliminarla sin guardar en el historial", onclick: descartarSesion }),
        el("button", { type: "button", class: "boton secundario", text: "Cerrar sesión", onclick: cerrarSesion }));
      panelSesion(s, s.moneda_inicial, (d.mercado.actual || {}).precio_venta);
    } else {
      poner("sesion-sub", "Dile al sistema con cuánto empiezas y en qué moneda; te recomienda qué hacer y al cerrar ves tu rendimiento.");
      formularioSesion();
    }
    // historial de sesiones cerradas
    const cerradas = (d.sesiones || []).filter((x) => x.estado === "cerrada");
    const hist = $("#sesion-historial");
    hist.hidden = cerradas.length === 0;
    if (cerradas.length) {
      G.tabla($("#sesion-historial-tabla"), [
        { titulo: "Inicio", valor: (x) => fechaHora(x.inicio_ms) },
        { titulo: "Cierre", valor: (x) => (x.cierre_ms ? fechaHora(x.cierre_ms) : "—") },
        { titulo: "Moneda", valor: (x) => x.moneda_inicial },
        { titulo: "Capital inicial", num: true, valor: (x) => num(x.capital_inicial, x.moneda_inicial === "VES" ? 0 : 2) },
        { titulo: "Capital final", num: true, valor: (x) => (x.capital_final != null ? num(x.capital_final, x.moneda_inicial === "VES" ? 0 : 2) : "—") },
        { titulo: "Rendimiento", num: true, valor: (x) => (x.capital_final != null && x.capital_inicial ? conSigno((x.capital_final / x.capital_inicial - 1) * 100) + " %" : "—") },
      ], cerradas);
    }
  }

  // ---- mercado ---------------------------------------------------------------

  function renderMercado(d) {
    const a = d.mercado.actual;
    if (!a) {
      poner("m-hora", "esperando la primera consulta…");
      return;
    }
    poner("m-hora", `${hace(a.ts_ms)} · ${a.anuncios_compra} vendedores y ${a.anuncios_venta} compradores válidos`);
    poner("m-compra", a.precio_compra ? `${num(a.precio_compra)} Bs` : "—");
    poner("m-venta", a.precio_venta ? `${num(a.precio_venta)} Bs` : "—");
    poner("m-brecha", a.spread_pct != null ? `${num(a.spread_pct)} %` : "—");
    const umbral = d.config.spread_minimo_pct;
    neto("m-neto-bdv", a.netos_por_ruta.bdv, (v) => v >= umbral, "sobre tu umbral");
    neto("m-neto-inter", a.netos_por_ruta.interbancario, (v) => v >= umbral, "sobre tu umbral");
    neto("m-neto-tomador", a.neto_tomador_pct, (v) => v > 0, "arbitraje inmediato");
    poner("m-sugerencia", a.sugerido_compra && a.sugerido_venta
      ? `Para quedar de primero: tu anuncio de compra a ${num(a.sugerido_compra)} Bs y el de venta a ${num(a.sugerido_venta)} Bs.`
      : "");
    tablaAnuncios("#m-anuncios-compra", a.detalle && a.detalle.compra);
    tablaAnuncios("#m-anuncios-venta", a.detalle && a.detalle.venta);
  }

  function neto(id, valor, destacar, texto) {
    const n = document.getElementById(id);
    n.textContent = "";
    n.append(valor == null ? "—" : `${conSigno(valor)} %`);
    if (valor != null && destacar(valor)) n.append(el("span", { class: "marca-umbral estado-ok", text: texto }));
  }

  function tablaAnuncios(selector, anuncios) {
    G.tabla($(selector), [
      { titulo: "Precio", num: true, valor: (a) => num(a.precio) },
      { titulo: "USDT", num: true, valor: (a) => num(a.disponible, 0) },
      { titulo: "Límites Bs", num: true, valor: (a) => `${compacto(a.min_bs)}–${compacto(a.max_bs)}` },
      { titulo: "Anunciante", valor: (a) => a.anunciante },
      { titulo: "Órdenes/mes", num: true, valor: (a) => num(a.ordenes_mes, 0) },
      { titulo: "Completadas", num: true, valor: (a) => `${num(a.tasa_completado * 100, 1)} %` },
    ], anuncios || [], "Sin detalle de anuncios todavía.");
  }

  // ---- tu operación ----------------------------------------------------------

  function renderOperacion(d) {
    const r = d.resumen;
    const a = d.mercado.actual;
    poner("o-capital", `capital ${num(r.capital_usdt, 0)} USDT`);
    const inter = r.pagado_hoy.interbancario;
    const relleno = $("#o-inter-relleno");
    const pista = $("#o-inter-pista");
    if (inter.limite_bs > 0) {
      const pct = (inter.usado_bs / inter.limite_bs) * 100;
      const queda = Math.max(0, inter.limite_bs - inter.usado_bs);
      poner("o-inter-valor", `${num(pct, 0)} %`);
      relleno.style.width = `${Math.min(100, pct)}%`;
      relleno.dataset.nivel = pct >= 90 ? "critico" : pct >= 70 ? "aviso" : "";
      pista.setAttribute("aria-valuenow", String(Math.round(Math.min(100, pct))));
      poner("o-inter-det", `Bs ${num(inter.usado_bs, 0)} de ${num(inter.limite_bs, 0)} · quedan Bs ${num(queda, 0)}` +
        (a && a.precio_compra ? ` (≈ ${num(queda / a.precio_compra, 0)} USDT)` : ""));
    } else {
      poner("o-inter-valor", "sin límite");
      relleno.style.width = "0";
      pista.setAttribute("aria-valuenow", "0");
      poner("o-inter-det", `Bs ${num(inter.usado_bs, 0)} pagados hoy`);
    }
    const bdv = r.pagado_hoy.bdv;
    poner("o-bdv", `Bs ${num(bdv.usado_bs, 0)} · ${bdv.limite_bs > 0 ? `límite ${num(bdv.limite_bs, 0)}` : "sin límite"}`);
    const p = r.posicion;
    if (Math.abs(p.usdt) < 0.005) poner("o-posicion", "sin posición abierta");
    else if (p.usdt > 0) poner("o-posicion", `${num(p.usdt)} USDT comprados sin vender, a ${num(p.precio_promedio)}`);
    else poner("o-posicion", `${num(-p.usdt)} USDT vendidos sin recomprar, a ${num(p.precio_promedio)}`);
    poner("o-no-realizado", p.no_realizado_usdt == null ? "—" : `${conSigno(p.no_realizado_usdt)} USDT (Bs ${conSigno(p.no_realizado_bs, 0)})`);
    poner("o-dias", r.dias_operados ? `${r.dias_positivos} de ${r.dias_operados}` : "—");
    poner("o-mejor", r.mejor_dia ? `${diaDeISO(r.mejor_dia.fecha)} · ${conSigno(r.mejor_dia.ganancia_usdt)} USDT` : "—");
    poner("o-rotacion", `${num(r.rotacion, 1)} veces tu capital`);
  }

  // ---- automatización de anuncios --------------------------------------------

  function ladoAnuncio(id, datos) {
    const caja = document.getElementById(id);
    const precio = caja.querySelector("[data-precio]");
    const nota = caja.querySelector("[data-nota]");
    caja.querySelector(".insignia-lider")?.remove();
    if (!datos || !datos.publicar || datos.precio == null) {
      caja.dataset.activo = "no";
      precio.textContent = "en pausa";
      nota.textContent = datos ? datos.motivo : "—";
      return;
    }
    caja.dataset.activo = "si";
    precio.textContent = `${num(datos.precio)} Bs`;
    nota.textContent = datos.competitivo ? "quedas de primero" : "precio con margen protegido";
    if (datos.competitivo) {
      const insignia = el("span", { class: "insignia-lider estado-ok", text: "Líder" });
      caja.insertBefore(insignia, nota);
    }
  }

  function renderAnuncios(d) {
    const a = d.anuncios;
    if (!a) return;
    const cfg = a.config;
    const estado = a.estado;
    const plan = a.plan || (estado && estado.lados ? { lados: estado.lados, margen_pct: estado.margen_pct, competitivo: estado.competitivo } : null);
    const modo = cfg.modo === "real" ? "modo real" : "modo simulación";
    poner("an-sub", cfg.automatico
      ? `Repositor activo en ${modo}, cada ${Math.round(cfg.intervalo_seg)} s · margen mínimo ${num(cfg.margen_minimo_pct, 1)} % · pago ${rutaTexto(cfg.ruta)}.`
      : "Repositor apagado. Actívalo con anuncios.automatico = true en config.toml, o pulsa “Calcular ahora” para ver qué haría.");
    const chip = $("#an-estado");
    const pausada = operativaPausada(d);
    if (pausada) { chip.dataset.estado = "apagado"; chip.querySelector(".texto").textContent = "En pausa"; chip.title = "Operatividad en pausa"; }
    else {
      if (!cfg.automatico) chip.dataset.estado = "apagado";
      else if (estado.error) chip.dataset.estado = "error";
      else chip.dataset.estado = cfg.modo === "real" ? "ok" : "aviso";
      chip.querySelector(".texto").textContent = !cfg.automatico ? "Apagado"
        : estado.error ? "Error" : cfg.modo === "real" ? `Real · ${hace(estado.ok_ms)}` : `Simulación · ${hace(estado.ok_ms)}`;
      chip.title = estado.error || "";
    }

    renderGuiaManual(d);
    ladoAnuncio("an-compra", plan && plan.lados ? plan.lados.compra : null);
    ladoAnuncio("an-venta", plan && plan.lados ? plan.lados.venta : null);
    const margen = plan ? plan.margen_pct : null;
    poner("an-margen", margen == null ? "—" : `${conSigno(margen)} %`);
    poner("an-margen-nota", margen == null ? "sin mercado aún"
      : margen >= cfg.margen_minimo_pct ? "cumple tu mínimo" : "por debajo de tu mínimo");
    const acciones = (estado && estado.acciones) || [];
    poner("an-log", acciones.length ? acciones.map((x) => "• " + x.texto).join("\n") : (estado && estado.error) || "");
    $("#an-modo-real").hidden = !(cfg.automatico && cfg.modo === "real");
    $("#an-reposicionar").hidden = !cfg.binance_configurado && cfg.modo === "real";
  }

  function copiar(texto, boton) {
    const listo = () => { const t = boton.textContent; boton.textContent = "Copiado"; setTimeout(() => { boton.textContent = t; }, 1200); };
    if (navigator.clipboard) navigator.clipboard.writeText(texto).then(listo, () => {});
  }

  function renderGuiaManual(d) {
    const g = d.anuncios && d.anuncios.guia_manual;
    const manual = d.anuncios && d.anuncios.config.modo === "manual";
    $("#guia-manual").hidden = !manual || !g;
    if (!manual || !g) return;
    poner("gm-par", `${g.activo} / ${g.fiat}`);
    const cont = $("#gm-lados");
    cont.textContent = "";
    for (const lado of ["compra", "venta"]) {
      const v = g.lados[lado];
      const nombre = (id) => NOMBRES_METODO[id] || id;
      const fila = (t, valor) => el("span", { class: "sub", text: `${t}: ${valor}` });
      const caja = el("div", { class: "an-lado" },
        el("span", { class: "etiqueta", text: `Anuncio de ${lado} (en Binance: ${v.tipo_binance})` }),
        el("span", { class: "cifra", text: v.precio ? `${num(v.precio)} Bs` : "No publicar" }),
        fila("Cantidad", `${num(v.usdt, 2)} USDT`),
        fila("Límite por orden", v.min_bs || v.max_bs ? `${num(v.min_bs, 0)}–${num(v.max_bs, 0)} Bs` : "a tu gusto"),
        fila("Métodos de pago", v.metodos.map(nombre).join(", ")),
        fila("Tiempo de pago", `${v.tiempo_pago_min} min`),
        el("span", { class: "sub", text: v.precio ? "" : v.motivo }));
      if (v.precio) caja.append(el("button", { type: "button", class: "boton secundario", text: "Copiar precio",
        onclick: (e) => copiar(String(v.precio), e.currentTarget) }));
      cont.append(caja);
    }
    poner("gm-nota", "El precio se actualiza solo con el mercado; esta tarjeta siempre muestra el valor más reciente.");
  }

  // ---- gráficos --------------------------------------------------------------

  function figura(id, dibujar, columnas, filas) {
    const fig = document.getElementById(id);
    const lienzo = fig.querySelector(".lienzo");
    dibujos[id] = () => dibujar(lienzo);
    dibujos[id]();
    G.tabla(fig.querySelector(".tabla-alt"), columnas, filas);
  }

  function renderGraficos(d) {
    // con el período "hoy" una sola barra no dice nada: se grafica cada ciclo
    const porCiclo = d.periodo === "hoy";
    const ciclos = [...d.ciclos].reverse();
    poner("g-ganancia-titulo", porCiclo ? "Ganancia por ciclo, hoy" : "Ganancia por día");

    const barras = porCiclo
      ? ciclos.map((c) => ({ etiqueta: soloHora(c.ts_cierre), valor: c.ganancia_usdt, ciclo: c }))
      : d.diaria.map((f) => ({ etiqueta: diaDeISO(f.fecha), valor: f.ganancia_usdt, dia: f }));
    figura("g-ganancia", (lienzo) => G.barras(lienzo, barras, {
      descripcion: porCiclo ? "Ganancia de cada ciclo de hoy en USDT" : "Ganancia de cada día en USDT",
      vacio: "Sin ciclos cerrados en este período",
      formatoEje: ejeNumero,
      formatoEtiqueta: (v) => conSigno(v),
      tooltip: (b) => porCiclo
        ? { titulo: `Ciclo cerrado a las ${b.etiqueta}`, filas: [
          { valor: `${conSigno(b.valor)} USDT`, clase: b.valor < 0 ? "neg" : "s1" },
          { valor: `${num(b.ciclo.usdt)} USDT`, nombre: "emparejados" },
          { valor: `${num(b.ciclo.spread_pct)} %`, nombre: "spread" }] }
        : { titulo: b.etiqueta, filas: [
          { valor: `${conSigno(b.valor)} USDT`, clase: b.valor < 0 ? "neg" : "s1" },
          { valor: `Bs ${conSigno(b.dia.ganancia_bs, 0)}` },
          { valor: String(b.dia.ciclos), nombre: b.dia.ciclos === 1 ? "ciclo" : "ciclos" },
          { valor: `${num(b.dia.volumen_usdt, 0)} USDT`, nombre: "volumen" }] },
    }), porCiclo
      ? [{ titulo: "Hora", valor: (b) => b.etiqueta }, { titulo: "Ganancia USDT", num: true, valor: (b) => conSigno(b.valor) },
        { titulo: "USDT", num: true, valor: (b) => num(b.ciclo.usdt) }, { titulo: "Spread %", num: true, valor: (b) => num(b.ciclo.spread_pct) }]
      : [{ titulo: "Día", valor: (b) => b.etiqueta }, { titulo: "Ganancia USDT", num: true, valor: (b) => conSigno(b.valor) },
        { titulo: "Ganancia Bs", num: true, valor: (b) => conSigno(b.dia.ganancia_bs, 0) }, { titulo: "Ciclos", num: true, valor: (b) => b.dia.ciclos },
        { titulo: "Volumen USDT", num: true, valor: (b) => num(b.dia.volumen_usdt, 0) }],
    [...barras].reverse());

    let suma = 0;
    const acumulada = porCiclo
      ? ciclos.map((c) => ({ t: c.ts_cierre, v: (suma += c.ganancia_usdt) }))
      : d.diaria.map((f) => ({ t: partesISO(f.fecha), v: f.acumulada_usdt }));
    figura("g-acumulada", (lienzo) => G.lineas(lienzo, [{ nombre: "Acumulada", clase: "s1", puntos: acumulada }], {
      descripcion: "Ganancia acumulada del período en USDT",
      vacio: "Sin ciclos cerrados en este período",
      area: true, incluirCero: true,
      formatoEje: ejeNumero,
      formatoValor: (v) => `${conSigno(v)} USDT`,
      formatoTiempo: porCiclo ? soloHora : diaCorto,
      formatoTiempoLargo: porCiclo ? fechaHora : diaCorto,
    }), [{ titulo: porCiclo ? "Hora" : "Día", valor: (p) => (porCiclo ? soloHora(p.t) : diaCorto(p.t)) },
      { titulo: "Acumulada USDT", num: true, valor: (p) => conSigno(p.v) }],
    [...acumulada].reverse());

    const serie = d.mercado.serie;
    const rango = serie.length ? serie[serie.length - 1].ts_ms - serie[0].ts_ms : 0;
    const etiquetaTiempo = rango > 2 * DIA_MS ? diaCorto : soloHora;
    figura("g-precios", (lienzo) => G.lineas(lienzo, [
      { nombre: "Comprar", clase: "s1", puntos: serie.map((p) => ({ t: p.ts_ms, v: p.precio_compra })) },
      { nombre: "Vender", clase: "s2", puntos: serie.map((p) => ({ t: p.ts_ms, v: p.precio_venta })) },
    ], {
      descripcion: "Precio para comprar y para vender USDT en Binance P2P, en bolívares",
      vacio: "Todavía no hay cotizaciones guardadas en este período",
      etiquetasFinales: true,
      formatoEje: ejeNumero,
      formatoValor: (v) => num(v),
      formatoTiempo: etiquetaTiempo,
      formatoTiempoLargo: fechaHora,
    }), [{ titulo: "Fecha", valor: (p) => fechaHora(p.ts_ms) }, { titulo: "Comprar USDT", num: true, valor: (p) => num(p.precio_compra) },
      { titulo: "Vender USDT", num: true, valor: (p) => num(p.precio_venta) }, { titulo: "Brecha %", num: true, valor: (p) => num(p.spread_pct) }],
    [...serie].reverse());

    poner("g-spread-sub", `% por cada USDT comprado y vendido, pagando ${rutaTexto(d.config.ruta_por_defecto)}. Línea punteada: tu umbral de alerta.`);
    figura("g-spread", (lienzo) => G.lineas(lienzo, [
      { nombre: "neto como anunciante", clase: "s1", puntos: serie.map((p) => ({ t: p.ts_ms, v: p.neto_anunciante_pct })) },
    ], {
      descripcion: "Ganancia neta estimada por vuelta como anunciante, en porcentaje",
      vacio: "Todavía no hay cotizaciones guardadas en este período",
      umbral: { valor: d.config.spread_minimo_pct, etiqueta: `umbral ${num(d.config.spread_minimo_pct, 1)} %` },
      incluirCero: true,
      formatoEje: ejeNumero,
      formatoValor: (v) => `${conSigno(v)} %`,
      formatoTiempo: etiquetaTiempo,
      formatoTiempoLargo: fechaHora,
      filasExtra: (t) => {
        const p = serie.find((q) => q.ts_ms === t);
        return p ? [{ valor: `${num(p.spread_pct)} %`, nombre: "brecha bruta" }] : [];
      },
    }), [{ titulo: "Fecha", valor: (p) => fechaHora(p.ts_ms) }, { titulo: "Neto anunciante %", num: true, valor: (p) => conSigno(p.neto_anunciante_pct) },
      { titulo: "Neto tomador %", num: true, valor: (p) => conSigno(p.neto_tomador_pct) }, { titulo: "Brecha %", num: true, valor: (p) => num(p.spread_pct) }],
    [...serie].reverse());
  }

  // ---- tablas ----------------------------------------------------------------

  function selectorRuta(o, rutaDefecto) {
    const select = el("select", { "aria-label": `Cómo pagaste la orden ${o.numero}` },
      el("option", { value: "", text: `Por defecto (${rutaTexto(rutaDefecto)})` }),
      el("option", { value: "BDV", text: "BDV → BDV" }),
      el("option", { value: "INTERBANCARIO", text: "Interbancario" }));
    select.value = o.ruta_pago || "";
    select.addEventListener("change", async () => {
      select.disabled = true;
      try {
        await api(`/api/ordenes/${encodeURIComponent(o.numero)}`, { method: "PATCH", body: JSON.stringify({ ruta_pago: select.value || null }) });
        cargar(false);
      } catch (e) {
        avisos([`No pude guardar el cambio: ${e.message}`]);
        select.disabled = false;
      }
    });
    return select;
  }

  async function borrarOrden(numero) {
    if (!window.confirm("¿Borrar esta orden manual?")) return;
    try {
      await api(`/api/ordenes/${encodeURIComponent(numero)}`, { method: "DELETE" });
      cargar(false);
    } catch (e) {
      avisos([`No pude borrar la orden: ${e.message}`]);
    }
  }

  function claseEstado(estado) {
    if (estado === "COMPLETED") return "estado-ok";
    return estado.startsWith("CANCEL") ? "estado-neutro" : "estado-alerta";
  }

  function renderOrdenes(d) {
    poner("c-ordenes", d.ordenes.length ? String(d.ordenes.length) : "");
    G.tabla($("#p-ordenes"), [
      { titulo: "Fecha", valor: (o) => fechaHora(o.creada_ms) },
      { titulo: "Tipo", valor: (o) => el("span", { class: "etiqueta-tipo", text: o.tipo === "COMPRA" ? "Compra" : "Venta" }) },
      { titulo: "USDT", num: true, valor: (o) => num(o.cantidad) },
      { titulo: "Precio", num: true, valor: (o) => num(o.precio) },
      { titulo: "Total Bs", num: true, valor: (o) => num(o.total_bs) },
      { titulo: "Comisión USDT", num: true, valor: (o) => num(o.comision, 4) },
      { titulo: "Cómo pagaste", valor: (o) => (o.tipo === "COMPRA" ? selectorRuta(o, d.config.ruta_por_defecto) : "—") },
      { titulo: "Estado", valor: (o) => el("span", { class: claseEstado(o.estado), text: ESTADOS[o.estado] || o.estado }) },
      { titulo: "Contraparte", valor: (o) => o.contraparte },
      { titulo: "Rol", valor: (o) => ({ MAKER: "Anunciante", TAKER: "Tomador" })[o.rol] || "—" },
      { titulo: "", valor: (o) => (o.origen === "manual"
        ? el("button", { type: "button", class: "enlace-boton", text: "Borrar", onclick: () => borrarOrden(o.numero) })
        : o.origen === "demo" ? "demo" : "") },
    ], d.ordenes, "No hay órdenes en este período.");
  }

  function renderCiclos(d) {
    poner("c-ciclos", d.ciclos.length ? String(d.ciclos.length) : "");
    G.tabla($("#p-ciclos"), [
      { titulo: "Cerrado", valor: (c) => fechaHora(c.ts_cierre) },
      { titulo: "Sentido", valor: (c) => (c.sentido.startsWith("COMPRA") ? "Compra → Venta" : "Venta → Compra") },
      { titulo: "USDT", num: true, valor: (c) => num(c.usdt) },
      { titulo: "Compra efectiva", num: true, valor: (c) => num(c.precio_compra) },
      { titulo: "Venta efectiva", num: true, valor: (c) => num(c.precio_venta) },
      { titulo: "Spread %", num: true, valor: (c) => conSigno(c.spread_pct) },
      { titulo: "Ganancia USDT", num: true, valor: (c) => conSigno(c.ganancia_usdt, 4) },
      { titulo: "Ganancia Bs", num: true, valor: (c) => conSigno(c.ganancia_bs) },
    ], d.ciclos, "Todavía no hay ciclos cerrados en este período: un ciclo se cierra cuando una venta se empareja con una compra.");
  }

  function renderBanco(d) {
    const cont = $("#p-banco");
    const c = d.banco.conciliacion;
    const pendientes = c.pagos_sin_orden.length + c.alertas.length + (c.por_liberar ? c.por_liberar.length : 0);
    const contador = $("#c-banco");
    contador.textContent = pendientes ? String(pendientes) : "";
    contador.classList.toggle("atencion", pendientes > 0);
    cont.textContent = "";
    if (!c.activo) {
      cont.append(el("p", { class: "nota-tabla", text: "Aún no hay pagos del banco registrados en este período. Activa el reenvío automático de SMS (ver README) o pega un SMS en el formulario de abajo." }));
      if (!d.config.webhook_configurado) {
        cont.append(el("p", { class: "nota-tabla", text: "El webhook de SMS está apagado: define banco.webhook_token en config.toml para recibirlos solos." }));
      }
      return;
    }
    if (c.alertas.length) cont.append(el("ul", { class: "lista-alertas" }, ...c.alertas.map((a) => el("li", { text: a }))));
    if (c.por_liberar && c.por_liberar.length) {
      const caja = el("div", { class: "lista-liberar" }, el("h4", { text: `Listas para liberar (${c.por_liberar.length})` }));
      const lista = el("ul");
      for (const x of c.por_liberar) {
        const li = el("li", {}, `Orden ${x.orden.numero}: ${num(x.orden.cantidad)} USDT · pago de Bs ${num(x.pago.monto_bs)}`
          + (x.pago.contraparte ? ` de ${x.pago.contraparte}` : "") + (x.pago.referencia ? ` (Ref ${x.pago.referencia})` : "") + " ");
        li.append(badgeVeredicto(x.verificacion));
        lista.append(li);
      }
      caja.append(lista, el("p", { class: "recordatorio", text: "Antes de liberar en Binance: confirma el pago en tu BDVApp y que el titular sea el mismo comprador. Si ves “posible tercero”, no liberes. La liberación la haces tú en la app de Binance." }));
      cont.append(caja);
    }
    cont.append(el("div", { class: "resumen-banco" },
      el("span", { class: "estado-ok", text: `${c.conciliados} pagos conciliados con su orden` }),
      el("span", { class: c.pagos_sin_orden.length ? "estado-alerta" : "estado-neutro", text: `${c.pagos_sin_orden.length} pagos sin orden` }),
      el("span", { class: c.ordenes_sin_pago.length ? "estado-alerta" : "estado-neutro", text: `${c.ordenes_sin_pago.length} órdenes completadas sin pago registrado` })));
    const envoltura = el("div", { class: "tabla-envoltura" });
    cont.append(envoltura);
    G.tabla(envoltura, [
      { titulo: "Fecha", valor: (m) => fechaHora(m.ts_ms) },
      { titulo: "Tipo", valor: (m) => el("span", { class: "etiqueta-tipo", text: m.tipo === "ENTRADA" ? "Recibido" : "Enviado" }) },
      { titulo: "Monto Bs", num: true, valor: (m) => num(m.monto_bs) },
      { titulo: "Referencia", valor: (m) => m.referencia },
      { titulo: "Contraparte", valor: (m) => m.contraparte },
      { titulo: "Orden", valor: (m) => m.orden_numero },
      { titulo: "Estado", valor: (m) => (m.orden_numero
        ? el("span", { class: "estado-ok", text: "Conciliado" })
        : el("span", { class: "estado-alerta", text: "Sin orden: revísalo" })) },
    ], d.banco.movimientos);
  }

  function renderContrapartes(d) {
    const cont = $("#p-contrapartes");
    const lista = d.contrapartes || [];
    poner("c-contrapartes", lista.length ? String(lista.length) : "");
    cont.textContent = "";
    cont.append(el("p", { class: "nota-tabla", text: "Guarda la identidad verificada de cada contraparte (lo que ves en su perfil/orden de Binance). El bot la usa para avisarte si el banco no coincide al pagar o al liberar." }));
    // formulario para agregar/editar
    const f = el("form", { class: "campos-linea", id: "f-contraparte", autocomplete: "off" });
    const campo = (nombre, ph, w) => el("input", { name: nombre, placeholder: ph, style: `flex:${w}` });
    const nick = campo("nick", "Apodo en Binance *", "1.2");
    const nombre = campo("nombre", "Nombre verificado", "1.4");
    const cedula = campo("cedula", "Cédula/RIF", "1");
    const tel = campo("telefonos", "Teléfonos (coma)", "1.2");
    const guardar = el("button", { type: "submit", class: "boton", text: "Guardar" });
    f.append(nick, nombre, cedula, tel, guardar);
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!nick.value.trim()) return;
      try {
        await api("/api/contrapartes", { method: "PUT", body: JSON.stringify({
          nick: nick.value.trim(), nombre: nombre.value || null, cedula: cedula.value || null,
          telefonos: tel.value || null, confianza: "verificada",
        }) });
        cargar(false);
      } catch (err) {
        avisos([`No pude guardar la contraparte: ${err.message}`]);
      }
    });
    cont.append(f);
    const envoltura = el("div", { class: "tabla-envoltura" });
    cont.append(envoltura);
    G.tabla(envoltura, [
      { titulo: "Apodo Binance", valor: (c) => c.nick },
      { titulo: "Nombre", valor: (c) => c.nombre },
      { titulo: "Cédula/RIF", valor: (c) => c.cedula },
      { titulo: "Teléfonos", valor: (c) => c.telefonos },
      { titulo: "Estado", valor: (c) => el("span", { class: c.confianza === "sospechosa" ? "estado-alerta" : "estado-ok",
        text: c.confianza === "sospechosa" ? "Sospechosa" : "Verificada" }) },
      { titulo: "", valor: (c) => el("button", { type: "button", class: "enlace-boton", text: "Borrar",
        onclick: () => borrarContraparte(c.nick) }) },
    ], lista, "Aún no has guardado contrapartes. Agrega una arriba con el apodo que te muestra Binance.");
  }

  async function borrarContraparte(nick) {
    if (!window.confirm(`¿Borrar la contraparte "${nick}"?`)) return;
    try {
      await api(`/api/contrapartes/${encodeURIComponent(nick)}`, { method: "DELETE" });
      cargar(false);
    } catch (err) {
      avisos([`No pude borrar la contraparte: ${err.message}`]);
    }
  }

  function renderDecisiones(d) {
    const lista = $("#decisiones-lista");
    lista.textContent = "";
    for (const e of d.decisiones || []) {
      lista.append(el("li", {}, el("time", { text: fechaHora(e.ts_ms) }), el("span", { text: e.mensaje })));
    }
    $("#decisiones-vacio").hidden = !!(d.decisiones || []).length;
    poner("decisiones-estado", d.config.pagos_automaticos ? "Pagos automáticos: activados" : "Pagos automáticos: apagados");
  }

  function renderEventos(d) {
    G.tabla($("#p-eventos"), [
      { titulo: "Fecha", valor: (e) => fechaHora(e.ts_ms) },
      { titulo: "Tipo", valor: (e) => el("span", { class: e.nivel === "error" ? "estado-alerta" : e.nivel === "info" ? "estado-neutro" : "estado-ok", text: NIVELES[e.nivel] || e.nivel }) },
      { titulo: "Mensaje", clase: "texto-largo", valor: (e) => e.mensaje },
    ], d.eventos, "Sin eventos todavía.");
  }

  // ---- formularios y controles -----------------------------------------------

  function ahoraLocal() {
    const ahora = new Date();
    return new Date(ahora.getTime() - ahora.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }

  function iniciarFormularioOrden() {
    const f = $("#f-orden");
    const total = () => {
      const t = Number(f.cantidad.value) * Number(f.precio.value);
      poner("f-orden-total", t > 0 ? `Total: Bs ${num(t)}` : "Total: —");
    };
    const tipo = () => { f.querySelector(".solo-compra").hidden = f.tipo.value !== "COMPRA"; };
    f.cantidad.addEventListener("input", total);
    f.precio.addEventListener("input", total);
    f.tipo.addEventListener("change", tipo);
    f.closest("details").addEventListener("toggle", () => { if (!f.fecha.value) f.fecha.value = ahoraLocal(); });
    tipo();
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const cuerpo = {
        tipo: f.tipo.value,
        cantidad: Number(f.cantidad.value),
        precio: Number(f.precio.value),
        comision: Number(f.comision.value || 0),
        ruta_pago: f.tipo.value === "COMPRA" && f.ruta_pago.value ? f.ruta_pago.value : null,
        fecha_ms: f.fecha.value ? new Date(f.fecha.value).getTime() : null,
        contraparte: f.contraparte.value || null,
        nota: f.nota.value || null,
      };
      try {
        const o = await api("/api/ordenes", { method: "POST", body: JSON.stringify(cuerpo) });
        poner("f-orden-resultado", `Orden guardada (${o.numero}).`);
        f.cantidad.value = "";
        f.precio.value = "";
        f.contraparte.value = "";
        f.nota.value = "";
        total();
        cargar(false);
      } catch (err) {
        poner("f-orden-resultado", `No pude guardar la orden: ${err.message}`);
      }
    });
  }

  function describirAviso(a) {
    return `${a.tipo === "ENTRADA" ? "Pago recibido" : "Pago enviado"} · Bs ${num(a.monto_bs)}` +
      (a.referencia ? ` · Ref ${a.referencia}` : "") + (a.contraparte ? ` · ${a.contraparte}` : "");
  }

  function iniciarFormularioBanco() {
    const f = $("#f-banco");
    $("#f-banco-probar").addEventListener("click", async () => {
      if (f.texto.value.trim().length < 3) return;
      try {
        const r = await api("/api/banco/probar", { method: "POST", body: JSON.stringify({ texto: f.texto.value }) });
        poner("f-banco-resultado", r.ok ? `Lo leo así: ${describirAviso(r.aviso)}` : "No reconozco este texto como un aviso de pago. Ajusta los patrones en [banco] de config.toml.");
      } catch (err) {
        poner("f-banco-resultado", `Error: ${err.message}`);
      }
    });
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (f.texto.value.trim().length < 3) return;
      try {
        const r = await api("/api/banco/texto", { method: "POST", body: JSON.stringify({ texto: f.texto.value }) });
        if (!r.ok) poner("f-banco-resultado", r.motivo);
        else if (r.duplicado) poner("f-banco-resultado", "Ese pago ya estaba registrado.");
        else {
          poner("f-banco-resultado", `${describirAviso(r.aviso)}\n` + (r.orden ? `Conciliado con la orden ${r.orden.numero}.` : "Guardado; todavía sin orden asociada."));
          f.texto.value = "";
        }
        cargar(false);
      } catch (err) {
        poner("f-banco-resultado", `Error: ${err.message}`);
      }
    });
  }

  function iniciarControles() {
    const botones = document.querySelectorAll(".segmentado button");
    const marcar = () => botones.forEach((b) => b.setAttribute("aria-checked", String(b.dataset.periodo === periodo)));
    botones.forEach((b) => b.addEventListener("click", () => {
      periodo = b.dataset.periodo;
      guardar("periodo", periodo);
      marcar();
      cargar(true);
    }));
    marcar();

    $("#btn-sync").addEventListener("click", async (e) => {
      const boton = e.currentTarget;
      boton.disabled = true;
      boton.textContent = "Sincronizando…";
      try {
        const r = await api("/api/sincronizar", { method: "POST" });
        avisos(r.ok ? [] : [r.error]);
        await cargar(false);
      } catch (err) {
        avisos([`No pude sincronizar: ${err.message}`]);
      } finally {
        boton.disabled = false;
        boton.textContent = "Sincronizar con Binance";
      }
    });

    $("#btn-operativa").addEventListener("click", () => cambiarOperativa($("#btn-operativa").dataset.pausada !== "true"));
    $("#banner-reanudar").addEventListener("click", () => cambiarOperativa(false));

    $("#an-reposicionar").addEventListener("click", async (e) => {
      const boton = e.currentTarget;
      boton.disabled = true;
      const previo = boton.textContent;
      boton.textContent = "Calculando…";
      try {
        await api("/api/anuncios/reposicionar", { method: "POST" });
        await cargar(false);
      } catch (err) {
        avisos([`No pude reposicionar los anuncios: ${err.message}`]);
      } finally {
        boton.disabled = false;
        boton.textContent = previo;
      }
    });

    $("#tema").addEventListener("click", () => {
      const raiz = document.documentElement;
      const oscuro = raiz.dataset.theme ? raiz.dataset.theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
      raiz.dataset.theme = oscuro ? "light" : "dark";
      guardar("tema", raiz.dataset.theme);
    });

    const pestanas = [...document.querySelectorAll('[role="tab"]')];
    const activar = (pestana) => {
      for (const p of pestanas) {
        const activa = p === pestana;
        p.setAttribute("aria-selected", String(activa));
        p.tabIndex = activa ? 0 : -1;
        document.getElementById(p.getAttribute("aria-controls")).hidden = !activa;
      }
    };
    pestanas.forEach((p, i) => {
      p.addEventListener("click", () => activar(p));
      p.addEventListener("keydown", (e) => {
        const salto = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
        if (!salto) return;
        const siguiente = pestanas[(i + salto + pestanas.length) % pestanas.length];
        activar(siguiente);
        siguiente.focus();
      });
    });
    activar(pestanas[0]);

    document.querySelectorAll(".ver-tabla").forEach((boton) => boton.addEventListener("click", () => {
      const tablaAlt = boton.closest("figure").querySelector(".tabla-alt");
      tablaAlt.hidden = !tablaAlt.hidden;
      boton.setAttribute("aria-expanded", String(!tablaAlt.hidden));
      boton.textContent = tablaAlt.hidden ? "Ver tabla" : "Ocultar tabla";
    }));

    // redibuja los gráficos cuando cambia el ancho (rotar el teléfono, cambiar ventana)
    let pendiente = 0;
    let anchoPrevio = 0;
    new ResizeObserver((entradas) => {
      const ancho = Math.round(entradas[0].contentRect.width);
      if (ancho === anchoPrevio) return;
      anchoPrevio = ancho;
      cancelAnimationFrame(pendiente);
      pendiente = requestAnimationFrame(() => Object.values(dibujos).forEach((dibujar) => dibujar()));
    }).observe($(".graficos"));
  }

  function iniciarSimulador() {
    const f = $("#f-sim");
    const cuerpoSim = () => {
      const opcional = (v) => (v === "" ? null : Number(v));
      return { capital: Number(f.capital.value), moneda: f.moneda.value, ciclos: Number(f.ciclos.value),
        ciclos_por_dia: Number(f.ciclos_por_dia.value || 1), rol: f.rol.value, ruta: f.ruta.value,
        reinvertir: f.reinvertir.checked, precio_compra: opcional(f.precio_compra.value),
        precio_venta: opcional(f.precio_venta.value) };
    };
    $("#sim-aplicar").addEventListener("click", async () => {
      try {
        const r = await api("/api/simulacion/ciclos", { method: "POST", body: JSON.stringify(cuerpoSim()) });
        poner("sim-resultado", `Listo: creé ${r.ordenes_creadas} órdenes simuladas (${r.ciclos} ciclos). Mira los indicadores y las tablas.`);
        cargar(false);
      } catch (err) { poner("sim-resultado", `No pude aplicar los ciclos: ${err.message}`); }
    });
    $("#sim-reiniciar").addEventListener("click", async () => {
      const ejemplo = confirm("¿Reiniciar la simulación? Se borra todo lo simulado.\n\nAceptar = empezar de cero.\nCancelar = no hacer nada.");
      if (!ejemplo) return;
      const conEjemplo = confirm("¿Quieres cargar 45 días de datos de ejemplo?\n\nAceptar = con datos de ejemplo.\nCancelar = totalmente vacía.");
      try {
        await api("/api/simulacion/reiniciar", { method: "POST", body: JSON.stringify({ capital: Number(f.capital.value) || 1000, con_ejemplo: conEjemplo }) });
        $("#sim-salida").hidden = true;
        poner("sim-resultado", `Simulación reiniciada con ${num(Number(f.capital.value) || 1000, 2)} USDT${conEjemplo ? " y datos de ejemplo" : ""}.`);
        cargar(false);
      } catch (err) { poner("sim-resultado", `No pude reiniciar: ${err.message}`); }
    });
    const salida = $("#sim-salida");
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const opcional = (v) => (v === "" ? null : Number(v));
      poner("sim-resultado", "");
      try {
        const r = await api("/api/simulador", { method: "POST", body: JSON.stringify({
          capital: Number(f.capital.value), moneda: f.moneda.value, ciclos: Number(f.ciclos.value),
          ciclos_por_dia: Number(f.ciclos_por_dia.value || 1), rol: f.rol.value, ruta: f.ruta.value,
          reinvertir: f.reinvertir.checked, precio_compra: opcional(f.precio_compra.value),
          precio_venta: opcional(f.precio_venta.value) }) });
        const u = r.moneda === "USDT" ? "USDT" : "Bs";
        const dec = r.moneda === "USDT" ? 2 : 0;
        const dato = (t, v) => el("div", {}, el("dt", { text: t }), el("dd", { text: v }));
        const resumen = $("#sim-resumen");
        resumen.textContent = "";
        resumen.append(
          dato("Capital inicial", `${num(r.capital_inicial, dec)} ${u}`),
          dato("Capital final", `${num(r.capital_final, dec)} ${u}`),
          dato("Ganancia total", `${conSigno(r.ganancia, dec)} ${u}` + (r.moneda === "VES" ? ` (≈ ${conSigno(r.ganancia_usdt, 2)} USDT)` : "")),
          dato("Rendimiento", `${conSigno(r.rendimiento_pct, 2)} %`),
          dato("Ganancia por ciclo", `${conSigno(r.ganancia_por_ciclo_pct, 3)} %`),
          dato("Duración", `${r.ciclos} ciclos en ${r.dias} ${r.dias === 1 ? "día" : "días"}`),
          dato("Precios usados", `compras a ${num(r.precio_compra)} · vendes a ${num(r.precio_venta)}${r.precios_de_mercado ? " (mercado)" : ""}`),
          dato("Ruta y rol", `${rutaTexto(r.ruta)} · ${r.rol}`));
        const av = $("#sim-avisos");
        av.hidden = !r.avisos.length;
        av.textContent = r.avisos.join(" ");
        G.tabla($("#sim-tabla"), [
          { titulo: "Ciclo", num: true, valor: (c) => num(c.n, 0) },
          { titulo: "Día", num: true, valor: (c) => num(c.dia, 0) },
          { titulo: `Capital inicio (${u})`, num: true, valor: (c) => num(c.capital_inicio, dec) },
          { titulo: `Ganancia (${u})`, num: true, valor: (c) => conSigno(c.ganancia, dec === 0 ? 0 : 3) },
          { titulo: "%", num: true, valor: (c) => conSigno(c.ganancia_pct, 3) },
          { titulo: `Acumulada (${u})`, num: true, valor: (c) => conSigno(c.acumulada, dec) },
          { titulo: `Capital fin (${u})`, num: true, valor: (c) => num(c.capital_fin, dec) },
        ], r.filas, "Sin ciclos.");
        salida.hidden = false;
      } catch (err) {
        salida.hidden = true;
        poner("sim-resultado", `No pude simular: ${err.message}`);
      }
    });
  }

  iniciarControles();
  iniciarFormularioOrden();
  iniciarSimulador();
  iniciarFormularioBanco();
  cargar(true);
  setInterval(() => { if (!document.hidden) cargar(false); }, 30000);
})();
