-- limits_sweep.lua
-- Отдельный скрипт ПЛАНОК (getParamEx). Запускать ВТОРЫМ скриптом в Quik.
-- ВЕРСИЯ 5 (2026-08-20, ветка Qwen_coder).
--
-- v5: ГЛАВНЫЙ ФИКС ДНЯ — в этом Quik getParamEx отдаёт param_type
-- СТРОКОЙ ("1"), а сравнение было числом (== 1) → всегда nil.
-- Теперь tonumber() и для param_type, и для param_value.
-- Это же объясняет все "Limits written: 0" в v3.x — квоты не существовало.
-- Ломтик 2/2c, кэш, атомарная перезапись data/quik_limits.csv.
-- SLICE-DBG оставлен на 3 тикерах — теперь покажет getnum=число (проверка).
-- ФАЙЛЫ: data/quik_limits.csv, data/limits_sweep.log

local limits_file = "C:/Users/Public/MI_CODES/Qwen_coder/data/quik_limits.csv"
local limits_tmp  = "C:/Users/Public/MI_CODES/Qwen_coder/data/quik_limits.tmp"
local log_file    = "C:/Users/Public/MI_CODES/Qwen_coder/data/limits_sweep.log"

local LIMITS_SLICE = 2
local CYCLE_MS = 2000

stopped = false
local all_secs = nil
local pos = 0
local cache = {}
local dbg = 0
local last_progress_sec = 0

local function now_ms()
    if getTickCount then return getTickCount() end
    return os.time() * 1000
end

local function write_log(msg)
    local f = io.open(log_file, "a")
    if f then
        f:write(os.date("%H:%M:%S") .. " - " .. msg .. "\n")
        f:close()
    end
end

-- v5: tonumber() для типа и значения (Quik отдаёт строки)
local function get_num(class, sec, param)
    local r = getParamEx(class, sec, param)
    if not r then return nil end
    local pt = tonumber(r.param_type)
    local pv = tonumber(r.param_value)
    if (pt == 1 or pt == 4) and pv then
        return pv
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

local function write_file()
    local f = io.open(limits_tmp, "w")
    if not f then return 0 end
    f:write("ticker;current_price;limit_up;limit_down;change_percent\n")
    local c = 0
    for _, line in pairs(cache) do
        f:write(line)
        c = c + 1
    end
    f:close()
    os.remove(limits_file)
    os.rename(limits_tmp, limits_file)
    return c
end

local function process_slice()
    local secs = ensure_secs()
    local n = #secs
    if n == 0 then return end

    for _ = 1, LIMITS_SLICE do
        pos = pos % n + 1
        local code = secs[pos]

        if dbg < 3 then
            local g = get_num("TQBR", code, "LAST")
            write_log("SLICE-DBG code=[" .. code .. "] getnum=" .. tostring(g))
            dbg = dbg + 1
        end

        local last = get_num("TQBR", code, "LAST")
        local prev = get_num("TQBR", code, "PREVWAPRICE")
        local price = (last and last > 0) and last or prev
        if price and price > 0 then
            local up = get_num("TQBR", code, "PRICEMAX")
            local dn = get_num("TQBR", code, "PRICEMIN")
            if up and dn then
                local ch = get_num("TQBR", code, "CHANGE")
                cache[code] = code .. ";" .. fmt(price) .. ";" .. fmt(up)
                    .. ";" .. fmt(dn) .. ";" .. fmt(ch) .. "\n"
            end
        end
    end

    local c = write_file()
    if pos <= LIMITS_SLICE then
        write_log("Limits sweep complete: " .. c .. " securities cached")
    end
end

function OnInit()
    write_log("=== LIMITS SWEEP STARTED (v5: tonumber fix) ===")
    write_log("Waiting 5 sec...")
    sleep(5000)
end

function OnStop()
    stopped = true
    write_log("=== LIMITS SWEEP STOPPED ===")
end

function main()
    write_log("main() loop started (v5)")
    local last_ms = now_ms()
    while not stopped do
        local t = now_ms()
        if t - last_ms >= CYCLE_MS then
            last_ms = t
            process_slice()
        end

        local now = os.time()
        if now - last_progress_sec >= 60 then
            last_progress_sec = now
            local c = 0
            for _ in pairs(cache) do c = c + 1 end
            write_log("progress: pos=" .. pos .. " cache=" .. c)
        end
        sleep(100)
    end
end