using System;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Collections;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.Text.Json;
using System.Reflection;
using ICore;
using PbSdk;

namespace PbSdkConsole
{
    internal class Program
    {
        const string Account = "07193";
        const string Password = "skTVnJ1J9i";
        const string CmdToken = "pribluda-local-1";
        const string HttpPrefix = "http://127.0.0.1:8787/";
        const bool AllowLive = true;
        const bool TestMode = true;
        const bool SpoofEnabled = false; // фоновые мигалки ОТКЛЮЧЕНЫ (спам)
        const string ProjectRoot = @"C:\Users\Public\MI_CODES\Qwen_coder";
        const int ConnectRetries = 3;
        const int ConnectRetryDelayMs = 5000;

        // Торговый whitelist (не меняется расширением подписки)
        static readonly Dictionary<string, HashSet<decimal>> TestWhitelist = new()
        {
            { "MTLR",      new HashSet<decimal>{ 1, 99, 100, 200, 300, 500 } },
            { "MTLRP",     new HashSet<decimal>{ 1, 9, 10, 20, 30, 50 } },
            { "CNYRUBF",   new HashSet<decimal>{ 1, 2, 5 } },
            { "CNY-12.26", new HashSet<decimal>{ 1, 2, 5 } },
        };

        static readonly Dictionary<string, double> SpoofThresholds = new()
        {
            { "MTLR", 2000 }, { "MTLRP", 500 }, { "CNYRUBF", 2000 }, { "CNY-12.26", 200 },
        };
        const double SpoofMinHoldSec = 20;
        const double SpoofBotPeriodSec = 90;
        const int SpoofBotMinCycles = 3;
        const double SpoofStaleSec = 15;
        const double SpoofAlertCooldownSec = 300;
        const int SpoofSkipTopLevels = 5;

        static IPbSdkManager _sdk;
        static Mutex _singleInstance;
        static readonly ConcurrentDictionary<string, ILayer> Layers = new ConcurrentDictionary<string, ILayer>();
        static readonly ConcurrentDictionary<string, Dictionary<string, double>> Fin = new ConcurrentDictionary<string, Dictionary<string, double>>();
        static readonly HashSet<string> SeenCmdIds = new HashSet<string>();
        static readonly object CmdLock = new object();
        static StreamWriter? CommandWriter;
        static StreamWriter? SpoofWriter;
        static string _lastStateJson = "{}";
        static string _lastSpoofJson = "{\"levels\":[],\"updated_ms\":0}";
        const string StatePath = ProjectRoot + @"\data\bridge_state.json";

        static List<string> TapeSymbols = new List<string>();
        static readonly HashSet<string> Subscribed = new HashSet<string>();
        static readonly object SubLock = new object();

        sealed class TrackedLevel
        {
            public string Sym = "";
            public string Side = "";
            public double Price;
            public double MaxAmount;
            public double LastAmount;
            public long FirstSeenMs;
            public long LastSeenMs;
            public int Cycles;
            public long LastAlertMs;
            public string? LastAlertKind;
        }
        static readonly Dictionary<string, TrackedLevel> Tracked = new Dictionary<string, TrackedLevel>();
        static readonly object TrackedLock = new object();

        static List<string> LoadTapeSymbols()
        {
            var list = new List<string>();
            var path = Path.Combine(ProjectRoot, "ticker_settings.json");
            try
            {
                using var doc = JsonDocument.Parse(File.ReadAllText(path));
                foreach (var prop in doc.RootElement.EnumerateObject())
                {
                    bool active = true;
                    if (prop.Value.TryGetProperty("active", out var a) && a.ValueKind == JsonValueKind.False)
                        active = false;
                    if (active) list.Add(prop.Name);
                }
                Console.WriteLine($"[OK] ticker_settings: {list.Count} активных тикеров");
            }
            catch (Exception e)
            {
                Console.WriteLine($"[!] ticker_settings.json не прочитан: {e.Message}");
                Console.WriteLine("[!] подписка не выполнена — проверь файл");
            }
            return list;
        }

        static string[] AliasesFor(string sym)
            => new[] { "TQBR." + sym, "FUT." + sym, "CETS." + sym, sym };

