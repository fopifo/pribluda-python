# 3. Обработка событий и потокобезопасность (Event Handling)

Все данные от сервера Привода Бондаря поступают через два интерфейса обратного вызова:
* **`IDispatcherSlotEventsReceiver`** — уровень подключения и финансового состояния счета (реализован в [`PbSdkConsole/DispatcherSlotEventsReceiver.cs`](../PbSdkConsole/DispatcherSlotEventsReceiver.cs)).
* **`ILayerEventsReceiver`** — уровень конкретного инструмента: стакан, тики, позиция, сделки и ордера (реализован в [`PbSdkConsole/LayerEventsReceiver.cs`](../PbSdkConsole/LayerEventsReceiver.cs)).

---

## 3.1. Получение финансовых показателей счета (`OnFinDataUpdate`)

После подключения к серверу в метод `OnFinDataUpdate(IDispatcherSlot sender, object updateKey, IFinDataUpdate update)` начинают приходить данные о состоянии торгового счета.

### Особенности протокола финансовых обновлений:
1. **Ключ строки (`updateKey`):** один торговый счет может иметь несколько субсчетов, кошельков или валютных секций. Каждый уникальный `updateKey` соответствует одной строке финансовой таблицы.
2. **Частичные (дельта) обновления:** в одном вызове `OnFinDataUpdate` может прийти только изменившееся поле (например, `FreeAssets` или `PnL`), в то время как остальные свойства в объекте `update` будут равны `null`.
3. **Схема поддерживаемых полей (`sender.FinDataUpdateSchema`):** битовая маска `FinDataUpdateType`, указывающая, какие именно поля поддерживает данное подключение.

В `DispatcherSlotEventsReceiver.cs` реализована готовая модель накопления финансового состояния (`HandleFinDataUpdate` и класс `FinDataViewModel`):
```csharp
FinDataUpdateType schema = sender.FinDataUpdateSchema;

if (schema.HasFlag(FinDataUpdateType.TotalAssets) && update.TotalAssets.HasValue)
    model.TotalAssets = update.TotalAssets; // Общий баланс счета

if (schema.HasFlag(FinDataUpdateType.FreeAssets) && update.FreeAssets.HasValue)
    model.FreeAssets = update.FreeAssets;   // Свободные средства (покупательная способность)

if (schema.HasFlag(FinDataUpdateType.BlockedAssets) && update.BlockedAssets.HasValue)
    model.BlockedAssets = update.BlockedAssets; // Заблокировано под активные заявки и ГО

if (schema.HasFlag(FinDataUpdateType.PnL) && update.PnL.HasValue)
    model.PnL = update.PnL;                 // Текущий финансовый результат (вариационная маржа)

if (schema.HasFlag(FinDataUpdateType.Commission) && update.Commission.HasValue)
    model.Commission = update.Commission;   // Накопленная комиссия
```

> [!TIP]
> При отключении от сервера (`OnDisconnected` и `OnDisposed`) обязательно очищайте локальный кэш финансовых моделей (`ClearFinData()`), чтобы при следующем подключении не отображались устаревшие балансы.

---

## 3.2. Подписка на события инструмента (`LayerEventsReceiverSubscribtions`)

В классе `LayerEventsReceiver` свойство `LayerSubscriptions` задает битовую маску типов событий, которые ваш робот хочет получать от слоя `ILayer`:

```csharp
readonly LayerEventsReceiverSubscribtions _layerSubscriptions =
    LayerEventsReceiverSubscribtions.Assets |
    LayerEventsReceiverSubscribtions.OrderStates |
    LayerEventsReceiverSubscribtions.OrderStock |
    LayerEventsReceiverSubscribtions.Position |
    LayerEventsReceiverSubscribtions.Replications |
    LayerEventsReceiverSubscribtions.Trades |
    LayerEventsReceiverSubscribtions.Ticks;
```
Если вашему роботу не нужна лента обезличенных сделок (`Ticks`) или стакан (`OrderStock`), вы можете исключить соответствующие флаги из маски для экономии ресурсов процессора.

