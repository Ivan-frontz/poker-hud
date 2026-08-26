import pytest

from poker_hud.overlay import WindowGeometry
from poker_hud.overlay.layout import (
    DEFAULT_BOX_HEIGHT,
    DEFAULT_BOX_WIDTH,
    DEFAULT_MAX_SEATS,
    SeatBox,
    build_seat_boxes,
    compute_seat_position,
    compute_seat_positions,
    format_stats_line,
    resolve_max_seats,
)
from poker_hud.stats import PlayerStats

TABLE = WindowGeometry(x=100, y=200, width=1000, height=800)


def test_seat_one_is_centered_horizontally_near_the_bottom_of_the_table():
    x, y = compute_seat_position(TABLE, seat=1, max_seats=9)

    box_center_x = x + DEFAULT_BOX_WIDTH / 2
    table_center_x = TABLE.x + TABLE.width / 2
    assert box_center_x == pytest.approx(table_center_x, abs=1)

    # "Abajo" del centro: por debajo de la mitad vertical de la ventana.
    table_center_y = TABLE.y + TABLE.height / 2
    assert y > table_center_y


def test_seats_are_spread_evenly_around_the_table():
    positions = compute_seat_positions(TABLE, max_seats=6)
    assert set(positions) == {1, 2, 3, 4, 5, 6}
    # Con 6 asientos y simetría de la mesa, no puede haber dos asientos en
    # exactamente el mismo punto.
    assert len(set(positions.values())) == 6


def test_seat_positions_are_all_within_table_bounds():
    for max_seats in (2, 6, 9):
        positions = compute_seat_positions(TABLE, max_seats=max_seats)
        for x, y in positions.values():
            assert TABLE.x <= x <= TABLE.x + TABLE.width - DEFAULT_BOX_WIDTH
            assert TABLE.y <= y <= TABLE.y + TABLE.height - DEFAULT_BOX_HEIGHT


def test_compute_seat_positions_matches_compute_seat_position_per_seat():
    positions = compute_seat_positions(TABLE, max_seats=9)
    for seat in range(1, 10):
        assert positions[seat] == compute_seat_position(TABLE, seat=seat, max_seats=9)


def test_seat_positions_are_absolute_screen_coordinates_with_negative_origin():
    # Setup multi-monitor con la mesa en un monitor a la izquierda del
    # principal (coordenadas x negativas), como en test_overlay.py de T4.
    table = WindowGeometry(x=-1920, y=0, width=1024, height=768)
    positions = compute_seat_positions(table, max_seats=9)
    for x, y in positions.values():
        assert table.x <= x <= table.x + table.width - DEFAULT_BOX_WIDTH
        assert table.y <= y <= table.y + table.height - DEFAULT_BOX_HEIGHT


def test_seat_position_moves_with_the_table_window():
    # Si la ventana de mesa se mueve/redimensiona (T4 lo detecta), las
    # cajas deben recalcularse en base a la nueva geometría, no quedarse
    # ancladas a la posición vieja.
    moved_table = WindowGeometry(x=TABLE.x + 300, y=TABLE.y + 50, width=TABLE.width, height=TABLE.height)

    original = compute_seat_position(TABLE, seat=1, max_seats=9)
    moved = compute_seat_position(moved_table, seat=1, max_seats=9)

    assert moved[0] == original[0] + 300
    assert moved[1] == original[1] + 50


def test_compute_seat_position_rejects_zero_max_seats():
    with pytest.raises(ValueError):
        compute_seat_position(TABLE, seat=1, max_seats=0)


def test_compute_seat_position_clamps_when_box_bigger_than_table():
    # Mesa más pequeña que la propia caja en ambos ejes: no hay hueco
    # donde encajarla sin salirse, así que se ancla a la esquina superior
    # izquierda de la ventana en vez de devolver coordenadas fuera de rango.
    tiny_table = WindowGeometry(x=10, y=20, width=100, height=30)
    x, y = compute_seat_position(tiny_table, seat=1, max_seats=9)
    assert x == 10
    assert y == 20


def test_format_stats_line_for_empty_seat():
    assert format_stats_line(None, None) == ""


def test_format_stats_line_for_player_without_hands_yet():
    line = format_stats_line("Villain88", None)
    assert line == "Villain88\n- manos"


