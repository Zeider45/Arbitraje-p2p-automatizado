# Arbitraje P2P USDT/VES — panel y automatización

Panel de control y bots para hacer arbitraje P2P de **USDT/bolívares** en Binance,
pensado para operar con **Banco de Venezuela (BDV)** y **Pago Móvil**. Reúne en un
solo lugar el escáner de precios, la contabilidad de tus ganancias, la conciliación
de los pagos del banco y el reposicionamiento automático de tus anuncios.

> **Qué es el arbitraje P2P aquí:** publicas un anuncio para **comprar** USDT barato y
> otro para **vender** USDT caro. La diferencia entre ambos precios, menos las
> comisiones de Binance y el costo del banco, es tu ganancia por cada "vuelta". El
> panel calcula ese margen en vivo y, si lo activas, mueve tus anuncios para que
> queden de primeros sin bajar de la ganancia mínima que tú fijes.

---

## ⚠️ Lee esto primero: qué se automatiza y qué no

Hay una parte que **sí** se puede automatizar de forma segura y legal, y otra que
**no**. Este proyecto es honesto con esa línea:

**Se automatiza (con tu API key):**
- 📈 **Escaneo del mercado** P2P de Binance (precios, mejores anuncios, spread, margen neto).
- 🔔 **Alertas** por Telegram cuando hay oportunidad, y resumen diario.
- 📥 **Importación de tus órdenes** desde Binance (API de **solo lectura**).
- 🧮 **Contabilidad** de ganancias por ciclos FIFO, ROI, volumen, posición abierta.
- 🏷️ **Reposicionamiento de tus anuncios**: mueve el precio de tu anuncio de compra y
  de venta para liderar, respetando tu margen mínimo. Usa la API oficial
  "C2C Agent" de Binance (`ads/update`, `ads/updateStatus`) y requiere **cuenta de
  comerciante** y API key con permiso de escritura.
- 🧾 **Conciliación de pagos** del BDV: lee los SMS/notificaciones que tu teléfono le
  reenvía y los casa con la orden de Binance correspondiente.

**NO se automatiza (a propósito):**
- 🚫 **Mover dinero desde el Banco de Venezuela.** El BDV **no tiene una API pública**
  para que un programa haga pagos o Pago Móvil por ti. Automatizar eso implicaría
  controlar la BDVApp por fuera (frágil, contra los términos del banco y vía rápida a
  que te bloqueen la cuenta). El panel **prepara** el pago (monto, teléfono/cédula,
  referencia) para que tú lo confirmes en tu BDVApp en segundos, pero el pago lo haces tú.
- 🚫 **Liberar USDT automáticamente.** Liberar sin verificar es exactamente la puerta
  de la estafa de triangulación (otra persona paga por el comprador) y va contra las
  reglas de Binance. El panel te muestra la orden **"lista para liberar"** con el pago
  ya casado, pero **tú** confirmas en tu BDVApp que el titular coincide y liberas en Binance.

En resumen: el bot **vigila el mercado, mueve tus precios y te cuadra las cuentas**;
el **movimiento de dinero y la liberación de cripto los confirmas tú**. Esa frontera
no es una limitación técnica por pereza: es lo que te protege de fraudes y de perder la cuenta.

---

## Puesta en marcha

### Opción rápida: modo demo (sin claves, datos simulados)

Para ver el panel funcionando con 45 días de datos de ejemplo:

```bash
# Linux / macOS
./iniciar.sh --demo

# Windows
iniciar.bat --demo
```

Abre <http://localhost:8000>. Todo lo que verás es **simulado** (precios, órdenes,
pagos) y vive en `datos/demo.db`, aparte de tus datos reales.

### Instalación manual

Requiere **Python 3.11+**.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.toml config.toml  # edítalo
python -m arbitraje                 # abre el panel
```

---

## Configuración

Todo se controla desde `config.toml` (copia `config.example.toml`). Los **secretos**
mejor ponlos como variables de entorno: `BINANCE_API_KEY`, `BINANCE_API_SECRET`,
`TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, `WEBHOOK_TOKEN`, `PANEL_CLAVE`.

### 1. Conectar Binance (importar tus órdenes)

1. En Binance: **Administración de API** → crea una API key.
2. Deja **solo** "Enable Reading" (lectura). No actives retiros ni trading.
3. Restringe por **IP** la de tu servidor.
4. Ponlas en variables de entorno o en `[binance]` de `config.toml`.

Comprueba la conexión:

```bash
python -m arbitraje sincronizar      # descarga tu historial una vez
python -m arbitraje mercado          # precios y margen actuales
python -m arbitraje metodos          # identificadores de métodos de pago para [mercado]
```

### 2. Alertas por Telegram (opcional)