---

## 3.3. Потокобезопасная работа с биржевым стаканом (`OnStockUpdate`)

Событие `OnStockUpdate(ILayer sender, ContractOrderStock stock)` вызывается при каждом изменении котировок в стакане инструмента. Также последний полученный стакан всегда доступен в свойстве `layer.LastStock`.

### Правила потокобезопасности для `ContractOrderStock`:
1. **Лучшие цены (`stock.BestAsk` и `stock.BestBid`):** являются примитивными значениями `double` и доступны для чтения в любой момент без блокировки.
2. **Массивы уровней (`stock.Asks` и `stock.Bids`):** для высокой скорости ядро переиспользует буферы памяти. Если вам нужно перебрать уровни стакана в цикле, **строго обязательно** брать блокировку `lock (stock)` и проверять `!stock.IsDisposed`:

```csharp
public void OnStockUpdate(ILayer sender, ContractOrderStock stock)
{
    // Быстрое чтение лучших цен без блокировки:
    double bestBid = stock.BestBid;
    double bestAsk = stock.BestAsk;

    // Безопасный обход глубины стакана:
    lock (stock)
    {
        if (!stock.IsDisposed)
        {
            foreach (var row in stock.Asks) // аналогично для stock.Bids
            {
                double price = row.Price;
                decimal volume = row.Amount;
            }
        }
    }
}
```

---

## 3.4. Лента обезличенных сделок (`OnTick`)

В интерфейсе `ILayerEventsReceiver` предусмотрены две перегрузки метода `OnTick`:
* `OnTick(ILayer sender, TickGraphicData tick)` — одиночный тик;
* `OnTick(ILayer sender, List<TickGraphicData> ticks)` — пакет тиков.

В рамках конкретного типа подключения работает один из этих двух каналов (для пакетной или поштучной доставки). Последний прошедший по рынку тик также всегда сохраняется в `layer.LastTick`.

---

## 3.5. Репликации, позиции и собственные сделки

При создании слоя первичное состояние портфеля по инструменту приходит внутри окна репликации:
1. Вызывается **`OnReplicationBegin(ILayer sender)`** (`layer.IsReplications == true`).
2. Приходят события текущей позиции (`OnPositionUpdate`), совершенных за сессию сделок (`OnTrade`, `OnClosingTrade`) и активных заявок (`OnLimitOrdersChanged`, `OnStopOrdersChanged`).
3. Вызывается **`OnReplicationEnd(ILayer sender)`** (`layer.IsReplicationsDone == true`). С этого момента слой синхронизирован с сервером, а все последующие вызовы этих методов означают живые рыночные события в реальном времени.

### Чтение текущей позиции (`layer.Posmonitor`):
* **`layer.Posmonitor._PositionAmount`** (`decimal`) — текущий объем позиции (`> 0` — лонг, `< 0` — шорт, `0` — вне рынка).
* **`layer.Posmonitor._PositionPrice`** (`double`) — средняя цена открытой позиции.
* **`layer.Posmonitor._Trades`** — коллекция совершенных сделок.
* **`layer.Posmonitor._ClosingTrades`** — сделки, приведшие к полному или частичному закрытию позиции.

---

## 3.6. Мониторинг активных заявок и ошибок

* **`OnLimitOrdersChanged(ILayer sender, ReadOnlyCollection<LimitOrder> list)`** — вызывается при выставлении, частичном исполнении или снятии лимитной заявки. Актуальный список всегда доступен в `layer.Lomonitor._List`.
* **`OnLimitOrderOpFailed(ILayer sender, int code, string msg)`** — вызывается, если сервер или биржа отклонили выставление/отмену лимитной заявки (содержит код ошибки и текст причины).
* **`OnStopOrdersChanged(ILayer sender, ReadOnlyCollection<StopOrder> list)`** — обновление списка активных стоп-заявок (также доступен в `layer.Somonitor._List`).
* **`OnStopOrderOpFailed(ILayer sender, int code, string msg)`** — отказ в операции со стоп-заявкой.
