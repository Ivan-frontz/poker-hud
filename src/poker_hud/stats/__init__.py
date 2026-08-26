"""Motor de stats incremental por jugador (VPIP/PFR/3-bet/manos jugadas).

Consume las manos estructuradas que produce ``poker_hud.parser`` y va
acumulando, mano a mano, los contadores necesarios para calcular las
stats clásicas de HUD en SQLite:

- Manos jugadas.
- VPIP (Voluntarily Put money In Pot): % de manos en las que el
  jugador mete fichas de forma voluntaria preflop (call/bet/raise;
  no cuenta postear ciega o ante).
- PFR (PreFlop Raise): % de manos en las que el jugador sube preflop.
- 3-bet %: de las manos en las que el jugador tuvo la oportunidad de
  resubir (se encontró con exactamente una subida antes de actuar),
  en qué porcentaje subió él mismo (haciendo la segunda subida, el
  "3-bet").
- Fold al 3-bet % (T13): el reverso del 3-bet %. De las manos en las
  que el jugador abrió subiendo preflop (fue el primer raiser) y
  luego se encontró con un 3-bet de otro jugador, en qué porcentaje
  se retiró en vez de pagar o resubir (4-bet).
- Manos que vio el flop % (T13): de todas las manos jugadas, en qué
  porcentaje el jugador seguía en la mano cuando se repartió el flop
  (no se retiró durante la ronda preflop).

El estado se persiste en SQLite para poder consultarse de forma
incremental (cada mano nueva actualiza los contadores existentes) y
para que otros módulos, como el overlay (T5), puedan leer las stats
de un jugador dado su ``screen_name`` sin depender de tener el
historial completo en memoria.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass

from poker_hud.parser import ActionType, Hand, Street

__all__ = [
    "PlayerStats",
    "connect",
    "init_db",
    "update_stats",
    "update_stats_from_hands",
    "get_player_stats",
]

_VOLUNTARY_ACTION_TYPES = (ActionType.CALL, ActionType.BET, ActionType.RAISE)
_TRACKED_ACTION_TYPES = (
    ActionType.FOLD,
    ActionType.CHECK,
    ActionType.CALL,
    ActionType.BET,
    ActionType.RAISE,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS player_stats (
    screen_name TEXT PRIMARY KEY,
    hands_played INTEGER NOT NULL DEFAULT 0,
    vpip_count INTEGER NOT NULL DEFAULT 0,
    pfr_count INTEGER NOT NULL DEFAULT 0,
    three_bet_opportunities INTEGER NOT NULL DEFAULT 0,
    three_bet_count INTEGER NOT NULL DEFAULT 0,
    fold_to_3bet_opportunities INTEGER NOT NULL DEFAULT 0,
    fold_to_3bet_count INTEGER NOT NULL DEFAULT 0,
    saw_flop_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS processed_hands (
    hand_key TEXT PRIMARY KEY
);
"""


@dataclass
class PlayerStats:
    """Stats acumuladas de un jugador, tal y como se leen de SQLite."""

    screen_name: str
    hands_played: int
    vpip_count: int
    pfr_count: int
    three_bet_opportunities: int
    three_bet_count: int
    fold_to_3bet_opportunities: int = 0
    fold_to_3bet_count: int = 0
    saw_flop_count: int = 0

    @property
    def vpip_pct(self) -> float | None:
        return _pct(self.vpip_count, self.hands_played)

    @property
    def pfr_pct(self) -> float | None:
        return _pct(self.pfr_count, self.hands_played)

    @property
    def three_bet_pct(self) -> float | None:
        return _pct(self.three_bet_count, self.three_bet_opportunities)

    @property
    def fold_to_3bet_pct(self) -> float | None:
        return _pct(self.fold_to_3bet_count, self.fold_to_3bet_opportunities)

    @property
    def saw_flop_pct(self) -> float | None:
        return _pct(self.saw_flop_count, self.hands_played)


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return 100.0 * numerator / denominator


# app.py (T6) comparte una única conexión entre el hilo del watcher (T3,
# que escribe en cada mano nueva vía update_stats) y el hilo principal de
# Tkinter del overlay (T5, que lee vía get_player_stats en cada refresco).
# check_same_thread=False solo levanta la prohibición de sqlite3 de usar el
# objeto fuera de su hilo de creación; sqlite3 sigue sin ser seguro ante
# accesos concurrentes reales desde varios hilos, así que además serializamos
# toda entrada/salida con este lock.
_ACCESS_LOCK = threading.Lock()


def connect(db_path: str = ":memory:") -> sqlite3.Connection:
    """Abre (y crea si hace falta) la base de SQLite con el esquema de stats."""

    conn = sqlite3.connect(db_path, check_same_thread=False)
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def _preflop_events(hand: Hand) -> list[tuple[str, ActionType, int]]:
    """Secuencia de acciones preflop relevantes junto al nº de subidas ya vistas.

    Cada elemento es ``(jugador, tipo_de_accion, subidas_previas)``, donde
    ``subidas_previas`` es el número de RAISE preflop que ya se habían
    producido (por otros jugadores) en el momento de esta acción. Un
    jugador que actúa con ``subidas_previas == 1`` se encuentra ante una
    única subida abierta: esa es la oportunidad clásica de 3-bet.
    """

    events: list[tuple[str, ActionType, int]] = []
    raise_count = 0
    for action in hand.actions_for(Street.PREFLOP):
        if action.player is not None and action.action_type in _TRACKED_ACTION_TYPES:
            events.append((action.player, action.action_type, raise_count))
        if action.action_type is ActionType.RAISE:
            raise_count += 1
    return events


