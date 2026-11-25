# 2. Жизненный цикл и автомат состояний (SDK Lifecycle)

Архитектура `PbSdk` построена вокруг трех основных сущностей:
1. **`IPbSdkManager`** — корневой менеджер жизненного цикла ядра и лицензии.
2. **`IDispatcherSlot`** — сетевое подключение к конкретному торговому серверу Привода Бондаря 1.0.
3. **`ILayer`** — рабочий слой конкретного биржевого инструмента (стакан, лента, позиция и управление заявками по тикеру).

Нарушение порядка инициализации этих объектов приведет к исключениям или отсутствию данных от сервера.

---

## 2.1. Фаза 1: Инициализация и остановка ядра (`IPbSdkManager`)

Порядок запуска и завершения работы `IPbSdkManager` (см. [`PbSdkConsole/Program.cs`](../PbSdkConsole/Program.cs)):

```csharp
using ICore;
using PbSdk;

// 1. Создание экземпляра менеджера SDK
IPbSdkManager sdk = PbSdkFactory.Create();

// 2. Проверка лицензии cs.license и получение каталога серверов
if (!await sdk.AuthAsync())
{
    throw new Exception("Authorization failed: проверьте наличие %APPDATA%\PbSdk\cs.license");
}

// 3. Внутренняя инициализация компонентов и диспетчеров
sdk.Initialize();

// 4. Запуск фоновых служб и восстановление ранее сохраненных слотов
await sdk.StartAsync();

// ... работа со слотами и торговля ...

// 5. Корректная остановка (закрывает соединения и сохраняет конфигурацию слотов на диск)
await sdk.StopAsync();
```

> [!WARNING]
> Никогда не обращайтесь к `sdk.PrivodBondarDispatcher` до завершения `sdk.Initialize()` и `await sdk.StartAsync()`.

---

## 2.2. Фаза 2: Создание и подключение слота (`IDispatcherSlot`)

Для работы с Приводом Бондаря 1.0 используется диспетчер `_sdk.PrivodBondarDispatcher` и тип слота `DispatcherSlotType.XDealingCommon`.

> [!NOTE]
> При вызове `await sdk.StopAsync()` библиотека сохраняет добавленные слоты. При следующем запуске `await sdk.StartAsync()` ранее созданный слот уже будет присутствовать в коллекции `dispatcher.Slots`.

Рекомендуемый паттерн получения или создания слота:
```csharp
IDispatcher dispatcher = _sdk.PrivodBondarDispatcher;
IDispatcherSlot slot = null;

if (dispatcher.Slots.Count == 0)
{
    // Если слотов еще нет — создаем новый слот Привода Бондаря 1.0
    dispatcher.TryAddSlot(DispatcherSlotType.XDealingCommon, out slot, out _);
}
else
{
    // Если слот был восстановлен после прошлого запуска — берем его
    slot = dispatcher.GetSlot(0);
}
```

Подписка на события и установка соединения:
```csharp
if (slot != null)
{
    // 1. Сначала регистрируем получателя событий слота
    _slotEvRec = new DispatcherSlotEventsReceiver(slot);
    _slotEvRec.Log += msg => Console.WriteLine(msg);
    _slotEvRec.CanCreateLayers += OnSlotReadyToCreateLayers;

    // 2. Выбираем сервер из справочника, полученного при AuthAsync()
    var serverInfo = slot.PublicServersInfo.FirstOrDefault(a => a.StockLongName == _findStockLongName);
    if (serverInfo != null)
    {
        slot.PublicServerInfo = serverInfo;
        slot.Account = _account;
        slot.Password = _password;

        // 3. Устанавливаем сетевое соединение с сервером ПБ
        slot.ConnectAsync(CancellationToken.None).GetAwaiter().GetResult();
    }
}
```

### Последовательность событий слота при подключении:
1. **`OnConnected(slot)`** — физическое соединение установлено, логин/пароль приняты (`slot.IsConnected == true`).
2. **`OnContractsTableUpdatedAsync(slot, ct)`** — загружен справочник торгуемых инструментов (`slot.IsContractsUpdated == true`, таблица всегда доступна в `slot.ContractsTable`, пока активно соединение). **С этого момента разрешено создавать слои инструментов `ILayer`.**
3. **`OnFinDataUpdate(slot, key, update)`** — поступают данные о балансе, свободных/заблокированных средствах и финрезе счета.
4. Завершение первичной репликации на уровне слота — флаг **`slot.IsReady`** становится `true` (объединяет `IsConnected && IsContractsUpdated && IsReplicationsDone`).

