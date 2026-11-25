# 4. Справочник интерфейсов и торговых команд (API Reference)

В данном разделе собраны ключевые публичные интерфейсы, свойства и методы `PbSdk` и `ICore`, используемые для алгоритмической торговли через **Привод Бондаря 1.0**.

---

## 4.1. Фабрика и менеджер SDK (`PbSdk`)

| Тип / Метод | Описание |
|---|---|
| `PbSdkFactory.Create()` | Создает и возвращает корневой экземпляр `IPbSdkManager`. |
| `Task<bool> sdk.AuthAsync()` | Проверяет файл лицензии `%APPDATA%\PbSdk\cs.license` и загружает каталог доступных серверов. |
| `void sdk.Initialize()` | Инициализирует внутренние сервисы и диспетчеры ядра (вызывать после `AuthAsync`). |
| `Task sdk.StartAsync()` | Запускает сетевую подсистему и восстанавливает сохраненные слоты. |
| `Task sdk.StopAsync()` | Останавливает все слоты, закрывает соединения и сохраняет конфигурацию. |
| `IDispatcher sdk.PrivodBondarDispatcher` | Диспетчер подключений Привода Бондаря 1.0 (`DispatcherSlotType.XDealingCommon`). |

---

## 4.2. Слот подключения (`IDispatcherSlot`) и таблица контрактов (`IContractInfo`)

| Член `IDispatcherSlot` | Описание |
|---|---|
| `PublicServersInfo` | Коллекция серверов (`StockLongName`, `Host`, `Port`), доступных по вашей лицензии. |
| `PublicServerInfo` | Выбранный сервер для подключения слота. |
| `Account` / `Password` | Строковые логин и пароль торгового счета Привода Бондаря. |
| `ConnectAsync(CancellationToken)` | Асинхронное подключение к серверу. |
| `DisconnectAsync(CancellationToken)` | Штатное отключение от сервера. |
| `IsConnected` | `true`, если соединение с сервером активно. |
| `IsContractsUpdated` | `true`, если загружен справочник инструментов `ContractsTable`. |
| `IsReplicationsDone` | `true`, если завершена первичная синхронизация данных счета. |
| `IsReady` | Сводный флаг готовности слота (`IsConnected && IsContractsUpdated && IsReplicationsDone`). |
| `ContractsTable.SearchContractByID(string id)` | Поиск инструмента по полному коду `SecurityID` (например, `"TQBR.GAZP"`). Возвращает `IContractInfo`. |
| `InvokeLayerAsync(IContractInfo, CancellationToken)` | Возвращает или создает слой инструмента `(ILayer layer, bool isnew)`. |
| `InvokeLayerEndAsync(object, ILayer, CancellationToken)` | Запускает первичную репликацию для нового слоя (`isnew == true`). |
| `CheckFeature(DispatcherSlotFeature)` | Проверка поддержки опций сервером (например, `DispatcherSlotFeature.StopOrdersAvailable`). |

### Основные свойства инструмента (`IContractInfo` в `layer.ContractInfo`):
* **`Symbol`** (`string`) — краткое название тикера (например, `"GAZP"`).
* **`SecurityID`** (`string`) — уникальный идентификатор инструмента с кодом режима торгов (например, `"TQBR.GAZP"`).
* **`Step`** (`double`) — минимальный шаг цены инструмента.
* **`Decimals`** (`int`) — количество знаков после запятой в цене инструмента (используется в `Math.Round(price, layer.ContractInfo.Decimals)`).

---

## 4.3. Выставление биржевых заявок (`layer.SendOrder`)

Метод `layer.SendOrder` отправляет заявку на сервер, **дожидается ответа от сервера** (не просто записи в сокет) и возвращает `bool` (`true` — заявка успешно принята, `false` — отклонена, детали придут в `OnLimitOrderOpFailed`).

```csharp
// Расчет цены на 30 шагов выше средней цены позиции с округлением по стандарту инструмента:
double orderPrice = layer.Posmonitor._PositionPrice + (layer.ContractInfo.Step * 30);
orderPrice = Math.Round(orderPrice, layer.ContractInfo.Decimals);

// Объем заявки (всегда положительное число):
decimal orderAmount = Math.Abs(layer.Posmonitor._PositionAmount);

// Отправка лимитной заявки на продажу:
// Аргументы: цена, объем, направление (OrderAction.Buy / OrderAction.Sell), тип заявки
bool orderSuccess = layer.SendOrder(orderPrice, orderAmount, OrderAction.Sell, type: OrderFlags.Limit);
```

> [!NOTE]
> Чтобы проверить, поддерживает ли текущий сервер рыночные заявки (`OrderFlags.Market`), используйте флаг `layer.IsServerMarketOrdersAvailable`.

---

## 4.4. Выставление серверных стоп-заявок (`layer.SendStopOrder`)

Метод `layer.SendStopOrder` выставляет стоп-лосс или тейк-профит и также синхронно дожидается подтверждения от сервера:

```csharp
// Расчет цены активации стоп-лосса на 30 шагов ниже цены входа:
double slPrice = layer.Posmonitor._PositionPrice - (layer.ContractInfo.Step * 30);
slPrice = Math.Round(slPrice, layer.ContractInfo.Decimals);

bool slSuccess = layer.SendStopOrder(
    stop_price: slPrice,                     // Цена триггера (активации)
    price: 0,                                // Цена лимитной заявки при срабатывании (при bymarket: true передается 0)
    amount: 0m,                              // Для StopLoss закрытия позиции передается 0m
    direction: OrderAction.Sell,             // Направление закрывающей сделки
    type: StopOrderType.StopLoss,            // StopOrderType.StopLoss или StopOrderType.TakeProfit
    method: StopOrderMethod.TickPrice,       // Условие срабатывания (доступные методы: slot.AvailableStopOrdersMethods)
    location: StopOrderLocation.Server,      // Размещение на сервере Привода Бондаря
    bymarket: true                           // Исполнение по рынку при активации (проверка: layer.IsServerMarketStopOrdersAvailable)
);
```

### Полезные проверки возможностей стоп-заявок:
* Поддержка стоп-заявок на слоте: `slot.CheckFeature(DispatcherSlotFeature.StopOrdersAvailable)`.
* Доступные методы триггера: `slot.AvailableStopOrdersMethods`.
* Поддержка рыночного исполнения серверных стопов: `layer.IsServerMarketStopOrdersAvailable`.

---

## 4.5. Отмена активных лимитных и стоп-заявок (`CancelOrder` / `CancelStopOrder`)

Для снятия выставленной заявки необходим ее идентификатор (`ID`), который берется из списков активных заявок слоя:
* **`layer.Lomonitor._List`** (`ReadOnlyCollection<LimitOrder>`) — активные биржевые (лимитные) заявки.
* **`layer.Somonitor._List`** (`ReadOnlyCollection<StopOrder>`) — активные стоп-заявки.

Пример снятия всех активных заявок по инструменту:
```csharp
// 1. Отмена всех активных лимитных заявок по инструменту
foreach (var limitOrder in layer.Lomonitor._List.ToList())
{
    // wait_cancel: true — дождаться подтверждения снятия от сервера
    bool canceled = layer.CancelOrder(limitOrder.ID, wait_cancel: true);
}

// 2. Отмена всех активных стоп-заявок по инструменту
foreach (var stopOrder in layer.Somonitor._List.ToList())
{
    bool stopCanceled = layer.CancelStopOrder(stopOrder.ID, wait_cancel: true);
}
```
