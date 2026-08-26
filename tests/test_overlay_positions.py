import json

from poker_hud.overlay.positions import load_seat_positions, save_seat_position


def test_load_seat_positions_returns_empty_dict_when_file_is_missing(tmp_path):
    assert load_seat_positions(tmp_path / "does-not-exist.json") == {}


def test_load_seat_positions_returns_empty_dict_for_corrupt_json(tmp_path):
    path = tmp_path / "seat_positions.json"
    path.write_text("not valid json{{{")

    assert load_seat_positions(path) == {}


def test_load_seat_positions_returns_empty_dict_for_unexpected_shape(tmp_path):
    path = tmp_path / "seat_positions.json"
    path.write_text(json.dumps([1, 2, 3]))

    assert load_seat_positions(path) == {}


def test_save_then_load_round_trips_a_single_seat(tmp_path):
    path = tmp_path / "seat_positions.json"

    save_seat_position(path, seat=1, dx=50, dy=75)

    assert load_seat_positions(path) == {1: (50, 75)}


def test_save_seat_position_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "seat_positions.json"

    save_seat_position(path, seat=1, dx=1, dy=2)

    assert path.exists()
    assert load_seat_positions(path) == {1: (1, 2)}


def test_save_seat_position_preserves_other_seats(tmp_path):
    path = tmp_path / "seat_positions.json"
    save_seat_position(path, seat=1, dx=10, dy=20)

    save_seat_position(path, seat=3, dx=30, dy=40)

    assert load_seat_positions(path) == {1: (10, 20), 3: (30, 40)}


def test_save_seat_position_overwrites_the_same_seat(tmp_path):
    path = tmp_path / "seat_positions.json"
    save_seat_position(path, seat=1, dx=10, dy=20)

    save_seat_position(path, seat=1, dx=99, dy=100)

    assert load_seat_positions(path) == {1: (99, 100)}


def test_load_seat_positions_skips_entries_with_invalid_seat_or_offset(tmp_path):
    path = tmp_path / "seat_positions.json"
    path.write_text(
        json.dumps(
            {
                "1": [10, 20],
                "not-a-seat": [1, 2],
                "2": "not-a-pair",
            }
        )
    )

    assert load_seat_positions(path) == {1: (10, 20)}
