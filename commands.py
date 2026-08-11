import os
import re
import asyncio
import discord
from core import Plugin, Server, Report, Status
from discord.ext import tasks
from typing import Optional

def parse_lua_stats(lua_str: str) -> dict:
    """Parses Lua zonePersistance['playerStats'] data block into a Python dict."""
    def _skip_whitespace(text: str, idx: int) -> int:
        while idx < len(text) and text[idx].isspace():
            idx += 1
        return idx

    def _parse_value(text: str, idx: int):
        idx = _skip_whitespace(text, idx)
        if idx >= len(text):
            return None, idx

        if text[idx] == '{':
            return _parse_table(text, idx)
        if text[idx] == '"':
            end = idx + 1
            while end < len(text) and text[end] != '"':
                if text[end] == '\\' and end + 1 < len(text):
                    end += 2
                else:
                    end += 1
            value = text[idx + 1:end]
            return value, end + 1

        match = re.match(r'(-?\d+)(?:\.(\d+))?|true|false|null', text[idx:], re.IGNORECASE)
        if match:
            raw = match.group(0)
            if raw.lower() == 'true':
                return True, idx + len(raw)
            if raw.lower() == 'false':
                return False, idx + len(raw)
            if raw.lower() == 'null':
                return None, idx + len(raw)
            if '.' in raw:
                return float(raw), idx + len(raw)
            return int(raw), idx + len(raw)

        return None, idx

    def _parse_table(text: str, idx: int):
        result = {}
        idx += 1
        idx = _skip_whitespace(text, idx)
        while idx < len(text) and text[idx] != '}':
            key_match = re.match(r'\["(.*?)"\]\s*=\s*', text[idx:])
            if not key_match:
                break
            key = key_match.group(1)
            idx += key_match.end()
            value, idx = _parse_value(text, idx)
            result[key] = value
            idx = _skip_whitespace(text, idx)
            if idx < len(text) and text[idx] == ',':
                idx += 1
            idx = _skip_whitespace(text, idx)
        return result, idx + 1

    stats = {}
    start = lua_str.find('zonePersistance["playerStats"]')
    if start == -1:
        return stats

    start = lua_str.find('{', start)
    if start == -1:
        return stats

    player_block, _ = _parse_table(lua_str, start)
    for player_name, player_data in player_block.items():
        if isinstance(player_data, dict):
            stats[player_name] = player_data
    return stats

