import math
from constants.match import SlotStatus, ScoringType
from constants.mods import Mods
from constants.packets import BanchoPackets
from constants.player import Privileges
from constants.beatmap import Approved
from constants.playmode import Mode
from dataclasses import dataclass
from typing import TYPE_CHECKING
from objects.bot import Bot
from typing import Callable
from objects.group import Group
from objects.score import SubmitStatus
from packets import writer
from typing import Union
from objects import services
from utils import log
import asyncio
import random
import copy
import uuid
import time

from rina_pp_pyb import Beatmap as BMap, Calculator

if TYPE_CHECKING:
    from objects.channel import Channel
    from objects.player import Player


@dataclass
class Context:
    author: "Player"
    reciever: Union["Channel", "Player"]  # can't use | operator because str and str

    cmd: str
    args: list[str]

    # there is probably a better solution to this
    # but this is just what i quickly came up with
    async def await_response(self) -> str:
        services.await_response[self.author.token] = ""

        # they will have 60 seconds to respond.
        for i in range(0, 60):
            if services.await_response[self.author.token]:
                msg = services.await_response[self.author.token]
                services.await_response.pop(self.author.token)

                if msg[0] == "!":
                    pass

                return msg

            await asyncio.sleep(1)
        else:
            return ""


@dataclass
class Command:
    trigger: Callable
    cmd: str
    aliases: list[str]

    perms: Privileges
    doc: str
    category: str
    hidden: bool


commands: list["Command"] = []
mp_commands: list["Command"] = []


def rmp_command(
    trigger: str,
    required_perms: Privileges = Privileges.USER,
    hidden: bool = False,
    aliases: list[str] = [],
):
    def decorator(cb: Callable) -> None:
        cmd = Command(
            trigger=cb,
            cmd=trigger,
            aliases=aliases,
            perms=required_perms,
            doc=cb.__doc__,
            hidden=hidden,
            category="Multiplayer"
        )

        mp_commands.append(cmd)

    return decorator


def register_command(
    trigger: str,
    required_perms: Privileges = Privileges.USER,
    hidden: bool = False,
    aliases: list[str] = (),
    category: str = "undefined",
):
    def decorator(cb: Callable) -> None:
        cmd = Command(
            trigger=cb,
            cmd=trigger,
            aliases=aliases,
            perms=required_perms,
            doc=cb.__doc__,
            hidden=hidden,
            category=category,
        )

        commands.append(cmd)

    return decorator


#
# Normal user commands
#


@register_command("help", category="General")
async def help(ctx: Context) -> str:
    """The help message"""

    cmds: dict[str, dict[str, str]] = {}
    for cmd in commands:
        if cmd.hidden:
            continue

        if cmd.category not in cmds:
            cmds[cmd.category] = {}

        cmds[cmd.category][cmd.cmd] = cmd.doc
    
    command_list = ""
    for key, value in cmds.items():
        command_list += key + " commands:\n"

        for key, value in value.items():
            command_list += f"!{key} - {value}\n"

        command_list += "\n"

    return "These are the commands supported by our chat bot.\n" + command_list


@register_command("ping", category="General")
async def ping_command(ctx: Context) -> str:
    """Ping the server, to see if it responds."""

    return "PONG"


@register_command("roll", category="General")
async def roll(ctx: Context) -> str:
    """Roll a dice!"""

    x = 100

    if len(ctx.args) > 1:
        x = int(ctx.args[1])

    return f"{ctx.author.username} rolled {random.randint(0, x)} point(s)"


# rewrite this
@register_command("stats", category="Player")
async def user_stats(ctx: Context) -> str:
    """Display a users stats both vanilla or relax."""

    if len(ctx.args) < 1:
        return "Usage: !stats <username>"

    if not (t := await services.players.get_offline(ctx.args[0])):
        return "Player isn't online or couldn't be found in the database"

    relax = 0

    if len(ctx.args) == 2:
        if ctx.args[1] == "rx":
            relax = 1

    ret = await t.get_stats(relax)

    return (
        f"Stats for {t.username}:\n"
        f"PP: {ret['pp']} (#{ret['rank']})\n"
        f"Plays: {ret['playcount']} (lv{ret['level']})\n"
        f"Accuracy: {ret['level']}%"
    )

