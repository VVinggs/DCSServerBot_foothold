import os
import re
import json
import logging
import asyncio
import discord
from core import Plugin, Server, Report, Status
from discord.ext import tasks
from typing import Optional, Tuple

ZONE_COMMANDER_FILE = os.path.join(os.path.dirname(__file__), "zoneCommander.lua")
ZONE_COMMANDER_DEFS = None

def get_zone_commander_defs():
    global ZONE_COMMANDER_DEFS
    if ZONE_COMMANDER_DEFS is None:
        ZONE_COMMANDER_DEFS = ZoneCommanderDefinitions(ZONE_COMMANDER_FILE)
    return ZONE_COMMANDER_DEFS


def _extract_lua_block(lua_str: str, start_pattern: str) -> Tuple[Optional[str], int]:
    match = re.search(start_pattern, lua_str)
    if not match:
        return None, -1
    idx = match.end() - 1
    depth = 0
    in_string = False
    escape = False
    start_idx = None
    for i in range(idx, len(lua_str)):
        ch = lua_str[i]
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            if start_idx is None:
                start_idx = i
            continue
        if ch == '{':
            if start_idx is None:
                start_idx = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start_idx is not None:
                return lua_str[start_idx : i + 1], i + 1
    return None, -1


def _lua_table_to_json(lua_table: str) -> str:
    json_text = lua_table
    # Convert single-quoted strings to JSON double-quoted strings.
    json_text = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", r'"\1"', json_text)
    # Convert quoted keys like ['key'] or ["key"] to JSON string keys.
    json_text = re.sub(r'\[\s*"([^\"]+)"\s*\]\s*=\s*', r'"\1": ', json_text)
    json_text = re.sub(r"\[\s*'([^']+)'\s*\]\s*=\s*", r'"\1": ', json_text)
    # Convert numeric bracketed keys like [1] = to JSON string keys
    json_text = re.sub(r'\[\s*(\d+)\s*\]\s*=\s*', r'"\1": ', json_text)
    # Convert bareword keys to JSON string keys.
    json_text = re.sub(r'([A-Za-z_][A-Za-z0-9_]*)\s*=', r'"\1": ', json_text)
    json_text = re.sub(r'\bnil\b', 'null', json_text)
    json_text = re.sub(r'\btrue\b', 'true', json_text)
    json_text = re.sub(r'\bfalse\b', 'false', json_text)
    json_text = re.sub(r',\s*([}\]])', r'\1', json_text)
    return json_text


def _flatten_rank_save_player(record: dict) -> dict:
    defs = get_zone_commander_defs()
    flat = {}
    flat['Points'] = int(record.get('credits', 0) or 0)
    career = record.get('career') or {}
    flat['career'] = career
    flat['credits'] = flat['Points']

    for stat_id, value in career.items():
        # Prefer the canonical CAREER_STAT name (e.g. 'AirKills') when available.
        name = None
        try:
            sid = int(stat_id)
        except Exception:
            sid = None

        if sid is not None:
            name = defs.career_stat_by_id.get(sid)

        # Fallback to a friendly label if we couldn't resolve the numeric id
        if not name:
            try:
                name = defs.friendly_career_label(sid) if sid is not None else str(stat_id)
            except Exception:
                name = str(stat_id)

        if name not in ('Points', 'credits'):
            flat[name] = int(value or 0)

    return flat