        static StreamWriter OpenAppend(string path)
        {
            var fs = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite);
            return new StreamWriter(fs) { AutoFlush = true };
        }

        static void TryParseFin(string m, string slotName)
        {
            if (m == null || !m.StartsWith("Slot: Currency:")) return;
            var d = new Dictionary<string, double>();
            foreach (var part in m.Substring("Slot: ".Length).Split(';'))
            {
                var kv = part.Split(':');
                if (kv.Length != 2) continue;
                var key = kv[0].Trim();
                var val = kv[1].Trim().Replace(" ", "").Replace(",", ".");
                if (double.TryParse(val, NumberStyles.Any, CultureInfo.InvariantCulture, out double v))
                    d[key] = v;
            }
            if (d.Count > 0) Fin[slotName] = d;
        }

        static async Task StateLoop()
        {
            while (true)
            {
                try
                {
                    var symbols = new Dictionary<string, object>();
                    foreach (var kv in Layers)
                    {
                        var l = kv.Value;
                        string sym = kv.Key;
                        decimal pos = 0; double pp = 0, bb = 0, ba = 0;
                        try { pos = l.Posmonitor._PositionAmount; pp = l.Posmonitor._PositionPrice; } catch { }
                        var st = l.LastStock;
                        if (st != null) { bb = st.BestBid; ba = st.BestAsk; }
                        bool ready = l.DispatcherSlot.IsReady && !l.IsReplications && l.IsReplicationsDone && l.IsStockReceived;
                        symbols[sym] = new
                        {
                            position = pos,
                            pos_price = pp,
                            best_bid = bb,
                            best_ask = ba,
                            ready = ready,
                            realized = LayerEventsReceiver.GetRealized(sym),
                            avg_entry = LayerEventsReceiver.GetAvg(sym)
                        };
                    }
                    var fin = Fin.ToDictionary(kv => kv.Key, kv => kv.Value);
                    var json = JsonSerializer.Serialize(new
                    {
                        updated_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                        symbols = symbols,
                        fin = fin
                    });
                    _lastStateJson = json;
                    var tmp = StatePath + ".tmp";
                    File.WriteAllText(tmp, json);
                    File.Move(tmp, StatePath, true);
                }
                catch { }
                await Task.Delay(1000);
            }
        }

        static string MakeKey(string sym, string side, double price)
            => $"{sym}|{side}|{price.ToString("F6", CultureInfo.InvariantCulture)}";

        static async Task SpoofMonitorLoop()
        {
            if (!SpoofEnabled) { Console.WriteLine("[OK] spoof monitor: ОТКЛЮЧЕН"); return; }
            while (true)
            {
                try
                {
                    long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                    var activeNow = new HashSet<string>();
                    foreach (var kv in Layers)
                    {
                        string sym = kv.Key;
                        var layer = kv.Value;
                        if (!SpoofThresholds.TryGetValue(sym, out double thr)) continue;
                        var stock = layer.LastStock;
                        if (stock == null) continue;
                        try { if (stock.BestBid == 0 && stock.BestAsk == 0) continue; } catch { continue; }
                        var bids = new List<(double p, double a)>();
                        var asks = new List<(double p, double a)>();
                        try
                        {
                            lock (stock)
                            {
                                if (stock.IsDisposed) continue;
                                int bidsLen = stock.Bids.Length;
                                int asksLen = stock.Asks.Length;
                                for (int i = SpoofSkipTopLevels; i < bidsLen; i++)
                                {
                                    var row = stock.Bids[i];
                                    bids.Add((Convert.ToDouble(row.Price), Convert.ToDouble(row.Amount)));
                                }
                                for (int i = SpoofSkipTopLevels; i < asksLen; i++)
                                {
                                    var row = stock.Asks[i];
                                    asks.Add((Convert.ToDouble(row.Price), Convert.ToDouble(row.Amount)));
                                }
                            }
                        }
                        catch { continue; }
                        ProcessSide(sym, "bids", bids, thr, nowMs, activeNow);
                        ProcessSide(sym, "asks", asks, thr, nowMs, activeNow);
                    }
                    lock (TrackedLock)
                    {
                        foreach (var k in Tracked.Keys.ToList())
                        {
                            if (activeNow.Contains(k)) continue;
                            var lvl = Tracked[k];
                            double age = (nowMs - lvl.LastSeenMs) / 1000.0;
                            if (age < SpoofStaleSec) continue;
                            if (lvl.LastAlertKind != "gone" && (nowMs - lvl.LastAlertMs) / 1000.0 > SpoofAlertCooldownSec)
                                EmitAlert(lvl, "gone", nowMs, lvl.MaxAmount);
                            Tracked.Remove(k);
                        }
                    }
                    UpdateSpoofJson(nowMs);
                }
                catch (Exception e) { Console.WriteLine($"[spoof] error: {e.Message}"); }
                await Task.Delay(2000);
            }
        }

