using System;
using System.IO;
using System.Text;
using System.Globalization;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Threading;
using System.Threading.Tasks;
using ICore;

namespace PbSdkConsole
{
    internal class LayerEventsReceiver : ILayerEventsReceiver
    {
        public LayerEventsReceiver(ILayer layer)
        {
            _layer = layer ?? throw new ArgumentNullException(nameof(_layer));
            _layer.AddLayerEventsReceiverAsync(this, CancellationToken.None).GetAwaiter().GetResult();
        }

        readonly ILayer _layer;
        public ILayer Layer => _layer;
        public event Action<string> Log;
        readonly LayerEventsReceiverSubscribtions _layerSubscriptions = LayerEventsReceiverSubscribtions.Assets | LayerEventsReceiverSubscribtions.OrderStates | LayerEventsReceiverSubscribtions.OrderStock | LayerEventsReceiverSubscribtions.Position | LayerEventsReceiverSubscribtions.Replications | LayerEventsReceiverSubscribtions.Trades | LayerEventsReceiverSubscribtions.Ticks;
        ContractOrderStock _lastStock;

        public static StreamWriter? TradeWriter { get; set; }
        public static StreamWriter? OrderBookWriter { get; set; }
        public static readonly object WriteLock = new object();
        static readonly Dictionary<string, long> _obLast = new Dictionary<string, long>();
        const int ObIntervalMs = 500;
        const int ObDepth = 10;

        void OnLog(string message) { Log?.Invoke($"Layer ({_layer.ContractInfo.Symbol}): {message}"); }
        public LayerEventsReceiverSubscribtions LayerSubscriptions => _layerSubscriptions;
        public Task OnLayerDestroyedAsync(ILayer sender, CancellationToken cancellationToken) => Task.CompletedTask;
        public void OnReplicationBegin(ILayer sender) { OnLog("OnReplicationBegin"); }
        public void OnReplicationEnd(ILayer sender) { OnLog("OnReplicationEnd"); }
        public void OnTrade(ILayer sender, TradeData data) { OnLog($"OnTrade: Price={data.Price}; Amount={data.Amount}"); }
        public void OnClosingTrade(ILayer sender, ClosingTradeData data) { OnLog("OnClosingTrade"); }
        public void OnTradesPosition(ILayer sender, ITradesPositionData data) { }
        public void OnPositionUpdate(ILayer sender, decimal amount, double price) { OnLog($"OnPositionUpdate: Price={price}; Amount={amount}"); }
        public void OnLimitOrdersChanged(ILayer sender, ReadOnlyCollection<LimitOrder> list) { OnLog($"OnLimitOrdersChanged: Count={list.Count}"); }
        public void OnLimitOrderOpFailed(ILayer sender, int code, string msg) { OnLog($"OnLimitOrderOpFailed: Code={code}; Message={msg}"); }
        public void OnLimitOrderPingReceived(ILayer sender, long ms) { }
        public void OnStopOrdersChanged(ILayer sender, ReadOnlyCollection<StopOrder> list) { OnLog($"OnStopOrdersChanged: Count={list.Count}"); }
        public void OnStopOrderOpFailed(ILayer sender, int code, string msg) { OnLog($"OnStopOrderOpFailed: Code={code}; Message={msg}"); }
        public void OnStopOrderPingReceived(ILayer sender, long ms) { }

        public void OnStockUpdate(ILayer sender, ContractOrderStock stock)
        {
            _lastStock = stock;
            if (OrderBookWriter == null) return;
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            string sym = _layer.ContractInfo.Symbol;
            lock (WriteLock)
            {
                if (_obLast.TryGetValue(sym, out var last) && now - last < ObIntervalMs) return;
                _obLast[sym] = now;
            }
            double bb = 0, ba = 0;
            var bids = new StringBuilder();
            var asks = new StringBuilder();
            var inv = CultureInfo.InvariantCulture;
            lock (stock)
            {
                if (stock.IsDisposed) return;
                bb = stock.BestBid; ba = stock.BestAsk;
                int n = 0;
                foreach (var row in stock.Bids)
                {
                    if (n++ >= ObDepth) break;
                    if (bids.Length > 0) bids.Append(',');
                    bids.Append('[').Append(Convert.ToDouble(row.Price).ToString(inv)).Append(',').Append(Convert.ToDouble(row.Amount).ToString(inv)).Append(']');
                }
                n = 0;
                foreach (var row in stock.Asks)
                {
                    if (n++ >= ObDepth) break;
                    if (asks.Length > 0) asks.Append(',');
                    asks.Append('[').Append(Convert.ToDouble(row.Price).ToString(inv)).Append(',').Append(Convert.ToDouble(row.Amount).ToString(inv)).Append(']');
                }
            }
            string json = $"{{\"symbol\": \"{sym}\", \"timestamp\": {now}, \"best_bid\": {bb.ToString(inv)}, \"best_ask\": {ba.ToString(inv)}, \"bids\": [{bids}], \"asks\": [{asks}]}}";
            lock (WriteLock) { OrderBookWriter.WriteLine(json); }
        }

        static string GetSide(TickGraphicData t)
        {
            var v = t.GetType().GetProperty("Direction")?.GetValue(t);
            var s = Convert.ToString(v)?.ToLowerInvariant() ?? "";
            return s.Contains("buy") ? "buy" : s.Contains("sell") ? "sell" : s;
        }
        static long GetTs(TickGraphicData t)
        {
            var v = t.GetType().GetProperty("UpdateDateTime")?.GetValue(t);
            if (v is DateTime dt) return new DateTimeOffset(dt).ToUnixTimeMilliseconds();
            return DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        }

        public void OnTick(ILayer sender, TickGraphicData tick)
        {
            if (TradeWriter != null)
            {
                var inv = CultureInfo.InvariantCulture;
                string side = GetSide(tick);
                long ts = GetTs(tick);
                string price = tick.Price.ToString(inv);
                string qty = tick.Amount.ToString(inv);
                string json = $"{{\"symbol\": \"{_layer.ContractInfo.Symbol}\", \"side\": \"{side}\", \"qty\": {qty}, \"price\": {price}, \"timestamp\": {ts}}}";
                lock (WriteLock) { TradeWriter.WriteLine(json); }
            }
        }

        public void OnTick(ILayer sender, List<TickGraphicData> ticks) { foreach (var t in ticks) OnTick(sender, t); }
        public void OnTick_DarkPool(ILayer sender, TickGraphicData tick) { }
        public void OnWatchlistUpdate(ILayer sender, IWatchlist data) { }
        public void OnCandlesReceived(ILayer sender, CandleTimeframe timeframe, List<BaseTicksCandle> candles) { }
        public void OnClustersReceived(ILayer sender, CandleTimeframe timeframe, List<TickGraphicData> clusters) { }
        public Task OnContractExpiredAsync(ILayer sender, IContractInfo current, IContractInfo next) => Task.CompletedTask;
    }
}