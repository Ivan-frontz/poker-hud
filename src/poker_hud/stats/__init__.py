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

El estado se persiste en SQLite para poder consultarse de forma
incremental (cada mano nueva actualiza los contadores existentes) y
para que otros módulos, como el overlay (T5), puedan leer las stats
de un jugador dado su ``screen_name`` sin depender de tener el
historial completo en memoria.
"""

from __future__ import annotations

import sqlite3
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
    three_bet_count INTEGER NOT NULL DEFAULT 0
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

    @property
    def vpip_pct(self) -> float | None:
        return _pct(self.vpip_count, self.hands_played)

    @property
    def pfr_pct(self) -> float | None:
        return _pct(self.pfr_count, self.hands_played)

    @property
    def three_bet_pct(self) -> float | None:
        return _pct(self.three_bet_count, self.three_bet_opportunities)


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return 100.0 * numerator / denominator


def connect(db_path: str = ":memory:") -> sqlite3.Connection:
    """Abre (y crea si hace falta) la base de SQLite con el esquema de stats."""

    conn = sqlite3.connect(db_path)
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


def _hand_key(hand: Hand) -> str:
    return f"{hand.tournament_id}:{hand.hand_id}"


def update_stats(conn: sqlite3.Connection, hand: Hand) -> None:
    """Actualiza de forma incremental las stats de todos los jugadores de ``hand``.

    Es idempotente: si la misma mano (identificada por torneo + hand id)
    ya se había procesado, se ignora en vez de contar dos veces.
    """

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
        vpip = any(action_type in _VOLUNTARY_ACTION_TYPES for _, action_type, _ in player_events)
        pfr = any(action_type is ActionType.RAISE for _, action_type, _ in player_events)

        facing_one_raise = [e for e in player_events if e[2] == 1]
        three_bet_opportunity = bool(facing_one_raise)
        three_bet_made = any(
            action_type is ActionType.RAISE for _, action_type, _ in facing_one_raise
        )

        conn.execute(
            """
            INSERT INTO player_stats (
                screen_name, hands_played, vpip_count, pfr_count,
                three_bet_opportunities, three_bet_count
            )
            VALUES (?, 1, ?, ?, ?, ?)
            ON CONFLICT(screen_name) DO UPDATE SET
                hands_played = hands_played + 1,
                vpip_count = vpip_count + excluded.vpip_count,
                pfr_count = pfr_count + excluded.pfr_count,
                three_bet_opportunities = three_bet_opportunities + excluded.three_bet_opportunities,
                three_bet_count = three_bet_count + excluded.three_bet_count
            """,
            (
                player.name,
                int(vpip),
                int(pfr),
                int(three_bet_opportunity),
                int(three_bet_made),
            ),
        )

    conn.commit()


def update_stats_from_hands(conn: sqlite3.Connection, hands: list[Hand]) -> None:
    for hand in hands:
        update_stats(conn, hand)


def get_player_stats(conn: sqlite3.Connection, screen_name: str) -> PlayerStats | None:
    """Devuelve las stats acumuladas de un jugador, o ``None`` si no hay datos."""

    row = conn.execute(
        """
        SELECT screen_name, hands_played, vpip_count, pfr_count,
               three_bet_opportunities, three_bet_count
        FROM player_stats
        WHERE screen_name = ?
        """,
        (screen_name,),
    ).fetchone()
    if row is None:
        return None
    return PlayerStats(*row)
