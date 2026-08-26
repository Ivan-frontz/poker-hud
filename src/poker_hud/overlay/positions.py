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

T23 (HUD multi-mesa): con más de una mesa de torneo abierta a la vez, el
mismo nº de asiento existe en cada una y el offset ajustado a mano en una
no tiene por qué valer para la otra. Por eso ``tournament_id`` (opcional
en ambas funciones) permite guardar/leer offsets con clave compuesta
``"<tournament_id>:<seat>"`` en vez de sólo ``"<seat>"``. Sin
``tournament_id`` (el valor por defecto) el comportamiento es exactamente
el de antes de T23, clave por asiento a secas -las entradas con clave
compuesta de otras mesas se ignoran en ese modo en vez de mezclarse-, así
que un fichero de posiciones de antes de T23 se sigue leyendo igual con la
v1 de una sola mesa.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["load_seat_positions", "save_seat_position"]


def _read_raw(path: Path) -> dict:
    """Contenido crudo (sin parsear a offsets) del fichero, o ``{}`` si no hay o es inválido.

    Separado de :func:`load_seat_positions` para que :func:`save_seat_position`
    pueda escribir de vuelta conservando entradas que no entiende -por
    ejemplo, offsets con clave compuesta de otra mesa (T23) cuando se llama
    sin ``tournament_id``-, en vez de perderlas al re-serializar sólo lo que
    supo parsear.
    """

    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _key(seat: int, tournament_id: str | None) -> str:
    return f"{tournament_id}:{seat}" if tournament_id is not None else str(seat)


def load_seat_positions(
    path: Path | str, tournament_id: str | None = None
) -> dict[int, tuple[int, int]]:
    """Offsets ``(dx, dy)`` guardados por asiento, o ``{}`` si no hay fichero o está corrupto.

    Un fichero ausente (primer arranque, nadie ajustó nunca ninguna caja) o
    con contenido inesperado (JSON corrupto, editado a mano de forma
    inválida) se trata igual que "sin posiciones guardadas" en vez de
    hacer fallar el arranque del HUD: cada asiento sin entrada válida cae
    en el cálculo automático de siempre.

    Con ``tournament_id`` (T23), sólo se devuelven los offsets guardados
    con esa clave compuesta (ver docstring del módulo); sin él, sólo los
    guardados con clave de asiento a secas -las entradas de otra mesa no
    parsean como ``int`` y se descartan igual que cualquier clave inválida-.
    """

    raw = _read_raw(Path(path))

    prefix = f"{tournament_id}:" if tournament_id is not None else None
    positions: dict[int, tuple[int, int]] = {}
    for raw_key, offset in raw.items():
        if prefix is not None:
            if not isinstance(raw_key, str) or not raw_key.startswith(prefix):
                continue
            seat_key = raw_key[len(prefix) :]
        else:
            seat_key = raw_key
        try:
            seat = int(seat_key)
            dx, dy = offset
            positions[seat] = (int(dx), int(dy))
        except (TypeError, ValueError):
            continue
    return positions


def save_seat_position(
    path: Path | str, seat: int, dx: int, dy: int, tournament_id: str | None = None
) -> None:
    """Guarda el offset de ``seat``, conservando el resto de entradas ya guardadas.

    Con ``tournament_id`` (T23) se guarda bajo la clave compuesta de esa
    mesa (ver docstring del módulo) sin tocar las entradas de otras mesas
    ni las de asiento a secas; conserva todo lo demás vía :func:`_read_raw`
    en vez de pasar por :func:`load_seat_positions`, que descartaría esas
    otras entradas al no saber parsearlas con el ``tournament_id`` dado.
    """

    path = Path(path)
    raw = _read_raw(path)
    raw[_key(seat, tournament_id)] = [dx, dy]

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2, sort_keys=True))
