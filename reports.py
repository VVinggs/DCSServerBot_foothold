import discord
from core import report

class FootholdStats(report.EmbedElement):
    async def render(self, data: dict):
        self.embed.title = f"⚔️ Foothold Campaign Leaderboard"
        self.embed.color = discord.Color.blue()
        sorted_players = sorted(data.items(), key=lambda item: item[1].get('Points', 0), reverse=True)

        # Define columns and order for the table display
        # 'AG' maps to original 'Ground Units', 'AA' -> 'Air', 'inf' -> 'Infantry'
        columns = ["Player", "Points", "💀", "AG", "AA", "inf", "Helo"]

        def _cell(v):
            if v is None:
                return "-"
            if isinstance(v, bool):
                return "Yes" if v else "No"
            return str(v)

        # Build rows (limit to a reasonable number of players to avoid oversized embeds)
        max_players = 25
        rows = []
        # mapping from display column -> actual stat key in the data
        lookup = {
            "AG": "Ground Units",
            "AA": "Air",
            "inf": "Infantry",
        }

        for player, stats in sorted_players[:max_players]:
            # Display only up to the last two words of the player name
            parts = player.split()
            if len(parts) <= 2:
                display_name = player
            else:
                display_name = " ".join(parts[-2:])

            row = [display_name]
            for col in columns[1:]:
                key = lookup.get(col, col)
                row.append(_cell(stats.get(key, stats.get(key.lower(), "-"))))
            rows.append(row)

        if not rows:
            self.embed.description = "No stats available."
            return

        # Compute column widths with sensible minimums to produce a wider table
        min_widths = {
            "Player": 15,
            "Points": 6,
            "💀": 2,
            "AG": 3,
            "AA": 3,
            "inf": 3,
            "Helo": 4,
        }

        # Calculate natural widths then apply minimums
        widths = []
        for ci, col in enumerate(columns):
            maxw = len(col)
            for r in rows:
                if ci < len(r):
                    maxw = max(maxw, len(r[ci]))
            minw = min_widths.get(col, 4)
            widths.append(max(maxw, minw))

        # Truncate cell content if it exceeds its column width (reserve 1 char for ellipsis)
        def _truncate_cell(s: str, w: int) -> str:
            if len(s) <= w:
                return s
            if w <= 1:
                return s[:w]
            return s[: w - 1] + "…"

        # Render header, separator, and rows with monospace formatting
        header = " | ".join(columns[i].ljust(widths[i]) for i in range(len(columns)))
        separator = "-+-".join("-" * widths[i] for i in range(len(columns)))
        lines = [header, separator]
        for r in rows:
            rendered = []
            for i in range(len(columns)):
                cell = r[i] if i < len(r) else ""
                cell = _truncate_cell(cell, widths[i])
                rendered.append(cell.ljust(widths[i]))
            lines.append(" | ".join(rendered))

        remaining = len(sorted_players) - len(rows)
        if remaining > 0:
            lines.append(f"... and {remaining} more players")

        table = "```\n" + "\n".join(lines) + "\n```"

        # Discord embed description limit is 4096 characters; truncate if necessary
        if len(table) > 4000:
            table = table[:3997] + "...```"

        self.embed.description = table