# this is not a permanent command, as soon as the
# server has a website, it'll be removed
@register_command("leaderboard", category="Player")
async def leaderboard(ctx: Context) -> str:
    if len(ctx.args) < 2:
        return "Wrong usage: !leaderboard <rx|vn> <mode>"
    
    _relax, _mode = ctx.args
    mode = Mode.from_str(_mode)
    relax = _relax == "rx"

    if mode == Mode.NONE:
        return f"Mode {_mode.lower()} doesn't exist."

    leaderboard = await services.redis.zrevrange(
        f"ragnarok:{'leaderboard' if not relax else 'leaderboard_rx'}:{mode.value}",
        start=0,
        end=10
    )

    response = f"Leaderboard for {mode} [{_relax}]\n"

    for rank, id in enumerate(leaderboard):
        if not (player := await services.players.get_offline(int(id))):
            # what da floppers
            log.fail(f"Failed to fetch player ({id}), when fetching leaderboard")
            continue

        response += f"#{rank + 1} - {player.username} ({player.pp}pp)\n" 

    return response


@register_command("verify", category="Player", hidden=True, required_perms=Privileges.PENDING)
async def verify_with_key(ctx: Context) -> str:
    """Verify your account with our key system!"""

    if type(ctx.reciever) is not type(services.bot):
        return "This command only works in BanchoBot's PMs."

    if not ctx.args:
        return "Usage: !verify <your beta key>"

    key = ctx.args[0]

    if not (
        key_info := await services.sql.fetch(
            "SELECT id, beta_key, made FROM beta_keys WHERE beta_key = %s", (key)
        )
    ):
        return "Invalid key"

    asyncio.create_task(
        services.sql.execute(
            "UPDATE users SET privileges = %s WHERE id = %s",
            (Privileges.USER.value + Privileges.VERIFIED.value, ctx.author.id),
        )
    )

    asyncio.create_task(
        services.sql.execute("DELETE FROM beta_keys WHERE id = %s", key_info["id"])
    )

    ctx.author.privileges = Privileges.USER + Privileges.VERIFIED
    ctx.author.enqueue(
        await writer.Notification(
            "Welcome to Ragnarok. You've successfully verified your account and gained beta access! If you see any bugs or anything unusal, please report it to one of the developers, through Github issues or Discord."
        )
        + await writer.Notification(
            "Please login again to prevent any corruption to your user data!"
        )
    )

    log.info(f"{ctx.author.username} successfully verified their account with a key")

    return "Successfully verified your account."


@register_command("pp", category="Tillerino-like")
async def calc_pp_for_map(ctx: Context) -> str:
    if not (_map := ctx.author.last_np):
        return "Please /np a map first."

    if not ctx.args:
        return "Usage: !pp <mods>"

    mods = Mods.from_str(ctx.args[0])
    bmap = BMap(path=f".data/beatmaps/{_map.file}")

    # if the original map mode is standard, but
    # the user is on another mode, it should convert pp
    mode = _map.mode

    if mode == Mode.OSU and mode != ctx.author.play_mode:
        mode = ctx.author.play_mode

    calc = Calculator(
        mods=mods,
        mode=mode,
    )

    pp_100p = calc.performance(bmap).pp

    calc.set_acc(99)
    pp_99p = calc.performance(bmap).pp

    calc.set_acc(98)
    pp_98p = calc.performance(bmap).pp

    calc.set_acc(97)
    pp_97p = calc.performance(bmap).pp

    calc.set_acc(96)
    pp_96p = calc.performance(bmap).pp

    calc.set_acc(95)
    pp_95p = calc.performance(bmap).pp

    return (
        f"{_map.full_title} >> "
        f"95%: {round(pp_95p, 2)}pp | "
        f"96%: {round(pp_96p, 2)}pp | "
        f"97%: {round(pp_97p, 2)}pp | "
        f"98%: {round(pp_98p, 2)}pp | "
        f"99%: {round(pp_99p, 2)}pp | "
        f"100%: {round(pp_100p, 2)}pp "
    )


