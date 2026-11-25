# Инструкция для ИИ-агентов (AI Agent Context & Coding Rules)

Данный документ содержит строгие архитектурные правила, инварианты и сигнатуры `PbSdk` для ИИ-ассистентов (Cursor, Windsurf, Claude Code, GitHub Copilot), генерирующих код торговых роботов под **Привод Бондаря 1.0**.

## 1. Стек и базовые ограничения
* **Целевая платформа:** `.NET 7.0` (`net7.0-windows`), `PlatformTarget: x64`, `AllowUnsafeBlocks: true`.
* **Пространства имен:** `using PbSdk;` (фабрика и менеджер SDK) и `using ICore;` (диспетчеры, слоты, слои, стакан, ордера).
* **Закрытое ядро:** пакет `PbSdk.nupkg` обфусцирован. Никогда не используйте рефлексию к приватным типам; работайте исключительно через публичные интерфейсы `IPbSdkManager`, `IDispatcher`, `IDispatcherSlot`, `ILayer`, `IDispatcherSlotEventsReceiver`, `ILayerEventsReceiver`.
* **Файл лицензии:** для прохождения `await sdk.AuthAsync()` требуется валидный файл `%APPDATA%\PbSdk\cs.license`.

## 2. Строгий порядок инициализации (Lifecycle Invariant)
Запрещено менять последовательность запуска SDK и подключения слота:
1. `IPbSdkManager sdk = PbSdkFactory.Create();`
2. `if (!await sdk.AuthAsync()) throw new Exception("Authorization failed");`
3. `sdk.Initialize();`
4. `await sdk.StartAsync();`
5. Получение диспетчера ПБ 1.0: `IDispatcher dispatcher = sdk.PrivodBondarDispatcher;`
6. Получение или создание слота:
   ```csharp
   IDispatcherSlot slot = dispatcher.Slots.Count == 0
       ? (dispatcher.TryAddSlot(DispatcherSlotType.XDealingCommon, out var s, out _) ? s : null)
       : dispatcher.GetSlot(0);
   ```
7. Регистрация слушателя событий слота **до** вызова подключения: `slot.AddDispatcherSlotEventsReceiver(slotReceiver);`
8. Выбор сервера из `slot.PublicServersInfo`, установка `slot.PublicServerInfo`, `slot.Account`, `slot.Password` и вызов `await slot.ConnectAsync(CancellationToken.None);`.
9. Корректная остановка при выходе: `await sdk.StopAsync();`.

## 3. Создание слоя инструмента (`ILayer`) и репликации
Позиции, сделки, стакан и методы торговли привязаны к слою конкретного инструмента (`ILayer`):
1. Дождитесь события `OnContractsTableUpdatedAsync` в `IDispatcherSlotEventsReceiver` (или проверьте `slot.IsConnected && slot.IsContractsUpdated`).
2. Найдите инструмент по уникальному коду `SecurityID` (предпочтительнее) или тикеру:
   ```csharp
   IContractInfo tickerInfo = slot.ContractsTable.SearchContractByID("TQBR.GAZP");
   ```
3. Вызовите создание слоя:
   ```csharp
   var layerData = await slot.InvokeLayerAsync(tickerInfo, CancellationToken.None);
   ILayer layer = layerData.layer;
   ```
4. Подпишите получателя событий слоя (`ILayerEventsReceiver`):
   ```csharp
   await layer.AddLayerEventsReceiverAsync(layerReceiver, CancellationToken.None);
   ```
5. **КРИТИЧЕСКИ ВАЖНО:** если слой создан впервые (`layerData.isnew == true`), **обязательно** вызовите:
   ```csharp
   await slot.InvokeLayerEndAsync(this, layer, CancellationToken.None);
   ```
   Без вызова `InvokeLayerEndAsync` сервер не отправит первичные репликации портфеля по инструменту, и флаг `layer.IsReplicationsDone` никогда не станет `true`.

## 4. Инвариант готовности перед отправкой ордеров
Отправлять торговые команды (`SendOrder`, `SendStopOrder`, `CancelOrder`) разрешено **только** при одновременном выполнении всех условий:
```csharp
bool canTrade = layer != null
    && layer.DispatcherSlot.IsReady          // Слот подключен, справочник и общие репликации получены
    && !layer.IsReplications                 // Первичная репликация слоя не в процессе
    && layer.IsReplicationsDone              // Первичная репликация слоя завершена
    && layer.IsStockReceived                 // Получен хотя бы один снимок стакана
    && layer.LastStock != null
    && layer.LastStock.BestAsk != 0D
    && layer.LastStock.BestBid != 0D
    && layer.LastStock.BestAsk >= layer.LastStock.BestBid;
```

## 5. Потокобезопасность при работе со стаканом (`ContractOrderStock`)
* Свойства `stock.BestAsk` и `stock.BestBid` доступны для чтения напрямую.
* Для перебора уровней стакана (`stock.Asks`, `stock.Bids`) внутри `OnStockUpdate` **строго обязательна** конструкция:
  ```csharp
  lock (stock)
  {
      if (!stock.IsDisposed)
      {
          foreach (var row in stock.Asks) // или stock.Bids
          {
              double price = row.Price;
              decimal amount = row.Amount;
          }
      }
  }
  ```

## 6. Выставление и отмена заявок
* **Округление цен:** всегда рассчитывайте цену кратно шагу `layer.ContractInfo.Step` и округляйте через `Math.Round(price, layer.ContractInfo.Decimals)`.
* **Лимитная/рыночная заявка (синхронно ожидает ответа сервера):**
  ```csharp
  bool ok = layer.SendOrder(orderPrice, orderAmount, OrderAction.Sell, type: OrderFlags.Limit);
  ```
* **Серверная стоп-заявка (Stop-Loss / Take-Profit):**
  ```csharp
  bool slOk = layer.SendStopOrder(
      stop_price: slPrice,
      price: 0,
      amount: 0m, // 0m для закрытия всей позиции по StopLoss
      direction: OrderAction.Sell,
      type: StopOrderType.StopLoss,
      method: StopOrderMethod.TickPrice,
      location: StopOrderLocation.Server,
      bymarket: true);
  ```
* **Отмена заявок:**
  ```csharp
  layer.CancelOrder(orderId, wait_cancel: true);     // ID берется из layer.Lomonitor._List
  layer.CancelStopOrder(stopId, wait_cancel: true);  // ID берется из layer.Somonitor._List
  ```
