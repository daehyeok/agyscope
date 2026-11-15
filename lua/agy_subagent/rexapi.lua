-- agy_subagent/rexapi.lua: the Rex calls the action needs. Runs in the Rex config
-- (client side). A tab may belong to a remote host: each call is tried on the local
-- server first and then on the tab's server (ctx.server), re-attaching once if needed.

local M = {}

M.TERMINAL = "com.superlogical.terminal"

local function file_exists(path)
  local f = io.open(path, "r")
  if f then
    f:close()
    return true
  end
  return false
end

-- Returns the path of the client's rex CLI: $REX_BIN_DIR/rex, then the macOS
-- app bundle, then plain "rex" from PATH.
function M.rex_cli(env_fn, exists_fn)
  local getenv = env_fn or os.getenv
  local exists = exists_fn or file_exists

  local bin_dir = getenv("REX_BIN_DIR")
  if bin_dir and bin_dir ~= "" then
    local p = bin_dir:match("/$") and (bin_dir .. "rex") or (bin_dir .. "/rex")
    if exists(p) then
      return p
    end
  end

  local home = getenv("HOME")
  if home and home ~= "" then
    local mac_path = home
      .. "/Library/Application Support/com.superlogical.rex/RexServer/Versions/Current/Rex Server.app/Contents/MacOS/rex"
    if exists(mac_path) then
      return mac_path
    end
  end

  return "rex"
end

-- Attaches the calling stream to a session. Calls made outside a key press
-- start without one. Wrapped in pcall so failures do not unwind uncaught.
local function attach(api, session_id)
  if session_id and api and api.session and type(api.session.attach) == "function" then
    pcall(api.session.attach, { session_id = session_id })
  end
end

-- Calls a terminal method on the focused block, on whichever server owns it.
function M.terminal_call(ctx, method, extra)
  local args = { block_id = ctx.block_id, session_id = ctx.session_id }
  for k, v in pairs(extra or {}) do
    args[k] = v
  end
  local r, err = rex.block.call(M.TERMINAL, method, args)
  if r == nil and tostring(err):find("not attached", 1, true) then
    attach(rex, ctx.session_id)
    r, err = rex.block.call(M.TERMINAL, method, args)
  end
  if r ~= nil or not ctx.server then
    return r, err
  end
  local ok, srv = pcall(rex.server, ctx.server)
  if not ok then
    return nil, srv
  end
  r, err = srv.block.call(M.TERMINAL, method, args)
  if r ~= nil then
    return r, err
  end
  attach(srv, ctx.session_id)
  return srv.block.call(M.TERMINAL, method, args)
end

-- Runs probe(api) with the local API, then with the tab's server when the result is nil.
-- Returns api, result, extra, err: the API that answered and probe's results.
function M.tab_api(ctx, probe)
  local api = rex
  local res, extra, err = probe(api)
  if res == nil and tostring(err):find("not attached", 1, true) then
    attach(rex, ctx.session_id)
    res, extra, err = probe(api)
  end
  if res == nil and ctx.server then
    local ok, srv = pcall(rex.server, ctx.server)
    if not ok then
      return rex, nil, nil, srv
    end
    api = srv
    res, extra, err = probe(api)
    if res == nil then
      attach(srv, ctx.session_id)
      res, extra, err = probe(api)
    end
  end
  return api, res, extra, err
end

local function basename(path)
  return (tostring(path or ""):match("([^/]+)$")) or ""
end

-- Pure: whether a process name, path or argv0 matches one of the agent names. argv0
-- still names the agent after a self-update renamed the running binary (agy.123.old).
function M.matches(apps, name, path, argv0)
  for _, app in ipairs(apps) do
    if name and (name == app or basename(name) == app) then
      return true
    end
    if path and (basename(path) == app or path:find("/" .. app .. "/", 1, true)) then
      return true
    end
    if argv0 and basename(argv0) == app then
      return true
    end
  end
  return false
end

-- The pid of the agent running in the focused pane, or nil (and an error string when
-- the terminal could not be read at all).
function M.agent_pid(ctx, apps)
  local st, serr = M.terminal_call(ctx, "program_status")
  for _, r in ipairs((st and st.records) or {}) do
    if r.owner and r.owner.pid and M.matches(apps, r.app) then
      return r.owner.pid
    end
  end
  local p, perr = M.terminal_call(ctx, "process")
  local fg = p and p.foreground
  if fg and fg.pid and M.matches(apps, fg.name, fg.path, fg.argv0) then
    return fg.pid
  end
  if st == nil and p == nil then
    return nil, tostring(perr or serr)
  end
  return nil
end

return M
