from pathlib import Path

import pytest

from poker_hud.parser import ActionType, Street
from poker_hud.stats import connect, get_player_stats
from poker_hud.watcher import HandHistoryWatcher

FIXTURES_DIR = Path(__file__).parent / "fixtures"

ONE_HAND = (FIXTURES_DIR / "three_bet_allin.txt").read_text()
TWO_HANDS = (FIXTURES_DIR / "level_up_two_hands.txt").read_text()


def _split_hand_texts(raw: str) -> list[str]:
    """Separa un fichero de fixture en los textos de cada mano, sin recortar nada."""

    return [block for block in raw.split("\n\n") if block.strip()]


@pytest.fixture
def conn():
    return connect(":memory:")


@pytest.fixture
def hh_dir(tmp_path):
    return tmp_path


def test_empty_directory_has_nothing_to_process(hh_dir, conn):
    watcher = HandHistoryWatcher(hh_dir, conn)
    assert watcher.poll() == []


def test_hand_being_written_is_not_processed_until_stable(hh_dir, conn):
    path = hh_dir / "HH20240201 T112233445.txt"
    watcher = HandHistoryWatcher(hh_dir, conn)

    lines = ONE_HAND.splitlines(keepends=True)
    assert lines[47] == "*** SUMMARY ***\n"

    # Escritura incremental: cabecera + asientos, sin ninguna acción todavía.
    path.write_text("".join(lines[:11]))
    assert watcher.poll() == []
    assert get_player_stats(conn, "Jon") is None

    # Preflop completo, calles siguientes ni empezadas.
    path.write_text("".join(lines[:34]))
    assert watcher.poll() == []
    assert get_player_stats(conn, "Jon") is None

    # Todas las calles jugadas, pero el *** SUMMARY *** todavía no se ha escrito.
    path.write_text("".join(lines[:47]))
    assert watcher.poll() == []
    assert get_player_stats(conn, "Jon") is None

    # El *** SUMMARY *** completo ya está en disco: el contenido ya "parece"
    # una mano completa, pero como el fichero acaba de crecer en este mismo
    # sondeo, todavía no se considera estable y no se procesa.
    path.write_text(ONE_HAND)
    assert watcher.poll() == []
    assert get_player_stats(conn, "Jon") is None

    # Un sondeo más sin que el fichero haya vuelto a crecer: ahora sí se
    # considera cerrada y se procesa.
    hands = watcher.poll()
    assert [h.hand_id for h in hands] == ["500100003"]
    jon = get_player_stats(conn, "Jon")
    assert jon is not None
    assert jon.hands_played == 1
    assert jon.pfr_count == 1  # Jon abre subiendo preflop
    # La 3-bet de Ova llega tras dos subidas en total (la suya propia y la
    # de Ova): para el motor de stats (T2) eso ya no es una oportunidad de
    # 3-bet "clásica" (una única subida abierta), así que no cuenta.
    assert jon.three_bet_opportunities == 0

    # Sondeos posteriores sin cambios no reprocesan nada.
    assert watcher.poll() == []
    jon_again = get_player_stats(conn, "Jon")
    assert jon_again.hands_played == 1


def test_next_hand_header_confirms_previous_hand_without_waiting(hh_dir, conn):
    path = hh_dir / "HH20240202 T223344556.txt"
    watcher = HandHistoryWatcher(hh_dir, conn)

    hand1_text, hand2_text = _split_hand_texts(TWO_HANDS)
    hand2_lines = (hand2_text + "\n").splitlines(keepends=True)

    # Se escribe la primera mano completa y ya ha empezado la cabecera de
    # la segunda: la primera mano queda confirmada por ese límite, sin
    # necesidad de esperar a que el fichero deje de crecer.
    path.write_text(hand1_text + "\n\n" + "".join(hand2_lines[:2]))
    hands = watcher.poll()
    assert [h.hand_id for h in hands] == ["500200001"]
    assert get_player_stats(conn, "Ka") is not None

    # La segunda mano sigue incompleta: no se procesa todavía aunque el
    # fichero crezca.
    path.write_text(hand1_text + "\n\n" + "".join(hand2_lines[:20]))
    assert watcher.poll() == []

    # Se termina de escribir la segunda mano y el fichero se estabiliza.
    path.write_text(TWO_HANDS)
    assert watcher.poll() == []  # acaba de crecer, todavía no es estable
    hands = watcher.poll()
    assert [h.hand_id for h in hands] == ["500200002"]

    ka = get_player_stats(conn, "Ka")
    assert ka.hands_played == 2


