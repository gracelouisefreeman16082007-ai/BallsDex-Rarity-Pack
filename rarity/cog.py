import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from django.utils import timezone

from ballsdex.core.utils.menus import ListSource, Menu, TextFormatter
from ballsdex.core.utils.transformers import BallEnabledTransform, SpecialEnabledTransform
from bd_models.models import balls, specials as special_cache

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.rarity")

# Configuration constants
ITEMS_PER_PAGE = 7 # How many tiers are shown on a page
# INTEGER

class RarityView(discord.ui.LayoutView):
    """A simple embed paginator for Discord."""

    def __init__(self, interaction: discord.Interaction, **kwargs):
        super().__init__(**kwargs)
        self.invoker_id = interaction.user.id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                "You cannot use this button.", ephemeral=True
            )
            return False
        return True


class Rarity(commands.Cog):
    """
    Rarity cog
    """

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @staticmethod
    def _is_special_active(special) -> bool:
        return (
            (special.start_date or timezone.datetime.min.replace(tzinfo=timezone.get_current_timezone()))
            <= timezone.now()
            <= (special.end_date or timezone.datetime.max.replace(tzinfo=timezone.get_current_timezone()))
        )

    @staticmethod
    def _format_percentage(value: float) -> str:
        percentage = value * 100
        if percentage == 0:
            return "0%"
        if percentage >= 1:
            return f"{percentage:.2f}".rstrip("0").rstrip(".") + "%"
        return f"{percentage:.4f}".rstrip("0").rstrip(".") + "%"

    def _format_special_emoji(self, special) -> str:
        if not special.emoji:
            return "N/A"

        try:
            emoji = self.bot.get_emoji(int(special.emoji))
        except (TypeError, ValueError):
            return special.emoji

        return str(emoji) if emoji else "N/A"

    def _get_special_line(self, special) -> str:
        return f"\u200b ⋄ {self._format_special_emoji(special)} {special.name}"

    def _is_special_spawnable(self, special) -> bool:
        return not special.hidden and special.rarity > 0 and self._is_special_active(special)

    async def _send_text_menu(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        title: str,
        entries: list[tuple[str, str]],
    ):
        pages = []
        page_entries = []

        for entry in entries:
            name, _ = entry
            if page_entries and (
                len(page_entries) >= ITEMS_PER_PAGE or name == page_entries[-1][0]
            ):
                pages.append(page_entries)
                page_entries = []
            page_entries.append(entry)

        if page_entries:
            pages.append(page_entries)

        page_texts = []
        total_pages = len(pages)
        for i, page_entries in enumerate(pages):
            text = ""
            for name, value in page_entries:
                text += f"### {name}\n{value}\n"
            text += f"\n-# Page {i + 1}/{total_pages}"
            page_texts.append(text)

        view = RarityView(interaction)
        container = discord.ui.Container(
            discord.ui.TextDisplay(content=f"# {title}"),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large),
        )
        text_display = discord.ui.TextDisplay("")
        container.add_item(text_display)
        view.add_item(container)
        menu = Menu(
            self.bot,
            view,
            ListSource(page_texts),
            TextFormatter(text_display),
        )
        await menu.init()
        await interaction.followup.send(view=view)

    @app_commands.describe(
        specials="Show the enabled specials list",
        special="Specific special event to show spawn chance for",
    )
    @app_commands.checks.cooldown(1, 20, key=lambda i: i.user.id)
    async def rarity(
        self,
        interaction: discord.Interaction["BallsDexBot"],
        countryball: BallEnabledTransform | None = None,
        special: SpecialEnabledTransform | None = None,
        tier: int | None = None,
        specials: bool = False,
        reverse: bool = False,
    ):
        """
        Show the rarity list of the collectibles
        
        Parameters
        ----------
        countryball: BallEnabledTransform
            Specific countryball to show rarity for
        special: SpecialEnabledTransform
            Specific special event to show spawn chance for
        tier: int
            Specific tier to show
        specials: bool
            Whether to show the special event spawn chance list
        reverse: bool
            Whether to reverse the rarity list
        """
        try:
            await interaction.response.defer(thinking=True)
            
            from settings.models import settings

            balls_rarity_list_title = f"{settings.plural_collectible_name.title()} Rarity List"
            specials_rarity_list_title = "Specials Rarity List"
            
            if sum(parameter is not None for parameter in (countryball, special, tier)) + int(specials) > 1:
                await interaction.followup.send(
                    "You can only use one of countryball, special, tier, or specials at a time.",
                    ephemeral=True,
                )
                return

            active_specials = [x for x in special_cache.values() if self._is_special_active(x)]

            if special:
                if not self._is_special_spawnable(special):
                    await interaction.followup.send(
                        "That special is not currently spawnable.",
                        ephemeral=True,
                    )
                    return

                text = f"# {specials_rarity_list_title}\n### ∥ {self._format_percentage(special.rarity)}\n{self._get_special_line(special)}\n"
                view = discord.ui.LayoutView()
                container = discord.ui.Container()
                text_display = discord.ui.TextDisplay(text)
                container.add_item(text_display)
                view.add_item(container)
                await interaction.followup.send(view=view)
                return

            if specials:
                visible_specials = [x for x in active_specials if self._is_special_spawnable(x)]

                if not visible_specials:
                    await interaction.followup.send("No active specials are available.", ephemeral=True)
                    return

                sorted_specials = sorted(
                    visible_specials,
                    key=lambda x: x.rarity,
                    reverse=reverse,
                )

                all_entries = []
                percentage_to_specials = {}
                for event in sorted_specials:
                    percentage = self._format_percentage(event.rarity)
                    percentage_to_specials.setdefault(percentage, []).append(event)

                for percentage, events in percentage_to_specials.items():
                    names = "\n".join(self._get_special_line(event) for event in events)

                    if len(names) > 1024:
                        current_chunk = []
                        current_length = 0

                        for event in events:
                            line = f"{self._get_special_line(event)}\n"
                            line_length = len(line)

                            if current_length + line_length > 1024:
                                all_entries.append((f"∥ {percentage}", "".join(current_chunk)))
                                current_chunk = [line]
                                current_length = line_length
                            else:
                                current_chunk.append(line)
                                current_length += line_length

                        if current_chunk:
                            all_entries.append((f"∥ {percentage}", "".join(current_chunk)))
                    else:
                        all_entries.append((f"∥ {percentage}", names))

                await self._send_text_menu(interaction, specials_rarity_list_title, all_entries)
                return

            enabled_collectibles = [x for x in balls.values() if x.enabled and x.rarity > 0]

            if not enabled_collectibles:
                await interaction.followup.send(
                    f"There are no {settings.plural_collectible_name} registered in {settings.bot_name} yet.",
                    ephemeral=True,
                )
                return

            rarities = [c.rarity for c in enabled_collectibles]
            min_rarity = min(rarities) if rarities else 1.0
            max_rarity = max(rarities) if rarities else 1.0

            if max_rarity > min_rarity:
                multiplier = 99.0 / (max_rarity - min_rarity)
            else:
                multiplier = 1.0

            rarity_to_collectibles = {}
            for c in enabled_collectibles:
                if max_rarity > min_rarity:
                    tier_num = int((c.rarity - min_rarity) * multiplier + 1.5)
                else:
                    tier_num = 1
                tier_num = max(1, tier_num)
                rarity_to_collectibles.setdefault(tier_num, []).append(c)

            if countryball:
                target_ball = countryball
                if target_ball.rarity <= 0:
                    await interaction.followup.send(
                        f"That {settings.collectible_name} is not included in the rarity list.",
                        ephemeral=True,
                    )
                    return

                if max_rarity > min_rarity:
                    tier_num = int((target_ball.rarity - min_rarity) * multiplier + 1.5)
                else:
                    tier_num = 1
                tier_num = max(1, tier_num)
                collectible_name = f"\u200b ⋄ {self.bot.get_emoji(target_ball.emoji_id) or 'N/A'} {target_ball.country}"

                text = f"# {balls_rarity_list_title}\n### ∥ T{tier_num}\n{collectible_name}\n"
                view = discord.ui.LayoutView()
                container = discord.ui.Container()
                text_display = discord.ui.TextDisplay(text)
                container.add_item(text_display)
                view.add_item(container)
                await interaction.followup.send(view=view)
                return

            if tier:
                if tier not in rarity_to_collectibles:
                    await interaction.followup.send(f"T{tier} does not exist.", ephemeral=True)
                    return

                filtered_collectibles = rarity_to_collectibles[tier]

                names = "".join(
                    f"\u200b ⋄ {self.bot.get_emoji(c.emoji_id) or 'N/A'} {c.country}\n"
                    for c in filtered_collectibles
                )
                await self._send_text_menu(
                    interaction,
                    f"{balls_rarity_list_title} - T{tier}",
                    [(f"∥ T{tier}", names)],
                )
                return

            all_entries = []

            sorted_rarities = sorted(rarity_to_collectibles.keys())
            if reverse:
                sorted_rarities.reverse()

            for i in sorted_rarities:
                collectibles = rarity_to_collectibles[i]
                names = "\n".join(
                    f"\u200b ⋄ {self.bot.get_emoji(c.emoji_id) or 'N/A'} {c.country}" for c in collectibles
                )

                if len(names) > 1024:
                    current_chunk = []
                    current_length = 0

                    for c in collectibles:
                        line = f"\u200b ⋄ {self.bot.get_emoji(c.emoji_id) or 'N/A'} {c.country}\n"
                        line_length = len(line)

                        if current_length + line_length > 1024:
                            chunk_text = "".join(current_chunk)
                            all_entries.append((f"∥ T{i}", chunk_text))
                            current_chunk = [line]
                            current_length = line_length
                        else:
                            current_chunk.append(line)
                            current_length += line_length

                    if current_chunk:
                        chunk_text = "".join(current_chunk)
                        all_entries.append((f"∥ T{i}", chunk_text))
                else:
                    all_entries.append((f"∥ T{i}", names))

            if not all_entries:
                await interaction.followup.send("No rarity data available.", ephemeral=True)
                return

            await self._send_text_menu(interaction, balls_rarity_list_title, all_entries)

        except Exception:
            log.exception("Error building rarity list")
            raise
