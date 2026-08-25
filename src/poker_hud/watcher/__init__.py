"""Watcher de la carpeta de hand history de torneos de PokerStars.

Vigila por *polling* (sin dependencias nativas de inotify, para no atarnos
a una plataforma concreta) la carpeta de hand history de torneos, que es
distinta de la carpeta de cash y debe indicarse explícitamente al crear el
watcher: PokerStars no separa "torneos" de "cash" por convención de
nombre de fichero, así que la elección de carpeta es responsabilidad de
quien configura el HUD.

Cada llamada a :meth:`HandHistoryWatcher.poll` hace un ciclo de sondeo:

- Descubre ficheros ``.txt`` nuevos en la carpeta (cada torneo nuevo
  empieza un fichero nuevo) y empieza a vigilarlos desde el principio.
- Lee los bytes añadidos desde el último sondeo a cada fichero ya
  conocido.
- Extrae las manos completas del texto acumulado y, por cada una,
  invoca el parser (T1, :func:`poker_hud.parser.parse_hand`) y
  actualiza las stats incrementales (T2, :func:`poker_hud.stats.update_stats`).

El punto delicado es no procesar una mano mientras el cliente de
PokerStars todavía la está escribiendo en disco. Una mano se considera
seguramente completa en dos situaciones:

1. Ya ha aparecido en el fichero el separador en blanco seguido de la
   cabecera de la mano siguiente (``PokerStars Hand #...``): si el
   cliente ya ha empezado a escribir la mano siguiente es porque la
   anterior quedó completamente escrita y cerrada.
2. Es la última mano del fichero (todavía no hay mano siguiente) pero el
   fichero lleva un ciclo de sondeo entero sin crecer *y* el texto
   acumulado ya incluye el bloque ``*** SUMMARY ***`` con el pot total,
   o un marcador de mano cancelada. Este caso cubre tanto una pausa real
   del jugador entre manos como el final de la sesión (torneo terminado,
   eliminado, o cliente cerrado) sin depender de que aparezca una mano
   siguiente que quizá nunca llegue.

Mientras no se cumpla ninguna de las dos condiciones, el texto se queda
en el buffer interno del fichero a la espera del siguiente sondeo.
"""

from __future__ import annotations

import glob
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from poker_hud.parser import Hand, ParseError, parse_hand
from poker_hud.stats import update_stats

__all__ = ["HandHistoryWatcher"]

_HAND_BOUNDARY_RE = re.compile(r"\n\s*\n(?=PokerStars Hand #)")

# Mismos marcadores que usa el parser (T1) para detectar manos canceladas.
_CANCELLED_MARKERS = (
    "Hand cancelled",
    "hand is cancelled",
    "was cancelled",
)


def _looks_like_complete_hand(block: str) -> bool:
    """Heurística de "esto ya no va a cambiar" para la última mano de un fichero.

    No sustituye al parser: solo decide si merece la pena intentar
    parsear un bloque que todavía no está confirmado por la aparición de
    la mano siguiente.
    """

    if any(marker in block for marker in _CANCELLED_MARKERS):
        return True
    return "*** SUMMARY ***" in block and "Total pot" in block


@dataclass
class _FileState:
    offset: int = 0
    buffer: str = ""


class HandHistoryWatcher:
    """Sondea una carpeta de hand history de torneos y procesa manos nuevas.

    ``conn`` es una conexión de :mod:`poker_hud.stats` (ver
    :func:`poker_hud.stats.connect`) ya inicializada; cada mano nueva y
    completa se pasa a :func:`poker_hud.stats.update_stats`, que ya es
    idempotente por mano, así que volver a ver el mismo bloque (por
    ejemplo tras reiniciar el watcher) no duplica stats.
    """

    def __init__(
        self,
        hand_history_dir: str | os.PathLike[str],
        conn: sqlite3.Connection,
        *,
        pattern: str = "*.txt",
        on_hand: Callable[[Hand], None] | None = None,
    ) -> None:
        self.hand_history_dir = str(hand_history_dir)
        self.conn = conn
        self.pattern = pattern
        self._on_hand = on_hand
        self._files: dict[str, _FileState] = {}
        self.errors: list[tuple[str, str]] = []

    def poll(self) -> list[Hand]:
        """Ejecuta un ciclo de sondeo y devuelve las manos nuevas ya procesadas."""

        new_hands: list[Hand] = []
        for path in self._discover_files():
            new_hands.extend(self._poll_file(path))
        return new_hands

    def run_forever(
        self,
        interval_seconds: float = 2.0,
        *,
        iterations: int | None = None,
    ) -> None:
        """Bucle de sondeo continuo, para uso en producción (no en tests)."""

        import time

        count = 0
        while iterations is None or count < iterations:
            self.poll()
            count += 1
            if iterations is None or count < iterations:
                time.sleep(interval_seconds)

    def _discover_files(self) -> list[str]:
        paths = sorted(glob.glob(os.path.join(self.hand_history_dir, self.pattern)))
        for path in paths:
            self._files.setdefault(path, _FileState())
        return paths

    def _poll_file(self, path: str) -> list[Hand]:
        state = self._files[path]
        try:
            size = os.path.getsize(path)
        except OSError:
            return []

        if size < state.offset:
            # Fichero truncado o rotado (no debería pasar con hand history
            # de PokerStars, pero mejor no atascarse si ocurre): reempezar.
            state.offset = 0
            state.buffer = ""

        grew = size > state.offset
        if grew:
            with open(path, "rb") as fh:
                fh.seek(state.offset)
                raw = fh.read()
            state.offset += len(raw)
            state.buffer += raw.decode("utf-8", errors="replace")

        normalized = state.buffer.replace("\r\n", "\n").replace("\r", "\n")
        parts = _HAND_BOUNDARY_RE.split(normalized)
        confirmed, remainder = parts[:-1], parts[-1]

        hands: list[Hand] = []
        for block in confirmed:
            block = block.strip("\n")
            if block:
                hand = self._process_block(path, block)
                if hand is not None:
                    hands.append(hand)

        if not grew:
            candidate = remainder.strip("\n")
            if candidate and _looks_like_complete_hand(candidate):
                hand = self._process_block(path, candidate)
                if hand is not None:
                    hands.append(hand)
                remainder = ""

        state.buffer = remainder
        return hands

    def _process_block(self, path: str, block: str) -> Hand | None:
        try:
            hand = parse_hand(block)
        except ParseError as exc:
            self.errors.append((path, str(exc)))
            return None

        update_stats(self.conn, hand)
        if self._on_hand is not None:
            self._on_hand(hand)
        return hand