#
# Multiplayer commands
#


@rmp_command("help")
async def multi_help(ctx: Context) -> str:
    """Multiplayer help command"""
    return "Not done yet."


@rmp_command("make")
async def make_multi(ctx: Context) -> str:
    ...


@rmp_command("makeprivate")
async def make_private_multi(ctx: Context) -> str:
    ...


@rmp_command("name")
async def change_multi_name(ctx: Context) -> str:
    ...


@rmp_command("lock")
async def lock_slot(ctx: Context) -> str:
    ...


@rmp_command("unlock")
async def unlock_slot(ctx: Context) -> str:
    ...


@rmp_command("start")
async def start_match(ctx: Context) -> str:
    """Start the multiplayer when all players are ready or force start it."""
    if (
        not ctx.reciever.is_multi
        or not (m := ctx.author.match)
        or ctx.author.match.host != ctx.author.id
    ):
        return

    m = ctx.author.match

    if ctx.args:
        if ctx.args[0] == "force":
            for slot in m.slots:
                if slot.status & SlotStatus.OCCUPIED:
                    if slot.status != SlotStatus.NOMAP:
                        slot.status = SlotStatus.PLAYING
                        slot.p.enqueue(await writer.MatchStart(m))

            m.in_progress = True

            await m.enqueue_state(lobby=True)
            return "Starting match... Good luck!"

    if not all(
        slot.status == SlotStatus.READY
        for slot in m.slots
        if slot.status & SlotStatus.OCCUPIED
    ):
        await ctx.reciever.send(
            message="All players aren't ready, would you like to force start? (y/n)",
            sender=services.bot,
        )
        response = await ctx.await_response()
        if response == "n":
            return

    for slot in m.slots:
        if slot.status & SlotStatus.OCCUPIED:
            slot.status = SlotStatus.PLAYING

    m.in_progress = True

    m.enqueue(await writer.MatchStart(m))
    await m.enqueue_state()
    return "Starting match... Good luck!"


@rmp_command("abort", aliases=("ab"))
async def abort_match(ctx: Context) -> str:
    if (
        not ctx.reciever.is_multi
        or not (m := ctx.author.match)
        or not m.in_progress
        or m.host != ctx.author.id
    ):
        return

    for s in m.slots:
        if s.status == SlotStatus.PLAYING:
            s.p.enqueue(await writer.write(BanchoPackets.CHO_MATCH_ABORT))
            s.status = SlotStatus.NOTREADY

            s.skipped = False
            s.loaded = False

    m.in_progress = False

    await m.enqueue_state(lobby=True)
    return "Aborted match."


@rmp_command("win", aliases=("wc"))
async def win_condition(ctx: Context) -> str:
    """Change win condition in a multiplayer match."""
    if (
        not ctx.reciever.is_multi
        or not (m := ctx.author.match)
        or ctx.author.match.host != ctx.author.id
    ):
        return

    if not ctx.args:
        return f"Wrong usage. !multi {ctx.cmd} <score/acc/combo/sv2/pp>"

    if ctx.args[0] in ("score", "acc", "sv2", "combo"):
        old_scoring = copy.copy(m.scoring_type)
        m.scoring_type = ScoringType.find_value(ctx.args[0])

        await m.enqueue_state()
        return f"Changed win condition from {old_scoring.name.lower()} to {m.scoring_type.name.lower()}"
    elif ctx.args[0] == "pp":
        m.scoring_type = ScoringType.SCORE  # force it to be score
        m.pp_win_condition = True

        await m.enqueue_state()
        return (
            "Changed win condition to pp. THIS IS IN BETA AND CAN BE REMOVED ANY TIME."
        )

    return "Not a valid win condition"


