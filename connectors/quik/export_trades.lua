-- export_trades.lua
-- Экспорт ЛЕНТЫ СДЕЛОК из Quik в CSV.
-- ВЕРСИЯ 3.20 (2026-08-23, ветка Qwen_coder).
--
-- СТОРОНА (v3.20): метод по флагам (чётность).
-- Гипотеза: нечётный флаг = продажа, чётный = покупка.
-- Основание: независимое подтверждение из обратной связи (другой проект
-- использует правило флаги % 2 == 1 -> продажа), зонд показал 97.3%
-- совпадений с предыдущим методом. Прямой сверки с Алором пока нет.
--
-- ВАЖНО: старый метод (на основе последовательных цен, v3.10) оставлен
-- в 6-м поле для сравнения и возможного отката. НЕ УДАЛЯТЬ до
-- подтверждения, что новый метод лучше через ab_compare на свежих данных.
--
-- Формат: символ;объём;цена;сторона_флаги;время;сторона_тек.метод;флаги
-- Боевой код (backend.py) читает первые 5 полей, лишние игнорирует.
--
-- ПРЕЖНИЙ МЕТОД (помечен как deprecated, v3.10):
-- В этом Quik бит направления во флагах обезличенных сделок не взводится
-- (проверено: покупка=4975/продажа=0 и наоборот), операция отсутствует.
-- Поэтому: цена ВЫШЕ прошлой сделки тикера = покупка, НИЖЕ = продажа,
-- РАВНА = наследуем прошлую сторону.
-- Этот метод даёт 87.6% совпадений с Алором (сверка 2026-08-21).

local trades_file = "C:/Users/Public/MI_CODES/Qwen_coder/data/quik_trades.csv"
local log_file    = "C:/Users/Public/MI_CODES/Qwen_coder/data/export_debug.log"

stopped = false
trades_handle = nil
buffer = {}
trade_count = 0
buy_count = 0
sell_count = 0
last_flush_ms = 0
last_counter_log_time = 0

local last_price = {}
local last_side = {}

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

-- v3.20: метод по флагам (чётность)
-- Гипотеза: нечётный флаг = продажа, чётный = покупка
function get_side_by_flags(alltrade)
    local flags = alltrade.flags or 0
    if flags % 2 == 1 then
        return "sell"
    else
        return "buy"
    end
end

-- v3.10 (deprecated): метод на основе последовательных цен
-- Оставлен для сравнения и возможного отката. НЕ УДАЛЯТЬ.
function get_side_tickrule(alltrade)
    local code = alltrade.sec_code or ""
    local p = alltrade.price or 0
    local prev = last_price[code]
    last_price[code] = p
    if prev == nil then
        local s = last_side[code] or "buy"
        last_side[code] = s
        return s
    end
    local s
    if p > prev then
        s = "buy"
    elseif p < prev then
        s = "sell"
    else
        s = last_side[code] or "buy"
    end
    last_side[code] = s
    return s
end

function OnInit()
    write_log("=== QUIK EXPORT STARTED (v3.20: flags parity side) ===")
    open_trades()
    message("Quik Export: Started (v3.20)")
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
    local flags = alltrade.flags or 0
    local ts = trade_time_ms(alltrade)

    -- v3.20: основной метод - по флагам
    local side_flags = get_side_by_flags(alltrade)
    -- v3.10 (deprecated): старый метод - для сравнения
    local side_tickrule = get_side_tickrule(alltrade)

    trade_count = trade_count + 1
    if side_flags == "buy" then buy_count = buy_count + 1 else sell_count = sell_count + 1 end
    buffer[#buffer + 1] = sec_code .. ";" .. quantity .. ";" .. price
        .. ";" .. side_flags .. ";" .. ts
        .. ";" .. side_tickrule .. ";" .. flags .. "\n"
end

function OnStop()
    stopped = true
    if trades_handle then
        trades_handle:close()
        trades_handle = nil
    end
    write_log("=== QUIK EXPORT STOPPED ===")
    message("Quik Export: Stopped")
end

function main()
    write_log("main() loop started (v3.20)")
    last_flush_ms = now_ms()

    while not stopped do
        local t_ms = now_ms()
        local now = os.time()

        if t_ms - last_flush_ms >= 200 then
            last_flush_ms = t_ms
            flush_trades()
        end

        if now - last_counter_log_time >= 60 then
            last_counter_log_time = now
            write_log("trades exported total: " .. trade_count
                .. " (buy=" .. buy_count .. ", sell=" .. sell_count .. ")")
        end
    end

    flush_trades()
    if trades_handle then
        trades_handle:close()
        trades_handle = nil
    end
    write_log("main() loop ended")
end