from decimal import Decimal
from pathlib import Path

import pytest

from poker_hud.parser import (
    ActionType,
    ParseError,
    Street,
    parse_file,
    parse_hand,
    parse_hands,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _one_hand(filename: str):
    hands = parse_file(str(FIXTURES_DIR / filename))
    assert len(hands) == 1
    return hands[0]


def test_limp_hand_multiway_no_ante_early_level():
    hand = _one_hand("limp_early_level_no_ante.txt")

    assert hand.hand_id == "500100001"
    assert hand.tournament_id == "112233445"
    assert hand.buy_in == "$10+$1"
    assert hand.level == "II"
    assert hand.small_blind == Decimal("25")
    assert hand.big_blind == Decimal("50")
    assert hand.ante == Decimal("0")
    assert hand.max_seats == 9
    assert hand.button_seat == 5
    assert len(hand.players) == 9

    sam = hand.player_by_name("Sam")
    tere = hand.player_by_name("Tere")
    uxio = hand.player_by_name("Uxio")
    assert sam.is_button and sam.position == "BTN"
    assert tere.position == "SB"
    assert uxio.position == "BB"

    preflop_calls = [
        a for a in hand.actions_for(Street.PREFLOP) if a.action_type == ActionType.CALL
    ]
    assert {a.player for a in preflop_calls} == {"Vera", "Willy", "Olga", "Tere"}

    assert hand.board == ["9c", "4d", "2s", "Kd", "7h"]
    assert hand.pot == Decimal("450")
    assert hand.winners == [("Vera", Decimal("450"), "collected")]
    assert not hand.is_cancelled
    assert not hand.eliminations


def test_raise_and_cbet_with_ante():
    hand = _one_hand("raise_and_cbet_with_ante.txt")

    assert hand.level == "VII"
    assert hand.small_blind == Decimal("200")
    assert hand.big_blind == Decimal("400")
    assert hand.ante == Decimal("50")

    antes = hand.actions_for(Street.PREFLOP)
    ante_actions = [a for a in antes if a.action_type == ActionType.POST_ANTE]
    assert len(ante_actions) == 6
    assert all(a.amount == Decimal("50") for a in ante_actions)

    raises = [a for a in hand.actions_for(Street.PREFLOP) if a.action_type == ActionType.RAISE]
    assert len(raises) == 1
    assert raises[0].player == "Gus"
    assert raises[0].to_amount == Decimal("800")

    flop_bets = [a for a in hand.actions_for(Street.FLOP) if a.action_type == ActionType.BET]
    assert len(flop_bets) == 1
    assert flop_bets[0].player == "Gus"
    assert flop_bets[0].amount == Decimal("1200")

    uncalled = [a for a in hand.actions if a.action_type == ActionType.UNCALLED_RETURN]
    assert uncalled[0].player == "Gus"
    assert uncalled[0].amount == Decimal("2500")

    assert hand.pot == Decimal("4900")
    assert hand.winners == [("Gus", Decimal("4900"), "collected")]


def test_three_bet_and_allin_showdown():
    hand = _one_hand("three_bet_allin.txt")

    assert hand.max_seats == 9
    assert hand.ante == Decimal("25")

    preflop_raises = [
        a for a in hand.actions_for(Street.PREFLOP) if a.action_type == ActionType.RAISE
    ]
    assert [a.player for a in preflop_raises] == ["Jon", "Ova"]
    assert preflop_raises[0].to_amount == Decimal("600")
    assert preflop_raises[1].to_amount == Decimal("1500")  # 3-bet

    turn_actions = hand.actions_for(Street.TURN)
    allin_actions = [a for a in turn_actions if a.is_all_in]
    assert {a.player for a in allin_actions} == {"Ova", "Jon"}
    assert all(
        a.action_type in (ActionType.BET, ActionType.CALL) for a in allin_actions
    )

    shows = [a for a in hand.actions if a.action_type == ActionType.SHOW]
    shown_players = {a.player: a.cards for a in shows}
    assert shown_players["Jon"] == ["As", "Ac"]
    assert shown_players["Ova"] == ["Kc", "Kd"]

    assert hand.pot == Decimal("18625")
    assert ("Jon", Decimal("18625"), "collected") in hand.winners


def test_allin_below_stack_and_elimination():
    hand = _one_hand("allin_below_stack_elimination.txt")

    assert hand.ante == Decimal("75")

    rex_raise = next(
        a
        for a in hand.actions_for(Street.PREFLOP)
        if a.action_type == ActionType.RAISE and a.player == "Rex"
    )
    assert rex_raise.is_all_in
    assert rex_raise.to_amount == Decimal("1725")

    # Rex se queda corto (short stack de 1800) y se va all-in con su
    # stack completo, por debajo de lo que costaría subir "a mano llena".
    rex = hand.player_by_name("Rex")
    assert rex.chips == Decimal("1800")

    assert hand.eliminations == [("Rex", 6)]
    assert hand.pot == Decimal("4200")
    assert ("Uma", Decimal("4200"), "collected") in hand.winners


def test_walk_uncontested_no_flop():
    hand = _one_hand("walk.txt")

    assert hand.small_blind == Decimal("75")
    assert hand.big_blind == Decimal("150")
    assert hand.board == []  # nunca se llega al flop

    folds = [a for a in hand.actions_for(Street.PREFLOP) if a.action_type == ActionType.FOLD]
    assert {a.player for a in folds} == {"Ax", "Bx", "Cx", "Dx", "Ex"}

    uncalled = next(a for a in hand.actions if a.action_type == ActionType.UNCALLED_RETURN)
    assert uncalled.player == "Fx"
    assert uncalled.amount == Decimal("150")

    assert hand.pot == Decimal("75")
    assert hand.winners == [("Fx", Decimal("75"), "collected")]
    assert not hand.is_cancelled


def test_cancelled_hand_has_no_summary_and_is_flagged():
    hand = _one_hand("cancelled.txt")

    assert hand.is_cancelled
    assert hand.pot is None
    assert hand.winners == []
    assert hand.level == "I"
    assert hand.ante == Decimal("0")

    dealt = hand.player_by_name("Az")
    assert dealt.cards == ["As", "Kc"]


def test_level_up_across_two_consecutive_hands():
    hands = parse_hands((FIXTURES_DIR / "level_up_two_hands.txt").read_text())
    assert len(hands) == 2
    hand1, hand2 = hands

    assert hand1.hand_id == "500200001"
    assert hand1.level == "V"
    assert hand1.small_blind == Decimal("75")
    assert hand1.big_blind == Decimal("150")
    assert hand1.ante == Decimal("0")

    assert hand2.hand_id == "500200002"
    assert hand2.level == "VI"
    assert hand2.small_blind == Decimal("100")
    assert hand2.big_blind == Decimal("200")
    assert hand2.ante == Decimal("25")

    # Mismo torneo y mesa en ambas manos, la ciega del botón avanza.
    assert hand1.tournament_id == hand2.tournament_id == "223344556"
    assert hand1.button_seat == 1
    assert hand2.button_seat == 2


def test_final_table_allin_below_blind_and_side_pot():
    hand = _one_hand("final_table_allin_below_blind.txt")

    assert hand.max_seats == 4  # mesa final, menos de 9 asientos
    assert hand.level == "XV"
    assert hand.ante == Decimal("500")

    ra_post = next(
        a
        for a in hand.actions
        if a.action_type == ActionType.POST_SB and a.player == "Ra"
    )
    assert ra_post.is_all_in
    assert ra_post.amount == Decimal("900")  # por debajo de la small blind completa (2000)

    ra = hand.player_by_name("Ra")
    assert ra.chips == Decimal("1400")

    assert hand.pot == Decimal("10900")
    winners = dict((name, amount) for name, amount, _ in hand.winners)
    assert winners["Ra"] == Decimal("4700")
    assert winners["Sa"] == Decimal("6200")


def test_real_pokerstars_es_file_with_bom_and_et_bracket_timestamp():
    hands = parse_file(str(FIXTURES_DIR / "bom_et_bracket_real_hh.txt"))
    assert len(hands) == 2
    hand1, hand2 = hands

    assert hand1.hand_id == "261871593712"
    assert hand1.tournament_id == "4022790069"
    assert hand1.level == "VI"
    assert hand1.small_blind == Decimal("75")
    assert hand1.big_blind == Decimal("150")
    assert hand1.ante == Decimal("20")
    assert len(hand1.players) == 8

    preflop_raises = [
        a for a in hand1.actions_for(Street.PREFLOP) if a.action_type == ActionType.RAISE
    ]
    assert len(preflop_raises) == 1
    assert preflop_raises[0].player == "BURBUJA50"
    assert preflop_raises[0].to_amount == Decimal("450")

    assert hand1.board == ["7h", "Kh", "Jd", "7d"]
    assert hand1.pot == Decimal("4755")
    assert hand1.winners == [("Laurent06010", Decimal("4755"), "collected")]
    assert not hand1.is_cancelled

    # Segunda mano: eventos que no aparecen en los fixtures de ejemplo
    # ("has returned"/"has timed out"/"is sitting out" en medio de una
    # calle/"doesn't show hand") no deben interrumpir el parseo del resto.
    assert hand2.hand_id == "261871596476"
    assert hand2.small_blind == Decimal("75")
    assert hand2.big_blind == Decimal("150")

    preflop_calls = [
        a for a in hand2.actions_for(Street.PREFLOP) if a.action_type == ActionType.CALL
    ]
    assert {a.player for a in preflop_calls} == {"starsky744", "wakamayo3"}

    flop_bets = [a for a in hand2.actions_for(Street.FLOP) if a.action_type == ActionType.BET]
    assert len(flop_bets) == 1
    assert flop_bets[0].player == "wakamayo3"
    assert flop_bets[0].amount == Decimal("450")

    assert hand2.board == ["Th", "6s", "2s"]
    assert hand2.pot == Decimal("685")
    assert hand2.winners == [("wakamayo3", Decimal("685"), "collected")]
    assert not hand2.is_cancelled


def test_parse_hand_rejects_unknown_header():
    with pytest.raises(ParseError):
        parse_hand("no es una mano de PokerStars\notra linea")


def test_positions_are_consistent_with_button_seat():
    hand = _one_hand("three_bet_allin.txt")
    ova = hand.player_by_name("Ova")
    pia = hand.player_by_name("Pia")
    quim = hand.player_by_name("Quim")
    iva = hand.player_by_name("Iva")

    assert ova.is_button and ova.position == "BTN"
    assert pia.position == "SB"
    assert quim.position == "BB"
    assert iva.position == "UTG"


def test_progressive_ko_seat_lines_with_bounty_are_parsed():
    # Torneos Progressive KO añaden el bounty acumulado directamente en la
    # línea de asiento ("... in chips, €X.XX bounty)"); el regex de asientos
    # debe seguir matcheando igual que en un torneo sin KO (T28).
    hand = _one_hand("progressive_ko_bounty_seats.txt")

    assert len(hand.players) == 6
    names_by_seat = {p.seat: p.name for p in hand.players}
    assert names_by_seat == {
        1: "NZFIER",
        2: "FatalNem",
        3: "BerniBeau",
        4: "flopipok",
        5: "wakamayo3",
        6: "kelet12",
    }

    kelet12 = hand.player_by_name("kelet12")
    assert kelet12.chips == Decimal("47182")

    berni = hand.player_by_name("BerniBeau")
    assert berni.is_button and berni.position == "BTN"

    # Las líneas "is connected"/"is disconnected" (eventos de T9) no deben
    # romper el parseo del resto de la mano.
    assert hand.pot == Decimal("800")
    assert hand.winners == [("wakamayo3", Decimal("800"), "collected")]
    assert not hand.is_cancelled
