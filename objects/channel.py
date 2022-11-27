from typing import TYPE_CHECKING

from objects import services
from packets import writer
from utils import log

if TYPE_CHECKING:
    from objects.player import Player


class Channel:
    def __init__(self, **kwargs):
        self.name: str = kwargs.get("name", "unnamed")  # display name
        self._name: str = kwargs.get("raw", self.name)  # real name. fx #multi_1

        self.description: str = kwargs.get("description", "An osu! channel.")

        self.public: bool = kwargs.get("public", True)
        self.read_only: bool = kwargs.get("read_only", False)
        self.auto_join: bool = kwargs.get("auto_join", False)

        self.staff: bool = kwargs.get("staff", False)

        self.connected: list[Player] = []

    def __repr__(self) -> str:
        return (
            "Channel("
            f'display="{self.name}", '
            f'name="{self._name}", '
            f'description="{self.description}", '
            f"connected={self.connected[0:3]}..."
            ")"
        )

    @property
    def is_multi(self) -> bool:
        return self.name == "#multiplayer"

    @property
    def is_dm(self) -> bool:
        return self._name[0] != "#"

    def enqueue(self, data: bytes, ignore: list[int] = []) -> None:
        for p in self.connected:
            if p.id not in ignore:
                p.enqueue(data)

    async def update_info(self) -> None:
        services.players.enqueue(await writer.ChanInfo(self._name))

    async def force_join(self, p: "Player") -> None:
        if self in p.channels:
            return

        p.channels.append(self)
        self.connected.append(p)

        p.enqueue(await writer.ChanJoin(self._name))

        await self.update_info()

    async def kick(self, p: "Player") -> None:
        if not self in p.channels:
            return

        p.channels.remove(self)
        self.connected.remove(p)

        p.enqueue(await writer.ChanKick(self._name))

        await self.update_info()

    async def send(self, message: str, sender: "Player") -> None:
        if not sender.bot:
            if not (self in sender.channels or self.read_only):
                return

        ret = await writer.SendMessage(
            sender=sender.username, message=message, channel=self.name, id=sender.id
        )

        self.enqueue(ret, ignore=[sender.id])

        log.chat(f"<{sender.username}> {message} [{self._name}]")
