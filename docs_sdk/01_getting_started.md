# 1. Установка и настройка окружения (Getting Started)

Данное руководство описывает подготовку рабочего места, настройку лицензии `cs.license` и первый запуск демонстрационного проекта `PbSdkConsole` с подключением к серверу **Привода Бондаря 1.0**.

---

## 1.1. Системные требования

* **Операционная система:** Windows 10, Windows 11 или Windows Server 2019/2022 (строго 64-разрядная архитектура `x64`).
* **Платформа разработки:** установленный [.NET 7.0 SDK](https://dotnet.microsoft.com/download/dotnet/7.0) или новее.
* **IDE:** Visual Studio 2022, JetBrains Rider или VS Code (с расширением C# Dev Kit).
* **Торговый доступ:**
  1. Действующий файл пользовательской лицензии терминала CScalp / Привода Бондаря — **`cs.license`**.
  2. Логин (`Account`) и пароль (`Password`) от вашего торгового счета на проп-сервере Привода Бондаря 1.0.

---

## 1.2. Размещение файла лицензии (`cs.license`)

При вызове метода `await sdk.AuthAsync()` библиотека проверяет наличие и цифровую подпись файла лицензии, после чего загружает с сервера авторизации список доступных вам проп-серверов (`PublicServersInfo`).

Поместите ваш файл `cs.license` по следующему пути:
```text
%APPDATA%\PbSdk\cs.license
```
*(Полный путь обычно выглядит так: `C:\Users\<ИмяПользователя>\AppData\Roaming\PbSdk\cs.license`).*

> [!TIP]
> Вы можете быстро открыть нужную папку, нажав `Win + R`, введя `%APPDATA%` и создав внутри папку `PbSdk`.

---

## 1.3. Устройство проекта и подключение `PbSdk.nupkg`

Пакет `PbSdk` распространяется локально в папке `nupkg/` репозитория, а его стандартные зависимости (`Serilog`, `Newtonsoft.Json` и др.) автоматически скачиваются с официального `nuget.org`.

В корне решения расположен файл `nuget.config`:
```xml
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" protocolVersion="3" />
    <add key="local-pbsdk" value="nupkg" />
  </packageSources>
</configuration>
```

Конфигурация целевого проекта (`.csproj`) должна быть нацелена на `net7.0-windows` и платформу `x64` (см. `PbSdkConsole/PbSdkConsole.csproj`):
```xml
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net7.0-windows</TargetFramework>
    <OutputType>Exe</OutputType>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <PlatformTarget>x64</PlatformTarget>
    <AllowUnsafeBlocks>true</AllowUnsafeBlocks>
  </PropertyGroup>

  <ItemGroup>
    <PackageReference Include="PbSdk" Version="0.1.32" />
  </ItemGroup>
</Project>
```

---

## 1.4. Настройка подключения в `PbSdkConsole/Program.cs`

Откройте файл [`PbSdkConsole/Program.cs`](../PbSdkConsole/Program.cs) и заполните четыре строковые переменные в верхней части файла:

```csharp
string _findStockLongName = "YOUR_SERVER_NAME"; // Точное название сервера из вашей лицензии
string _account = "YOUR_LOGIN";                    // Ваш логин счета Привода Бондаря
string _password = "YOUR_PASSWORD";      // Ваш пароль счета

string _tickerSecurityID = "TQBR.GAZP";       // Уникальный код инструмента (РежимТоргов.Тикер)
```

### Как узнать точное название сервера (`StockLongName`)?
После успешного вызова `await _sdk.AuthAsync()` и создания слота `IDispatcherSlot` коллекция `slot.PublicServersInfo` заполняется всеми серверами, которые привязаны к вашей лицензии `cs.license`. Вы можете вывести их список в консоль перед подключением:
```csharp
foreach (var srv in slot.PublicServersInfo)
{
    Console.WriteLine($"Сервер: '{srv.StockLongName}' | Хост: {srv.Host}:{srv.Port}");
}
```
Скопируйте нужное значение `StockLongName` в переменную `_findStockLongName`.

### Почему рекомендуется использовать `SecurityID` (`TQBR.GAZP`), а не просто `Symbol` (`GAZP`)?
На серверах с несколькими режимами торгов или секциями краткое название тикера (`Symbol`, например `GAZP`) может повторяться. Идентификатор `SecurityID` (`TQBR.GAZP`) гарантирует однозначное совпадение при поиске через `slot.ContractsTable.SearchContractByID(_tickerSecurityID)`.

---

## 1.5. Сборка и первый запуск

Выполните в консоли из корня репозитория:
```bash
dotnet restore PbSdk.sln
dotnet run --project PbSdkConsole/PbSdkConsole.csproj
```

Что произойдет при запуске `PbSdkConsole`:
1. Создастся экземпляр `IPbSdkManager`, пройдет авторизация лицензии `AuthAsync()`, инициализируется и запустится ядро (`Initialize()`, `StartAsync()`).
2. В диспетчере Привода Бондаря (`_sdk.PrivodBondarDispatcher`) создастся слот `DispatcherSlotType.XDealingCommon` и подключится слушатель `DispatcherSlotEventsReceiver`.
3. Слот установит соединение с выбранным сервером (`slot.ConnectAsync`).
4. Сразу после получения таблицы инструментов (`OnContractsTableUpdated`) программа найдет инструмент `TQBR.GAZP`, создаст для него слой `ILayer`, подпишет `LayerEventsReceiver` и запустит репликацию состояния счета (`InvokeLayerEndAsync`).
5. В фоновом потоке запустится метод `RunLayerTradingSample_1()`, который дождется готовности стакана и при обнаружении открытой лонговой позиции по `TQBR.GAZP` автоматически выставит лимитную заявку на закрытие (`+30` шагов цены) и серверный стоп-лосс (`-30` шагов цены).
6. Для штатной остановки робота нажмите любую клавишу — вызов `await _sdk.StopAsync()` корректно закроет сокеты и сохранит настройки слота.
