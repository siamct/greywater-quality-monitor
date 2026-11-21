"""Greywater Filtration Monitoring System - desktop dashboard.

Reads the Pico over USB serial and shows intake vs filtered readings, the
SAFE / UNSAFE status, OLED-style bars, live graphs, the rig topology, the
Pico's alerts (the same events the buzzer beeps for), and lets you open /
close the valve. Everything gets logged to CSV once a second.

Modes:
    LIVE    real data from the Pico
    NORMAL  made-up clean water   (demo, banner on screen, logged as NORMAL)
    CONTAM  made-up dirty water   (demo)
    CALIB   sliders for the filtered pH / turbidity, to check the safety logic

    pip install -r gui/requirements.txt
    python gui/dashboard.py
"""

import csv
import queue
import random
import threading
import time
import tkinter as tk
from datetime import datetime

import customtkinter as ctk
import serial
import serial.tools.list_ports
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

# --- Configuration ---
LOG_FILE = "differential_filtration_log.csv"
LOG_EVERY_S = 1.0
BAUD = 115200
PICO_VID = 0x2E8A                      # Raspberry Pi USB vendor id
MAX_POINTS = 50
ALPHA = 0.3                            # EMA smoothing (the Pico already averages 10 samples)

# Same rule as the firmware (PH_SAFE / TURB_SAFE_MAX in firmware/main.py)
PH_SAFE = (6.9, 7.8)
TURB_SAFE_MAX = 10.0
PH_NEAR = 0.2                          # "near limit" warning margin
TURB_NEAR = 2.0

# Firmware calibration, only used to show probe voltages in the demo modes
PH_IN_CAL = (8.8031, -10.4569)
PH_OUT_CAL = (8.8031, -12.3569)

DEMO_TARGETS = {
    "NORMAL": {"ph_in": 8.4, "ph_out": 7.3, "turb_in": 80.0, "turb_out": 4.5, "tds": 280.0},
    "CONTAM": {"ph_in": 5.8, "ph_out": 6.5, "turb_in": 120.0, "turb_out": 15.0, "tds": 550.0},
}

# --- Colours ---
GREEN, RED, AMBER, BLUE = "#10B981", "#EF4444", "#F59E0B", "#3B82F6"
INK, GREY, LINE, PANEL = "#1E293B", "#64748B", "#E2E8F0", "#F4F6F9"
FONT = "Segoe UI"

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")


def find_pico():
    """Prefer the Pico; the old version grabbed the first COM port it could open."""
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        if p.vid == PICO_VID:
            return p.device
    return ports[0].device if ports else None


def parse(line):
    """Pico reading line -> dict. Returns None for text lines (ALERT, Action...)."""
    try:
        f = [float(x) for x in line.split(",")]
    except ValueError:
        return None
    if len(f) < 4:
        return None
    r = {"ph_in": f[0], "ph_out": f[1], "turb_in": f[2], "turb_out": f[3]}
    if len(f) >= 6:
        r["valve"], r["safe"] = bool(f[4]), bool(f[5])
    if len(f) >= 9:
        r["v_ph_in"], r["v_ph_out"], r["v_turb"] = f[6], f[7], f[8]
    return r


