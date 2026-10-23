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
- 🛡️ **Verificación de identidad (anti-triangulación)**: compara los datos del banco
  con la identidad que guardaste de la contraparte en Binance y te avisa **✅ coincide /
  ⚠️ posible tercero** al pagar y al liberar.

**NO se automatiza (a propósito):**
- 🚫 **Mover dinero desde el Banco de Venezuela.** El BDV **no tiene una API pública**
  para que un programa haga pagos o Pago Móvil por ti. Automatizar eso implicaría
  controlar la BDVApp por fuera (frágil, contra los términos del banco y vía rápida a
  que te bloqueen la cuenta). En su lugar, el panel **arma el bloque de "Pegar Datos"**
  (banco, teléfono/cédula, monto y concepto) y te lo **manda por Telegram**: lo pegas en
  tu BDVApp, confirmas con tu clave y envías. Rápido, pero el pago lo haces tú.
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

#### WhatsApp (opcional, gratis con CallMeBot)

Los mismos avisos de Telegram pueden llegar a tu WhatsApp. Desde tu WhatsApp escribe
`I allow callmebot to send me messages` al **+34 644 51 95 23**; te responde con una
apikey. Ponla en `[alertas]` (`whatsapp_telefono` con código de país y solo dígitos, p. ej.
`584141234567`, y `whatsapp_apikey`) o en las variables `WHATSAPP_TELEFONO` / `WHATSAPP_APIKEY`.

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

### Anuncios a mano (sin API de comerciante)

Si no puedes dar permiso de escritura a la API, usa `modo = "manual"` en `[anuncios]`.
El bot calcula el precio ideal de tus anuncios, la tarjeta **"Crear tus anuncios a mano"**
te da los valores exactos (precio, cantidad, límites, métodos de pago) con un botón de
copiar, y cuando conviene cambiar el precio te avisa por Telegram/WhatsApp para que lo
edites en **P2P → Mis anuncios**. Los avisos se controlan con `aviso_cambio_pct` y
`aviso_cada_min`.

## Uso diario (paso a paso)

1. **Abre el panel** y pulsa **Iniciar operación**: pon tu capital y si empiezas en
   bolívares o en USDT.
2. Mira **"Qué hacer ahora"** en la tarjeta de sesión: te dice si comprar, vender o
   esperar, con el precio objetivo.
3. Deja el **repositor de anuncios** encendido (o pulsa *Calcular ahora*) para liderar
   el mercado con tu margen mínimo.
4. Cuando **compras**: en *Preparar pago* eliges la orden, el bloque llega a tu
   **Telegram**, lo pegas en **"Pegar Datos"** de la BDVApp y pagas con tu clave. Si el
   beneficiario no coincide con la contraparte, el sistema te frena.
5. Cuando **vendes**: el pago entra por SMS, se concilia y se verifica el titular; la
   orden aparece en **"listas para liberar"** y **tú liberas** en Binance.
6. Al terminar, pulsa **Cerrar sesión**: deja tu saldo en **USDT** (el panel te avisa si
   te queda algo en bolívares) y guarda el rendimiento del día.

Para **detener todo** en cualquier momento (vas a comer, te vas a dormir, el mercado se
puso feo): el botón **⏸ Pausar operatividad** baja tus anuncios y deja de mover precios.
**▶ Reanudar operatividad** los vuelve a publicar. El estado es el mismo en todos tus
dispositivos.

## Modo Real y modo Simulación

El panel tiene dos modos, con el selector **Real | Simulación** arriba a la derecha:

- **Real** (`/`): tu base de datos, tu cuenta de Binance, el bot de anuncios y los avisos por
  Telegram/WhatsApp.
- **Simulación** (`/sim/`): datos de ejemplo en su propia base (`datos/demo.db`), con el
  **simulador de ciclos** (capital, ciclos, rol, ruta). Con **Aplicar al panel** crea esos
  ciclos como órdenes simuladas, y con **Reiniciar simulación** empieza de cero con el capital
  que elijas. Nunca toca Binance ni te manda avisos.

Se apaga con `simulacion = false` en `[panel]`.

## Desde tu teléfono y tu PC

El panel es **un solo servidor** con una sola base de datos: el teléfono y la PC solo son
navegadores que se conectan a él. Lo que haces en uno (pausar, iniciar sesión, preparar un
pago) aparece al instante en el otro. Córrelo en un equipo que esté siempre encendido (tu
PC o un VPS barato) y conéctate desde donde quieras:

- **Misma red WiFi (lo más fácil):** en `config.toml` pon `host = "0.0.0.0"` y una
  `clave` en `[panel]`. Desde el teléfono entra a `http://IP-DE-TU-PC:8000` (la IP local,
  p. ej. `192.168.1.50`).
- **Desde la calle (recomendado):** usa un túnel seguro como **Tailscale** (crea una red
  privada entre tus equipos) o **Cloudflare Tunnel** / **ngrok** (te da una URL https).
  Así entras por internet sin abrir puertos en tu router.
- **Siempre con clave.** Si el panel sale de `127.0.0.1`, define `panel.clave` (o la
  variable `PANEL_CLAVE`); el programa se niega a exponerse sin clave. Idealmente detrás
  de https (el túnel ya te lo da).
- Para recibir los **avisos y el bloque de pago en el teléfono**, configura Telegram en
  `[alertas]`: te llegan a la app donde sea que estés, sin depender del panel abierto.

## Sesiones de operación (empezar y cerrar la jornada)

En el panel, la tarjeta **"Sesión de operación"** te deja decirle al sistema con cuánto
empiezas y en qué moneda (**bolívares o USDT**). A partir de ahí:

- Mide tu **rendimiento** contra ese capital inicial (en USDT y en Bs).
- Te dice **qué hacer ahora** (comprar / vender / esperar) según lo que tengas en mano
  y el margen del momento, para sacarle el mayor provecho a la jornada.
- Vigila tu **composición**: cuánto está resguardado en USDT y cuánto sigue expuesto en
  bolívares. Como el bolívar se devalúa, te recomienda **cerrar el día en USDT**:
  convierte el saldo en Bs antes de cerrar.
- Al pulsar **"Cerrar sesión"** guarda el resultado y lo suma al **historial** para que
  compares jornadas. Pensado para una operatividad **diaria**, pero sirve para cualquier período.

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
- **Preparar pago para la BDVApp**: eliges la orden de compra por pagar (prellena el
  monto), pones los datos del vendedor (o pegas su bloque de la orden) y el panel arma
  el texto para **"Pegar Datos"** y te lo manda por Telegram. Tú pagas en la app.
- **Contrapartes**: guarda la identidad verificada (nombre, cédula, teléfonos) de cada
  apodo de Binance con el que operas. El panel la usa para verificar que el banco
  coincida antes de pagar y antes de liberar.
- **Simulador de ciclos**: pon capital, moneda y número de ciclos; proyecta la ganancia
  (con o sin reinvertir) usando los precios de mercado y tus costos. No guarda nada.
- **Formularios**: registrar una orden a mano y pegar un SMS del banco.
- **Pausar / reanudar operatividad**: un botón que baja todos tus anuncios (fuera de
  línea) y los vuelve a publicar. El estado se comparte entre el teléfono y la PC.
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
