-- probe_params.lua
-- Приблуда: зонд того, что РЕАЛЬНО отдаёт Quik через getParamEx.
-- ВЕРСИЯ 3 (2026-08-20): добавлены PRICEMIN/PRICEMAX (планки) и
-- LASTTOPREVPRICE (изменение к вчерашнему закрытию).
-- Выводы v2: LAST/WAPRICE/PREVWAPRICE/HIGH/LOW/OPEN/CHANGE/TRADINGSTATUS
-- работают; CLOSE/CLOSEPRICE/OFFICIALCLOSE/PREVCLOSE не работают;
-- аукционные поля не работают.
-- Запуск вручную из "Расширения -> Доступные скрипты".

local log_file = "C:/Users/Public/MI_CODES/Qwen_coder/data/probe_params.log"

local PROBE_TICKERS = { "SBER", "GAZP" }
local PROBE_PARAMS = {
    "LAST", "PREVWAPRICE", "WAPRICE",
    "PRICEMAX", "PRICEMIN",
    "HIGH", "LOW", "OPEN", "CHANGE",
    "LASTTOPREVPRICE", "LASTCHANGEPRCNT",
    "TRADINGSTATUS", "OPENPERIOD", "CLOSEPERIOD",
}

stopped = false

local function probe_once()
    local lines = {}
    lines[#lines + 1] = "=== PROBE v3 " .. os.date("%H:%M:%S") .. " ==="
    lines[#lines + 1] = "isConnected = "
        .. tostring(isConnected and isConnected() or "нет функции")

    for _, t in ipairs(PROBE_TICKERS) do
        for _, p in ipairs(PROBE_PARAMS) do
            local ok, r = pcall(getParamEx, "TQBR", t, p)
            if not ok then
                lines[#lines + 1] = t .. "|" .. p .. " -> ошибка: " .. tostring(r)
            elseif r == nil then
                lines[#lines + 1] = t .. "|" .. p .. " -> nil"
            else
                lines[#lines + 1] = t .. "|" .. p
                    .. " -> type=" .. tostring(r.param_type)
                    .. " value=" .. tostring(r.param_value)
            end
        end
    end

    local f = io.open(log_file, "w")
    if f then
        f:write(table.concat(lines, "\n") .. "\n")
        f:close()
    end
end

function main()
    probe_once()
    -- v3: один прогон, чтобы не мешать основному скрипту
end

function OnStop()
    stopped = true
end