-- agyscope/message.lua: shows a short message in a small floating pane on the
-- focused pane's host. The pane prints the text, closes after about 1 second or on
-- any key, and gives focus back to the source pane. No desktop notifications.

local config = require "agyscope.config"
local rexapi = require "agyscope.rexapi"

local M = {}

-- POSIX sh: no bash read -t. stty/dd wait up to 1 s for one key.
M.SCRIPT = [[
printf '\n  %s\n' "$1"
old=$(stty -g 2>/dev/null)
stty -icanon -echo min 0 time 10 2>/dev/null
dd bs=1 count=1 >/dev/null 2>&1
[ -n "$old" ] && stty "$old" 2>/dev/null
r="${REX_BIN_DIR:+$REX_BIN_DIR/}rex"
command -v "$r" >/dev/null 2>&1 || r=$3
[ -n "$2" ] && "$r" focus "$2" >/dev/null 2>&1
exit 0
]]

M.BOUNDS = { 0.25, 0.42, 0.5, 0.12 }

-- Pure: the session.new_layer request for a message about source_block.
function M.request(ctx, window_id, text, rex_cli)
  local cli = rex_cli or rexapi.rex_cli()
  return {
    session_id = ctx.session_id,
    window_id = window_id,
    bounds = M.BOUNDS,
    focus = true,
    layout = {
      block = {
        flavor = config.FLAVOR,
        label = "agyscope-message",
        options = { command = { "sh", "-c", M.SCRIPT, "sh", text, ctx.block_id, cli } },
      },
    },
  }
end

-- Shows text for ctx's pane using api (the server that owns the tab), falling back to
-- stderr when no pane can be opened.
function M.show(api, ctx, window_id, text, rex_cli)
  local _, err = api.session.new_layer(M.request(ctx, window_id, text, rex_cli))
  if err ~= nil then
    io.stderr:write("[agyscope] " .. text .. "\n")
  end
end

return M
