"""Cálculo de la posición y el contenido de las cajas de stats por asiento.

Dada la geometría de la ventana de mesa (T4, :class:`poker_hud.overlay.WindowGeometry`)
y el número de asiento, esta lógica decide dónde en pantalla debe dibujarse
la cajita de stats de ese asiento. Es pura y determinista (sin tocar X11,
Tkinter ni disco), así que es la parte del overlay que sí se puede cubrir
con tests unitarios; el renderizado real de las ventanas vive en
:mod:`poker_hud.overlay.hud` y ese sí depende de tener un servidor X activo
(ver el docstring de ese módulo para el porqué no está testeado).

Convención de asientos (asunción de diseño, documentada explícitamente
porque no se deriva de ningún dato externo): las cajas se reparten en una
elipse centrada en la mesa, con el asiento 1 abajo del centro (como el
hero en la UI de PokerStars) y los siguientes asientos en sentido horario
según se ve en pantalla, espaciados uniformemente según ``max_seats``.
Es una aproximación geométrica razonable sin analizar el fieltro de la
mesa píxel a píxel; si en el futuro hace falta más precisión (asientos no
uniformes, mesas ovaladas de verdad) esta es la función a ajustar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from poker_hud.overlay import WindowGeometry
    from poker_hud.stats import PlayerStats

__all__ = [
    "DEFAULT_BOX_WIDTH",
    "DEFAULT_BOX_HEIGHT",
    "SeatBox",
    "compute_seat_position",
    "compute_seat_positions",
    "format_stats_line",
    "build_seat_boxes",
]

# Tamaño por defecto de cada caja, en píxeles. Lo bastante pequeño para no
# tapar las cartas/fichas de la mesa, lo bastante grande para leer 4 stats.
DEFAULT_BOX_WIDTH = 150
DEFAULT_BOX_HEIGHT = 46

# Fracción del semieje de la mesa que ocupa el radio de la elipse sobre la
# que se reparten los asientos. <1.0 para que las cajas queden dentro de la
# ventana de mesa incluso con su propio ancho/alto restado.
_RADIUS_RATIO = 0.42


@dataclass(frozen=True)
class SeatBox:
    """Posición y tamaño (en coordenadas absolutas de pantalla) de la caja de un asiento."""

    seat: int
    x: int
    y: int
    width: int
    height: int
    text: str = ""


def _seat_angle(seat: int, max_seats: int) -> float:
    """Ángulo (radianes) del asiento sobre la elipse de reparto.

    ``seat`` es 1-indexado, igual que en :class:`poker_hud.parser.Player`.
    El asiento 1 va a 90° (abajo del centro, ver docstring del módulo) y
    cada asiento siguiente suma ``360 / max_seats`` grados en sentido
    horario tal y como se ve en pantalla.
    """

    if max_seats <= 0:
        raise ValueError("max_seats debe ser mayor que 0")
    return math.pi / 2 + (seat - 1) * (2 * math.pi / max_seats)


def compute_seat_position(
    table_geometry: "WindowGeometry",
    seat: int,
    max_seats: int,
    box_width: int = DEFAULT_BOX_WIDTH,
    box_height: int = DEFAULT_BOX_HEIGHT,
) -> tuple[int, int]:
    """Esquina superior izquierda (x, y absolutos de pantalla) de la caja de ``seat``.

    El resultado se recorta (clamp) para que la caja quede siempre dentro
    de los límites de la ventana de mesa, incluso para asientos cuyo punto
    en la elipse caiga muy cerca del borde.
    """

    angle = _seat_angle(seat, max_seats)

    center_x = table_geometry.x + table_geometry.width / 2
    center_y = table_geometry.y + table_geometry.height / 2
    radius_x = table_geometry.width / 2 * _RADIUS_RATIO
    radius_y = table_geometry.height / 2 * _RADIUS_RATIO

    point_x = center_x + radius_x * math.cos(angle)
    point_y = center_y + radius_y * math.sin(angle)

    x = round(point_x - box_width / 2)
    y = round(point_y - box_height / 2)

    min_x = table_geometry.x
    max_x = table_geometry.x + table_geometry.width - box_width
    min_y = table_geometry.y
    max_y = table_geometry.y + table_geometry.height - box_height

    # Si la caja es más grande que la propia ventana, min_x > max_x: en ese
    # caso degeneramos a la esquina superior izquierda de la ventana en
    # vez de dejar la caja fuera de rango.
    x = min(max(x, min_x), max_x) if max_x >= min_x else min_x
    y = min(max(y, min_y), max_y) if max_y >= min_y else min_y

    return x, y


def compute_seat_positions(
    table_geometry: "WindowGeometry",
    max_seats: int,
    box_width: int = DEFAULT_BOX_WIDTH,
    box_height: int = DEFAULT_BOX_HEIGHT,
) -> dict[int, tuple[int, int]]:
    """Posición de la caja de cada asiento de 1 a ``max_seats``."""

    return {
        seat: compute_seat_position(table_geometry, seat, max_seats, box_width, box_height)
        for seat in range(1, max_seats + 1)
    }


def format_stats_line(screen_name: str | None, stats: "PlayerStats | None") -> str:
    """Texto a mostrar en la caja de un asiento.

    Un asiento vacío (``screen_name`` es ``None``) se muestra en blanco. Un
    jugador sentado del que aún no hay manos registradas (``stats`` es
    ``None``, o con ``hands_played == 0``) muestra sólo el nombre, sin
    stats en "-" para no dar una falsa sensación de dato real con 0 manos.
    """

    if screen_name is None:
        return ""

    if stats is None or stats.hands_played == 0:
        return f"{screen_name}\n- manos"

    def _fmt_pct(pct: float | None) -> str:
        return "-" if pct is None else f"{pct:.0f}%"

    return (
        f"{screen_name}\n"
        f"{stats.hands_played}m "
        f"V{_fmt_pct(stats.vpip_pct)} "
        f"P{_fmt_pct(stats.pfr_pct)} "
        f"3B{_fmt_pct(stats.three_bet_pct)}"
    )


def build_seat_boxes(
    table_geometry: "WindowGeometry",
    max_seats: int,
    seat_players: dict[int, str],
    get_stats: Callable[[str], "PlayerStats | None"],
    box_width: int = DEFAULT_BOX_WIDTH,
    box_height: int = DEFAULT_BOX_HEIGHT,
) -> list[SeatBox]:
    """Caja completa (posición + texto) de cada asiento de la mesa.

    ``seat_players`` mapea nº de asiento -> ``screen_name`` del jugador
    sentado ahí (asientos vacíos, sin entrada). ``get_stats`` es cualquier
    callable que dado un ``screen_name`` devuelva sus :class:`~poker_hud.stats.PlayerStats`
    (típicamente ``functools.partial(get_player_stats, conn)`` del motor
    de T2) o ``None`` si el jugador es nuevo y aún no hay stats para él.
    """

    positions = compute_seat_positions(table_geometry, max_seats, box_width, box_height)

    boxes = []
    for seat in range(1, max_seats + 1):
        screen_name = seat_players.get(seat)
        stats = get_stats(screen_name) if screen_name is not None else None
        x, y = positions[seat]
        boxes.append(
            SeatBox(
                seat=seat,
                x=x,
                y=y,
                width=box_width,
                height=box_height,
                text=format_stats_line(screen_name, stats),
            )
        )
    return boxes
