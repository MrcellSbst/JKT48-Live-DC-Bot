"""
Discord bot helper that monitors the local JKT48 IDN Live and SHOWROOM feeds
and relays updates into a Discord channel.

Usage:
  1. Configure DISCORD_TOKEN and DISCORD_CHANNEL_ID in .env (already used by NotifX).
  2. Install requirements: pip install discord.py aiohttp python-dotenv
  3. Run the bot: python jkt48_live_bot.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))


IDN_GRAPHQL_URL = "https://api.idn.app/graphql"
SHOWROOM_ONLIVE_URL = "https://www.showroom-live.com/api/live/onlives"
DATA_DIR = Path(__file__).with_name("data")
DEFAULT_DB_PATH = DATA_DIR / "live_status.db"
LIVE_STATUS_DB = os.path.expanduser(os.getenv("LIVE_STATUS_DB", str(DEFAULT_DB_PATH)))

# Same whitelist used in the Node API repo.
JKT48_IDN_USERNAMES: Tuple[str, ...] = (
    "jkt48_freya",
    "jkt48_amanda",
    "jkt48_gita",
    "jkt48_lulu",
    "jkt48_jessi",
    "jkt48_raisha",
    "jkt48_muthe",
    "jkt48_christy",
    "jkt48_lia",
    "jkt48_cathy",
    "jkt48_cynthia",
    "jkt48_daisy",
    "jkt48_indira",
    "jkt48_eli",
    "jkt48_michie",
    "jkt48_gracia",
    "jkt48_ella",
    "jkt48_feni",
    "jkt48_marsha",
    "jkt48_lyn",
    "jkt48_indah",
    "jkt48_elin",
    "jkt48_chelsea",
    "jkt48_danella",
    "jkt48_gendis",
    "jkt48_gracie",
    "jkt48_greesel",
    "jkt48_olla",
    "jkt48_kathrina",
    "jkt48_oniel",
    "jkt48_fiony",
    "jkt48_alya",
    "jkt48_anindya",
    "jkt48-official",
    "jkt48_nala",
    "jkt48_aralie",
    "jkt48_delynn",
    "jkt48_lana",
    "jkt48_erine",
    "jkt48_fritzy",
    "jkt48_lily",
    "jkt48_trisha",
    "jkt48_moreen",
    "jkt48_levi",
    "jkt48_nayla",
    "jkt48_nachia",
    "jkt48_oline",
    "jkt48_regie",
    "jkt48_ribka",
    "jkt48_kimmy",
    "jkt48_virgi",
    "jkt48_auwia",
    "jkt48_rilly",
    "jkt48_giaa",
    "jkt48_maira",
    "jkt48_ekin",
    "jkt48_jemima",
    "jkt48_mikaela",
    "jkt48_intan",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("jkt48-live-bot")


@dataclass(frozen=True)
class IDNLive:
    slug: str
    title: str
    username: str
    display_name: str
    avatar: Optional[str]
    stream_url: Optional[str]
    view_count: Optional[int]
    started_at: Optional[datetime]


@dataclass(frozen=True)
class ShowroomLive:
    live_id: Optional[int]
    room_id: Optional[int]
    room_name: str
    room_url_key: str
    image: Optional[str]
    started_at: Optional[datetime]
    view_count: Optional[int]


@dataclass(frozen=True)
class StreamSnapshot:
    idn: Sequence[IDNLive]
    showroom: Sequence[ShowroomLive]





@dataclass(frozen=True)
class StoredLive:
    signature: str
    source: str
    display_name: str
    title: Optional[str]
    url: Optional[str]
    started_at: Optional[str]
    ended_at: Optional[str]


def idn_profile_url(live: IDNLive) -> Optional[str]:
    if not live.username:
        return None
    if live.slug:
        return f"https://www.idn.app/{live.username}/live/{live.slug}"
    return f"https://www.idn.app/{live.username}"


def showroom_profile_url(live: ShowroomLive) -> Optional[str]:
    return (
        f"https://www.showroom-live.com/r/{live.room_url_key}"
        if live.room_url_key
        else None
    )


def idn_signature(live: IDNLive) -> str:
    base = live.slug or live.username or live.display_name or "idn-live"
    if live.started_at:
        return f"idn:{base}:{int(live.started_at.timestamp())}"
    return f"idn:{base}"


def showroom_signature(live: ShowroomLive) -> str:
    if live.live_id:
        return f"showroom:{live.live_id}"
    base = live.room_url_key or live.room_name or "showroom"
    if live.started_at:
        return f"showroom:{base}:{int(live.started_at.timestamp())}"
    return f"showroom:{base}"


def require_configuration() -> None:
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN env var is required.")
    if CHANNEL_ID == 0:
        raise SystemExit("DISCORD_CHANNEL_ID env var is required.")




def to_datetime(value: Optional[object]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):  # ms precision timestamps
        if value > 1_000_000_000_000:  # heuristically treat as ms
            value = value / 1000.0
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def format_discord_time(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return f"{discord.utils.format_dt(dt)} ({discord.utils.format_dt(dt, style='R')})"


class StreamMonitor:
    def __init__(self) -> None:
        self._last_idn_signatures: set[str] = set()
        self._last_showroom_signatures: set[str] = set()
        self._primed = False

    async def fetch_snapshot(self, session: aiohttp.ClientSession) -> StreamSnapshot:
        idn_task = asyncio.create_task(self._fetch_idn_lives(session))
        showroom_task = asyncio.create_task(self._fetch_showroom_lives(session))
        idn, showroom = await asyncio.gather(idn_task, showroom_task)
        # Stable ordering by start time (latest first) helps summary readability.
        sorted_idn = sorted(
            idn,
            key=lambda item: item.started_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        sorted_showroom = sorted(
            showroom,
            key=lambda item: item.started_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return StreamSnapshot(idn=sorted_idn, showroom=sorted_showroom)

    async def prime(self, session: aiohttp.ClientSession) -> StreamSnapshot:
        snapshot = await self.fetch_snapshot(session)
        self._last_idn_signatures = {idn_signature(live) for live in snapshot.idn}
        self._last_showroom_signatures = {
            showroom_signature(live) for live in snapshot.showroom
        }
        self._primed = True
        log.info(
            "Stream monitor primed with %d IDN lives and %d SHOWROOM lives",
            len(snapshot.idn),
            len(snapshot.showroom),
        )
        return snapshot

    def consume_updates(self, snapshot: StreamSnapshot) -> List[discord.Embed]:
        if not self._primed:
            self._last_idn_signatures = {idn_signature(live) for live in snapshot.idn}
            self._last_showroom_signatures = {
                showroom_signature(live) for live in snapshot.showroom
            }
            self._primed = True
            return []

        embeds: List[discord.Embed] = []
        for live in snapshot.idn:
            signature = idn_signature(live)
            if signature not in self._last_idn_signatures:
                log.info(
                    "Detected new IDN live for %s (signature %s)", live.username, signature
                )
                embeds.append(build_idn_live_embed(live))

        for live in snapshot.showroom:
            signature = showroom_signature(live)
            if signature not in self._last_showroom_signatures:
                log.info(
                    "Detected new SHOWROOM live for %s (signature %s)",
                    live.room_name,
                    signature,
                )
                embeds.append(build_showroom_live_embed(live))

        self._last_idn_signatures = {idn_signature(live) for live in snapshot.idn}
        self._last_showroom_signatures = {
            showroom_signature(live) for live in snapshot.showroom
        }
        return embeds

    async def _fetch_idn_lives(self, session: aiohttp.ClientSession) -> List[IDNLive]:
        payload = {
            "query": (
                'query SearchLivestream { searchLivestream(query: "", limit: 100) '
                "{ result { slug title image_url view_count playback_url room_identifier "
                "status live_at end_at scheduled_at gift_icon_url category { name slug } "
                "creator { uuid username name avatar } } } }"
            )
        }
        async with session.post(IDN_GRAPHQL_URL, json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()

        result = (
            data.get("data", {})
            .get("searchLivestream", {})
            .get("result", [])
        )

        lives: List[IDNLive] = []
        for entry in result:
            creator = entry.get("creator", {}) or {}
            username = creator.get("username")
            if username not in JKT48_IDN_USERNAMES:
                continue

            started_at = to_datetime(entry.get("live_at"))
            lives.append(
                IDNLive(
                    slug=entry.get("slug", ""),
                    title=entry.get("title") or "Sedang live di IDN Live",
                    username=username or "unknown",
                    display_name=creator.get("name") or username or "JKT48 Member",
                    avatar=creator.get("avatar"),
                    stream_url=entry.get("playback_url"),
                    view_count=entry.get("view_count"),
                    started_at=started_at,
                )
            )
        return lives

    async def _fetch_showroom_lives(
        self, session: aiohttp.ClientSession
    ) -> List[ShowroomLive]:
        async with session.get(SHOWROOM_ONLIVE_URL) as resp:
            resp.raise_for_status()
            data = await resp.json()

        onlives = data.get("onlives", [])

        # Collect every live across all genre blocks (API uses Japanese genre names
        # such as "アイドル" and "ミュージック", which can change – scanning all genres
        # is simpler and future-proof).
        all_lives: List[Dict[str, object]] = []
        seen_live_ids: set[int] = set()
        for block in onlives:
            for live in block.get("lives", []):
                lid = live.get("live_id")
                if lid is not None and lid in seen_live_ids:
                    continue
                if lid is not None:
                    seen_live_ids.add(lid)
                all_lives.append(live)

        filtered = [
            live
            for live in all_lives
            if isinstance(live.get("room_url_key"), str)
            and "jkt48" in live.get("room_url_key", "").lower()
        ]

        lives: List[ShowroomLive] = []
        for entry in filtered:
            started_at = to_datetime(entry.get("started_at"))
            # API field is "main_name", NOT "room_name".
            display_name = (
                entry.get("main_name")
                or entry.get("room_name")
                or "JKT48 SHOWROOM"
            )
            lives.append(
                ShowroomLive(
                    live_id=entry.get("live_id"),
                    room_id=entry.get("room_id"),
                    room_name=display_name,
                    room_url_key=entry.get("room_url_key") or "",
                    image=entry.get("image_square") or entry.get("image"),
                    started_at=started_at,
                    view_count=entry.get("view_num"),
                )
            )
        return lives


def current_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def human_duration(start: Optional[datetime], end: Optional[datetime]) -> Optional[str]:
    if not start or not end:
        return None
    delta = end - start
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return None
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


class LiveStatusStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_status (
                    signature TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    identifier TEXT,
                    display_name TEXT,
                    title TEXT,
                    url TEXT,
                    started_at TEXT,
                    last_seen_at TEXT NOT NULL,
                    ended_at TEXT,
                    is_live INTEGER NOT NULL
                );
                """
            )

    def seed(self, snapshot: StreamSnapshot) -> None:
        entries = self._snapshot_entries(snapshot)
        self._upsert(entries)

    def sync(self, snapshot: StreamSnapshot) -> List[StoredLive]:
        entries = self._snapshot_entries(snapshot)
        current_signatures = {entry["signature"] for entry in entries}
        now_iso = current_utc_iso()

        with self._connect() as conn:
            previous_live = {
                row["signature"]: row
                for row in conn.execute(
                    "SELECT * FROM live_status WHERE is_live = 1"
                ).fetchall()
            }
            ended_signatures = set(previous_live.keys()) - current_signatures
            ended_rows: List[sqlite3.Row] = []
            if ended_signatures:
                conn.executemany(
                    "UPDATE live_status SET is_live = 0, ended_at = ?, last_seen_at = ? WHERE signature = ?",
                    [(now_iso, now_iso, sig) for sig in ended_signatures],
                )
                placeholders = ",".join("?" for _ in ended_signatures)
                ended_rows = conn.execute(
                    f"SELECT * FROM live_status WHERE signature IN ({placeholders})",
                    list(ended_signatures),
                ).fetchall()

            self._upsert(entries, conn=conn)

        return [self._row_to_stored(row) for row in ended_rows]

    def _snapshot_entries(self, snapshot: StreamSnapshot) -> List[Dict[str, Optional[str]]]:
        now_iso = current_utc_iso()
        entries: List[Dict[str, Optional[str]]] = []

        for live in snapshot.idn:
            entries.append(
                {
                    "signature": idn_signature(live),
                    "source": "IDN",
                    "identifier": live.username or live.slug or live.display_name,
                    "display_name": live.display_name,
                    "title": live.title,
                    "url": idn_profile_url(live),
                    "started_at": to_iso(live.started_at),
                    "last_seen_at": now_iso,
                    "ended_at": None,
                    "is_live": 1,
                }
            )

        for live in snapshot.showroom:
            entries.append(
                {
                    "signature": showroom_signature(live),
                    "source": "SHOWROOM",
                    "identifier": live.room_url_key or live.room_name,
                    "display_name": live.room_name,
                    "title": "Live SHOWROOM",
                    "url": showroom_profile_url(live),
                    "started_at": to_iso(live.started_at),
                    "last_seen_at": now_iso,
                    "ended_at": None,
                    "is_live": 1,
                }
            )

        return entries

    def _upsert(
        self, entries: List[Dict[str, Optional[str]]], conn: Optional[sqlite3.Connection] = None
    ) -> None:
        if not entries:
            return
        owns_conn = False
        if conn is None:
            conn = self._connect()
            owns_conn = True

        conn.executemany(
            """
            INSERT INTO live_status (signature, source, identifier, display_name, title, url, started_at, last_seen_at, ended_at, is_live)
            VALUES (:signature, :source, :identifier, :display_name, :title, :url, :started_at, :last_seen_at, :ended_at, :is_live)
            ON CONFLICT(signature) DO UPDATE SET
                source = excluded.source,
                identifier = excluded.identifier,
                display_name = excluded.display_name,
                title = excluded.title,
                url = excluded.url,
                last_seen_at = excluded.last_seen_at,
                ended_at = NULL,
                is_live = 1,
                started_at = COALESCE(live_status.started_at, excluded.started_at)
            ;
            """,
            entries,
        )

        if owns_conn:
            conn.commit()
            conn.close()

    def _row_to_stored(self, row: sqlite3.Row) -> StoredLive:
        return StoredLive(
            signature=row["signature"],
            source=row["source"],
            display_name=row["display_name"] or row["identifier"] or "Unknown",
            title=row["title"],
            url=row["url"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        )


def build_idn_live_embed(live: IDNLive) -> discord.Embed:
    profile_url = idn_profile_url(live)
    embed = discord.Embed(
        title=f"IDN Live • {live.display_name}",
        description=live.title,
        color=discord.Color.red(),
        url=profile_url or discord.Embed.Empty,
    )
    embed.add_field(name="Username", value=live.username, inline=True)
    if live.view_count is not None:
        embed.add_field(name="Viewers", value=str(live.view_count), inline=True)
    started = format_discord_time(live.started_at)
    if started:
        embed.add_field(name="Started", value=started, inline=False)
    if profile_url:
        embed.add_field(name="Stream URL", value=profile_url, inline=False)
    if live.avatar:
        embed.set_thumbnail(url=live.avatar)
    embed.set_footer(text="Source: IDN Live")
    return embed


def build_showroom_live_embed(live: ShowroomLive) -> discord.Embed:
    url = showroom_profile_url(live)
    embed = discord.Embed(
        title=f"SHOWROOM • {live.room_name}",
        description="Sedang live di SHOWROOM",
        color=discord.Color.dark_magenta(),
        url=url or discord.Embed.Empty,
    )
    if live.view_count is not None:
        embed.add_field(name="Viewers", value=str(live.view_count), inline=True)
    if live.room_id is not None:
        embed.add_field(name="Room ID", value=str(live.room_id), inline=True)
    started = format_discord_time(live.started_at)
    if started:
        embed.add_field(name="Started", value=started, inline=False)
    if live.image:
        embed.set_thumbnail(url=live.image)
    embed.set_footer(text="Source: SHOWROOM")
    return embed


def build_summary_embed(snapshot: StreamSnapshot) -> discord.Embed:
    embed = discord.Embed(
        title="JKT48 Stream Monitor",
        description="Live data from IDN Live and SHOWROOM APIs.",
        color=discord.Color.blurple(),
    )

    def summarize_idn(items: Sequence[IDNLive]) -> str:
        if not items:
            return "Tidak ada member yang live di IDN."
        lines: List[str] = []
        for live in items[:5]:
            url = idn_profile_url(live) or ""
            started = format_discord_time(live.started_at) or "Unknown"
            line = f"• **{live.display_name}** — {live.title}"
            line += f"\n  Mulai: {started}"
            if url:
                line += f"\n  [Klik disini]({url})"
            lines.append(line)
        remaining = len(items) - len(lines)
        if remaining > 0:
            lines.append(f"...dan {remaining} lainnya.")
        return "\n".join(lines)

    def summarize_showroom(items: Sequence[ShowroomLive]) -> str:
        if not items:
            return "Tidak ada member yang live di SHOWROOM."
        lines: List[str] = []
        for live in items[:5]:
            url = showroom_profile_url(live) or ""
            started = format_discord_time(live.started_at) or "Unknown"
            line = f"• **{live.room_name}**"
            line += f"\n  Mulai: {started}"
            if url:
                line += f"\n  [Klik disini]({url})"
            if live.view_count is not None:
                line += f", Viewers: {live.view_count}"
            lines.append(line)
        remaining = len(items) - len(lines)
        if remaining > 0:
            lines.append(f"...dan {remaining} lainnya.")
        return "\n".join(lines)

    embed.add_field(
        name=f"IDN Live ({len(snapshot.idn)})",
        value=summarize_idn(snapshot.idn),
        inline=False,
    )
    embed.add_field(
        name=f"SHOWROOM ({len(snapshot.showroom)})",
        value=summarize_showroom(snapshot.showroom),
        inline=False,
    )
    return embed


def build_live_end_embed(record: StoredLive) -> discord.Embed:
    embed = discord.Embed(
        title=f"{record.source} • {record.display_name}",
        description="Live telah selesai.",
        color=discord.Color.dark_gray(),
        url=record.url or discord.Embed.Empty,
    )
    if record.title:
        embed.add_field(name="Judul", value=record.title, inline=False)

    started_dt = parse_iso(record.started_at)
    ended_dt = parse_iso(record.ended_at)

    started_text = format_discord_time(started_dt)
    ended_text = format_discord_time(ended_dt)
    if started_text:
        embed.add_field(name="Mulai", value=started_text, inline=False)
    if ended_text:
        embed.add_field(name="Selesai", value=ended_text, inline=False)

    duration = human_duration(started_dt, ended_dt)
    if duration:
        embed.add_field(name="Durasi", value=duration, inline=False)

    embed.set_footer(text="Status: Offline")
    return embed


class JKT48Bot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # allow prefix commands like !streams
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.http_session: Optional[aiohttp.ClientSession] = None

    async def setup_hook(self) -> None:
        timeout = aiohttp.ClientTimeout(total=15)
        self.http_session = aiohttp.ClientSession(timeout=timeout)
        snapshot = await monitor.prime(self.http_session)
        await asyncio.to_thread(status_store.seed, snapshot)
        log.info(
            "Live status database seeded (%d IDN, %d SHOWROOM)",
            len(snapshot.idn),
            len(snapshot.showroom),
        )
        poll_streams.start()
        await send_startup_summary(snapshot)
        try:
            await self.tree.sync()
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to sync application commands: %s", exc)

    async def close(self) -> None:
        poll_streams.cancel()
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        await super().close()


status_store = LiveStatusStore(LIVE_STATUS_DB)
monitor = StreamMonitor()
bot = JKT48Bot()


async def fetch_target_channel() -> Optional[discord.abc.Messageable]:
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(CHANNEL_ID)
        except discord.DiscordException as exc:
            log.error("Unable to fetch Discord channel %s: %s", CHANNEL_ID, exc)
            return None
    return channel


async def send_startup_summary(snapshot: StreamSnapshot) -> None:
    channel = await fetch_target_channel()
    if channel is None:
        log.warning("Cannot send startup summary; channel %s not found", CHANNEL_ID)
        return

    embed = build_summary_embed(snapshot)
    try:
        await channel.send(content="🟢 JKT48 Member's Live Monitoring is Active!", embed=embed)
    except discord.DiscordException as exc:
        log.error("Failed to send startup summary: %s", exc)


async def notif_message_worker() -> None:
    assert notif_queue is not None
    while True:
        payload = await notif_queue.get()
        try:
            channel = await fetch_target_channel()
            if channel is None:
                continue
            embed = build_change_embed(payload)
            content = payload.title or payload.url or "Notifikasi baru"
            await channel.send(content=content, embed=embed)
            log.info("Webhook notification delivered to Discord for %s", payload.url)
        except Exception:  # noqa: BLE001
            log.exception("Failed to deliver webhook notification")
        finally:
            notif_queue.task_done()


async def start_notif_worker() -> None:
    if notif_queue is None:
        return
    global notif_worker_task
    if notif_worker_task is None or notif_worker_task.done():
        notif_worker_task = asyncio.create_task(notif_message_worker())


async def send_end_notifications(records: Sequence[StoredLive]) -> None:
    if not records:
        return

    channel = await fetch_target_channel()
    if channel is None:
        return

    for record in records:
        embed = build_live_end_embed(record)
        content = f"⚫ Live selesai: {record.display_name} ({record.source})"
        try:
            await channel.send(content=content, embed=embed)
        except discord.DiscordException as exc:
            log.error("Failed to send live end embed: %s", exc)


async def run_api() -> None:
    global api_server
    config = uvicorn.Config(app, host=API_HOST, port=API_PORT, log_level="info")
    api_server = uvicorn.Server(config)
    await api_server.serve()


@tasks.loop(minutes=2)
async def poll_streams() -> None:
    if bot.http_session is None:
        return
    try:
        snapshot = await monitor.fetch_snapshot(bot.http_session)
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to refresh stream data: %s", exc)
        return

    try:
        ended_records = await asyncio.to_thread(status_store.sync, snapshot)
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to sync live status database: %s", exc)
        ended_records = []

    embeds = monitor.consume_updates(snapshot)
    if not embeds and not ended_records:
        return

    channel = await fetch_target_channel()
    if channel is None:
        return

    for embed in embeds:
        try:
            await channel.send(content="🔴 JKT48 live update", embed=embed)
        except discord.DiscordException as exc:
            log.error("Failed to send embed: %s", exc)

    if ended_records:
        await send_end_notifications(ended_records)


@bot.command(name="streams")
async def streams_command(ctx: commands.Context) -> None:
    """Manual command to display the current snapshots."""
    result = await get_snapshot_embed()
    if isinstance(result, discord.Embed):
        await ctx.send(embed=result)
    else:
        await ctx.send(result)


@bot.command(name="help")
async def help_command(ctx: commands.Context) -> None:
    embed = discord.Embed(
        title="JKT48 Bot Commands",
        description="Perintah yang tersedia:",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="!streams",
        value="Menampilkan ringkasan live IDN & SHOWROOM saat ini.",
        inline=False,
    )
    await ctx.send(embed=embed)


@bot.tree.command(name="streams", description="Menampilkan ringkasan live IDN & SHOWROOM saat ini.")
async def streams_slash(interaction: discord.Interaction) -> None:
    result = await get_snapshot_embed()
    if isinstance(result, discord.Embed):
        await interaction.response.send_message(embed=result)
    else:
        await interaction.response.send_message(result, ephemeral=True)


async def get_snapshot_embed() -> discord.Embed | str:
    if bot.http_session is None:
        return "Bot belum siap, coba lagi sebentar lagi."
    try:
        snapshot = await monitor.fetch_snapshot(bot.http_session)
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to fetch snapshot on demand: %s", exc)
        return "Gagal mengambil data IDN atau SHOWROOM."
    return build_summary_embed(snapshot)


async def main() -> None:
    require_configuration()
    await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
