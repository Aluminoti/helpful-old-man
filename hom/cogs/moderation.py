"""Private moderator commands ported from WOM Discord Bot 148c2bb."""

import asyncio
import logging
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from urllib.parse import quote

import discord
from discord import app_commands
from discord.ext import commands

from hom import utils
from hom.bot import Bot
from hom.config import Config
from hom.config import Constants
from hom.moderation_data import ANNOTATIONS
from hom.moderation_data import METRICS
from hom.moderation_data import parse_metric
from hom.wom import WomError

LOGGER = logging.getLogger(__name__)


async def metric_options(
    interaction: discord.Interaction[Any], current: str
) -> List[app_commands.Choice[str]]:
    query = current.lower()
    alias = parse_metric(query)
    return [
        app_commands.Choice(name=name, value=metric)
        for metric, name in METRICS.items()
        if query in metric or query in name.lower() or metric == alias
    ][:25]


def review_embed(review: Dict[str, Any]) -> discord.Embed:
    change, data = review["nameChange"], review["data"]
    old = (data.get("oldStats") or {}).get("data", {}).get("skills", {}).get("overall", {})
    new = (data.get("newStats") or {}).get("data", {}).get("skills", {}).get("overall", {})
    context = change.get("reviewContext") or {}
    reasons = {
        "transition_period_too_long": (
            f"Transition period between the two names is too long. (>{context.get('maxHoursDiff')} hours)"
        ),
        "excessive_gains": "Excessive gains between the two name changes.",
        "total_level_too_low": (
            f"Total level of old name is too low. (<{context.get('minTotalLevel')})"
        ),
    }
    lines = [
        f"New name on the hiscores? {'✅' if data.get('isNewOnHiscores') else '❌'}",
        f"Has no negative gains? {'❌' if data.get('hasNegativeGains') else '✅'}",
        f"Hours difference? `{data.get('hoursDiff')}`",
        f"EHP difference? `{data.get('ehpDiff')}`",
        f"EHB difference? `{data.get('ehbDiff')}`",
        f"Exp difference? `{new.get('experience', 0) - old.get('experience', 0) if new and old else 0}`",
        f"Old total level? `{old.get('level')}`",
        f"New total level? `{new.get('level')}`",
        f"\nReason: {reasons.get(context.get('reason', ''), 'Not supplied')}",
    ]
    return discord.Embed(
        title=f"Name change review: {change['oldName']} → {change['newName']}",
        description="\n".join(lines),
        colour=Constants.BLUE,
    )


