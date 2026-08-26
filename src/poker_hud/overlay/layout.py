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
    "DEFAULT_OPACITY",
    "DEFAULT_MAX_SEATS",
    "COLOR_NAME",
    "COLOR_HANDS",
    "COLOR_VPIP",
    "COLOR_PFR",
    "COLOR_THREE_BET",
    "COLOR_FOLD_TO_3BET",
    "COLOR_SAW_FLOP",
    "StatSegment",
    "SeatBox",
    "compute_seat_position",
    "compute_seat_positions",
    "resolve_seat_position",
    "format_stats_line",
    "build_seat_boxes",
    "resolve_max_seats",
]

# Tamaño por defecto de cada caja, en píxeles. Lo bastante pequeño para no
# tapar las cartas/fichas de la mesa, lo bastante grande para leer las 6
# stats repartidas en 3 líneas (nombre, manos+vio flop+VPIP+PFR+3-bet, fold
# a 3-bet; T13 añadió la tercera línea y T15 reordenó la segunda). T25 bajó
# ambos valores a pedido de Ivan probando en vivo ("la caja tapa de más");
# el texto ahora entra igual con margen porque T25 también quitó el "%" de
# cada stat (menos caracteres por línea, ver ``_fmt_pct``) y bajó el tamaño
# de fuente de la caja (``SeatBoxWindow`` en overlay/hud.py).
DEFAULT_BOX_WIDTH = 120
DEFAULT_BOX_HEIGHT = 48

# Opacidad por defecto de las cajas (T20; bajada desde 0.80, que tapaba
# demasiado la mesa de detrás). Vive aquí -no en overlay.hud, que ya importa
# tkinter- para que app.py pueda usarla como default de ``--opacity`` sin
# tener que importar tkinter sólo para leer un argumento de la CLI (ver
# comentario sobre el import perezoso de ``poker_hud.overlay.hud`` en
# app.py).
DEFAULT_OPACITY = 0.32

# Tamaño de mesa a asumir cuando todavía no se conoce el de la mesa real
# (por ejemplo, el watcher aún no procesó ninguna mano completa).
DEFAULT_MAX_SEATS = 9

# Fracción del semieje de la mesa que ocupa el radio de la elipse sobre la
# que se reparten los asientos. <1.0 para que las cajas queden dentro de la
# ventana de mesa incluso con su propio ancho/alto restado.
_RADIUS_RATIO = 0.42

# Colores por stat (T12): cada stat de la caja se pinta en un color propio
# en vez de un único verde, para poder distinguirlas de un vistazo. Elegidos
# por contraste sobre el fondo casi negro de la caja (``hud._BACKGROUND``),
# no por ningún significado semántico del color en sí.
COLOR_NAME = "#e0e0e0"
COLOR_HANDS = "#9e9e9e"
COLOR_VPIP = "#4fa8ff"
COLOR_PFR = "#ff9f40"
COLOR_THREE_BET = "#ff5c5c"
# T13: mismo criterio de contraste sobre _BACKGROUND que el resto de T12.
# Morado para fold al 3-bet y el verde que ya usaba el texto por defecto
# antes de T12 para manos que vieron el flop.
COLOR_FOLD_TO_3BET = "#b06cff"
COLOR_SAW_FLOP = "#39ff6a"


@dataclass(frozen=True)
class StatSegment:
    """Un tramo de texto de la caja de un asiento y el color en que se pinta."""

    text: str
    color: str


@dataclass(frozen=True)
class SeatBox:
    """Posición y tamaño (en coordenadas absolutas de pantalla) de la caja de un asiento."""

    seat: int
    x: int
    y: int
    width: int
    height: int
    segments: tuple[StatSegment, ...] = ()


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


def _clamp_to_table(
    table_geometry: "WindowGeometry",
    x: int,
    y: int,
    box_width: int,
    box_height: int,
) -> tuple[int, int]:
    """Recorta ``(x, y)`` para que la caja quede dentro de los límites de la ventana de mesa."""

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

    return _clamp_to_table(table_geometry, x, y, box_width, box_height)


