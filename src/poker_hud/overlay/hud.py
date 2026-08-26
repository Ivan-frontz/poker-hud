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

*** Modo edición: arrastrar cajas con el ratón (T16, atajo global real desde T17) ***
Mientras el click-through de arriba está activo, un bind normal de
arrastre (``<ButtonPress-1>``/``<B1-Motion>``) nunca recibiría el evento:
el click ya se fue a la ventana de PokerStars de debajo antes de llegar a
la caja. Por eso el arrastre vive detrás de un "modo edición" explícito,
que alterna la tecla **F9**. Ver :meth:`HudController._toggle_edit_mode`:
mientras está activo, cada :class:`SeatBoxWindow` desactiva su propio
click-through (vuelve a capturar el ratón, con un borde amarillo de aviso
para que sea obvio a golpe de vista qué modo está activo) y permite
arrastrarla con el botón izquierdo; al soltar F9 se restaura el
click-through normal en todas las cajas. Se descartó un gesto por caja
(p.ej. click derecho) porque, con la región de input vacía del
click-through, ningún click aterriza en la caja para empezar — hacía
falta desactivarlo primero de todos modos, así que un atajo global es la
opción más simple sin más mecanismo.

F9 como atajo *de verdad* global (T17): la primera versión (T16) capturaba
F9 con ``self._root.bind_all(...)``, que en Tkinter sólo entrega eventos
de teclado cuando alguna ventana *de esta misma app* tiene el foco de
teclado de X11. En el uso real la ventana enfocada es la mesa de
PokerStars (la raíz de Tk está oculta con ``withdraw`` y nunca puede tener
foco; las cajas son ``overrideredirect`` y además click-through, así que
tampoco lo consiguen), así que F9 nunca le llegaba al HUD — sólo "andaba"
si por casualidad el propio HUD tenía el foco, que es justo lo que no pasa
mientras se juega. El fix (:meth:`HudController._init_global_hotkey`) usa
``python-xlib`` para pedirle al servidor X que agarre la tecla a nivel
global con ``XGrabKey`` sobre la ventana raíz de la pantalla (no una
ventana de Tk): con eso, el evento de teclado llega aunque el foco esté en
PokerStars. Esa captura viaja por la conexión X propia de ``python-xlib``,
en un socket aparte del que usa Tk para su mainloop, así que hace falta un
hilo dedicado que bloquee en ``display.next_event()`` y, al ver la tecla,
encole el toggle real en el hilo de Tk vía ``self._root.after(0, ...)``
-igual que T10 con sqlite3, la API de Tk no es segura de llamar
directamente desde otro hilo, pero sí lo es encolarla con ``after``-. Si
``python-xlib`` no está instalada, o el ``grab_key`` falla (servidor sin
la tecla disponible, u otra app ya la tiene agarrada), se cae de nuevo al
``bind_all`` de siempre como mecanismo único: sigue sin ser un atajo
global de verdad en ese caso, pero es mejor que nada si el HUD llega a
tener el foco, y es el mismo patrón de degradación que ya usa
``_init_click_through`` sin la librería.

Mientras el modo edición está activo, :class:`HudController` congela el
refresco periódico (no recalcula ni reposiciona cajas) para que un
sondeo de mesa a mitad de un arrastre no le devuelva la caja a la
posición vieja debajo del ratón del usuario.

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
resultados y pintarlos, y a reaccionar a los eventos de ratón/teclado de
Tk. El gesto de arrastre en sí (modo edición con F9 + arrastrar con el
botón izquierdo, ver arriba) sólo se verificó a mano contra una mesa
real, no con un test automatizado: quien revise T16 no debería esperar
cobertura de pytest para eso.

Verificación manual (no automatizable): lanzar ``run()`` con una mesa de
PokerStars real (o cualquier ventana renombrada a un título con pinta de
mesa, ver :mod:`poker_hud.overlay`) corriendo bajo X11/Wine, y comprobar a
ojo que aparece una caja por asiento con las stats correctas, que siguen a
la ventana al moverla o redimensionarla, que los clicks sobre las cajas
le llegan a la mesa de debajo (no al overlay) fuera de modo edición, y
que F9 + arrastrar con el botón izquierdo mueve la caja y esa posición
sobrevive al siguiente refresco y a reiniciar el HUD. Importante (T17,
para no repetir el error de verificación de T16): probar F9 con **otra
ventana enfocada, no el HUD** — p.ej. haciendo click en la barra de título
de la mesa de PokerStars (o en la terminal) justo antes de apretar F9-.
Si sólo se prueba con el propio HUD en foco, un ``bind_all`` de Tk ya
"funcionaría" y el bug de T17 (F9 no le llega al HUD con PokerStars
enfocada, que es el caso real de uso) pasaría desapercibido otra vez.

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

