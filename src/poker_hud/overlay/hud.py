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
  llegan a la mesa que hay debajo, para no interferir con el juego),
  salvo una pequeña manija fija en la esquina superior derecha (ver
  siguiente sección) que nunca es click-through.

y la reposiciona/redimensiona cada vez que la ventana de mesa se mueve o
cambia de tamaño (via polling con ``root.after``, reutilizando la misma
detección de ventana de T4).

*** Arrastrar cajas con el ratón: manija fija por caja (T19) ***
T16 introdujo el arrastre de cajas, pero como el click-through de arriba
hace que ningún click normal aterrice en la caja, hacía falta activar
antes un "modo edición" explícito con la tecla **F9** (T16, con un intento
de atajo *de verdad* global vía ``XGrabKey`` en T17). En la práctica ese
atajo global no llegó a dispararse en el entorno real de Ivan incluso tras
el fix de T17 (el ``XGrabKey`` aislado funcionaba en pruebas pero no
dentro de la app completa; la causa concreta -¿timing entre el hilo de
Xlib y el mainloop de Tk?, ¿otra app con F9 agarrada?- no se identificó) y
además dependía de que ningún otro programa tuviera esa tecla agarrada
antes, algo fuera de nuestro control. T19 reemplaza todo ese mecanismo por
uno más simple y sin modos: cada :class:`SeatBoxWindow` tiene una manija
fija (``self._handle``, un pequeño ``tk.Label`` con el símbolo "✛") en su
esquina superior derecha, del tamaño de ``_HANDLE_SIZE`` píxeles, que
**nunca** es click-through -a diferencia del resto de la caja, que sigue
siéndolo siempre, sin alternar-. Arrastrar esa manija con el botón
izquierdo mueve la caja directamente (mismos ``_on_drag_start`` /
``_on_drag_motion`` / ``_on_drag_end`` de T16, que ya calculan el arrastre
en coordenadas absolutas de pantalla y llaman a ``on_position_changed``
para persistir el offset). No hace falta ningún modo global ni congelar el
refresco periódico: la manija es clickeable en todo momento, y como el
resto de la caja sigue siendo click-through en todo momento, un click
fuera de la manija sigue llegándole a la mesa de debajo sin más lógica.

Sin ``python-xlib`` (mismo caso que sin click-through en general, ver
``_init_click_through``) no hay región de input que restringir: la
ventana entera ya captura todos los clicks todo el tiempo, así que la
manija sería redundante -arrastrar funciona igual desde cualquier punto
de la caja, no sólo la esquina-. En ese caso el arrastre se activa sobre
toda la caja (``self._text``) en vez de sólo la manija; se documenta como
diferencia de comportamiento conocida, no como bug.

*** Por qué este módulo no tiene tests automatizados ***
Crear una ventana Tk real (``tkinter.Tk()``) requiere un servidor X activo
(``$DISPLAY``) y no falla de forma limpia si no lo hay: no hay forma de
instanciarla en un runner de CI headless sin un Xvfb de por medio, y el
click-through de verdad depende además de la extensión X Shape del
servidor concreto. Por eso pytest no puede ejercitar nada de este archivo.
La única lógica de este módulo que es determinista y no depende de X
-dónde va cada caja, qué texto lleva, y qué posición usar si el asiento
tiene un offset guardado a mano (T16)- se extrajo a
:mod:`poker_hud.overlay.layout` y :mod:`poker_hud.overlay.positions`, que
sí están cubiertas por tests (``tests/test_overlay_layout.py``,
``tests/test_overlay_positions.py``). Este archivo se limita a leer esos
resultados y pintarlos, y a reaccionar a los eventos de ratón de Tk. El
gesto de arrastre en sí (manija de esquina, ver arriba) sólo se verificó
a mano contra una mesa real, no con un test automatizado: quien revise
T19 no debería esperar cobertura de pytest para eso.

