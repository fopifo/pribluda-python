-- Приблуда: экспорт обезличенных сделок Quik -> CSV.
-- Timestamp в МИЛЛИСЕКУНДАХ через socket.gettime() для точного анализа.
local socket = require("socket")
local OUT = "C:\\Users\\Public\\MI_CODES\\Qwen_coder\\data\\quik_trades.csv"
local f = nil
local stopped = false

local function is_sell(flags)
  return (flags % 2) == 0
end

function OnInit()
  f = io.open(OUT, "a")
  if f then f:write("# started ", os.time(), "\n"); f:flush() end
end

function OnAllTrade(t)
  if f == nil then return end
  pcall(function()
    -- socket.gettime() возвращает секунды с дробной частью (мс)
    local ts_ms = math.floor(socket.gettime() * 1000)
    f:write(t.sec_code, ";", t.qty, ";", t.price, ";",
            (is_sell(t.flags) and "sell" or "buy"), ";", ts_ms, ";", t.flags, "\n")
    f:flush()
  end)
end

function OnStop()
  stopped = true
end

function main()
  while not stopped do
    sleep(100)
  end
end