def resolve_seat_position(
    table_geometry: "WindowGeometry",
    seat: int,
    max_seats: int,
    overrides: "dict[int, tuple[int, int]] | None" = None,
    box_width: int = DEFAULT_BOX_WIDTH,
    box_height: int = DEFAULT_BOX_HEIGHT,
) -> tuple[int, int]:
    """Posición final de la caja de ``seat`` (T16): la ajustada a mano si existe, si no la calculada.

    ``overrides`` mapea asiento -> ``(dx, dy)``, el offset (no coordenadas
    absolutas) que el usuario dejó la caja respecto a la esquina superior
    izquierda de la ventana de mesa la última vez que la arrastró (ver
    :mod:`poker_hud.overlay.positions`, que es quien persiste ese dict en
    disco). Guardar un offset relativo a la mesa en vez de coordenadas de
    pantalla es a propósito: si la ventana de mesa se mueve o cambia de
    tamaño en la siguiente partida, la posición ajustada a mano se sigue
    aplicando en el sitio correcto relativo a la mesa, no queda anclada a
    donde estaba la pantalla la noche que se arrastró.

    Un asiento sin entrada en ``overrides`` (el caso por defecto, sin
    ajustar nunca a mano) cae en :func:`compute_seat_position` como antes
    de T16. El resultado, en ambos casos, se recorta a los límites de la
    ventana de mesa (:func:`_clamp_to_table`) para que un offset guardado
    contra una mesa más grande no deje la caja fuera de una mesa más
    pequeña tras redimensionar.
    """

    if overrides and seat in overrides:
        dx, dy = overrides[seat]
        return _clamp_to_table(
            table_geometry, table_geometry.x + dx, table_geometry.y + dy, box_width, box_height
        )
    return compute_seat_position(table_geometry, seat, max_seats, box_width, box_height)


def compute_seat_positions(
    table_geometry: "WindowGeometry",
    max_seats: int,
    box_width: int = DEFAULT_BOX_WIDTH,
    box_height: int = DEFAULT_BOX_HEIGHT,
    overrides: "dict[int, tuple[int, int]] | None" = None,
) -> dict[int, tuple[int, int]]:
    """Posición de la caja de cada asiento de 1 a ``max_seats`` (ver :func:`resolve_seat_position`)."""

    return {
        seat: resolve_seat_position(table_geometry, seat, max_seats, overrides, box_width, box_height)
        for seat in range(1, max_seats + 1)
    }


