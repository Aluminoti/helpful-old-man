# HOM

The Helpful Old Man!

A discord bot used in the Wise Old Man Discord server for facilitating support tickets.

## Contributing

Commands shown here are unix compatible and may need to be altered for Windows users.

Create a new virtual environment and activate it

```bash
$ python -m venv .venv
$ source ./.venv/bin/activate
```

Install dependencies

```bash
$ pip install -r requirements.dev.txt
```

Copy the example `.env` file

```bash
$ cp .example.env .env
```

Update the values in `.env` for your development bot

Ensure the bot has the proper permissions and intents (all)

Run the bot

```bash
$ python -m hom
```

Once the bot is running, make sure to run the `!sync` command in your dev server.
This syncs the bots application commands with Discord.

## GitHub Issue Command

The bot can expose a moderator-only slash command for creating issues:

```text
/github create repository:<repo> title:<title> body:<body> image:<optional attachment>
```

It also adds a message-only right-click action:

```text
Apps > Create GitHub Issue
```

That context-menu flow opens a modal with a prefilled title of
`UserDisplayName - Suggestion` and a body that starts with the Discord message
link, followed by the message content so a moderator can edit it before
submitting. The modal starts with a required repository dropdown and also
includes an optional attachment upload field.

To enable it, set these optional values in `.env`:

```bash
HOM_GITHUB_REPOSITORIES=wise-old-man/wise-old-man,wise-old-man/wiseoldman-discord-bot,wise-old-man/wiseoldman-runelite-plugin,wise-old-man/helpful-old-man
HOM_GITHUB_APP_ID=1234567
HOM_GITHUB_PRIVATE_KEY_PATH=.secrets/hom-github-app.pem
```

If `HOM_GITHUB_REPOSITORIES` is blank, the GitHub issue commands stay disabled and
the bot will prompt you to configure it instead of falling back to defaults.

Create a private GitHub App for the repos listed in `HOM_GITHUB_REPOSITORIES`,
disable webhooks, grant repository `Issues: Read and write`, generate a private
key, and install the app on the target repos. The bot resolves the installation
automatically per repository, so you do not need to store an installation ID in
`.env`. Keep the PEM file out of git and mount it into Docker at runtime instead
of copying it into the image.

If an image attachment is supplied, the bot adds the Discord attachment URL to the
issue body and renders it inline when GitHub can display it.

## Contributing with Docker

You can also run the bot inside docker with hot reloading. It is still
recommended to have have python and the bot's dependencies installed for
compatibility with your linter.

Still make sure to copy the `.env` file and that your discord bot application
has proper perms in the developer dashboard.

If `HOM_BASE_API_URL` is set to `http://localhost:5000`, the bot will
automatically use `host.docker.internal` when it is running inside Docker so it
can still reach an API running on your host machine.

For the GitHub App private key, keep the file in `.secrets/` locally and mount
that directory into the container. In production, mount the PEM from the host or
your secret manager to the same in-container path referenced by
`HOM_GITHUB_PRIVATE_KEY_PATH`.

```sh
$ docker compose up
```

Every time you save a python file in the `hom` directory, the bot will restart.

## License

Helpful Old Man is licensed under the [MIT License]
(https://github.com/wise-old-man/helpful-old-man/blob/main/LICENSE).

## Moderation commands

All 14 WOM Discord Bot moderation commands are available in HOM. They require
`HOM_MOD_ROLE`, run only in a server, and respond privately to the moderator.
Arguments in brackets are optional; names match the source bot.

| Command | Arguments |
| --- | --- |
| `/namechange` | `name_change_id` |
| `/verify-group` | `id`, `user` |
| `/reset-group-code` | `id`, `user` |
| `/reset-competition-code` | `id`, `user` |
| `/remove-from-group` | `id`, `username`, `[requester]` |
| `/remove-from-competition` | `id`, `username`, `[requester]` |
| `/delete-group` | `id`, `[requester]` |
| `/delete-competition` | `id`, `[requester]` |
| `/delete-player` | `username`, `[requester]` |
| `/clear-name-change-history` | `username`, `[requester]` |
| `/add-player-annotation` | `player`, `annotation`, `[requester]` |
| `/remove-player-annotation` | `player`, `annotation`, `[requester]` |
| `/rollback-player-snapshot-values` | `username`, `metric` |
| `/create-api-key` | `project`, `requester` |

Name-change reviews show the hiscores checks, time/experience/efficiency differences,
levels, and review reason. Approve/Deny buttons expire after five minutes, belong to
the moderator who opened the review, and recheck their role on each click. Run the
command again after expiration or a bot restart. As in the source bot, name-change
outcomes appear on the review; the other commands write moderator logs, including
requester attribution when supplied, to `HOM_MOD_LOG_CHANNEL`.

Verification codes and API keys are delivered only by DM. The bot sends a
placeholder DM before calling WOM, so closed DMs prevent a reset or key creation.
If delivery fails after WOM succeeds, the moderator receives a warning and the
completed action is still logged without the secret. Network timeouts are never
retried automatically: check WOM's state before repeating an action.

Group verification grants `HOM_GROUP_LEADER_ROLE`. To also grant an API Consumer
role when creating keys, set the optional `HOM_API_CONSUMER_ROLE` to its Discord
role ID. Blank or `0` disables that assignment. The bot needs permission to assign
these roles, send DMs, and write to the moderator log channel. A role/logging
failure is reported separately from a successful WOM action.

Annotation choices and metric names are pinned to `@wise-old-man/utils` 4.0.22,
matching the reference bot at `148c2bb`. Metric autocomplete and the reference's
abbreviations (for example `cox-cm`, `hp`, and `rc`) are supported. Update
`hom/moderation_data.py` when adopting newer WOM metrics or annotations.

The existing ticket buttons and `/competition remove_from_competitions` bulk
command remain available. These new slash commands load with the existing cogs;
registration still uses the existing manual `!sync` workflow.

### Offline validation

After installing development dependencies, run:

```sh
python -m unittest discover -v
nox -s tests types formatting imports
```

Tests use fake credentials and mocked WOM/Discord operations, including command
registration, role restrictions, approval/denial and expiration, credential DM
failures, API errors, attribution, and the existing bulk-removal behavior. They
never connect to Discord, sync commands, or make real moderation requests.

Command behavior and metric aliases were ported from
[WOM Discord Bot](https://github.com/wise-old-man/wiseoldman-discord-bot) at
`148c2bb`; see [third-party notices](THIRD_PARTY_NOTICES.md).