@rmp_command("move")
async def move_slot(ctx: Context) -> str:
    if (
        not ctx.reciever.is_multi
        or not (m := ctx.author.match)
        or ctx.author.match.host != ctx.author.id
    ):
        return

    if len(ctx.args) < 2:
        return "Wrong usage: !multi move <player> <to_slot>"

    ctx.args[1] = int(ctx.args[1]) - 1

    player = services.players.get(ctx.args[0])

    if not (target := m.find_user(player)):
        return "Slot is not occupied."

    if (to := m.find_slot(ctx.args[1])).status & SlotStatus.OCCUPIED:
        return "That slot is already occupied."

    to.copy_from(target)
    target.reset()

    await m.enqueue_state(lobby=True)

    return f"Moved {to.p.username} to slot {ctx.args[1] + 1}"


@rmp_command("size")
async def change_size(ctx: Context) -> str:
    if (
        not ctx.reciever.is_multi
        or not (m := ctx.author.match)
        or ctx.author.match.host != ctx.author.id
        or m.in_progress
    ):
        return

    if not ctx.args:
        return "Wrong usage: !multi size <amount of available slots>"

    for slot_id in range(0, int(ctx.args[0])):
        slot = m.find_slot(slot_id)

        if not slot.status & SlotStatus.OCCUPIED:
            slot.status = SlotStatus.LOCKED

    return f"Changed size to {ctx.args[0]}"


@rmp_command("get")
async def get_beatmap(ctx: Context) -> str:
    if not ctx.reciever.is_multi or not (m := ctx.author.match):
        return

    if not ctx.args:
        return "Wrong usage: !multi get <chimu|katsu>"

    if m.map_id == 0:
        return "The host has probably choosen a map that needs to be updated! Tell them to do so!"

    if ctx.args[0] not in (mirrors := services.config["api_conf"]["mirrors"]):
        return "Mirror doesn't exist in our database"

    url = mirrors[ctx.args[0]]

    if ctx.args[0] == "chimu":
        url += f"download/{m.map_id}"

    elif ctx.args[0] == "katsu":
        url += f"d/{m.map_id}"

    return f"[{url} Download beatmap from {ctx.args[0]}]"


@rmp_command("invite")
async def invite_people(ctx: Context) -> str:
    if not ctx.reciever.is_multi or not (m := ctx.author.match):
        return

    if not ctx.args:
        await ctx.reciever.send(
            message="Who do you want to invite?", sender=services.bot
        )
        response = await ctx.await_response()

        if not (target := services.players.get(response)):
            return "The user is not online."
    else:
        if not (target := services.players.get(ctx.args[0])):
            return "The user is not online."

    if target is ctx.author:
        return "You can't invite yourself."

    await ctx.author.send_message(
        f"Come join my multiplayer match: [osump://{m.match_id}/{m.match_pass.replace(' ', '_')} {m.match_name}]",
        reciever=target,
    )

    return f"Invited {target.username}"


#
# Staff commands
#


@register_command("announce", category="Staff", required_perms=Privileges.MODERATOR)
async def announce(ctx: Context) -> str:
    if len(ctx.args) < 2:
        return

    msg = " ".join(ctx.args[1:])

    if ctx.args[0] == "all":
        services.players.enqueue(await writer.Notification(msg))
    else:
        if not (target := services.players.get(ctx.args[0])):
            return "Player is not online."

        target.enqueue(await writer.Notification(msg))

    return "ok"


@register_command("kick", category="Staff", required_perms=Privileges.MODERATOR)
async def kick_user(ctx: Context) -> str:
    """Kick all players or just one player from the server."""

    if not ctx.args:
        return "Usage: !kick <username>"

    if ctx.args[0].lower() == "all":
        for p in services.players.players[:]:
            if (p == ctx.author) or p.bot:
                continue

            await p.logout()

        return "Kicked every. single. user online."

    if not (t := await services.players.get_offline(" ".join(ctx.args))):
        return "Player isn't online or couldn't be found in the database"

    if t.bot:
        return "You can't kick me from the server!"

    await t.logout()
    t.enqueue(await writer.Notification("You've been kicked!"))

    return f"Successfully kicked {t.username}"


