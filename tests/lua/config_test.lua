-- Tests for agy_subagent.config and agy_subagent.rexapi.matches.
-- Run from anywhere: rex do path/to/tests/lua/config_test.lua

local dir = debug.getinfo(1, "S").source:match("^@?(.*[/\\])") or "./"
local t = dofile(dir .. "harness.lua")(dir)
local config = require "agy_subagent.config"
local rexapi = require "agy_subagent.rexapi"

-- defaults
local cfg = config.resolve(nil)
t.check("default action", cfg.action == "agy_subagent")
t.check("default agent apps", cfg.agent_apps[1] == "agy" and cfg.agent_apps[2] == "antigravity")
t.check("default command", cfg.command[1] == "agy-subagent" and #cfg.command == 1)
t.check("default bounds", #cfg.bounds == 4 and cfg.bounds[1] == 0.6)
t.check("defaults are copies", config.resolve({}).agent_apps ~= config.OPTIONS.agent_apps[2])

-- overrides
cfg = config.resolve { agent_apps = { "myagent" }, panel = { app_dir = "~/.myagent", keymap = "vim" } }
t.check("agent apps override", cfg.agent_apps[1] == "myagent" and #cfg.agent_apps == 1)
t.check("panel app_dir", cfg.panel.app_dir == "~/.myagent")

-- errors
t.raises("unknown option", function()
  config.resolve { prefx = "ctrl+a" }
end, "unknown option 'prefx'")
t.raises("removed prefix option", function()
  config.resolve { prefix = "ctrl+b" }
end, "unknown option 'prefix'")
t.raises("wrong type", function()
  config.resolve { command = "agy-subagent" }
end, "option 'command' must be a table")
t.raises("empty apps", function()
  config.resolve { agent_apps = {} }
end, "agent_apps")
t.raises("bad bounds", function()
  config.resolve { bounds = { 0, 0, 2, 1 } }
end, "bounds")
t.raises("unknown panel setting", function()
  config.resolve { panel = { colour = "red" } }
end, "unknown panel setting 'colour'")
t.raises("pager not a list", function()
  config.resolve { panel = { pager = { cmd = "bat" } } }
end, "pager")

-- label
t.check("label", config.label("block:1") == "agy-subagent:block:1")

-- json
t.check("json list", config.json({ "bat", "--paging=always" }) == '["bat","--paging=always"]')
t.check("json escape", config.json('a"b\\c\n') == '"a\\"b\\\\c\\n"')
t.check("json object sorted", config.json({ up = { "K" }, down = { "J" } }) == '{"down":["J"],"up":["K"]}')

-- panel argv
cfg = config.resolve {
  command = { "~/bin/agy-subagent" },
  panel = {
    app_dir = "~/.gemini/x",
    keymap = "vim",
    pager = { "bat", "-p" },
    keys = { down = { "J" } },
  },
}
local argv = config.panel_argv(cfg, 42, "block:9")
t.check("launcher shell", argv[1] == "sh" and argv[2] == "-c" and argv[4] == "sh")
t.check("command after launcher", argv[5] == "~/bin/agy-subagent")
local rest = table.concat(argv, " ", 6)
t.check("pid and block", rest:find("--pid 42 --block block:9", 1, true) ~= nil, rest)
t.check(
  "panel flags",
  rest:find('--app-dir ~/.gemini/x --keymap vim --keys-json {"down":["J"]} --pager-json ["bat","-p"]', 1, true) ~= nil,
  rest
)
t.check("no flags by default", #config.panel_argv(config.resolve(), 1, "b") == 9)

-- panel block: runs through the user's shell with interactive startup files by default
t.check("default interactive", config.resolve().interactive == true)
local blk = config.panel_block(config.resolve(), 42, "block:9")
t.check("block flavor", blk.flavor == config.FLAVOR)
t.check("block label", blk.label == "agy-subagent:block:9")
t.check("block options interactive by default", blk.options.interactive == true)
t.check("block options command", blk.options.command[5] == "agy-subagent" and blk.options.command[7] == "42")
blk = config.panel_block(config.resolve { interactive = false }, 42, "block:9")
t.check("block options interactive false", blk.options.interactive == false)
t.raises("interactive not boolean", function()
  config.resolve { interactive = "yes" }
end, "option 'interactive' must be a boolean")

-- process matching
t.check("match name", rexapi.matches({ "agy" }, "agy", nil))
t.check("match path basename", rexapi.matches({ "agy" }, nil, "/usr/local/bin/agy"))
t.check("match path dir", rexapi.matches({ "myagent" }, "node", "/opt/myagent/bin/run"))
t.check("no match", not rexapi.matches({ "agy" }, "zsh", "/bin/zsh"))
t.check("no partial name match", not rexapi.matches({ "agy" }, "agyx", "/bin/agyx"))
t.check("match argv0 after self-update rename", rexapi.matches({ "agy" }, "agy.1.old", "/x/agy.1.old", "agy"))
t.check("no partial argv0 match", not rexapi.matches({ "agy" }, "zsh", "/bin/zsh", "-zsh"))

return t.finish("config")
