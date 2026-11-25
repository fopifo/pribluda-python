# 5. Решение типовых проблем (Troubleshooting & FAQ)

В этом разделе собраны частые ошибки, возникающие при настройке окружения и разработке торговых роботов на базе `PbSdk`, и способы их устранения.

---

## 5.1. Ошибка `Authorization failed` при вызове `await _sdk.AuthAsync()`

**Симптомы:** программа падает на шаге `Authorization...` с исключением `Authorization failed`.

**Причины и решение:**
1. **Отсутствует файл лицензии:** убедитесь, что действующий файл `cs.license` скопирован в папку `%APPDATA%\PbSdk\cs.license`.
2. **Истек срок действия лицензии:** откройте терминал CScalp с этим файлом лицензии или запросите обновленный ключ у поддержки.
3. **Нет доступа к серверу авторизации:** проверьте интернет-соединение и убедитесь, что фаервол не блокирует исходящие HTTPS-запросы для процесса `PbSdkConsole.exe`.

---

## 5.2. Не восстанавливается пакет `PbSdk` при `dotnet restore`

**Симптомы:** ошибка `NU1101: Unable to find package PbSdk` или `NU1102: Unable to find package PbSdk with version (>= 0.1.32)`.

**Причины и решение:**
1. Запускайте `dotnet restore` из корневой папки решения, где лежит файл `nuget.config` и папка `nupkg/`.
2. Проверьте, что версия в `<PackageReference Include="PbSdk" Version="0.1.32" />` внутри `.csproj` совпадает с версией `.nupkg` файла в папке `nupkg/`.

---

## 5.3. Ошибка `ERROR: Ticker not found (TQBR.GAZP)`

**Симптомы:** подключение к серверу проходит успешно (`Slot: OnConnected`, `Slot: OnContractsTableUpdated`), но поиск тикера через `SearchContractByID` возвращает `null`.

**Причины и решение:**
1. Вы подключились к серверу другой секции (например, к серверу фьючерсов FORTS, где нет фондовой секции `TQBR`).
2. Вы передали краткое имя (`"GAZP"`) вместо полного `SecurityID` (`"TQBR.GAZP"`).
3. Чтобы посмотреть точные коды доступных инструментов на вашем сервере, выведите первые 20 контрактов внутри `OnContractsTableUpdatedAsync`:
   ```csharp
   foreach (var c in sender.Slot.ContractsTable.Contracts.Take(20))
   {
       Console.WriteLine($"Symbol: {c.Symbol} | SecurityID: {c.SecurityID} | Step: {c.Step}");
   }
   ```

---

## 5.4. Слой создан, но `layer.IsReplicationsDone` всегда `false` (не приходят позиции и заявки)

**Симптомы:** `InvokeLayerAsync` вернул `ILayer`, стакан может обновляться, но события `OnReplicationBegin` / `OnReplicationEnd` не срабатывают, и условие готовности к торговле вечно ждет.

**Причина и решение:**
Вы забыли вызвать завершающий метод инициализации слоя `InvokeLayerEndAsync`. Сразу после создания нового слоя и подписки `LayerEventsReceiver` обязательно вызывайте:
```csharp
if (layerData.isnew)
{
    sender.Slot.InvokeLayerEndAsync(sender, layer, CancellationToken.None).GetAwaiter().GetResult();
}
```

---

## 5.5. Исключение `InvalidOperationException` или `NullReferenceException` при чтении стакана

**Симптомы:** во время перебора `stock.Asks` или `stock.Bids` внутри `OnStockUpdate` периодически возникает ошибка доступа к коллекции или разрушенному объекту.

**Причина и решение:**
Ядро переиспользует объекты стакана между потоками. Чтение массивов `Asks` и `Bids` должно выполняться строго внутри блока `lock (stock)` с проверкой `!stock.IsDisposed`:
```csharp
lock (stock)
{
    if (!stock.IsDisposed)
    {
        foreach (var row in stock.Asks)
        {
            // безопасная обработка уровня цены
        }
    }
}
```
При этом к свойствам `stock.BestBid` и `stock.BestAsk` можно обращаться напрямую без `lock`.

---

## 5.6. Заявка отклоняется (`SendOrder` возвращает `false`, срабатывает `OnLimitOrderOpFailed`)

**Симптомы:** вызов `layer.SendOrder(...)` или `layer.SendStopOrder(...)` возвращает `false`, а в консоль выводится `OnLimitOrderOpFailed: Code=...; Message=...`.

**Причины и решение:**
1. **Неокругленная цена (не кратна шагу цены):** перед отправкой всегда вычисляйте цену через `layer.ContractInfo.Step` и округляйте через `Math.Round(price, layer.ContractInfo.Decimals)`.
2. **Недостаточно средств или превышен лимит риска:** проверьте `FreeAssets` в `OnFinDataUpdate` и настройки риск-менеджмента вашего счета в Приводе Бондаря.
3. **Попытка отправить заявку до готовности слоя:** убедитесь, что перед отправкой проверены `layer.DispatcherSlot.IsReady`, `layer.IsReplicationsDone` и `layer.IsStockReceived`.
