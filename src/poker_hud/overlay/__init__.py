"""Detección de la ventana de mesa de PokerStars (xdotool/wmctrl bajo Wine).

PokerStars corre bajo Wine en Linux, así que no hay forma de preguntarle
directamente "¿dónde está la mesa X en pantalla?": hay que tirar de las
herramientas de gestión de ventanas de X11 (``wmctrl``/``xdotool``), listar
todas las ventanas abiertas y quedarnos con las que "parecen" una mesa de
PokerStars por su título.

Se elige ``wmctrl -lG`` como fuente de datos en vez de ``xdotool`` porque
en una sola invocación (una línea por ventana) ya trae ID, escritorio,
geometría completa (posición y tamaño) y título, sin tener que encadenar
varias llamadas (``search`` + ``getwindowgeometry`` + ``getwindowname``
por ventana) como haría falta con xdotool. El formato de esa línea es:

    <id> <desktop> <x> <y> <ancho> <alto> <host> <título>

El título de una mesa de torneo bajo Wine tiene esta forma (asunción de
diseño, a validar/ajustar contra una captura real cuando haya un cliente
de PokerStars corriendo):

    "<nombre de mesa> - Tournament <id de torneo>, Table <nº de mesa>"

por ejemplo ``"Trantor 25 - Tournament 3181234567, Table 1"``. El
"nombre de mesa" es el mismo texto que aparece en la línea ``Table
'Trantor 25' ...`` del hand history (T1, :attr:`poker_hud.parser.Hand.table_name`),
que es justo lo que hace falta para casar una ventana en pantalla con las
stats de esa mesa.

Todo el módulo, salvo :func:`list_windows`, trabaja sobre listas de
líneas de texto o de :class:`Window` ya construidas en vez de invocar el
binario directamente, para poder testear el reconocimiento de mesas con
salidas de ejemplo de ``wmctrl -lG`` sin necesitar un servidor X real ni
Wine instalado.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

__all__ = [
    "WindowGeometry",
    "Window",
    "PokerTable",
    "parse_wmctrl_output",
    "find_poker_tables",
    "list_windows",
]

_WMCTRL_LINE_RE = re.compile(
    r"^(?P<id>0x[0-9a-fA-F]+)\s+"
    r"(?P<desktop>-?\d+)\s+"
    r"(?P<x>-?\d+)\s+(?P<y>-?\d+)\s+(?P<width>\d+)\s+(?P<height>\d+)\s+"
    r"(?P<host>\S+)\s+"
    r"(?P<title>.*)$"
)

# Formato asumido para el título de una mesa de torneo (ver docstring del
# módulo). El nombre de mesa se captura de forma no ávida hasta el primer
# " - Tournament ..." para admitir nombres de mesa con espacios.
_TABLE_TITLE_RE = re.compile(
    r"^(?P<table_name>.+?)\s*-\s*Tournament\s+(?P<tournament_id>\d+)"
    r"(?:\s*,\s*Table\s+(?P<table_number>\d+))?\s*$",
    re.IGNORECASE,
)


@dataclass
class WindowGeometry:
    """Posición y tamaño de una ventana, en píxeles de pantalla."""

    x: int
    y: int
    width: int
    height: int


@dataclass
class Window:
    """Una ventana tal y como la reporta ``wmctrl -lG``, sin interpretar."""

    window_id: str
    desktop: int
    geometry: WindowGeometry
    host: str
    title: str


@dataclass
class PokerTable:
    """Una ventana ya identificada como mesa de torneo de PokerStars.

    ``table_name`` es el nombre a casar contra
    :attr:`poker_hud.parser.Hand.table_name`.
    """

    window: Window
    table_name: str
    tournament_id: str | None = None
    table_number: int | None = None

    @property
    def geometry(self) -> WindowGeometry:
        return self.window.geometry


def parse_wmctrl_output(lines: list[str]) -> list[Window]:
    """Parsea líneas con el formato de ``wmctrl -lG`` a una lista de :class:`Window`.

    Las líneas vacías o que no encajan con el formato esperado se ignoran
    en vez de levantar una excepción: una ventana rara (título con
    caracteres inesperados, cliente de wmctrl distinto) no debería tirar
    abajo la detección del resto de ventanas.
    """

    windows: list[Window] = []
    for line in lines:
        if not line.strip():
            continue
        match = _WMCTRL_LINE_RE.match(line)
        if not match:
            continue
        windows.append(
            Window(
                window_id=match.group("id"),
                desktop=int(match.group("desktop")),
                geometry=WindowGeometry(
                    x=int(match.group("x")),
                    y=int(match.group("y")),
                    width=int(match.group("width")),
                    height=int(match.group("height")),
                ),
                host=match.group("host"),
                title=match.group("title"),
            )
        )
    return windows


def find_poker_tables(windows: list[Window]) -> list[PokerTable]:
    """Identifica, de entre ``windows``, cuáles son mesas de torneo de PokerStars.

    Recibe la lista de ventanas ya lista (en vez de llamar a ``wmctrl``
    directamente) para poder testear el reconocimiento por título con
    listados simulados. Se descartan explícitamente los lobbies (ventana
    de lobby de torneo o del lobby general), que también incluyen la
    palabra "Tournament" en el título pero no son una mesa jugable.
    """

    tables: list[PokerTable] = []
    for window in windows:
        if "lobby" in window.title.lower():
            continue

        match = _TABLE_TITLE_RE.match(window.title)
        if not match:
            continue

        tables.append(
            PokerTable(
                window=window,
                table_name=match.group("table_name").strip(),
                tournament_id=match.group("tournament_id"),
                table_number=(
                    int(match.group("table_number"))
                    if match.group("table_number")
                    else None
                ),
            )
        )
    return tables


def list_windows() -> list[Window]:
    """Ejecuta de verdad ``wmctrl -lG`` y devuelve las ventanas abiertas.

    Es la única función del módulo que depende de tener ``wmctrl``
    instalado y un servidor X en marcha; el resto de la lógica (parseo de
    líneas, reconocimiento de mesas) recibe listas ya construidas para
    que se pueda testear sin ese entorno.
    """

    result = subprocess.run(
        ["wmctrl", "-lG"], capture_output=True, text=True, check=True
    )
    return parse_wmctrl_output(result.stdout.splitlines())
