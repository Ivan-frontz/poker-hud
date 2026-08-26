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

from poker_hud.parser import Hand
from poker_hud.stats import connect
from poker_hud.watcher import HandHistoryWatcher

__all__ = ["main", "build_arg_parser", "SharedTableState"]

_DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "poker-hud" / "stats.db"


class SharedTableState:
    """Última alineación de asientos conocida, compartida entre el hilo del watcher y el overlay.

    El watcher procesa manos en su propio hilo; el overlay lee el estado
    desde el hilo principal (bucle de eventos de Tkinter). El lock protege
    la única variable compartida entre ambos.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seat_players: dict[int, str] = {}
        self._max_seats = 0

    def on_hand(self, hand: Hand) -> None:
        seats = {p.seat: p.name for p in hand.players if not p.is_sitting_out}
        with self._lock:
            self._seat_players = seats
            self._max_seats = hand.max_seats

    def get_current_players(self) -> dict[int, str]:
        with self._lock:
            return dict(self._seat_players)

    def get_max_seats(self) -> int:
        """Tamaño real de la mesa (T1), 0 si aún no se procesó ninguna mano.

        T11: no se puede inferir de forma fiable a partir de las claves de
        ``get_current_players()`` (nº de asiento ocupado más alto != nº de
        asientos de la mesa), así que viaja aparte desde ``Hand.max_seats``.
        """

        with self._lock:
            return self._max_seats


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

    run(
        state.get_current_players,
        conn,
        get_max_seats=state.get_max_seats,
        positions_path=positions_path,
    )
    return 0
