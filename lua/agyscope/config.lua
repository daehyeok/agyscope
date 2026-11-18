-- agyscope/config.lua: setup options, their defaults and validation, and the argv
-- that starts the panel on the pane's host. Pure Lua 5.1, no Rex API (tested by
-- tests/lua/config_test.lua).

local M = {}

M.LABEL_PREFIX = "agyscope:"
M.FLAVOR = "com.superlogical.terminal.shell"

-- Option name -> { type, default }. Defaults suit the public Antigravity CLI.
M.OPTIONS = {
  action = { "string", "agyscope" },
  title = { "string", "Show/Hide Agent Subagent Panel" },
  agent_apps = { "table", { "agy", "antigravity" } },
  command = { "table", { "agyscope" } },
  bounds = { "table", { 0.6, 0.04, 0.38, 0.42 } },
  panel = { "table", {} },
  interactive = { "boolean", true },
}

-- Panel settings passed as flags; the panel validates their contents itself.
M.PANEL = {
  app_dir = "string",
  keymap = "string",
  keys = "table",
  pager = "table",
  live = "boolean",
  main_rows = "boolean",
}

local function copy(v)
  if type(v) ~= "table" then
    return v
  end
  local out = {}
  for k, x in pairs(v) do
    out[k] = copy(x)
  end
  return out
end

local function is_string_list(t)
  if type(t) ~= "table" or #t == 0 then
    return false
  end
  for k, v in pairs(t) do
    if type(k) ~= "number" or type(v) ~= "string" or v == "" then
      return false
    end
  end
  return true
end

local function fail(msg, level)
  error("agyscope: " .. msg, (level or 1) + 1)
end

-- Validated settings from user options (nil allowed). Raises an error naming the bad option.
-- level is passed to error() so messages point at the caller of setup().
function M.resolve(opts, level)
  level = (level or 1) + 1
  opts = opts or {}
  if type(opts) ~= "table" then
    fail("setup expects a table of options", level)
  end
  for k, v in pairs(opts) do
    local spec = M.OPTIONS[k]
    if not spec then
      fail(string.format("unknown option '%s'", tostring(k)), level)
    end
    if type(v) ~= spec[1] then
      fail(string.format("option '%s' must be a %s, got %s", k, spec[1], type(v)), level)
    end
  end
  local cfg = {}
  for k, spec in pairs(M.OPTIONS) do
    if opts[k] ~= nil then
      cfg[k] = copy(opts[k])
    else
      cfg[k] = copy(spec[2])
    end
  end
  if cfg.action == "" then
    fail("option 'action' must not be empty", level)
  end
  if not is_string_list(cfg.agent_apps) then
    fail("option 'agent_apps' must be a list of names", level)
  end
  if not is_string_list(cfg.command) then
    fail("option 'command' must be a list of strings", level)
  end
  if #cfg.bounds ~= 4 then
    fail("option 'bounds' must be { x, y, w, h }", level)
  end
  for _, n in ipairs(cfg.bounds) do
    if type(n) ~= "number" or n < 0 or n > 1 then
      fail("option 'bounds' values must be numbers between 0 and 1", level)
    end
  end
  for k, v in pairs(cfg.panel) do
    local want = M.PANEL[k]
    if not want then
      fail(string.format("unknown panel setting '%s'", tostring(k)), level)
    end
    if type(v) ~= want then
      fail(string.format("panel setting '%s' must be a %s, got %s", k, want, type(v)), level)
    end
  end
  if cfg.panel.pager ~= nil and not is_string_list(cfg.panel.pager) then
    fail("panel setting 'pager' must be a list of strings", level)
  end
  return cfg
end

function M.label(block_id)
  return M.LABEL_PREFIX .. tostring(block_id)
end

-- Minimal JSON encoder for strings, numbers, booleans, lists and string-keyed tables.
local function encode(v)
  local t = type(v)
  if t == "string" then
    return '"'
      .. v:gsub('[%c"\\]', function(c)
        local map = { ['"'] = '\\"', ["\\"] = "\\\\", ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t" }
        return map[c] or string.format("\\u%04x", c:byte())
      end)
      .. '"'
  elseif t == "number" or t == "boolean" then
    return tostring(v)
  elseif t == "table" then
    if #v > 0 or next(v) == nil then
      local parts = {}
      for _, x in ipairs(v) do
        parts[#parts + 1] = encode(x)
      end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    local keys = {}
    for k in pairs(v) do
      keys[#keys + 1] = tostring(k)
    end
    table.sort(keys)
    local parts = {}
    for _, k in ipairs(keys) do
      parts[#parts + 1] = encode(k) .. ":" .. encode(v[k])
    end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return "null"
end
M.json = encode

-- Panel flags for the panel settings, one argv element per value (no shell quoting).
function M.panel_flags(panel)
  local out = {}
  if panel.app_dir then
    out[#out + 1] = "--app-dir"
    out[#out + 1] = panel.app_dir
  end
  if panel.keymap then
    out[#out + 1] = "--keymap"
    out[#out + 1] = panel.keymap
  end
  if panel.keys then
    out[#out + 1] = "--keys-json"
    out[#out + 1] = encode(panel.keys)
  end
  if panel.pager then
    out[#out + 1] = "--pager-json"
    out[#out + 1] = encode(panel.pager)
  end
  if panel.live ~= nil then
    out[#out + 1] = panel.live and "--live" or "--no-live"
  end
  if panel.main_rows ~= nil then
    out[#out + 1] = panel.main_rows and "--main-rows" or "--no-main-rows"
  end
  return out
end

-- Runs on the pane's host: expands a leading ~/ in the command, looks it up on PATH plus
-- ~/.local/bin, and says so when it is missing.
M.LAUNCH = [[
c=$1; shift
case $c in "~/"*) c=$HOME/${c#"~/"} ;; esac
PATH="$HOME/.local/bin:$PATH"
if ! command -v "$c" >/dev/null 2>&1; then
  printf '\n  %s not found on this host\n' "${c##*/}"
  sleep 2
  exit 127
fi
exec "$c" "$@"
]]

-- argv for the panel layer: the launcher, the configured command, then args.
function M.launcher(command, args)
  local argv = { "sh", "-c", M.LAUNCH, "sh" }
  for _, a in ipairs(command) do
    argv[#argv + 1] = a
  end
  for _, a in ipairs(args or {}) do
    argv[#argv + 1] = a
  end
  return argv
end

-- argv to open the panel for an agent process in block_id.
function M.panel_argv(cfg, pid, block_id)
  local args = { "--pid", tostring(pid), "--rex-block", tostring(block_id) }
  for _, f in ipairs(M.panel_flags(cfg.panel)) do
    args[#args + 1] = f
  end
  return M.launcher(cfg.command, args)
end

-- Block layout for the panel layer. interactive makes Rex run the command through the
-- user's shell with its interactive startup files (~/.zshrc), so the panel sees the same
-- PATH as the user's terminals.
function M.panel_block(cfg, pid, block_id)
  return {
    flavor = M.FLAVOR,
    label = M.label(block_id),
    options = { command = M.panel_argv(cfg, pid, block_id), interactive = cfg.interactive },
  }
end

return M
