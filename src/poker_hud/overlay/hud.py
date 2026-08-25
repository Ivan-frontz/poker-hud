"""Renderizado real de las cajas de overlay: una ventana X11 por asiento.

Este módulo es la contraparte "gráfica" de :mod:`poker_hud.overlay.layout`:
crea, para cada asiento de la mesa detectada (T4), una ventanita Tk

- sin bordes ni decoración (``overrideredirect``),
- siempre-encima de la ventana de PokerStars (``wm_attributes('-topmost', ...)``),
- semitransparente (``wm_attributes('-alpha', ...)``, requiere un gestor de
  ventanas con compositor; sin compositor la ventana se ve opaca pero
  sigue funcionando),
- click-through cuando hay soporte de la extensión X Shape vía
  ``python-xlib`` instalado (los clicks del ratón atraviesan la caja y le
  llegan a la mesa que hay debajo, para no interferir con el juego).

y la reposiciona/redimensiona cada vez que la ventana de mesa se mueve o
cambia de tamaño (via polling con ``root.after``, reutilizando la misma
detección de ventana de T4).

*** Por qué este módulo no tiene tests automatizados ***
Crear una ventana Tk real (``tkinter.Tk()``) requiere un servidor X activo
(``$DISPLAY``) y no falla de forma limpia si no lo hay: no hay forma de
instanciarla en un runner de CI headless sin un Xvfb de por medio, y el
click-through de verdad depende además de la extensión X Shape del
servidor concreto. Por eso pytest no puede ejercitar nada de este archivo.
La única lógica de este módulo que es determinista y no depende de X
-dónde va cada caja, qué texto lleva- se extrajo a
:mod:`poker_hud.overlay.layout`, que sí está cubierta por
``tests/test_overlay_layout.py``. Este archivo se limita a leer esos
resultados y pintarlos.

Verificación manual (no automatizable): lanzar ``run()`` con una mesa de
PokerStars real (o cualquier ventana renombrada a un título con pinta de
mesa, ver :mod:`poker_hud.overlay`) corriendo bajo X11/Wine, y comprobar a
ojo que aparece una caja por asiento con las stats correctas, que siguen a
la ventana al moverla o redimensionarla, y que los clicks sobre las cajas
le llegan a la mesa de debajo (no al overlay).

Dependencias del sistema (ninguna es instalable sólo con pip, por eso no
están en ``pyproject.toml`` como dependencias normales):

- ``tkinter``: paquete del sistema operativo (p.ej. ``python3-tk`` en
  Debian/Ubuntu), no viene siempre con el intérprete de Python. Sin él,
  importar este módulo falla; el resto del paquete ``poker_hud.overlay``
  (detección de ventana, :mod:`poker_hud.overlay.layout`) no depende de
  ``tkinter`` y sigue funcionando.
- ``python-xlib`` (``pip install python-xlib``), opcional: sin ella el
  overlay funciona igual pero sin click-through, ver ``_make_click_through``.
"""

from __future__ import annotations

import tkinter as tk
from functools import partial
from typing import Callable

from poker_hud.overlay import PokerTable, find_poker_tables, list_windows
from poker_hud.overlay.layout import DEFAULT_BOX_HEIGHT, DEFAULT_BOX_WIDTH, SeatBox, build_seat_boxes
from poker_hud.stats import PlayerStats, get_player_stats

__all__ = ["SeatBoxWindow", "HudController", "run"]

_BACKGROUND = "#101010"
_FOREGROUND = "#39ff6a"
_ALPHA = 0.80
_POLL_INTERVAL_MS = 1000


