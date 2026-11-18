import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import aiohttp
import discord
from discord import app_commands

from hom.bot import Bot
from hom.cogs.moderation import Moderation
from hom.cogs.moderation import NameChangeReview
from hom.cogs.moderation import metric_options
from hom.cogs.moderation import review_embed
from hom.config import Config
from hom.moderation_data import ANNOTATIONS
from hom.moderation_data import METRIC_ALIASES
from hom.moderation_data import METRICS
from hom.moderation_data import parse_metric
from hom.wom import WomClient
from hom.wom import WomError


def member(user_id=1, moderator=True):
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.name = "requester"
    user.display_name = "Requester"
    user.mention = f"<@{user_id}>"
    user.roles = [SimpleNamespace(id=Config.HOM_MOD_ROLE)] if moderator else []
    user.send = AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))
    user.add_roles = AsyncMock()
    return user


def interaction(user=None):
    return SimpleNamespace(
        user=user or member(),
        guild=MagicMock(),
        client=SimpleNamespace(wom=SimpleNamespace(moderation_request=AsyncMock())),
        response=SimpleNamespace(
            defer=AsyncMock(), send_message=AsyncMock(), is_done=lambda: True
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def forbidden():
    return discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "DMs disabled")


REVIEW = {
    "nameChange": {
        "status": "pending",
        "oldName": "Old",
        "newName": "New",
        "reviewContext": {"reason": "excessive_gains"},
    },
    "data": {
        "isNewOnHiscores": True,
        "hasNegativeGains": False,
        "hoursDiff": 12,
        "ehpDiff": 1,
        "ehbDiff": 2,
        "oldStats": {"data": {"skills": {"overall": {"experience": 10, "level": 100}}}},
        "newStats": {"data": {"skills": {"overall": {"experience": 20, "level": 101}}}},
    },
}


class ModerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.i = interaction()
        self.i.guild.get_member.return_value = member(99)
        self.cog = Moderation(self.i.client)
        self.api = self.i.client.wom.moderation_request
        self.api.return_value = {"name": "Group", "newCode": "SECRET", "id": "SECRET"}
        self.log_patch = patch(
            "hom.cogs.moderation.utils.send_log_message", new_callable=AsyncMock
        )
        self.log = self.log_patch.start()
        self.addCleanup(self.log_patch.stop)

    async def test_all_commands_register_without_network_and_existing_workflows_remain(self):
        async with Bot() as bot:
            with patch.object(bot.tree, "sync", side_effect=AssertionError("No live sync")):
                for path in Path("hom/cogs").glob("[!_]*.py"):
                    await bot.load_extension(f"hom.cogs.{path.stem}")
            commands = {c.name: c for c in bot.tree.get_commands()}
            expected = {
                "namechange": ["name_change_id"],
                "verify-group": ["id", "user"],
                "reset-group-code": ["id", "user"],
                "reset-competition-code": ["id", "user"],
                "remove-from-group": ["id", "username", "requester"],
                "remove-from-competition": ["id", "username", "requester"],
                "delete-group": ["id", "requester"],
                "delete-competition": ["id", "requester"],
                "delete-player": ["username", "requester"],
                "clear-name-change-history": ["username", "requester"],
                "add-player-annotation": ["player", "annotation", "requester"],
                "remove-player-annotation": ["player", "annotation", "requester"],
                "rollback-player-snapshot-values": ["username", "metric"],
                "create-api-key": ["project", "requester"],
            }
            for name, params in expected.items():
                command = commands[name]
                self.assertEqual([p.name for p in command.parameters], params)
                self.assertTrue(command.guild_only)
                self.assertEqual(type(command.binding).__name__, "Moderation")
                for parameter in command.parameters:
                    self.assertEqual(
                        parameter.required,
                        parameter.name != "requester" or name == "create-api-key",
                    )
            self.assertIsNotNone(commands["competition"].get_command("remove_from_competitions"))
            self.assertIn("setflag", commands)
            self.assertTrue(bot.persistent_views)

    async def test_role_and_guild_check_precedes_api_and_is_private(self):
        for allowed, user, guild in (
            (True, member(), MagicMock()),
            (False, member(moderator=False), MagicMock()),
            (False, member(), None),
        ):
            i = interaction(user)
            i.guild = guild
            self.assertEqual(await self.cog.interaction_check(i), allowed)
            i.response.defer.assert_awaited_once_with(ephemeral=True)
            self.api.assert_not_awaited()
            if not allowed:
                self.assertTrue(i.followup.send.call_args.kwargs["ephemeral"])

    async def test_simple_command_routes_payloads_and_attribution(self):
        cases = [
            ("remove_from_group", (7, "A B"), "DELETE", "/groups/7/members", {"members": ["A B"]}),
            (
                "remove_from_competition",
                (7, "A B"),
                "DELETE",
                "/competitions/7/participants",
                {"participants": ["A B"]},
            ),
            ("delete_group", (7,), "DELETE", "/groups/7", {}),
            ("delete_competition", (7,), "DELETE", "/competitions/7", {}),
            ("delete_player", ("A/B ?",), "DELETE", "/players/A%2FB%20%3F", {}),
            ("clear_history", ("A B",), "POST", "/names/A%20B/clear-history", {}),
            (
                "add_annotation",
                ("A B", ANNOTATIONS[0]),
                "POST",
                "/players/A%20B/annotation",
                {"annotationType": ANNOTATIONS[0]},
            ),
            (
                "remove_annotation",
                ("A B", ANNOTATIONS[0]),
                "DELETE",
                "/players/A%20B/annotation",
                {"annotationType": ANNOTATIONS[0]},
            ),
        ]
        for name, args, method, path, payload in cases:
            with self.subTest(command=name):
                self.api.reset_mock()
                self.log.reset_mock()
                command = getattr(self.cog, name)
                await command.callback(self.cog, self.i, *args, requester=member(99))
                self.api.assert_awaited_once_with(method, path, **payload)
                self.assertIn("<@99>", self.log.call_args.args[1])
                self.assertIn("Moderator: <@1>", self.log.call_args.args[1])

    async def test_errors_are_private_and_not_logged_as_success(self):
        self.api.side_effect = WomError(404, "Player not found.")
        with self.assertRaises(WomError) as caught:
            await self.cog.delete_player.callback(self.cog, self.i, "Missing")
        self.log.assert_not_awaited()
        error = app_commands.CommandInvokeError(self.cog.delete_player, caught.exception)
        await self.cog.cog_app_command_error(self.i, error)
        self.i.followup.send.assert_awaited_once_with("Player not found.", ephemeral=True)

    async def test_verify_assigns_leader_role_and_logs(self):
        user = member(99)
        await self.cog.verify_group.callback(self.cog, self.i, 7, user)
        self.api.assert_awaited_once_with("PUT", "/groups/7/verify")
        self.i.guild.get_role.assert_called_with(Config.HOM_GROUP_LEADER_ROLE)
        self.assertIn("<@99>", self.log.call_args.args[1])

    async def test_missing_recipient_prevents_mutation(self):
        self.i.guild.get_member.return_value = None
        for command, args in (
            (self.cog.verify_group, (7, member(99))),
            (self.cog.reset_group_code, (7, member(99))),
            (self.cog.reset_competition_code, (7, member(99))),
            (self.cog.create_api_key, ("Project", member(99))),
        ):
            with self.assertRaisesRegex(WomError, "Couldn't find that user"):
                await command.callback(self.cog, self.i, *args)
        self.api.assert_not_awaited()

    async def test_role_failure_preserves_success_and_audit_log(self):
        self.i.guild.get_member.return_value.add_roles.side_effect = forbidden()
        await self.cog.verify_group.callback(self.cog, self.i, 7, member(99))
        self.api.assert_awaited_once()
        self.log.assert_awaited_once()
        self.assertIn(
            "role could not be assigned",
            self.i.edit_original_response.call_args.kwargs["embed"].description,
        )

    async def test_annotation_validation_and_optional_requester(self):
        for command in (self.cog.add_annotation, self.cog.remove_annotation):
            with self.assertRaisesRegex(WomError, "Invalid annotation"):
                await command.callback(self.cog, self.i, "A B", "invalid")
        self.api.assert_not_awaited()
        await self.cog.delete_group.callback(self.cog, self.i, 7)
        self.assertNotIn("Requested by", self.log.call_args.args[1])

    async def test_secret_commands_dm_only_and_preflight(self):
        for name, args, path, payload in (
            ("reset_group_code", (7,), "/groups/7/reset-code", {}),
            ("reset_competition_code", (7,), "/competitions/7/reset-code", {}),
            (
                "create_api_key",
                ("Project",),
                "/api-key",
                {"application": "Project", "developer": "requester"},
            ),
        ):
            with self.subTest(command=name):
                self.api.reset_mock()
                user = member(99)

                # Delivering the placeholder must happen before mutation.
                async def request(*args, **kwargs):
                    user.send.assert_awaited_once()
                    return {"id": "SECRET", "newCode": "SECRET"}

                self.api.side_effect = request
                await getattr(self.cog, name).callback(self.cog, self.i, *args, user)
                self.api.assert_awaited_once_with(
                    "POST" if name == "create_api_key" else "PUT", path, **payload
                )
                self.assertIn("SECRET", user.send.return_value.edit.call_args.kwargs["content"])
                self.assertNotIn("SECRET", str(self.log.call_args))
                self.assertNotIn(
                    "SECRET", self.i.edit_original_response.call_args.kwargs["embed"].description
                )
                self.api.reset_mock()
                user.send.side_effect = forbidden()
                with self.assertRaisesRegex(WomError, "enable Direct Messages"):
                    await getattr(self.cog, name).callback(self.cog, self.i, *args, user)
                self.api.assert_not_awaited()

    async def test_secret_api_failure_updates_dm_and_no_success_log(self):
        user = member(99)
        self.api.side_effect = WomError(404, "Not found")
        with self.assertRaises(WomError):
            await self.cog.reset_group_code.callback(self.cog, self.i, 7, user)
        self.assertIn(
            "Failed to generate", user.send.return_value.edit.call_args.kwargs["content"]
        )
        self.log.assert_not_awaited()

    async def test_secret_delivery_failure_reports_committed_action_and_logs(self):
        user = member(99)
        user.send.return_value.edit.side_effect = forbidden()
        await self.cog.reset_group_code.callback(self.cog, self.i, 7, user)
        self.api.assert_awaited_once()
        self.log.assert_awaited_once()
        self.assertIn(
            "DM delivery failed",
            self.i.edit_original_response.call_args.kwargs["embed"].description,
        )

    async def test_rollback_metric_validation_aliases_autocomplete(self):
        self.assertTrue(all(value in METRICS for value in METRIC_ALIASES.values()))
        self.assertEqual(parse_metric("COX-CM"), "chambers_of_xeric_challenge_mode")
        await self.cog.rollback.callback(self.cog, self.i, "A B", "COX-CM")
        self.api.assert_awaited_once_with(
            "POST",
            "/players/A%20B/rollback-snapshot-metric-values",
            metric="chambers_of_xeric_challenge_mode",
        )
        self.api.reset_mock()
        with self.assertRaisesRegex(WomError, "Invalid metric"):
            await self.cog.rollback.callback(self.cog, self.i, "A B", "not-a-metric")
        self.api.assert_not_awaited()
        self.assertLessEqual(len(await metric_options(self.i, "")), 25)
        self.assertIn(
            "chambers_of_xeric_challenge_mode",
            [c.value for c in await metric_options(self.i, "COX-CM")],
        )

    async def test_namechange_pending_review_and_invalid_states(self):
        self.api.return_value = REVIEW
        await self.cog.namechange.callback(self.cog, self.i, 7)
        self.api.assert_awaited_once_with("GET", "/names/7")
        args = self.i.edit_original_response.call_args.kwargs
        self.assertIn("Exp difference? `10`", args["embed"].description)
        self.assertEqual(len(args["view"].children), 2)
        args["view"].stop()
        for result, message in (
            ({"nameChange": {"status": "approved"}}, "not pending"),
            ({"nameChange": {"status": "pending"}, "data": None}, "not found"),
        ):
            self.api.return_value = result
            with self.assertRaisesRegex(WomError, message):
                await self.cog.namechange.callback(self.cog, self.i, 7)

    async def test_review_owner_role_timeout_and_duplicate_clicks(self):
        for action in ("approve", "deny"):
            view = NameChangeReview(self.i, 7, review_embed(REVIEW))
            self.assertEqual(view.timeout, 300)
            self.assertFalse(await view.interaction_check(interaction(member(99))))
            self.assertFalse(await view.interaction_check(interaction(member(1, False))))
            self.assertTrue(await view.interaction_check(self.i))
            self.api.reset_mock()
            await asyncio.gather(view.decide(self.i, action), view.decide(self.i, action))
            self.api.assert_awaited_once_with("POST", f"/names/7/{action}")
            self.assertIsNone(self.i.edit_original_response.call_args.kwargs["view"])
        view = NameChangeReview(self.i, 7, review_embed(REVIEW))
        self.api.reset_mock()
        await view.on_timeout()
        await view.decide(self.i, "approve")
        self.api.assert_not_awaited()
        view.stop()

    async def test_review_504_removes_buttons_and_warns(self):
        view = NameChangeReview(self.i, 7, review_embed(REVIEW))
        self.api.side_effect = WomError(504, "Timed out")
        await view.decide(self.i, "approve")
        self.assertIn("Check the status", view.embed.footer.text)
        self.assertIsNone(self.i.edit_original_response.call_args.kwargs["view"])

    async def test_log_failure_does_not_claim_api_failure(self):
        self.log.side_effect = forbidden()
        await self.cog.delete_group.callback(self.cog, self.i, 7)
        self.assertIn(
            "action succeeded", self.i.edit_original_response.call_args.kwargs["embed"].description
        )

    async def test_bulk_removal_retains_filter_and_legacy_client_contract(self):
        from hom.cogs.competition import Competition

        cog = Competition(self.i.client)
        self.i.client.wom.get_player_competitions = AsyncMock(
            return_value=[
                {"competitionId": 1, "competition": {"groupId": 7}},
                {"competitionId": 2, "competition": {"groupId": 8}},
                {"competitionId": 3, "competition": {"groupId": 7}},
            ]
        )
        remove = AsyncMock(
            side_effect=[(200, "OK"), (400, "None of the players given were competing")]
        )
        self.i.client.wom.remove_competition_participant = remove
        await cog.remove_from_competitions.callback(cog, self.i, "A B", group_id=7)
        self.assertEqual([c.args for c in remove.await_args_list], [(1, "A B"), (3, "A B")])
        self.api.assert_not_awaited()


class WomClientTests(unittest.IsolatedAsyncioTestCase):
    def session(self, status=200, body="{}"):
        response = SimpleNamespace(status=status, text=AsyncMock(return_value=body))
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.request.return_value = context
        return session

    async def test_authenticated_payload_and_get_no_password(self):
        session = self.session(body='{"newCode":"secret"}')
        client = WomClient(session)
        result = await client.moderation_request("PUT", "/groups/7/reset-code")
        self.assertEqual(result["newCode"], "secret")
        self.assertEqual(
            session.request.call_args.kwargs["json"],
            {"adminPassword": Config.SHARED_ADMIN_PASSWORD},
        )
        self.assertEqual(
            session.request.call_args.kwargs["headers"]["x-api-key"], Config.HOM_API_KEY
        )
        self.assertEqual(session.request.call_args.kwargs["timeout"].total, 30)
        await client.moderation_request("GET", "/names/7")
        self.assertIsNone(session.request.call_args.kwargs["json"])

    async def test_errors_non_json_empty_and_redaction(self):
        for status, body in (
            (404, '{"message":"Player not found."}'),
            (409, '{"message":"Already exists."}'),
            (500, "<html>Oops</html>"),
            (504, ""),
        ):
            with self.subTest(status=status):
                with self.assertRaises(WomError) as caught:
                    await WomClient(self.session(status, body)).moderation_request(
                        "DELETE", "/players/a"
                    )
                self.assertEqual(caught.exception.status, status)
                self.assertNotIn("html", str(caught.exception))
        self.assertEqual(
            await WomClient(self.session(204, "")).moderation_request("DELETE", "/players/a"), {}
        )
        body = json.dumps({"message": Config.SHARED_ADMIN_PASSWORD + Config.HOM_API_KEY})
        with self.assertRaises(WomError) as caught:
            await WomClient(self.session(400, body)).moderation_request("DELETE", "/players/a")
        self.assertNotIn(Config.SHARED_ADMIN_PASSWORD, str(caught.exception))
        self.assertNotIn(Config.HOM_API_KEY, str(caught.exception))

    async def test_timeout_connection_failure_no_retry(self):
        for error in (asyncio.TimeoutError(), aiohttp.ClientConnectionError()):
            session = self.session()
            session.request.side_effect = error
            with self.assertRaises(WomError) as caught:
                await WomClient(session).moderation_request("POST", "/api-key")
            self.assertEqual(caught.exception.status, 504)
            session.request.assert_called_once()