class ZoneCommanderDefinitions:
    def __init__(self, path: str):
        self.path = path
        self.career_stat = {}
        self.aircraft_metric = {}
        self.career_kill_stat_map = {}
        self.career_global_display = []
        self.career_aircraft_display = []
        self.aircraft_defs = {}
        self._load_definitions()

    def _load_definitions(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                text = f.read()
        except Exception:
            return

        def load_table(name):
            block, _ = _extract_lua_block(text, rf'{re.escape(name)}\s*=\s*\{{')
            if not block:
                return None
            try:
                table, _ = self._parse_lua_table(block, 0)
            except Exception:
                return None
            return self._resolve_references(table)

        self.career_stat = load_table('BattleCommander.CAREER_STAT') or {}
        self.aircraft_metric = load_table('BattleCommander.CAREER_AIRCRAFT_METRIC') or {}
        self.career_kill_stat_map = load_table('BattleCommander.CAREER_KILL_STAT_MAP') or {}
        self.career_global_display = load_table('BattleCommander.CAREER_GLOBAL_DISPLAY') or []
        self.career_aircraft_display = load_table('BattleCommander.CAREER_AIRCRAFT_DISPLAY') or []
        self.aircraft_defs = load_table('BattleCommander.CAREER_AIRCRAFT') or {}

        self.career_stat_by_id = {v: k for k, v in self.career_stat.items()}
        self.aircraft_metric_by_id = {v: k for k, v in self.aircraft_metric.items()}

    def _skip_whitespace(self, text: str, idx: int) -> int:
        while idx < len(text) and text[idx].isspace():
            idx += 1
        return idx

    def _parse_string(self, text: str, idx: int):
        end = idx + 1
        while end < len(text):
            if text[end] == '"':
                return text[idx + 1:end], end + 1
            if text[end] == '\\' and end + 1 < len(text):
                end += 2
            else:
                end += 1
        raise ValueError('Unterminated string')

    def _parse_number(self, text: str, idx: int):
        match = re.match(r'-?\d+(?:\.\d+)?', text[idx:])
        if not match:
            raise ValueError('Invalid number at %d' % idx)
        raw = match.group(0)
        end = idx + len(raw)
        return (float(raw) if '.' in raw else int(raw)), end

    def _parse_identifier(self, text: str, idx: int):
        match = re.match(r'[A-Za-z_][A-Za-z0-9_\.]*', text[idx:])
        if not match:
            raise ValueError('Invalid identifier at %d' % idx)
        return match.group(0), idx + len(match.group(0))

    def _parse_key(self, text: str, idx: int):
        idx = self._skip_whitespace(text, idx)
        if idx < len(text) and text[idx] == '"':
            return self._parse_string(text, idx)
        if idx < len(text) and (text[idx] == '-' or text[idx].isdigit()):
            return self._parse_number(text, idx)
        return self._parse_identifier(text, idx)

    def _parse_value(self, text: str, idx: int):
        idx = self._skip_whitespace(text, idx)
        if idx >= len(text):
            return None, idx
        char = text[idx]
        if char == '{':
            return self._parse_lua_table(text, idx)
        if char == '"':
            return self._parse_string(text, idx)
        if char == '-' or char.isdigit():
            return self._parse_number(text, idx)
        value, idx = self._parse_identifier(text, idx)
        lowered = value.lower()
        if lowered == 'true':
            return True, idx
        if lowered == 'false':
            return False, idx
        if lowered == 'nil':
            return None, idx
        return value, idx

    def _parse_lua_table(self, text: str, idx: int):
        assert text[idx] == '{'
        idx += 1
        result = {}
        array = []
        is_dict = False
        while True:
            idx = self._skip_whitespace(text, idx)
            if idx >= len(text):
                raise ValueError('Unterminated table')
            if text[idx] == '}':
                idx += 1
                return (result if is_dict else array), idx
            if text[idx] == ',':
                idx += 1
                continue

            key = None
            if text[idx] == '[':
                idx += 1
                idx = self._skip_whitespace(text, idx)
                if text[idx] == '"':
                    key, idx = self._parse_string(text, idx)
                else:
                    key, idx = self._parse_identifier(text, idx)
                idx = self._skip_whitespace(text, idx)
                if text[idx] != ']':
                    raise ValueError('Expected ]')
                idx += 1
                idx = self._skip_whitespace(text, idx)
                if text[idx] == '=':
                    is_dict = True
                    idx += 1
                    value, idx = self._parse_value(text, idx)
                    result[key] = value
                else:
                    array.append(key)
            else:
                match = re.match(r'([A-Za-z_][A-Za-z0-9_]*)\s*=', text[idx:])
                if match:
                    is_dict = True
                    key = match.group(1)
                    idx += match.end()
                    value, idx = self._parse_value(text, idx)
                    result[key] = value
                else:
                    value, idx = self._parse_value(text, idx)
                    array.append(value)

            idx = self._skip_whitespace(text, idx)
            if idx < len(text) and text[idx] == ',':
                idx += 1

    def _resolve_references(self, obj):
        if isinstance(obj, dict):
            return {self._resolve_references(k): self._resolve_references(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._resolve_references(v) for v in obj]
        if isinstance(obj, str):
            if obj.startswith('BattleCommander.CAREER_STAT.'):
                key = obj.split('.')[-1]
                return self.career_stat.get(key, obj)
            if obj.startswith('BattleCommander.CAREER_AIRCRAFT_METRIC.'):
                key = obj.split('.')[-1]
                return self.aircraft_metric.get(key, obj)
            return obj
        return obj

    def friendly_career_label(self, stat_id: int) -> str:
        if stat_id == self.career_stat.get('TotalKills'):
            return '💀'
        for category, mapping in self.career_kill_stat_map.items():
            if isinstance(mapping, list) and mapping[0] == stat_id:
                return category
        name = self.career_stat_by_id.get(stat_id)
        if name == 'FlightSeconds':
            return 'Flight time'
        if name == 'FixedWingSeconds':
            return 'Fixed Wing Seconds'
        if name == 'HelicopterSeconds':
            return 'Helicopter Seconds'
        if name == 'PilotDeaths':
            return 'PilotDeaths'
        if name is None:
            return str(stat_id)
        return re.sub(r'(?<!^)(?=[A-Z])', ' ', name).strip()

    def friendly_aircraft_label(self, metric_id: int) -> str:
        name = self.aircraft_metric_by_id.get(metric_id)
        if name == 'FlightSeconds':
            return 'Flight time'
        return re.sub(r'(?<!^)(?=[A-Z])', ' ', name).strip() if name else str(metric_id)


def parse_lua_stats(lua_str: str) -> dict:
    """Parses Lua RankSave data into a Python dict."""
    logger = logging.getLogger(__name__)
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

    # Try full RankSave = { ... } block first
    rank_block, _ = _extract_lua_block(lua_str, r'RankSave\s*=\s*\{')
    rank_data = None
    if rank_block is not None:
        try:
            json_text = _lua_table_to_json(rank_block)
            rank_data = json.loads(json_text)
            logger.debug("parse_lua_stats: parsed full RankSave via JSON; players=%d", len((rank_data.get('players') or {})))
        except Exception as e:
            logger.warning("parse_lua_stats: JSON parse failed for RankSave block: %s", e)
            # fallback: try the internal Lua table parser
            try:
                parsed, _ = _parse_table(rank_block, 0)
                if isinstance(parsed, dict) and 'players' in parsed:
                    rank_data = parsed
                    logger.debug("parse_lua_stats: parsed full RankSave via fallback parser; players=%d", len((rank_data.get('players') or {})))
                else:
                    # if parser returned an array or unexpected structure, wrap if possible
                    rank_data = parsed if isinstance(parsed, dict) else None
            except Exception as e2:
                logger.warning("parse_lua_stats: fallback parser failed for RankSave block: %s", e2)
                rank_data = None

    # If RankSave was present but contains no players (e.g. file has `RankSave = {}` then later assigns RankSave["players"]),
    # attempt to parse the players-specific assignment and merge it.
    if rank_data is not None and not (rank_data.get('players')):
        players_block, _ = _extract_lua_block(lua_str, r'RankSave\[\s*["\']players["\']\s*\]\s*=\s*\{')
        if players_block is not None:
            try:
                json_text = _lua_table_to_json(players_block)
                players_data = json.loads(json_text)
                rank_data['players'] = players_data
                logger.debug("parse_lua_stats: merged players block into RankSave; players=%d", len(players_data))
            except Exception as e:
                logger.warning("parse_lua_stats: JSON parse failed for players block during merge: %s", e)
                try:
                    parsed_players, _ = _parse_table(players_block, 0)
                    rank_data['players'] = parsed_players
                    logger.debug("parse_lua_stats: merged players block via fallback parser; players=%d", len(parsed_players))
                except Exception as e2:
                    logger.warning("parse_lua_stats: fallback parser failed for players block during merge: %s", e2)

    # If the full RankSave block wasn't present or parseable, try the players-specific assignment:
    # RankSave["players"] = { ... }
    if rank_data is None:
        players_block, _ = _extract_lua_block(lua_str, r'RankSave\[\s*["\']players["\']\s*\]\s*=\s*\{')
        if players_block is None:
            logger.debug("parse_lua_stats: no RankSave players block found")
            return stats
        try:
            json_text = _lua_table_to_json(players_block)
            players_data = json.loads(json_text)
            rank_data = { 'players': players_data }
            logger.debug("parse_lua_stats: parsed players block via JSON; players=%d", len(players_data))
        except Exception as e:
            logger.warning("parse_lua_stats: JSON parse failed for players block: %s", e)
            # fallback to internal parser
            try:
                parsed_players, _ = _parse_table(players_block, 0)
                rank_data = { 'players': parsed_players }
                logger.debug("parse_lua_stats: parsed players block via fallback parser; players=%d", len(parsed_players))
            except Exception as e2:
                logger.warning("parse_lua_stats: fallback parser failed for players block: %s", e2)
                return stats

    for player_name, player_data in (rank_data.get('players') or {}).items():
        if isinstance(player_data, dict):
            stats[player_name] = _flatten_rank_save_player(player_data)
    return stats

class Foothold(Plugin):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.last_save = {}
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
                # Immediately clear previous bot messages before sending any startup greeting or publish
                def _old_message_for_purge(m):
                    try:
                        if getattr(m, 'pinned', False):
                            return False
                        if not getattr(m, 'author', None):
                            return False
                        return getattr(m.author, 'id', None) == getattr(self.bot.user, 'id', None)
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
                    self.log.info(f"[Foothold] Cleared older messages in channel {channel.name} on startup.")
                except Exception as e:
                    self.log.warning(f"[Foothold] Failed clearing older messages on startup in {channel.name}: {e}")

                # Send a startup greeting after purge so it isn't immediately deleted
                try:
                    await channel.send(f"👋 **[Foothold Debug]** Service initialized for server match: `{server.name}`")
                    self.log.info(f"[Foothold] Sent startup debug greeting to channel: {channel.name}")
                except Exception as e:
                    self.log.error(f"[Foothold] Failed sending debug text to channel: {e}")

                # Attempt an initial publish immediately after startup
                try:
                    data, mtime = self.read_foothold_data(server)
                    if data:
                        report = Report(self.bot, self.plugin_name, "foothold.json")
                        env = await report.render(data=data)
                        try:
                            new_msg = await channel.send(embed=env.embed)
                            self.message_ids[server_name] = new_msg.id
                            self.log.info(f"[Foothold] Published startup campaign display to channel {channel.name} (message id={new_msg.id}).")
                        except Exception as send_exc:
                            self.log.error(f"[Foothold] Failed sending startup embed to {channel.name}: {send_exc}")
                            try:
                                desc = getattr(env.embed, 'description', None)
                                if desc:
                                    await channel.send(f"[Foothold DEBUG - embed failed]\n" + desc)
                            except Exception:
                                pass
                    # Mark first run state so the background loop treats subsequent cycles normally
                    self.first_run[server_name] = False
                except Exception as pub_exc:
                    self.log.warning(f"[Foothold] Startup publish failed for {server.name}: {pub_exc}")

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

        # Build clean absolute mapping to Saved Games/Instance/Missions/<lua_file_name>
        saves_dir = os.path.join(server.instance.home, "Missions", "Saves")
        self.log.debug(f"[Foothold] read_foothold_data: server={getattr(server, 'name', None)}, configured lua_file_name={lua_file_name}, saves_dir={saves_dir}")
        file_path = os.path.join(saves_dir, lua_file_name)
        if not os.path.exists(file_path):
            self.log.warning(f"[Foothold] Save file not found at path: {file_path}")
            return None, None

        try:
            mtime = os.path.getmtime(file_path)
            size = os.path.getsize(file_path)
            self.log.debug(f"[Foothold] Found save file at {file_path} (size={size} bytes, mtime={mtime})")
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            # log a short preview to help debug parsing issues
            preview = content[:1000].replace('\n', '\\n')
            data = parse_lua_stats(content)
            if not data:
                self.log.debug("[Foothold] parse_lua_stats returned empty data for file: %s", file_path)
            return data, mtime
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

                save_key = f"{server_name}:{config.get('lua_file_name')}"
                last_save = self.last_save.get(save_key)
                if not is_first_run and last_save == mtime:
                    continue

                channel = self.bot.get_channel(int(config['channel']))
                if not channel:
                    continue

                report = Report(self.bot, self.plugin_name, "foothold.json")
                env = await report.render(data=data)
                
                # Before publishing, remove previous bot messages so the channel
                # only shows the latest campaign display. Preserve pinned
                # messages and non-bot messages.
                self.log.debug(f"[Foothold] publish debug: server={server_name}, channel={getattr(channel,'id',None)}, is_first_run={is_first_run}, mtime={mtime}")

                def _old_message_for_purge(m):
                    try:
                        # preserve pinned messages
                        if getattr(m, 'pinned', False):
                            return False
                        if not getattr(m, 'author', None):
                            return False
                        return getattr(m.author, 'id', None) == getattr(self.bot.user, 'id', None)
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
                    # The report.render() implementation may return different shapes
                    # (an object with `.embed`, a raw `discord.Embed`, or plain text).
                    embed_obj = None
                    desc_preview = ''
                    try:
                        if isinstance(env, discord.Embed):
                            embed_obj = env
                        elif hasattr(env, 'embed') and isinstance(getattr(env, 'embed', None), discord.Embed):
                            embed_obj = env.embed
                        else:
                            # attempt to extract a description or string representation
                            desc = None
                            if hasattr(env, 'description') and env.description:
                                desc = env.description
                            elif isinstance(env, str) and env:
                                desc = env
                            elif hasattr(env, 'embed') and hasattr(env.embed, 'description'):
                                desc = getattr(env.embed, 'description', None)
                            if desc:
                                desc_preview = (desc[:300] + '...') if len(desc) > 300 else desc
                                embed_obj = discord.Embed(description=desc)

                    except Exception:
                        embed_obj = None

                    self.log.debug(f"[Foothold] Sending publish: channel={getattr(channel,'id',None)}, has_embed={bool(embed_obj)}, preview={desc_preview}")

                    if embed_obj:
                        new_msg = await channel.send(embed=embed_obj)
                        self.message_ids[server_name] = new_msg.id
                        self.log.info(f"[Foothold] Published campaign display embed to channel {channel.name} (message id={new_msg.id}).")
                    else:
                        # Fallback: try sending a textual description or a generic notice
                        sent = False
                        try:
                            text = None
                            if hasattr(env, 'description') and env.description:
                                text = env.description
                            elif isinstance(env, str) and env:
                                text = env
                            elif hasattr(env, 'embed') and hasattr(env.embed, 'description'):
                                text = getattr(env.embed, 'description', None)
                            if text:
                                fallback = await channel.send(text)
                                self.message_ids[server_name] = fallback.id
                                sent = True
                        except Exception:
                            sent = False

                        if not sent:
                            try:
                                fallback = await channel.send("[Foothold] Failed to publish embed; no content available.")
                                self.message_ids[server_name] = fallback.id
                            except Exception as fallback_exc:
                                self.log.error(f"[Foothold] Failed sending fallback message to {channel.name}: {fallback_exc}")
                except Exception as send_exc:
                    self.log.error(f"[Foothold] Unexpected error while publishing to {channel.name}: {send_exc}")

                self.last_save[save_key] = mtime
                if is_first_run:
                    self.first_run[server_name] = False

        except Exception as global_err:
            self.log.error(f"[Foothold] Error inside background loop routine: {global_err}")

    @foothold_loop.before_loop
    async def before_foothold_loop(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(Foothold(bot, None))