@register_command("restrict", category="Staff", required_perms=Privileges.ADMIN)
async def restrict_user(ctx: Context) -> str:
    """Restrict users from the server"""

    if (not ctx.reciever == services.bot) and ctx.reciever.name != "#staff":
        return "You can't do that here."

    if len(ctx.args) < 1:
        return "Usage: !restrict <username>"

    if not (t := await services.players.get_offline(" ".join(ctx.args))):
        return "Player isn't online or couldn't be found in the database"

    if t.is_restricted:
        return "Player is already restricted? Did you mean to unrestrict them?"

    asyncio.create_task(
        services.sql.execute(
            "UPDATE users SET privileges = privileges - 4 WHERE id = %s", (t.id)
        )
    )

    t.privileges -= Privileges.VERIFIED

    t.enqueue(
        await writer.Notification("An admin has set your account in restricted mode!")
    )

    return f"Successfully restricted {t.username}"


@register_command("unrestrict", category="Staff", required_perms=Privileges.ADMIN)
async def unrestrict_user(ctx: Context) -> str:
    """Unrestrict users from the server."""

    if ctx.reciever != "#staff":
        return "You can't do that here."

    if len(ctx.args) < 1:
        return "Usage: !unrestrict <username>"

    if not (t := await services.players.get_offline(" ".join(ctx.args))):
        return "Player couldn't be found in the database"

    if not t.is_restricted:
        return "Player isn't even restricted?"

    await services.sql.execute(
        "UPDATE users SET privileges = privileges + 4 WHERE id = %s", (t.id)
    )

    t.privileges |= Privileges.VERIFIED

    if t.token:  # if user is online
        t.enqueue(await writer.Notification("An admin has unrestricted your account!"))

    return f"Successfully unrestricted {t.username}"


@register_command("bot", category="Staff", required_perms=Privileges.ADMIN)
async def bot_commands(ctx: Context) -> str:
    """Handle our bot ingame"""

    if not ctx.args:
        return f"{services.bot.username.lower()}."

    if ctx.args[0] == "reconnect":
        if services.players.get(1):
            return f"{services.bot.username} is already connected."

        await Bot.init()

        return f"Successfully connected {services.bot.username}."


@register_command("approve", category="Staff", required_perms=Privileges.BAT)
async def approve_map(ctx: Context) -> str:
    """Change the ranked status of beatmaps."""

    if not ctx.author.last_np:
        return "Please /np a map first."

    bmap = ctx.author.last_np

    if len(ctx.args) != 2:
        return "Usage: !approve <set/map> <rank/love/unrank>"

    if not ctx.args[0] in ("map", "set"):
        return "Invalid first argument (map or set)"

    if not ctx.args[1] in ("rank", "love", "unrank"):
        return "Invalid approved status (rank, love or unrank)"

    ranked_status = {
        "rank": Approved.RANKED,
        "love": Approved.LOVED,
        "unrank": Approved.PENDING,
    }[ctx.args[1]]

    if bmap.approved == ranked_status.value:
        return f"Map is already {ranked_status.name}"

    condition = {"map": "map_id", "set": "set_id"}[ctx.args[0]]

    await services.sql.execute(
        "UPDATE beatmaps SET approved = %s " f"WHERE {condition} = %s",
        (ranked_status.value, bmap.map_id if condition == "map_id" else bmap.set_id),
    )

    if condition == "set_id":
        title = f"{bmap.artist} - {bmap.title}"
    else:
        title = bmap.full_title

    resp = f"Successfully changed {title}'s status, from {Approved(bmap.approved).name} to {ranked_status.name}"

    bmap.approved = ranked_status

    if condition == "set_id":
        set = services.get_beatmap_hashes_by_set_id(bmap.set_id)

        for hash in set:
            services.beatmaps[hash].approved = ranked_status
    else:
        # do i even need this check?
        if ctx.author.last_np.hash_md5 in services.beatmaps:
            services.beatmaps[ctx.author.last_np.hash_md5].approved = ranked_status

    return resp


