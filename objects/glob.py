from typing import Any, Callable, Pattern, TYPE_CHECKING
from lenhttp import Router, LenHTTP
from lib.database import Database
from config import conf
import re

if TYPE_CHECKING:
    from objects.collections import Tokens, Channels, Matches
    from objects.beatmap import Beatmap
    from objects.player import Player
    from packets.reader import Packet


server: LenHTTP = None

debug: bool = conf["server"]["debug"]
domain: str = conf["server"]["domain"]
port: int = conf["server"]["port"]

bancho: Router = None
avatar: Router = None
osu: Router = None

packets: dict[int, "Packet"] = {}
tasks: list[dict[str, Callable]] = []

bot: "Player" = None

prefix: str = "!"

config: dict[str, dict[str, Any]] = conf

sql: Database = None

bcrypt_cache: dict[str, bytes] = {}

title_card: str = '''
                . . .o .. o
                    o . o o.o
                        ...oo.
                   ________[]_
            _______|_o_o_o_o_o\___
            \\""""""""""""""""""""/
             \ ...  .    . ..  ./
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
osu!ragnarok, an osu!bancho & /web/ emulator.
Simon & Aoba
'''


players: "Tokens" = None

channels: "Channels" = None

matches: "Matches" = None

osu_key: str = config["api_conf"]["osu_api_key"]

beatmaps: dict[str, "Beatmap"] = {}

regex: dict[str, Pattern[str]] = {
    "np": re.compile(
        rf"\x01ACTION is (?:listening|editing|playing|watching) to \[https://osu.{domain}/beatmapsets/[0-9].*#/(\d*)"
    )
}