import threading
import tkinter as tk
from functools import partial
from pathlib import Path
from typing import Callable

from poker_hud.overlay import PokerTable, find_poker_tables, list_windows
from poker_hud.overlay.layout import (
    DEFAULT_BOX_HEIGHT,
    DEFAULT_BOX_WIDTH,
    SeatBox,
    build_seat_boxes,
    resolve_max_seats,
)
from poker_hud.overlay.positions import load_seat_positions, save_seat_position
from poker_hud.stats import PlayerStats, get_player_stats

__all__ = ["SeatBoxWindow", "HudController", "run"]

_BACKGROUND = "#101010"
_ALPHA = 0.80
_POLL_INTERVAL_MS = 1000

# T16: tecla que alterna el modo edición (ver docstring del módulo para por
# qué hace falta un modo explícito en vez de un bind de arrastre normal).
# Formato de secuencia de Tk, usado sólo por el ``bind_all`` de fallback
# (ver T17 en el docstring del módulo y `HudController._init_global_hotkey`
# para el atajo global de verdad vía XGrabKey).
_EDIT_MODE_KEY = "<F9>"
# Borde de aviso visual mientras una caja está en modo edición, para que sea
# obvio a golpe de vista qué cajas se pueden arrastrar ahora mismo.
_EDIT_BORDER_COLOR = "#ffcc00"


