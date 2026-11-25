# -*- coding: utf-8 -*-
# Оверлей v2.8: полоска - всегда 10 равных ячеек поверх Привода.
# В ячейке несколько тикеров (группа столбца). В пульте каждое окошко ячейки -
# три строки, тикеры в столбик; в разметке на полоске тикеры тоже в столбик.
# Редактирование: окошки в пульте ИЛИ двойной клик по ячейке полоски.
# Робот проснулся у любого тикера группы - ячейка вспыхивает и показывает,
# какой именно тикер жив (тикер, отсчёт в целых секундах, лот удара).
# Цвет = направление: зелёный покупка, красный продажа. Двойной робот - две строки.
# CD=0 без удара -> зачёркивание и пара миганий, дальше тускло; удар пришёл -> цикл;
# робот умер -> ячейка скрыта.
# Двигание/растягивание - СТРЕЛКАМИ из пульта: стрелки - 10 px, Shift - 1 px,
# Ctrl+влево/вправо - ширина, Ctrl+вверх/вниз - высота.
# Пульт: F9 - показать/скрыть. Всё хранится в data\overlay_layout.json.
import json, os, time, ctypes
import tkinter as tk

ROOT   = r"C:\Users\Public\MI_CODES\dpNEW"
DATA   = os.path.join(ROOT, "data")
LIVE   = os.path.join(DATA, "entities_live.json")
LAYOUT = os.path.join(DATA, "overlay_layout.json")

NSLOTS       = 10
MAX_PER_CELL = 5
STRIP_H      = 46
GREEN, RED         = "#3fb950", "#f85149"
GREEN_DIM, RED_DIM = "#1d4429", "#5a1d21"
CELL_BG  = "#10151c"
IDLE_BG  = "#0b0f14"
IDLE_FG  = "#3a4350"
SEL_BG   = "#2a3542"
TRANS    = "#010101"
FONT      = ("Consolas", 11, "bold")
FONT_ST   = ("Consolas", 11, "bold", "overstrike")
FONT_GUIDE = ("Consolas", 10, "bold")

def load_live():
    try:
        with open(LIVE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"updated_ms": 0, "clusters": []}

def parse_cell_text(raw):
    """Тикеры одной ячейки: разделитель - любой пробел/перевод строки."""
    return [t.upper() for t in (raw or "").split() if t][:MAX_PER_CELL]

def load_layout():
    try:
        with open(LAYOUT, "r", encoding="utf-8") as f:
            lay = json.load(f)
    except (OSError, ValueError):
        lay = {}
    for s in lay.get("strips", []):
        t = s.get("tickers")
        cells = []
        if isinstance(t, list):
            for item in t:
                if isinstance(item, list):
                    cells.append([str(x).upper() for x in item if str(x).strip()])
                elif isinstance(item, str) and item.strip():
                    cells.append(parse_cell_text(item))
        flat = [x for x in (t if isinstance(t, list) else [])
                if isinstance(x, str) and x.strip() and ":" not in x]
        if flat and not cells:
            cells = [parse_cell_text(x) for x in flat]
        cells = (cells + [[] for _ in range(NSLOTS)])[:NSLOTS]
        s["tickers"] = cells
    lay.setdefault("alpha", 0.85)
    lay.setdefault("guide", False)
    lay.setdefault("sel", 0)
    strips = lay.setdefault("strips", [])
    while len(strips) < 2:
        strips.append({"visible": len(strips) == 0, "x": 80,
                       "y": 100 + 60 * len(strips), "w": 1200, "h": STRIP_H,
                       "tickers": [[] for _ in range(NSLOTS)]})
    return lay

def save_layout(lay):
    try:
        with open(LAYOUT, "w", encoding="utf-8") as f:
            json.dump(lay, f, ensure_ascii=False)
    except OSError:
        pass

