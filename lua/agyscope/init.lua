-- agyscope: Rex plugin entry point. setup(opts) registers one action that, in the
-- focused pane's window:
--   * minimizes a visible subagent panel, or restores, raises and focuses a minimized one;
--   * with no panel, opens one (focused) for the agent running in the focused pane, on
--     that pane's host;
--   * with no agent in the pane, shows a short message pane.
-- It binds no keys: bind the action yourself, e.g. rex.bind("alt+s", "agyscope").

local config = require "agyscope.config"
local layer_mod = require "agyscope.layer"
local rexapi = require "agyscope.rexapi"
local message = require "agyscope.message"

local M = {}

local function run(cfg, ctx)
  local api, layer, window, err = rexapi.tab_api(ctx, function(a)
    local view, verr = a.session.view { session_id = ctx.session_id }
    if verr ~= nil or not view then
      return nil, nil, verr
    end
    local l, w = layer_mod.find_panel(view, ctx.block_id)
    return l, w, nil
  end)

  if layer == nil then
    -- No window known: the message pane opens in the active window.
    message.show(api, ctx, nil, "Can't read this pane: " .. tostring(err or "not found"))
    return
  end

  if layer then
    local req = { session_id = ctx.session_id, layer_id = layer.layer_id }
    if layer.state == "minimized" then
      req.state, req.focus = "normal", true
    else
      req.state = "minimized"
    end
    local _, serr = api.session.set_layer_state(req)
    if serr ~= nil then
      message.show(api, ctx, window.window_id, "Can't change the panel: " .. tostring(serr))
    end
    return
  end

  local pid, perr = rexapi.agent_pid(ctx, cfg.agent_apps)
  if not pid then
    message.show(api, ctx, window.window_id, perr and ("Can't read this pane: " .. perr) or "No agent in this pane")
    return
  end

  local _, nerr = api.session.new_layer {
    session_id = ctx.session_id,
    window_id = window.window_id,
    bounds = cfg.bounds,
    focus = true,
    layout = { block = config.panel_block(cfg, pid, ctx.block_id) },
  }
  if nerr ~= nil then
    message.show(api, ctx, window.window_id, "Can't open the panel: " .. tostring(nerr))
  end
end

-- run(cfg, ctx) is the action body; exposed for scripts and manual testing.
M.run = run

-- Registers the action. Options are listed in README.md; unknown ones raise an error.
function M.setup(opts)
  local cfg = config.resolve(opts, 2)
  rex.action {
    name = cfg.action,
    title = cfg.title,
    run = function(ctx)
      run(cfg, ctx)
    end,
  }
  return cfg
end

return M