Verificación manual (no automatizable): lanzar ``run()`` con una mesa de
PokerStars real (o cualquier ventana renombrada a un título con pinta de
mesa, ver :mod:`poker_hud.overlay`) corriendo bajo X11/Wine, y comprobar a
ojo que aparece una caja por asiento con las stats correctas, que siguen a
la ventana al moverla o redimensionarla, que un click y arrastre sobre la
manija ("✛" en la esquina superior derecha de la caja) la mueve y esa
posición sobrevive al siguiente refresco y a reiniciar el HUD, y que un
click en cualquier otro punto de la caja le sigue llegando a la mesa de
debajo (no al overlay) — importante probarlo con la mesa de PokerStars
enfocada, no el HUD, para que sea un caso realista de uso mientras se
juega.

Dependencias del sistema (ninguna es instalable sólo con pip, por eso no
están en ``pyproject.toml`` como dependencias normales):

- ``tkinter``: paquete del sistema operativo (p.ej. ``python3-tk`` en
  Debian/Ubuntu), no viene siempre con el intérprete de Python. Sin él,
  importar este módulo falla; el resto del paquete ``poker_hud.overlay``
  (detección de ventana, :mod:`poker_hud.overlay.layout`) no depende de
  ``tkinter`` y sigue funcionando.
- ``python-xlib`` (``pip install python-xlib``), opcional: sin ella el
  overlay funciona igual pero sin click-through, ver ``_init_click_through``.
