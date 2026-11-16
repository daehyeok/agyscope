-- Tests for agyscope.rexapi.
-- Run from anywhere: rex do path/to/tests/lua/rexapi_test.lua

local dir = debug.getinfo(1, "S").source:match("^@?(.*[/\\])") or "./"
local t = dofile(dir .. "harness.lua")(dir)
local rexapi = require "agyscope.rexapi"

-- rex_cli tests
local env_bin = { REX_BIN_DIR = "/custom/bin", HOME = "/Users/test" }
local function mock_env_bin(k)
  return env_bin[k]
end
local function mock_exists_bin(p)
  return p == "/custom/bin/rex"
end
t.check(
  "rex_cli: REX_BIN_DIR wins when file exists",
  rexapi.rex_cli(mock_env_bin, mock_exists_bin) == "/custom/bin/rex"
)

local mac_rex =
  "/Users/test/Library/Application Support/com.superlogical.rex/RexServer/Versions/Current/Rex Server.app/Contents/MacOS/rex"
local env_mac = { HOME = "/Users/test" }
local function mock_env_mac(k)
  return env_mac[k]
end
local function mock_exists_mac(p)
  return p == mac_rex
end
t.check("rex_cli: macOS app used when REX_BIN_DIR absent", rexapi.rex_cli(mock_env_mac, mock_exists_mac) == mac_rex)

local function mock_exists_none(_)
  return false
end
t.check("rex_cli: default rex when neither exists", rexapi.rex_cli(mock_env_mac, mock_exists_none) == "rex")
t.check("rex_cli: default call returns string", type(rexapi.rex_cli()) == "string")

-- terminal_call: local attach on "session not attached"
local local_attach_calls = {}
local local_call_count = 0
rex = {
  block = {
    call = function(ext, method, args)
      local_call_count = local_call_count + 1
      if local_call_count == 1 then
        return nil, "session not attached"
      else
        return { success = true }, nil
      end
    end,
  },
  session = {
    attach = function(req)
      table.insert(local_attach_calls, req)
    end,
  },
}
local ctx_local = { session_id = "s1", block_id = "b1" }
local res, err = rexapi.terminal_call(ctx_local, "method1")
t.check(
  "terminal_call: local attach called with session_id",
  #local_attach_calls == 1 and local_attach_calls[1].session_id == "s1"
)
t.check("terminal_call: local succeeds on retry", res and res.success == true and err == nil)

-- terminal_call: remote attach when first srv.block.call returns nil, even if attach throws
local remote_attach_called = false
local remote_call_count = 0
local srv = {
  block = {
    call = function(ext, method, args)
      remote_call_count = remote_call_count + 1
      if remote_call_count == 1 then
        return nil, "not found"
      else
        return { remote = true }, nil
      end
    end,
  },
  session = {
    attach = function(req)
      remote_attach_called = true
      error("attach failed")
    end,
  },
}
rex = {
  block = {
    call = function(ext, method, args)
      return nil, "not found"
    end,
  },
  server = function(name)
    if name == "ssh://h" then
      return srv
    end
    error("unknown server: " .. tostring(name))
  end,
}
local ctx_remote = { session_id = "s1", block_id = "b1", server = "ssh://h" }
local rres, rerr = rexapi.terminal_call(ctx_remote, "method1")
t.check("terminal_call: remote attach called even if it throws", remote_attach_called == true)
t.check("terminal_call: remote succeeds on retry", rres and rres.remote == true and rerr == nil)

-- terminal_call: controller ruling - throwing rex.server returns nil, srv
rex.server = function(name)
  error("server error: " .. tostring(name))
end
local ctx_throwing = { session_id = "s1", block_id = "b1", server = "bad://host" }
local tres, terr = rexapi.terminal_call(ctx_throwing, "method1")
t.check(
  "terminal_call: throwing rex.server caught",
  tres == nil and tostring(terr):find("server error", 1, true) ~= nil
)

-- tab_api: local attach on "not attached" and retry
local tab_local_attach_calls = {}
local probe_call_count = 0
rex = {
  session = {
    attach = function(req)
      table.insert(tab_local_attach_calls, req)
    end,
  },
}
local ctx_tab = { session_id = "s2" }
local probe = function(api)
  probe_call_count = probe_call_count + 1
  if probe_call_count == 1 then
    return nil, nil, "not attached"
  else
    return "found_view", "extra_info", nil
  end
end
local papi, pres, pextra, perr = rexapi.tab_api(ctx_tab, probe)
t.check(
  "tab_api: local attach called with session_id",
  #tab_local_attach_calls == 1 and tab_local_attach_calls[1].session_id == "s2"
)
t.check(
  "tab_api: local succeeds on retry",
  papi == rex and pres == "found_view" and pextra == "extra_info" and perr == nil
)

-- tab_api: remote attach srv when first probe(srv) returns nil, nil, "not attached"
local tab_remote_attach_calls = {}
local remote_probe_count = 0
local srv_tab = {
  session = {
    attach = function(req)
      table.insert(tab_remote_attach_calls, req)
    end,
  },
}
rex = {
  server = function(name)
    if name == "ssh://h2" then
      return srv_tab
    end
    error("unknown server: " .. tostring(name))
  end,
}
local probe_remote = function(api)
  if api == rex then
    return nil, nil, "no local"
  elseif api == srv_tab then
    remote_probe_count = remote_probe_count + 1
    if remote_probe_count == 1 then
      return nil, nil, "not attached"
    else
      return "remote_view", "remote_extra", nil
    end
  end
  return nil
end
local ctx_tab_remote = { session_id = "s2", server = "ssh://h2" }
local tapi, tres2, textra2, terr2 = rexapi.tab_api(ctx_tab_remote, probe_remote)
t.check("tab_api: remote attaches srv", #tab_remote_attach_calls == 1 and tab_remote_attach_calls[1].session_id == "s2")
t.check(
  "tab_api: remote returns srv, res, extra, nil on retry",
  tapi == srv_tab and tres2 == "remote_view" and textra2 == "remote_extra" and terr2 == nil
)

-- tab_api: controller ruling - throwing rex.server returns rex, nil, nil, srv
rex.server = function(name)
  error("cannot reach " .. tostring(name))
end
local throwing_probe = function(api)
  return nil, nil, "local failed"
end
local ctx_bad_server = { session_id = "s3", server = "ssh://bad" }
local tapi_bad, tres_bad, textra_bad, terr_bad = rexapi.tab_api(ctx_bad_server, throwing_probe)
t.check(
  "tab_api: throwing rex.server returns rex, nil, nil, srv",
  tapi_bad == rex and tres_bad == nil and textra_bad == nil and tostring(terr_bad):find("cannot reach", 1, true) ~= nil
)

-- matches: process name, path, and renamed-binary argv0
t.check("matches: process name", rexapi.matches({ "agy" }, "agy", nil, nil))
t.check("matches: process path basename", rexapi.matches({ "agy" }, nil, "/usr/local/bin/agy", nil))
t.check("matches: process path dir", rexapi.matches({ "antigravity" }, nil, "/opt/antigravity/bin/run", nil))
t.check("matches: renamed-binary argv0", rexapi.matches({ "agy" }, "agy.123.old", "/usr/bin/agy.123.old", "agy"))
t.check("matches: no match", not rexapi.matches({ "agy" }, "bash", "/bin/bash", "bash"))

return t.finish("rexapi")
