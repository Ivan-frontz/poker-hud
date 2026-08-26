"""Punto de entrada único: arranca el watcher (T3), las stats (T2) y el overlay (T5) juntos.

Cablea las tres piezas que hasta ahora solo se integraban en tests:

- El watcher (:mod:`poker_hud.watcher`) sondea la carpeta de hand history
  en un hilo aparte y, por cada mano nueva, actualiza las stats (T2, ya
  integrado dentro del propio watcher) y notifica el asiento->jugador de
  la mano en curso vía :class:`SharedTableState`.
- El overlay (:mod:`poker_hud.overlay.hud`) corre en el hilo principal
  (Tkinter exige que su bucle de eventos viva en el hilo principal) y en
  cada refresco pregunta a ``SharedTableState`` quién está sentado en cada
  asiento ahora mismo, y a T2 (vía la misma conexión SQLite) las stats de
  cada uno.

``tkinter`` solo se importa dentro de :func:`main`, después de validar los
argumentos: así ``python -m poker_hud --help`` (o un error de argumentos)
no requiere tener un servidor X ni ``python3-tk`` instalados.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

from poker_hud.overlay.layout import DEFAULT_OPACITY
from poker_hud.parser import Hand
from poker_hud.stats import connect
from poker_hud.watcher import HandHistoryWatcher

__all__ = ["main", "build_arg_parser", "SharedTableState"]

_DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "poker-hud" / "stats.db"


class SharedTableState:
    """Última alineación de asientos conocida por torneo, compartida entre el hilo del watcher y el overlay.

    El watcher procesa manos en su propio hilo; el overlay lee el estado
    desde el hilo principal (bucle de eventos de Tkinter). El lock protege
    las variables compartidas entre ambos.

    T22: PokerStars.ES guarda todas las manos de todos los torneos
    simultáneos de un mismo nick en la misma carpeta, así que el watcher ya
    procesa manos de varias mesas a la vez. El estado se indexa por
    ``hand.tournament_id`` para no mezclar los asientos de una mesa con los
    de otra; capa de datos únicamente, sin conectar todavía con el overlay
    (eso es T23/T24).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seat_players: dict[str, dict[int, str]] = {}
        self._max_seats: dict[str, int] = {}

    def on_hand(self, hand: Hand) -> None:
        seats = {p.seat: p.name for p in hand.players if not p.is_sitting_out}
        with self._lock:
            self._seat_players[hand.tournament_id] = seats
            self._max_seats[hand.tournament_id] = hand.max_seats

    def get_current_players(self, tournament_id: str) -> dict[int, str]:
        with self._lock:
            return dict(self._seat_players.get(tournament_id, {}))

    def get_max_seats(self, tournament_id: str) -> int:
        """Tamaño real de la mesa (T1) del torneo dado, 0 si aún no se procesó ninguna mano suya.

        T11: no se puede inferir de forma fiable a partir de las claves de
        ``get_current_players()`` (nº de asiento ocupado más alto != nº de
        asientos de la mesa), así que viaja aparte desde ``Hand.max_seats``.
        """

        with self._lock:
            return self._max_seats.get(tournament_id, 0)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m poker_hud",
        description=(
            "HUD casero para PokerStars: arranca en un único proceso el watcher de "
            "hand history, el motor de stats y el overlay sobre la mesa."
        ),
    )
    parser.add_argument(
        "--hand-history-dir",
        required=True,
        help=(
            "Carpeta de hand history de TORNEOS de PokerStars a vigilar "
            "(ver README para cómo activar su guardado y encontrar la ruta)."
        ),
    )
    parser.add_argument(
        "--db-path",
        default=str(_DEFAULT_DB_PATH),
        help=f"Fichero SQLite donde persisten las stats (por defecto {_DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Segundos entre sondeos de la carpeta de hand history (por defecto 2.0).",
    )
    parser.add_argument(
        "--opacity",
        type=float,
        default=DEFAULT_OPACITY,
        help=(
            "Opacidad de las cajas del HUD, de 0.0 (invisible) a 1.0 (opaca) "
            f"(por defecto {DEFAULT_OPACITY}). Requiere un gestor de ventanas con "
            "compositor activo; sin uno, Tk puede ignorarlo y la caja queda opaca "
            "igual (ver poker_hud.overlay.hud)."
        ),
    )
    parser.add_argument(
        "--tournament-id",
        default=None,
        help=(
            "Fija el HUD a la mesa de este ID de torneo en vez de seguir la primera "
            "mesa de PokerStars detectada (v1 es de una sola mesa: con más de una "
            "abierta a la vez el orden de wmctrl no es estable entre sondeos y el HUD "
            "salta de una a otra). Sin este flag, se mantiene el comportamiento actual."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not os.path.isdir(args.hand_history_dir):
        print(
            f"error: la carpeta de hand history no existe: {args.hand_history_dir}\n"
            "Revisa el README para activar el guardado de hand history de torneos "
            "en las opciones de PokerStars y localizar la carpeta.",
            file=sys.stderr,
        )
        return 1

    if not 0.0 <= args.opacity <= 1.0:
        print(
            f"error: --opacity debe estar entre 0.0 y 1.0 (recibido: {args.opacity}).",
            file=sys.stderr,
        )
        return 1

    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(db_path))

    state = SharedTableState()
    watcher = HandHistoryWatcher(args.hand_history_dir, conn, on_hand=state.on_hand)

    watcher_thread = threading.Thread(
        target=watcher.run_forever,
        kwargs={"interval_seconds": args.poll_interval},
        daemon=True,
    )
    watcher_thread.start()

    from poker_hud.overlay.hud import run

    # T16: junto al fichero de stats, no dentro (posición de caja es un
    # dato de presentación del overlay, no una stat de jugador de T2).
    positions_path = db_path.parent / "seat_positions.json"

    # T22: SharedTableState pasó a indexar por tournament_id, pero el
    # cableado a un único ``run()`` con un solo torneo sigue pendiente de
    # T23/T24 (multi-mesa real en el overlay). No se toca aquí a propósito.
    run(
        state.get_current_players,
        conn,
        get_max_seats=state.get_max_seats,
        positions_path=positions_path,
        tournament_id=args.tournament_id,
        opacity=args.opacity,
    )
    return 0
