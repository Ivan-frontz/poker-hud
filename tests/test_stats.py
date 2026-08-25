from datetime import datetime
from decimal import Decimal

import pytest

from poker_hud.parser import Action, ActionType, Hand, Player, Street
from poker_hud.stats import connect, get_player_stats, update_stats, update_stats_from_hands


def _hand(hand_id, preflop_actions, *, tournament_id="T1", sitting_out=None, is_cancelled=False):
    """Construye una mano sintética a partir de una lista de acciones preflop.

    ``preflop_actions`` es una lista de tuplas ``(nombre, action_type)``, en
    el orden en que se producen. Los jugadores se sientan en el orden en
    que aparecen mencionados por primera vez.
    """

    sitting_out = sitting_out or set()
    names: list[str] = []
    for name, _ in preflop_actions:
        if name not in names:
            names.append(name)

    players = [
        Player(
            seat=i + 1,
            name=name,
            chips=Decimal("1500"),
            is_sitting_out=name in sitting_out,
        )
        for i, name in enumerate(names)
    ]

    hand = Hand(
        hand_id=str(hand_id),
        tournament_id=tournament_id,
        buy_in="$10+$1",
        game="Hold'em",
        limit="No Limit",
        level="I",
        small_blind=Decimal("25"),
        big_blind=Decimal("50"),
        currency="USD",
        timestamp=datetime(2024, 1, 1),
        table_name="Table 1",
        max_seats=9,
        button_seat=1,
        players=players,
        is_cancelled=is_cancelled,
    )
    for name, action_type in preflop_actions:
        hand.actions.append(Action(street=Street.PREFLOP, player=name, action_type=action_type))
    return hand


@pytest.fixture
def conn():
    return connect(":memory:")


def test_unknown_player_has_no_stats(conn):
    assert get_player_stats(conn, "Nadie") is None


def test_folding_every_hand_never_counts_as_vpip_or_pfr(conn):
    for i in range(10):
        hand = _hand(
            i,
            [("Alice", ActionType.FOLD), ("Bob", ActionType.CHECK)],
        )
        update_stats(conn, hand)

    stats = get_player_stats(conn, "Alice")
    assert stats.hands_played == 10
    assert stats.vpip_count == 0
    assert stats.vpip_pct == 0.0
    assert stats.pfr_count == 0
    assert stats.pfr_pct == 0.0
    # Alice nunca se enfrenta a una subida: no hay oportunidades de 3-bet.
    assert stats.three_bet_opportunities == 0
    assert stats.three_bet_pct is None


def test_limping_every_hand_gives_full_vpip_and_zero_pfr(conn):
    for i in range(20):
        hand = _hand(i, [("Alice", ActionType.CALL), ("Bob", ActionType.CHECK)])
        update_stats(conn, hand)

    stats = get_player_stats(conn, "Alice")
    assert stats.hands_played == 20
    assert stats.vpip_pct == 100.0
    assert stats.pfr_pct == 0.0


def test_raising_every_hand_gives_full_vpip_and_pfr(conn):
    for i in range(15):
        hand = _hand(i, [("Alice", ActionType.RAISE), ("Bob", ActionType.FOLD)])
        update_stats(conn, hand)

    stats = get_player_stats(conn, "Alice")
    assert stats.hands_played == 15
    assert stats.vpip_pct == 100.0
    assert stats.pfr_pct == 100.0
    # Alice es la que abre: nunca se enfrenta ella misma a una subida previa.
    assert stats.three_bet_opportunities == 0
    assert stats.three_bet_pct is None


def test_vpip_and_pfr_converge_to_expected_ratio_over_many_hands(conn):
    # Alice sube 1 de cada 4 manos (PFR 25%) y limpa las otras 3 (VPIP 100%).
    for i in range(400):
        action = ActionType.RAISE if i % 4 == 0 else ActionType.CALL
        hand = _hand(i, [("Alice", action), ("Bob", ActionType.FOLD)])
        update_stats(conn, hand)

    stats = get_player_stats(conn, "Alice")
    assert stats.hands_played == 400
    assert stats.vpip_pct == 100.0
    assert stats.pfr_pct == 25.0


