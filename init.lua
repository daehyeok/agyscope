-- rex-agy-subagent: loader for `require("plugins.rex-agy-subagent")`.
-- Adds this directory's lua/ to package.path and returns the agyscope module,
-- so `require("plugins.rex-agy-subagent").setup{}` works from a Rex config.

local src = debug.getinfo(1, "S").source
local dir = src:match("^@?(.*[/\\])") or "./"
local lua_dir = dir .. "lua/"
if not package.path:find(lua_dir, 1, true) then
  package.path = lua_dir .. "?.lua;" .. lua_dir .. "?/init.lua;" .. package.path
end

return require "agyscope"
