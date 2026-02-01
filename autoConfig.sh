#!/bin/bash

# Configuration script for NamelessNameSanitizerBot
# This script creates a .env file from .env.example with user prompts

set -e

ENV_EXAMPLE=".env.example"
ENV_FILE=".env"

# Check if .env.example exists
if [ ! -f "$ENV_EXAMPLE" ]; then
	echo "Error: $ENV_EXAMPLE not found in the current directory"
	exit 1
fi

# Warn if .env already exists
if [ -f "$ENV_FILE" ]; then
	read -p ".env already exists. Do you want to overwrite it? (y/N): " -n 1 -r
	echo
	if [[ ! $REPLY =~ ^[Yy]$ ]]; then
		echo "Aborted."
		exit 0
	fi
fi

echo "================================================"
echo "NamelessNameSanitizerBot Automatic Configuration"
echo "================================================"
echo ""

# Function to prompt with default value
prompt_with_default() {
	local prompt_text="$1"
	local default_value="$2"
	local input_value
	if [ -z "$default_value" ]; then
		read -r -p "$prompt_text: " input_value
	else
		read -r -p "$prompt_text [$default_value]: " input_value
		input_value="${input_value:-$default_value}"
	fi
	echo "$input_value"
}

# Function to prompt without default value
prompt_basic() {
	local prompt_text="$1"
	local input_value
	read -r -p "$prompt_text: " input_value
	echo "$input_value"
}