def test_three_bet_percentage_converges_over_many_hands():
    conn = connect(":memory:")
    # Bob siempre abre subiendo. Alice, al enfrentarse a esa subida, resube
    # 1 de cada 5 veces (3-bet 20%) y paga el resto.
    for i in range(500):
        alice_action = ActionType.RAISE if i % 5 == 0 else ActionType.CALL
        hand = _hand(
            i,
            [("Bob", ActionType.RAISE), ("Alice", alice_action)],
        )
        update_stats(conn, hand)

    alice = get_player_stats(conn, "Alice")
    assert alice.hands_played == 500
    assert alice.three_bet_opportunities == 500
    assert alice.three_bet_count == 100
    assert alice.three_bet_pct == 20.0

    bob = get_player_stats(conn, "Bob")
    assert bob.pfr_pct == 100.0
    # Bob abre siempre sin que nadie haya subido antes que él.
    assert bob.three_bet_opportunities == 0


def test_only_counts_as_three_bet_opportunity_when_facing_exactly_one_raise(conn):
    # Carla se enfrenta a una única subida (Bob) y resube -> 3-bet.
    # Luego Dan se enfrenta a DOS subidas (Bob y Carla) y resube -> eso es
    # un 4-bet, no cuenta como oportunidad de 3-bet para Dan.
    hand = _hand(
        1,
        [
            ("Bob", ActionType.RAISE),
            ("Carla", ActionType.RAISE),
            ("Dan", ActionType.RAISE),
        ],
    )
    update_stats(conn, hand)

    carla = get_player_stats(conn, "Carla")
    assert carla.three_bet_opportunities == 1
    assert carla.three_bet_count == 1
    assert carla.three_bet_pct == 100.0

    dan = get_player_stats(conn, "Dan")
    assert dan.three_bet_opportunities == 0
    assert dan.three_bet_pct is None
    assert dan.pfr_count == 1  # sí cuenta para PFR, aunque no sea 3-bet


def test_sitting_out_players_are_not_counted(conn):
    hand = _hand(
        1,
        [("Alice", ActionType.CALL)],
        sitting_out={"Bob"},
    )
    hand.players.append(Player(seat=9, name="Bob", chips=Decimal("0"), is_sitting_out=True))
    update_stats(conn, hand)

    assert get_player_stats(conn, "Alice") is not None
    assert get_player_stats(conn, "Bob") is None


def test_cancelled_hands_are_ignored(conn):
    hand = _hand(1, [("Alice", ActionType.RAISE)], is_cancelled=True)
    update_stats(conn, hand)

    assert get_player_stats(conn, "Alice") is None


def test_processing_the_same_hand_twice_is_idempotent(conn):
    hand = _hand(1, [("Alice", ActionType.RAISE), ("Bob", ActionType.FOLD)])
    update_stats(conn, hand)
    update_stats(conn, hand)

    stats = get_player_stats(conn, "Alice")
    assert stats.hands_played == 1
    assert stats.pfr_count == 1


def test_update_stats_from_hands_processes_a_full_sequence(conn):
    hands = [
        _hand(1, [("Alice", ActionType.RAISE), ("Bob", ActionType.FOLD)]),
        _hand(2, [("Alice", ActionType.FOLD), ("Bob", ActionType.RAISE)]),
        _hand(3, [("Alice", ActionType.CALL), ("Bob", ActionType.CHECK)]),
    ]
    update_stats_from_hands(conn, hands)

    alice = get_player_stats(conn, "Alice")
    assert alice.hands_played == 3
    assert alice.vpip_count == 2
    assert alice.pfr_count == 1

    bob = get_player_stats(conn, "Bob")
    assert bob.hands_played == 3
    assert bob.pfr_count == 1


def test_stats_persist_across_connections_to_the_same_file(tmp_path):
    db_path = str(tmp_path / "stats.sqlite3")

    conn1 = connect(db_path)
    update_stats(conn1, _hand(1, [("Alice", ActionType.RAISE), ("Bob", ActionType.FOLD)]))
    conn1.close()

    conn2 = connect(db_path)
    update_stats(conn2, _hand(2, [("Alice", ActionType.CALL), ("Bob", ActionType.CHECK)]))

    alice = get_player_stats(conn2, "Alice")
    assert alice.hands_played == 2
    assert alice.vpip_count == 2
    assert alice.pfr_count == 1
    conn2.close()
