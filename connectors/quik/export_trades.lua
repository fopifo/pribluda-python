-- export_trades.lua
-- ВЕРСИЯ 3.8 (2026-08-20, ветка Qwen_coder).
-- Сделки (OnAllTrade) + ПЛАНКИ медленным опросом Quik.
-- Планки: ломтики по LIMITS_SLICE=10 тикеров за цикл 2 c (~50 вызовов
-- getParamEx за раз — столько же делал рабочий зонд), кэш, атомарная
-- перезапись data/quik_limits.csv. Полный обход ~2 минуты; планки
-- статичны внутри дня, этого достаточно.
-- Котировки (quik_quotes.csv) пишет tools/iss_quotes_sync.py — не здесь.
--
-- ФАЙЛЫ: data/quik_trades.csv, data/quik_limits.csv, data/export_debug.log

local trades_file = "C:/Users/Public/MI_CODES/Qwen_coder/data/quik_trades.csv"
local limits_file = "C:/Users/Public/MI_CODES/Qwen_coder/data/quik_limits.csv"
local limits_tmp  = "C:/Users/Public/MI_CODES/Qwen_coder/data/quik_limits.tmp"
local log_file    = "C:/Users/Public/MI_CODES/Qwen_coder/data/export_debug.log"

local LIMITS_SLICE = 10

stopped = false
trades_handle = nil
buffer = {}
trade_count = 0
last_flush_ms = 0
last_limits_ms = 0
last_counter_log_time = 0

local all_secs = nil
local limits_pos = 0
local limits_cache = {}

local function now_ms()
    if getTickCount then
        return getTickCount()
    end
    return os.time() * 1000
end

function write_log(msg)
    local f = io.open(log_file, "a")
    if f then
        f:write(os.date("%H:%M:%S") .. " - " .. msg .. "\n")
        f:close()
    end
end

function open_trades()
    trades_handle = io.open(trades_file, "a")
    if trades_handle then
        write_log("Trades file opened (append mode)")
    else
        write_log("ERROR: Cannot open trades file")
    end
end

function flush_trades()
    if trades_handle and #buffer > 0 then
        trades_handle:write(table.concat(buffer))
        trades_handle:flush()
        buffer = {}
    end
end

function trade_time_ms(alltrade)
    local dt = alltrade.datetime
    if dt and dt.year then
        local ok, sec = pcall(os.time, {
            year = dt.year, month = dt.month, day = dt.day,
            hour = dt.hour, min = dt.min, sec = dt.sec,
        })
        if ok and sec then
            return sec * 1000 + math.floor(dt.msec or 0)
        end
    end
    return os.time() * 1000
end

function get_quantity(alltrade)
    local qty = alltrade.qty
    if qty and qty > 0 then return qty end
    qty = alltrade.quantity
    if qty and qty > 0 then return qty end
    qty = alltrade.lot_quantity
    if qty and qty > 0 then return qty end
    return 0
end

function get_side(alltrade)
    local op = alltrade.operation
    if op == 1 or op == "B" or op == "b" then
        return "buy"
    end
    return "sell"
end

-- Безопасное чтение числового параметра (type 1 и 4).
local function get_num(class, sec, param)
    local r = getParamEx(class, sec, param)
    if r and (r.param_type == 1 or r.param_type == 4) and r.param_value then
        return r.param_value
    end
    return nil
end

local function fmt(v)
    if v == nil then return "" end
    return tostring(v)
end

local function ensure_secs()
    if all_secs == nil then
        all_secs = {}
        local s = getClassSecurities("TQBR")
        if s and s ~= "" then
            for code in string.gmatch(s, "([^,]+)") do
                all_secs[#all_secs + 1] = code
            end
        end
        write_log("TQBR list cached: " .. #all_secs .. " tickers")
    end
    return all_secs
end

-- Ломтик планок: 10 тикеров, кэш, атомарная перезапись файла.
local function process_limits_slice()
    local secs = ensure_secs()
    local n = #secs
    if n == 0 then return end

    for _ = 1, LIMITS_SLICE do
        limits_pos = limits_pos % n + 1
        local code = secs[limits_pos]

        local last = get_num("TQBR", code, "LAST")
        local prev = get_num("TQBR", code, "PREVWAPRICE")
        local price = (last and last > 0) and last or prev
        local up = get_num("TQBR", code, "PRICEMAX")
        local dn = get_num("TQBR", code, "PRICEMIN")
        local ch = get_num("TQBR", code, "CHANGE")

        if price and price > 0 and up and dn then
            limits_cache[code] = code .. ";" .. fmt(price) .. ";" .. fmt(up)
                .. ";" .. fmt(dn) .. ";" .. fmt(ch) .. "\n"
        end
    end

    local f = io.open(limits_tmp, "w")
    if f then
        f:write("ticker;current_price;limit_up;limit_down;change_percent\n")
        local c = 0
        for _, line in pairs(limits_cache) do
            f:write(line)
            c = c + 1
        end
        f:close()
        os.remove(limits_file)
        os.rename(limits_tmp, limits_file)
        if limits_pos <= LIMITS_SLICE then
            write_log("Limits sweep complete: " .. c .. " securities cached")
        end
    end
end

function OnInit()
    write_log("=== QUIK EXPORT STARTED (v3.8: trades + slow limits) ===")
    write_log("Waiting 5 sec for Quik to load data...")
    sleep(5000)
    open_trades()
    message("Quik Export: Started (v3.8)")
end

function OnAllTrade(alltrade)
    if alltrade.class_code ~= "TQBR" then
        return
    end
    if not trades_handle then
        open_trades()
        if not trades_handle then return end
    end

    local quantity = get_quantity(alltrade)
    if quantity <= 0 then
        return
    end

    local sec_code = alltrade.sec_code or ""
    local price = alltrade.price or 0
    local side = get_side(alltrade)
    local ts = trade_time_ms(alltrade)

    trade_count = trade_count + 1
    buffer[#buffer + 1] = sec_code .. ";" .. quantity .. ";" .. price
        .. ";" .. side .. ";" .. ts .. "\n"
end

function OnStop()
    stopped = true
    flush_trades()
    if trades_handle then
        trades_handle:close()
        trades_handle = nil
    end
    write_log("=== QUIK EXPORT STOPPED ===")
    message("Quik Export: Stopped")
end

function main()
    write_log("main() loop started (v3.8)")
    last_flush_ms = now_ms()
    last_limits_ms = now_ms()

    while not stopped do
        local t_ms = now_ms()
        local now = os.time()

        if t_ms - last_flush_ms >= 200 then
            last_flush_ms = t_ms
            flush_trades()
        end

        if t_ms - last_limits_ms >= 2000 then
            last_limits_ms = t_ms
            process_limits_slice()
        end

        if now - last_counter_log_time >= 60 then
            last_counter_log_time = now
            write_log("trades exported total: " .. trade_count)
        end

        sleep(100)
    end

    flush_trades()
    if trades_handle then
        trades_handle:close()
        trades_handle = nil
    end
    write_log("main() loop ended")
end