class SeatBoxWindow:
    """Una única ventana Tk (``Toplevel``) que representa la caja de un asiento.

    ``seat`` y ``on_position_changed`` son de T16: ``seat`` identifica esta
    caja frente a :class:`HudController` (que gestiona una por asiento), y
    ``on_position_changed(seat, x, y)`` se llama con las coordenadas
    absolutas de pantalla tras soltar un arrastre en modo edición, para que
    el controlador las convierta a offset relativo a la mesa y las
    persista (ver :mod:`poker_hud.overlay.positions`).
    """

    def __init__(
        self,
        master: tk.Misc,
        seat: int,
        on_position_changed: Callable[[int, int, int], None] | None = None,
    ) -> None:
        self._seat = seat
        self._on_position_changed = on_position_changed
        self._drag_offset: tuple[int, int] | None = None
        self._edit_mode = False

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

        self._xlib_display = None
        self._xlib_window = None
        self._xlib_shape = None
        self._init_click_through()

    def _tag_for_color(self, color: str) -> str:
        """Nombre del tag de Tk para ``color``, configurándolo la primera vez que se usa."""

        tag = f"color_{color}"
        if tag not in self._known_tags:
            self._text.tag_configure(tag, foreground=color)
            self._known_tags.add(tag)
        return tag

    def _init_click_through(self) -> None:
        """Prepara la ventana para poder alternar click-through, y lo activa.

        Requiere ``python-xlib`` (dependencia opcional, no listada en
        ``pyproject.toml`` porque el resto del proyecto no la necesita).
        Si no está instalada, o el servidor X no soporta la extensión
        Shape, la caja se queda visible pero capturando el ratón: se
        documenta como limitación conocida en vez de fallar. En ese caso
        tampoco hay nada que alternar en modo edición (T16): la caja ya
        captura el ratón siempre, así que el arrastre funcionaría sin
        pasar por modo edición, pero perdiendo el click-through normal
        fuera de él.
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
        except Exception:
            # Cualquier fallo de la extensión Shape es no-fatal: peor caso,
            # la caja no es click-through pero sigue mostrando las stats.
            return

        self._apply_click_through(True)

    def _apply_click_through(self, enabled: bool) -> None:
        """Activa o desactiva el click-through vía la región de input de X Shape.

        ``enabled=True``: región de input vacía, ningún click aterriza en
        esta ventana (le llegan a la mesa de debajo). ``enabled=False``
        (T16, modo edición): se resetea la región de input al valor por
        defecto (toda la ventana, vía ``ShapeMask`` con ``source_bitmap``
        nulo, la forma estándar de "quitar" una forma de la extensión
        Shape), para que la caja vuelva a capturar clicks y se pueda
        arrastrar.
        """

        if self._xlib_window is None:
            return
        try:
            from Xlib import X

            if enabled:
                self._xlib_window.shape_rectangles(
                    self._xlib_shape.SO.Set, self._xlib_shape.SK.Input, 0, 0, 0, []
                )
            else:
                self._xlib_window.shape_mask(
                    self._xlib_shape.SO.Set, self._xlib_shape.SK.Input, 0, 0, X.NONE
                )
            self._xlib_display.sync()
        except Exception:
            # No-fatal (ver _init_click_through): peor caso, el modo
            # edición no cambia el click-through pero el arrastre sigue
            # intentándose con los binds de ratón normales de Tk.
            pass

    def set_edit_mode(self, enabled: bool) -> None:
        """Activa/desactiva el arrastre con el ratón para esta caja (T16).

        Ver el docstring del módulo para por qué hace falta un modo
        explícito en vez de un simple bind de arrastre: mientras el
        click-through está activo, los clicks nunca llegan a la caja.
        """

        self._edit_mode = enabled
        self._apply_click_through(not enabled)

        if enabled:
            self._text.configure(
                cursor="fleur", highlightthickness=2, highlightbackground=_EDIT_BORDER_COLOR
            )
            self._text.bind("<ButtonPress-1>", self._on_drag_start)
            self._text.bind("<B1-Motion>", self._on_drag_motion)
            self._text.bind("<ButtonRelease-1>", self._on_drag_end)
        else:
            self._drag_offset = None
            self._text.configure(cursor="arrow", highlightthickness=0)
            self._text.unbind("<ButtonPress-1>")
            self._text.unbind("<B1-Motion>")
            self._text.unbind("<ButtonRelease-1>")

    def _on_drag_start(self, event: tk.Event) -> None:
        self._drag_offset = (event.x, event.y)

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
        if self._on_position_changed is not None:
            self._on_position_changed(self._seat, self._top.winfo_x(), self._top.winfo_y())

    def update(self, box: SeatBox) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        for segment in box.segments:
            self._text.insert("end", segment.text, self._tag_for_color(segment.color))
        self._text.configure(state="disabled")
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
    - ``get_max_seats``: tamaño real de la mesa en curso (``Hand.max_seats``
      de T1, vía T3). Puede devolver ``0``/``None`` si aún no llegó ninguna
      mano completa; en ese caso se cae a :func:`~poker_hud.overlay.layout.resolve_max_seats`
      (ver T11: antes se inferia del asiento ocupado más alto en
      ``seat_players``, lo que perdía asientos reales de la mesa).
    - ``get_stats``: ``screen_name`` -> :class:`~poker_hud.stats.PlayerStats`
      (por defecto, el motor de T2 sobre la conexión SQLite dada).
    - ``positions_path`` (T16): fichero donde persisten los offsets de
      asiento ajustados a mano (ver :mod:`poker_hud.overlay.positions`).
      Si es ``None``, el modo edición sigue funcionando dentro de la
      sesión (arrastrar mueve la caja) pero no sobrevive a un refresco ni
      a reiniciar el HUD, ya que no hay dónde guardar el offset.
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

        self._root = tk.Tk()
        self._root.withdraw()  # la ventana raíz no se muestra, sólo las cajas
        self._boxes: dict[int, SeatBoxWindow] = {}
        self._edit_mode = False
        self._last_table_geometry = None
        self._xlib_hotkey_display = None
        # Fallback (ver docstring del módulo, T17): sólo dispara si alguna
        # ventana de esta app tiene el foco de teclado de X11, cosa que no
        # pasa en el uso real con PokerStars enfocada. Se deja como red de
        # seguridad por si _init_global_hotkey no puede agarrar la tecla.
        self._root.bind_all(_EDIT_MODE_KEY, self._toggle_edit_mode)
        self._init_global_hotkey()

    def start(self) -> None:
        self._refresh()
        self._root.mainloop()

    def stop(self) -> None:
        if self._xlib_hotkey_display is not None:
            try:
                self._xlib_hotkey_display.close()
            except Exception:
                # Best-effort: sólo libera el fd cuanto antes; el hilo de
                # _xlib_hotkey_loop de todos modos es daemon y no bloquea
                # la salida del proceso.
                pass
            self._xlib_hotkey_display = None
        self._root.quit()

    def _init_global_hotkey(self) -> None:
        """Agarra F9 a nivel de servidor X11 con ``XGrabKey`` (T17).

        A diferencia del ``bind_all`` de Tk (fallback, ver ``__init__``),
        esto sí es un atajo global de verdad: el servidor X le entrega el
        evento a esta conexión sin importar qué ventana tenga el foco de
        teclado, incluida la mesa de PokerStars durante el juego real. Ver
        el docstring del módulo para el porqué completo.

        Requiere ``python-xlib`` (dependencia opcional, ver
        ``_init_click_through`` en :class:`SeatBoxWindow` para el mismo
        patrón). Si no está instalada, o el grab falla (p.ej. otra
        aplicación ya tiene F9 agarrada de antes), no se hace nada más:
        el ``bind_all`` de ``__init__`` se queda como único mecanismo,
        documentado como limitación conocida en ese caso.
        """

        try:
            from Xlib import X, XK
            from Xlib.display import Display
        except ImportError:
            return

        try:
            display = Display()
            root_window = display.screen().root
            keycode = display.keysym_to_keycode(XK.XK_F9)
            # AnyModifier: agarra F9 sin importar qué otras teclas
            # modificadoras (Shift, Ctrl, Num/Caps Lock...) estén activas a
            # la vez, en vez de tener que enumerar a mano cada combinación
            # de "modificadores de bloqueo" que X trata como estado
            # aparte -el mismo problema de siempre al usar XGrabKey con un
            # modificador concreto en vez de AnyModifier.
            root_window.grab_key(keycode, X.AnyModifier, True, X.GrabModeAsync, X.GrabModeAsync)
            display.sync()
        except Exception:
            return

        self._xlib_hotkey_display = display
        thread = threading.Thread(
            target=self._xlib_hotkey_loop, args=(display, keycode), daemon=True
        )
        thread.start()

    def _xlib_hotkey_loop(self, display, keycode: int) -> None:
        """Bucle bloqueante (en un hilo aparte) que espera el F9 agarrado globalmente.

        ``display.next_event()`` bloquea leyendo del socket propio de esta
        conexión Xlib -aparte del que usa Tk para su mainloop, que no lo
        integra-, así que no hay forma de sondearlo desde ``root.after``
        sin más: hace falta este hilo dedicado. Nunca se llama a la API de
        Tk directamente desde aquí -mismo motivo que la lección de T10 con
        sqlite3: Tk no es seguro de usar desde un hilo ajeno al del
        mainloop-, sólo se encola el toggle real con ``after(0, ...)``,
        que sí es seguro y lo ejecuta en el hilo de Tk en la próxima vuelta
        del mainloop.

        Termina sola cuando ``display.close()`` (ver :meth:`stop`) rompe
        la conexión y ``next_event`` lanza; el hilo es además ``daemon``,
        así que tampoco bloquea la salida del proceso si eso no llega a
        pasar.
        """

        from Xlib import X

        while True:
            try:
                event = display.next_event()
            except Exception:
                return
            if event.type == X.KeyPress and event.detail == keycode:
                try:
                    self._root.after(0, self._toggle_edit_mode)
                except RuntimeError:
                    # Tcl exige que el mainloop ya esté corriendo en el
                    # hilo que creó el intérprete para poder encolar algo
                    # desde otro hilo ("main thread is not in main loop");
                    # puede pasar si F9 se aprieta en la breve ventana
                    # entre construir HudController y llamar a start(). No
                    # es fatal: se pierde ese toggle puntual, pero el hilo
                    # sigue vivo para la siguiente pulsación de F9, que ya
                    # encontrará el mainloop corriendo.
                    pass

    def _toggle_edit_mode(self, event: tk.Event | None = None) -> None:
        """Alterna el modo edición (T16) en todas las cajas activas, vía F9.

        Congela también el refresco periódico (ver :meth:`_refresh`)
        mientras está activo: un sondeo de mesa a mitad de un arrastre
        recalcularía la posición "de siempre" y le pelearía la caja al
        usuario debajo del ratón.
        """

        self._edit_mode = not self._edit_mode
        for box in self._boxes.values():
            box.set_edit_mode(self._edit_mode)

    def _on_seat_dragged(self, seat: int, x: int, y: int) -> None:
        """Callback de :class:`SeatBoxWindow` al soltar un arrastre (T16).

        Convierte las coordenadas absolutas de pantalla a offset relativo
        a la esquina superior izquierda de la mesa (ver
        :func:`~poker_hud.overlay.layout.resolve_seat_position` para por
        qué relativo y no absoluto) y lo persiste. Sin geometría de mesa
        conocida (no debería pasar en la práctica: hace falta una mesa
        detectada para entrar en modo edición y ver cajas que arrastrar)
        no hay base para calcular el offset, así que se ignora el
        arrastre en vez de guardar algo sin sentido.
        """

        if self._last_table_geometry is None:
            return

        dx = x - self._last_table_geometry.x
        dy = y - self._last_table_geometry.y
        self._overrides[seat] = (dx, dy)
        if self._positions_path is not None:
            save_seat_position(self._positions_path, seat, dx, dy)

    def _refresh(self) -> None:
        if self._edit_mode:
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
                continue
            if box.seat not in self._boxes:
                self._boxes[box.seat] = SeatBoxWindow(self._root, box.seat, self._on_seat_dragged)
            self._boxes[box.seat].update(box)

        for seat in list(self._boxes):
            if seat not in seen:
                self._boxes.pop(seat).destroy()

    def _clear_boxes(self) -> None:
        for window in self._boxes.values():
            window.destroy()
        self._boxes.clear()


def _default_find_table() -> PokerTable | None:
    tables = find_poker_tables(list_windows())
    return tables[0] if tables else None


def run(
    get_current_players: Callable[[], dict[int, str]],
    stats_conn,
    get_max_seats: Callable[[], int] | None = None,
    positions_path: Path | str | None = None,
) -> None:
    """Arranca el overlay con la configuración por defecto y bloquea hasta cerrarlo."""

    HudController(
        get_current_players, stats_conn, get_max_seats=get_max_seats, positions_path=positions_path
    ).start()