def _fold_to_3bet_facts(
    player_events: list[tuple[str, ActionType, int]],
) -> tuple[bool, bool]:
    """(oportunidad_de_fold_al_3bet, se_retiró) para las acciones de un jugador.

    Es el reverso de la lógica de 3-bet: en vez de mirar si el jugador se
    encuentra con exactamente una subida antes de actuar, busca la subida de
    apertura del propio jugador (``RAISE`` con ``subidas_previas == 0``) y
    mira su siguiente acción. ``subidas_previas`` en esa siguiente acción ya
    incluye la propia subida de apertura del jugador, así que una única
    subida adicional (el 3-bet en sí) se ve como ``subidas_previas == 2``,
    no ``1``. Si ese es el caso hubo oportunidad limpia; si esa siguiente
    acción es ``FOLD``, se retiró ante el 3-bet.
    """

    open_index = next(
        (
            i
            for i, (_, action_type, raises_before) in enumerate(player_events)
            if action_type is ActionType.RAISE and raises_before == 0
        ),
        None,
    )
    if open_index is None or open_index + 1 >= len(player_events):
        return False, False

    _, next_action_type, raises_before_next = player_events[open_index + 1]
    if raises_before_next != 2:
        return False, False

    return True, next_action_type is ActionType.FOLD


def _hand_key(hand: Hand) -> str:
    return f"{hand.tournament_id}:{hand.hand_id}"


def update_stats(conn: sqlite3.Connection, hand: Hand) -> None:
    """Actualiza de forma incremental las stats de todos los jugadores de ``hand``.

    Es idempotente: si la misma mano (identificada por torneo + hand id)
    ya se había procesado, se ignora en vez de contar dos veces.
    """

    with _ACCESS_LOCK:
        cur = conn.execute(
            "INSERT OR IGNORE INTO processed_hands (hand_key) VALUES (?)",
            (_hand_key(hand),),
        )
        if cur.rowcount == 0:
            return

        if hand.is_cancelled:
            conn.commit()
            return

        events = _preflop_events(hand)

        for player in hand.players:
            if player.is_sitting_out:
                continue

            player_events = [e for e in events if e[0] == player.name]
            vpip = any(
                action_type in _VOLUNTARY_ACTION_TYPES for _, action_type, _ in player_events
            )
            pfr = any(action_type is ActionType.RAISE for _, action_type, _ in player_events)

            facing_one_raise = [e for e in player_events if e[2] == 1]
            three_bet_opportunity = bool(facing_one_raise)
            three_bet_made = any(
                action_type is ActionType.RAISE for _, action_type, _ in facing_one_raise
            )

            fold_to_3bet_opportunity, fold_to_3bet_made = _fold_to_3bet_facts(player_events)

            saw_flop = not any(
                action_type is ActionType.FOLD for _, action_type, _ in player_events
            )

            conn.execute(
                """
                INSERT INTO player_stats (
                    screen_name, hands_played, vpip_count, pfr_count,
                    three_bet_opportunities, three_bet_count,
                    fold_to_3bet_opportunities, fold_to_3bet_count, saw_flop_count
                )
                VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(screen_name) DO UPDATE SET
                    hands_played = hands_played + 1,
                    vpip_count = vpip_count + excluded.vpip_count,
                    pfr_count = pfr_count + excluded.pfr_count,
                    three_bet_opportunities = three_bet_opportunities + excluded.three_bet_opportunities,
                    three_bet_count = three_bet_count + excluded.three_bet_count,
                    fold_to_3bet_opportunities = fold_to_3bet_opportunities + excluded.fold_to_3bet_opportunities,
                    fold_to_3bet_count = fold_to_3bet_count + excluded.fold_to_3bet_count,
                    saw_flop_count = saw_flop_count + excluded.saw_flop_count
                """,
                (
                    player.name,
                    int(vpip),
                    int(pfr),
                    int(three_bet_opportunity),
                    int(three_bet_made),
                    int(fold_to_3bet_opportunity),
                    int(fold_to_3bet_made),
                    int(saw_flop),
                ),
            )

        conn.commit()


def update_stats_from_hands(conn: sqlite3.Connection, hands: list[Hand]) -> None:
    for hand in hands:
        update_stats(conn, hand)


def get_player_stats(conn: sqlite3.Connection, screen_name: str) -> PlayerStats | None:
    """Devuelve las stats acumuladas de un jugador, o ``None`` si no hay datos."""

    with _ACCESS_LOCK:
        row = conn.execute(
            """
            SELECT screen_name, hands_played, vpip_count, pfr_count,
                   three_bet_opportunities, three_bet_count,
                   fold_to_3bet_opportunities, fold_to_3bet_count, saw_flop_count
            FROM player_stats
            WHERE screen_name = ?
            """,
            (screen_name,),
        ).fetchone()
    if row is None:
        return None
    return PlayerStats(*row)
