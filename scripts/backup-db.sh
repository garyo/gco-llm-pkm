#!/usr/bin/env bash
# Nightly logical backup of the PKM database, for a host crontab on docker-server.
#
#   backup-db.sh [BACKUP_DIR]
#
# Writes a pg_dump custom-format archive (restorable with pg_restore) named
# pkm_db-YYYYmmdd-HHMMSS.dump, checks that pg_restore can read it, then deletes
# archives older than RETENTION_DAYS. On any failure it pushes an ntfy alert,
# using NTFY_* from the environment or, failing that, from the project's .env.
#
# Environment (all optional):
#   BACKUP_DIR      where archives go (or pass it as the first argument)
#   RETENTION_DAYS  days of archives to keep (default 30)
#   DB_CONTAINER    postgres container (default pkm-db)
#   DB_USER, DB_NAME  (default pkm, pkm_db)
#   ENV_FILE        where to look for NTFY_* settings (default: ../.env)
#
# BACKUP_DIR must already exist: if it is an NFS mount that has dropped, we
# want a loud failure, not a dump quietly written to the local disk under the
# empty mount point.

set -euo pipefail

BACKUP_DIR=${1:-${BACKUP_DIR:-}}
RETENTION_DAYS=${RETENTION_DAYS:-30}
DB_CONTAINER=${DB_CONTAINER:-pkm-db}
DB_USER=${DB_USER:-pkm}
DB_NAME=${DB_NAME:-pkm_db}
ENV_FILE=${ENV_FILE:-"$(dirname "$(readlink -f "$0")")/../.env"}

# Read one NTFY_* setting from the environment, else from ENV_FILE. Only these
# keys are read; the rest of .env is never sourced.
ntfy_setting() {
    local name=$1
    if [ -n "${!name:-}" ]; then
        printf '%s' "${!name}"
    elif [ -r "$ENV_FILE" ]; then
        sed -n "s/^${name}=//p" "$ENV_FILE" | tail -n 1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
    fi
}

# Quote a value for a curl config file.
curl_quote() {
    local value=${1//\\/\\\\}
    printf '"%s"' "${value//\"/\\\"}"
}

notify_failure() {
    local message=$1
    local server topic user password token
    server=$(ntfy_setting NTFY_SERVER)
    topic=$(ntfy_setting NTFY_TOPIC)
    if [ -z "$topic" ]; then
        echo "ntfy not configured (no NTFY_TOPIC); not pushing the failure" >&2
        return
    fi
    user=$(ntfy_setting NTFY_USER)
    password=$(ntfy_setting NTFY_PASS)
    token=$(ntfy_setting NTFY_TOKEN)

    # Credentials go to curl on stdin (-K -), never on its command line.
    {
        if [ -n "$user" ]; then
            printf 'user = %s\n' "$(curl_quote "$user:$password")"
        elif [ -n "$token" ]; then
            printf 'header = %s\n' "$(curl_quote "Authorization: Bearer $token")"
        fi
    } | curl -fsS --max-time 15 -K - \
        -H "Title: PKM database backup failed" -H "Tags: warning" -H "Priority: high" \
        --data-binary "$message" "${server:-https://ntfy.sh}/$topic" >/dev/null ||
        echo "ntfy push failed too" >&2
}

fail() {
    echo "$(date '+%F %T') backup FAILED: $1" >&2
    notify_failure "$(hostname): $1"
    exit 1
}

[ -n "$BACKUP_DIR" ] || fail "no backup directory given (argument or BACKUP_DIR)"
[ -d "$BACKUP_DIR" ] || fail "backup directory $BACKUP_DIR does not exist (NAS not mounted?)"
[ -w "$BACKUP_DIR" ] || fail "backup directory $BACKUP_DIR is not writable"

stamp=$(date +%Y%m%d-%H%M%S)
final="$BACKUP_DIR/${DB_NAME}-${stamp}.dump"
partial="$final.partial"
errors=$(mktemp)
trap 'rm -f "$partial" "$errors"' EXIT

docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc >"$partial" 2>"$errors" ||
    fail "pg_dump of $DB_NAME failed: $(tail -c 300 "$errors")"

[ -s "$partial" ] || fail "pg_dump produced an empty file"
docker exec -i "$DB_CONTAINER" pg_restore --list <"$partial" >/dev/null ||
    fail "pg_restore cannot read the new archive $final"

mv "$partial" "$final"
echo "$(date '+%F %T') wrote $final ($(du -h "$final" | cut -f1))"

# Prune only after a good dump, so a run of failures never empties the directory.
find "$BACKUP_DIR" -maxdepth 1 -name "${DB_NAME}-*.dump" -mtime "+$RETENTION_DAYS" -print -delete