class NameChangeReview(discord.ui.View):
    def __init__(
        self, origin: discord.Interaction[Any], change_id: int, embed: discord.Embed
    ) -> None:
        super().__init__(timeout=300)
        self.origin = origin
        self.change_id = change_id
        self.embed = embed
        self.finished = False
        self.lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction[Any]) -> bool:
        await interaction.response.defer(ephemeral=True)
        if interaction.user.id != self.origin.user.id:
            await interaction.followup.send("These buttons are not for you!", ephemeral=True)
            return False
        if not isinstance(interaction.user, discord.Member):
            await interaction.followup.send("Use this action in the server.", ephemeral=True)
            return False
        return await utils.mod_check(interaction)

    async def decide(self, interaction: discord.Interaction[Any], action: str) -> None:
        async with self.lock:
            if self.finished:
                await interaction.followup.send("This review has already ended.", ephemeral=True)
                return
            # Consume the review before the request, including ambiguous timeout outcomes.
            self.finished = True
            self.stop()
            try:
                await interaction.client.wom.moderation_request(
                    "POST", f"/names/{self.change_id}/{action}"
                )
                status = "Approved ✅" if action == "approve" else "Denied ❌"
                self.embed.colour = discord.Colour(
                    Constants.GREEN if action == "approve" else Constants.RED
                )
            except WomError as exc:
                status = (
                    "Approval timed out. Check the status again in a few minutes."
                    if action == "approve" and exc.status == 504
                    else f"Failed to {action} name change. {exc}"
                )
                self.embed.colour = discord.Colour(
                    Constants.ORANGE if exc.status == 504 else Constants.RED
                )
            self.embed.set_footer(text=status)
            await self.origin.edit_original_response(embed=self.embed, view=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(
        self, interaction: discord.Interaction[Any], button: discord.ui.Button["NameChangeReview"]
    ) -> None:
        await self.decide(interaction, "approve")

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(
        self, interaction: discord.Interaction[Any], button: discord.ui.Button["NameChangeReview"]
    ) -> None:
        await self.decide(interaction, "deny")

    async def on_timeout(self) -> None:
        async with self.lock:
            if self.finished:
                return
            self.finished = True
            self.embed.set_footer(text="Review expired. Run /namechange again to review.")
            try:
                await self.origin.edit_original_response(embed=self.embed, view=None)
            except discord.HTTPException:
                pass


class Moderation(commands.Cog):
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction[Any]) -> bool:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send("Use this command in the server.", ephemeral=True)
            return False
        return await utils.mod_check(interaction)

    async def cog_app_command_error(
        self, interaction: discord.Interaction[Any], error: app_commands.AppCommandError
    ) -> None:
        cause = getattr(error, "original", error)
        if isinstance(cause, app_commands.CheckFailure):
            return
        message = (
            str(cause)
            if isinstance(cause, WomError)
            else "An unexpected error occurred. Check the action's status before retrying."
        )
        if not isinstance(cause, WomError):
            # Do not log exception text/HTTP payloads that could contain credentials.
            LOGGER.error("Moderation command failed: %s", type(cause).__name__)
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def complete(
        self,
        interaction: discord.Interaction[Any],
        title: str,
        details: str,
        requester: Optional[discord.User] = None,
        message: Optional[str] = None,
    ) -> None:
        if requester:
            details += f"\nRequested by: {requester.mention}, `{requester.id}`, `{requester.name}`"
        details += f"\nModerator: {interaction.user.mention}, `{interaction.user.id}`, `{interaction.user.name}`"
        warning = ""
        try:
            logged = await utils.send_log_message(
                interaction, details, title=title, mod=interaction.user
            )
            if logged is None:
                warning = "\nThe moderation log channel is unavailable."
        except discord.HTTPException:
            warning = "\nThe action succeeded, but the moderation log could not be sent."
        await interaction.edit_original_response(
            embed=discord.Embed(
                title=title,
                description=(message or f"✅ {details}") + warning,
                colour=Constants.GREEN,
            )
        )

    async def grant_role(
        self, interaction: discord.Interaction[Any], user: discord.User, role_id: int
    ) -> str:
        if not role_id:
            return ""
        assert interaction.guild is not None
        member = interaction.guild.get_member(user.id)
        role = interaction.guild.get_role(role_id)
        if member is None or role is None:
            return (
                "\nThe action succeeded, but the recipient or configured role could not be found."
            )
        try:
            await member.add_roles(role)
        except discord.HTTPException:
            return "\nThe action succeeded, but the role could not be assigned."
        return ""

    async def deliver_secret(
        self,
        interaction: discord.Interaction[Any],
        user: discord.User,
        method: str,
        path: str,
        field: str,
        label: str,
        title: str,
        details: str,
        role_id: int = 0,
        **payload: Any,
    ) -> None:
        if interaction.guild is None or interaction.guild.get_member(user.id) is None:
            raise WomError(400, "Couldn't find that user in this server.")
        # Confirm DMs are open before creating or rotating a credential.
        try:
            dm = await user.send(f"Generating {label}...")
        except discord.HTTPException:
            raise WomError(
                400, f"Failed to send DM to {user.mention}. Please enable Direct Messages."
            )
        try:
            result = await self.bot.wom.moderation_request(method, path, **payload)
        except WomError:
            try:
                await dm.edit(
                    content=f"Failed to generate {label}. Ask a moderator to check the status before retrying."
                )
            except discord.HTTPException:
                pass
            raise
        secret = result.get(field)
        if not isinstance(secret, str) or not secret:
            raise WomError(
                502, "The API did not return the credential. Check the status before retrying."
            )
        try:
            await dm.edit(
                content=f"{label}:\n`{secret}`\n\nKeep this secret and save it somewhere safe."
            )
            message = f"✅ {title}. A DM has been sent to {user.mention}."
        except discord.HTTPException:
            message = "The action succeeded, but DM delivery failed. Enable DMs before requesting a replacement."
        message += await self.grant_role(interaction, user, role_id)
        await self.complete(interaction, title, details, user, message)

    @app_commands.guild_only()  # type: ignore[arg-type]
    @app_commands.describe(name_change_id="The namechange Id.")
    @app_commands.command(
        name="namechange", description="Review and take action on a name change."
    )
    async def namechange(self, interaction: discord.Interaction[Any], name_change_id: int) -> None:
        try:
            review = await self.bot.wom.moderation_request("GET", f"/names/{name_change_id}")
        except WomError as exc:
            if exc.status == 404:
                raise WomError(404, "Name change ID not found.") from exc
            if exc.status == 500:
                raise WomError(500, "Failed to load hiscores. Please try again.") from exc
            raise
        if review["nameChange"]["status"] != "pending":
            raise WomError(400, "This name change is not pending.")
        if not review.get("data"):
            raise WomError(400, "Name change data was not found.")
        embed = review_embed(review)
        await interaction.edit_original_response(
            embed=embed, view=NameChangeReview(interaction, name_change_id, embed)
        )

    @app_commands.guild_only()  # type: ignore[arg-type]
    @app_commands.describe(id="The group ID.", user="Discord user tag.")
    @app_commands.command(name="verify-group", description="Set a group as verified.")
    async def verify_group(
        self, interaction: discord.Interaction[Any], id: int, user: discord.User
    ) -> None:
        if interaction.guild is None or interaction.guild.get_member(user.id) is None:
            raise WomError(400, "Couldn't find that user in this server.")
        group = await self.bot.wom.moderation_request("PUT", f"/groups/{id}/verify")
        message = f"✅ `{group.get('name', id)}` has been successfully verified!"
        message += await self.grant_role(interaction, user, Config.HOM_GROUP_LEADER_ROLE)
        await self.complete(
            interaction,
            "Verified Group",
            f"Group: [{id}]({Config.HOM_BASE_WEBSITE_URL}/groups/{id})",
            user,
            message,
        )

    @app_commands.guild_only()  # type: ignore[arg-type]
    @app_commands.describe(id="The group ID.", user="Discord user tag.")
    @app_commands.command(
        name="reset-group-code", description="Reset a group's verification code."
    )
    async def reset_group_code(
        self, interaction: discord.Interaction[Any], id: int, user: discord.User
    ) -> None:
        await self.deliver_secret(
            interaction,
            user,
            "PUT",
            f"/groups/{id}/reset-code",
            "newCode",
            f"Verification code for group [{id}]({Config.HOM_BASE_WEBSITE_URL}/groups/{id})",
            "Group Code Reset",
            f"Group: `{id}`",
        )

    @app_commands.guild_only()  # type: ignore[arg-type]
    @app_commands.describe(id="The competition ID.", user="Discord user tag.")
    @app_commands.command(
        name="reset-competition-code", description="Reset a competition's verification code."
    )
    async def reset_competition_code(
        self, interaction: discord.Interaction[Any], id: int, user: discord.User
    ) -> None:
        await self.deliver_secret(
            interaction,
            user,
            "PUT",
            f"/competitions/{id}/reset-code",
            "newCode",
            f"Verification code for competition [{id}]({Config.HOM_BASE_WEBSITE_URL}/competitions/{id})",
            "Competition Code Reset",
            f"Competition: `{id}`",
        )

    @app_commands.guild_only()  # type: ignore[arg-type]
    @app_commands.describe(
        id="The group ID.", username="In-game username.", requester="Requester's Discord user tag."
    )
    @app_commands.command(name="remove-from-group", description="Remove a player from a group.")
    async def remove_from_group(
        self,
        interaction: discord.Interaction[Any],
        id: int,
        username: str,
        requester: Optional[discord.User] = None,
    ) -> None:
        await self.bot.wom.moderation_request(
            "DELETE", f"/groups/{id}/members", members=[username]
        )
        await self.complete(
            interaction,
            "Removed User From Group",
            f"Username: `{username}`\nGroup: [{id}]({Config.HOM_BASE_WEBSITE_URL}/groups/{id})",
            requester,
        )

    @app_commands.guild_only()  # type: ignore[arg-type]
    @app_commands.describe(
        id="The competition ID.",
        username="In-game username.",
        requester="Requester's Discord user tag.",
    )
    @app_commands.command(
        name="remove-from-competition", description="Remove a player from a competition."
    )
    async def remove_from_competition(
        self,
        interaction: discord.Interaction[Any],
        id: int,
        username: str,
        requester: Optional[discord.User] = None,
    ) -> None:
        await self.bot.wom.moderation_request(
            "DELETE", f"/competitions/{id}/participants", participants=[username]
        )
        await self.complete(
            interaction,
            "Removed User From Competition",
            f"Username: `{username}`\nCompetition: [{id}]({Config.HOM_BASE_WEBSITE_URL}/competitions/{id})",
            requester,
        )

    @app_commands.guild_only()  # type: ignore[arg-type]
    @app_commands.describe(id="The group ID.", requester="Requester's Discord user tag.")
    @app_commands.command(name="delete-group", description="Delete a group.")
    async def delete_group(
        self,
        interaction: discord.Interaction[Any],
        id: int,
        requester: Optional[discord.User] = None,
    ) -> None:
        await self.bot.wom.moderation_request("DELETE", f"/groups/{id}")
        await self.complete(interaction, "Deleted Group", f"Group: `{id}`", requester)

    @app_commands.guild_only()  # type: ignore[arg-type]
    @app_commands.describe(id="The competition ID.", requester="Requester's Discord user tag.")
    @app_commands.command(name="delete-competition", description="Delete a competition.")
    async def delete_competition(
        self,
        interaction: discord.Interaction[Any],
        id: int,
        requester: Optional[discord.User] = None,
    ) -> None:
        await self.bot.wom.moderation_request("DELETE", f"/competitions/{id}")
        await self.complete(interaction, "Deleted Competition", f"Competition: `{id}`", requester)

    @app_commands.guild_only()  # type: ignore[arg-type]
    @app_commands.describe(
        username="The username of the player to delete.", requester="Requester's Discord user tag."
    )
    @app_commands.command(name="delete-player", description="Delete a player from the database.")
    async def delete_player(
        self,
        interaction: discord.Interaction[Any],
        username: str,
        requester: Optional[discord.User] = None,
    ) -> None:
        await self.bot.wom.moderation_request("DELETE", f"/players/{quote(username, safe='')}")
        await self.complete(interaction, "Deleted User", f"Username: `{username}`", requester)

    @app_commands.guild_only()  # type: ignore[arg-type]
    @app_commands.describe(
        username="The username of the player to clear the history from.",
        requester="Requester's Discord user tag.",
    )
    @app_commands.command(
        name="clear-name-change-history", description="Clear a player's name change history."
    )
    async def clear_history(
        self,
        interaction: discord.Interaction[Any],
        username: str,
        requester: Optional[discord.User] = None,
    ) -> None:
        await self.bot.wom.moderation_request(
            "POST", f"/names/{quote(username, safe='')}/clear-history"
        )
        await self.complete(
            interaction, "Name Change History Cleared", f"Username: `{username}`", requester
        )

    @app_commands.guild_only()  # type: ignore[arg-type]
    @app_commands.describe(player="The player name.", requester="Requester's Discord user tag.")
    @app_commands.command(name="add-player-annotation", description="Add a player annotation.")
    @app_commands.choices(
        annotation=[app_commands.Choice(name=value, value=value) for value in ANNOTATIONS]
    )
    async def add_annotation(
        self,
        interaction: discord.Interaction[Any],
        player: str,
        annotation: str,
        requester: Optional[discord.User] = None,
    ) -> None:
        if annotation not in ANNOTATIONS:
            raise WomError(400, "Invalid annotation.")
        await self.bot.wom.moderation_request(
            "POST", f"/players/{quote(player, safe='')}/annotation", annotationType=annotation
        )
        await self.complete(
            interaction,
            "Added Player Annotation",
            f"Player: `{player}`\nAnnotation: `{annotation}`",
            requester,
        )

    @app_commands.guild_only()  # type: ignore[arg-type]
    @app_commands.describe(player="The player name.", requester="Requester's Discord user tag.")
    @app_commands.command(
        name="remove-player-annotation", description="Remove a player annotation."
    )
    @app_commands.choices(
        annotation=[app_commands.Choice(name=value, value=value) for value in ANNOTATIONS]
    )
    async def remove_annotation(
        self,
        interaction: discord.Interaction[Any],
        player: str,
        annotation: str,
        requester: Optional[discord.User] = None,
    ) -> None:
        if annotation not in ANNOTATIONS:
            raise WomError(400, "Invalid annotation.")
        await self.bot.wom.moderation_request(
            "DELETE", f"/players/{quote(player, safe='')}/annotation", annotationType=annotation
        )
        await self.complete(
            interaction,
            "Deleted Player Annotation",
            f"Player: `{player}`\nAnnotation: `{annotation}`",
            requester,
        )

    @app_commands.guild_only()  # type: ignore[arg-type]
    @app_commands.describe(
        username="In-game username.", metric="The metric to rollback values for."
    )
    @app_commands.command(
        name="rollback-player-snapshot-values",
        description="Rollback a player snapshot metric values.",
    )
    @app_commands.autocomplete(metric=metric_options)
    async def rollback(
        self, interaction: discord.Interaction[Any], username: str, metric: str
    ) -> None:
        parsed = parse_metric(metric)
        if parsed is None:
            raise WomError(400, "Invalid metric.")
        await self.bot.wom.moderation_request(
            "POST",
            f"/players/{quote(username, safe='')}/rollback-snapshot-metric-values",
            metric=parsed,
        )
        await self.complete(
            interaction,
            "Snapshot Metric Values Rolled Back",
            f"Username: `{username}`\nMetric: `{parsed}`",
        )

    @app_commands.guild_only()  # type: ignore[arg-type]
    @app_commands.describe(
        project="The name of the project this key is meant to be used by.",
        requester="Requester's Discord user tag.",
    )
    @app_commands.command(name="create-api-key", description="Create a new Wise Old Man API key.")
    async def create_api_key(
        self, interaction: discord.Interaction[Any], project: str, requester: discord.User
    ) -> None:
        await self.deliver_secret(
            interaction,
            requester,
            "POST",
            "/api-key",
            "id",
            f"Wise Old Man API key for `{project}` (<https://docs.wiseoldman.net/#rate-limits--api-keys>)",
            "API Key Created",
            f"Project: `{project}`",
            Config.HOM_API_CONSUMER_ROLE,
            application=project,
            developer=requester.name,
        )


async def setup(bot: Bot) -> None:
    await bot.add_cog(Moderation(bot))