"""

from __future__ import annotations

import tkinter as tk
from functools import partial
from pathlib import Path
from typing import Callable

from poker_hud.overlay import (
    PokerTable,
    find_poker_tables,
    find_table_by_tournament_id,
    list_windows,
)
from poker_hud.overlay.layout import (
    DEFAULT_BOX_HEIGHT,
    DEFAULT_BOX_WIDTH,
    DEFAULT_OPACITY,
    SeatBox,
    build_seat_boxes,
    resolve_max_seats,
)
from poker_hud.overlay.positions import load_seat_positions, save_seat_position
from poker_hud.stats import PlayerStats, get_player_stats

__all__ = ["SeatBoxWindow", "HudController", "run"]

_BACKGROUND = "#101010"
_POLL_INTERVAL_MS = 1000

# T19: tamaño (cuadrado, en píxeles) y aspecto de la manija de arrastre fija
# en la esquina superior derecha de cada caja. Es también el tamaño del
# único rectángulo que se deja fuera del click-through (ver
# ``SeatBoxWindow._update_input_region``): tiene que ser lo bastante grande
# para poder pincharla con el ratón con comodidad, pero chico para no comerse
# mucho contenido de la caja.
_HANDLE_SIZE = 16
_HANDLE_COLOR = "#9e9e9e"
_HANDLE_SYMBOL = "✛"


class SeatBoxWindow:
    """Una única ventana Tk (``Toplevel``) que representa la caja de un asiento.

    ``seat`` y ``on_position_changed`` son de T16: ``seat`` identifica esta
    caja frente a :class:`HudController` (que gestiona una por asiento), y
    ``on_position_changed(seat, x, y)`` se llama con las coordenadas
    absolutas de pantalla tras soltar un arrastre sobre la manija de
    esquina (T19), para que el controlador las convierta a offset relativo
    a la mesa y las persista (ver :mod:`poker_hud.overlay.positions`).
    ``on_drag_state_changed(seat, dragging)`` (T19) se llama al empezar y
    al soltar un arrastre, para que el controlador pueda congelar su
    refresco periódico mientras dura (ver
    :meth:`HudController._on_seat_drag_state_changed`).
    ``opacity`` (T20) es el valor de ``-alpha`` de la ventana (0.0
    totalmente transparente, 1.0 opaca); lo decide en última instancia
    :class:`HudController` a partir del flag ``--opacity`` de la CLI.
    """

    def __init__(
        self,
        master: tk.Misc,
        seat: int,
        on_position_changed: Callable[[int, int, int], None] | None = None,
        on_drag_state_changed: Callable[[int, bool], None] | None = None,
        opacity: float = DEFAULT_OPACITY,
    ) -> None:
        self._seat = seat
        self._on_position_changed = on_position_changed
        self._on_drag_state_changed = on_drag_state_changed
        self._drag_offset: tuple[int, int] | None = None

        self._top = tk.Toplevel(master)
        self._top.overrideredirect(True)
        self._top.attributes("-topmost", True)
        try:
            self._top.attributes("-alpha", opacity)
        except tk.TclError:
            # Sin compositor, algunos gestores de ventanas rechazan -alpha.
            # No es fatal: la caja se queda opaca en vez de semitransparente.
            pass
        self._top.configure(bg=_BACKGROUND)

        self._text = tk.Text(
            self._top,
            bg=_BACKGROUND,
            font=("Sans", 9),
            wrap="none",
            borderwidth=0,
            highlightthickness=0,
            padx=4,
            pady=2,
            cursor="arrow",
        )
        self._text.configure(state="disabled")
        self._text.pack(fill="both", expand=True)
        self._known_tags: set[str] = set()

        # T19: manija fija de arrastre (ver docstring del módulo). Se
        # coloca con ``place`` (no ``pack``, que la haría compartir espacio
        # con el Text en vez de flotar sobre su esquina) en cada
        # :meth:`update`, cuando se conoce el ancho real de la caja.
        self._handle = tk.Label(
            self._top,
            text=_HANDLE_SYMBOL,
            bg=_HANDLE_COLOR,
            fg=_BACKGROUND,
            font=("Sans", 9, "bold"),
            cursor="fleur",
            borderwidth=0,
            highlightthickness=0,
        )
        self._handle.bind("<ButtonPress-1>", self._on_drag_start)
        self._handle.bind("<B1-Motion>", self._on_drag_motion)
        self._handle.bind("<ButtonRelease-1>", self._on_drag_end)

        self._xlib_display = None
        self._xlib_window = None
        self._xlib_shape = None
        self._click_through_supported = False
        self._init_click_through()
        if not self._click_through_supported:
            # Sin click-through no hay nada que la manija proteja: la caja
            # entera ya captura todos los clicks todo el tiempo, así que se
            # puede arrastrar desde cualquier punto (ver docstring del
            # módulo, diferencia de comportamiento documentada a propósito).
            self._text.configure(cursor="fleur")
            self._text.bind("<ButtonPress-1>", self._on_drag_start)
            self._text.bind("<B1-Motion>", self._on_drag_motion)
            self._text.bind("<ButtonRelease-1>", self._on_drag_end)

    def _tag_for_color(self, color: str) -> str:
        """Nombre del tag de Tk para ``color``, configurándolo la primera vez que se usa."""

        tag = f"color_{color}"
        if tag not in self._known_tags:
            self._text.tag_configure(tag, foreground=color)
            self._known_tags.add(tag)
        return tag

    def _init_click_through(self) -> None:
        """Prepara la conexión X11 propia para poder fijar la región de input (T19).

        Requiere ``python-xlib`` (dependencia opcional, no listada en
        ``pyproject.toml`` porque el resto del proyecto no la necesita).
        Si no está instalada, o el servidor X no soporta la extensión
        Shape, ``self._click_through_supported`` se queda en ``False`` y la
        caja se queda visible pero capturando el ratón siempre: se
        documenta como limitación conocida en vez de fallar (ver
        docstring del módulo).
        """

        try:
            from Xlib.display import Display
            from Xlib.ext import shape
        except ImportError:
            return

        try:
            # Sin esto, winfo_id() puede devolver el id de una ventana que
            # Tk aún no terminó de crear en el servidor X: la conexión propia
            # de python-xlib (independiente de la de Tk) la ve como
            # inexistente y sus peticiones fallan con BadWindow.
            self._top.update_idletasks()
            display = Display()
            window_id = self._top.winfo_id()
            xlib_window = display.create_resource_object("window", window_id)
            if not display.has_extension("SHAPE"):
                return
            xlib_window.shape_select_input(0)
            self._xlib_display = display
            self._xlib_window = xlib_window
            self._xlib_shape = shape
            self._click_through_supported = True
        except Exception:
            # Cualquier fallo de la extensión Shape es no-fatal: peor caso,
            # la caja no es click-through pero sigue mostrando las stats.
            return

    def _update_input_region(self, width: int) -> None:
        """Fija la región de input de X Shape a sólo el rectángulo de la manija (T19).

        El resto de la ventana queda fuera de la región (click-through: los
        clicks le llegan a la mesa de debajo); ese rectángulo -del tamaño
        de la manija, en su esquina superior derecha- es la única parte que
        sí los captura. Se recalcula en cada :meth:`update` porque el ancho
        de la caja (``width``) puede cambiar.
        """

        if self._xlib_window is None:
            return
        try:
            handle_x = max(0, width - _HANDLE_SIZE)
            self._xlib_window.shape_rectangles(
                self._xlib_shape.SO.Set,
                self._xlib_shape.SK.Input,
                0,
                0,
                0,
                [(handle_x, 0, _HANDLE_SIZE, _HANDLE_SIZE)],
            )
            self._xlib_display.sync()
        except Exception:
            # No-fatal (ver _init_click_through): peor caso, la región de
            # input no se actualiza pero la caja sigue mostrando las stats.
            pass

    def _on_drag_start(self, event: tk.Event) -> None:
        # event.x_root/y_root son coordenadas absolutas de pantalla, válidas
        # sin importar si el evento llegó por la manija o (sin
        # click-through, ver __init__) por toda la caja: restar la esquina
        # de la ventana da el punto de arrastre relativo a ella misma, no
        # al widget concreto que capturó el click.
        self._drag_offset = (
            event.x_root - self._top.winfo_x(),
            event.y_root - self._top.winfo_y(),
        )
        if self._on_drag_state_changed is not None:
            self._on_drag_state_changed(self._seat, True)

    def _on_drag_motion(self, event: tk.Event) -> None:
        if self._drag_offset is None:
            return
        offset_x, offset_y = self._drag_offset
        # winfo_pointerx/y son absolutos de pantalla; restar el punto donde
        # empezó el arrastre dentro de la caja mantiene el cursor "pegado"
        # al mismo punto de la caja mientras se mueve, en vez de saltar la
        # esquina superior izquierda al cursor.
        new_x = self._top.winfo_pointerx() - offset_x
        new_y = self._top.winfo_pointery() - offset_y
        self._top.geometry(f"+{new_x}+{new_y}")

    def _on_drag_end(self, event: tk.Event) -> None:
        self._drag_offset = None
        if self._on_drag_state_changed is not None:
            self._on_drag_state_changed(self._seat, False)
        if self._on_position_changed is not None:
            self._on_position_changed(self._seat, self._top.winfo_x(), self._top.winfo_y())

    def update(self, box: SeatBox) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        for segment in box.segments:
            self._text.insert("end", segment.text, self._tag_for_color(segment.color))
        self._text.configure(state="disabled")
        self._top.geometry(f"{box.width}x{box.height}+{box.x}+{box.y}")
        self._handle.place(
            x=box.width - _HANDLE_SIZE, y=0, width=_HANDLE_SIZE, height=_HANDLE_SIZE
        )
        self._handle.lift()
        self._update_input_region(box.width)

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
    - ``get_max_seats``: tamaño real de la mesa en curso (``Hand.max_seats``
      de T1, vía T3). Puede devolver ``0``/``None`` si aún no llegó ninguna
      mano completa; en ese caso se cae a :func:`~poker_hud.overlay.layout.resolve_max_seats`
      (ver T11: antes se inferia del asiento ocupado más alto en
      ``seat_players``, lo que perdía asientos reales de la mesa).
    - ``get_stats``: ``screen_name`` -> :class:`~poker_hud.stats.PlayerStats`
      (por defecto, el motor de T2 sobre la conexión SQLite dada).
    - ``positions_path`` (T16): fichero donde persisten los offsets de
      asiento ajustados a mano (ver :mod:`poker_hud.overlay.positions`).
      Si es ``None``, arrastrar sigue moviendo la caja dentro de la sesión
      pero no sobrevive a un refresco ni a reiniciar el HUD, ya que no hay
      dónde guardar el offset.
    - ``opacity`` (T20): ``-alpha`` de cada caja (0.0-1.0), ver
      :func:`build_arg_parser` en ``app.py`` para el flag ``--opacity`` que
      lo fija. La validación del rango vive ahí, no aquí: para cuando llega
      a este constructor ya se asume válido.
    """

    def __init__(
        self,
        get_current_players: Callable[[], dict[int, str]],
        stats_conn,
        find_table: Callable[[], PokerTable | None] | None = None,
        get_stats: Callable[[str], PlayerStats | None] | None = None,
        get_max_seats: Callable[[], int] | None = None,
        box_width: int = DEFAULT_BOX_WIDTH,
        box_height: int = DEFAULT_BOX_HEIGHT,
        poll_interval_ms: int = _POLL_INTERVAL_MS,
        positions_path: Path | str | None = None,
        opacity: float = DEFAULT_OPACITY,
    ) -> None:
        self._get_current_players = get_current_players
        self._find_table = find_table or _default_find_table
        self._get_stats = get_stats or partial(get_player_stats, stats_conn)
        self._get_max_seats = get_max_seats or (lambda: 0)
        self._box_width = box_width
        self._box_height = box_height
        self._poll_interval_ms = poll_interval_ms
        self._positions_path = positions_path
        self._overrides = load_seat_positions(positions_path) if positions_path else {}
        self._opacity = opacity

        self._root = tk.Tk()
        self._root.withdraw()  # la ventana raíz no se muestra, sólo las cajas
        self._boxes: dict[int, SeatBoxWindow] = {}
        self._dragging_seats: set[int] = set()
        self._last_table_geometry = None

    def start(self) -> None:
        self._refresh()
        self._root.mainloop()

    def stop(self) -> None:
        self._root.quit()

    def _on_seat_drag_state_changed(self, seat: int, dragging: bool) -> None:
        """Callback de :class:`SeatBoxWindow` al empezar/soltar un arrastre (T19).

        Mientras haya al menos un asiento en ``self._dragging_seats``,
        :meth:`_refresh` congela el refresco periódico (ver ahí): un
        sondeo de mesa a mitad de un arrastre recalcularía la caja a su
        posición "de siempre" y se la pelearía al usuario debajo del
        ratón. A diferencia del modo edición global de T16/T17, esto sólo
        congela mientras hay un arrastre de verdad en curso, no de forma
        indefinida hasta soltar un atajo.
        """

        if dragging:
            self._dragging_seats.add(seat)
        else:
            self._dragging_seats.discard(seat)

    def _on_seat_dragged(self, seat: int, x: int, y: int) -> None:
        """Callback de :class:`SeatBoxWindow` al soltar un arrastre (T16).

        Convierte las coordenadas absolutas de pantalla a offset relativo
        a la esquina superior izquierda de la mesa (ver
        :func:`~poker_hud.overlay.layout.resolve_seat_position` para por
        qué relativo y no absoluto) y lo persiste. Sin geometría de mesa
        conocida (no debería pasar en la práctica: hace falta una mesa
        detectada para ver cajas que arrastrar) no hay base para calcular
        el offset, así que se ignora el arrastre en vez de guardar algo
        sin sentido.
        """

        if self._last_table_geometry is None:
            return

        dx = x - self._last_table_geometry.x
        dy = y - self._last_table_geometry.y
        self._overrides[seat] = (dx, dy)
        if self._positions_path is not None:
            save_seat_position(self._positions_path, seat, dx, dy)

    def _refresh(self) -> None:
        if self._dragging_seats:
            self._root.after(self._poll_interval_ms, self._refresh)
            return

        table = self._find_table()
        if table is not None:
            self._last_table_geometry = table.geometry
            seat_players = self._get_current_players()
            max_seats = resolve_max_seats(self._get_max_seats(), seat_players)
            boxes = build_seat_boxes(
                table.geometry,
                max_seats,
                seat_players,
                self._get_stats,
                self._box_width,
                self._box_height,
                self._overrides,
            )
            self._sync_boxes(boxes)
        else:
            self._clear_boxes()

        self._root.after(self._poll_interval_ms, self._refresh)

    def _sync_boxes(self, boxes: list[SeatBox]) -> None:
        seen = set()
        for box in boxes:
            seen.add(box.seat)
            if not box.segments:
                if box.seat in self._boxes:
                    self._boxes.pop(box.seat).destroy()
                    self._dragging_seats.discard(box.seat)
                continue
            if box.seat not in self._boxes:
                self._boxes[box.seat] = SeatBoxWindow(
                    self._root,
                    box.seat,
                    self._on_seat_dragged,
                    self._on_seat_drag_state_changed,
                    opacity=self._opacity,
                )
            self._boxes[box.seat].update(box)

        for seat in list(self._boxes):
            if seat not in seen:
                self._boxes.pop(seat).destroy()
                self._dragging_seats.discard(seat)

    def _clear_boxes(self) -> None:
        for window in self._boxes.values():
            window.destroy()
        self._boxes.clear()
        self._dragging_seats.clear()