@register_command("recalc", category="Staff", required_perms=Privileges.ADMIN)
async def recalc_scores(ctx: Context) -> str:
    """Recalculate all the scores on either relax or vanilla."""
    if not ctx.args:
        return "Usage: !recalc <relax/vanilla>"

    if ctx.args[0].lower() not in ("relax", "vanilla"):
        return "Usage: !recalc <relax/vanilla>"

    async for score in services.sql.iterall(
        "SELECT s.id, s.mods, s.count_300, s.count_100, s.count_50, "
        "s.count_geki, s.count_katu, s.count_miss, s.max_combo, "
        "s.accuracy, b.map_id, s.mode, s.pp, b.title, "
        "b.version, b.artist FROM scores s "
        "INNER JOIN beatmaps b ON b.hash = s.hash_md5 "
        "WHERE s.relax = %s AND s.mode = 0 AND s.status >= %s",
        (True if ctx.args[0].lower() == "relax" else False, SubmitStatus.PASSED),
    ):
        bmap = BMap(path=f".data/beatmaps/{score['map_id']}.osu")

        calc = Calculator(
            mode=score["mode"],
            n300=score["count_300"],
            n100=score["count_100"],
            n50=score["count_50"],
            n_misses=score["count_miss"],
            n_geki=score["count_geki"],
            n_katu=score["count_katu"],
            combo=score["max_combo"],
            acc=score["accuracy"],
            mods=score["mods"],
        )

        pp = min(calc.performance(bmap).pp, 2000)

        if math.isnan(pp):
            await ctx.reciever.send(
                message=f"Failed to recalculate pp for score {score['id']} on map {score['artist']} - {score['title']} [{score['version']}]",
                sender=services.bot,
            )
            pp = 0

        await services.sql.execute(
            "UPDATE scores SET pp = %s WHERE id = %s", (pp, score["id"])
        )

        await ctx.reciever.send(
            message=f"Finished recalculating score {score['id']} (before: {round(score['pp'],4)}, after: {round(pp,4)})",
            sender=services.bot,
        )

    return f"Finished recalculating all scores for {ctx.args[0]}"


@register_command("key", category="Admin", required_perms=Privileges.ADMIN)
async def beta_keys(ctx: Context) -> str:
    """Create or delete keys."""

    if len(ctx.args) < 1:
        return "Usage: !key <create/delete> <name if create (OPTIONAL) / id if delete>"

    if ctx.args[0] == "create":
        if len(ctx.args) != 2:
            key = uuid.uuid4().hex

            asyncio.create_task(
                services.sql.execute(
                    "INSERT INTO beta_keys VALUES (NULL, %s, %s)",
                    (key, time.time() + 432000),
                )
            )

            return f"Created key with the name {key}"

        key = ctx.args[1]

        asyncio.create_task(
            services.sql.execute(
                "INSERT INTO beta_keys VALUES (NULL, %s, %s)",
                (key, time.time() + 432000),
            )
        )

        return f"Created key with the name {key}"

    elif ctx.args[0] == "delete":
        if len(ctx.args) != 2:
            return "Usage: !key delete <key id>"

        key_id = ctx.args[1]

        if not await services.sql.fetch(
            "SELECT 1 FROM beta_keys WHERE id = %s", (key_id)
        ):
            return "Key doesn't exist"

        asyncio.create_task(
            services.sql.execute("DELETE FROM beta_keys WHERE id = %s", (key_id))
        )

        return f"Deleted key {key_id}"

    return "Usage: !key <create/delete> <name if create (OPTIONAL) / id if delete>"


# group commands
@register_command("group", hidden=True, required_perms=Privileges.DEV)
async def creategroup(ctx: Context) -> str:
    if len(ctx.args) != 1:
        return "Usage: !group <name>"

    if services.channels.get(name := ctx.args[0]):
        return "Group name already created (fix)"

    await Group.create(ctx.author, name)

    return f"Created group `{name}`"


async def handle_commands(
    message: str, sender: "Player", reciever: Union["Channel", "Player"]
) -> None:
    if message[:6] == "!multi":
        message = message[7:]
        commands_set = mp_commands
    else:
        message = message[1:]
        commands_set = commands

    ctx = Context(
        author=sender,
        reciever=reciever,
        cmd=message.split(" ")[0].lower(),
        args=message.split(" ")[1:],
    )

    for command in commands_set:
        if ctx.cmd != command.cmd or not command.perms & ctx.author.privileges:
            if ctx.cmd not in command.aliases:
                continue

        return await command.trigger(ctx)
