"""Persistencia en disco de las posiciones de asiento ajustadas a mano (T16).

Hasta T16, la posición de cada caja de asiento la calculaba siempre
:func:`poker_hud.overlay.layout.compute_seat_position`: una aproximación
geométrica (elipse sobre la geometría de la ventana de mesa) que no
siempre coincide con dónde está el asiento de verdad en el fieltro de esa
mesa/tema visual. Este módulo persiste, por asiento, el offset ``(dx, dy)``
que el usuario dejó tras arrastrar la caja a mano (ver
``SeatBoxWindow`` en :mod:`poker_hud.overlay.hud`), para que
:func:`poker_hud.overlay.layout.resolve_seat_position` la use en el
siguiente refresco en vez de volver a calcularla, incluso tras reiniciar
el HUD.

Se guarda como offset relativo a la esquina superior izquierda de la
ventana de mesa, no como coordenadas absolutas de pantalla: así la
posición ajustada sigue siendo válida si la ventana de mesa se mueve o
cambia de tamaño en la siguiente sesión.

Es un fichero JSON aparte (no una tabla nueva en ``stats.db``) a propósito,
para no acoplar el motor de stats de T2 (que no sabe nada de overlay ni de
X11) a esto: la posición de las cajas es un dato de presentación local del
overlay, no una stat de jugador.

Este módulo es E/S de disco pura (sin tocar X11 ni Tkinter), así que sí
está cubierto por tests (``tests/test_overlay_positions.py``), a
diferencia del resto de lo interactivo de T16 que vive en
:mod:`poker_hud.overlay.hud` (ver el docstring de ese módulo para el
porqué no es testeable).
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["load_seat_positions", "save_seat_position"]


def load_seat_positions(path: Path | str) -> dict[int, tuple[int, int]]:
    """Offsets ``(dx, dy)`` guardados por asiento, o ``{}`` si no hay fichero o está corrupto.

    Un fichero ausente (primer arranque, nadie ajustó nunca ninguna caja) o
    con contenido inesperado (JSON corrupto, editado a mano de forma
    inválida) se trata igual que "sin posiciones guardadas" en vez de
    hacer fallar el arranque del HUD: cada asiento sin entrada válida cae
    en el cálculo automático de siempre.
    """

    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}

    if not isinstance(raw, dict):
        return {}

    positions: dict[int, tuple[int, int]] = {}
    for seat_key, offset in raw.items():
        try:
            seat = int(seat_key)
            dx, dy = offset
            positions[seat] = (int(dx), int(dy))
        except (TypeError, ValueError):
            continue
    return positions


def save_seat_position(path: Path | str, seat: int, dx: int, dy: int) -> None:
    """Guarda el offset de ``seat``, conservando el resto de asientos ya guardados."""

    path = Path(path)
    positions = load_seat_positions(path)
    positions[seat] = (dx, dy)

    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {str(seat_number): list(offset) for seat_number, offset in positions.items()}
    path.write_text(json.dumps(serializable, indent=2, sort_keys=True))
