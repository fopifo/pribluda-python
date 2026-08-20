-- Приблуда на python — диагностический мост QUIK -> Python.
-- Не часть рабочей интеграции, только чтобы увидеть РЕАЛЬНЫЕ поля,
-- которые Quik присылает в OnAllTrade, прежде чем строить постоянный
-- код на предположениях о названиях полей.
--
-- ВАЖНО: используем sender:sendto(data, ip, port), а НЕ
-- setpeername()+send() — в окружении QUIK связка setpeername/send
-- иногда даёт ошибку "calling 'send' on bad self (udp{connected}
-- expected)", потому что сокет остаётся неподключённым, даже если
-- setpeername отработал без видимой ошибки. sendto работает у любого
-- UDP-сокета независимо от состояния "подключён" — это стандартный
-- обход именно этой проблемы.

stopped = false
socket = require("socket")

IPAddr = "127.0.0.1"
IPPort = 3587
sender = nil

function OnInit(path)
    sender = socket.udp()
    message("Приблуда: диагностический мост запущен, шлю на " .. IPAddr .. ":" .. IPPort)
end

-- Рекурсивно разворачивает таблицу в строку "ключ=значение;ключ2=значение2".
function serialize_table(t, prefix)
    local parts = {}
    for k, v in pairs(t) do
        local key = prefix and (prefix .. "." .. tostring(k)) or tostring(k)
        if type(v) == "table" then
            table.insert(parts, serialize_table(v, key))
        else
            table.insert(parts, key .. "=" .. tostring(v))
        end
    end
    return table.concat(parts, ";")
end

function OnAllTrade(alltrade)
    if sender == nil then
        return
    end
    local line = serialize_table(alltrade, nil)
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