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
# With AGE_RECIPIENT set, the archive is encrypted to that age public key and
# written as .dump.age; the plaintext only ever exists in a private temporary
# directory on the local disk. Restore with:
#   age -d -i KEY_FILE pkm_db-....dump.age | pg_restore -d pkm_db --no-owner
#
# Environment (all optional):
#   BACKUP_DIR      where archives go (or pass it as the first argument)
#   RETENTION_DAYS  days of archives to keep (default 30)
#   DB_CONTAINER    postgres container (default pkm-db)
#   DB_USER, DB_NAME  (default pkm, pkm_db)
#   ENV_FILE        where to look for NTFY_* settings (default: ../.env)
#   AGE_RECIPIENT   age public key (age1...) to encrypt archives to
#   AGE             age binary (default: age on PATH, else ~/.local/bin/age)
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
AGE_RECIPIENT=${AGE_RECIPIENT:-}
AGE=${AGE:-$(command -v age || echo "$HOME/.local/bin/age")}

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

if [ -n "$AGE_RECIPIENT" ]; then
    [ -x "$AGE" ] || fail "AGE_RECIPIENT is set but age is not installed ($AGE)"
fi

stamp=$(date +%Y%m%d-%H%M%S)
final="$BACKUP_DIR/${DB_NAME}-${stamp}.dump"
if [ -n "$AGE_RECIPIENT" ]; then
    final="$final.age"
fi
partial="$final.partial"
work=$(mktemp -d) # private (0700), on the local disk
dump="$work/${DB_NAME}.dump"
trap 'rm -rf "$work" "$partial"' EXIT

docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc >"$dump" 2>"$work/errors" ||
    fail "pg_dump of $DB_NAME failed: $(tail -c 300 "$work/errors")"

[ -s "$dump" ] || fail "pg_dump produced an empty file"
docker exec -i "$DB_CONTAINER" pg_restore --list <"$dump" >/dev/null ||
    fail "pg_restore cannot read the new archive"

if [ -n "$AGE_RECIPIENT" ]; then
    "$AGE" -r "$AGE_RECIPIENT" -o "$partial" "$dump" 2>"$work/errors" ||
        fail "age encryption failed: $(tail -c 300 "$work/errors")"
else
    cp "$dump" "$partial"
fi
mv "$partial" "$final"
echo "$(date '+%F %T') wrote $final ($(du -h "$final" | cut -f1))"

# Prune only after a good dump, so a run of failures never empties the directory.
find "$BACKUP_DIR" -maxdepth 1 \( -name "${DB_NAME}-*.dump" -o -name "${DB_NAME}-*.dump.age" \) \
    -mtime "+$RETENTION_DAYS" -print -delete
