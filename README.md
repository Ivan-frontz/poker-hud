# poker-hud

HUD casero y gratuito para PokerStars, alternativa personal a PokerTracker 4.

Basado en el mismo enfoque que usan PT4/HM3: parsea los hand history que
PokerStars ya escribe en disco (no lee memoria del proceso ni hace screen
scraping), calcula stats por jugador y las muestra en un overlay sobre la
mesa.

## Alcance v1

- Torneos (no cash), una mesa.
- Cliente PokerStars corriendo bajo Wine en Linux.
- Stats básicas por asiento: manos, VPIP, PFR, 3-bet.
- Sin popups ni multi-tabla todavía (fases posteriores).

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
  siempre-encima).

Proyecto gestionado vía [panel-tareas](https://github.com/Ivan-frontz/Panel_tareas_automatizado).
