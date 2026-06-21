from __future__ import annotations

import json
import queue
import threading
from datetime import datetime
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from reg.runtime import ENV_FILE, REGISTER_CONFIG_FILE, SETTINGS_FILE
from reg.run import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_REGISTER_CONFIG,
    check_cloud_and_refill,
    run_local_register_job,
)


class RegGuiApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ChatGPT2API 本地注册工具")
        self.root.geometry("1040x700")
        self.root.minsize(820, 620)
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.busy = False
        self.busy_lock = threading.Lock()
        self.monitoring = False
        self.monitor_stop_event = threading.Event()
        self.runtime_status_var = tk.StringVar(value="空闲")
        self.runtime_hint_var = tk.StringVar(value="可以开始本地注册、导入云端或开启自动补号监控")
        self.monitor_countdown_var = tk.StringVar(value="未启动")
        self.progress_text_var = tk.StringVar(value="等待任务开始")
        self.progress_done_var = tk.StringVar(value="0 / 0")
        self.progress_success_var = tk.StringVar(value="0")
        self.progress_fail_var = tk.StringVar(value="0")
        self.progress_running_var = tk.StringVar(value="0")
        self.output_dir_display_var = tk.StringVar()
        self.accounts_file_display_var = tk.StringVar()
        self.count_thread_display_var = tk.StringVar()
        self.monitor_rule_display_var = tk.StringVar()
        self.layout_mode = "wide"
        self.log_history: list[str] = []

        settings = self._load_settings()
        self.output_dir_var = tk.StringVar(value=str(settings.get("output_dir") or DEFAULT_OUTPUT_DIR))
        self.accounts_file_var = tk.StringVar(value=str(settings.get("accounts_file") or ""))
        self.count_var = tk.StringVar(value=str(settings.get("count") or 20))
        self.threads_var = tk.StringVar(value=str(settings.get("threads") or 3))
        self.server_var = tk.StringVar(value=str(settings.get("server") or ""))
        self.auth_key_var = tk.StringVar(value=str(settings.get("auth_key") or ""))
        self.min_active_var = tk.StringVar(value=str(settings.get("min_active_accounts") or 60))
        self.monitor_interval_var = tk.StringVar(value=str(settings.get("monitor_interval_seconds") or 300))
        self.upload_var = tk.BooleanVar(value=bool(settings.get("upload_to_cloud", True)))
        self.enable_flaresolverr_var = tk.BooleanVar(value=bool(settings.get("enable_flaresolverr", False)))
        self.flaresolverr_url_var = tk.StringVar(value=str(settings.get("flaresolverr_url") or ""))

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(200, self._flush_logs)
        self.root.bind("<Configure>", self._on_window_resize)
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        self.root.bind_all("<Button-4>", self._on_mousewheel_linux)
        self.root.bind_all("<Button-5>", self._on_mousewheel_linux)

    def _load_settings(self) -> dict:
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_settings(self) -> None:
        data = {
            "output_dir": self.output_dir_var.get().strip(),
            "accounts_file": self.accounts_file_var.get().strip(),
            "count": self.count_var.get().strip(),
            "threads": self.threads_var.get().strip(),
            "server": self.server_var.get().strip(),
            "auth_key": self.auth_key_var.get().strip(),
            "min_active_accounts": self.min_active_var.get().strip(),
            "monitor_interval_seconds": self.monitor_interval_var.get().strip(),
            "upload_to_cloud": bool(self.upload_var.get()),
            "enable_flaresolverr": bool(self.enable_flaresolverr_var.get()),
            "flaresolverr_url": self.flaresolverr_url_var.get().strip(),
        }
        SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _configure_styles(self) -> None:
        self.root.configure(bg="#eef3f8")
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure(".", font=("Microsoft YaHei UI", 9))
        style.configure("App.TFrame", background="#eef3f8")
        style.configure("Card.TFrame", background="#ffffff", relief="flat")
        style.configure("Hero.TFrame", background="#16324f")
        style.configure("Section.TLabelframe", background="#ffffff", borderwidth=0, relief="flat")
        style.configure("Section.TLabelframe.Label", background="#ffffff", foreground="#18324b", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("HeroTitle.TLabel", background="#16324f", foreground="#ffffff", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("HeroSub.TLabel", background="#16324f", foreground="#d8e5f2", font=("Microsoft YaHei UI", 9))
        style.configure("Label.TLabel", background="#ffffff", foreground="#294157", font=("Microsoft YaHei UI", 9))
        style.configure("Hint.TLabel", background="#ffffff", foreground="#60758a", font=("Microsoft YaHei UI", 8))
        style.configure("InlineHint.TLabel", background="#ffffff", foreground="#60758a", font=("Microsoft YaHei UI", 8))
        style.configure("Value.TLabel", background="#ffffff", foreground="#10253a", font=("Consolas", 9))
        style.configure("StatusTitle.TLabel", background="#ffffff", foreground="#60758a", font=("Microsoft YaHei UI", 8))
        style.configure("StatusValue.TLabel", background="#ffffff", foreground="#10253a", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("StatusMeta.TLabel", background="#ffffff", foreground="#7990a5", font=("Microsoft YaHei UI", 8))
        style.configure("Progress.Horizontal.TProgressbar", troughcolor="#e8eef5", background="#1f6feb", bordercolor="#e8eef5", lightcolor="#1f6feb", darkcolor="#1f6feb")
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 9, "bold"), padding=(10, 7), background="#1f6feb", foreground="#ffffff", borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#1959bf"), ("disabled", "#b5c7dd")], foreground=[("disabled", "#eff4fa")])
        style.configure("Secondary.TButton", font=("Microsoft YaHei UI", 9), padding=(10, 7), background="#e8eef5", foreground="#1d3954", borderwidth=0)
        style.map("Secondary.TButton", background=[("active", "#d7e3ef"), ("disabled", "#eef3f8")], foreground=[("disabled", "#8aa0b6")])
        style.configure("Danger.TButton", font=("Microsoft YaHei UI", 9), padding=(10, 7), background="#ffe7e5", foreground="#a13d33", borderwidth=0)
        style.map("Danger.TButton", background=[("active", "#ffd7d2"), ("disabled", "#f4eeed")], foreground=[("disabled", "#cda29d")])
        style.configure("Tool.TButton", font=("Microsoft YaHei UI", 8), padding=(8, 6), background="#f3f7fb", foreground="#294157", borderwidth=0)
        style.map("Tool.TButton", background=[("active", "#e4edf7"), ("disabled", "#f5f7fa")], foreground=[("disabled", "#9aaebe")])
        style.configure("App.TEntry", fieldbackground="#f7fafc", foreground="#10253a", bordercolor="#d6e0ea", lightcolor="#d6e0ea", darkcolor="#d6e0ea", padding=6)
        style.map("App.TEntry", bordercolor=[("focus", "#1f6feb")], lightcolor=[("focus", "#1f6feb")], darkcolor=[("focus", "#1f6feb")])
        style.configure("App.TCheckbutton", background="#ffffff", foreground="#294157")

    def _build_ui(self) -> None:
        self._configure_styles()
        scroll_shell = ttk.Frame(self.root, style="App.TFrame")
        scroll_shell.pack(fill=tk.BOTH, expand=True)

        self.scroll_canvas = tk.Canvas(
            scroll_shell,
            bg="#eef3f8",
            highlightthickness=0,
            bd=0,
        )
        self.scroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar = ttk.Scrollbar(scroll_shell, orient=tk.VERTICAL, command=self.scroll_canvas.yview)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.scroll_canvas.configure(yscrollcommand=self.scrollbar.set)

        container = ttk.Frame(self.scroll_canvas, padding=10, style="App.TFrame")
        self.container_window = self.scroll_canvas.create_window((0, 0), window=container, anchor="nw")
        container.bind("<Configure>", self._sync_scroll_region)
        self.scroll_canvas.bind("<Configure>", self._on_canvas_configure)

        hero = ttk.Frame(container, padding=(14, 12), style="Hero.TFrame")
        hero.pack(fill=tk.X)

        left_hero = ttk.Frame(hero, style="Hero.TFrame")
        left_hero.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        title_row = ttk.Frame(left_hero, style="Hero.TFrame")
        title_row.pack(anchor="w", fill=tk.X)
        icon_badge = tk.Canvas(title_row, width=34, height=34, bg="#16324f", highlightthickness=0)
        icon_badge.create_oval(2, 2, 32, 32, fill="#2c73d9", outline="")
        icon_badge.create_text(17, 17, text="R", fill="#ffffff", font=("Segoe UI", 14, "bold"))
        icon_badge.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(title_row, text="本地注册控制台", style="HeroTitle.TLabel").pack(side=tk.LEFT, anchor="center")
        ttk.Label(
            left_hero,
            text="独立运行、本地保存、可按云端阈值自动补号",
            style="HeroSub.TLabel",
        ).pack(anchor="w", pady=(6, 0))

        right_hero = ttk.Frame(hero, style="Hero.TFrame")
        right_hero.pack(side=tk.RIGHT, anchor="e")
        self.hero_status_label = tk.Label(
            right_hero,
            textvariable=self.runtime_status_var,
            font=("Microsoft YaHei UI", 9, "bold"),
            bg="#e9f2ff",
            fg="#1a4b8c",
            padx=10,
            pady=5,
        )
        self.hero_status_label.pack(anchor="e")
        self.hero_hint_label = ttk.Label(right_hero, textvariable=self.runtime_hint_var, style="HeroSub.TLabel")
        self.hero_hint_label.pack(anchor="e", pady=(5, 0))
        ttk.Label(right_hero, textvariable=self.monitor_countdown_var, style="HeroSub.TLabel").pack(anchor="e", pady=(2, 0))

        self.main = ttk.Frame(container, style="App.TFrame", padding=(0, 10, 0, 0))
        self.main.pack(fill=tk.BOTH, expand=True)
        self.main.columnconfigure(0, weight=4)
        self.main.columnconfigure(1, weight=3)
        self.main.rowconfigure(0, weight=1)

        self.left_panel = ttk.Frame(self.main, style="Card.TFrame", padding=12)
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.right_panel = ttk.Frame(self.main, style="Card.TFrame", padding=12)
        self.right_panel.grid(row=0, column=1, sticky="nsew")

        config_section = ttk.LabelFrame(self.left_panel, text="路径与任务参数", style="Section.TLabelframe", padding=10)
        config_section.pack(fill=tk.X)
        ttk.Label(config_section, text=f"注册配置固定读取: {REGISTER_CONFIG_FILE.name}", style="InlineHint.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6)
        )
        ttk.Label(config_section, text=f"环境变量文件: {ENV_FILE.name}", style="InlineHint.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        self._row_path(config_section, 2, "保存目录", self.output_dir_var, self._choose_output_dir)
        self._row_path(config_section, 3, "账号文件", self.accounts_file_var, self._choose_accounts_file)
        self._row_entry(config_section, 4, "注册数量", self.count_var)
        self._row_entry(config_section, 5, "线程数", self.threads_var)
        self._row_entry(config_section, 6, "监控间隔(秒)", self.monitor_interval_var)

        cloud_section = ttk.LabelFrame(self.left_panel, text="云端与风控", style="Section.TLabelframe", padding=10)
        cloud_section.pack(fill=tk.X, pady=(8, 0))
        self._row_entry(cloud_section, 0, "云端地址", self.server_var)
        self._row_entry(cloud_section, 1, "管理员密钥", self.auth_key_var, show="*")
        self._row_entry(cloud_section, 2, "最低有效账号", self.min_active_var)
        self._row_entry(cloud_section, 3, "FlareSolverr URL", self.flaresolverr_url_var)

        options = ttk.LabelFrame(self.left_panel, text="运行选项", style="Section.TLabelframe", padding=10)
        options.pack(fill=tk.X, pady=(8, 0))
        ttk.Checkbutton(options, text="注册完成后直接上传到云端", variable=self.upload_var, style="App.TCheckbutton").pack(anchor="w")
        ttk.Checkbutton(options, text="本地启用 FlareSolverr", variable=self.enable_flaresolverr_var, style="App.TCheckbutton").pack(anchor="w", pady=(6, 0))
        ttk.Label(
            options,
            text="建议本地直连优先，只有明确需要时再单独开启 FlareSolverr。",
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(6, 0))

        actions = ttk.LabelFrame(self.left_panel, text="快捷操作", style="Section.TLabelframe", padding=10)
        actions.pack(fill=tk.X, pady=(8, 0))
        actions.columnconfigure(0, weight=1)
        actions.columnconfigure(1, weight=1)
        actions.columnconfigure(2, weight=1)
        self.save_button = ttk.Button(actions, text="保存配置", command=self._handle_save, style="Secondary.TButton")
        self.register_button = ttk.Button(actions, text="本地注册", command=self._handle_register, style="Primary.TButton")
        self.refill_button = ttk.Button(actions, text="检查云端并补号", command=self._handle_check_and_refill, style="Secondary.TButton")
        self.import_button = ttk.Button(actions, text="只导入账号文件", command=self._handle_import_only, style="Secondary.TButton")
        self.monitor_start_button = ttk.Button(actions, text="开始监控补号", command=self._handle_start_monitor, style="Primary.TButton")
        self.monitor_stop_button = ttk.Button(actions, text="停止监控", command=self._handle_stop_monitor, style="Danger.TButton")
        self.clear_log_button = ttk.Button(actions, text="清空日志", command=self._clear_logs, style="Tool.TButton")
        self.export_log_button = ttk.Button(actions, text="导出日志 .log", command=self._export_logs, style="Tool.TButton")
        self.save_button.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 6))
        self.register_button.grid(row=0, column=1, sticky="ew", padx=3, pady=(0, 6))
        self.refill_button.grid(row=0, column=2, sticky="ew", padx=(6, 0), pady=(0, 6))
        self.import_button.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        self.monitor_start_button.grid(row=1, column=1, sticky="ew", padx=3)
        self.monitor_stop_button.grid(row=1, column=2, sticky="ew", padx=(6, 0))
        self.clear_log_button.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.export_log_button.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        overview = ttk.LabelFrame(self.right_panel, text="运行概览", style="Section.TLabelframe", padding=10)
        overview.pack(fill=tk.X)
        self._bind_display_vars()
        stats = ttk.Frame(overview, style="Card.TFrame")
        stats.pack(fill=tk.X)
        stats.columnconfigure((0, 1, 2), weight=1)
        self._stat_card(stats, 0, 0, "当前状态", self.runtime_status_var, icon="●", accent="#2c73d9")
        self._stat_card(stats, 0, 1, "输出目录", self.output_dir_display_var, value_style="Value.TLabel", icon="⌂", accent="#5f7f99")
        self._stat_card(stats, 0, 2, "默认数量 / 线程", self.count_thread_display_var, icon="#", accent="#5d8d51")
        self._stat_card(stats, 1, 0, "监控阈值", self.monitor_rule_display_var, icon="↻", accent="#a56e25")
        self._stat_card(stats, 1, 1, "下次检查", self.monitor_countdown_var, icon="⏱", accent="#7a5fd0")
        self._stat_card(stats, 1, 2, "账号文件", self.accounts_file_display_var, value_style="Value.TLabel", icon="⇪", accent="#b35f76")

        progress_section = ttk.LabelFrame(self.right_panel, text="任务进度", style="Section.TLabelframe", padding=10)
        progress_section.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(progress_section, textvariable=self.progress_text_var, style="StatusTitle.TLabel").pack(anchor="w")
        self.progress_bar = ttk.Progressbar(progress_section, style="Progress.Horizontal.TProgressbar", mode="determinate", maximum=100, value=0)
        self.progress_bar.pack(fill=tk.X, pady=(8, 6))
        progress_stats = ttk.Frame(progress_section, style="Card.TFrame")
        progress_stats.pack(fill=tk.X)
        progress_stats.columnconfigure((0, 1, 2, 3), weight=1)
        self._mini_stat(progress_stats, 0, "已完成", self.progress_done_var)
        self._mini_stat(progress_stats, 1, "成功", self.progress_success_var)
        self._mini_stat(progress_stats, 2, "失败", self.progress_fail_var)
        self._mini_stat(progress_stats, 3, "运行中", self.progress_running_var)

        tips = ttk.LabelFrame(self.right_panel, text="操作建议", style="Section.TLabelframe", padding=10)
        tips.pack(fill=tk.X, pady=(10, 0))
        for text in (
            "本地注册时会把成功账号和失败记录分别保存到 output 目录。",
            "如果开启循环监控，会按间隔读取云端有效账号数，不够再自动补号。",
            "EXE 运行时默认使用 reg 自己的数据目录，不会覆盖主项目数据。",
        ):
            ttk.Label(tips, text=f"• {text}", style="Hint.TLabel", wraplength=320, justify="left").pack(anchor="w", pady=2)

        log_frame = ttk.LabelFrame(self.right_panel, text="实时日志", style="Section.TLabelframe", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.log_text = ScrolledText(
            log_frame,
            font=("Consolas", 9),
            bg="#0f1b2b",
            fg="#d8e5f2",
            insertbackground="#d8e5f2",
            relief="flat",
            bd=0,
            padx=8,
            pady=8,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(state=tk.DISABLED)
        self._configure_log_tags()
        self._apply_responsive_layout(force=True)
        self._refresh_runtime_state()
        self._refresh_actions_state()

    def _row_path(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, button_command) -> None:
        ttk.Label(parent, text=label, width=10, style="Label.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=variable, style="App.TEntry").grid(row=row, column=1, sticky="ew", pady=3, padx=6)
        ttk.Button(parent, text="选择", command=button_command, style="Tool.TButton").grid(row=row, column=2, pady=4)
        parent.columnconfigure(1, weight=1)

    def _row_entry(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, show: str | None = None) -> None:
        ttk.Label(parent, text=label, width=10, style="Label.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Entry(parent, textvariable=variable, show=show or "", style="App.TEntry")
        entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=2, padx=5)

    @staticmethod
    def _compact_path_text(value: str, keep: int = 22) -> str:
        text = str(value or "").strip()
        if not text:
            return "-"
        path = Path(text)
        name = path.name.strip()
        if name:
            return name if len(name) <= keep else f"...{name[-keep:]}"
        return text if len(text) <= keep else f"...{text[-keep:]}"

    def _bind_display_vars(self) -> None:
        def update_paths(*_args) -> None:
            self.output_dir_display_var.set(self._compact_path_text(self.output_dir_var.get(), 24))
            self.accounts_file_display_var.set(self._compact_path_text(self.accounts_file_var.get(), 24))

        def update_count_thread(*_args) -> None:
            count = self.count_var.get().strip() or "-"
            threads = self.threads_var.get().strip() or "-"
            self.count_thread_display_var.set(f"{count} / {threads}")

        def update_monitor_rule(*_args) -> None:
            threshold = self.min_active_var.get().strip() or "-"
            interval = self.monitor_interval_var.get().strip() or "-"
            self.monitor_rule_display_var.set(f"{threshold} / {interval}s")

        self.output_dir_var.trace_add("write", update_paths)
        self.accounts_file_var.trace_add("write", update_paths)
        self.count_var.trace_add("write", update_count_thread)
        self.threads_var.trace_add("write", update_count_thread)
        self.min_active_var.trace_add("write", update_monitor_rule)
        self.monitor_interval_var.trace_add("write", update_monitor_rule)
        update_paths()
        update_count_thread()
        update_monitor_rule()

    def _stat_card(
        self,
        parent: ttk.Frame,
        row: int,
        column: int,
        title: str,
        variable: tk.StringVar,
        value_style: str = "StatusValue.TLabel",
        icon: str = "",
        accent: str = "#2c73d9",
    ) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=(6, 5))
        card.grid(row=row, column=column, sticky="nsew", padx=2, pady=2)
        header = ttk.Frame(card, style="Card.TFrame")
        header.pack(fill=tk.X)
        icon_label = tk.Label(
            header,
            text=icon,
            font=("Segoe UI Symbol", 8, "bold"),
            bg="#eef5fb",
            fg=accent,
            width=2,
            pady=2,
        )
        icon_label.pack(side=tk.LEFT)
        ttk.Label(header, text=title, style="StatusTitle.TLabel").pack(side=tk.LEFT, padx=(6, 0))
        ttk.Label(card, textvariable=variable, style=value_style, wraplength=110, justify="left").pack(anchor="w", pady=(3, 0))

    def _mini_stat(self, parent: ttk.Frame, column: int, title: str, variable: tk.StringVar) -> None:
        block = ttk.Frame(parent, style="Card.TFrame", padding=(4, 3))
        block.grid(row=0, column=column, sticky="nsew", padx=3, pady=2)
        ttk.Label(block, text=title, style="StatusTitle.TLabel").pack(anchor="w")
        ttk.Label(block, textvariable=variable, style="StatusValue.TLabel").pack(anchor="w", pady=(2, 0))

    def _configure_log_tags(self) -> None:
        self.log_text.tag_configure("info", foreground="#d8e5f2")
        self.log_text.tag_configure("success", foreground="#7ee787")
        self.log_text.tag_configure("warning", foreground="#f2cc60")
        self.log_text.tag_configure("error", foreground="#ff8e8a")
        self.log_text.tag_configure("debug", foreground="#8fbce6")

    def _log_tag(self, text: str) -> str:
        lowered = str(text).lower()
        if any(keyword in lowered for keyword in ("失败", "错误", "error", "exception")):
            return "error"
        if any(keyword in lowered for keyword in ("成功", "完成", "已保存", "已开启")):
            return "success"
        if any(keyword in lowered for keyword in ("跳过", "提示", "warning", "暂停", "拦截")):
            return "warning"
        if any(keyword in lowered for keyword in ("检查", "监控", "启动", "导入", "开始")):
            return "debug"
        return "info"

    def _on_window_resize(self, event) -> None:
        if event.widget is not self.root:
            return
        self._apply_responsive_layout()

    def _sync_scroll_region(self, _event=None) -> None:
        if hasattr(self, "scroll_canvas"):
            self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        if hasattr(self, "container_window"):
            self.scroll_canvas.itemconfigure(self.container_window, width=event.width)

    def _on_mousewheel(self, event) -> None:
        if not hasattr(self, "scroll_canvas"):
            return
        delta = 0
        if getattr(event, "delta", 0):
            delta = int(-event.delta / 120)
        if delta:
            self.scroll_canvas.yview_scroll(delta, "units")

    def _on_mousewheel_linux(self, event) -> None:
        if not hasattr(self, "scroll_canvas"):
            return
        if getattr(event, "num", None) == 4:
            self.scroll_canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            self.scroll_canvas.yview_scroll(1, "units")

    def _apply_responsive_layout(self, force: bool = False) -> None:
        width = max(int(self.root.winfo_width() or 0), int(self.root.winfo_reqwidth() or 0))
        mode = "compact" if width < 980 else "wide"
        if not force and mode == self.layout_mode:
            return
        self.layout_mode = mode
        if mode == "compact":
            self.left_panel.grid_configure(row=0, column=0, padx=0, pady=(0, 10))
            self.right_panel.grid_configure(row=1, column=0, padx=0, pady=0)
            self.main.columnconfigure(0, weight=1)
            self.main.columnconfigure(1, weight=0)
            self.main.rowconfigure(0, weight=0)
            self.main.rowconfigure(1, weight=1)
        else:
            self.left_panel.grid_configure(row=0, column=0, padx=(0, 10), pady=0)
            self.right_panel.grid_configure(row=0, column=1, padx=0, pady=0)
            self.main.columnconfigure(0, weight=4)
            self.main.columnconfigure(1, weight=3)
            self.main.rowconfigure(0, weight=1)
            self.main.rowconfigure(1, weight=0)

    def _choose_config(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if path:
            self.config_path_var.set(path)

    def _choose_output_dir(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.output_dir_var.set(path)

    def _choose_accounts_file(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if path:
            self.accounts_file_var.set(path)

    def _log(self, text: str) -> None:
        self.log_queue.put(text)
        self.log_history.append(text)

    def _flush_logs(self) -> None:
        while True:
            try:
                text = self.log_queue.get_nowait()
            except queue.Empty:
                break
            tag = self._log_tag(text)
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, f"{text}\n", tag)
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        self.root.after(200, self._flush_logs)

    def _clear_logs(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.log_history.clear()

    def _export_logs(self) -> None:
        default_name = f"reg-tool-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        target = filedialog.asksaveasfilename(
            defaultextension=".log",
            initialfile=default_name,
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not target:
            return
        try:
            Path(target).write_text("\n".join(self.log_history) + ("\n" if self.log_history else ""), encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        messagebox.showinfo("导出成功", f"日志已保存到:\n{target}")

    def _begin_job(self) -> bool:
        with self.busy_lock:
            if self.busy:
                return False
            self.busy = True
        self.root.after(0, self._refresh_runtime_state)
        self.root.after(0, self._refresh_actions_state)
        self.root.after(0, lambda: self.runtime_hint_var.set("任务执行中，日志会持续滚动输出"))
        return True

    def _finish_job(self) -> None:
        with self.busy_lock:
            self.busy = False
        self.root.after(0, self._refresh_runtime_state)
        self.root.after(0, self._refresh_actions_state)

    def _coerce_int(self, value: str, field: str, minimum: int = 1) -> int:
        try:
            parsed = int(value)
        except Exception as exc:
            raise ValueError(f"{field} 必须是整数") from exc
        if parsed < minimum:
            raise ValueError(f"{field} 必须大于等于 {minimum}")
        return parsed

    def _reset_progress(self, text: str = "等待任务开始") -> None:
        self.progress_text_var.set(text)
        self.progress_done_var.set("0 / 0")
        self.progress_success_var.set("0")
        self.progress_fail_var.set("0")
        self.progress_running_var.set("0")
        self.progress_bar.configure(value=0, maximum=100)

    def _update_progress(self, payload: dict) -> None:
        def apply() -> None:
            total = max(0, int(payload.get("total") or 0))
            done = max(0, int(payload.get("done") or 0))
            success = max(0, int(payload.get("success") or 0))
            fail = max(0, int(payload.get("fail") or 0))
            running = max(0, int(payload.get("running") or 0))
            self.progress_done_var.set(f"{done} / {total}")
            self.progress_success_var.set(str(success))
            self.progress_fail_var.set(str(fail))
            self.progress_running_var.set(str(running))
            maximum = total if total > 0 else 100
            self.progress_bar.configure(maximum=maximum, value=min(done, maximum))
            if total > 0:
                self.progress_text_var.set(f"注册进度 {done}/{total}，成功 {success}，失败 {fail}")
            else:
                self.progress_text_var.set("等待任务开始")

        self.root.after(0, apply)

    def _common_kwargs(self) -> dict:
        config_path = Path(DEFAULT_REGISTER_CONFIG).resolve()
        output_dir = Path(self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR).resolve()
        return {
            "config_path": config_path,
            "output_dir": output_dir,
            "count": self._coerce_int(self.count_var.get().strip() or "1", "注册数量"),
            "threads": self._coerce_int(self.threads_var.get().strip() or "1", "线程数"),
            "enable_flaresolverr": bool(self.enable_flaresolverr_var.get()),
            "flaresolverr_url": self.flaresolverr_url_var.get().strip(),
            "upload_to_cloud": bool(self.upload_var.get()),
            "server": self.server_var.get().strip(),
            "auth_key": self.auth_key_var.get().strip(),
            "logger": self._log,
            "progress_callback": self._update_progress,
        }

    def _refresh_runtime_state(self) -> None:
        if self.monitoring:
            self.runtime_status_var.set("监控中")
            self.runtime_hint_var.set("正在循环检查云端账号数量，低于阈值时会自动补号")
            self.hero_status_label.configure(bg="#e8f7ec", fg="#1f6b3b")
            return
        if self.busy:
            self.runtime_status_var.set("执行中")
            self.runtime_hint_var.set("任务正在后台运行，请关注右侧日志输出")
            self.hero_status_label.configure(bg="#fff1dd", fg="#8d5a17")
            self.monitor_countdown_var.set("当前任务运行中")
            return
        self.runtime_status_var.set("空闲")
        self.runtime_hint_var.set("可以开始本地注册、导入云端或开启自动补号监控")
        self.hero_status_label.configure(bg="#e9f2ff", fg="#1a4b8c")
        if not self.monitoring:
            self.monitor_countdown_var.set("未启动")

    def _refresh_actions_state(self) -> None:
        if not hasattr(self, "register_button"):
            return
        busy = bool(self.busy)
        monitoring = bool(self.monitoring)
        manual_state = tk.DISABLED if monitoring or busy else tk.NORMAL
        for button in (self.register_button, self.refill_button, self.import_button, self.monitor_start_button):
            button.configure(state=manual_state)
        self.save_button.configure(state=tk.NORMAL if not busy else tk.DISABLED)
        self.monitor_stop_button.configure(state=tk.NORMAL if monitoring else tk.DISABLED)
        self.clear_log_button.configure(state=tk.NORMAL)
        self.export_log_button.configure(state=tk.NORMAL)

    def _run_background(self, target) -> None:
        if self.monitoring:
            messagebox.showinfo("提示", "当前正在循环监控，请先停止监控后再手动执行")
            return
        if not self._begin_job():
            messagebox.showinfo("提示", "当前已有任务在运行，请稍等")
            return
        self._save_settings()
        self._reset_progress("任务已启动，等待进度更新")

        def runner() -> None:
            try:
                target()
            except Exception as exc:
                self._log(f"发生错误: {exc}")
                self.root.after(0, lambda: messagebox.showerror("运行失败", str(exc)))
            finally:
                self._finish_job()

        threading.Thread(target=runner, daemon=True, name="reg-gui-worker").start()

    def _run_refill_check_once(self) -> None:
        kwargs = self._common_kwargs()
        server = str(kwargs.get("server") or "").strip()
        auth_key = str(kwargs.get("auth_key") or "").strip()
        if not server or not auth_key:
            raise ValueError("检查云端并补号需要填写云端地址和管理员密钥")
        min_active_accounts = self._coerce_int(self.min_active_var.get().strip() or "1", "最低有效账号")
        check_cloud_and_refill(
            server=server,
            auth_key=auth_key,
            min_active_accounts=min_active_accounts,
            config_path=kwargs["config_path"],
            output_dir=kwargs["output_dir"],
            count=kwargs["count"],
            threads=kwargs["threads"],
            enable_flaresolverr=kwargs["enable_flaresolverr"],
            flaresolverr_url=kwargs["flaresolverr_url"],
            upload_to_cloud=kwargs["upload_to_cloud"],
            logger=self._log,
            progress_callback=self._update_progress,
        )

    def _handle_save(self) -> None:
        try:
            self._save_settings()
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        messagebox.showinfo("提示", "配置已保存")

    def _handle_register(self) -> None:
        def task() -> None:
            kwargs = self._common_kwargs()
            run_local_register_job(**kwargs)

        self._run_background(task)

    def _handle_check_and_refill(self) -> None:
        def task() -> None:
            self._run_refill_check_once()

        self._run_background(task)

    def _handle_import_only(self) -> None:
        def task() -> None:
            kwargs = self._common_kwargs()
            accounts_file = Path(self.accounts_file_var.get().strip()).resolve() if self.accounts_file_var.get().strip() else None
            if accounts_file is None:
                raise ValueError("请先选择账号文件")
            run_local_register_job(
                config_path=kwargs["config_path"],
                output_dir=kwargs["output_dir"],
                count=kwargs["count"],
                threads=kwargs["threads"],
                enable_flaresolverr=kwargs["enable_flaresolverr"],
                flaresolverr_url=kwargs["flaresolverr_url"],
                accounts_file=accounts_file,
                import_only=True,
                upload_to_cloud=True,
                server=kwargs["server"],
                auth_key=kwargs["auth_key"],
                logger=self._log,
                progress_callback=self._update_progress,
            )

        self._run_background(task)

    def _handle_start_monitor(self) -> None:
        if self.monitoring:
            messagebox.showinfo("提示", "循环监控已经在运行中")
            return
        if self.busy:
            messagebox.showinfo("提示", "当前已有任务在运行，请稍后再开启监控")
            return
        try:
            interval_seconds = self._coerce_int(self.monitor_interval_var.get().strip() or "300", "监控间隔", minimum=5)
            self._coerce_int(self.min_active_var.get().strip() or "1", "最低有效账号")
            kwargs = self._common_kwargs()
            if not str(kwargs.get("server") or "").strip() or not str(kwargs.get("auth_key") or "").strip():
                raise ValueError("开启循环监控需要填写云端地址和管理员密钥")
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc))
            return

        self._save_settings()
        self.monitor_stop_event.clear()
        self.monitoring = True
        self.monitor_countdown_var.set("即将开始首次检查")
        self._reset_progress("监控模式已启动，等待首次检查")
        self._refresh_runtime_state()
        self._refresh_actions_state()
        self._log(f"已开启循环监控，间隔 {interval_seconds}s，低于阈值时自动补号")
        threading.Thread(
            target=self._run_monitor_loop,
            args=(interval_seconds,),
            daemon=True,
            name="reg-gui-monitor",
        ).start()

    def _handle_stop_monitor(self) -> None:
        if not self.monitoring:
            messagebox.showinfo("提示", "当前没有运行中的循环监控")
            return
        self.monitor_stop_event.set()
        self.monitoring = False
        self.monitor_countdown_var.set("停止中")
        self._refresh_runtime_state()
        self._refresh_actions_state()
        self._log("已请求停止循环监控，当前轮次结束后会退出")

    def _run_monitor_loop(self, interval_seconds: int) -> None:
        try:
            while not self.monitor_stop_event.is_set():
                self.root.after(0, lambda: self.monitor_countdown_var.set("正在执行检查"))
                if not self._begin_job():
                    self._log("检测到已有任务在执行，本轮监控跳过")
                else:
                    try:
                        self.root.after(0, lambda: self.progress_text_var.set("正在执行云端检查 / 补号"))
                        self._log("开始执行监控检查")
                        self._run_refill_check_once()
                    except Exception as exc:
                        self._log(f"监控补号执行失败: {exc}")
                    finally:
                        self._finish_job()
                for remaining in range(interval_seconds, 0, -1):
                    if self.monitor_stop_event.wait(1):
                        break
                    self.root.after(0, lambda seconds=remaining - 1: self.monitor_countdown_var.set(f"{seconds}s 后再次检查"))
                if self.monitor_stop_event.is_set():
                    break
        finally:
            self.monitoring = False
            self.monitor_stop_event.clear()
            self.root.after(0, self._refresh_runtime_state)
            self.root.after(0, self._refresh_actions_state)
            self.root.after(0, lambda: self.monitor_countdown_var.set("未启动"))
            self._log("循环监控已停止")

    def _on_close(self) -> None:
        try:
            self.monitor_stop_event.set()
            self.monitoring = False
            self._save_settings()
        finally:
            self.root.destroy()


def main() -> int:
    root = tk.Tk()
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    app = RegGuiApp(root)
    app._log("桌面工具已启动")
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