class SeatBoxWindow:
    """Una única ventana Tk (``Toplevel``) que representa la caja de un asiento."""

    def __init__(self, master: tk.Misc) -> None:
        self._top = tk.Toplevel(master)
        self._top.overrideredirect(True)
        self._top.attributes("-topmost", True)
        try:
            self._top.attributes("-alpha", _ALPHA)
        except tk.TclError:
            # Sin compositor, algunos gestores de ventanas rechazan -alpha.
            # No es fatal: la caja se queda opaca en vez de semitransparente.
            pass
        self._top.configure(bg=_BACKGROUND)

        self._label = tk.Label(
            self._top,
            bg=_BACKGROUND,
            fg=_FOREGROUND,
            font=("Sans", 9),
            justify="left",
            anchor="w",
        )
        self._label.pack(fill="both", expand=True)

        self._make_click_through()

    def _make_click_through(self) -> None:
        """Intenta hacer la ventana click-through vía la extensión X Shape.

        Requiere ``python-xlib`` (dependencia opcional, no listada en
        ``pyproject.toml`` porque el resto del proyecto no la necesita).
        Si no está instalada, o el servidor X no soporta la extensión
        Shape, la caja se queda visible pero capturando el ratón: se
        documenta como limitación conocida en vez de fallar.
        """

        try:
            from Xlib.display import Display
            from Xlib.ext import shape
        except ImportError:
            return

        try:
            display = Display()
            window_id = self._top.winfo_id()
            xlib_window = display.create_resource_object("window", window_id)
            if not display.has_extension("SHAPE"):
                return
            # Región de input vacía == ningún click aterriza en esta ventana.
            xlib_window.shape_select_input(0)
            shape.set_bounding_shape(xlib_window, [])
            display.sync()
        except Exception:
            # Cualquier fallo de la extensión Shape es no-fatal: peor caso,
            # la caja no es click-through pero sigue mostrando las stats.
            pass

    def update(self, box: SeatBox) -> None:
        self._label.configure(text=box.text)
        self._top.geometry(f"{box.width}x{box.height}+{box.x}+{box.y}")

    def destroy(self) -> None:
        self._top.destroy()


class HudController:
    """Orquesta una :class:`SeatBoxWindow` por asiento y las mantiene al día.

    Desacoplado a propósito de dónde salen los datos en tiempo real:

    - ``find_table``: cómo localizar la :class:`~poker_hud.overlay.PokerTable`
      a mostrar (por defecto, ``wmctrl`` vía T4).
    - ``get_current_players``: nº de asiento -> ``screen_name`` sentado ahí
      para la mano en curso (lo produce el parser/watcher de T1/T3 a partir
      del último ``Hand`` visto; se inyecta porque el overlay no sabe leer
      hand history por sí mismo).
    - ``get_stats``: ``screen_name`` -> :class:`~poker_hud.stats.PlayerStats`
      (por defecto, el motor de T2 sobre la conexión SQLite dada).
    """

    def __init__(
        self,
        get_current_players: Callable[[], dict[int, str]],
        stats_conn,
        find_table: Callable[[], PokerTable | None] | None = None,
        get_stats: Callable[[str], PlayerStats | None] | None = None,
        box_width: int = DEFAULT_BOX_WIDTH,
        box_height: int = DEFAULT_BOX_HEIGHT,
        poll_interval_ms: int = _POLL_INTERVAL_MS,
    ) -> None:
        self._get_current_players = get_current_players
        self._find_table = find_table or _default_find_table
        self._get_stats = get_stats or partial(get_player_stats, stats_conn)
        self._box_width = box_width
        self._box_height = box_height
        self._poll_interval_ms = poll_interval_ms

        self._root = tk.Tk()
        self._root.withdraw()  # la ventana raíz no se muestra, sólo las cajas
        self._boxes: dict[int, SeatBoxWindow] = {}

    def start(self) -> None:
        self._refresh()
        self._root.mainloop()

    def stop(self) -> None:
        self._root.quit()

    def _refresh(self) -> None:
        table = self._find_table()
        if table is not None:
            seat_players = self._get_current_players()
            boxes = build_seat_boxes(
                table.geometry,
                max(seat_players, default=0) or _DEFAULT_MAX_SEATS,
                seat_players,
                self._get_stats,
                self._box_width,
                self._box_height,
            )
            self._sync_boxes(boxes)
        else:
            self._clear_boxes()

        self._root.after(self._poll_interval_ms, self._refresh)

    def _sync_boxes(self, boxes: list[SeatBox]) -> None:
        seen = set()
        for box in boxes:
            seen.add(box.seat)
            if not box.text:
                if box.seat in self._boxes:
                    self._boxes.pop(box.seat).destroy()
                continue
            if box.seat not in self._boxes:
                self._boxes[box.seat] = SeatBoxWindow(self._root)
            self._boxes[box.seat].update(box)

        for seat in list(self._boxes):
            if seat not in seen:
                self._boxes.pop(seat).destroy()

    def _clear_boxes(self) -> None:
        for window in self._boxes.values():
            window.destroy()
        self._boxes.clear()


_DEFAULT_MAX_SEATS = 9


def _default_find_table() -> PokerTable | None:
    tables = find_poker_tables(list_windows())
    return tables[0] if tables else None


def run(get_current_players: Callable[[], dict[int, str]], stats_conn) -> None:
    """Arranca el overlay con la configuración por defecto y bloquea hasta cerrarlo."""

    HudController(get_current_players, stats_conn).start()