---

## 2.3. Фаза 3: Создание слоя инструмента (`ILayer`) и запуск репликации

В архитектуре `ICore` сам слот не пересылает стаканы и не выставляет заявки напрямую. Для каждого торгуемого тикера создается объект **`ILayer`** (в рамках одного подключения существует ровно один `ILayer` на один `IContractInfo`).

Эталонный код создания слоя из `PbSdkConsole/Program.cs`:
```csharp
void _slotEvRec_CanCreateLayers(DispatcherSlotEventsReceiver sender)
{
    // 1. Поиск описания инструмента в таблице контрактов слота
    IContractInfo tickerInfo = sender.Slot.ContractsTable.SearchContractByID(_tickerSecurityID);
    if (tickerInfo == null)
    {
        Console.WriteLine($"ERROR: Ticker not found ({_tickerSecurityID})");
        return;
    }

    // 2. Запрос или создание слоя для выбранного инструмента
    // Возвращает кортеж (ILayer layer, bool isnew)
    var layerData = sender.Slot.InvokeLayerAsync(tickerInfo, CancellationToken.None).GetAwaiter().GetResult();
    ILayer layer = layerData.layer;

    if (layer != null)
    {
        // 3. Подписываем получателя событий инструмента ДО вызова InvokeLayerEndAsync
        _layerEvRec = new LayerEventsReceiver(layer);
        _layerEvRec.Log += msg => Console.WriteLine(msg);

        // 4. ОБЯЗАТЕЛЬНЫЙ ШАГ для нового слоя: завершение инициализации и запрос репликаций
        if (layerData.isnew)
        {
            sender.Slot.InvokeLayerEndAsync(sender, layer, CancellationToken.None).GetAwaiter().GetResult();
        }
    }
}
```

> [!IMPORTANT]
> **Зачем нужен `InvokeLayerEndAsync`?**
> Двухфазная инициализация (`InvokeLayerAsync` ➔ подписка `AddLayerEventsReceiverAsync` ➔ `InvokeLayerEndAsync`) гарантирует, что вы успеете подключить свой обработчик `LayerEventsReceiver` **до того**, как ядро начнет отсылать первичную репликацию (открытую позицию, совершенные за сессию сделки и активные заявки по этому тикеру). Если не вызвать `InvokeLayerEndAsync`, репликации по слою не начнутся!

---

## 2.4. Фаза 4: Проверка полной готовности к торговле

Даже после создания `ILayer` нельзя отправлять заявки в первую миллисекунду: слой должен завершить репликацию портфеля (`OnReplicationBegin` ➔ `OnReplicationEnd`) и получить актуальный биржевой стакан (`OnStockUpdate`).

Перед вызовом `SendOrder` / `SendStopOrder` всегда проверяйте следующий набор флагов (см. `RunLayerTradingSample_1()` в `Program.cs`):

```csharp
bool IsLayerReadyForTrading(ILayer layer)
{
    return layer != null
        // 1. Слот подключен, таблица инструментов и общие репликации получены
        && layer.DispatcherSlot.IsReady
        // 2. Репликация конкретного слоя завершена
        && !layer.IsReplications
        && layer.IsReplicationsDone
        // 3. Стакан котировок получен и содержит валидные встречные цены
        && layer.IsStockReceived
        && IsStockReady(layer.LastStock);
}

bool IsStockReady(ContractOrderStock stock)
{
    return stock != null
        && stock.BestAsk != 0D
        && stock.BestBid != 0D
        && stock.BestAsk >= stock.BestBid;
}
```

---

## 2.5. Поведение при разрыве связи (`OnDisconnected` / `OnLayerDestroyedAsync`)

* При отключении от сервера (плановом или из-за обрыва сети) в `DispatcherSlotEventsReceiver` вызывается `OnDisconnected`.
* **Все ранее созданные слои `ILayer` автоматически уничтожаются** (вызывается `OnLayerDestroyedAsync` в `LayerEventsReceiver`) и **не подлежат повторному использованию**.
* После повторного подключения слота и нового прихода `OnContractsTableUpdatedAsync` слои инструментов необходимо создать заново через `InvokeLayerAsync` + `InvokeLayerEndAsync`.
