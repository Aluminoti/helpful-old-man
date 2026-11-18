"""Offline test configuration; never load credentials from a local .env."""
import os
from unittest.mock import patch

for key in (
    "HOM_SUPPORT_CHANNEL",
    "HOM_TICKET_CATEGORY",
    "HOM_MOD_LOG_CHANNEL",
    "HOM_PATREON_CHANNEL",
    "HOM_QUESTIONS_CHANNEL",
    "HOM_FLAG_CHANNEL",
    "HOM_MOD_ROLE",
    "HOM_GROUP_LEADER_ROLE",
    "HOM_API_CONSUMER_ROLE",
):
    os.environ[key] = "123"
os.environ.update(
    HOM_DISCORD_TOKEN="test-token",
    SHARED_ADMIN_PASSWORD="test-password",
    HOM_BASE_API_URL="https://wom.invalid/v2",
    HOM_BASE_WEBSITE_URL="https://wom.invalid",
    HOM_API_KEY="test-api-key",
    HOM_GITHUB_REPOSITORIES="",
)
with patch("dotenv.load_dotenv"):
    from hom.config import Config  # noqa: F401
