-- probe_datasource_ticks.lua
-- Приблуда: зонд сравнения источников данных для стороны сделки.
-- ВЕРСИЯ 1 (2026-08-23): параллельно с OnAllTrade подписывается на
-- CreateDataSource(INTERVAL_TICK) для 3-5 тикеров.
-- Сравнивает стороны:
--   1) tick-rule из OnAllTrade (текущий боевой)
--   2) tick-rule из CreateDataSource (гипотеза: агрегирует иначе)
--   3) flags % 2 (гипотеза из DU_glass_analyzer: нечётный = sell)
-- Запуск вручную из "Расширения → Доступные скрипты" (параллельно с боевым).

local log_file = "C:/Users/Public/MI_CODES/Qwen_coder/data/probe_datasource.csv"
local PROBE_TICKERS = {"SBER", "GAZP", "LKOH"}
local PROBE_DURATION_SEC = 300  -- 5 минут сбора данных

local datasource = {}  -- ticker -> DataSource object
local last_price_ds = {}  -- ticker -> last price from DataSource
local last_side_ds = {}  -- ticker -> last side from DataSource

local last_price_ot = {}  -- ticker -> last price from OnAllTrade
local last_side_ot = {}  -- ticker -> last side from OnAllTrade

local trades_count = 0
local mismatches_count = 0

stopped = false

local function write_header()
    local f = io.open(log_file, "w")
    if f then
        f:write("timestamp_ms,ticker,qty,price,side_onalltrade,side_datasource,flags,flags_parity\n")
        f:close()
    end
end

local function append_trade(ts_ms, ticker, qty, price, side_ot, side_ds, flags, flags_parity)
    local f = io.open(log_file, "a")
    if f then
        f:write(string.format("%d,%s,%d,%.2f,%s,%s,%d,%s\n",
            ts_ms, ticker, qty, price, side_ot, side_ds, flags, flags_parity))
        f:close()
    end
end

local function get_side_tickrule(price, last_price, last_side)
    if last_price == nil then
        return last_side or "buy"
    end
    if price > last_price then
        return "buy"
    elseif price < last_price then
        return "sell"
    else
        return last_side or "buy"
    end
end

local function trade_time_ms(alltrade)
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

function OnInit()
    write_header()
    message("Probe DataSource: Started (5 min)")
    
    -- Подписываемся на CreateDataSource для каждого тикера
    for _, ticker in ipairs(PROBE_TICKERS) do
        local ds = CreateDataSource("TQBR", ticker, INTERVAL_TICK)
        if ds then
            datasource[ticker] = ds
            ds:SetUpdateCallback(function(idx)
                -- Callback вызывается при появлении нового тика
                -- Но мы не используем его для записи, только для обновления last_price_ds
                local p = ds:C(idx)
                if p then
                    last_price_ds[ticker] = p
                    last_side_ds[ticker] = get_side_tickrule(p, last_price_ds[ticker], last_side_ds[ticker])
                end
            end)
            message("Probe: subscribed to " .. ticker)
        else
            message("Probe: FAILED to subscribe to " .. ticker)
        end
    end
end

function OnAllTrade(alltrade)
    if alltrade.class_code ~= "TQBR" then
        return
    end
    
    local ticker = alltrade.sec_code or ""
    -- Проверяем, что это один из наших probe-тикер
    local is_probe = false
    for _, t in ipairs(PROBE_TICKERS) do
        if t == ticker then
            is_probe = true
            break
        end
    end
    if not is_probe then
        return
    end
    
    local qty = alltrade.qty or 0
    if qty <= 0 then
        return
    end
    
    local price = alltrade.price or 0
    local flags = alltrade.flags or 0
    local ts_ms = trade_time_ms(alltrade)
    
    -- Сторона из OnAllTrade (tick-rule, как в боевом)
    local side_ot = get_side_tickrule(price, last_price_ot[ticker], last_side_ot[ticker])
    last_price_ot[ticker] = price
    last_side_ot[ticker] = side_ot
    
    -- Сторона из DataSource (tick-rule)
    local side_ds = last_side_ds[ticker] or "unknown"
    
    -- Сторона из flags (чётность)
    local flags_parity = (flags % 2 == 1) and "sell" or "buy"
    
    -- Записываем
    append_trade(ts_ms, ticker, qty, price, side_ot, side_ds, flags, flags_parity)
    
    trades_count = trades_count + 1
    
    -- Считаем расхождения
    if side_ot ~= side_ds then
        mismatches_count = mismatches_count + 1
    end
    
    -- Логируем каждые 100 сделок
    if trades_count % 100 == 0 then
        local pct = (mismatches_count / trades_count) * 100
        message(string.format("Probe: %d trades, %d mismatches (%.1f%%)", 
            trades_count, mismatches_count, pct))
    end
end

function OnStop()
    stopped = true
    message(string.format("Probe: Stopped. Total: %d trades, %d mismatches", 
        trades_count, mismatches_count))
end

function main()
    local start_time = os.time()
    
    while not stopped do
        local elapsed = os.time() - start_time
        if elapsed >= PROBE_DURATION_SEC then
            message("Probe: Duration reached, stopping")
            stopped = true
            break
        end
        sleep(1000)
    end
    
    message(string.format("Probe: Final stats: %d trades, %d mismatches (%.1f%%)", 
        trades_count, mismatches_count, 
        trades_count > 0 and (mismatches_count / trades_count) * 100 or 0))
end