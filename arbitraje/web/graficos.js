/* Gráficos SVG sin dependencias externas (funcionan sin internet).
   - barras(): una barra por categoría, azul si gana y roja si pierde.
   - lineas(): series en el tiempo con cruceta, tooltip y navegación con flechas.
   - tabla():  la versión en tabla de cualquier gráfico.
   Los colores salen de variables CSS: cambiar de tema no obliga a redibujar. */
(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const MARGEN = { arriba: 16, derecha: 16, abajo: 28, izquierda: 52 };
  let limpiezaActual = null;

  function nodo(tag, attrs, padre) {
    const n = document.createElementNS(NS, tag);
    if (attrs) for (const k of Object.keys(attrs)) n.setAttribute(k, attrs[k]);
    if (padre) padre.appendChild(n);
    return n;
  }

  function texto(padre, x, y, contenido, clase, ancla) {
    const t = nodo("text", { x, y, class: clase, "text-anchor": ancla || "start", "dominant-baseline": "middle" }, padre);
    t.textContent = contenido;
    return t;
  }

  // Escala con marcas redondas: 0, 2, 4… o 950, 960, 970…
  function escala(min, max, cantidad) {
    if (!isFinite(min) || !isFinite(max)) { min = 0; max = 1; }
    if (min === max) { const d = Math.abs(min) * 0.05 || 1; min -= d; max += d; }
    const crudo = (max - min) / cantidad;
    const potencia = Math.pow(10, Math.floor(Math.log10(crudo)));
    const r = crudo / potencia;
    const paso = (r >= 7.5 ? 10 : r >= 3.5 ? 5 : r >= 1.5 ? 2 : 1) * potencia;
    const ini = Math.floor(min / paso + 1e-9) * paso;
    const fin = Math.ceil(max / paso - 1e-9) * paso;
    const marcas = [];
    for (let v = ini; v <= fin + paso / 2; v += paso) marcas.push(Math.abs(v) < paso / 1e6 ? 0 : Number(v.toPrecision(12)));
    return { min: ini, max: fin, marcas, paso };
  }

  function tooltip() { return document.getElementById("tooltip"); }

  function mostrarTooltip(contenido, x, y) {
    const tt = tooltip();
    if (!tt || !contenido) return;
    tt.textContent = "";
    const titulo = document.createElement("div");
    titulo.className = "t-titulo";
    titulo.textContent = contenido.titulo;
    tt.appendChild(titulo);
    for (const f of contenido.filas) {
      const fila = document.createElement("div");
      fila.className = "t-fila";
      const clave = document.createElement("span");
      clave.className = "t-clave " + (f.clase || "ninguna");
      const valor = document.createElement("span");
      valor.className = "t-valor";
      valor.textContent = f.valor;
      fila.append(clave, valor);
      if (f.nombre) {
        const nombre = document.createElement("span");
        nombre.className = "t-nombre";
        nombre.textContent = f.nombre;
        fila.appendChild(nombre);
      }
      tt.appendChild(fila);
    }
    tt.hidden = false;
    const caja = tt.getBoundingClientRect();
    let px = x + 14;
    let py = y - caja.height - 12;
    if (px + caja.width > window.innerWidth - 8) px = x - caja.width - 14;
    if (py < 8) py = y + 18;
    tt.style.left = Math.max(8, px) + "px";
    tt.style.top = Math.max(8, Math.min(py, window.innerHeight - caja.height - 8)) + "px";
  }

  function ocultarTooltip() {
    const tt = tooltip();
    if (tt) tt.hidden = true;
    if (limpiezaActual) { limpiezaActual(); limpiezaActual = null; }
  }

  function vacio(cont, mensaje) {
    cont.textContent = "";
    const div = document.createElement("div");
    div.className = "vacio";
    div.textContent = mensaje || "Sin datos para este período";
    cont.appendChild(div);
  }

  // Puntero, toque y teclado (flechas, Inicio, Fin) sobre una capa que cubre toda el área
  function interaccion(svg, capa, xs, mostrar, limpiar) {
    let activo = -1;
    const cercano = (px) => {
      let mejor = 0;
      for (let i = 1; i < xs.length; i++) if (Math.abs(xs[i] - px) < Math.abs(xs[mejor] - px)) mejor = i;
      return mejor;
    };
    const activar = (i, cx, cy) => {
      if (limpiezaActual && limpiezaActual !== limpiar) limpiezaActual();
      limpiar();
      limpiezaActual = limpiar;
      activo = i;
      const caja = svg.getBoundingClientRect();
      mostrarTooltip(mostrar(i), cx != null ? cx : caja.left + xs[i], cy != null ? cy : caja.top + 40);
    };
    const alMover = (e) => {
      const caja = svg.getBoundingClientRect();
      activar(cercano(e.clientX - caja.left), e.clientX, e.clientY);
    };
    capa.addEventListener("pointermove", alMover);
    capa.addEventListener("pointerdown", alMover);
    capa.addEventListener("pointerleave", (e) => { if (e.pointerType !== "touch") ocultarTooltip(); });
    svg.addEventListener("focus", () => activar(activo >= 0 ? activo : xs.length - 1));
    svg.addEventListener("blur", ocultarTooltip);
    svg.addEventListener("keydown", (e) => {
      const pasos = { ArrowLeft: -1, ArrowRight: 1, Home: -Infinity, End: Infinity };
      if (!(e.key in pasos)) return;
      e.preventDefault();
      const base = activo < 0 ? xs.length - 1 : activo;
      activar(Math.min(xs.length - 1, Math.max(0, base + pasos[e.key])));
    });
  }

  // Barra con el extremo de datos redondeado (4px) y la base recta
  function rutaBarra(x, yArriba, ancho, alto, positiva) {
    if (alto < 0.5) return "";
    const r = Math.min(4, ancho / 2, alto);
    const yAbajo = yArriba + alto;
    if (positiva) {
      return `M${x},${yAbajo}V${yArriba + r}A${r},${r} 0 0 1 ${x + r},${yArriba}` +
        `H${x + ancho - r}A${r},${r} 0 0 1 ${x + ancho},${yArriba + r}V${yAbajo}Z`;
    }
    return `M${x},${yArriba}V${yAbajo - r}A${r},${r} 0 0 0 ${x + r},${yAbajo}` +
      `H${x + ancho - r}A${r},${r} 0 0 0 ${x + ancho},${yAbajo - r}V${yArriba}Z`;
  }

  function lienzoSvg(cont, descripcion) {
    cont.textContent = "";
    const ancho = Math.max(cont.clientWidth, 240);
    const alto = cont.clientHeight || 250;
    const svg = nodo("svg", { width: ancho, height: alto, viewBox: `0 0 ${ancho} ${alto}`, tabindex: 0, role: "img",
      "aria-label": descripcion + ". Usa las flechas para recorrer los valores." }, cont);
    return { svg, ancho, alto };
  }

  function ejeY(svg, esc, y, x1, x2, formato, conCero) {
    for (const v of esc.marcas) {
      nodo("line", { x1, x2, y1: y(v), y2: y(v), class: v === 0 && conCero ? "eje-linea" : "rejilla" }, svg);
      texto(svg, x1 - 8, y(v), formato(v, esc.paso), "eje-texto", "end");
    }
  }

  function barras(cont, datos, op) {
    if (!datos.length) return vacio(cont, op.vacio);
    const { svg, ancho, alto } = lienzoSvg(cont, op.descripcion);
    const m = MARGEN;
    const valores = datos.map((d) => d.valor);
    const esc = escala(Math.min(0, ...valores), Math.max(0, ...valores), 4);
    const anchoPlot = ancho - m.izquierda - m.derecha;
    const altoPlot = alto - m.arriba - m.abajo;
    const y = (v) => m.arriba + ((esc.max - v) / (esc.max - esc.min)) * altoPlot;
    ejeY(svg, esc, y, m.izquierda, ancho - m.derecha, op.formatoEje, true);

    const banda = anchoPlot / datos.length;
    const grosor = Math.max(1, Math.min(24, banda - 2)); // 2px de aire entre barras vecinas
    const centros = datos.map((_, i) => m.izquierda + banda * (i + 0.5));
    const cada = Math.max(1, Math.ceil(datos.length / Math.max(1, Math.floor(anchoPlot / 70))));
    datos.forEach((d, i) => {
      if (i % cada === 0) texto(svg, centros[i], alto - m.abajo + 16, d.etiqueta, "eje-texto", "middle");
    });

    const y0 = y(0);
    const marcas = datos.map((d, i) => {
      const yv = y(d.valor);
      return nodo("path", {
        d: rutaBarra(centros[i] - grosor / 2, Math.min(yv, y0), grosor, Math.abs(yv - y0), d.valor >= 0),
        class: d.valor >= 0 ? "barra-pos" : "barra-neg",
      }, svg);
    });

    // etiqueta directa solo en el mejor valor
    const iMax = valores.indexOf(Math.max(...valores));
    if (valores[iMax] > 0 && op.formatoEtiqueta) {
      const xEtiqueta = Math.min(Math.max(centros[iMax], m.izquierda + 24), ancho - m.derecha - 24);
      texto(svg, xEtiqueta, y(valores[iMax]) - 9, op.formatoEtiqueta(valores[iMax]), "etiqueta-valor", "middle");
    }

    const capa = nodo("rect", { x: m.izquierda, y: m.arriba, width: anchoPlot, height: altoPlot, class: "capa-impacto" }, svg);
    let resaltada = null;
    const limpiar = () => { if (resaltada) resaltada.classList.remove("activa"); resaltada = null; };
    interaccion(svg, capa, centros, (i) => {
      resaltada = marcas[i];
      resaltada.classList.add("activa");
      return op.tooltip(datos[i]);
    }, limpiar);
  }

  function lineas(cont, series, op) {
    const conDatos = series.map((s) => ({ ...s, puntos: s.puntos.filter((p) => p.v != null && isFinite(p.v)) }));
    const todos = conDatos.flatMap((s) => s.puntos);
    if (!todos.length) return vacio(cont, op.vacio);
    const { svg, ancho, alto } = lienzoSvg(cont, op.descripcion);
    const conEtiquetas = op.etiquetasFinales && ancho >= 440;
    const m = { ...MARGEN, derecha: conEtiquetas ? 96 : MARGEN.derecha };
    const anchoPlot = ancho - m.izquierda - m.derecha;
    const altoPlot = alto - m.arriba - m.abajo;

    const tiempos = todos.map((p) => p.t);
    const tMin = Math.min(...tiempos);
    const tMax = Math.max(...tiempos);
    let vMin = Math.min(...todos.map((p) => p.v));
    let vMax = Math.max(...todos.map((p) => p.v));
    if (op.umbral) { vMin = Math.min(vMin, op.umbral.valor); vMax = Math.max(vMax, op.umbral.valor); }
    if (op.incluirCero) { vMin = Math.min(vMin, 0); vMax = Math.max(vMax, 0); }
    const esc = escala(vMin, vMax, 4);
    const x = (t) => (tMax === tMin ? m.izquierda + anchoPlot / 2 : m.izquierda + ((t - tMin) / (tMax - tMin)) * anchoPlot);
    const y = (v) => m.arriba + ((esc.max - v) / (esc.max - esc.min)) * altoPlot;
    ejeY(svg, esc, y, m.izquierda, ancho - m.derecha, op.formatoEje, esc.min < 0);

    const divisiones = tMax === tMin ? 0 : Math.max(1, Math.floor(anchoPlot / 110));
    for (let i = 0; i <= divisiones; i++) {
      const t = divisiones ? tMin + ((tMax - tMin) * i) / divisiones : tMin;
      const ancla = !divisiones ? "middle" : i === 0 ? "start" : i === divisiones ? "end" : "middle";
      texto(svg, x(t), alto - m.abajo + 16, op.formatoTiempo(t), "eje-texto", ancla);
    }

    if (op.umbral) {
      const yu = y(op.umbral.valor);
      nodo("line", { x1: m.izquierda, x2: ancho - m.derecha, y1: yu, y2: yu, class: "umbral" }, svg);
      texto(svg, ancho - m.derecha - 4, yu - 9, op.umbral.etiqueta, "umbral-texto", "end");
    }

    const base = y(Math.min(Math.max(0, esc.min), esc.max));
    for (const s of conDatos) {
      if (!s.puntos.length) continue;
      const d = s.puntos.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
      if (op.area) {
        const primero = x(s.puntos[0].t).toFixed(1);
        const ultimo = x(s.puntos[s.puntos.length - 1].t).toFixed(1);
        nodo("path", { d: `${d}L${ultimo},${base}L${primero},${base}Z`, class: "area " + s.clase }, svg);
      }
      nodo("path", { d, class: "linea " + s.clase }, svg);
    }

    const finales = conDatos.filter((s) => s.puntos.length).map((s) => {
      const p = s.puntos[s.puntos.length - 1];
      return { s, p, x: x(p.t), y: y(p.v) };
    });
    for (const f of finales) nodo("circle", { cx: f.x, cy: f.y, r: 4, class: "punto " + f.s.clase }, svg);
    if (conEtiquetas) {
      // valor al final de cada línea; si chocan, se deja la leyenda y el tooltip
      const porAltura = [...finales].sort((a, b) => a.y - b.y);
      const separadas = porAltura.every((f, i) => i === 0 || f.y - porAltura[i - 1].y >= 28);
      if (separadas) {
        for (const f of finales) {
          texto(svg, f.x + 10, f.y - 7, op.formatoValor(f.p.v), "etiqueta-valor");
          texto(svg, f.x + 10, f.y + 8, f.s.nombre, "etiqueta-nombre");
        }
      }
    }

    const cruceta = nodo("line", { y1: m.arriba, y2: alto - m.abajo, class: "cruceta", visibility: "hidden" }, svg);
    const activos = conDatos.map((s) => nodo("circle", { r: 4, class: "punto " + s.clase, visibility: "hidden" }, svg));
    const referencia = conDatos.find((s) => s.puntos.length).puntos;
    const xs = referencia.map((p) => x(p.t));
    const capa = nodo("rect", { x: m.izquierda, y: m.arriba, width: anchoPlot, height: altoPlot, class: "capa-impacto" }, svg);
    const limpiar = () => {
      cruceta.setAttribute("visibility", "hidden");
      activos.forEach((c) => c.setAttribute("visibility", "hidden"));
    };
    interaccion(svg, capa, xs, (i) => {
      const t = referencia[i].t;
      cruceta.setAttribute("x1", xs[i]);
      cruceta.setAttribute("x2", xs[i]);
      cruceta.setAttribute("visibility", "visible");
      const filas = [];
      conDatos.forEach((s, k) => {
        const p = s.puntos.find((q) => q.t === t);
        if (!p) return;
        activos[k].setAttribute("cx", x(p.t));
        activos[k].setAttribute("cy", y(p.v));
        activos[k].setAttribute("visibility", "visible");
        filas.push({ valor: op.formatoValor(p.v), nombre: s.nombre, clase: s.clase });
      });
      return { titulo: op.formatoTiempoLargo(t), filas: filas.concat(op.filasExtra ? op.filasExtra(t) : []) };
    }, limpiar);
  }

  function tabla(cont, columnas, filas, mensajeVacio) {
    cont.textContent = "";
    if (!filas.length) {
      const p = document.createElement("p");
      p.className = "nota-tabla";
      p.textContent = mensajeVacio || "Sin datos para este período.";
      cont.appendChild(p);
      return;
    }
    const t = document.createElement("table");
    const encabezado = t.createTHead().insertRow();
    for (const c of columnas) {
      const th = document.createElement("th");
      th.textContent = c.titulo;
      if (c.num) th.className = "num";
      encabezado.appendChild(th);
    }
    const cuerpo = t.createTBody();
    for (const f of filas) {
      const tr = cuerpo.insertRow();
      for (const c of columnas) {
        const td = tr.insertCell();
        const v = c.valor(f);
        if (v instanceof Node) td.appendChild(v);
        else td.textContent = v == null || v === "" ? "—" : v;
        const clases = [c.num ? "num" : "", c.clase || ""].join(" ").trim();
        if (clases) td.className = clases;
      }
    }
    cont.appendChild(t);
  }

  window.addEventListener("scroll", ocultarTooltip, { passive: true });
  document.addEventListener("pointerdown", (e) => {
    if (!(e.target instanceof Element) || !e.target.closest(".lienzo")) ocultarTooltip();
  });

  window.Graficos = { barras, lineas, tabla, ocultarTooltip };
})();
