from decimal import Decimal

import pytest

from poker_hud.app import SharedTableState, build_arg_parser, main
from poker_hud.parser import Player


def _player(seat: int, name: str, *, sitting_out: bool = False) -> Player:
    return Player(seat=seat, name=name, chips=Decimal("1000"), is_sitting_out=sitting_out)


class _FakeHand:
    def __init__(self, players: list[Player], max_seats: int = 9) -> None:
        self.players = players
        self.max_seats = max_seats


def test_arg_parser_requires_hand_history_dir():
    parser = build_arg_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_arg_parser_defaults():
    parser = build_arg_parser()
    args = parser.parse_args(["--hand-history-dir", "/tmp/hh"])

    assert args.hand_history_dir == "/tmp/hh"
    assert args.poll_interval == 2.0
    assert args.db_path
    assert args.tournament_id is None


def test_arg_parser_accepts_tournament_id():
    # T18: con dos mesas de PokerStars abiertas a la vez, fija el HUD a una.
    parser = build_arg_parser()
    args = parser.parse_args(
        ["--hand-history-dir", "/tmp/hh", "--tournament-id", "4022790069"]
    )

    assert args.tournament_id == "4022790069"


def test_main_reports_missing_hand_history_dir(tmp_path, capsys):
    missing = tmp_path / "does-not-exist"

    exit_code = main(["--hand-history-dir", str(missing)])

    assert exit_code == 1
    assert "no existe" in capsys.readouterr().err


def test_shared_table_state_starts_empty():
    state = SharedTableState()
    assert state.get_current_players() == {}


def test_shared_table_state_tracks_seats_of_last_hand():
    state = SharedTableState()
    hand = _FakeHand([_player(1, "Jon"), _player(4, "Ova")])

    state.on_hand(hand)

    assert state.get_current_players() == {1: "Jon", 4: "Ova"}


def test_shared_table_state_excludes_sitting_out_players():
    state = SharedTableState()
    hand = _FakeHand([_player(1, "Jon"), _player(2, "Ausente", sitting_out=True)])

    state.on_hand(hand)

    assert state.get_current_players() == {1: "Jon"}


def test_shared_table_state_replaces_seats_on_next_hand():
    state = SharedTableState()
    state.on_hand(_FakeHand([_player(1, "Jon")]))

    state.on_hand(_FakeHand([_player(2, "Ova")]))

    assert state.get_current_players() == {2: "Ova"}


def test_shared_table_state_starts_without_a_known_max_seats():
    state = SharedTableState()
    assert state.get_max_seats() == 0


def test_shared_table_state_tracks_max_seats_of_last_hand():
    # T11: max_seats viaja aparte de seat_players porque no se puede
    # inferir de forma fiable a partir de los números de asiento ocupados
    # (una mesa de 9-max con jugadores sólo en asientos bajos parecería de
    # menos asientos si se calculara así).
    state = SharedTableState()
    hand = _FakeHand([_player(1, "Jon"), _player(2, "Ova"), _player(3, "Ren")], max_seats=9)

    state.on_hand(hand)

    assert state.get_current_players() == {1: "Jon", 2: "Ova", 3: "Ren"}
    assert state.get_max_seats() == 9
