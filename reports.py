import discord
from core import report
from .commands import get_zone_commander_defs


class FootholdStats(report.EmbedElement):
    async def render(self, data: dict):
        """Render two emoji-labeled tables:
        Table 1: Credits, TotalKills, PilotDeaths, Ejections
        Table 2: Air, Helo, Ground, SAM, Ship, Structure, Infantry, Friendly
        """
        self.embed.title = "⚔️ Foothold Campaign Leaderboard"
        self.embed.color = discord.Color.blue()

        def _get_stat(stats: dict, candidates: list, default="-"):
            if not isinstance(stats, dict):
                return default
            for c in candidates:
                if c in stats:
                    return stats[c]
                if c.lower() in stats:
                    return stats[c.lower()]
            for c in candidates:
                for k in stats.keys():
                    try:
                        if str(k).lower().startswith(str(c).lower()):
                            return stats[k]
                    except Exception:
                        continue
            return default

        def _sort_key(item):
            stats = item[1]
            def _to_int(x):
                try:
                    if x is None:
                        return 0
                    if isinstance(x, (int, float)):
                        return int(x)
                    # strip commas, spaces
                    s = str(x).strip().replace(',', '')
                    if s == '-' or s == '':
                        return 0
                    if '.' in s:
                        return int(float(s))
                    return int(s)
                except Exception:
                    return 0

            return (
                _to_int(_get_stat(stats, ["Points", "points"])),
                _to_int(_get_stat(stats, ["TotalKills", "totalkills", "Total"])),
                _to_int(_get_stat(stats, ["credits", "Credits"])),
            )

        # Normalize incoming data: if a player's stats include a 'career' table
        # with numeric IDs, expand them to their CAREER_STAT names so the
        # report tables can find keys like 'AirKills'.
        try:
            defs = get_zone_commander_defs()
        except Exception:
            defs = None

        normalized = {}
        for player, stats in (data or {}).items():
            if not isinstance(stats, dict):
                normalized[player] = stats
                continue
            s = dict(stats)
            career = s.get('career') or {}
            if isinstance(career, dict) and defs:
                for k, v in career.items():
                    try:
                        sid = int(k)
                    except Exception:
                        sid = None
                    if sid is not None:
                        name = defs.career_stat_by_id.get(sid)
                        if name and name not in s:
                            s[name] = int(v or 0)
            # prefer explicit Points/credits mapping
            if 'Points' not in s:
                if 'credits' in s:
                    s['Points'] = s.get('credits')
            normalized[player] = s

        sorted_players = sorted(normalized.items(), key=_sort_key, reverse=True)

        def _short_name(name: str) -> str:
            parts = name.split()
            if len(parts) <= 2:
                return name
            return " ".join(parts[-2:])

        table1_cols = [
            ("Player", None),
            ("💰 Credits", ["credits", "Credits", "Credit"]),
            ("⚔️ Total", ["TotalKills", "totalkills", "Total", "Kills"]),
            ("💀 Deaths", ["PilotDeaths", "pilotdeaths", "Deaths", "deaths"]),
            ("🪂 Ejections", ["Ejections", "ejects", "ejected"]),
        ]

        # split the previous second table into two tables (both include player)
        table2_cols = [
            ("✈️ Air", ["AirKills", "Air", "air"]),
            ("🚁 Helo", ["HeloKills", "Helo", "helo"]),
            ("🛡️ Ground", ["GroundKills", "Ground Units", "Ground", "ground"]),
            ("📡 SAM", ["SamKills", "SAM", "sam"]),
        ]

        table3_cols = [
            ("🚢 Ship", ["ShipKills", "Ship", "ship"]),
            ("🏗️ Structure", ["StructureKills", "Structure", "structure", "Buildings"]),
            ("🪖 Infantry", ["InfantryKills", "Infantry", "infantry"]),
            ("⚠️ Friendly", ["FriendlyKills", "Friendly", "friendly"]),
        ]

        table4_cols = [
            ("📦 Supplies", ["SupplyUnitsDelivered", "supplyunitsdelivered", "SupplyUnits"]),
            ("🪂 Airdrops", ["AirdroppedSupplyUnits", "airdroppedsupplyunits", "AirdroppedSupplyUnits"]),
            ("🧑‍✈️ Pilot Rescues", ["PilotRescues", "pilotrescues", "PilotRescue"]),
        ]

        table5_cols = [
            ("✈️ FW Streak", ["FixedWingAirStreakBest", "FixedWingAirStreak", "FixedWingStreak"]),
            ("🚁 Helo Streak", ["HelicopterAirStreakBest", "HelicopterAirStreak", "HelicopterStreak"]),
            ("🛬 Takeoffs/Landings", ["Takeoffs", "takeoff", "Takeoff", "Landings", "landings", "Landing"]),
        ]

        max_players = 25
        rows1 = []
        rows2 = []
        rows3 = []
        rows4 = []
        rows5 = []

        def _numeric_stat(stats: dict, candidates: list):
            value = _get_stat(stats, candidates)
            try:
                return float(value)
            except Exception:
                return None

        for player, stats in sorted_players[:max_players]:
            sname = _short_name(player)
            row1 = [sname]
            for _label, candidates in table1_cols[1:]:
                val = _get_stat(stats, candidates)
                row1.append(str(val if val is not None else "-"))
            rows1.append(row1)

            row2 = [sname]
            for _label, candidates in table2_cols:
                val = _get_stat(stats, candidates)
                row2.append(str(val if val is not None else "-"))
            rows2.append(row2)

            row3 = [sname]
            for _label, candidates in table3_cols:
                val = _get_stat(stats, candidates)
                row3.append(str(val if val is not None else "-"))
            rows3.append(row3)

            row4 = [sname]
            for _label, candidates in table4_cols:
                val = _get_stat(stats, candidates)
                row4.append(str(val if val is not None else "-"))
            rows4.append(row4)

            takeoffs = _numeric_stat(stats, ["Takeoffs", "takeoff", "Takeoff"])
            landings = _numeric_stat(stats, ["Landings", "landings", "Landing"])
            if takeoffs is not None and landings is not None and landings != 0:
                ratio = f"{takeoffs / landings:.2f}"
            else:
                ratio = "-"

            row5 = [sname]
            for _label, candidates in table5_cols[:-1]:
                val = _get_stat(stats, candidates)
                row5.append(str(val if val is not None else "-"))
            row5.append(ratio)
            rows5.append(row5)

        if not rows1:
            self.embed.description = "No stats available."
            return

        def render_table(header_labels, rows, min_widths_override=None):
            min_widths = min_widths_override or {}
            widths = []
            for ci, col in enumerate(header_labels):
                maxw = len(col)
                for r in rows:
                    if ci < len(r):
                        maxw = max(maxw, len(r[ci]))
                minw = min_widths.get(col, 4)
                widths.append(max(maxw, minw))

            def _truncate_cell(s: str, w: int) -> str:
                if len(s) <= w:
                    return s
                if w <= 1:
                    return s[:w]
                return s[: w - 1] + "…"

            header = " | ".join(header_labels[i].ljust(widths[i]) for i in range(len(header_labels)))
            separator = "-+-".join("-" * widths[i] for i in range(len(header_labels)))
            lines = [header, separator]
            for r in rows:
                rendered = []
                for i in range(len(header_labels)):
                    cell = r[i] if i < len(r) else ""
                    cell = _truncate_cell(cell, widths[i])
                    rendered.append(cell.ljust(widths[i]))
                lines.append(" | ".join(rendered))
            return "```\n" + "\n".join(lines) + "\n```"

        # use short emoji-only headers for compact display; include Player column in each
        headers1 = ["Player", "💰", "⚔️", "💀", "🪂"]
        headers2 = ["Player", "✈️", "🚁", "🛡️", "📡"]
        headers3 = ["Player", "🚢", "🏗️", "🪖", "⚠️"]
        headers4 = ["Player", "📦", "🪂", "🧑‍✈️"]
        headers5 = ["Player", "✈️", "🚁", "🛬"]

        min_widths1 = {headers1[0]: 15}
        min_widths23 = {"Player": 12}
        min_widths45 = {"Player": 12}
        table1_block = render_table(headers1, rows1, min_widths_override=min_widths1)
        table2_block = render_table(headers2, rows2, min_widths_override=min_widths23)
        table3_block = render_table(headers3, rows3, min_widths_override=min_widths23)
        table4_block = render_table(headers4, rows4, min_widths_override=min_widths45)
        table5_block = render_table(headers5, rows5, min_widths_override=min_widths45)

        # append a short legend mapping emojis to stat names
        legend = (
            "Legend: 💰 Credits | ⚔️ Total | 💀 Deaths | 🪂 Ejections | "
            "✈️ Air | 🚁 Helo | 🛡️ Ground | 📡 SAM | 🚢 Ship | 🏗️ Structure | 🪖 Infantry | ⚠️ Friendly | "
            "📦 Supplies | 🪂 Airdrops | 🧑‍✈️ Pilot Rescues | ✈️ FW Streak | 🚁 Helo Streak | 🛬 Takeoff/Landing"
        )

        # headers showing the emojis used in each table
        header1 = "Overall rank: 💰 Credits | ⚔️ Kills | 💀 Deaths | 🪂 Ejections"
        header2 = "Enemies Killed: ✈️ Air | 🚁 Helo | 🛡️ Ground | 📡 SAM"
        header3 = "Enemies Killed: 🚢 Ship | 🏗️ Structure | 🪖 Infantry | ⚠️ Friendly"
        header4 = "Support: 📦 Supplies | 🪂 Airdrops | 🧑‍✈️ Pilot Rescues"
        header5 = "Streaks/Ratios: ✈️ FW Streak | 🚁 Helo Streak | 🛬 Takeoff/Landing"

        description = (
            header1 + "\n" + table1_block + "\n"
            + header2 + "\n" + table2_block + "\n"
            + header3 + "\n" + table3_block + "\n"
            + header4 + "\n" + table4_block + "\n"
            + header5 + "\n" + table5_block + "\n"
            + legend
        )
        if len(description) > 4000:
            description = description[:3997] + "...```"

        self.embed.description = description
