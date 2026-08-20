-- Приблуда на python — рабочий мост QUIK -> Python по UDP.
-- Используем sender:sendto(data, ip, port) — как в диагностическом
-- скрипте, который у вас заработал.
-- Формат строки: SECCODE;QTY;PRICE;SIDE;TIMESTAMP_MS
-- SIDE: "buy" или "sell"
-- TIMESTAMP_MS: время в миллисекундах Unix

stopped = false
socket = require("socket")

IPAddr = "127.0.0.1"
IPPort = 3587
sender = nil

function OnInit(path)
    sender = socket.udp()
    message("Приблуда: мост QUIK -> Python запущен, порт " .. IPPort)
end

function serialize_alltrade(alltrade)
    -- Извлекаем только нужные поля и формируем строку
    local seccode = alltrade.sec_code or alltrade.sec_code or ""
    local qty = alltrade.qty or 0
    local price = alltrade.price or 0
    local flags = alltrade.flags or 0
    local side = "buy"
    if flags ~= nil and (flags % 2) == 1 then
        side = "sell"
    end
    local ts_ms = os.time() * 1000
    return string.format("%s;%d;%.2f;%s;%d", seccode, qty, price, side, ts_ms)
end

function OnAllTrade(alltrade)
    if sender == nil then
        return
    end
    local line = serialize_alltrade(alltrade)
    local ok, err = sender:sendto(line .. "\n", IPAddr, IPPort)
    if not ok then
        message("Приблуда: ошибка отправки: " .. tostring(err))
    end
end

function OnStop()
    stopped = true
end

function main()
    while not stopped do
        sleep(100)
    end
end