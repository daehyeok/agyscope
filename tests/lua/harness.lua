-- Minimal check harness for rex do tests. Usage:
--   local t = dofile(<dir> .. "harness.lua")(<dir>)   -- sets package.path for the plugin
--   t.check(name, ok, detail) ... return t.finish("config")

return function(dir)
  local root = dir .. "../../"
  package.path = root .. "lua/?.lua;" .. root .. "lua/?/init.lua;" .. package.path
  local failures, passes = {}, 0
  local t = {}
  function t.check(name, ok, detail)
    if ok then
      passes = passes + 1
    else
      failures[#failures + 1] = name .. (detail ~= nil and (": " .. tostring(detail)) or "")
    end
  end
  function t.raises(name, fn, pattern)
    local ok, err = pcall(fn)
    t.check(name, not ok and tostring(err):find(pattern, 1, true) ~= nil, err)
  end
  function t.finish(what)
    for _, f in ipairs(failures) do
      print("FAIL: " .. f)
    end
    print(string.format("%s: %d passed, %d failed", what, passes, #failures))
    if #failures > 0 then
      error(#failures .. " " .. what .. " check(s) failed", 0)
    end
    return "ok"
  end
  return t
end
