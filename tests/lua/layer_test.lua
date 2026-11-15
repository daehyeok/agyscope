-- Tests for agy_subagent.layer and agy_subagent.message.request.
-- Run from anywhere: rex do path/to/tests/lua/layer_test.lua

local dir = debug.getinfo(1, "S").source:match("^@?(.*[/\\])") or "./"
local t = dofile(dir .. "harness.lua")(dir)
local layer = require "agy_subagent.layer"
local message = require "agy_subagent.message"

local panel_b2 = {
  layer_id = "f2",
  kind = "floating",
  state = "normal",
  blocks = { { block_id = "p2", label = "agy-subagent:b2" } },
}
local other_float = { layer_id = "f3", kind = "floating", blocks = { { block_id = "x3", label = "agy-subagent:b9" } } }
local view = {
  windows = {
    {
      window_id = "w1",
      layers = { { layer_id = "t1", kind = "tiled", blocks = { { block_id = "b1", label = "shell" } } } },
    },
    {
      window_id = "w2",
      layers = {
        { layer_id = "t2", kind = "tiled", blocks = { { block_id = "b2" }, { block_id = "b3" } } },
        panel_b2,
        other_float,
      },
    },
  },
}

local l, w = layer.find_panel(view, "b2")
t.check("panel for its pane", l == panel_b2 and w.window_id == "w2")
l, w = layer.find_panel(view, "b3")
t.check("other pane in same window has none", l == false and w.window_id == "w2")
l = layer.find_panel(view, "b1")
t.check("window without panel", l == false)
t.check("unknown block", layer.find_panel(view, "zz") == nil)
t.check("nil view", layer.find_panel(nil, "b1") == nil)
l = layer.find_panel(view, "p2")
t.check("focused panel finds itself", l == panel_b2)
l = layer.find_panel(view, "x3")
t.check("any panel block toggles its own layer", l == other_float)

local req = message.request({ session_id = "s", block_id = "b1" }, "w1", "No agent in this pane", "/opt/rex")
t.check("message window", req.window_id == "w1" and req.focus == true)
local cmd = req.layout.block.options.command
t.check(
  "message argv",
  cmd[1] == "sh" and cmd[5] == "No agent in this pane" and cmd[6] == "b1" and cmd[7] == "/opt/rex"
)

return t.finish("layer")