def _default_find_table() -> PokerTable | None:
    tables = find_poker_tables(list_windows())
    return tables[0] if tables else None


def _find_table_for_tournament(tournament_id: str) -> Callable[[], PokerTable | None]:
    """``find_table`` que fija el HUD a la mesa de ``tournament_id`` (T18).

    Sondea las mismas ventanas que :func:`_default_find_table` pero, en vez
    de quedarse siempre con la primera mesa detectada, filtra por
    ``tournament_id`` vía :func:`~poker_hud.overlay.find_table_by_tournament_id`.
    Necesario con más de una mesa abierta a la vez: el orden que devuelve
    ``wmctrl -l`` no es estable entre sondeos, así que ``tables[0]`` salta de
    una mesa a otra en cada refresco. Si esa mesa no está abierta (todavía,
    o ya no), devuelve ``None`` igual que ``_default_find_table`` sin mesas:
    el HUD deja de dibujar cajas hasta que reaparezca.
    """

    def _find() -> PokerTable | None:
        tables = find_poker_tables(list_windows())
        return find_table_by_tournament_id(tables, tournament_id)

    return _find


def run(
    get_current_players: Callable[[], dict[int, str]],
    stats_conn,
    get_max_seats: Callable[[], int] | None = None,
    positions_path: Path | str | None = None,
    tournament_id: str | None = None,
    opacity: float = DEFAULT_OPACITY,
) -> None:
    """Arranca el overlay con la configuración por defecto y bloquea hasta cerrarlo.

    ``tournament_id`` (T18): si se pasa, fija el HUD a la mesa de ese
    torneo en vez de seguir la primera mesa detectada (ver
    :func:`_find_table_for_tournament`), para el caso de más de una mesa
    de PokerStars abierta a la vez.
    ``opacity`` (T20): ``-alpha`` de cada caja; se asume ya validado en
    0.0-1.0 por quien llama (``app.py``).
    """

    find_table = _find_table_for_tournament(tournament_id) if tournament_id else None

    HudController(
        get_current_players,
        stats_conn,
        find_table=find_table,
        get_max_seats=get_max_seats,
        positions_path=positions_path,
        opacity=opacity,
    ).start()