def test_cancelled_hand_is_recognised_as_complete_without_summary(hh_dir, conn):
    path = hh_dir / "HH20240201 T112233445-cancelled.txt"
    watcher = HandHistoryWatcher(hh_dir, conn)

    cancelled_text = (FIXTURES_DIR / "cancelled.txt").read_text()
    path.write_text(cancelled_text)
    assert watcher.poll() == []  # primer sondeo: acaba de crecer

    hands = watcher.poll()
    assert [h.hand_id for h in hands] == ["500100006"]
    assert hands[0].is_cancelled


def test_new_tournament_file_is_picked_up_as_it_appears(hh_dir, conn):
    watcher = HandHistoryWatcher(hh_dir, conn)
    assert watcher.poll() == []

    first_path = hh_dir / "HH20240201 T112233445.txt"
    first_path.write_text(ONE_HAND)
    watcher.poll()
    hands = watcher.poll()
    assert [h.hand_id for h in hands] == ["500100003"]

    # Un torneo nuevo empieza un fichero nuevo, que aparece más tarde en
    # la misma carpeta.
    second_path = hh_dir / "HH20240202 T223344556.txt"
    hand1_text, _ = _split_hand_texts(TWO_HANDS)
    second_path.write_text(hand1_text + "\n\n" + "PokerStars Hand #500200002:")
    hands = watcher.poll()
    assert [h.hand_id for h in hands] == ["500200001"]

    ka = get_player_stats(conn, "Ka")
    assert ka is not None
    assert ka.hands_played == 1


def test_multiple_files_are_tracked_independently(hh_dir, conn):
    watcher = HandHistoryWatcher(hh_dir, conn)

    path_a = hh_dir / "HH_a.txt"
    path_b = hh_dir / "HH_b.txt"
    hand1_text, hand2_text = _split_hand_texts(TWO_HANDS)

    path_a.write_text(hand1_text)
    path_b.write_text(ONE_HAND)
    watcher.poll()  # primer sondeo de ambos: solo confirma que crecieron

    hands = watcher.poll()
    hand_ids = {h.hand_id for h in hands}
    assert hand_ids == {"500200001", "500100003"}


def test_processed_hand_updates_vpip_and_pfr(hh_dir, conn):
    path = hh_dir / "HH.txt"
    watcher = HandHistoryWatcher(hh_dir, conn)
    path.write_text(ONE_HAND)
    watcher.poll()
    watcher.poll()

    ova = get_player_stats(conn, "Ova")
    assert ova.vpip_pct == 100.0
    assert ova.pfr_pct == 100.0
    assert ova.three_bet_pct == 100.0  # Ova resube la subida de Jon


def test_malformed_but_summary_shaped_block_does_not_crash_watcher(hh_dir, conn):
    path = hh_dir / "HH_bad.txt"
    watcher = HandHistoryWatcher(hh_dir, conn)

    path.write_text("esto no es una mano de PokerStars\n*** SUMMARY ***\nTotal pot 100\n")
    watcher.poll()
    hands = watcher.poll()

    assert hands == []
    assert len(watcher.errors) == 1


def test_on_hand_callback_is_invoked_per_new_hand(hh_dir, conn):
    seen = []
    watcher = HandHistoryWatcher(hh_dir, conn, on_hand=seen.append)

    path = hh_dir / "HH.txt"
    path.write_text(ONE_HAND)
    watcher.poll()
    watcher.poll()

    assert len(seen) == 1
    assert seen[0].hand_id == "500100003"


def test_preflop_action_types_are_preserved_through_the_watcher(hh_dir, conn):
    seen = []
    watcher = HandHistoryWatcher(hh_dir, conn, on_hand=seen.append)

    path = hh_dir / "HH.txt"
    path.write_text(ONE_HAND)
    watcher.poll()
    watcher.poll()

    hand = seen[0]
    raises = [a for a in hand.actions_for(Street.PREFLOP) if a.action_type == ActionType.RAISE]
    assert [a.player for a in raises] == ["Jon", "Ova"]