        static void ProcessSide(string sym, string side, List<(double p, double a)> rows, double thr,
                                long nowMs, HashSet<string> activeNow)
        {
            foreach (var (price, amt) in rows)
            {
                if (amt < thr) continue;
                string key = MakeKey(sym, side, price);
                activeNow.Add(key);
                TrackedLevel lvl;
                bool isNew = false;
                lock (TrackedLock)
                {
                    if (!Tracked.TryGetValue(key, out lvl!))
                    {
                        lvl = new TrackedLevel
                        {
                            Sym = sym, Side = side, Price = price,
                            FirstSeenMs = nowMs, LastSeenMs = nowMs,
                            MaxAmount = amt, LastAmount = amt, Cycles = 1
                        };
                        Tracked[key] = lvl;
                        isNew = true;
                    }
                    else
                    {
                        lvl.LastSeenMs = nowMs;
                        lvl.LastAmount = amt;
                        if (amt > lvl.MaxAmount) lvl.MaxAmount = amt;
                    }
                }
                double hold = (nowMs - lvl.FirstSeenMs) / 1000.0;
                double sinceAlert = (nowMs - lvl.LastAlertMs) / 1000.0;
                if (isNew && hold >= SpoofMinHoldSec && sinceAlert > SpoofAlertCooldownSec)
                    EmitAlert(lvl, "wall", nowMs, amt);
                else if (!isNew && lvl.LastAlertKind == "wall" && lvl.Cycles >= SpoofBotMinCycles
                         && hold >= SpoofBotPeriodSec && sinceAlert > SpoofAlertCooldownSec)
                    EmitAlert(lvl, "bot", nowMs, amt);
            }
        }

        static void EmitAlert(TrackedLevel lvl, string kind, long nowMs, double amount)
        {
            lvl.LastAlertMs = nowMs;
            lvl.LastAlertKind = kind;
            lvl.Cycles++;
            var rec = new
            {
                ts = nowMs, sym = lvl.Sym, side = lvl.Side, price = lvl.Price,
                amount, kind, hold_s = (int)((nowMs - lvl.FirstSeenMs) / 1000.0), cycles = lvl.Cycles
            };
            var line = JsonSerializer.Serialize(rec);
            lock (CmdLock) { SpoofWriter?.WriteLine(line); }
            Console.WriteLine($"[SPOOF {kind.ToUpper()}] {lvl.Sym} {lvl.Side} {lvl.Price} x{amount:F0}");
        }

        static void UpdateSpoofJson(long nowMs)
        {
            var list = new List<object>();
            lock (TrackedLock)
            {
                foreach (var lvl in Tracked.Values)
                {
                    double hold = (nowMs - lvl.FirstSeenMs) / 1000.0;
                    if (hold < SpoofMinHoldSec) continue;
                    list.Add(new
                    {
                        sym = lvl.Sym, side = lvl.Side, price = lvl.Price,
                        amount = lvl.MaxAmount, hold_s = (int)hold,
                        cycles = lvl.Cycles, last_alert = lvl.LastAlertKind
                    });
                }
            }
            _lastSpoofJson = JsonSerializer.Serialize(new { updated_ms = nowMs, levels = list });
        }

