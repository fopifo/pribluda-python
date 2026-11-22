"""
ai_solution_v1/detect/registry.py
=================================
Реестр детекторов: один объект, который держит детекторы по тикерам,
принимает сделки из ленты и раскладывает их по нужным детекторам.

Роль в архитектуре:
    лента (JSONL/мост) -> DetectorRegistry.feed_batch()/feed()
        -> IntervalDetector[symbol].on_trade()
            -> события (Confirmation/Signal) собираются реестром

Зачем отдельно, а не цикл в оркестраторе:
  - тикер определяется ОДИН раз (dict-lookup), а не перебором активных;
  - unsupported-тикеры отсекаются до создания детекторов (как
    allowed_set в старом backend.py, но без set(keys) на каждый вызов);
  - конфиги тикеров (ticker_settings.json) применяются через
    build_config/overlay один раз при создании;
  - адаптивный min_qty живёт на реестре, а не читает ФАЙЛ с диска
    внутри on_trade (боль старого _compute_adaptive_min_qty);
  - единая точка для пауз (тикер сняли с учёта в середине дня),
    flush() и сбора статистики.

Ограничения сознательные:
  - НЕ потокобезопасен: предполагается один поток-писатель (так
    работают и ленты, и детекторы). Если понадобится — внешний Lock,
    а не подпирание каждой сделки внутри.
  - НЕ знает про GUI и НЕ торгует. Только читает сделки и отдаёт события.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ai_solution_v1.config import (
    DEFAULT_CONFIG,
    DetectorConfig,
    SettingsError,
    build_config,
    overlay,
)
from ai_solution_v1.detect.interval import (
    Confirmation,
    IntervalDetector,
    Signal,
    Snapshot,
    Trade,
    parse_trade,
)

__all__ = [
    "DetectorRegistry",
    "RegistryStats",
    "StatisticsError",
]


class StatisticsError(ValueError):
    """Ошибка настройки реестра (неизвестный тикер, пустой конфиг и т.п.)."""


@dataclass(slots=True)
class RegistryStats:
    """Сводная статистика реестра. Считается без перебора детекторов."""

    lines_seen: int = 0          # строк подано на вход
    lines_bad: int = 0           # строк, не распознанных как сделка
    trades_ignored: int = 0      # валидные, но тикер не отслеживается
    trades_fed: int = 0          # реально отдано детекторам
    confirmations: int = 0
    signals: int = 0
    reported: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "lines_seen": self.lines_seen,
            "lines_bad": self.lines_bad,
            "trades_ignored": self.trades_ignored,
            "trades_fed": self.trades_fed,
            "confirmations": self.confirmations,
            "signals": self.signals,
            "reported": self.reported,
        }


class DetectorRegistry:
    """
    Набор детекторов по тикерам с быстрым диспетчером сделок.

    Пример (без сети, без GUI):
        reg = DetectorRegistry(configs={"SBER": {"min_qty": 10, "min_interval_sec": 2.0},
                                        "GAZP": {}})
        for line in tape_lines:
            reg.feed_line(line)
        for sig in reg.drain_signals():
            ...            # анализ/запись в файл — снаружи

    Основные методы:
        add_ticker / remove_ticker / is_enabled  — управление составом
        feed(trade)                              — одна сделка (Trade)
        feed_dict(raw)                           — одна сделка (dict)
        feed_line(line)                          — одна строка JSONL
        feed_batch(lines)                        — батч строк (быстро)
        drain_events / drain_signals             — забрать накопленное
        snapshot / stats / flush                 — снимок, метрики, закрытие
    """

    __slots__ = (
        "_dets",             # dict[symbol] -> IntervalDetector
        "_configs",          # dict[symbol] -> DetectorConfig (эталон, без изменений)
        "_dispatcher",       # dict[symbol] -> IntervalDetector (то же, что _dets)
        "_allowed",          # set[symbol] — быстрая проверка «свой тикер»
        "_events",           # list накопленных Signal/Confirmation
        "_confirmations",    # list[Confirmation]
        "_reports",          # list[Signal] прошедшие порог качества
        "_auto_min_qty",     # dict[symbol] -> int; 0 = из конфига
        "_stats",
        "_strict",           # bool: ошибка при неизвестном тикере
    )

    def __init__(
        self,
        configs: Optional[Mapping[str, Optional[Mapping[str, Any]]]] = None,
        *,
        allowed: Optional[Iterable[str]] = None,
        tickers: Optional[Iterable[str]] = None,
        strict: bool = False,
    ) -> None:
        """
        Создать реестр.

        configs: symbol -> overrides (dict) либо None (дефолты).
            ЛЮБОЙ ключ в overrides идёт в DetectorConfig.with_overrides —
            опечатка даёт SettingsError сразу, а не через месяц.
        allowed: если задан — детекторы создаются ТОЛЬКО для этих тикеров.
            Позволяет передать configs целиком (86 тикеров) и взять
            для теста срез — 2-3 тикера, без создания мусорных детекторов.
        tickers: синоним allowed для читаемости (allowed=..., tickers=...).
        strict: True — feed() по неизвестному тикеру бросает
            StatisticsError; False (по умолчанию) — сделка тихо
            игнорируется и попадает в stats["trades_ignored"].
        """
        self._dets: Dict[str, IntervalDetector] = {}
        self._configs: Dict[str, DetectorConfig] = {}
        self._dispatcher = self._dets  # алиас по смыслу (диспетчеризация)
        self._allowed: set[str] = set()
        self._events: List[Any] = []
        self._confirmations: List[Confirmation] = []
        self._reports: List[Signal] = []
        self._auto_min_qty: Dict[str, int] = {}
        self._stats = RegistryStats()
        self._strict = bool(strict)

        permitted = allowed if allowed is not None else tickers
        permitted_set: Optional[set[str]] = set(permitted) if permitted is not None else None

        for sym, ov in (configs or {}).items():
            if permitted_set is not None and sym not in permitted_set:
                continue
            self.add_ticker(sym, ov)

        if permitted_set is not None:
            self._allowed = permitted_set

    # ------------------------------------------------------------------
    # Состав реестра
    # ------------------------------------------------------------------
    @property
    def symbols(self) -> Tuple[str, ...]:
        """Отслеживаемые тикеры (отсортировано, стабильно для логов)."""
        return tuple(sorted(self._allowed))

    def __len__(self) -> int:
        return len(self._dets)

    def __contains__(self, symbol: str) -> bool:
        return symbol in self._dets

    def is_enabled(self, symbol: str) -> bool:
        return symbol in self._dets

    def add_ticker(
        self,
        symbol: str,
        overrides: Optional[Mapping[str, Any]] = None,
        *,
        adaptive_min_qty: int = 0,
    ) -> IntervalDetector:
        """
        Создать (или ПЕРЕсоздать) детектор тикера.

        Повторный вызов с тем же символом заменит детектор чистым —
        старые серии этого тикера закрываются и попадают в
        накопленные события (не теряются молча).

        adaptive_min_qty: >0 — открыть тикер с готовым адаптивным
            порогом (см. set_adaptive_min_qty). 0 — брать из конфига.
        """
        if not symbol:
            raise StatisticsError("пустой символ тикера")
        old = self._dets.get(symbol)
        if old is not None:
            self._absorb(symbol, old.flush())
            del self._dets[symbol]

        cfg = build_config(symbol, dict(overrides or {}))
        det = IntervalDetector(symbol, cfg, adaptive_min_qty=int(adaptive_min_qty or 0))
        self._dets[symbol] = det
        self._configs[symbol] = cfg
        self._allowed.add(symbol)
        self._auto_min_qty[symbol] = int(adaptive_min_qty or 0)
        return det

    def remove_ticker(self, symbol: str, *, flush: bool = True) -> None:
        """
        Снять тикер с учёта. flush=True — сначала закрыть его активные
        серии (события попадают в drain_events); False — выбросить.
        """
        det = self._dets.pop(symbol, None)
        self._allowed.discard(symbol)
        self._configs.pop(symbol, None)
        self._auto_min_qty.pop(symbol, None)
        if det is not None and flush:
            self._absorb(symbol, det.flush())

    def get(self, symbol: str) -> Optional[IntervalDetector]:
        """Детектор тикера (прямой доступ — для отладки/метрик)."""
        return self._dets.get(symbol)

    def get_config(self, symbol: str) -> Optional[DetectorConfig]:
        return self._configs.get(symbol)

    def patch_config(self, symbol: str, **kwargs: Any) -> DetectorConfig:
        """
        Изменить пороги тикера НА ХОДУ. Детектор пересоздаётся с новым
        конфигом (активные серии старого закрываются). Возвращает
        новый конфиг.
        """
        cfg = self._configs.get(symbol)
        if cfg is None:
            raise StatisticsError(f"тикер {symbol!r} не отслеживается")
        new_cfg = overlay(cfg, **kwargs)
        old = self._dets.get(symbol)
        if old is not None:
            self._absorb(symbol, old.flush())
            self._dets[symbol] = IntervalDetector(
                symbol, new_cfg, adaptive_min_qty=self._auto_min_qty.get(symbol, 0)
            )
        self._configs[symbol] = new_cfg
        return new_cfg

    def set_adaptive_min_qty(self, symbol: str, value: int) -> None:
        """
        Выставить адаптивный порог объёма тикера БЕЗ пересоздания
        детектора и без чтения файлов. value<=0 — снять (вернуться к
        cfg.min_qty).
        """
        det = self._dets.get(symbol)
        if det is None:
            raise StatisticsError(f"тикер {symbol!r} не отслеживается")
        self._auto_min_qty[symbol] = int(value or 0)
        det.set_adaptive_min_qty(int(value or 0))

    # ------------------------------------------------------------------
    # Подача сделок
    # ------------------------------------------------------------------
    def feed(self, trade: Trade) -> List[Any]:
        """
        Одна сделка (уже нормализованный Trade).
        Возвращает события этой сделки (обычно пусто).
        """
        det = self._dets.get(trade.symbol)
        if det is None:
            if self._strict:
                raise StatisticsError(f"тикер {trade.symbol!r} не отслеживается")
            self._stats.trades_ignored += 1
            return []
        ev = det.on_trade(trade)
        self._stats.trades_fed += 1
        if ev:
            self._absorb(trade.symbol, ev)
        return ev

    def feed_dict(self, raw: Mapping[str, Any]) -> List[Any]:
        """Подать сырой dict (строка из json.loads) без заившейся проверки."""
        self._stats.lines_seen += 1
        t = parse_trade(raw)
        if t is None:
            self._stats.lines_bad += 1
            return []
        return self.feed(t)

    def feed_line(self, line: str) -> List[Any]:
        """Подать одну строку JSONL. Мусорные/пустые строки — пропуск."""
        self._stats.lines_seen += 1
        if not line or not line.strip():
            return []
        try:
            raw = json.loads(line)
        except (ValueError, TypeError):
            self._stats.lines_bad += 1
            return []
        if not isinstance(raw, Mapping):
            self._stats.lines_bad += 1
            return []
        t = parse_trade(raw)
        if t is None:
            self._stats.lines_bad += 1
            return []
        return self.feed(t)

    def feed_batch(self, lines: Iterable[str]) -> int:
        """
        Батч строк JSONL. Возвращает число поданных сделок.

        Отличия от цикла feed_line: локальные ссылки на методы и
        словари (избегаем повторного резолва атрибутов), json.loads
        в локальной переменной, дешёвый отсев по тикеру ДО parse_trade
        (совпадает с backend.py: symbol проверялся первым). События
        не отдаются по одной — заберите их drain_events() после батча.
        """
        loads = json.loads
        dets = self._dets
        st = self._stats
        bad = 0
        fed = 0
        ignored = 0
        for line in lines:
            if not line or not line.strip():
                continue
            st.lines_seen += 1
            try:
                raw = loads(line)
            except (ValueError, TypeError):
                bad += 1
                continue
            sym = raw.get("symbol")
            det = dets.get(sym) if sym else None
            if det is None:
                ignored += 1
                continue
            t = parse_trade(raw)
            if t is None:
                bad += 1
                continue
            ev = det.on_trade(t)
            fed += 1
            if ev:
                self._absorb(sym, ev)
        st.lines_bad += bad
        st.trades_fed += fed
        st.trades_ignored += ignored
        return fed

    # ------------------------------------------------------------------
    # Накопленные события
    # ------------------------------------------------------------------
    def _absorb(self, symbol: str, events: Iterable[Any]) -> None:
        for e in events:
            if isinstance(e, Confirmation):
                self._stats.confirmations += 1
                self._confirmations.append(e)
                if e.reported:
                    self._stats.reported += 1
            elif isinstance(e, Signal):
                self._stats.signals += 1
                if e.reported:
                    self._reports.append(e)
            self._events.append(e)

    def drain_events(self) -> List[Any]:
        """Забрать всё накопленное (Signal + Confirmation), очистить буфер."""
        ev = self._events
        self._events = []
        return ev

    def drain_signals(self) -> List[Signal]:
        """Только закрытые серии."""
        return [e for e in self.drain_events() if isinstance(e, Signal)]

    def drain_confirmations(self) -> List[Confirmation]:
        """Только события подтверждения."""
        return self._take(self._confirmations)

    @staticmethod
    def _take(buf: list) -> list:
        out = list(buf)
        buf.clear()
        return out

    @property
    def reported(self) -> Sequence[Signal]:
        """Все сигналы, прошедшие порог качества (аналог robots_history)."""
        return self._reports

    # ------------------------------------------------------------------
    # Снимок и метрики
    # ------------------------------------------------------------------
    def snapshot(self, now_ms: Optional[int] = None) -> Dict[str, List[Snapshot]]:
        """
        Снимок активных серий по тикерам (форма для агрегатора).
        Пустые тикеры в словарь не попадают.
        """
        out: Dict[str, List[Snapshot]] = {}
        for sym, det in self._dets.items():
            rows = det.snapshot(now_ms)
            if rows:
                out[sym] = rows
        return out

    def tick_ms(self, now_ms: int) -> List[Any]:
        """
        Таймерный тик без ленты: закрыть просроченные серии всех тикеров.
        Вызывать раз в 1-2 секунды (как publisher в старом backend.py).
        """
        out: List[Any] = []
        for sym, det in self._dets.items():
            ev = det.check_overdue(now_ms)
            if ev:
                self._absorb(sym, ev)
                out.extend(ev)
        return out

    @property
    def stats(self) -> RegistryStats:
        return self._stats

    def detector_stats(self) -> Dict[str, Dict[str, int]]:
        return {sym: det.stats for sym, det in self._dets.items()}

    # ------------------------------------------------------------------
    # Закрытие
    # ------------------------------------------------------------------
    def flush(self) -> List[Any]:
        """
        Закрыть все активные серии всех тикеров (конец дня/файла).
        Возвращает события закрытия; они же остаются в drain_events().
        """
        out: List[Any] = []
        for sym, det in self._dets.items():
            ev = det.flush()
            if ev:
                self._absorb(sym, ev)
                out.extend(ev)
        return out

    def clear(self) -> None:
        """Сбросить реестр полностью (новый прогон)."""
        self._dets.clear()
        self._configs.clear()
        self._allowed.clear()
        self._auto_min_qty.clear()
        self._events = []
        self._confirmations = []
        self._reports = []
        self._stats = RegistryStats()