class Foothold(Plugin):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.last_mtime = {}
        self.message_ids = {}
        self.first_run = {}
        self.log.info("[Foothold] Core plugin initialized into memory.")

    async def cog_load(self):
        await super().cog_load()
        interval = 120
        try:
            for server in self.bot.servers.values():
                server_config = self.get_config(server) or {}
                if 'update_interval' in server_config:
                    interval = int(server_config.get('update_interval', interval))
                    break
        except Exception:
            pass
        self.foothold_loop.change_interval(seconds=interval)
        self.foothold_loop.start()
        self.log.info(f"[Foothold] Background tracking loop successfully started (interval={interval}s).")

    async def on_ready(self) -> None:
        """Fires automatically on startup using a clean native framework scan."""
        await super().on_ready()
        self.log.info("[Foothold] Bot infrastructure on_ready hook reached. Processing configurations...")
        
        await asyncio.sleep(4)
        
        for server_name, server in self.bot.servers.items():
            config = self.get_config(server)
            if not config or 'channel' not in config:
                # CRITICAL DEBUG: Print out the exact keys the bot is expecting to see in foothold.yaml
                self.log.warning(f"[Foothold] Startup skip: No configuration block matches server name '{server.name}' or instance name '{server.instance.name}' inside foothold.yaml")
                continue
                
            channel = self.bot.get_channel(int(config['channel']))
            if channel:
                # Send a startup greeting; actual clearing is performed
                # once immediately before publishing the first embed.
                try:
                    await channel.send(f"👋 **[Foothold Debug]** Service initialized for server match: `{server.name}`")
                    self.log.info(f"[Foothold] Sent startup debug greeting to channel: {channel.name}")
                except Exception as e:
                    self.log.error(f"[Foothold] Failed sending debug text to channel: {e}")

    async def cog_unload(self):
        self.foothold_loop.cancel()
        await super().cog_unload()

    def read_foothold_data(self, server: Server) -> tuple[Optional[dict], Optional[float]]:
        config = self.get_config(server)
        if not config:
            return None, None
            
        lua_file_name = config.get('lua_file_name')
        if not lua_file_name:
            return None, None

        # Build clean absolute mapping to Saved Games/Instance/Missions/foothold_save.lua
        file_path = os.path.join(server.instance.home, "Missions", "Saves", lua_file_name)
        if not os.path.exists(file_path):
            self.log.warning(f"[Foothold] Save file not found at path: {file_path}")
            return None, None
            
        try:
            mtime = os.path.getmtime(file_path)
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return parse_lua_stats(content), mtime
        except Exception as e:
            self.log.error(f"[Foothold] Error reading file at {file_path}: {e}")
            return None, None

    @tasks.loop(seconds=120)
    async def foothold_loop(self):
        """Monitors campaign state data files and handles automated persistent tracking embeds."""
        try:
            for server_name, server in self.bot.servers.items():
                config = self.get_config(server)
                if not config or 'channel' not in config:
                    continue
                
                data, mtime = self.read_foothold_data(server)
                if not data:
                    continue

                is_first_run = self.first_run.get(server_name, True)

                if not is_first_run and server.status not in [Status.RUNNING, Status.PAUSED]:
                    continue
                
                self.last_mtime[server_name] = mtime
                channel = self.bot.get_channel(int(config['channel']))
                if not channel:
                    continue

                report = Report(self.bot, self.plugin_name, "foothold.json")
                env = await report.render(data=data)
                
                # Before publishing, remove older messages so channel only shows
                # the fresh campaign display. Preserve pinned messages and the
                # most recent display message (if we have its id) by using that
                # message's timestamp as the cutoff.
                send_time = discord.utils.utcnow()
                msg_id = self.message_ids.get(server_name)
                prev_msg = None
                # If we have a previous message, use its creation time as cutoff.
                # Otherwise, preserve very recent messages (5s buffer) so
                # greetings or near-simultaneous posts aren't removed.
                from datetime import timedelta
                cutoff_time = send_time - timedelta(seconds=5)
                if msg_id:
                    try:
                        prev_msg = await channel.fetch_message(msg_id)
                        if prev_msg and prev_msg.created_at:
                            cutoff_time = prev_msg.created_at
                    except Exception:
                        prev_msg = None

                self.log.debug(f"[Foothold] publish debug: server={server_name}, channel={getattr(channel,'id',None)}, is_first_run={is_first_run}, mtime={mtime}")

                def _old_message_for_purge(m):
                    try:
                        # skip pinned messages
                        if getattr(m, 'pinned', False):
                            return False
                        # skip the previous display message itself
                        if prev_msg and getattr(m, 'id', None) == getattr(prev_msg, 'id', None):
                            return False
                        return (m.created_at is not None and m.created_at < cutoff_time)
                    except Exception:
                        return False

                try:
                    if hasattr(channel, 'purge'):
                        try:
                            await channel.purge(limit=1000, check=_old_message_for_purge)
                        except Exception:
                            async for msg in channel.history(limit=1000):
                                if _old_message_for_purge(msg):
                                    try:
                                        await msg.delete()
                                    except Exception:
                                        pass
                    else:
                        async for msg in channel.history(limit=1000):
                            if _old_message_for_purge(msg):
                                try:
                                    await msg.delete()
                                except Exception:
                                    pass
                    self.log.info(f"[Foothold] Cleared older messages in channel {channel.name} before publishing.")
                except Exception as e:
                    self.log.warning(f"[Foothold] Failed clearing older messages before publish in {channel.name}: {e}")

                # Publish fresh embed and remember its id. Wrap send with error handling
                try:
                    new_msg = await channel.send(embed=env.embed)
                    self.message_ids[server_name] = new_msg.id
                    self.log.info(f"[Foothold] Published campaign display embed to channel {channel.name} (message id={new_msg.id}).")
                except Exception as send_exc:
                    self.log.error(f"[Foothold] Failed sending embed to {channel.name}: {send_exc}")
                    try:
                        fallback = await channel.send("[Foothold] Failed to publish embed; see logs for details.")
                        self.message_ids[server_name] = fallback.id
                    except Exception as fallback_exc:
                        self.log.error(f"[Foothold] Failed sending fallback message to {channel.name}: {fallback_exc}")

                if is_first_run:
                    self.first_run[server_name] = False

        except Exception as global_err:
            self.log.error(f"[Foothold] Error inside background loop routine: {global_err}")

    @foothold_loop.before_loop
    async def before_foothold_loop(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(Foothold(bot, None))
