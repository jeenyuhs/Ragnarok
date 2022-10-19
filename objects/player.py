from constants.player import PresenceFilter, bStatus, Privileges, country_codes
from constants.match import SlotStatus
from objects.channel import Channel
from constants.levels import levels
from constants.playmode import Mode
from typing import TYPE_CHECKING
from constants.mods import Mods
from objects.match import Match
from typing import Optional
from packets import writer
from objects import glob
from utils import log
from copy import copy
import asyncio
import aiohttp
import time
import uuid

if TYPE_CHECKING:
    from objects.beatmap import Beatmap
    from objects.score import Score


class Player:
    def __init__(
        self,
        username: str,
        id: int,
        privileges: int,
        passhash: str,
        lon: float = 0.0,
        lat: float = 0.0,
        country: str = "XX",
        country_code: int = 0,
        **kwargs,
    ) -> None:
        self.id: int = id
        self.username: str = username
        self.safe_name: str = self.safe_username(self.username)
        self.privileges: int = privileges
        self.passhash: str = passhash

        self.country_code: str = country
        self.country: str = country_code

        self.ip: str = kwargs.get("ip", "127.0.0.1")
        self.longitude: float = lon
        self.latitude: float = lat
        self.timezone: int = kwargs.get("time_offset", 0) + 24
        self.client_version: float = kwargs.get("version", 0.0)
        self.in_lobby: bool = False

        if kwargs.get("token"):
            self.token: str = kwargs.get("token")
        else:
            self.token: str = self.generate_token()

        self.presence_filter: PresenceFilter = PresenceFilter.NIL

        self.status: bStatus = bStatus.IDLE
        self.status_text: str = ""
        self.beatmap_md5: str = ""
        self.current_mods: Mods = Mods.NONE
        self.play_mode: Mode = Mode.OSU
        self.beatmap_id: int = -1

        self.friends: set[int] = set()
        self.channels: list[Channel] = []
        self.spectators: list[Player] = []
        self.spectating: Player = None
        self.match: Match = None

        self.ranked_score: int = 0
        self.accuracy: float = 0.0
        self.playcount: int = 0
        self.total_score: int = 0
        self.level: float = 0.0
        self.rank: int = 0
        self.pp: int = 0

        self.relax: int = 0  # 0 for vn / 1 for rx

        self.block_unknown_pms: bool = kwargs.get("block_nonfriend", False)

        self.queue: bytes = bytearray()

        self.login_time: float = time.time()
        self.last_update: float = 0.0

        self.bot: bool = False

        self.is_restricted: bool = not (self.privileges & Privileges.VERIFIED) and (
            not self.privileges & Privileges.PENDING
        )
        self.is_staff: bool = self.privileges & Privileges.BAT

        self.last_np: "Beatmap" = None
        self.last_score: "Score" = None

    @property
    def embed(self) -> str:
        return f"[https://osu.mitsuha.pw/users/{self.id} {self.username}]"

    @property
    def url(self) -> str:
        return f"https://osu.mitsuha.pw/users/{self.id}"

    @staticmethod
    def generate_token() -> str:
        return str(uuid.uuid4())

    def safe_username(self, name) -> str:
        return name.lower().replace(" ", "_")

    def enqueue(self, packet: bytes) -> None:
        self.queue += packet

    def dequeue(self) -> None:
        if self.queue:
            ret = bytes(self.queue)
            self.queue.clear()
            return ret

    async def shout(self, text: str):
        self.enqueue(await writer.Notification(text))

    async def logout(self) -> None:
        if self.channels:
            while self.channels:
                await self.leave_channel(self.channels[0], kicked=False)

        if self.match:
            await self.leave_match()

        if self.spectating:
            # leave spectating code and stuff idk
            ...

        glob.players.remove_user(self)

        for player in glob.players.players:
            if player != self:
                player.enqueue(await writer.Logout(self.id))

    async def add_spectator(self, p) -> None:
        # TODO: Create temp spec channel
        joined = await writer.FellasJoinSpec(p.id)

        for s in self.spectators:
            s.enqueue(joined)
            p.enqueue(await writer.FellasJoinSpec(s.id))

        self.enqueue(await writer.UsrJoinSpec(p.id))
        self.spectators.append(p)

        p.spectating = self

    async def remove_spectator(self, p) -> None:
        # TODO: Remove chan and part chan
        left = await writer.FellasLeftSpec(p.id)

        for s in self.spectators:
            s.enqueue(left)

        self.enqueue(await writer.UsrLeftSpec(p.id))
        self.spectators.remove(p)

        p.spectating = None

    async def join_match(self, m: Match, pwd: Optional[str] = "") -> None:
        if self.match or pwd != m.match_pass or not m in glob.matches.matches:
            self.enqueue(await writer.MatchFail())
            return  # user is already in a match

        if (free_slot := m.get_free_slot()) is None:
            self.enqueue(await writer.MatchFail())
            log.warn(f"{self.username} tried to join a full match ({m!r})")
            return

        self.match = m

        slot = m.slots[free_slot]

        slot.p = self
        slot.mods = 0
        slot.status = SlotStatus.NOTREADY

        if m.host == self.id:
            slot.host = True

        if not self.match.chat:
            mc = Channel(
                **{
                    "raw": f"#multi_{self.match.match_id}",
                    "name": "#multiplayer",
                    "description": self.match.match_name,
                }
            )
            self.match.chat = mc

        await self.join_channel(self.match.chat)

        self.match.connected.append(self)

        self.enqueue(await writer.MatchJoin(self.match))  # join success

        log.info(f"{self.username} joined {m}")
        await self.match.enqueue_state(lobby=True)

    async def leave_match(self) -> None:
        if not self.match or not (slot := self.match.find_user(self)):
            return

        await self.leave_channel(self.match.chat)

        m = copy(self.match)
        self.match = None

        slot.reset()
        m.connected.remove(self)

        log.info(f"{self.username} left {m}")

        # if that was the last person
        # to leave the multiplayer
        # delete the multi lobby
        if not m.connected:
            log.info(f"{m} is empty! Removing...")

            m.enqueue(await writer.MatchDispose(m.match_id), lobby=True)

            await glob.matches.remove_match(m)
            return

        if m.host == self.id:
            log.info("Host left, rotating host.")
            for slot in m.slots:
                if not slot.host and slot.status & SlotStatus.OCCUPIED:
                    await m.transfer_host(slot)

                    break

        await m.enqueue_state(immune={self.id}, lobby=True)

    async def join_channel(self, chan: Channel):
        if chan in self.channels or (
            chan.staff  # if the chan is already in the user lists chans
            and not self.is_staff
        ):  # if the user isnt staff and the chan is.
            return

        self.channels.append(chan)
        chan.connected.append(self)

        self.enqueue(await writer.ChanJoin(chan.name))

        await chan.update_info()

    async def leave_channel(self, chan: Channel, kicked: bool = True):
        if not chan in self.channels:
            return

        self.channels.remove(chan)
        chan.connected.remove(self)

        if kicked:
            self.enqueue(await writer.ChanKick(chan.name))

        await chan.update_info()

    async def send_message(self, message, reciever: "Player" = None):
        reciever.enqueue(
            await writer.SendMessage(
                sender=self.username,
                message=message,
                channel=reciever.username,
                id=self.id,
            )
        )

    async def get_friends(self) -> None:
        async for player in glob.sql.iterall(
            "SELECT user_id2 as id FROM friends WHERE user_id1 = %s", (self.id)
        ):
            self.friends.add(player["id"])

    async def handle_friend(self, user: int) -> None:
        if not (t := glob.players.get_user(user)):
            return  # user isn't online; ignore

        # remove friend
        if await glob.sql.fetch(
            "SELECT 1 FROM friends WHERE user_id1 = %s AND user_id2 = %s",
            (self.id, user),
        ):
            await glob.sql.execute(
                "DELETE FROM friends WHERE user_id1 = %s AND user_id2 = %s",
                (self.id, user),
            )
            self.friends.remove(user)

            log.info(f"{self.username} removed {t.username} as friends.")
            return

        # add friend
        await glob.sql.execute(
            "INSERT INTO friends (user_id1, user_id2) VALUES (%s, %s)", (self.id, user)
        )
        self.friends.add(user)

        log.info(f"{self.username} added {t.username} as friends.")

    async def restrict(self) -> None:
        if self.is_restricted:
            return  # just ignore if the user
            # is already restricted.

        self.privileges -= Privileges.VERIFIED

        asyncio.create_task(
            glob.db.execute("UPDATE users SET privileges -= 4 WHERE id = %s", (self.id))
        )

        # notify user
        await self.shout("Your account has been put in restricted mode!")

        log.info(f"{self.username} has been put in restricted mode!")

    async def update_stats(self, mode=None, relax=None) -> None:
        if (m := mode) is None:
            m = self.play_mode

        if (rx := relax) is None:
            rx = self.relax

        spec_tables = ("stats", "stats_rx")[rx]
        se = ("std", "taiko", "catch", "mania")[m]

        self.get_level()

        await glob.sql.execute(
            f"UPDATE {spec_tables} SET pp_{se} = %s, playcount_{se} = %s, "
            f"accuracy_{se} = %s, total_score_{se} = %s, "
            f"ranked_score_{se} = %s, level_{se} = %s WHERE id = %s",
            (
                self.pp,
                self.playcount,
                round(self.accuracy, 2),
                self.total_score,
                self.ranked_score,
                self.level,
                self.id,
            ),
        )

    def get_level(self):
        for idx, req_score in enumerate(levels):
            if req_score < self.total_score < levels[idx + 1]:
                self.level = idx + 1

    # used for background tasks
    async def check_loc(self):
        lon, lat, cc, c = await self.set_location(get=True)

        if lon != self.longitude:
            self.longitude = lon

        if lat != self.latitude:
            self.latitude = lat

        if c != self.country_code:
            self.country_code = c

        if cc != self.country:
            self.country = cc

        await self.save_location()

    async def set_location(self, get: bool = False):
        async with aiohttp.ClientSession() as sess:
            async with sess.get(
                f"http://ip-api.com/json/{self.ip}?fields=status,message,countryCode,region,lat,lon"
            ) as resp:
                if not (ret := await resp.json()):
                    return  # sus

                if ret["status"] == "fail":
                    log.fail(
                        f"Unable to get {self.username}'s location. Response: {ret['message']}"
                    )
                    return

                if not get:
                    self.latitude = ret["lat"]
                    self.longitude = ret["lon"]
                    self.country = country_codes[ret["countryCode"]]
                    self.country_code = ret["countryCode"]

                    return

                return (
                    ret["lat"],
                    ret["lon"],
                    country_codes[ret["countryCode"]],
                    ret["countryCode"],
                )

    async def save_location(self):
        await glob.sql.execute(
            "UPDATE users SET lon = %s, lat = %s, country = %s, cc = %s WHERE id = %s",
            (self.longitude, self.latitude, self.country_code, self.country, self.id),
        )

    async def get_stats(self, relax: int = 0, mode: int = 0) -> dict:
        table = ("stats", "stats_rx")[relax]
        se = ("std", "taiko", "catch", "mania")[mode]

        ret = await glob.sql.fetch(
            f"SELECT ranked_score_{se} AS ranked_score, "
            f"total_score_{se} AS total_score, accuracy_{se} AS accuracy, "
            f"playcount_{se} AS playcount, pp_{se} AS pp, "
            f"level_{se} AS level FROM {table} "
            "WHERE id = %s",
            (self.id),
        )

        if ret["pp"] >= 1:
            # if the users pp is
            # higher or equal to
            # one, add rank to the user
            rank = await glob.sql.fetch(
                f"SELECT COUNT(*) AS rank FROM {table} t "
                "INNER JOIN users u ON u.id = t.id "
                f"WHERE t.id != %s AND t.pp_{se} > %s "
                f"ORDER BY t.pp_{se} DESC, t.total_score_{se} DESC LIMIT 1",
                (self.id, self.pp),
            )

            ret["rank"] = rank["rank"] + 1
        else:
            # if not, make the user
            # not display any rank. (0)
            ret["rank"] = 0

        return ret

    async def update_stats_cache(self) -> bool:
        ret = await self.get_stats(self.relax, self.play_mode)

        self.ranked_score = ret["ranked_score"]
        self.accuracy = ret["accuracy"]
        self.playcount = ret["playcount"]
        self.total_score = ret["total_score"]
        self.level = ret["level"]
        self.rank = ret["rank"]
        self.pp = int(ret["pp"])

        return True
