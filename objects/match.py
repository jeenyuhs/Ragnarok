from constants.match import SlotStatus, SlotTeams, TeamType, ScoringType
from objects.channel import Channel
from constants.playmode import Mode
from constants.mods import Mods
from packets import writer
from objects import services
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from objects.player import Player


class Players:
    def __init__(self):
        self.p: "Player" = None  # no superman :pensive:
        self.mods: Mods = Mods.NONE
        self.host: bool = False
        self.status: SlotStatus = SlotStatus.OPEN
        self.team: SlotTeams = SlotTeams.NEUTRAL
        self.loaded: bool = False
        self.skipped: bool = False

    def reset(self):
        self.p = None
        self.mods = Mods.NONE
        self.host = False
        self.status = SlotStatus.OPEN
        self.team = SlotTeams.NEUTRAL
        self.loaded = False
        self.skipped = False

    def copy_from(self, old):
        self.p = old.p
        self.mods = old.mods
        self.host = old.host
        self.status = old.status
        self.team = old.team
        self.loaded = old.loaded
        self.skipped = old.skipped


class Match:
    def __init__(self):
        self.match_id: int = 0
        self.match_name: str = ""
        self.match_pass: str = ""

        self.host: int = 0
        self.in_progress: bool = False

        self.map_id: int = 0
        self.map_title: str = ""
        self.map_md5: str = ""

        self.slots: list[Players] = [Players() for _ in range(0, 16)]

        self.mode: Mode = Mode.OSU
        self.mods: Mods = Mods.NONE
        self.freemods: bool = False

        self.scoring_type: ScoringType = ScoringType.SCORE
        self.pp_win_condition: bool = False
        self.team_type: TeamType = TeamType.HEAD2HEAD

        self.seed: int = 0

        self.connected: list = []

        self.locked: bool = False

        self.chat: Channel = None

    def __repr__(self) -> str:
        return f"MATCH-{self.match_id}"

    def get_free_slot(self) -> int:
        for id, slot in enumerate(self.slots):
            if slot.status == SlotStatus.OPEN:
                return id

        return -1

    def find_host(self) -> Players | None:
        for slot in self.slots:
            if slot.p.id == self.host:
                return slot

    def find_user(self, p: "Player") -> Players | None:
        for slot in self.slots:
            if slot.p == p:
                return slot

    def find_user_slot(self, p: "Player") -> int | None:
        for id, slot in enumerate(self.slots):
            if slot.p == p:
                return id

    def find_slot(self, slot_id: int) -> Players | None:
        if slot_id > 16:
            return

        for id, slot in enumerate(self.slots):
            if id == slot_id:
                return slot

    async def transfer_host(self, slot) -> None:
        self.host = slot.p.id

        slot.p.enqueue(await writer.MatchTransferHost())

        self.enqueue(await writer.Notification(f"{slot.p.username} became host!"))

        await self.enqueue_state()

    async def enqueue_state(
        self, immune: set[int] = set(), lobby: bool = False
    ) -> None:
        for p in self.connected:
            if p.id not in immune:
                p.enqueue(await writer.MatchUpdate(self))

        if lobby:
            chan = services.channels.get("#lobby")
            chan.enqueue(await writer.MatchUpdate(self))

    def enqueue(self, data, lobby: bool = False) -> None:
        for p in self.connected:
            p.enqueue(data)

        if lobby:
            chan = services.channels.get("#lobby")
            chan.enqueue(data)