# Function to validate Discord user ID format
validate_owner_id() {
	local owner_id="$1"

	if [[ -z "$owner_id" ]]; then
		echo "Error: OWNER_ID is missing." >&2
		return 1
	fi

	if [[ "$owner_id" =~ [[:space:]] ]]; then
		echo "Error: OWNER_ID contains whitespace." >&2
		return 1
	fi

	if ! [[ "$owner_id" =~ ^[0-9]+$ ]]; then
		echo "Error: OWNER_ID must be numeric (a Discord user ID)." >&2
		return 1
	fi

	if [[ ${#owner_id} -lt 17 ]] || [[ ${#owner_id} -gt 20 ]]; then
		echo "Error: OWNER_ID appears invalid (should be 17-20 digits)." >&2
		return 1
	fi

	return 0
}

# Function to validate Discord token format
validate_discord_token() {
	local token="$1"

	if [[ -z "$token" ]]; then
		echo "Error: DISCORD_TOKEN is missing." >&2
		return 1
	fi

	if [[ "$token" =~ [[:space:]] ]]; then
		echo "Error: DISCORD_TOKEN contains whitespace; remove spaces or line breaks." >&2
		return 1
	fi

	local dot_count
	dot_count=$(grep -o "\." <<<"$token" | wc -l | tr -d '[:space:]')
	if [[ "${dot_count:-0}" -ne 2 ]]; then
		echo "Error: DISCORD_TOKEN appears malformed (expected three segments separated by '.'). Re-copy the token." >&2
		return 1
	fi

	if ! [[ "$token" =~ ^[A-Za-z0-9._-]+$ ]]; then
		echo "Error: DISCORD_TOKEN contains unexpected characters. Re-copy the token without special characters." >&2
		return 1
	fi

	local seg0="${token%%.*}"
	while ((${#seg0} % 4)); do
		seg0="${seg0}="
	done
	if ! printf '%s' "$seg0" | base64 --decode >/dev/null 2>&1; then
		echo "Warning: DISCORD_TOKEN first segment did not decode via base64; continuing. If login fails, regenerate the token." >&2
	fi

	return 0
}

# Collect user inputs
echo "Discord Configuration:"
while :; do
	DISCORD_TOKEN=$(prompt_basic "Discord bot token (keep this secret)")
	if validate_discord_token "$DISCORD_TOKEN"; then
		break
	fi
	echo "Validation failed. Please enter the Discord bot token again."
done
while :; do
	OWNER_ID=$(prompt_basic "Your Discord user ID (you will be the bot owner)")
	if validate_owner_id "$OWNER_ID"; then
		break
	fi
	echo "Validation failed. Please enter a valid Discord user ID again."
done
APPLICATION_ID=$(prompt_with_default "Application ID from Discord Developer Portal (for invite links and the API)" "0")

echo
echo "Database Configuration:"
# Auto-generate secure username and password
echo "Generating secure PostgreSQL credentials..."
LEN=32
BYTES=$((((LEN + 3) / 4) * 3))
POSTGRES_USER=$(openssl rand -base64 "$BYTES" | tr '+/' '-_' | tr -d '\n=' | head -c "$LEN")
LEN=128
BYTES=$((((LEN + 3) / 4) * 3))
POSTGRES_PASSWORD=$(openssl rand -base64 "$BYTES" | tr '+/' '-_' | tr -d '\n=' | head -c "$LEN")
echo "PostgreSQL credentials generated"

echo
echo "Default Policy Configuration for new servers:"
CHECK_LENGTH=$(prompt_with_default "Number of leading grapheme clusters to sanitize (0=disable)" "0")
MIN_NICK_LENGTH=$(prompt_with_default "Minimum allowed nickname length" "3")
MAX_NICK_LENGTH=$(prompt_with_default "Maximum allowed nickname length" "32")
PRESERVE_SPACES=$(prompt_with_default "Keep spaces in sanitized names (true/false)" "true")
COOLDOWN_SECONDS=$(prompt_with_default "Wait time between sanitizations (seconds)" "30")
SANITIZE_EMOJI=$(prompt_with_default "Remove emoji from names (true/false)" "true")
ENFORCE_BOTS=$(prompt_with_default "Apply sanitization to bot accounts (true/false)" "false")
FALLBACK_MODE=$(prompt_with_default "Fallback mode (default/numbered/random)" "default")
FALLBACK_LABEL=$(prompt_with_default "Fallback name for sanitized accounts" "Illegal Name")

echo
echo "Advanced Configuration (if you are unsure use the default):"
COOLDOWN_TTL_SEC=$(prompt_with_default "How long to remember user cooldowns (seconds)" "864000")
SWEEP_INTERVAL_SEC=$(prompt_with_default "Check for name changes every N seconds" "60")
DM_OWNER_ON_GUILD_EVENTS=$(prompt_with_default "Send DM when bot joins/leaves a server (true/false)" "true")
DM_OWNER_ON_ERRORS=$(prompt_with_default "Send DM when errors trigger DnD status (true/false)" "true")
COMMAND_COOLDOWN_SECONDS=$(prompt_with_default "Cooldown per user for slash commands (seconds)" "2")
OWNER_DESTRUCTIVE_COOLDOWN_SECONDS=$(prompt_with_default "Cooldown for dangerous owner commands (seconds)" "5")
DEBUG_MODE=$(prompt_with_default "Enable detailed output of bot actions for debugging (true/false)" "false")
NNSB_TELEMETRY_OPTOUT=$(prompt_with_default "Send anonymous non-identifiable usage stats to help improve bot (0=yes, 1=no)" "0")
LOG_LEVEL=$(prompt_with_default "Log message detail level: DEBUG/INFO/WARNING/ERROR" "INFO")

# Create .env file
cat >"$ENV_FILE" <<EOF
# This software is licensed under NNCL v1.3 see LICENSE.md for more info
# Environment configuration for Discord Sanitizer Bot.
# Generated by autoConfig.sh - do not commit to version control.

# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! #
# BY USING/HOSTING THIS BOT YOU AGREE TO THE FOLLOWING DOCUMENTS: #
# https://nnsb.namelessnanashi.dev/TermsOfService                 #
# https://nnsb.namelessnanashi.dev/PrivacyPolicy                  #
# https://nnsb.namelessnanashi.dev/license                        #
# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! #

# Discord bot token - obtain from the Developer Portal and keep secret.
DISCORD_TOKEN=$DISCORD_TOKEN

# Numeric Discord user ID considered the bot owner (authorizes owner-only commands).
OWNER_ID=$OWNER_ID

# PostgreSQL container credentials (used by the db service).
# Ensure they are secure and not guessable in production environments.
# ONCE SET, DO NOT CHANGE THESE VALUES UNLESS YOU KNOW WHAT YOU ARE DOING.
POSTGRES_DB=bot # Don't change this
POSTGRES_USER=$POSTGRES_USER # Don't change this
POSTGRES_PASSWORD=$POSTGRES_PASSWORD # Don't change this

# Application (client) ID used to generate invite links. Optional but recommended.
APPLICATION_ID=$APPLICATION_ID

# Policy defaults (can be overridden per-guild via slash commands).
CHECK_LENGTH=$CHECK_LENGTH
MIN_NICK_LENGTH=$MIN_NICK_LENGTH
MAX_NICK_LENGTH=$MAX_NICK_LENGTH
PRESERVE_SPACES=$PRESERVE_SPACES
COOLDOWN_SECONDS=$COOLDOWN_SECONDS
SANITIZE_EMOJI=$SANITIZE_EMOJI
ENFORCE_BOTS=$ENFORCE_BOTS
FALLBACK_MODE=$FALLBACK_MODE
FALLBACK_LABEL="$FALLBACK_LABEL"

# Retention for per-user cooldown entries; older entries are purged automatically.
COOLDOWN_TTL_SEC=$COOLDOWN_TTL_SEC

# Background sweep configuration.
SWEEP_INTERVAL_SEC=$SWEEP_INTERVAL_SEC

# Whether the bot should DM the owner when it joins or leaves a guild.
# Set to false to disable owner notifications for these events.
DM_OWNER_ON_GUILD_EVENTS=$DM_OWNER_ON_GUILD_EVENTS

# Whether the bot should DM the owner when errors occur that trigger DnD (red) status.
# This includes database failures, status file issues, and runtime errors.
# Set to false to disable error notifications.
DM_OWNER_ON_ERRORS=$DM_OWNER_ON_ERRORS

# Global per-user command cooldown (in seconds). Set to 0 to disable.
# Owner and bot admins bypass this cooldown.
COMMAND_COOLDOWN_SECONDS=$COMMAND_COOLDOWN_SECONDS

# Cooldown for destructive owner commands (in seconds).
OWNER_DESTRUCTIVE_COOLDOWN_SECONDS=$OWNER_DESTRUCTIVE_COOLDOWN_SECONDS

# Debug mode - Enable detailed output of bot actions for debugging.
# Set to true to output more guild info to terminal; false (default) logs only counts for privacy.
DEBUG_MODE=$DEBUG_MODE

# Telemetry (privacy-respecting census)
# You can see the data here: https://telemetry.namelessnanashi.dev/
# Enabled by default; disable by setting opt-out to true.
# If you dont mind I would apprecieate self-hosters allowing this data collection
# to help improve the bot service for everyone.
# Preferred flag to disable the census entirely (1/true/yes/on):
NNSB_TELEMETRY_OPTOUT=$NNSB_TELEMETRY_OPTOUT
# Alternative opt-out variable (also supported):
# TELEMETRY_OPTOUT=$NNSB_TELEMETRY_OPTOUT

# Log verbosity for the process (DEBUG, INFO, WARNING, ERROR).
LOG_LEVEL=$LOG_LEVEL
EOF

echo
echo "==================================="
echo "Configuration complete!"
echo "==================================="
echo ".env file has been created with your chosen settings."
echo
echo "Next steps:"
echo "1. Review the .env file: cat .env"
echo "2. Start the services: docker compose up -d --build"
echo "3. Check logs: docker compose logs -f bot"
echo
