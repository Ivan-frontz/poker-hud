# poker-hud

HUD casero y gratuito para PokerStars, alternativa personal a PokerTracker 4.

Basado en el mismo enfoque que usan PT4/HM3: parsea los hand history que
PokerStars ya escribe en disco (no lee memoria del proceso ni hace screen
scraping), calcula stats por jugador y las muestra en un overlay sobre la
mesa.

## Alcance v1

- Torneos (no cash), multi-mesa: el overlay sigue todas las mesas de
  torneo abiertas a la vez.
- Cliente PokerStars corriendo bajo Wine en Linux.
- Stats básicas por asiento: manos, VPIP, PFR, 3-bet.
- Sin popups todavía (fases posteriores).

El hand history de torneo trae ciegas que suben por niveles, ante a partir de
cierto nivel, importes de fichas sin símbolo de moneda y eliminaciones de
jugadores — el parser lo tiene en cuenta.

## Componentes

- `parser`: parsea cada mano de un hand history de torneo a una
  representación estructurada.
- `watcher`: vigila por polling la carpeta de hand history de torneos,
  detecta manos nuevas y completas (y ficheros nuevos, uno por torneo) y
  las envía al parser y al motor de stats.
- `stats`: motor de cálculo incremental de stats por jugador, persistido en
  SQLite.
- `overlay`: detección de la ventana de mesa (vía `xdotool`/`wmctrl` sobre
  Wine) y renderizado de las cajas de stats por asiento (X11, click-through,
  siempre-encima). El cálculo de dónde va cada caja y qué texto muestra
  (`overlay.layout`, testeado) está separado del renderizado real con
  Tkinter/X Shape (`overlay.hud`, requiere servidor X y no es testeable
  por pytest). La posición automática es una aproximación geométrica (una
  elipse sobre la geometría de la mesa); cada caja se puede arrastrar a
  mano a su posición real y esa posición se recuerda entre sesiones
  (`overlay.positions`, ver "Ajustar la posición de las cajas a mano" más
  abajo).
- `app` (`python -m poker_hud`): punto de entrada único que cablea las
  piezas anteriores en un solo proceso — arranca el watcher en un hilo y
  el overlay en el hilo principal, compartiendo la conexión SQLite de
  stats y la alineación de asientos de la mano en curso.

## Instalación y puesta en marcha

### 1. Requisitos

- **Linux con Wine** corriendo el cliente de PokerStars (X11, no Wayland
  puro: la detección de ventana y el overlay dependen de herramientas de
  X11). Bajo Wayland puede funcionar vía XWayland, pero no está probado.
- **`wmctrl`** instalado y en el `PATH` (usado por `overlay` para localizar
  la ventana de la mesa, T4). En Debian/Ubuntu: `sudo apt install wmctrl`.
- **`python3-tk`** (el paquete del sistema, no de pip) para que
  `overlay.hud` pueda crear las ventanas de las cajas de stats. En
  Debian/Ubuntu: `sudo apt install python3-tk`.
- Python 3.10+.
- Opcional: **`python-xlib`** (`pip install python-xlib`) para que las
  cajas del overlay sean *click-through* (los clicks les llegan a la mesa
  de debajo en vez de quedarse en la caja), salvo la manija de arrastre de
  su esquina (ver "Ajustar la posición de las cajas a mano" más abajo).
  Sin ella el HUD funciona igual, pero las cajas capturan el ratón
  siempre.

### 2. Instalar el paquete

```bash
pip install -e .
# opcional, para click-through:
pip install python-xlib
```

### 3. Activar el guardado de hand history en PokerStars

El HUD lee del disco los hand history que el propio cliente escribe: hay
que activarlo primero, no viene activado por defecto.

1. Abre PokerStars (bajo Wine) → menú **Configuración/Settings**.
2. Ve a **Historial de manos / Hand History** (o **History** según el
   idioma del cliente).