class Strip(tk.Toplevel):
    def __init__(self, app, idx, cfg):
        super().__init__(app.root)
        self.app = app
        self.idx = idx
        self.cells_grp = [list(c or []) for c in
                          (cfg.get("tickers") or [[] for _ in range(NSLOTS)])]
        self.cells_grp = (self.cells_grp + [[] for _ in range(NSLOTS)])[:NSLOTS]
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=TRANS)
        self.attributes("-transparentcolor", TRANS)
        x, y = cfg.get("x", 80), cfg.get("y", 100)
        w, h = cfg.get("w", 1200), cfg.get("h", STRIP_H)
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.cells = []
        for i in range(NSLOTS):
            buy  = tk.Label(self, font=FONT, anchor="w", bg=CELL_BG, fg=GREEN)
            sell = tk.Label(self, font=FONT, anchor="w", bg=CELL_BG, fg=RED)
            for lab in (buy, sell):
                lab.bind("<ButtonPress-1>", self._press)
                lab.bind("<B1-Motion>", self._drag)
                lab.bind("<Double-Button-1>",
                         lambda e, ci=i, si=idx: self.app.open_cell_editor(si, ci))
            self.cells.append([buy, sell])
        # Отдельные подписи для режима "Разметка": тикеры в столбик на всю ячейку
        self.guides = []
        for i in range(NSLOTS):
            g = tk.Label(self, font=FONT_GUIDE, anchor="nw", justify="left",
                         bg=IDLE_BG, fg=IDLE_FG)
            g.bind("<Double-Button-1>",
                   lambda e, ci=i, si=idx: self.app.open_cell_editor(si, ci))
            self.guides.append(g)
        self.bind("<Configure>", lambda e: self.place_cells())
        self.place_cells()
        if not cfg.get("visible", True):
            self.withdraw()

    def place_cells(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        cw, ch = w / NSLOTS, h / 2.0
        for i, (buy, sell) in enumerate(self.cells):
            buy.place(x=int(i*cw), y=0, width=int(cw), height=int(ch))
            sell.place(x=int(i*cw), y=int(ch), width=int(cw), height=h-int(ch))

    def _press(self, e):
        self._dx, self._dy = e.x, e.y

    def _drag(self, e):
        nx = self.winfo_x() + e.x - self._dx
        ny = self.winfo_y() + e.y - self._dy
        self.geometry("+%d+%d" % (nx, ny))
        self.app.schedule_save()

    def move_by(self, dx, dy):
        self.geometry("+%d+%d" % (self.winfo_x()+dx, self.winfo_y()+dy))
        self.app.schedule_save()

    def resize_by(self, dw, dh):
        nw = max(60, self.winfo_width()+dw)
        nh = max(20, self.winfo_height()+dh)
        self.geometry("%dx%d" % (nw, nh))
        self.app.schedule_save()

    def apply_alpha(self, a):
        try:
            self.attributes("-alpha", a)
        except tk.TclError:
            pass

    def cfg_state(self):
        return {"visible": bool(self.winfo_viewable()),
                "x": self.winfo_x(), "y": self.winfo_y(),
                "w": self.winfo_width(), "h": self.winfo_height(),
                "tickers": self.cells_grp}

class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Оверлеи - пульт (F9)")
        self.root.geometry("760x560")
        self.root.attributes("-topmost", True)
        self.lay = load_layout()
        self.strips = [Strip(self, i, self.lay["strips"][i]) for i in range(2)]
        self.alpha = float(self.lay.get("alpha", 0.85))
        for s in self.strips:
            s.apply_alpha(self.alpha)
        self.state = {}
        self._save_job = None
        self._hk = False
        self._kprev = set()
        self._editor = None
        self.build_panel()
        self.refresh()
        self.hotkey_loop()
        self.key_loop()
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.root.mainloop()

    def build_panel(self):
        pad = {"padx": 8, "pady": 4}
        self.vis = []
        for i, s in enumerate(self.strips):
            v = tk.BooleanVar(value=bool(self.lay["strips"][i].get("visible", True)))
            cb = tk.Checkbutton(self.root, text="Полоска %d видима" % (i+1),
                                variable=v,
                                command=lambda i=i, v=v: self.on_vis(i, v))
            cb.grid(row=i, column=0, sticky="w", **pad)
            self.vis.append(v)
        tk.Label(self.root, text="Активная полоска (управление стрелками):") \
          .grid(row=4, column=0, sticky="w", **pad)
        self.sel_var = tk.IntVar(value=int(self.lay.get("sel", 0)))
        f = tk.Frame(self.root)
        f.grid(row=5, column=0, sticky="w", **pad)
        for txt, val in (("Полоска 1", 0), ("Полоска 2", 1), ("нет", -1)):
            tk.Radiobutton(f, text=txt, variable=self.sel_var, value=val,
                           command=self.on_sel).pack(side="left", padx=6)
        # Десять окошек-столбиков на каждую полоску: до 3 тикеров, по одному на строку
        self.cell_boxes = []
        for i, s in enumerate(self.strips):
            tk.Label(self.root,
                     text="Полоска %d, ячейки 1-10 (тикеры в столбик):" % (i+1)) \
              .grid(row=6+i*2, column=0, sticky="w", **pad)
            row = tk.Frame(self.root)
            row.grid(row=7+i*2, column=0, sticky="w", **pad)
            boxes = []
            for j in range(NSLOTS):
                b = tk.Text(row, width=9, height=3, font=("Consolas", 10, "bold"),
                            wrap="none")
                b.insert("1.0", "\n".join(s.cells_grp[j]))
                b.grid(row=0, column=j, padx=1)
                b.bind("<FocusOut>", lambda ev, si=i: self.on_cells(si))
                boxes.append(b)
            self.cell_boxes.append(boxes)
        self.guide_var = tk.BooleanVar(value=bool(self.lay.get("guide", False)))
        tk.Checkbutton(self.root, text="Разметка (видны все ячейки, для подгонки)",
                       variable=self.guide_var, command=self.on_guide) \
          .grid(row=10, column=0, sticky="w", **pad)
        tk.Label(self.root, text="Прозрачность полосок:") \
          .grid(row=11, column=0, sticky="w", **pad)
        sc = tk.Scale(self.root, from_=0.2, to=1.0, resolution=0.05,
                      orient="horizontal", length=300, command=self.on_alpha)
        sc.set(self.alpha)
        sc.grid(row=12, column=0, sticky="we", **pad)
        tk.Label(self.root,
                 text="F9 - показать/скрыть пульт.\n"
                      "Активную полоску двигают СТРЕЛКИ (по 10 px),\n"
                      "Shift+стрелки - по 1 px,\n"
                      "Ctrl+стрелки - размер (влево/вправо ширина, вверх/вниз высота).\n"
                      "Правка тикеров: окошки здесь ИЛИ двойной клик по ячейке полоски.\n"
                      "Настройки сохраняются автоматически.") \
          .grid(row=13, column=0, sticky="w", **pad)
        self.on_sel()

    def on_cells(self, si):
        grp = []
        for b in self.cell_boxes[si]:
            grp.append(parse_cell_text(b.get("1.0", "end")))
        self.strips[si].cells_grp = grp
        self.schedule_save()

    def open_cell_editor(self, si, ci):
        if self._editor is not None and self._editor.winfo_exists():
            self._editor.destroy()
        top = tk.Toplevel(self.root)
        self._editor = top
        top.title("Ячейка %d - полоска %d" % (ci+1, si+1))
        top.attributes("-topmost", True)
        top.resizable(False, False)
        tk.Label(top, text="Тикеры ячейки %d полоски %d (по одному в строке):"
                 % (ci+1, si+1)).pack(padx=10, pady=(10, 2))
        e = tk.Entry(top, width=34, font=("Consolas", 11, "bold"))
        e.insert(0, " ".join(self.strips[si].cells_grp[ci]))
        e.pack(padx=10, pady=4)
        def apply(_=None):
            toks = parse_cell_text(e.get())
            self.strips[si].cells_grp[ci] = toks
            b = self.cell_boxes[si][ci]
            b.delete("1.0", "end")
            b.insert("1.0", "\n".join(toks))
            self.schedule_save()
            top.destroy()
        e.bind("<Return>", apply)
        e.bind("<FocusOut>", apply)
        tk.Button(top, text="Готово", command=apply).pack(pady=(2, 10))
        e.focus_set()

    def on_sel(self):
        self.lay["sel"] = self.sel_var.get()
        for i, st in enumerate(self.strips):
            for g in st.guides:
                if st.idx == self.sel_var.get():
                    pass
        self.schedule_save()

    def on_guide(self):
        self.lay["guide"] = self.guide_var.get()
        self.schedule_save()

    def on_vis(self, i, v):
        if v.get():
            self.strips[i].deiconify()
            self.strips[i].place_cells()
        else:
            self.strips[i].withdraw()
            for g in self.strips[i].guides:
                g.place_forget()
        self.schedule_save()

    def on_alpha(self, val):
        self.alpha = float(val)
        for s in self.strips:
            s.apply_alpha(self.alpha)
        self.schedule_save()

    def schedule_save(self):
        if self._save_job:
            return
        self._save_job = self.root.after(800, self.do_save)

    def do_save(self):
        self._save_job = None
        lay = {"alpha": self.alpha,
               "guide": self.guide_var.get(),
               "sel": self.sel_var.get(),
               "strips": [s.cfg_state() for s in self.strips]}
        save_layout(lay)

    def quit_app(self):
        self.do_save()
        self.root.destroy()

    def hotkey_loop(self):
        u = ctypes.windll.user32
        combo = (u.GetAsyncKeyState(0x78) & 0x8000)   # F9
        if combo and not self._hk:
            self._hk = True
            if self.root.state() in ("withdrawn", "iconic"):
                self.root.deiconify()
                self.root.lift()
            else:
                self.root.withdraw()
        elif not combo:
            self._hk = False
        self.root.after(150, self.hotkey_loop)

    def key_loop(self):
        u = ctypes.windll.user32
        def down(vk):
            return bool(u.GetAsyncKeyState(vk) & 0x8000)
        keys = {0x25: "L", 0x26: "U", 0x27: "R", 0x28: "D"}
        pressed = {vk for vk in keys if down(vk)}
        new = pressed - self._kprev
        self._kprev = pressed
        sel = self.sel_var.get()
        if sel in (0, 1) and new:
            focus = self.root.focus_get()
            if isinstance(focus, (tk.Entry, tk.Text)):
                new = set()
        if sel in (0, 1) and new:
            s = self.strips[sel]
            fine = down(0x10)      # Shift
            ctrl = down(0x11)      # Ctrl
            step = 1 if fine else 10
            for vk in new:
                if ctrl:
                    if vk == 0x25: s.resize_by(-step, 0)
                    elif vk == 0x27: s.resize_by(step, 0)
                    elif vk == 0x26: s.resize_by(0, -step)
                    elif vk == 0x28: s.resize_by(0, step)
                else:
                    if vk == 0x25: s.move_by(-step, 0)
                    elif vk == 0x27: s.move_by(step, 0)
                    elif vk == 0x26: s.move_by(0, -step)
                    elif vk == 0x28: s.move_by(0, step)
        self.root.after(80, self.key_loop)

    def refresh(self):
        data = load_live()
        base = data.get("updated_ms") or 0
        by = {}
        for c in data.get("clusters") or []:
            by[(c["symbol"], c["side"])] = c
        now = time.perf_counter()
        guide = self.guide_var.get()
        sel = self.sel_var.get()
        for si, strip in enumerate(self.strips):
            for i in range(NSLOTS):
                grp = strip.cells_grp[i]
                for side, cell in (("buy", strip.cells[i][0]),
                                   ("sell", strip.cells[i][1])):
                    key = (si, i, side)
                    best = None
                    if grp:
                        cands = [by[(s, side)] for s in grp if (s, side) in by]
                        if cands:
                            best = min(cands, key=lambda c: c.get("next_ms", 0))
                    if best is None:
                        if key in self.state:
                            del self.state[key]
                        self.idle_cell(strip, i, cell, side, grp, guide, sel)
                        continue
                    st = self.state.get(key)
                    if st is None or st.get("sym") != best.get("symbol"):
                        st = {"sym": best.get("symbol"),
                              "next": best["next_ms"], "strike": False,
                              "strike_t": 0.0,
                              "cd0": max(0.0, (best["next_ms"] - base) / 1000.0),
                              "t0": now, "text": ""}
                        self.state[key] = st
                    else:
                        if best["next_ms"] > st["next"]:
                            st["next"] = best["next_ms"]
                            st["strike"] = False
                            st["cd0"] = max(0.0, (best["next_ms"] - base) / 1000.0)
                            st["t0"] = now
                    if not st["strike"] and st["cd0"] <= 0:
                        st["strike"] = True
                        st["strike_t"] = now
                    qty = best.get("lot_med")
                    if qty is None:
                        qty = best.get("avg_lot", 0)
                    st["text"] = "%s %d" % (st["sym"][:9], int(round(qty)))
                    if strip.guides[i].winfo_ismapped():
                        strip.guides[i].place_forget()
                    self.paint(strip, i, cell, side, st)
        self.root.after(400, self.refresh)

    def idle_cell(self, strip, i, cell, side, grp, guide, sel):
        row = 0 if side == "buy" else 1
        if guide:
            g = strip.guides[i]
            bg = SEL_BG if strip.idx == sel else IDLE_BG
            if grp:
                text = "\n".join(grp)
            else:
                text = str(i + 1)
            if row == 1:
                text = ""
            g.config(fg=IDLE_FG, bg=bg, text=text)
            w, h = strip.winfo_width(), strip.winfo_height()
            if w < 10 or h < 10:
                return
            cw = w / NSLOTS
            if not g.winfo_ismapped():
                g.place(x=int(i*cw), y=0, width=int(cw), height=h)
        else:
            if cell.winfo_ismapped():
                cell.place_forget()

    def paint(self, strip, i, cell, side, st):
        cd = max(0, int(round(st["cd0"] - (time.perf_counter() - st["t0"]))))
        if st["strike"]:
            cd = 0
        cell.config(text="%s %d" % (st["text"], cd))
        bright = GREEN if side == "buy" else RED
        dim    = GREEN_DIM if side == "buy" else RED_DIM
        if st["strike"]:
            el = time.perf_counter() - st["strike_t"]
            on = (el < 1.6) and (int(el / 0.4) % 2 == 0)
            cell.config(font=FONT_ST, fg=(bright if on else dim), bg=CELL_BG)
        else:
            cell.config(font=FONT, fg=bright, bg=CELL_BG)
        w, h = strip.winfo_width(), strip.winfo_height()
        if w < 10 or h < 10:
            return
        row = 0 if side == "buy" else 1
        cw, ch = w / NSLOTS, h / 2.0
        if not cell.winfo_ismapped():
            cell.place(x=int(i*cw), y=int(row*ch), width=int(cw), height=int(ch))

if __name__ == "__main__":
    print("Оверлей v2.8: 10 ячеек; в пульте тикеры в столбик, по 3 строки на ячейку.")
    print("Правка: окошки в пульте ИЛИ двойной клик по ячейке полоски.")
    App()