class Link(threading.Thread):
    """Background thread for the Pico / demo data. It only talks to the GUI
    through `inbox` (tkinter isn't thread safe - the old version updated
    labels straight from this thread, which is what caused the random freezes)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.inbox = queue.Queue()
        self.outbox = queue.Queue()
        self.mode = "LIVE"
        self.manual = {"ph_out": 7.4, "turb_out": 5.0}
        self.sim = {"ph_in": 7.5, "ph_out": 7.2, "turb_in": 80.0, "turb_out": 4.5, "tds": 320.0}
        self.ser = None
        self.port = None
        self.announce = False
        self.running = True

    def run(self):
        while self.running:
            if self.mode == "LIVE":
                self.read_pico()
                continue
            if self.mode == "CALIB":
                r = {"ph_in": 8.0, "turb_in": 80.0, "tds": 300.0, **self.manual}
                self.inbox.put(("status", "STATUS: CALIBRATION", AMBER))
            else:
                r = self.simulate(DEMO_TARGETS[self.mode])
                self.inbox.put(("status", "STATUS: DEMO DATA", AMBER))
            self.inbox.put(("data", r, self.mode))
            time.sleep(0.05)

    def read_pico(self):
        if self.ser is None:
            port = find_pico()
            try:
                self.ser = serial.Serial(port, BAUD, timeout=0.1) if port else None
            except serial.SerialException:
                self.ser = None
            if self.ser is None:
                self.inbox.put(("status", "PICO NOT FOUND", RED))
                time.sleep(1)
                return
            self.port = port
            self.announce = True
        if self.announce:
            self.announce = False
            self.inbox.put(("status", f"PICO CONNECTED ({self.port})", GREEN))
        try:
            while not self.outbox.empty():
                self.ser.write((self.outbox.get() + "\n").encode())
            line = self.ser.readline().decode("utf-8", errors="ignore").strip()
        except (serial.SerialException, OSError):
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
            self.inbox.put(("status", "PICO DISCONNECTED", RED))
            return
        if line:
            r = parse(line)
            self.inbox.put(("data", r, "LIVE") if r else ("event", line))

    def simulate(self, target):
        # small noise + drift toward target values
        noise = {"ph_in": 0.1, "ph_out": 0.1, "turb_in": 5.0, "turb_out": 1.25, "tds": 5.0}
        for k, n in noise.items():
            self.sim[k] += random.uniform(-n, n) + (target[k] - self.sim[k]) * 0.03
        return dict(self.sim)


class DifferentialFiltrationDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Greywater Filtration Monitoring System")
        self.geometry("1450x920")

        self.link = Link()
        self.sm = None                          # smoothed readings (for display)
        self.raw = None                         # latest reading (for the safety check)
        self.hist = {k: [] for k in ("ph_in", "ph_out", "turb_in", "turb_out")}
        self.valve_wanted = False               # what the button asked for
        self.tick = 0
        self.last_log = 0
        self.was_safe = True                    # for the demo-mode alerts
        self.flip_ticks = 0

        self.init_csv()
        self.setup_ui()
        self.link.start()
        self.update_gui()

    # ==================== CSV ====================
    def init_csv(self):
        try:
            with open(LOG_FILE, "x", newline="") as f:
                csv.writer(f).writerow(["Time", "Mode", "pH_IN", "pH_OUT", "Turb_IN",
                                        "Turb_OUT_est", "TDS", "Valve", "Safe"])
        except FileExistsError:
            pass

    def log_row(self, mode, safe, valve):
        if time.time() - self.last_log < LOG_EVERY_S:      # was writing 20 rows a second
            return
        self.last_log = time.time()
        s = self.sm
        tds = "" if mode == "LIVE" else int(s["tds"])
        try:
            with open(LOG_FILE, "a", newline="") as f:
                csv.writer(f).writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), mode,
                                        f"{s['ph_in']:.2f}", f"{s['ph_out']:.2f}", f"{s['turb_in']:.1f}",
                                        f"{s['turb_out']:.1f}", tds, int(valve), int(safe)])
        except OSError:
            pass

    # ==================== UI ====================
    def setup_ui(self):
        self.grid_columnconfigure(0, weight=0, minsize=440)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.banner = ctk.CTkLabel(self, text="", height=30, corner_radius=6, fg_color="#FEF3C7",
                                   text_color="#92400E", font=(FONT, 13, "bold"))

        # ---------- LEFT: sensors & control ----------
        left = ctk.CTkScrollableFrame(self, width=400, fg_color=PANEL, corner_radius=10, border_width=1, border_color=LINE)
        left.grid(row=1, column=0, padx=15, pady=15, sticky="nsew")
        ctk.CTkLabel(left, text="DIAGNOSTICS", font=(FONT, 18, "bold"), text_color=INK).pack(pady=(10, 8))

        cols = ctk.CTkFrame(left, fg_color="transparent")
        cols.pack(fill="x", padx=6)
        cols.grid_columnconfigure((0, 1), weight=1)
        self.lbl = {}
        for col, (title, colour, border, keys) in enumerate((
                ("RAW INTAKE", "#DC2626", "#FCA5A5", (("ph_in", "pH IN", "#0EA5E9"), ("turb_in", "TURBIDITY IN", "#B45309"))),
                ("FILTERED OUTTAKE", "#059669", "#6EE7B7", (("ph_out", "pH OUT", "#0369A1"), ("turb_out", "TURBIDITY OUT*", "#EC4899"))))):
            box = ctk.CTkFrame(cols, fg_color="#FFFFFF", corner_radius=8, border_width=2, border_color=border)
            box.grid(row=0, column=col, padx=5, pady=5, sticky="nsew")
            ctk.CTkLabel(box, text=title, font=(FONT, 12, "bold"), text_color=colour).pack(pady=5)
            for key, name, c in keys:
                ctk.CTkLabel(box, text=name, font=(FONT, 10, "bold"), text_color=GREY).pack()
                self.lbl[key] = ctk.CTkLabel(box, text="--.--", font=(FONT, 28, "bold"), text_color=c)
                self.lbl[key].pack()
                self.lbl["v_" + key] = ctk.CTkLabel(box, text=" ", font=(FONT, 10), text_color=GREY)
                self.lbl["v_" + key].pack(pady=(0, 6))
        ctk.CTkLabel(left, text="* no turbidity sensor on the outlet yet, estimated from intake",
                     font=(FONT, 10), text_color=GREY).pack()

        tds = ctk.CTkFrame(left, fg_color="#FFFFFF", corner_radius=8, border_width=1, border_color=LINE)
        tds.pack(fill="x", padx=12, pady=8)
        ctk.CTkLabel(tds, text="TOTAL DISSOLVED SOLIDS (TDS)", font=(FONT, 10, "bold"), text_color=GREY).pack(pady=(5, 0))
        self.lbl["tds"] = ctk.CTkLabel(tds, text="---", font=(FONT, 26, "bold"), text_color="#8B5CF6")
        self.lbl["tds"].pack()
        ctk.CTkLabel(tds, text="probe not fitted yet - demo modes only", font=(FONT, 10), text_color=GREY).pack(pady=(0, 5))

        # --- safety status (mirrors the Pico) ---
        self.safety_card = ctk.CTkFrame(left, fg_color="#FFFFFF", corner_radius=8, border_width=3, border_color=GREY)
        self.safety_card.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(self.safety_card, text="WATER QUALITY STATUS", font=(FONT, 10, "bold"), text_color=GREY).pack(pady=(5, 0))
        self.lbl_safety = ctk.CTkLabel(self.safety_card, text="--", font=(FONT, 36, "bold"), text_color=GREY)
        self.lbl_safety.pack()
        self.lbl_reason = ctk.CTkLabel(self.safety_card, text=" ", font=(FONT, 11), text_color=GREY)
        self.lbl_reason.pack(pady=(0, 6))

        # --- OLED style bars (same as the screen on the rig) ---
        oled = ctk.CTkFrame(left, fg_color="#F8FAFC", corner_radius=8, border_width=1, border_color=LINE)
        oled.pack(fill="x", padx=12, pady=(0, 8))
        ctk.CTkLabel(oled, text=f"SAFE ZONE ({PH_SAFE[0]} - {PH_SAFE[1]} pH // 0 - {TURB_SAFE_MAX:g} NTU)",
                     font=(FONT, 10, "bold"), text_color=GREY).pack(pady=(5, 0))
        self.bar_canvas = tk.Canvas(oled, bg="#F8FAFC", highlightthickness=0, height=84)
        self.bar_canvas.pack(fill="x", padx=10, pady=5)

        # --- data source ---
        ctk.CTkLabel(left, text="DATA SOURCE MODE", font=(FONT, 10, "bold"), text_color=GREY).pack(pady=(4, 0))
        self.mode_var = ctk.StringVar(value="LIVE")
        ctk.CTkSegmentedButton(left, values=["LIVE", "NORMAL", "CONTAM", "CALIB"], variable=self.mode_var,
                               command=self.on_mode_change).pack(pady=(5, 8), fill="x", padx=20)

        self.manual_frame = ctk.CTkFrame(left, fg_color="transparent")
        for text, key, lo, hi, start in (("MANUAL pH OUT", "ph_out", 5.0, 9.0, 7.4),
                                         ("MANUAL TURB OUT (NTU)", "turb_out", 0.0, 40.0, 5.0)):
            ctk.CTkLabel(self.manual_frame, text=text, font=(FONT, 10, "bold"), text_color=GREY).pack()
            s = ctk.CTkSlider(self.manual_frame, from_=lo, to=hi,
                              command=lambda v, k=key: self.link.manual.update({k: round(v, 2)}))
            s.set(start)
            s.pack(fill="x", padx=20, pady=(0, 8))

        # --- controls ---
        self.controls = ctk.CTkLabel(left, text="SYSTEM CONTROLS", font=(FONT, 14, "bold"), text_color=INK)
        self.controls.pack(pady=(4, 4))
        self.lbl_conn = ctk.CTkLabel(left, text="PICO STATUS", font=(FONT, 12, "bold"), text_color=RED)
        self.lbl_conn.pack(pady=3)
        self.btn_valve = ctk.CTkButton(left, text="OPEN VALVE", fg_color=BLUE, hover_color="#2563EB",
                                       font=(FONT, 13, "bold"), command=self.toggle_valve, height=40)
        self.btn_valve.pack(pady=5, fill="x", padx=20)
        ctk.CTkLabel(left, text="the Pico still keeps it shut while the water is unsafe",
                     font=(FONT, 10), text_color=GREY).pack(pady=(0, 10))

        # ---------- RIGHT: visuals ----------
        right = ctk.CTkFrame(self, fg_color="#FFFFFF", corner_radius=10, border_width=1, border_color=LINE)
        right.grid(row=1, column=1, padx=(0, 15), pady=15, sticky="nsew")
        right.grid_rowconfigure(0, weight=3)
        right.grid_rowconfigure(1, weight=2)
        right.grid_columnconfigure(0, weight=3)
        right.grid_columnconfigure(1, weight=1)

        graph_frame = ctk.CTkFrame(right, fg_color="transparent")
        graph_frame.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=10, pady=10)
        self.setup_graphs(graph_frame)

        topo = ctk.CTkFrame(right, fg_color="#F8FAFC", corner_radius=8, border_width=1, border_color=LINE)
        topo.grid(row=1, column=0, sticky="nsew", padx=(15, 6), pady=(0, 15))
        ctk.CTkLabel(topo, text="TOPOLOGY", font=(FONT, 12, "bold"), text_color=INK).pack(pady=(8, 0))
        self.sys_canvas = tk.Canvas(topo, bg="#F8FAFC", highlightthickness=0, height=230)
        self.sys_canvas.pack(fill="both", expand=True, padx=10, pady=8)
        self.sys_canvas.bind("<Configure>", lambda e: self.draw_topology())

        alerts = ctk.CTkFrame(right, fg_color="#F8FAFC", corner_radius=8, border_width=1, border_color=LINE)
        alerts.grid(row=1, column=1, sticky="nsew", padx=(6, 15), pady=(0, 15))
        ctk.CTkLabel(alerts, text="PICO ALERTS / BUZZER", font=(FONT, 12, "bold"), text_color=INK).pack(pady=(8, 0))
        self.alert_box = ctk.CTkTextbox(alerts, font=("Consolas", 11), fg_color="#FFFFFF", text_color=INK,
                                        border_width=1, border_color=LINE, wrap="word")
        self.alert_box.pack(fill="both", expand=True, padx=10, pady=8)
        self.alert_box.configure(state="disabled")

    def setup_graphs(self, parent):
        self.fig = Figure(figsize=(9, 4.6), facecolor="#FFFFFF")
        self.ax_ph = self.fig.add_subplot(211)
        self.ax_turb = self.fig.add_subplot(212, sharex=self.ax_ph)
        for ax, label, lo, hi in ((self.ax_ph, "pH", 5, 10), (self.ax_turb, "Turbidity (NTU)", 0, 100)):
            ax.set_xlim(0, MAX_POINTS)
            ax.set_ylim(lo, hi)
            ax.set_facecolor("#F8FAFC")
            ax.tick_params(colors="#475569", labelsize=8)
            ax.grid(True, color=LINE, linestyle="--", linewidth=0.5)
            ax.set_ylabel(label, color=INK, fontsize=10, weight="bold")
        # shade the safe band, same limits as the Pico
        self.ax_ph.axhspan(*PH_SAFE, color=GREEN, alpha=0.12, lw=0, label="safe band")
        self.ax_turb.axhspan(0, TURB_SAFE_MAX, color=GREEN, alpha=0.12, lw=0, label="safe band")
        self.ax_ph.tick_params(labelbottom=False)

        self.line = {
            "ph_in": self.ax_ph.plot([], [], color="#0EA5E9", lw=2.2, label="Intake pH")[0],
            "ph_out": self.ax_ph.plot([], [], color="#0369A1", lw=2.2, ls="--", label="Outtake pH")[0],
            "turb_in": self.ax_turb.plot([], [], color="#B45309", lw=2.2, label="Intake turb")[0],
            "turb_out": self.ax_turb.plot([], [], color="#EC4899", lw=2.2, ls="--", label="Outtake turb (est.)")[0],
        }
        self.ax_ph.legend(loc="upper left", fontsize=8, ncol=3)
        self.ax_turb.legend(loc="upper left", fontsize=8, ncol=3)
        self.fig.tight_layout()
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def draw_topology(self, s=None, safe=True, valve=False):
        """Redrawn to fit the panel (it used fixed x offsets and got cut off on small screens)."""
        c = self.sys_canvas
        c.delete("all")
        w = max(c.winfo_width(), 500)
        y = max(c.winfo_height(), 200) * 0.62
        X = lambda f: f * w
        water, empty = "#38BDF8", "#CBD5E1"

        c.create_rectangle(X(0.01), y - 50, X(0.99), y + 55, fill="#F1F5F9", outline=LINE, dash=(4, 4))
        c.create_line(X(0.03), y, X(0.33), y, width=12, fill=water)
        c.create_line(X(0.53), y, X(0.82), y, width=12, fill=water)
        c.create_line(X(0.86), y, X(0.91), y, width=12, fill=water if valve else empty)

        # 4-stage filter
        c.create_rectangle(X(0.33), y - 42, X(0.53), y + 42, fill="#F8FAFC", outline="#94A3B8", width=2)
        step = (X(0.53) - X(0.33)) / 4
        for i, col in enumerate(("#CBD5E1", "#9CA3AF", "#64748B", "#475569")):
            c.create_rectangle(X(0.33) + i * step + 5, y - 34, X(0.33) + (i + 1) * step - 5, y + 34, fill=col, outline="")
        c.create_text(X(0.43), y + 50, text="4-STAGE FILTER", fill="#475569", font=(FONT, 9, "bold"))

        # valve - red when the Pico has forced it shut
        vcol = BLUE if valve else (RED if not safe else GREY)
        c.create_polygon(X(0.82), y - 20, X(0.82), y + 20, X(0.86), y - 20, X(0.86), y + 20, fill=vcol, outline="")
        c.create_text(X(0.84), y + 32, text="VALVE", fill=GREY, font=(FONT, 9, "bold"))

        c.create_rectangle(X(0.91), y - 36, X(0.975), y + 36, fill="#E2E8F0", outline=GREY, width=2)
        if valve:
            c.create_rectangle(X(0.91) + 3, y + 5, X(0.975) - 3, y + 33, fill=water, outline="")
        c.create_text(X(0.9425), y + 48, text="TANK", fill="#475569", font=(FONT, 9, "bold"))

        live = self.link.mode == "LIVE"

        def node(fx, name, fill, ring, text):
            x = X(fx)
            c.create_line(x, y, x, y - 48, fill=fill, width=3, dash=(2, 2))
            c.create_oval(x - 20, y - 88, x + 20, y - 48, fill="#FFFFFF", outline=ring, width=3)
            c.create_oval(x - 15, y - 83, x + 15, y - 53, fill=fill, outline="")
            c.create_text(x, y - 68, text=name, fill="#FFFFFF", font=(FONT, 8, "bold"))
            c.create_text(x, y - 108, text=text, fill=ring, font=(FONT, 9, "bold"), justify="center")

        def val(key, fmt, vkey=None):
            if not s:
                return "--"
            out = fmt.format(s[key])
            return out + (f"\n({s[vkey]:.2f}V)" if vkey and vkey in s else "")

        node(0.08, "pH", "#FCA5A5", "#DC2626", val("ph_in", "{:.2f}", "v_ph_in"))
        node(0.20, "Turb", "#FCD34D", "#D97706", val("turb_in", "{:.1f}", "v_turb"))
        node(0.60, "pH", "#6EE7B7", "#059669", val("ph_out", "{:.2f}", "v_ph_out"))
        node(0.69, "Turb", "#F9A8D4", "#DB2777", val("turb_out", "{:.1f}*"))
        node(0.78, "TDS", "#C4B5FD", "#7C3AED", "STANDBY" if live or not s else f"{s['tds']:.0f}")

    def draw_bars(self, ph, turb):
        """Two bars like the OLED: where the reading is vs the safe zone."""
        c = self.bar_canvas
        c.delete("all")
        w = max(c.winfo_width(), 300)
        for y, value, lo_v, hi_v, scale, cursor, label in (
                (6, ph, PH_SAFE[0], PH_SAFE[1], (5.0, 9.0), "#0EA5E9", f"pH {ph:.2f}"),
                (46, turb, 0, TURB_SAFE_MAX, (0, 100.0), "#B45309", f"{turb:.1f} NTU")):
            span = scale[1] - scale[0]
            px = lambda v: (min(max(v, scale[0]), scale[1]) - scale[0]) / span * (w - 70)
            c.create_rectangle(0, y, w - 70, y + 20, fill=LINE, outline="")
            c.create_rectangle(px(lo_v), y, px(hi_v), y + 20, fill="#D1FAE5", outline="")
            c.create_line(px(lo_v), y + 22, px(hi_v), y + 22, fill=GREEN, width=2)
            c.create_rectangle(px(value) - 2, y - 2, px(value) + 2, y + 22, fill=cursor, outline="")
            c.create_text(w - 64, y + 10, text=label, anchor="w", fill=INK, font=(FONT, 9, "bold"))

    # ==================== actions ====================
    def on_mode_change(self, value):
        self.link.mode = value
        self.sm = self.raw = None
        for h in self.hist.values():
            h.clear()
        if value == "LIVE":
            self.banner.grid_forget()
            self.lbl_conn.configure(text="PICO STATUS", text_color=GREY)
            self.link.announce = True
        else:
            what = "slider values" if value == "CALIB" else "simulated data"
            self.banner.configure(text=f"{value} MODE - {what}, NOT real sensor readings")
            self.banner.grid(row=0, column=0, columnspan=2, sticky="ew", padx=15, pady=(10, 0))
        if value == "CALIB":
            self.manual_frame.pack(fill="x", pady=5, before=self.controls)
        else:
            self.manual_frame.pack_forget()

    def toggle_valve(self):
        # Old version only changed the button - now it actually tells the Pico
        self.valve_wanted = not self.valve_wanted
        self.link.outbox.put("UNLOCK" if self.valve_wanted else "LOCK")
        if self.valve_wanted:
            self.btn_valve.configure(text="CLOSE VALVE", fg_color="#9CA3AF", hover_color="#6B7280")
        else:
            self.btn_valve.configure(text="OPEN VALVE", fg_color=BLUE, hover_color="#2563EB")

    def add_alert(self, text):
        icon = "🔔 " if text.startswith("ALERT") else ("✔ " if text.startswith("OK") else "• ")
        self.alert_box.configure(state="normal")
        self.alert_box.insert("end", f"{datetime.now():%H:%M:%S}  {icon}{text}\n")
        self.alert_box.see("end")
        self.alert_box.configure(state="disabled")

    # ==================== update loop ====================
    def smooth(self, r):
        self.raw = r
        if self.sm is None:
            self.sm = dict(r)
            return
        for k, v in r.items():
            if isinstance(v, float) and not k.startswith("v_"):
                self.sm[k] = ALPHA * v + (1 - ALPHA) * self.sm[k]
            else:
                self.sm[k] = v

    def check(self, s):
        """Same rule as the Pico. Returns (safe, reason text)."""
        ph, turb = s["ph_out"], s["turb_out"]
        bad = []
        if not PH_SAFE[0] <= ph <= PH_SAFE[1]:
            bad.append(f"pH {'low' if ph < PH_SAFE[0] else 'high'}")
        if turb > TURB_SAFE_MAX:
            bad.append("too cloudy")
        if bad:
            return False, ", ".join(bad)
        near = (ph - PH_SAFE[0] < PH_NEAR or PH_SAFE[1] - ph < PH_NEAR or TURB_SAFE_MAX - turb < TURB_NEAR)
        return True, "near the limit" if near else "all readings in range"

    def update_gui(self):
        mode = self.link.mode
        while not self.link.inbox.empty():
            msg = self.link.inbox.get()
            if msg[0] == "status":
                self.lbl_conn.configure(text=msg[1], text_color=msg[2])
            elif msg[0] == "event":
                self.add_alert(msg[1])
            elif msg[2] == mode:                 # ignore leftovers from the previous mode
                self.smooth(msg[1])

        if self.sm:
            self.tick += 1
            s = self.sm
            # LIVE: check the latest reading (the average lags behind the Pico).
            # Demo: check the smoothed one, otherwise the noise makes alerts flicker.
            safe, reason = self.check(self.raw if mode == "LIVE" else s)
            if mode == "LIVE" and "safe" in s:
                safe = s["safe"]                 # the Pico has the final say
            valve = s["valve"] if "valve" in s else (self.valve_wanted and safe)

            if mode != "LIVE":                   # demo: show what the probe voltage would be
                s["v_ph_in"] = (s["ph_in"] - PH_IN_CAL[1]) / PH_IN_CAL[0]
                s["v_ph_out"] = (s["ph_out"] - PH_OUT_CAL[1]) / PH_OUT_CAL[0]
            # demo alerts, so the alert box does something outside LIVE too.
            # Only counts a change once it has held for 1 s, so noise doesn't spam it.
            if mode != "LIVE":
                self.flip_ticks = self.flip_ticks + 1 if safe != self.was_safe else 0
                if self.flip_ticks >= 10:
                    self.was_safe, self.flip_ticks = safe, 0
                    self.add_alert(("OK: water back in range" if safe else "ALERT: unsafe water, valve closed")
                                   + f"  [{mode}]")

            colour = GREEN if safe else RED
            if safe and reason == "near the limit":
                colour = AMBER
            self.lbl_safety.configure(text="SAFE" if safe else "UNSAFE", text_color=GREEN if safe else RED)
            self.lbl_reason.configure(text=reason, text_color=colour)
            self.safety_card.configure(border_color=colour)

            self.lbl["ph_in"].configure(text=f"{s['ph_in']:.2f}")
            self.lbl["ph_out"].configure(text=f"{s['ph_out']:.2f}")
            self.lbl["turb_in"].configure(text=f"{s['turb_in']:.1f}")
            self.lbl["turb_out"].configure(text=f"{s['turb_out']:.1f}")
            for key, vkey in (("ph_in", "v_ph_in"), ("ph_out", "v_ph_out"), ("turb_in", "v_turb")):
                self.lbl["v_" + key].configure(text=f"{s[vkey]:.3f} V" if vkey in s else " ")
            self.lbl["tds"].configure(text="STANDBY" if mode == "LIVE" else f"{s['tds']:.0f} PPM")

            for k, h in self.hist.items():
                h.append(s[k])
                del h[:-MAX_POINTS]
            self.log_row(mode, safe, valve)

            # heavy drawing only ~3 times a second, keeps the UI smooth
            if self.tick % 3 == 0:
                self.draw_bars(s["ph_out"], s["turb_out"])
                self.draw_topology(s, safe, valve)
                xs = range(len(self.hist["ph_in"]))
                for k, line in self.line.items():
                    line.set_data(xs, self.hist[k])
                top = max(self.hist["turb_in"] + self.hist["turb_out"])
                self.ax_turb.set_ylim(0, max(40, top * 1.2))
                self.canvas.draw_idle()

        self.after(100, self.update_gui)


if __name__ == "__main__":
    app = DifferentialFiltrationDashboard()

    def close():
        app.link.running = False
        app.destroy()

    app.protocol("WM_DELETE_WINDOW", close)
    app.mainloop()
