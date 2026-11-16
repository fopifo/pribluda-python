using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using ICore;
using PbSdk;

namespace PbSdkConsole
{
    internal class Program
    {
        const string Account = "07193";
        const string Password = "skTVnJ1J9i";
        static readonly string[][] TickerAliases = {
            new[]{ "TQBR.SBER" },
            new[]{ "TQBR.MTLR" },
            new[]{ "TQBR.MTLRP" },
            new[]{ "FUT.CNYRUBF", "RFUD.CNYRUBF", "CNYRUBF" },
            new[]{ "FUT.CRZ6", "RFUD.CRZ6", "CRZ6" },
            new[]{ "CETS.CNYRUB_TOM", "CNYRUB_TOM" },
        };
        static IPbSdkManager _sdk;
        static Mutex _singleInstance;

        static StreamWriter OpenAppend(string path)
        {
            var fs = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.ReadWrite);
            return new StreamWriter(fs) { AutoFlush = true };
        }

        static async Task Main()
        {
            _singleInstance = new Mutex(true, "PbSdkBridge_SingleInstance", out bool createdNew);
            if (!createdNew)
            {
                Console.WriteLine("[!] Мост уже запущен во втором экземпляре. Выход.");
                return;
            }

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
            var dataDir = @"C:\Users\Public\MI_CODES\Qwen_coder\data";
            Directory.CreateDirectory(dataDir);
            var logPath = Path.Combine(dataDir, $"pbsdk_trades_{today}.jsonl");
            var obPath = Path.Combine(dataDir, $"orderbook_{today}.jsonl");
            LayerEventsReceiver.TradeWriter = OpenAppend(logPath);
            LayerEventsReceiver.OrderBookWriter = OpenAppend(obPath);
            Console.WriteLine($"[OK] tape: {logPath}");
            Console.WriteLine($"[OK] orderbook: {obPath}");

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
                if (slot == null) continue;

                var rec = new DispatcherSlotEventsReceiver(slot);
                rec.Log += m => Console.WriteLine(m);
                rec.CanCreateLayers += OnCanCreateLayers;
                slot.PublicServerInfo = si;
                slot.Account = Account;
                slot.Password = Password;
                Console.WriteLine($"[connect] {si.StockLongName}");
                await slot.ConnectAsync(CancellationToken.None);
            }

            Console.WriteLine("[6/6] waiting. Press any key to stop.");
            Console.ReadKey(true);
            Console.WriteLine("stopping...");
            LayerEventsReceiver.TradeWriter?.Close();
            LayerEventsReceiver.OrderBookWriter?.Close();
            await _sdk.StopAsync();
        }

        static void OnCanCreateLayers(DispatcherSlotEventsReceiver sender)
        {
            Console.WriteLine($"[OK] contracts: {sender.Slot.PublicServerInfo?.StockLongName}");
            foreach (var aliases in TickerAliases)
            {
                IContractInfo info = null;
                string used = null;
                foreach (var a in aliases)
                {
                    info = sender.Slot.ContractsTable.SearchContractByID(a);
                    if (info != null) { used = a; break; }
                }
                if (info == null) { Console.WriteLine($"  [!] not found {aliases[0]}"); continue; }
                var ld = sender.Slot.InvokeLayerAsync(info, CancellationToken.None).GetAwaiter().GetResult();
                ILayer layer = ld.layer;
                if (layer == null) continue;
                var lr = new LayerEventsReceiver(layer);
                lr.Log += m => Console.WriteLine(m);
                if (ld.isnew)
                    sender.Slot.InvokeLayerEndAsync(sender, layer, CancellationToken.None).GetAwaiter().GetResult();
                Console.WriteLine($"  [+] subscribed {used}");
            }
        }
    }
}