def format_stats_line(screen_name: str | None, stats: "PlayerStats | None") -> list[StatSegment]:
    """Segmentos de texto a mostrar en la caja de un asiento, cada uno con su color (T12).

    Un asiento vacío (``screen_name`` es ``None``) no produce segmentos. Un
    jugador sentado del que aún no hay manos registradas (``stats`` es
    ``None``, o con ``hands_played == 0``) muestra sólo el nombre y "- manos",
    sin stats en "-" para no dar una falsa sensación de dato real con 0
    manos.

    Devuelve una lista de :class:`StatSegment` en vez de un string plano
    porque cada stat se pinta de un color distinto (T12: gris para el nº de
    manos, azul para VPIP, naranja para PFR, rojo para 3-bet; T13: morado
    para fold al 3-bet, verde para manos que vieron el flop); un único
    ``tk.Label`` con un solo ``fg`` no puede mezclar colores dentro del mismo
    texto, así que quien pinta esto (:class:`poker_hud.overlay.hud.SeatBoxWindow`)
    necesita el texto ya trozeado por color.

    Los dos stats de T13 arrancaron juntos en una segunda línea (en vez de
    ampliar la primera) para no saturar el ancho de la caja. T15 movió
    "vio el flop" (``SF``) a la primera línea, justo después de manos y
    antes de VPIP, a pedido de Ivan tras probar el HUD en vivo; fold al
    3-bet (``F3B``) se quedó solo en la segunda línea porque no se pidió
    moverlo y no había un lugar más natural en la primera. Ambas stats
    siguen abreviadas (``F3B``, ``SF``) para no saturar el ancho de la caja.
    """

    if screen_name is None:
        return []

    if stats is None or stats.hands_played == 0:
        return [
            StatSegment(f"{screen_name}\n", COLOR_NAME),
            StatSegment("- manos", COLOR_HANDS),
        ]

    def _fmt_pct(pct: float | None) -> str:
        # T25: sin el símbolo "%" a pedido de Ivan (la caja ya deja claro
        # por contexto/color que son porcentajes; el símbolo sólo ocupaba
        # espacio en una caja ya angosta). El nº de manos (``{n}m``, más
        # abajo) no pasa por acá y no le aplica este cambio.
        return "-" if pct is None else f"{pct:.0f}"

    return [
        StatSegment(f"{screen_name}\n", COLOR_NAME),
        StatSegment(f"{stats.hands_played}m ", COLOR_HANDS),
        StatSegment(f"SF{_fmt_pct(stats.saw_flop_pct)} ", COLOR_SAW_FLOP),
        StatSegment(f"V{_fmt_pct(stats.vpip_pct)} ", COLOR_VPIP),
        StatSegment(f"P{_fmt_pct(stats.pfr_pct)} ", COLOR_PFR),
        StatSegment(f"3B{_fmt_pct(stats.three_bet_pct)}\n", COLOR_THREE_BET),
        StatSegment(f"F3B{_fmt_pct(stats.fold_to_3bet_pct)}", COLOR_FOLD_TO_3BET),
    ]


def resolve_max_seats(max_seats: int | None, seat_players: dict[int, str]) -> int:
    """Nº real de asientos de la mesa a dibujar (T11).

    Usa el ``max_seats`` de la mano en curso (lo produce el parser en T1 a
    partir de la línea ``Table '...' N-max``) siempre que se conozca. Si
    todavía no llegó ninguna mano completa (``max_seats`` es ``None`` o
    ``0``), cae a estimarlo a partir del asiento ocupado más alto, o a
    :data:`DEFAULT_MAX_SEATS` si tampoco hay ningún jugador sentado todavía.

    Antes de T11 se usaba siempre ``max(seat_players)``: como
    ``seat_players`` es un ``dict`` asiento -> nombre, eso itera las CLAVES
    y devuelve el nº de asiento más alto *ocupado*, no la cantidad de
    asientos de la mesa. En una mesa de 9-max con jugadores sólo en
    asientos bajos (watcher con retraso, o eliminaciones recientes) se
    perdían asientos reales de la mesa.
    """

    if max_seats:
        return max_seats
    return max(seat_players, default=0) or DEFAULT_MAX_SEATS


def build_seat_boxes(
    table_geometry: "WindowGeometry",
    max_seats: int,
    seat_players: dict[int, str],
    get_stats: Callable[[str], "PlayerStats | None"],
    box_width: int = DEFAULT_BOX_WIDTH,
    box_height: int = DEFAULT_BOX_HEIGHT,
    overrides: "dict[int, tuple[int, int]] | None" = None,
) -> list[SeatBox]:
    """Caja completa (posición + texto) de cada asiento de la mesa.

    ``seat_players`` mapea nº de asiento -> ``screen_name`` del jugador
    sentado ahí (asientos vacíos, sin entrada). ``get_stats`` es cualquier
    callable que dado un ``screen_name`` devuelva sus :class:`~poker_hud.stats.PlayerStats`
    (típicamente ``functools.partial(get_player_stats, conn)`` del motor
    de T2) o ``None`` si el jugador es nuevo y aún no hay stats para él.
    ``overrides`` es el dict asiento -> offset manual de T16, ver
    :func:`resolve_seat_position`.
    """

    positions = compute_seat_positions(table_geometry, max_seats, box_width, box_height, overrides)

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
                segments=tuple(format_stats_line(screen_name, stats)),
            )
        )
    return boxes
