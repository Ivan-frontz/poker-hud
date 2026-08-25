from poker_hud.overlay import (
    PokerTable,
    Window,
    WindowGeometry,
    find_poker_tables,
    parse_wmctrl_output,
)

# Salida de ejemplo de `wmctrl -lG`: id, escritorio, x, y, ancho, alto,
# host, título. Incluye una mesa de torneo, el lobby de ese mismo torneo
# (que también lleva "Tournament" en el título pero no es una mesa) y un
# par de ventanas ajenas a PokerStars.
SAMPLE_WMCTRL_OUTPUT = [
    "0x02a00007  0 100  200  1024 768  ivan-pc Trantor 25 - Tournament 3181234567, Table 1",
    "0x02a00010  0 300  50   800  600  ivan-pc Tournament 3181234567 Lobby",
    "0x01c00002  0 0    0    1920 1080 ivan-pc Mozilla Firefox",
    "0x01c00003  0 10   10   640  480  ivan-pc Terminal",
]


def test_parse_wmctrl_output_extracts_geometry_and_title():
    windows = parse_wmctrl_output(SAMPLE_WMCTRL_OUTPUT)

    assert len(windows) == 4
    table_window = windows[0]
    assert table_window.window_id == "0x02a00007"
    assert table_window.desktop == 0
    assert table_window.geometry == WindowGeometry(x=100, y=200, width=1024, height=768)
    assert table_window.host == "ivan-pc"
    assert table_window.title == "Trantor 25 - Tournament 3181234567, Table 1"


def test_parse_wmctrl_output_ignores_blank_and_malformed_lines():
    lines = ["", "   ", "esto no es una línea de wmctrl", *SAMPLE_WMCTRL_OUTPUT]
    windows = parse_wmctrl_output(lines)
    assert len(windows) == 4


def test_parse_wmctrl_output_supports_negative_coordinates():
    # Setup multi-monitor con un monitor a la izquierda del principal.
    lines = ["0x03000001  1 -1920 0 1024 768 ivan-pc Trantor 25 - Tournament 1, Table 1"]
    windows = parse_wmctrl_output(lines)
    assert windows[0].geometry.x == -1920


def test_find_poker_tables_picks_only_table_windows():
    windows = parse_wmctrl_output(SAMPLE_WMCTRL_OUTPUT)
    tables = find_poker_tables(windows)

    assert len(tables) == 1
    table = tables[0]
    assert isinstance(table, PokerTable)
    assert table.table_name == "Trantor 25"
    assert table.tournament_id == "3181234567"
    assert table.table_number == 1


def test_find_poker_tables_excludes_lobby_even_with_tournament_in_title():
    windows = parse_wmctrl_output(SAMPLE_WMCTRL_OUTPUT)
    tables = find_poker_tables(windows)
    assert all("Lobby" not in t.window.title for t in tables)


def test_find_poker_tables_exposes_geometry_for_matched_table():
    windows = parse_wmctrl_output(SAMPLE_WMCTRL_OUTPUT)
    tables = find_poker_tables(windows)
    assert tables[0].geometry == WindowGeometry(x=100, y=200, width=1024, height=768)


def test_find_poker_tables_returns_empty_list_when_no_pokerstars_windows():
    windows = parse_wmctrl_output(SAMPLE_WMCTRL_OUTPUT[2:])  # solo Firefox y Terminal
    assert find_poker_tables(windows) == []


def test_find_poker_tables_handles_table_name_without_table_number():
    # Algunas variantes de título podrían no incluir el nº de mesa.
    window = Window(
        window_id="0x0",
        desktop=0,
        geometry=WindowGeometry(0, 0, 100, 100),
        host="ivan-pc",
        title="Zaire IV - Tournament 42",
    )
    tables = find_poker_tables([window])
    assert len(tables) == 1
    assert tables[0].table_name == "Zaire IV"
    assert tables[0].tournament_id == "42"
    assert tables[0].table_number is None


def test_find_poker_tables_matches_table_name_used_in_hand_history():
    # El nombre extraído debe coincidir tal cual con el que produce el
    # parser (T1) a partir de la línea "Table 'Trantor 25' 9-max ...".
    windows = parse_wmctrl_output(SAMPLE_WMCTRL_OUTPUT)
    tables = find_poker_tables(windows)
    assert tables[0].table_name == "Trantor 25"
