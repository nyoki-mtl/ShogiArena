"""Remote uv executable discovery shared by provisioning commands."""

REMOTE_UV_DISCOVERY_SCRIPT = (
    'if [ -x "$HOME/.local/bin/uv" ]; then '
    'remote_uv="$HOME/.local/bin/uv"; '
    "else remote_uv=$(command -v uv || true); fi; "
    '[ -n "$remote_uv" ] || { echo "uv executable not found" >&2; exit 69; }'
)

__all__ = ["REMOTE_UV_DISCOVERY_SCRIPT"]
