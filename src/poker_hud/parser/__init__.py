"""Parser de hand history de PokerStars (torneos, una mesa por mano).

Convierte el texto plano que escribe el cliente de PokerStars en el
directorio de hand history de torneos a una representación estructurada
por mano (:class:`Hand`), lista para que el motor de stats la consuma.

A diferencia del cash game, en los torneos:

- La cabecera incluye el ID del torneo y el buy-in, no un par de blinds
  fijo para todo el fichero.
- Las ciegas vienen dadas por el nivel (``Level``, numeración romana) y
  suben mano a mano según avanza el torneo; cada mano lleva su propio
  nivel y su propio par small blind/big blind.
- A partir de cierto nivel se paga ante por jugador.
- Los importes de fichas de torneo no llevan símbolo de moneda (a
  diferencia del buy-in, que sí lo lleva).
- El número de asientos de la mesa baja según el torneo se reduce
  (mesa final incluida) y los jugadores se van eliminando.

No depende de ningún otro módulo del proyecto.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum

__all__ = [
    "Action",
    "ActionType",
    "Hand",
    "ParseError",
    "Player",
    "Street",
    "parse_hand",
    "parse_hands",
    "parse_file",
]


class ParseError(ValueError):
    """El texto no tiene el formato de hand history de PokerStars esperado."""


class Street(str, Enum):
    PREFLOP = "PREFLOP"
    FLOP = "FLOP"
    TURN = "TURN"
    RIVER = "RIVER"
    SHOWDOWN = "SHOWDOWN"


class ActionType(str, Enum):
    POST_ANTE = "POST_ANTE"
    POST_SB = "POST_SB"
    POST_BB = "POST_BB"
    POST_DEAD = "POST_DEAD"
    FOLD = "FOLD"
    CHECK = "CHECK"
    CALL = "CALL"
    BET = "BET"
    RAISE = "RAISE"
    ALL_IN = "ALL_IN"
    UNCALLED_RETURN = "UNCALLED_RETURN"
    COLLECT = "COLLECT"
    SHOW = "SHOW"
    MUCK = "MUCK"


@dataclass
class Player:
    """Un jugador tal y como aparece sentado en la mesa al inicio de la mano."""

    seat: int
    name: str
    chips: Decimal
    is_button: bool = False
    position: str | None = None
    cards: list[str] | None = None
    is_sitting_out: bool = False


@dataclass
class Action:
    """Una acción de un jugador (o del propio dealer) dentro de una calle."""

    street: Street
    player: str | None
    action_type: ActionType
    amount: Decimal | None = None
    to_amount: Decimal | None = None
    is_all_in: bool = False
    cards: list[str] | None = None
    raw: str = ""


@dataclass
class Hand:
    """Representación estructurada de una mano de torneo de PokerStars."""

    hand_id: str
    tournament_id: str
    buy_in: str
    game: str
    limit: str
    level: str
    small_blind: Decimal
    big_blind: Decimal
    currency: str
    timestamp: datetime
    table_name: str
    max_seats: int
    button_seat: int
    ante: Decimal = Decimal("0")
    players: list[Player] = field(default_factory=list)
    board: list[str] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    winners: list[tuple[str, Decimal, str]] = field(default_factory=list)
    eliminations: list[tuple[str, int]] = field(default_factory=list)
    is_cancelled: bool = False
    rake: Decimal | None = None
    pot: Decimal | None = None
    hero_name: str | None = None
    raw_text: str = ""

    def actions_for(self, street: Street) -> list[Action]:
        return [a for a in self.actions if a.street == street]

    def player_by_name(self, name: str) -> Player | None:
        for p in self.players:
            if p.name == name:
                return p
        return None


# ---------------------------------------------------------------------------
# Expresiones regulares
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(
    r"^PokerStars Hand #(?P<hand_id>\d+): Tournament #(?P<tournament_id>\d+), "
    r"(?P<buyin>\S+) (?P<currency>[A-Z]+) "
    r"(?P<game>.+?) - Level (?P<level>[IVXLCDM]+|\d+) "
    r"\((?P<sb>[\d,]+)/(?P<bb>[\d,]+)\)"
    r" - (?P<timestamp>\d{4}/\d{2}/\d{2} \d{1,2}:\d{2}:\d{2}) (?P<tz>\S+)"
    r"(?:\s*\[[^\]]*\])?\s*$"
)

_TABLE_RE = re.compile(
    r"^Table '(?P<table>[^']*)' (?P<max_seats>\d+)-max"
    r"(?: Seat #(?P<button>\d+) is the button)?\s*$"
)

_SEAT_RE = re.compile(
    r"^Seat (?P<seat>\d+): (?P<name>.+?) \((?P<chips>[\d,]+(?:\.\d+)?) in chips\)"
    r"(?P<sitout> is sitting out)?\s*$"
)

# Importante: en torneos los importes de fichas no llevan símbolo de
# moneda, pero dejamos el símbolo opcional para no romper si el propio
# PokerStars lo incluyera en alguna variante regional.
_MONEY = r"\$?(?P<amount>[\d,]+(?:\.\d+)?)"
_ALLIN_SUFFIX = r"(?P<allin> and is all-in)?"

_POST_ANTE_RE = re.compile(rf"^(?P<name>.+?): posts the ante {_MONEY}{_ALLIN_SUFFIX}\s*$")
_POST_SB_RE = re.compile(rf"^(?P<name>.+?): posts small blind {_MONEY}{_ALLIN_SUFFIX}\s*$")
_POST_BB_RE = re.compile(rf"^(?P<name>.+?): posts big blind {_MONEY}{_ALLIN_SUFFIX}\s*$")
_POST_DEAD_RE = re.compile(
    rf"^(?P<name>.+?): posts small (?:&|and) big blind[s]? {_MONEY}{_ALLIN_SUFFIX}\s*$"
)

_FOLD_RE = re.compile(r"^(?P<name>.+?): folds\s*$")
_CHECK_RE = re.compile(r"^(?P<name>.+?): checks\s*$")
_CALL_RE = re.compile(rf"^(?P<name>.+?): calls {_MONEY}{_ALLIN_SUFFIX}\s*$")
_BET_RE = re.compile(rf"^(?P<name>.+?): bets {_MONEY}{_ALLIN_SUFFIX}\s*$")
_RAISE_RE = re.compile(
    rf"^(?P<name>.+?): raises {_MONEY} to \$?(?P<to>[\d,]+(?:\.\d+)?)"
    rf"{_ALLIN_SUFFIX}\s*$"
)

_UNCALLED_RE = re.compile(
    rf"^Uncalled bet \({_MONEY}\) returned to (?P<name>.+?)\s*$"
)
_COLLECT_RE = re.compile(
    rf"^(?P<name>.+?) collected {_MONEY} from (?:main |side )?pot\s*$"
)
_WINS_RE = re.compile(
    rf"^(?P<name>.+?) wins {_MONEY}(?: from (?:main |side )?pot)?\s*$"
)

_SHOWS_RE = re.compile(
    r"^(?P<name>.+?): shows \[(?P<cards>[^\]]+)\]"
)
_MUCKS_RE = re.compile(r"^(?P<name>.+?): mucks hand\s*$")

_DEALT_RE = re.compile(r"^Dealt to (?P<name>.+?) \[(?P<cards>[^\]]+)\]\s*$")

_STREET_HEADERS = {
    "*** HOLE CARDS ***": Street.PREFLOP,
    "*** FLOP ***": Street.FLOP,
    "*** TURN ***": Street.TURN,
    "*** RIVER ***": Street.RIVER,
    "*** SHOW DOWN ***": Street.SHOWDOWN,
}

_FLOP_RE = re.compile(r"^\*\*\* FLOP \*\*\* \[(?P<cards>[^\]]+)\]\s*$")
_TURN_RE = re.compile(
    r"^\*\*\* TURN \*\*\* \[[^\]]+\] \[(?P<card>[^\]]+)\]\s*$"
)
_RIVER_RE = re.compile(
    r"^\*\*\* RIVER \*\*\* \[[^\]]+\] \[(?P<card>[^\]]+)\]\s*$"
)

_SUMMARY_BOARD_RE = re.compile(r"^Board \[(?P<cards>[^\]]*)\]\s*$")

_ELIMINATED_RE = re.compile(
    r"^(?P<name>.+?) finished the tournament in (?P<place>\d+)(?:st|nd|rd|th) place"
)

_CANCELLED_MARKERS = (
    "Hand cancelled",
    "hand is cancelled",
    "was cancelled",
)


def _to_decimal(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def _split_hands(text: str) -> list[str]:
    """Divide el contenido de un fichero de historial en bloques por mano.

    Las notas de eliminación de jugador ("... finished the tournament in
    Nth place") que PokerStars añade justo después del resumen de la mano
    en la que se produce la baja quedan pegadas al bloque de esa mano.
    """

    normalized = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n(?=PokerStars Hand #)", normalized.strip())
    return [b.strip("\n") for b in blocks if b.strip()]


def parse_file(path: str) -> list[Hand]:
    """Lee un fichero de hand history y devuelve la lista de manos parseadas."""

    with open(path, encoding="utf-8-sig") as fh:
        content = fh.read()
    return parse_hands(content)


def parse_hands(text: str) -> list[Hand]:
    """Parsea un fichero completo (potencialmente con varias manos)."""

    return [parse_hand(block) for block in _split_hands(text)]


def parse_hand(text: str) -> Hand:  # noqa: C901 - parser secuencial, complejidad intrínseca
    """Parsea el texto de una única mano de torneo de PokerStars."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    lines = normalized.split("\n")
    if not lines:
        raise ParseError("Texto vacío")

    header_match = _HEADER_RE.match(lines[0])
    if not header_match:
        raise ParseError(f"Cabecera no reconocida: {lines[0]!r}")

    game_full = header_match.group("game")
    limit = "No Limit" if "No Limit" in game_full else game_full
    timestamp = datetime.strptime(header_match.group("timestamp"), "%Y/%m/%d %H:%M:%S")

    hand = Hand(
        hand_id=header_match.group("hand_id"),
        tournament_id=header_match.group("tournament_id"),
        buy_in=header_match.group("buyin"),
        game=game_full,
        limit=limit,
        level=header_match.group("level"),
        small_blind=_to_decimal(header_match.group("sb")),
        big_blind=_to_decimal(header_match.group("bb")),
        currency=header_match.group("currency"),
        timestamp=timestamp,
        table_name="",
        max_seats=0,
        button_seat=0,
        raw_text=text,
    )

    idx = 1
    table_match = _TABLE_RE.match(lines[idx]) if idx < len(lines) else None
    if not table_match:
        raise ParseError(f"Línea de mesa no reconocida: {lines[idx]!r}")
    hand.table_name = table_match.group("table")
    hand.max_seats = int(table_match.group("max_seats"))
    hand.button_seat = int(table_match.group("button")) if table_match.group("button") else 0
    idx += 1

    # --- Asientos ---
    while idx < len(lines):
        seat_match = _SEAT_RE.match(lines[idx])
        if not seat_match:
            break
        player = Player(
            seat=int(seat_match.group("seat")),
            name=seat_match.group("name"),
            chips=_to_decimal(seat_match.group("chips")),
            is_button=int(seat_match.group("seat")) == hand.button_seat,
            is_sitting_out=bool(seat_match.group("sitout")),
        )
        hand.players.append(player)
        idx += 1

    _assign_positions(hand)

    current_street = Street.PREFLOP
    cancelled = False

    # --- Resto de líneas: antes, blinds, acciones, calles, resumen ---
    while idx < len(lines):
        line = lines[idx]
        idx += 1
        stripped = line.strip()
        if not stripped:
            continue

        if any(marker in stripped for marker in _CANCELLED_MARKERS):
            cancelled = True
            continue

        if stripped in _STREET_HEADERS:
            current_street = _STREET_HEADERS[stripped]
            continue

        if stripped.startswith("*** FLOP ***"):
            m = _FLOP_RE.match(stripped)
            if m:
                hand.board = [c.strip() for c in m.group("cards").split()]
            current_street = Street.FLOP
            continue

        if stripped.startswith("*** TURN ***"):
            m = _TURN_RE.match(stripped)
            if m:
                hand.board.append(m.group("card").strip())
            current_street = Street.TURN
            continue

        if stripped.startswith("*** RIVER ***"):
            m = _RIVER_RE.match(stripped)
            if m:
                hand.board.append(m.group("card").strip())
            current_street = Street.RIVER
            continue

        if stripped.startswith("*** SUMMARY ***"):
            _parse_summary(hand, lines[idx:])
            break

        if stripped.startswith("*** ") and stripped.endswith(" ***"):
            # Otras cabeceras informativas no relevantes para la acción.
            continue

        m = _DEALT_RE.match(stripped)
        if m:
            player = hand.player_by_name(m.group("name"))
            cards = m.group("cards").split()
            if player is not None:
                player.cards = cards
            if hand.hero_name is None:
                hand.hero_name = m.group("name")
            continue

        action = _parse_action_line(stripped, current_street)
        if action is not None:
            hand.actions.append(action)
            continue

        # Línea no reconocida (mensajes de chat, "is connected", etc.): se ignora.

    hand.is_cancelled = cancelled or hand.pot == Decimal("0") and not hand.winners

    ante_amounts = [a.amount for a in hand.actions if a.action_type is ActionType.POST_ANTE]
    if ante_amounts:
        hand.ante = max(ante_amounts)

    return hand


def _assign_positions(hand: Hand) -> None:
    """Asigna posiciones (BTN/SB/BB/UTG.../CO) a partir del asiento del botón."""

    players = hand.players
    n = len(players)
    if n == 0 or hand.button_seat == 0:
        return

    ordered = sorted(players, key=lambda p: p.seat)
    seats = [p.seat for p in ordered]
    if hand.button_seat not in seats:
        return
    btn_idx = seats.index(hand.button_seat)
    rotated = ordered[btn_idx:] + ordered[:btn_idx]

    if n == 2:
        # Heads-up (mesa final a dos jugadores): el botón también es small blind.
        labels = ["BTN/SB", "BB"]
    else:
        labels = ["BTN", "SB", "BB"]
        remaining = n - 3
        if remaining == 1:
            labels.append("UTG")
        elif remaining >= 2:
            utg_count = remaining - 2
            labels += ["UTG" if i == 0 else f"UTG+{i}" for i in range(utg_count)]
            labels += ["HJ", "CO"]

    for player, label in zip(rotated, labels):
        player.position = label


def _parse_action_line(line: str, street: Street) -> Action | None:
    if m := _POST_ANTE_RE.match(line):
        return Action(
            street, m.group("name"), ActionType.POST_ANTE, _to_decimal(m.group("amount")),
            is_all_in=bool(m.group("allin")), raw=line,
        )
    if m := _POST_DEAD_RE.match(line):
        return Action(
            street, m.group("name"), ActionType.POST_DEAD, _to_decimal(m.group("amount")),
            is_all_in=bool(m.group("allin")), raw=line,
        )
    if m := _POST_SB_RE.match(line):
        return Action(
            street, m.group("name"), ActionType.POST_SB, _to_decimal(m.group("amount")),
            is_all_in=bool(m.group("allin")), raw=line,
        )
    if m := _POST_BB_RE.match(line):
        return Action(
            street, m.group("name"), ActionType.POST_BB, _to_decimal(m.group("amount")),
            is_all_in=bool(m.group("allin")), raw=line,
        )
    if m := _FOLD_RE.match(line):
        return Action(street, m.group("name"), ActionType.FOLD, raw=line)
    if m := _CHECK_RE.match(line):
        return Action(street, m.group("name"), ActionType.CHECK, raw=line)
    if m := _CALL_RE.match(line):
        return Action(
            street, m.group("name"), ActionType.CALL, _to_decimal(m.group("amount")),
            is_all_in=bool(m.group("allin")), raw=line,
        )
    if m := _BET_RE.match(line):
        return Action(
            street, m.group("name"), ActionType.BET, _to_decimal(m.group("amount")),
            is_all_in=bool(m.group("allin")), raw=line,
        )
    if m := _RAISE_RE.match(line):
        return Action(
            street, m.group("name"), ActionType.RAISE, _to_decimal(m.group("amount")),
            to_amount=_to_decimal(m.group("to")), is_all_in=bool(m.group("allin")), raw=line,
        )
    if m := _UNCALLED_RE.match(line):
        return Action(street, m.group("name"), ActionType.UNCALLED_RETURN, _to_decimal(m.group("amount")), raw=line)
    if m := _COLLECT_RE.match(line):
        return Action(street, m.group("name"), ActionType.COLLECT, _to_decimal(m.group("amount")), raw=line)
    if m := _SHOWS_RE.match(line):
        return Action(street, m.group("name"), ActionType.SHOW, cards=m.group("cards").split(), raw=line)
    if m := _MUCKS_RE.match(line):
        return Action(street, m.group("name"), ActionType.MUCK, raw=line)
    return None


def _parse_summary(hand: Hand, lines: list[str]) -> None:
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if m := _ELIMINATED_RE.match(stripped):
            hand.eliminations.append((m.group("name"), int(m.group("place"))))
            continue

        if m := re.match(rf"^Total pot {_MONEY.replace('amount', 'pot')}", stripped):
            hand.pot = _to_decimal(m.group("pot"))
        if m := re.search(r"Rake \$?(?P<rake>[\d,]+(?:\.\d+)?)", stripped):
            hand.rake = _to_decimal(m.group("rake"))

        if m := _SUMMARY_BOARD_RE.match(stripped):
            cards = m.group("cards").split()
            if cards:
                hand.board = cards

        if m := _WINS_RE.match(stripped):
            hand.winners.append((m.group("name"), _to_decimal(m.group("amount")), "wins"))
            continue
        if m := _COLLECT_RE.match(stripped):
            hand.winners.append((m.group("name"), _to_decimal(m.group("amount")), "collected"))
            continue

        seat_summary = re.match(
            r"^Seat (?P<seat>\d+): (?P<name>.+?) "
            r"(?:\((?P<pos>[^)]+)\) )?"
            r"(?P<rest>.+)$",
            stripped,
        )
        if seat_summary and ("collected" in seat_summary.group("rest") or "won" in seat_summary.group("rest")):
            m2 = re.search(rf"(?:collected|won) \({_MONEY}\)", seat_summary.group("rest"))
            if m2:
                hand.winners.append(
                    (seat_summary.group("name"), _to_decimal(m2.group("amount")), "collected")
                )
