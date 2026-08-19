-- export_trades.lua
-- Экспорт сделок и планок из Quik в CSV.
-- ВЕРСИЯ 2.1 (2026-08-19, ветка Qwen_coder).
-- Исправлено относительно v1:
-- 1) Сторона: в Quik alltrade.operation — ЧИСЛО (1=buy, 2=sell),
--    а не "B"/"S". Раньше все сделки писались как sell.
--    Для совместимости принимаем и число, и строку.
-- 2) Время: берём ВРЕМЯ СДЕЛКИ с миллисекундами (alltrade.datetime + .msec),
--    а не os.time() в момент колбэка (секундная точность).
-- 3) Не открываем/закрываем файл на каждой сделке: буфер в памяти,
--    сброс раз в ~200 мс из main() — не забиваем очередь колбэков Quik.
-- 4) Планки пишем во временный файл и переименовываем —
--    читатель больше не поймает "рваный" файл на половине записи.
-- 5) Раз в 60 секунд пишем в лог счётчик сделок — видно, живой ли поток.
-- ВЕРСИЯ 2.1: сброс буфера по getTickCount() (настенные мс),
--    а не по os.clock() (процессорное время).

local trades_file = "C:/Users/Public/MI_CODES/Qwen_coder/data/quik_trades.csv"
local limits_file = "C:/Users/Public/MI_CODES/Qwen_coder/data/quik_limits.csv"
local limits_tmp  = "C:/Users/Public/MI_CODES/Qwen_coder/data/quik_limits.tmp"
local log_file    = "C:/Users/Public/MI_CODES/Qwen_coder/data/export_debug.log"

stopped = false
trades_handle = nil
buffer = {}
trade_count = 0
last_flush_ms = 0
last_limits_time = 0
last_counter_log_time = 0

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

-- Время СДЕЛКИ в мс: дата/время из alltrade.datetime + миллисекунды.
-- Если datetime нет (старый Quik) — откат на os.time()*1000 (секунды).
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

function OnInit()
    local f = io.open(log_file, "w")
    if f then f:close() end

    write_log("=== QUIK EXPORT STARTED (v2.1) ===")
    write_log("Trades file: " .. trades_file)
    write_log("Limits file: " .. limits_file)

    open_trades()

    local lf = io.open(limits_file, "w")
    if lf then
        lf:write("ticker;current_price;limit_up;limit_down;change_percent\n")
        lf:close()
        write_log("Limits file created OK")
    else
        write_log("ERROR: Cannot create limits file")
    end

    message("Quik Export: Started (v2.1)")
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

function write_limits()
    local f = io.open(limits_tmp, "w")
    if not f then
        write_log("ERROR: Cannot open limits tmp file")
        return
    end
    f:write("ticker;current_price;limit_up;limit_down;change_percent\n")

    local secs = getClassSecurities("TQBR")
    local count = 0
    if secs and secs ~= "" then
        for sec_code in string.gmatch(secs, "([^,]+)") do
            local last_res = getParamEx("TQBR", sec_code, "LAST")
            if last_res and last_res.param_type == 1 and last_res.param_value > 0 then
                local price = last_res.param_value

                local min_res = getParamEx("TQBR", sec_code, "PRICEMIN")
                local max_res = getParamEx("TQBR", sec_code, "PRICEMAX")
                local limit_down = (min_res and min_res.param_type == 1) and min_res.param_value or 0
                local limit_up = (max_res and max_res.param_type == 1) and max_res.param_value or 0

                local change_res = getParamEx("TQBR", sec_code, "CHANGE")
                local change = (change_res and change_res.param_type == 1) and change_res.param_value or 0

                f:write(sec_code .. ";" .. price .. ";" .. limit_up .. ";" .. limit_down .. ";" .. change .. "\n")
                count = count + 1
            end
        end
    else
        write_log("ERROR: getClassSecurities returned empty or nil")
    end
    f:close()

    -- Атомарная замена: читатель не поймает половину файла.
    os.remove(limits_file)
    local ok = os.rename(limits_tmp, limits_file)
    if not ok then
        write_log("ERROR: Cannot rename limits tmp -> final")
    end
    write_log("Limits written: " .. count .. " securities")
end

function main()
    write_log("main() loop started (v2.1)")
    last_limits_time = os.time()
    last_flush_ms = now_ms()

    while not stopped do
        local t_ms = now_ms()
        local now = os.time()

        -- Сброс буфера сделок раз в ~200 мс
        if t_ms - last_flush_ms >= 200 then
            last_flush_ms = t_ms
            flush_trades()
        end

        -- Планки раз в 5 секунд
        if now - last_limits_time >= 5 then
            last_limits_time = now
            write_limits()
        end

        -- Счётчик потока раз в 60 секунд (диагностика потерь)
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