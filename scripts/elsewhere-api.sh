#!/usr/bin/env bash
set +x
set -euo pipefail
export LC_ALL=C

readonly BASE_URL="https://elsewhere.news/api/v1"
readonly VERSION_URL="https://elsewhere.news/.well-known/elsewhere-skill.json"
readonly VERSION_MAX_BYTES=1024

die() {
  printf '%s\n' "$1" >&2
  exit "${2:-1}"
}

check_version() {
  [[ "$#" -eq 0 ]] ||
    die "ELSEWHERE_VERSION=invalid_request" 2

  local curl_version curl_version_line curl_major curl_minor
  local response curl_status size status_tail status type_tail content_type
  local body body_bytes manifest_re major minor patch bundle

  # The version request is anonymous even when the host injected a key.
  unset ELSEWHERE_KEY AUTH_HEADER CURL_HOME SSLKEYLOGFILE

  set +e
  curl_version="$(command curl --disable --version 2>/dev/null)"
  curl_status="$?"
  set -e
  [[ "$curl_status" -eq 0 ]] ||
    die "ELSEWHERE_VERSION=unsupported_curl" 14

  curl_version_line="${curl_version%%$'\n'*}"
  [[ "$curl_version_line" =~ ^curl[[:space:]]+(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})([[:space:]]|$) ]] ||
    die "ELSEWHERE_VERSION=unsupported_curl" 14
  curl_major="${BASH_REMATCH[1]}"
  curl_minor="${BASH_REMATCH[2]}"
  if
    ((10#$curl_major < 8)) ||
      ((10#$curl_major == 8 && 10#$curl_minor < 4))
  then
    die "ELSEWHERE_VERSION=unsupported_curl" 14
  fi

  set +e
  response="$(
    command curl \
      --disable \
      --silent \
      --globoff \
      --request GET \
      --proto '=https' \
      --proto-redir '=https' \
      --max-redirs 0 \
      --tlsv1.2 \
      --connect-timeout 3 \
      --max-time 5 \
      --max-filesize "$VERSION_MAX_BYTES" \
      --user-agent '' \
      --header 'Accept: application/json' \
      --header 'Authorization:' \
      --header 'Cookie:' \
      --write-out $'\nELSEWHERE_VERSION_CONTENT_TYPE=%{content_type}\nELSEWHERE_VERSION_HTTP_STATUS=%{http_code}\nELSEWHERE_VERSION_SIZE=%{size_download}' \
      --url "$VERSION_URL"
  )"
  curl_status="$?"
  set -e

  [[ "$curl_status" -eq 0 ]] ||
    die "ELSEWHERE_VERSION=unavailable" 14

  if
    [[ "$response" != *$'\nELSEWHERE_VERSION_CONTENT_TYPE='* ]] ||
      [[ "$response" != *$'\nELSEWHERE_VERSION_HTTP_STATUS='* ]] ||
      [[ "$response" != *$'\nELSEWHERE_VERSION_SIZE='* ]]
  then
    die "ELSEWHERE_VERSION=invalid_response" 14
  fi

  size="${response##*$'\nELSEWHERE_VERSION_SIZE='}"
  status_tail="${response##*$'\nELSEWHERE_VERSION_HTTP_STATUS='}"
  status="${status_tail%%$'\n'*}"
  type_tail="${response##*$'\nELSEWHERE_VERSION_CONTENT_TYPE='}"
  content_type="${type_tail%%$'\n'*}"
  body="${response%%$'\nELSEWHERE_VERSION_CONTENT_TYPE='*}"

  [[ "$status" == "200" && "$content_type" == "application/json" ]] ||
    die "ELSEWHERE_VERSION=invalid_response" 14
  [[ "$size" =~ ^(0|[1-9][0-9]{0,3})$ ]] ||
    die "ELSEWHERE_VERSION=invalid_response" 14

  body_bytes="${#body}"
  ((body_bytes == 10#$size && body_bytes <= VERSION_MAX_BYTES)) ||
    die "ELSEWHERE_VERSION=invalid_response" 14

  # The committed static JSON has one final LF; reject any other whitespace.
  if [[ "$body" == *$'\n' ]]; then
    body="${body%$'\n'}"
  fi

  manifest_re='^\{"schema":1,"name":"elsewhere-news","version":"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})","bundle-version":(0|[1-9][0-9]{0,8})\}$'
  [[ "$body" =~ $manifest_re ]] ||
    die "ELSEWHERE_VERSION=invalid_manifest" 14

  major="${BASH_REMATCH[1]}"
  minor="${BASH_REMATCH[2]}"
  patch="${BASH_REMATCH[3]}"
  bundle="${BASH_REMATCH[4]}"

  # Reconstruct canonical output instead of reflecting remote bytes.
  printf '{"schema":1,"name":"elsewhere-news","version":"%s.%s.%s","bundle-version":%s}\n' \
    "$major" "$minor" "$patch" "$bundle"
}

if [[ "${1:-}" == "check-version" ]]; then
  shift
  check_version "$@"
  exit 0
fi

[[ "${ELSEWHERE_KEY:-}" =~ ^els_live_[A-Za-z0-9_-]{43}$ ]] ||
  die "ELSEWHERE_AUTH=missing_or_invalid: bind a personal key as ELSEWHERE_KEY" 11

[[ "$#" -ge 1 ]] || die "Usage: elsewhere-api.sh /allowed-route [name=value ...]" 2
PATH_PART="$1"
shift
[[ "${#PATH_PART}" -le 256 ]] ||
  die "ELSEWHERE_REQUEST=invalid: route is too long" 2

if
  [[ "$PATH_PART" =~ ^/(search/chunks|entities/(find|search)|relation-keys|topics|personas|me/(context|content-views|annotations|sessions|topics|whats-new))$ ]] ||
    [[ "$PATH_PART" =~ ^/entities/[A-Za-z0-9_-]+/(card|edges)$ ]] ||
    [[ "$PATH_PART" =~ ^/content/(article|podcast)/[A-Za-z0-9_-]+$ ]] ||
    [[ "$PATH_PART" =~ ^/(topics|personas)/[A-Za-z0-9_-]+$ ]] ||
    [[ "$PATH_PART" =~ ^/me/sessions/[A-Za-z0-9_-]+$ ]]
then
  :
else
  die "ELSEWHERE_REQUEST=invalid: route is not on the read-only allowlist" 2
fi

[[ "$#" -le 16 ]] ||
  die "ELSEWHERE_REQUEST=invalid: too many query arguments" 2

CURL_ARGS=(
  --disable
  --silent
  --show-error
  --globoff
  --get
  --proto '=https'
  --proto-redir '=https'
  --max-redirs 0
  --tlsv1.2
  --connect-timeout 10
  --max-time 45
  --max-filesize 8388608
  --include
  --header @-
  --write-out '\nELSEWHERE_CONTENT_TYPE=%{content_type}\nELSEWHERE_HTTP_STATUS=%{http_code}\n'
)

TOTAL_QUERY_BYTES=0
for ARG in "$@"; do
  [[ "${#ARG}" -le 4096 ]] ||
    die "ELSEWHERE_REQUEST=invalid: query argument is too long" 2
  [[ ! "$ARG" =~ [[:cntrl:]] ]] ||
    die "ELSEWHERE_REQUEST=invalid: query arguments contain control characters" 2
  [[ "$ARG" =~ ^[A-Za-z][A-Za-z0-9_]*= ]] ||
    die "ELSEWHERE_REQUEST=invalid: query arguments must be name=value" 2
  ((TOTAL_QUERY_BYTES += ${#ARG}))
  [[ "$TOTAL_QUERY_BYTES" -le 16384 ]] ||
    die "ELSEWHERE_REQUEST=invalid: query arguments are too large" 2
  CURL_ARGS+=(--data-urlencode "$ARG")
done
CURL_ARGS+=(--url "$BASE_URL$PATH_PART")

AUTH_HEADER="Authorization: Bearer $ELSEWHERE_KEY"
unset ELSEWHERE_KEY CURL_HOME SSLKEYLOGFILE

set +e
RESPONSE="$(command curl "${CURL_ARGS[@]}" <<< "$AUTH_HEADER")"
CURL_STATUS="$?"
set -e
unset AUTH_HEADER

if [[ "$CURL_STATUS" -ne 0 ]]; then
  die "ELSEWHERE_API=transport_error: curl exit $CURL_STATUS" 14
fi

STATUS="${RESPONSE##*$'\nELSEWHERE_HTTP_STATUS='}"
[[ "$STATUS" =~ ^[0-9]{3}$ ]] ||
  die "ELSEWHERE_API=invalid_response_status" 14

TYPE_TAIL="${RESPONSE##*$'\nELSEWHERE_CONTENT_TYPE='}"
CONTENT_TYPE="${TYPE_TAIL%%$'\n'*}"
case "$CONTENT_TYPE" in
  application/json* | text/markdown*) ;;
  *) die "ELSEWHERE_API=unexpected_content_type" 14 ;;
esac

printf '%s\n' "$RESPONSE"

case "$STATUS" in
  2??) exit 0 ;;
  401) die "ELSEWHERE_AUTH=unauthorized" 12 ;;
  429) die "ELSEWHERE_API=rate_or_quota_limited" 13 ;;
  *) die "ELSEWHERE_API=http_error" 14 ;;
esac
