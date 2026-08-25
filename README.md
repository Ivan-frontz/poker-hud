# poker-hud

HUD casero y gratuito para PokerStars, alternativa personal a PokerTracker 4.

Basado en el mismo enfoque que usan PT4/HM3: parsea los hand history que
PokerStars ya escribe en disco (no lee memoria del proceso ni hace screen
scraping), calcula stats por jugador y las muestra en un overlay sobre la
mesa.

## Alcance v1

- Cash game, una mesa.
- Cliente PokerStars corriendo bajo Wine en Linux.
- Stats básicas por asiento: manos, VPIP, PFR, 3-bet.
- Sin popups ni multi-tabla todavía (fases posteriores).

## Componentes

- `parser`: vigila la carpeta de hand history y parsea cada mano a una
  representación estructurada.
- `stats`: motor de cálculo incremental de stats por jugador, persistido en
  SQLite.
- `overlay`: detección de la ventana de mesa (vía `xdotool`/`wmctrl` sobre
  Wine) y renderizado de las cajas de stats por asiento (X11, click-through,
  siempre-encima).

Proyecto gestionado vía [panel-tareas](https://github.com/Ivan-frontz/Panel_tareas_automatizado).