        static string ProcessOrder(string body)
        {
            try
            {
                using var doc = JsonDocument.Parse(body);
                var r = doc.RootElement;
                string token = r.TryGetProperty("token", out var t) ? t.GetString() ?? "" : "";
                if (token != CmdToken) return "{\"ok\":false,\"error\":\"bad token\"}";
                string id = r.TryGetProperty("id", out var i) ? i.GetString() ?? "" : "";
                lock (CmdLock) { if (SeenCmdIds.Contains(id)) return "{\"ok\":true,\"duplicate\":true}"; SeenCmdIds.Add(id); }
                string symbol = r.GetProperty("symbol").GetString() ?? "";
                string side = (r.GetProperty("side").GetString() ?? "").ToLowerInvariant();
                decimal qty = r.GetProperty("qty").GetDecimal();
                string type = r.TryGetProperty("type", out var ty) ? (ty.GetString() ?? "limit").ToLowerInvariant() : "limit";
                double? reqPrice = r.TryGetProperty("price", out var pv) && pv.ValueKind == JsonValueKind.Number ? pv.GetDouble() : (double?)null;
                bool closePos = r.TryGetProperty("close_position", out var cp) && cp.ValueKind == JsonValueKind.True;
                bool live = r.TryGetProperty("live", out var lv) && lv.ValueKind == JsonValueKind.True;
                if (side != "buy" && side != "sell") return "{\"ok\":false,\"error\":\"bad side\"}";
                if (qty <= 0) return "{\"ok\":false,\"error\":\"bad qty\"}";
                if (live && !AllowLive) return "{\"ok\":false,\"error\":\"live disabled\"}";
                if (TestMode)
                {
                    if (!TestWhitelist.TryGetValue(symbol, out var allowed))
                        return $"{{\"ok\":false,\"error\":\"test mode: symbol {symbol} not allowed\"}}";
                    if (closePos)
                    {
                        if (!Layers.TryGetValue(symbol, out var layerClose))
                            return $"{{\"ok\":false,\"error\":\"no layer {symbol}\"}}";
                        decimal curPos = 0;
                        try { curPos = layerClose.Posmonitor._PositionAmount; } catch { }
                        if (qty != Math.Abs(curPos) || curPos == 0)
                            return $"{{\"ok\":false,\"error\":\"close qty mismatch (pos={curPos}, qty={qty})\"}}";
                    }
                    else if (!allowed.Contains(qty))
                        return $"{{\"ok\":false,\"error\":\"test mode: qty {qty} not in whitelist for {symbol}\"}}";
                }
                if (!Layers.TryGetValue(symbol, out var layer)) return $"{{\"ok\":false,\"error\":\"no layer {symbol}\"}}";
                var stock = layer.LastStock;
                bool ready = layer.DispatcherSlot.IsReady && !layer.IsReplications && layer.IsReplicationsDone
                             && layer.IsStockReceived && stock != null && stock.BestAsk != 0 && stock.BestBid != 0 && stock.BestAsk >= stock.BestBid;
                if (!ready) return "{\"ok\":false,\"error\":\"layer not ready\"}";
                double execPrice = reqPrice ?? (side == "buy" ? stock.BestAsk : stock.BestBid);
                execPrice = Math.Round(execPrice, layer.ContractInfo.Decimals);

                if (!live)
                {
                    var recDry = new
                    {
                        ts = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                        id, symbol, side, qty, type, price = execPrice,
                        close_position = closePos, status = "DRYRUN"
                    };
                    var lineDry = JsonSerializer.Serialize(recDry);
                    lock (CmdLock) { CommandWriter?.WriteLine(lineDry); }
                    Console.WriteLine($"[DRYRUN ORDER] {lineDry}");
                    return $"{{\"ok\":true,\"dry\":true,\"symbol\":\"{symbol}\",\"side\":\"{side}\",\"qty\":{qty.ToString(CultureInfo.InvariantCulture)},\"price\":{execPrice.ToString(CultureInfo.InvariantCulture)},\"note\":\"dry-run: заявка НЕ отправлена на биржу\"}}";
                }

                var action = side == "buy" ? OrderAction.Buy : OrderAction.Sell;
                bool sent = layer.SendOrder(execPrice, qty, action, type: OrderFlags.Limit);
                var recLive = new
                {
                    ts = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                    id, symbol, side, qty, type, price = execPrice,
                    close_position = closePos, status = "LIVE", accepted = sent
                };
                var lineLive = JsonSerializer.Serialize(recLive);
                lock (CmdLock) { CommandWriter?.WriteLine(lineLive); }
                Console.WriteLine($"[LIVE ORDER] {lineLive}");
                return $"{{\"ok\":{(sent ? "true" : "false")},\"live\":true,\"accepted\":{(sent ? "true" : "false")},\"symbol\":\"{symbol}\",\"side\":\"{side}\",\"qty\":{qty.ToString(CultureInfo.InvariantCulture)},\"price\":{execPrice.ToString(CultureInfo.InvariantCulture)},\"note\":\"{(sent ? "заявка отправлена на сервер" : "сервер отклонил заявку")}\"}}";
            }
            catch (Exception e)
            {
                return $"{{\"ok\":false,\"error\":\"{e.Message.Replace("\"", "'")}\"}}";
            }
        }

        static object? BuildArgValue(Type pt, string? oid, object? order)
        {
            try
            {
                if (pt == typeof(string)) return oid ?? "";
                if (pt == typeof(bool)) return true;
                if (pt == typeof(long) || pt == typeof(ulong)) return long.Parse(oid ?? "0", CultureInfo.InvariantCulture);
                if (pt == typeof(int) || pt == typeof(uint)) return int.Parse(oid ?? "0", CultureInfo.InvariantCulture);
                if (pt.IsEnum)
                {
                    var names = Enum.GetNames(pt);
                    string? pick = names.FirstOrDefault(n => n.IndexOf("Limit", StringComparison.OrdinalIgnoreCase) >= 0)
                                ?? names.FirstOrDefault(n => n.IndexOf("All", StringComparison.OrdinalIgnoreCase) >= 0)
                                ?? names.FirstOrDefault(n => n.IndexOf("Both", StringComparison.OrdinalIgnoreCase) >= 0)
                                ?? names.FirstOrDefault();
                    if (pick != null) return Enum.Parse(pt, pick);
                }
                if (order != null && pt.IsInstanceOfType(order)) return order;
            }
            catch { }
            return null;
        }

        static string ProcessCancelAll(string body)
        {
            try
            {
                using var doc = JsonDocument.Parse(body);
                var r = doc.RootElement;
                string token = r.TryGetProperty("token", out var t) ? t.GetString() ?? "" : "";
                if (token != CmdToken) return "{\"ok\":false,\"error\":\"bad token\"}";
                string symbol = r.TryGetProperty("symbol", out var s) ? (s.GetString() ?? "") : "";
                bool all = string.IsNullOrEmpty(symbol) || symbol == "*";

                var ifaceType = typeof(ILayer);
                var cancelAll = ifaceType.GetMethod("CancelAllOrders");
                var cancelOne = ifaceType.GetMethod("CancelOrder");

                int totalCancelled = 0;
                var results = new List<string>();
                foreach (var kv in Layers)
                {
                    string sym = kv.Key;
                    if (!all && sym != symbol) continue;
                    var layer = kv.Value;
                    int c = 0;
                    try
                    {
                        object? lomonitor = layer.GetType().GetProperty("Lomonitor", BindingFlags.Public | BindingFlags.Instance)?.GetValue(layer);
                        if (lomonitor == null) { results.Add($"{sym}:no_lomonitor"); continue; }
                        object? listObj = null;
                        var listProp = lomonitor.GetType().GetProperty("_List", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                        if (listProp != null) listObj = listProp.GetValue(lomonitor);
                        else
                        {
                            var listField = lomonitor.GetType().GetField("_List", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                            if (listField != null) listObj = listField.GetValue(lomonitor);
                        }
                        if (listObj is not IEnumerable enumerable) { results.Add($"{sym}:no_list"); continue; }

                        var orders = new List<object>();
                        foreach (var o in enumerable) if (o != null) orders.Add(o);
                        if (orders.Count == 0) { results.Add($"{sym}:0"); continue; }

                        bool done = false;
                        if (cancelAll != null)
                        {
                            var ps = cancelAll.GetParameters();
                            object?[] args = new object?[ps.Length];
                            bool ok = true;
                            for (int i = 0; i < ps.Length; i++)
                            {
                                args[i] = BuildArgValue(ps[i].ParameterType, null, null);
                                if (args[i] == null) { ok = false; break; }
                            }
                            if (ok)
                            {
                                try { cancelAll.Invoke(layer, args); c = orders.Count; done = true; }
                                catch (Exception ex) { Console.WriteLine($"[cancel-all] {sym}: {ex.Message}"); }
                            }
                        }
                        if (!done && cancelOne != null)
                        {
                            var ps = cancelOne.GetParameters();
                            foreach (var order in orders)
                            {
                                var ot = order.GetType();
                                string? oid = null;
                                foreach (var name in new[] { "ID", "Id", "OrderId", "OrderID", "id" })
                                {
                                    var p = ot.GetProperty(name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
                                    if (p != null) { try { oid = Convert.ToString(p.GetValue(order), CultureInfo.InvariantCulture); break; } catch { } }
                                }
                                if (string.IsNullOrEmpty(oid)) continue;
                                object?[] args = new object?[ps.Length];
                                bool ok = true;
                                for (int i = 0; i < ps.Length; i++)
                                {
                                    args[i] = BuildArgValue(ps[i].ParameterType, oid, order);
                                    if (args[i] == null) { ok = false; break; }
                                }
                                if (!ok) continue;
                                try { cancelOne.Invoke(layer, args); c++; done = true; }
                                catch (Exception ex) { Console.WriteLine($"[cancel-one] {sym} id={oid}: {ex.Message}"); }
                            }
                        }
                        if (!done) { results.Add($"{sym}:no_cancel_method"); continue; }
                    }
                    catch (Exception e) { Console.WriteLine($"[cancel] {sym}: {e.Message}"); }
                    results.Add($"{sym}:{c}");
                    totalCancelled += c;
                }
                var rec = new { ts = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), action = "cancel_all", symbol = all ? "*" : symbol, cancelled = totalCancelled, details = results };
                var line = JsonSerializer.Serialize(rec);
                lock (CmdLock) { CommandWriter?.WriteLine(line); }
                Console.WriteLine($"[CANCEL ALL] {line}");
                return $"{{\"ok\":true,\"cancelled\":{totalCancelled},\"details\":[{string.Join(",", results.Select(x => $"\"{x}\""))}]}}";
            }
            catch (Exception e)
            {
                return $"{{\"ok\":false,\"error\":\"{e.Message.Replace("\"", "'")}\"}}";
            }
        }

        static async Task HttpLoop()
        {
            var http = new HttpListener();
            http.Prefixes.Add(HttpPrefix);
            try { http.Start(); }
            catch (Exception e) { Console.WriteLine($"[!] cmd channel fail: {e.Message}"); return; }
            Console.WriteLine($"[OK] cmd channel: {HttpPrefix} (allow-live={AllowLive}, test-mode={TestMode})");
            while (http.IsListening)
            {
                HttpListenerContext ctx;
                try { ctx = await http.GetContextAsync(); } catch { continue; }
                _ = Task.Run(() =>
                {
                    try
                    {
                        string body;
                        using (var sr = new StreamReader(ctx.Request.InputStream, Encoding.UTF8)) body = sr.ReadToEnd();
                        string outJson; int code = 200;
                        if (ctx.Request.HttpMethod == "POST" && ctx.Request.Url!.AbsolutePath == "/order") outJson = ProcessOrder(body);
                        else if (ctx.Request.HttpMethod == "POST" && ctx.Request.Url!.AbsolutePath == "/cancel_all") outJson = ProcessCancelAll(body);
                        else if (ctx.Request.Url!.AbsolutePath == "/ping") outJson = "{\"ok\":true}";
                        else if (ctx.Request.Url!.AbsolutePath == "/state") outJson = _lastStateJson;
                        else if (ctx.Request.Url!.AbsolutePath == "/spoof_live") outJson = _lastSpoofJson;
                        else { code = 404; outJson = "{\"ok\":false,\"error\":\"not found\"}"; }
                        var buf = Encoding.UTF8.GetBytes(outJson);
                        ctx.Response.StatusCode = code;
                        ctx.Response.ContentType = "application/json";
                        ctx.Response.ContentLength64 = buf.Length;
                        ctx.Response.OutputStream.Write(buf, 0, buf.Length);
                    }
                    catch { try { ctx.Response.StatusCode = 500; } catch { } }
                    finally { try { ctx.Response.Close(); } catch { } }
                });
            }
        }

        static async Task Main()
        {
            _singleInstance = new Mutex(true, "PbSdkBridge_SingleInstance", out bool createdNew);
            if (!createdNew) { Console.WriteLine("[!] Мост уже запущен. Выход."); return; }

            TapeSymbols = LoadTapeSymbols();
            if (TapeSymbols.Count == 0) { Console.WriteLine("[!] нечего подписывать. Выход."); return; }

            Console.WriteLine("[1/6] Init...");
            _sdk = PbSdkFactory.Create();
            Console.WriteLine("[2/6] Auth...");
            if (!await _sdk.AuthAsync()) throw new Exception("Auth failed");
            Console.WriteLine("Authorized");
            Console.WriteLine("[3/6] Start...");
            _sdk.Initialize();
            await _sdk.StartAsync();

            Console.WriteLine("[4/6] Log files...");
            var today = DateTime.Now.ToString("yyyy-MM-dd");
            var dataDir = Path.Combine(ProjectRoot, "data");
            Directory.CreateDirectory(dataDir);
            var logPath = Path.Combine(dataDir, $"pbsdk_trades_{today}.jsonl");
            var obPath = Path.Combine(dataDir, $"orderbook_{today}.jsonl");
            var cmdPath = Path.Combine(dataDir, "trade_commands.jsonl");
            var spoofPath = Path.Combine(dataDir, "spoof_live.jsonl");
            LayerEventsReceiver.TradeWriter = OpenAppend(logPath);
            LayerEventsReceiver.OrderBookWriter = OpenAppend(obPath);
            CommandWriter = OpenAppend(cmdPath);
            SpoofWriter = OpenAppend(spoofPath);
            Console.WriteLine($"[OK] tape: {logPath}");
            Console.WriteLine($"[OK] orderbook: {obPath}");
            Console.WriteLine($"[OK] commands: {cmdPath}");

            Console.WriteLine("[5/6] Connect ALL markets...");
            IDispatcher dispatcher = _sdk.PrivodBondarDispatcher;
            IDispatcherSlot probe;
            if (dispatcher.Slots.Count == 0)
                dispatcher.TryAddSlot(DispatcherSlotType.XDealingCommon, out probe, out _);
            else
                probe = dispatcher.GetSlot(0);

            var servers = probe.PublicServersInfo
                .Where(a => a.StockLongName != null && a.StockLongName.IndexOf("lite", StringComparison.OrdinalIgnoreCase) >= 0)
                .ToList();
            if (servers.Count == 0)
            {
                Console.WriteLine("[!] no lite servers. Available:");
                foreach (var s in probe.PublicServersInfo) Console.WriteLine("    " + s.StockLongName);
            }

            bool first = true;
            foreach (var si in servers)
            {
                IDispatcherSlot slot;
                if (first) { slot = probe; first = false; }
                else { dispatcher.TryAddSlot(DispatcherSlotType.XDealingCommon, out slot, out _); }
                if (slot == null) { Console.WriteLine($"[!] slot null for {si.StockLongName}"); continue; }

                var rec = new DispatcherSlotEventsReceiver(slot);
                string slotName = si.StockLongName;
                // ДИАГНОСТИКА: печатаем все события слота, кроме частых fin-строк
                rec.Log += msg =>
                {
                    TryParseFin(msg, slotName);
                    if (msg != null && !msg.StartsWith("Slot: Currency:"))
                        Console.WriteLine($"[{slotName}] {msg}");
                };
                rec.CanCreateLayers += OnCanCreateLayers;
                slot.PublicServerInfo = si;
                slot.Account = Account;
                slot.Password = Password;
                Console.WriteLine($"[connect] {si.StockLongName}");
                try { await slot.ConnectAsync(CancellationToken.None); }
                catch (Exception e) { Console.WriteLine($"[connect] {si.StockLongName} исключение: {e.Message}"); }

                for (int attempt = 1; attempt <= ConnectRetries && !slot.IsConnected; attempt++)
                {
                    Console.WriteLine($"[connect] {si.StockLongName}: НЕ подключён, пауза {ConnectRetryDelayMs / 1000}с, попытка {attempt}/{ConnectRetries}");
                    await Task.Delay(ConnectRetryDelayMs);
                    try { await slot.ConnectAsync(CancellationToken.None); }
                    catch (Exception e) { Console.WriteLine($"[connect] {si.StockLongName} исключение: {e.Message}"); }
                }
                Console.WriteLine($"[connect] {si.StockLongName} -> IsConnected={slot.IsConnected}");
            }

            _ = Task.Run(StateLoop);
            _ = Task.Run(HttpLoop);
            if (SpoofEnabled) _ = Task.Run(SpoofMonitorLoop);
            else Console.WriteLine("[OK] spoof monitor: ОТКЛЮЧЕН");

            Console.WriteLine("[6/6] жду подписки (30с)...");
            await Task.Delay(30000);
            var missing = TapeSymbols.Where(s => !Subscribed.Contains(s)).ToList();
            Console.WriteLine("======================================================================");
            Console.WriteLine($" ПОДПИСКА: {Subscribed.Count} из {TapeSymbols.Count} тикеров; слоёв={Layers.Count}");
            Console.WriteLine($" НЕ НАЙДЕНО ({missing.Count}): {string.Join(", ", missing)}");
            Console.WriteLine("======================================================================");
            Console.WriteLine("waiting. Press any key to stop.");
            Console.ReadKey(true);
            Console.WriteLine("stopping...");
            LayerEventsReceiver.TradeWriter?.Close();
            LayerEventsReceiver.OrderBookWriter?.Close();
            CommandWriter?.Close();
            SpoofWriter?.Close();
            await _sdk.StopAsync();
        }

        static void OnCanCreateLayers(DispatcherSlotEventsReceiver sender)
        {
            string slotName = sender.Slot.PublicServerInfo?.StockLongName ?? "?";
            Console.WriteLine($"[contracts] {slotName}: справочник получен, ищу тикеры");
            int ok = 0;
            foreach (var sym in TapeSymbols)
            {
                bool reserved;
                lock (SubLock) { reserved = Subscribed.Add(sym); }
                if (!reserved) continue;

                IContractInfo info = null;
                string used = null;
                foreach (var a in AliasesFor(sym))
                {
                    try { info = sender.Slot.ContractsTable.SearchContractByID(a); }
                    catch { info = null; }
                    if (info != null) { used = a; break; }
                }
                if (info == null)
                {
                    lock (SubLock) { Subscribed.Remove(sym); }
                    continue;
                }

                try
                {
                    var ld = sender.Slot.InvokeLayerAsync(info, CancellationToken.None).GetAwaiter().GetResult();
                    ILayer layer = ld.layer;
                    if (layer == null)
                    {
                        lock (SubLock) { Subscribed.Remove(sym); }
                        continue;
                    }
                    var lr = new LayerEventsReceiver(layer);
                    lr.Log += m => { };
                    if (ld.isnew)
                        sender.Slot.InvokeLayerEndAsync(sender, layer, CancellationToken.None).GetAwaiter().GetResult();
                    Layers[layer.ContractInfo.Symbol] = layer;
                    ok++;
                    Console.WriteLine($"  [+] {slotName}: {used}");
                }
                catch (Exception e)
                {
                    lock (SubLock) { Subscribed.Remove(sym); }
                    Console.WriteLine($"  [!] {sym}: {e.Message}");
                }
            }
            Console.WriteLine($"[+] {slotName}: подписано {ok}, всего слоёв {Layers.Count}");
        }
    }
}