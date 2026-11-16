-- agyscope/layer.lua: finds the panel's floating layer in a session view.
-- Pure Lua 5.1, no Rex API (tested by tests/lua/layer_test.lua).

local config = require "agyscope.config"

local M = {}

-- The window, layer and block holding block_id in a session view, or nil.
function M.find_block(view, block_id)
  if type(view) ~= "table" or type(view.windows) ~= "table" then
    return nil
  end
  for _, w in ipairs(view.windows) do
    for _, layer in ipairs(w.layers or {}) do
      for _, b in ipairs(layer.blocks or {}) do
        if b.block_id == block_id then
          return w, layer, b
        end
      end
    end
  end
  return nil
end

-- Finds the panel for block_id in the window that holds block_id.
-- Returns:
--   layer, window   when the window has the panel (also when block_id is the panel itself)
--   false, window   when the window has no panel for block_id
--   nil             when block_id is not in the view
function M.find_panel(view, block_id)
  local window, own_layer, own_block = M.find_block(view, block_id)
  if not window then
    return nil
  end
  if own_layer.kind == "floating" and (own_block.label or ""):sub(1, #config.LABEL_PREFIX) == config.LABEL_PREFIX then
    return own_layer, window
  end
  local want = config.label(block_id)
  for _, layer in ipairs(window.layers or {}) do
    if layer.kind == "floating" then
      for _, b in ipairs(layer.blocks or {}) do
        if b.label == want then
          return layer, window
        end
      end
    end
  end
  return false, window
end

return M