1. Crea un bot con [@BotFather](https://t.me/BotFather) → copia el **token**.
2. Escríbele a tu bot y saca tu **chat id** con [@userinfobot](https://t.me/userinfobot).
3. Ponlos en `[alertas]`. Ajusta `spread_minimo_pct` y `resumen_diario_hora`.

### 3. Conciliación automática de pagos del BDV

El panel puede recibir tus SMS/notificaciones de pago y casarlos con tus órdenes.
No existe una API del banco para esto, así que se usa tu propio teléfono como puente:

1. Define un `WEBHOOK_TOKEN` (una cadena secreta larga).
2. Instala en tu teléfono una app de **reenvío de SMS a URL** (por ejemplo "SMS
   Forwarder" o similar) y configúrala para que, cuando llegue un SMS del BDV, haga
   un `POST` a:

   ```
   http://TU_SERVIDOR:8000/api/banco/sms?token=EL_TOKEN
   ```

   Puede mandar el texto como JSON (`{"texto": "..."}`), formulario o texto plano.
3. ¿El SMS de tu banco tiene otro formato? Pruébalo sin guardarlo:

   ```bash
   python -m arbitraje probar-sms "Recibiste un PagomovilBDV por Bs. 1.500,00 ..."
   ```

   Si no lo reconoce, ajusta los patrones en `[banco]` (`patron_entrada`,
   `patron_monto`, etc.). También puedes pegar un SMS a mano desde el panel.

### 4. Reposicionamiento automático de anuncios

> Para mover precios de verdad necesitas **cuenta de comerciante verificada** en
> Binance P2P y una API key con **permiso de escritura**. Empieza siempre en
> `modo = "simular"`.

En `[anuncios]`:

```toml
automatico = true          # enciende el repositor
modo = "simular"           # primero simula; cuando confíes, pon "real"
margen_minimo_pct = 0.8    # nunca publica por debajo de este neto por vuelta
lados = ["COMPRA", "VENTA"]
ruta = "BDV"               # con qué ruta bancaria calcula el margen
```

- En **simular**, el panel calcula y registra qué anuncios publicaría, pero **no toca
  Binance**. Ideal para ganar confianza.
- En **real**, mueve el precio de tus anuncios ya existentes. Si el mercado se cierra
  y liderar implicaría perder, **ensancha** tus precios para conservar el margen
  mínimo en vez de perseguir al rival. No crea anuncios desde cero (eso lo haces tú
  una vez en Binance, para evitar publicar algo mal por un error de configuración).
- El botón **"Calcular ahora"** del panel te muestra el plan en cualquier momento.

---

## El panel

- **Indicadores**: ganancia neta del período (USDT y Bs), ganancia de hoy, ROI,
  spread capturado, volumen, ciclos y órdenes. Filtros por hoy / 7 / 30 / 90 días / todo.
- **Mercado ahora**: mejor precio para comprar y vender, brecha y **margen neto** por
  ruta (BDV→BDV e interbancario), con el precio sugerido para quedar de primero.
- **Tu operación**: medidor del límite diario interbancario, posición abierta y su
  valor a mercado, mejores días, rotación del capital.
- **Automatización de anuncios**: estado del repositor, precios propuestos, margen y
  registro de lo que hizo o haría.
- **Gráficos**: ganancia por día, ganancia acumulada, precios de compra/venta y
  margen neto por vuelta (con tu umbral). Todos tienen tooltip, teclado y "Ver tabla".
- **Tablas**: órdenes (con la ruta de pago editable), ciclos, banco (conciliación y
  **"listas para liberar"**) y eventos. Exportables a CSV.
- **Formularios**: registrar una orden a mano y pegar un SMS del banco.
- **Tema claro/oscuro** y diseño adaptado a teléfono.

### Seguridad del panel

- Por defecto escucha en `127.0.0.1` (solo tu equipo).
- Si lo expones a la red (`host = "0.0.0.0"`), **define `panel.clave`** (o
  `PANEL_CLAVE`): el panel pide usuario y clave. El programa se niega a exponerse sin clave.
- Lo ideal para acceder desde fuera es un túnel/VPN, no abrir el puerto a internet.

---

## Cómo se calculan las ganancias

Cada orden completada se convierte en un flujo neto (USDT y Bs, ya con comisiones y
costo del banco). Compras y ventas se emparejan por orden de llegada (**FIFO**), sin
importar si empezaste comprando o vendiendo. Cada pareja es un **ciclo**, y su
ganancia es `USDT × (precio efectivo de venta − precio efectivo de compra)`. Lo que
queda sin emparejar es tu **posición abierta**, que se valora al precio de mercado.

Los costos se configuran en `[costos]`: comisión de anunciante/tomador, costo
interbancario (BDV→otro banco) y, para BDV→BDV, normalmente 0. Como entre cuentas BDV
no hay límite y el interbancario sí (2.500.000 Bs/día por defecto), el panel lleva
ambos por separado.

---

## Ejecutar con Docker

```bash
docker build -t arbitraje-p2p .
docker run -d --name arbitraje -p 8000:8000 \
  -v "$PWD/datos:/datos" \
  -e BINANCE_API_KEY=... -e BINANCE_API_SECRET=... \
  -e PANEL_CLAVE=una-clave-fuerte \
  arbitraje-p2p
```

---

## Desarrollo y pruebas

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Estructura:

```
arbitraje/
  config.py        configuración (TOML + variables de entorno)
  db.py            SQLite (órdenes, mercado, banco, eventos)
  mercado.py       escáner P2P público + cálculo de spread/margen
  binance_api.py   cliente firmado de SOLO LECTURA (historial de órdenes)
  anuncios.py      repositor de anuncios (API C2C Agent de Binance)
  ganancias.py     emparejado FIFO, ciclos, KPIs, series diarias
  banco.py         lectura de SMS del BDV y conciliación
  notificaciones.py alertas por Telegram
  tareas.py        bucles en segundo plano (el "motor")
  servidor.py      API del panel (FastAPI) + webhook de SMS
  demo.py          datos simulados para el modo demo
  web/             el panel (HTML, CSS y JS sin dependencias externas)
tests/             pruebas con pytest
```

---

## Aviso

Herramienta de apoyo para operar tu propia cuenta; **no es asesoría financiera**. El
arbitraje P2P tiene riesgos (precio, bloqueos bancarios, fraudes de contraparte,
cambios regulatorios). Cumple los términos de Binance y del Banco de Venezuela, la
normativa de la Sudeban/BCV y tus obligaciones fiscales. Tú eres responsable de tus
operaciones y de verificar cada pago antes de liberar cripto.