3. Marca la opción de guardar el historial de manos (**"Guardar el
   historial de manos"** / **"Save my hand history"**).
4. Marca también el historial de **torneos** específicamente si aparece
   como opción separada de la de mesas de dinero real (cash): v1 de este
   HUD sólo procesa torneos, y PokerStars guarda cash y torneos en
   carpetas distintas.
5. Anota (o cambia) la carpeta de destino que se muestra en esa misma
   pantalla — es la ruta que hay que pasarle al HUD en el paso siguiente.

Bajo Wine, esa carpeta suele quedar dentro del prefijo, con una ruta del
estilo:

```
~/.wine/drive_c/users/<tu-usuario>/AppData/Local/PokerStars/HandHistory/<tu-nick>/
```

pero puede variar según la versión del cliente y el prefijo de Wine
usado — la ruta exacta mostrada en el paso 5 es la fuente de verdad.

### 4. Lanzar el HUD

Con PokerStars ya abierto (aunque sea sólo en el lobby) y la carpeta de
hand history de torneos localizada:

```bash
python -m poker_hud --hand-history-dir "/ruta/a/HandHistory/tu-nick/Torneos"
```

Esto arranca en un único proceso el watcher de hand history (T3, sondea
la carpeta y actualiza stats), y el overlay (T5, detecta las ventanas de
mesa y dibuja las cajas de stats por asiento) — se queda corriendo hasta
que se cierra con Ctrl+C. Al sentarte en una mesa de torneo, el overlay
debería localizarla automáticamente y empezar a mostrar cajas por
asiento en cuanto haya al menos una mano jugada de cada jugador. Si te
anotás a más de un torneo a la vez, el overlay sigue todas las mesas
abiertas simultáneamente, cada una con sus propias cajas.

Argumentos opcionales:

- `--db-path RUTA`: fichero SQLite donde persisten las stats entre
  sesiones (por defecto `~/.local/share/poker-hud/stats.db`).
- `--poll-interval SEGUNDOS`: frecuencia de sondeo de la carpeta de hand
  history (por defecto 2.0).
- `--tournament-id ID`: fija el HUD a la mesa de este único torneo,
  ignorando cualquier otra mesa de PokerStars abierta a la vez. Sin este
  flag (el caso normal), el overlay sigue **todas** las mesas de torneo
  detectadas simultáneamente. Usa este flag sólo si querés excluir
  mesas que no te interesan. Si el ID no coincide con ninguna mesa
  abierta en ese momento, el HUD simplemente no muestra cajas hasta que
  esa mesa aparezca.
- `--opacity FLOAT`: opacidad de las cajas del HUD, de 0.0 (invisible) a
  1.0 (opaca), por defecto 0.32 (bastante transparente, para tapar lo
  menos posible la mesa de detrás sin dejar de leer las stats). Valores
  fuera de ese rango se rechazan con un error al arrancar. Requiere un
  gestor de ventanas con compositor activo; sin uno Tk puede ignorar el
  valor y la caja queda opaca igual, sin que sea un error.

### 5. Ajustar la posición de las cajas a mano

La posición por defecto de cada caja la calcula el HUD geométricamente
(una elipse alrededor de la mesa) y no siempre coincide con dónde está
cada asiento en el fieltro de la mesa/tema visual concreto. Para
corregirla:

Con el HUD corriendo y las cajas visibles sobre la mesa, cada caja tiene
una pequeña manija amarilla ("✛") fija en su esquina superior derecha.
Arrastra esa manija con el botón izquierdo del ratón hasta la posición que
prefieras y soltá — no hace falta ningún atajo de teclado ni modo previo.
El resto de la caja sigue siendo click-through en todo momento (los
clicks fuera de la manija le siguen llegando a la mesa de debajo, no
interfieren con el juego). Sin `python-xlib` instalada no hay
click-through que proteger, así que en ese caso se puede arrastrar desde
cualquier punto de la caja, no sólo la manija.

La posición de cada asiento se guarda automáticamente al soltar el
arrastre, en `seat_positions.json` junto al fichero de stats (mismo
directorio que `--db-path`, por defecto
`~/.local/share/poker-hud/seat_positions.json`) — no hace falta guardar
nada más ni hay un paso explícito de "guardar". Un asiento que nunca se
ajustó a mano sigue usando la posición calculada automáticamente, y eso
no cambia al reiniciar el HUD. Con varias mesas abiertas a la vez, la
posición ajustada se guarda por mesa: arrastrar el asiento 3 de un
torneo no afecta al asiento 3 de otro. Mientras se está arrastrando una
caja, el HUD deja de refrescar hasta soltarla, para no pelearle la caja
al ratón a mitad de un arrastre.

Proyecto gestionado vía [panel-tareas](https://github.com/Ivan-frontz/Panel_tareas_automatizado).