def test_format_stats_line_for_player_with_stats():
    stats = PlayerStats(
        screen_name="Villain88",
        hands_played=40,
        vpip_count=10,
        pfr_count=8,
        three_bet_opportunities=4,
        three_bet_count=1,
    )
    line = format_stats_line("Villain88", stats)
    assert line == "Villain88\n40m V25% P20% 3B25%"


def test_format_stats_line_shows_dash_for_stats_without_opportunities():
    stats = PlayerStats(
        screen_name="Villain88",
        hands_played=5,
        vpip_count=1,
        pfr_count=0,
        three_bet_opportunities=0,
        three_bet_count=0,
    )
    line = format_stats_line("Villain88", stats)
    assert "3B-" in line


def test_build_seat_boxes_returns_one_box_per_seat_including_empty_ones():
    seat_players = {1: "Hero", 3: "Villain88"}
    stats_by_name = {
        "Villain88": PlayerStats(
            screen_name="Villain88",
            hands_played=20,
            vpip_count=5,
            pfr_count=4,
            three_bet_opportunities=2,
            three_bet_count=1,
        )
    }

    boxes = build_seat_boxes(TABLE, max_seats=6, seat_players=seat_players, get_stats=stats_by_name.get)

    assert len(boxes) == 6
    assert all(isinstance(b, SeatBox) for b in boxes)

    by_seat = {b.seat: b for b in boxes}
    assert by_seat[1].text == "Hero\n- manos"
    assert by_seat[3].text == "Villain88\n20m V25% P20% 3B50%"
    # Asientos sin jugador sentado: caja sin texto (no se debería dibujar).
    assert by_seat[2].text == ""
    assert by_seat[4].text == ""


def test_build_seat_boxes_never_calls_get_stats_for_empty_seats():
    calls = []

    def get_stats(name):
        calls.append(name)
        return None

    build_seat_boxes(TABLE, max_seats=9, seat_players={1: "Hero"}, get_stats=get_stats)

    assert calls == ["Hero"]


def test_resolve_max_seats_prefers_the_real_table_size():
    # Caso real de T11: mesa de 9-max, pero en el momento del refresco el
    # watcher sólo vio jugadores en asientos bajos (1-4). Antes de T11 se
    # usaba max(seat_players) == 4, perdiendo 5 asientos reales de la mesa.
    seat_players = {1: "Hero", 2: "Villain1", 3: "Villain2", 4: "Villain3"}
    assert resolve_max_seats(9, seat_players) == 9


def test_resolve_max_seats_falls_back_to_highest_occupied_seat_without_real_size():
    seat_players = {1: "Hero", 4: "Villain"}
    assert resolve_max_seats(0, seat_players) == 4
    assert resolve_max_seats(None, seat_players) == 4


def test_resolve_max_seats_falls_back_to_default_without_real_size_or_players():
    assert resolve_max_seats(0, {}) == DEFAULT_MAX_SEATS
    assert resolve_max_seats(None, {}) == DEFAULT_MAX_SEATS


def test_build_seat_boxes_builds_every_seat_of_a_9max_table_with_few_players_seated():
    # Reproduce el bug real de esta noche (2026-08-26): mesa de 9-max con
    # sólo 4 de los 9 asientos ocupados (y todos en números bajos). Con el
    # max_seats real (T1) propagado hasta aquí, deben construirse las 9
    # cajas igualmente, no sólo hasta el asiento ocupado más alto.
    seat_players = {1: "Hero", 2: "Villain1", 3: "Villain2", 4: "Villain3"}

    boxes = build_seat_boxes(
        TABLE,
        max_seats=resolve_max_seats(9, seat_players),
        seat_players=seat_players,
        get_stats=lambda name: None,
    )

    assert len(boxes) == 9
    assert {b.seat for b in boxes} == set(range(1, 10))


def test_build_seat_boxes_positions_match_compute_seat_positions():
    seat_players = {1: "Hero"}
    boxes = build_seat_boxes(TABLE, max_seats=9, seat_players=seat_players, get_stats=lambda name: None)
    positions = compute_seat_positions(TABLE, max_seats=9)

    for box in boxes:
        assert (box.x, box.y) == positions[box.seat]
        assert box.width == DEFAULT_BOX_WIDTH
        assert box.height == DEFAULT_BOX_HEIGHT
