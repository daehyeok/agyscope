-- Manual check: runs the agyscope action for a given pane, as Rex would.
--   AGY_BLOCK=<block> AGY_SESSION=<session> [AGY_SERVER=<host>] rex do tests/lua/manual_toggle.lua [apps...]
-- Opens, minimizes or restores that pane's panel, or shows the "no agent" message.

local dir = debug.getinfo(1, "S").source:match("^@?(.*[/\\])") or "./"
local root = dir .. "../../"
package.path = root .. "lua/?.lua;" .. root .. "lua/?/init.lua;" .. package.path
local config = require "agyscope.config"
local plugin = require "agyscope"

local apps = os.getenv("AGY_APPS")
local opts = {}
if apps then
  opts.agent_apps = {}
  for a in apps:gmatch("[^,]+") do
    opts.agent_apps[#opts.agent_apps + 1] = a
  end
end
if os.getenv("AGY_APP_DIR") then
  opts.panel = { app_dir = os.getenv("AGY_APP_DIR") }
end
if os.getenv("AGY_COMMAND") then
  opts.command = { os.getenv("AGY_COMMAND") }
end

local ctx = {
  session_id = os.getenv("AGY_SESSION"),
  block_id = os.getenv("AGY_BLOCK"),
  server = os.getenv("AGY_SERVER"),
}
-- A script run from outside the session (e.g. over ssh) must attach before session calls.
if rex.session and type(rex.session.attach) == "function" then
  rex.session.attach { session_id = ctx.session_id }
end
plugin.run(config.resolve(opts), ctx)
return "ran"
