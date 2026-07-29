"""PixelEmu Studio — графический интерфейс (tkinter, только stdlib).

Главное окно: список виртуальных устройств и журнал.
Мастер «Новый эмулятор»: устройство → образ системы → характеристики →
режим эмуляции → загрузка и создание.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, avd, config as cfgmod, engine, netio, repo
from .config import EmuConfig, ImageSpec
from .presets import PRESETS, get_preset
from .util import (AppError, LEGACY_SETTINGS_FILE, ROOT, SETTINGS_FILE,
                   default_sdk_root, disk_free_gb, ensure_dirs, host_ram_mb,
                   human_size, is_windows, sanitize_avd_name)

FONT_MAIN = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 12, "bold")
FONT_MONO = ("Consolas", 9)

GPU_MODES = {
    "auto": "Авто (рекомендуется)",
    "host": "Аппаратное ускорение GPU ПК",
    "angle_indirect": "ANGLE (DirectX) — если чёрный экран",
    "swiftshader_indirect": "SwiftShader (программный, медленно, надёжно)",
}
BOOT_MODES = {
    "fast": "Быстрый старт (снапшот)",
    "cold": "Холодная загрузка",
    "wipe": "Сбросить данные и загрузить",
}


# --------------------------------------------------------------------------- #
#  Настройки приложения и фоновые задачи                                       #
# --------------------------------------------------------------------------- #

def load_settings() -> dict:
    for path in (SETTINGS_FILE, LEGACY_SETTINGS_FILE):  # с миграцией со 1.0.x
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def save_settings(s: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2),
                             encoding="utf-8")


def bg(work, on_done=None, on_error=None):
    """Запуск work() в потоке; on_done/on_error вызываются в UI-потоке."""
    def runner():
        try:
            result = work()
        except BaseException as exc:  # noqa: BLE001
            tk._default_root.after(0, lambda e=exc: (on_error or show_error)(e))
            return
        if on_done:
            tk._default_root.after(0, lambda r=result: on_done(r))
    threading.Thread(target=runner, daemon=True).start()


def ui(widget, fn, *args):
    """Безопасный вызов fn из фонового потока."""
    widget.after(0, lambda: fn(*args))


def show_error(exc: BaseException) -> None:
    messagebox.showerror("PixelEmu Studio — ошибка", str(exc))


class LogBox(tk.Text):
    """Журнал внизу главного окна."""

    def __init__(self, master, **kw):
        super().__init__(master, height=9, state="disabled", wrap="word",
                         bg="#101418", fg="#c8e6c9", insertbackground="#c8e6c9",
                         font=FONT_MONO, relief="flat", **kw)

    def write(self, text: str) -> None:
        self.configure(state="normal")
        self.insert("end", text.rstrip("\n") + "\n")
        self.see("end")
        self.configure(state="disabled")


# --------------------------------------------------------------------------- #
#  Мастер создания эмулятора                                                   #
# --------------------------------------------------------------------------- #

class Wizard(tk.Toplevel):
    STEPS = ("Устройство", "Образ системы", "Характеристики",
             "Режим эмуляции", "Загрузка и создание")

    def __init__(self, master: "PixEmuApp"):
        super().__init__(master)
        self.app = master
        self.title("Новый эмулятор Pixel")
        self.geometry("680x640")
        self.minsize(660, 620)
        self.transient(master)
        self.grab_set()

        self.data: dict = {"preset": PRESETS[0], "image": None}
        self.license_ok = tk.BooleanVar(value=False)
        self.created_cfg: EmuConfig | None = None
        self.idx = 0
        self.frames: list[tk.Frame] = []

        ttk.Label(self, text="Мастер создания виртуального устройства",
                  font=FONT_BOLD).pack(anchor="w", padx=14, pady=(12, 2))
        self.step_lbl = ttk.Label(self, text="", foreground="#666")
        self.step_lbl.pack(anchor="w", padx=14)

        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True, padx=14, pady=8)

        nav = ttk.Frame(self)
        nav.pack(fill="x", padx=14, pady=(0, 12))
        self.btn_back = ttk.Button(nav, text="← Назад", command=self.on_back)
        self.btn_next = ttk.Button(nav, text="Далее →", command=self.on_next)
        ttk.Button(nav, text="Отмена", command=self.destroy).pack(side="right")
        self.btn_next.pack(side="right", padx=(6, 6))
        self.btn_back.pack(side="right")

        self.show(0)

    # ----- каркас шагов -----

    def show(self, idx: int) -> None:
        self.idx = idx
        for f in self.frames:
            f.pack_forget()
        while len(self.frames) <= idx:
            i = len(self.frames)
            builder = (self.build_step1, self.build_step2, self.build_step3,
                       self.build_step4, self.build_step5)[i]
            self.frames.append(builder())
        frame = self.frames[idx]
        frame.pack(fill="both", expand=True)
        self.step_lbl.config(
            text=f"Шаг {idx + 1} из {len(self.STEPS)} — {self.STEPS[idx]}")
        self.btn_back.config(state="normal" if idx else "disabled",
                             text="← Назад")
        self.btn_next.config(text="Далее →" if idx < 4 else "Готово (закрыть)",
                             state="normal")
        if idx == 4:
            self.fill_summary()

    def on_back(self) -> None:
        if self.idx:
            self.show(self.idx - 1)

    def on_next(self) -> None:
        if self.idx == 4:               # финальный шаг — «Готово» закрывает мастер
            self.finish_close()
            return
        validators = (self.valid_step1, self.valid_step2, self.valid_step3,
                      self.valid_step4)
        if validators[self.idx]():
            self.show(self.idx + 1)

    # ----- Шаг 1: устройство -----

    def build_step1(self) -> tk.Frame:
        f = ttk.Frame(self.body)
        ttk.Label(f, text="Модель «железа» (профиль как в Android Studio):",
                  font=FONT_MAIN).pack(anchor="w")
        box = ttk.Frame(f)
        box.pack(fill="x", pady=6)
        self.preset_list = tk.Listbox(box, height=5, exportselection=False,
                                      font=FONT_MAIN)
        for p in PRESETS:
            self.preset_list.insert("end", f"{p.title}   "
                                    f"({p.width}×{p.height}, {p.density} dpi)")
        self.preset_list.selection_set(0)
        self.preset_list.pack(side="left", fill="x", expand=True)
        self.preset_list.bind("<<ListboxSelect>>", lambda _e: self.on_preset())

        self.preset_info = tk.Text(f, height=6, wrap="word", relief="flat",
                                   bg=self.cget("bg"), font=FONT_MAIN)
        self.preset_info.pack(fill="x", pady=4)
        self.preset_info.configure(state="disabled")

        ttk.Separator(f).pack(fill="x", pady=8)
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Имя эмулятора (латиницей):",
                  font=FONT_MAIN).pack(side="left")
        self.name_var = tk.StringVar(value="Pixel7Pro_API35")
        ttk.Entry(row, textvariable=self.name_var, width=30,
                  font=FONT_MAIN).pack(side="left", padx=8)
        self.on_preset()
        return f

    def on_preset(self) -> None:
        sel = self.preset_list.curselection()
        if not sel:
            return
        p = PRESETS[sel[0]]
        self.data["preset"] = p
        txt = (f"Экран: {p.screen_in}″, {p.width}×{p.height}, {p.density} dpi\n"
               f"Платформа: {p.soc}\n"
               f"По умолчанию: RAM {p.ram_mb} МБ, {p.cores} vCPU, "
               f"диск {p.storage_gb} ГБ\n{p.note}")
        self.preset_info.configure(state="normal")
        self.preset_info.delete("1.0", "end")
        self.preset_info.insert("1.0", txt)
        self.preset_info.configure(state="disabled")

    def valid_step1(self) -> bool:
        name = sanitize_avd_name(self.name_var.get())
        if not name:
            messagebox.showwarning("Мастер", "Введите имя эмулятора.", parent=self)
            return False
        existing = {p.stem for p in (self.app.sdk_root / "configs").glob("*.json")}
        if name in existing:
            messagebox.showwarning(
                "Мастер", f"Эмулятор «{name}» уже существует.", parent=self)
            return False
        self.data["name"] = name
        return True

    # ----- Шаг 2: образ системы -----

    def build_step2(self) -> tk.Frame:
        f = ttk.Frame(self.body)
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Источник:", font=FONT_MAIN).pack(side="left")
        self.src_combo = ttk.Combobox(
            row, state="readonly", width=40, font=FONT_MAIN,
            values=[f"{k} — {v[0]}" for k, v in repo.CHANNELS.items()])
        self.src_combo.current(0)
        self.src_combo.pack(side="left", padx=6)
        ttk.Button(row, text="Загрузить список образов",
                   command=self.refresh_images).pack(side="left", padx=4)

        cols = ("api", "android", "abi", "size", "rev")
        heads = ("API", "Android", "ABI", "Размер", "Ревизия")
        self.img_tree = ttk.Treeview(f, columns=cols, show="headings", height=11)
        for c, h, w in zip(cols, heads, (60, 90, 110, 110, 90)):
            self.img_tree.heading(c, text=h)
            self.img_tree.column(c, width=w, anchor="center")
        self.img_tree.pack(fill="both", expand=True, pady=6)
        sb = ttk.Scrollbar(f, orient="vertical", command=self.img_tree.yview)
        self.img_tree.configure(yscrollcommand=sb.set)
        sb.place(relx=1.0, rely=0.12, relheight=0.7, anchor="ne")

        self.img_note = ttk.Label(
            f, text="Образы скачиваются с официальных серверов Google "
                    "(dl.google.com). Для ПК на Intel/AMD выбирайте x86_64.",
            foreground="#666", wraplength=620, justify="left")
        self.img_note.pack(anchor="w")
        self.images: list[ImageSpec] = []
        self.refresh_images()
        return f

    def current_source(self) -> str:
        return self.src_combo.get().split(" — ")[0] if self.src_combo.get() else "play"

    def refresh_images(self) -> None:
        src = self.current_source()
        self.img_note.config(text="Загружаю манифест Google…")
        bg(lambda: repo.load_channel(src, self.app.sdk_root / "cache"),
           on_done=lambda imgs: ui(self, self.fill_images, imgs),
           on_error=lambda e: (self.img_note.config(text=str(e)), show_error(e)))

    def fill_images(self, images: list[ImageSpec]) -> None:
        self.images = images
        self.img_tree.delete(*self.img_tree.get_children())
        for i, img in enumerate(images):
            self.img_tree.insert(
                "", "end", iid=str(i),
                values=(img.api_label, repo.API_TO_ANDROID.get(img.api, "?"),
                        img.abi, human_size(img.size), img.revision))
        best = next((i for i, im in enumerate(images) if im.abi == "x86_64"), 0)
        if images:
            self.img_tree.selection_set(str(best))
            self.img_tree.see(str(best))
        self.img_note.config(text=f"Найдено образов: {len(images)}. "
                                  "Выберите версию Android.")

    def valid_step2(self) -> bool:
        sel = self.img_tree.selection()
        if not sel:
            messagebox.showwarning("Мастер", "Выберите системный образ.",
                                   parent=self)
            return False
        img = self.images[int(sel[0])]
        if img.abi.startswith("arm") and os.environ.get("PROCESSOR_ARCHITECTURE",
                                                        "AMD64") == "AMD64":
            messagebox.showinfo(
                "Мастер",
                "ARM-образ на x86-ПК будет работать очень медленно "
                "(трансляция кода). Рекомендуется x86_64.", parent=self)
        self.data["image"] = img
        return True

    # ----- Шаг 3: характеристики -----

    def build_step3(self) -> tk.Frame:
        f = ttk.Frame(self.body)
        p = self.data.get("preset", PRESETS[0])
        host_ram = host_ram_mb()
        max_ram = max(2048, min(32768, host_ram - 1024))

        grid = ttk.Frame(f)
        grid.pack(fill="x")

        def spin(row, label, var, frm, to, inc, hint=""):
            ttk.Label(grid, text=label, font=FONT_MAIN).grid(
                row=row, column=0, sticky="w", pady=4)
            ttk.Spinbox(grid, textvariable=var, from_=frm, to=to,
                        increment=inc, width=10, font=FONT_MAIN).grid(
                row=row, column=1, sticky="w", padx=8)
            ttk.Label(grid, text=hint, foreground="#666").grid(
                row=row, column=2, sticky="w")

        self.ram_var = tk.StringVar(value=str(min(p.ram_mb, max_ram)))
        self.cores_var = tk.StringVar(value=str(min(p.cores, 16)))
        self.w_var = tk.StringVar(value=str(p.width))
        self.h_var = tk.StringVar(value=str(p.height))
        self.dpi_var = tk.StringVar(value=str(p.density))
        self.data_var = tk.StringVar(value=str(p.storage_gb))
        self.sd_on = tk.BooleanVar(value=True)
        self.sd_var = tk.StringVar(value="512")

        spin(0, "Оперативная память, МБ:", self.ram_var, 1024, max_ram, 256,
             f"на ПК: {host_ram} МБ, оставьте ≥ 1 ГБ системе")
        spin(1, "Ядер процессора (vCPU):", self.cores_var, 1, 16, 1,
             "в пределах ядер вашего ПК")
        spin(2, "Ширина экрана, px:", self.w_var, 480, 3840, 10)
        spin(3, "Высота экрана, px:", self.h_var, 800, 3840, 10)
        spin(4, "Плотность (DPI):", self.dpi_var, 160, 640, 20)
        spin(5, "Постоянная память, ГБ:", self.data_var, 4, 64, 4,
             "раздел userdata")
        ttk.Checkbutton(grid, text="SD-карта, МБ:",
                        variable=self.sd_on).grid(row=6, column=0, sticky="w",
                                                  pady=4)
        ttk.Spinbox(grid, textvariable=self.sd_var, from_=128, to=4096,
                    increment=128, width=10, font=FONT_MAIN).grid(
            row=6, column=1, sticky="w", padx=8)

        ttk.Label(f, text="Эти параметры и есть «характеристики эмулируемой "
                          "ОС» — AVD получит именно их.",
                  foreground="#666", wraplength=620, justify="left").pack(
            anchor="w", pady=(10, 0))
        return f

    def valid_step3(self) -> bool:
        try:
            vals = {k: int(v.get()) for k, v in (
                ("ram_mb", self.ram_var), ("cores", self.cores_var),
                ("width", self.w_var), ("height", self.h_var),
                ("density", self.dpi_var), ("data_gb", self.data_var))}
            vals["sdcard_mb"] = int(self.sd_var.get()) if self.sd_on.get() else 0
        except ValueError:
            messagebox.showwarning("Мастер", "Характеристики должны быть "
                                             "числами.", parent=self)
            return False
        self.data.update(vals)
        return True

    # ----- Шаг 4: режим эмуляции -----

    def build_step4(self) -> tk.Frame:
        f = ttk.Frame(self.body)
        self.gpu_var = tk.StringVar(value="auto")
        self.boot_var = tk.StringVar(value="fast")
        self.cam_var = tk.BooleanVar(value=True)
        self.mic_var = tk.BooleanVar(value=True)
        self.gps_var = tk.BooleanVar(value=True)
        self.flags_var = tk.StringVar()

        ttk.Label(f, text="GPU (отрисовка):", font=FONT_MAIN).pack(anchor="w")
        ttk.Combobox(f, state="readonly", font=FONT_MAIN, width=52,
                     values=[f"{k} — {v}" for k, v in GPU_MODES.items()],
                     textvariable=self.gpu_var).pack(anchor="w", pady=(0, 8))
        self.gpu_var.set("auto — " + GPU_MODES["auto"])

        ttk.Label(f, text="Тип загрузки:", font=FONT_MAIN).pack(anchor="w")
        for k, v in BOOT_MODES.items():
            ttk.Radiobutton(f, text=v, value=k,
                            variable=self.boot_var).pack(anchor="w")

        ttk.Separator(f).pack(fill="x", pady=8)
        ttk.Checkbutton(f, text="Камеры (эмуляция)", variable=self.cam_var).pack(anchor="w")
        ttk.Checkbutton(f, text="Микрофон (звук с ПК)", variable=self.mic_var).pack(anchor="w")
        ttk.Checkbutton(f, text="GPS", variable=self.gps_var).pack(anchor="w")

        ttk.Label(f, text="Доп. флаги эмулятора (для экспертов):",
                  font=FONT_MAIN).pack(anchor="w", pady=(10, 0))
        ttk.Entry(f, textvariable=self.flags_var, font=FONT_MONO).pack(
            fill="x", pady=2)
        ttk.Label(f, text="Например: -no-boot-anim -wipe-data",
                  foreground="#666").pack(anchor="w")
        return f

    def valid_step4(self) -> bool:
        self.data["gpu"] = self.gpu_var.get().split(" — ")[0] or "auto"
        self.data["boot"] = self.boot_var.get()
        self.data["camera"] = self.cam_var.get()
        self.data["mic"] = self.mic_var.get()
        self.data["gps"] = self.gps_var.get()
        self.data["flags"] = self.flags_var.get().strip()
        return True

    def make_config(self) -> EmuConfig:
        d = self.data
        p = d.get("preset", PRESETS[0])
        return EmuConfig(
            avd_name=sanitize_avd_name(d["name"]), device_id=p.dev_id,
            width=d["width"], height=d["height"], density=d["density"],
            ram_mb=d["ram_mb"], cores=d["cores"], data_gb=d["data_gb"],
            sdcard_mb=d["sdcard_mb"], gpu=d["gpu"], boot=d["boot"],
            camera=d["camera"], mic=d["mic"], gps=d["gps"],
            extra_flags=d["flags"], image=d["image"],
        )

    # ----- Шаг 5: сводка + загрузка -----

    def build_step5(self) -> tk.Frame:
        f = ttk.Frame(self.body)
        self.summary = tk.Text(f, height=12, wrap="word", state="disabled",
                               font=FONT_MAIN, relief="solid", bd=1)
        self.summary.pack(fill="x")

        lic = ttk.Frame(f)
        lic.pack(fill="x", pady=6)
        ttk.Checkbutton(lic, variable=self.license_ok, text=(
            "Я принимаю условия лицензии Android Software Development Kit "
            "(образы © Google)")).pack(side="left")
        ttk.Button(lic, text="Открыть лицензию", width=18,
                   command=lambda: webbrowser.open(
                       "https://developer.android.com/studio/terms")).pack(
            side="left", padx=8)

        self.progress = ttk.Progressbar(f, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=4)
        self.prog_lbl = ttk.Label(f, text="Готово к загрузке.", foreground="#666")
        self.prog_lbl.pack(anchor="w")

        self.btn_create = ttk.Button(f, text="Скачать образ и создать эмулятор",
                                     command=self.start_create)
        self.btn_create.pack(pady=8)
        return f

    def finish_close(self) -> None:
        self.app.refresh_list()
        self.destroy()

    def fill_summary(self) -> None:
        cfg = self.make_config()
        img = cfg.image
        d = self.data
        txt = (
            f"Имя AVD:            {cfg.avd_name}\n"
            f"Устройство:         {get_preset(cfg.device_id).title}\n"
            f"Экран:              {cfg.width}×{cfg.height} @ {cfg.density} dpi\n"
            f"RAM / vCPU:         {cfg.ram_mb} МБ / {cfg.cores}\n"
            f"Память / SD:        {cfg.data_gb} ГБ / "
            f"{(str(cfg.sdcard_mb) + ' МБ') if cfg.sdcard_mb else 'нет'}\n"
            f"GPU:                {GPU_MODES.get(cfg.gpu, cfg.gpu)}\n"
            f"Загрузка:           {BOOT_MODES.get(cfg.boot, cfg.boot)}\n"
            f"Образ:              Android {repo.API_TO_ANDROID.get(img.api, '?')} "
            f"(API {img.api_label}), {img.tag_display}, {img.abi}\n"
            f"Скачать нужно:      {human_size(img.size)}  (sha1 проверяется)\n"
            f"Папка SDK:          {self.app.sdk_root}")
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", txt)
        self.summary.configure(state="disabled")
        if img.is_downloaded(self.app.sdk_root):
            self.prog_lbl.config(text="Образ уже скачан ранее — будет создан "
                                      "только AVD.")

    def set_progress(self, done: int, total: int) -> None:
        pct = (done * 100 // total) if total else 0
        ui(self.progress, self.progress.configure, {"value": pct})
        ui(self.prog_lbl, self.prog_lbl.config,
           {"text": f"Загружено {human_size(done)} из {human_size(total)} "
                    f"({pct}%)"})

    def set_status(self, text: str) -> None:
        ui(self.prog_lbl, self.prog_lbl.config, {"text": text})

    def start_create(self) -> None:
        if not self.license_ok.get():
            messagebox.showwarning("Мастер", "Подтвердите принятие лицензии "
                                             "Android SDK.", parent=self)
            return
        if disk_free_gb(self.app.sdk_root) < 6:
            messagebox.showwarning("Мастер", "На диске меньше 6 ГБ — образ "
                                             "может не поместиться.", parent=self)
            return
        cfg = self.make_config()
        sdk = self.app.sdk_root

        def work() -> EmuConfig:
            # скачать/распаковать/проверить образ (общий код с главным окном)
            engine.install_image(cfg, sdk, progress=self.set_progress)
            self.set_status("Создание AVD…")
            cfg.save(sdk / "configs")
            avd.ensure_avd(cfg, sdk)
            return cfg

        def done(c: EmuConfig) -> None:
            self.created_cfg = c
            self.set_status(f"Готово! Эмулятор «{c.avd_name}» создан. "
                            "Нажмите «Готово» и запустите его в главном окне.")
            self.app.append_log(f"Создан эмулятор: {c.avd_name}")
            self.app.refresh_list()
            self.btn_create.config(state="disabled")
            self.btn_next.config(text="Готово (закрыть)")

        self.set_status("Начинаю загрузку…")
        self.btn_create.config(state="disabled")
        bg(work, on_done=done,
           on_error=lambda e: (self.btn_create.config(state="normal"),
                               show_error(e)))


# --------------------------------------------------------------------------- #
#  Диалог настроек                                                             #
# --------------------------------------------------------------------------- #

class SettingsDialog(tk.Toplevel):

    def __init__(self, app: "PixEmuApp"):
        super().__init__(app)
        self.app = app
        self.title("Настройки / Движок")
        self.geometry("660x430")
        self.transient(app)
        self.grab_set()

        ttk.Label(self, text="Папка SDK (движок, образы, AVD):",
                  font=FONT_MAIN).pack(anchor="w", padx=12, pady=(12, 2))
        row = ttk.Frame(self)
        row.pack(fill="x", padx=12)
        self.path_var = tk.StringVar(value=str(app.sdk_root))
        ttk.Entry(row, textvariable=self.path_var, font=FONT_MAIN).pack(
            side="left", fill="x", expand=True)
        ttk.Button(row, text="…", width=3, command=self.browse).pack(
            side="left", padx=4)
        ttk.Button(row, text="Применить", command=self.apply_path).pack(side="left")

        ttk.Separator(self).pack(fill="x", pady=10, padx=12)
        self.engine_lbl = ttk.Label(self, text=self.engine_status(),
                                    font=FONT_MAIN, wraplength=620,
                                    justify="left")
        self.engine_lbl.pack(anchor="w", padx=12)
        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=6)
        ttk.Button(btns, text="Скачать движок эмулятора (~450 МБ)",
                   command=self.install_engine).pack(side="left")
        ttk.Button(btns, text="Скачать platform-tools (adb)",
                   command=self.install_pt).pack(side="left", padx=6)
        ttk.Button(btns, text="Проверить ускорение",
                   command=self.accel).pack(side="left")

        self.prog = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.prog.pack(fill="x", padx=12, pady=(10, 0))
        self.status = ttk.Label(self, text="", foreground="#666")
        self.status.pack(anchor="w", padx=12)

        note = ("Движок — официальный Android Emulator от Google (скачивается с "
                "dl.google.com). Для аппаратного ускорения включите компонент "
                "«Платформа гипервизора Windows» (WHPX) в Windows.")
        ttk.Label(self, text=note, wraplength=620, justify="left",
                  foreground="#666").pack(anchor="w", padx=12, pady=6)

    def engine_status(self) -> str:
        if engine.engine_installed(self.app.sdk_root):
            core = "есть" if netio.core_available() else "нет (будет использован Python-загрузчик)"
            return f"Движок: установлен ✔    C++-ядро: {core}"
        return ("Движок: НЕ установлен — нажмите «Скачать движок эмулятора». "
                f"C++-ядро: {'есть' if netio.core_available() else 'нет'}")

    def browse(self) -> None:
        d = filedialog.askdirectory(parent=self, initialdir=self.path_var.get())
        if d:
            self.path_var.set(d)

    def apply_path(self) -> None:
        p = Path(self.path_var.get()).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        ensure_dirs(p)
        self.app.set_sdk_root(p)
        self.engine_lbl.config(text=self.engine_status())
        messagebox.showinfo("Настройки", f"Папка SDK:\n{p}", parent=self)

    def set_progress(self, done: int, total: int) -> None:
        pct = done * 100 // total if total else 0
        ui(self.prog, self.prog.configure, {"value": pct})
        ui(self.status, self.status.config,
           {"text": f"{human_size(done)} / {human_size(total)} ({pct}%)"})

    def install_engine(self) -> None:
        sdk = self.app.sdk_root
        def work():
            return engine.install_engine(sdk, progress=self.set_progress,
                                         log=lambda m: self.app.append_log(m))
        def done(ver: str):
            self.engine_lbl.config(text=self.engine_status())
            self.status.config(text=f"Движок {ver} установлен.")
            self.app.append_log(f"Движок эмулятора {ver} установлен.")
        self.status.config(text="Загрузка движка…")
        bg(work, on_done=done)

    def install_pt(self) -> None:
        sdk = self.app.sdk_root
        def work():
            return engine.install_platform_tools(sdk, progress=self.set_progress)
        def done(adb: Path):
            self.status.config(text=f"adb: {adb}")
        self.status.config(text="Загрузка platform-tools…")
        bg(work, on_done=done)

    def accel(self) -> None:
        sdk = self.app.sdk_root
        def work():
            return engine.accel_report(sdk)
        def done(text: str):
            top = tk.Toplevel(self)
            top.title("Проверка ускорения")
            t = tk.Text(top, width=80, height=24, font=FONT_MONO)
            t.pack(fill="both", expand=True)
            t.insert("1.0", text)
            t.configure(state="disabled")
        self.status.config(text="Проверяю ускорение…")
        bg(work, on_done=done)


# --------------------------------------------------------------------------- #
#  Главное окно                                                                #
# --------------------------------------------------------------------------- #

class PixEmuApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(f"PixelEmu Studio {__version__} — эмулятор Pixel "
                   f"(Python 3.13 + C++/MSYS2)")
        self.geometry("960x660")
        self.minsize(880, 600)
        self._apply_icon()

        settings = load_settings()
        self.sdk_root = Path(settings.get("sdk_root") or default_sdk_root())
        ensure_dirs(self.sdk_root)

        self.procs: dict[str, subprocess.Popen] = {}
        self.avd_files: list[Path] = []

        self._build()
        self.refresh_list()
        self.append_log(
            f"SDK: {self.sdk_root}   |   C++-ядро: "
            f"{'найдено' if netio.core_available() else 'не собрано (загрузки — через Python)'}")
        if not is_windows():
            self.append_log("ВНИМАНИЕ: запуск эмулятора предназначен для "
                            "Windows 10/11. На этой ОС GUI откроется, но "
                            "эмулятор Google для Windows не запустится.")

    # ----- виджеты -----

    def _apply_icon(self) -> None:
        """Иконка окна (и панели задач). В exe-сборке файлы лежат в _MEIPASS."""
        try:
            meipass = Path(getattr(sys, "_MEIPASS", ROOT))
            png = ico = None
            for base in (meipass, ROOT):
                if (base / "assets" / "icon.png").exists():
                    png = base / "assets" / "icon.png"
                if (base / "assets" / "icon.ico").exists():
                    ico = base / "assets" / "icon.ico"
            if ico and is_windows():
                # default= передаёт иконку всем Toplevel-окнам
                self.wm_iconbitmap(default=str(ico))
            if png:
                self._icon_img = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._icon_img)
        except Exception:
            pass  # иконка — дело добровольное, не мешаем запуску

    def _build(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=(10, 4))
        ttk.Label(top, text="PixelEmu Studio", font=FONT_BOLD).pack(side="left")
        self.sdk_lbl = ttk.Label(top, text=str(self.sdk_root), foreground="#666")
        self.sdk_lbl.pack(side="right")

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, padx=12, pady=4)

        left = ttk.Frame(mid)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="Виртуальные устройства:",
                  font=FONT_MAIN).pack(anchor="w")
        self.listbox = tk.Listbox(left, exportselection=False, font=FONT_MAIN,
                                  activestyle="dotbox")
        self.listbox.pack(fill="both", expand=True, pady=4)
        self.listbox.bind("<Double-Button-1>", lambda _e: self.launch_selected())

        right = ttk.Frame(mid)
        right.pack(side="left", fill="y", padx=(10, 0))
        for text, cmd in (
            ("➕  Создать эмулятор", self.new_avd),
            ("▶  Запустить", self.launch_selected),
            ("🧊  Холодный старт", lambda: self.launch_selected(force_boot="cold")),
            ("🧨  Сброс + запуск", lambda: self.launch_selected(force_boot="wipe")),
            ("⏹  Остановить", self.stop_selected),
            ("🗑  Удалить", self.delete_selected),
            ("📂  Папка AVD", self.open_folder),
            ("⚙  Настройки / Движок", self.open_settings),
        ):
            ttk.Button(right, text=text, command=cmd, width=24).pack(pady=3,
                                                                     anchor="n")

        ttk.Label(self, text="Журнал:", font=FONT_MAIN).pack(anchor="w",
                                                             padx=12)
        self.log = LogBox(self)
        self.log.pack(fill="x", padx=12, pady=(0, 10))

    def append_log(self, text: str) -> None:
        ui(self.log, self.log.write, text)

    def set_sdk_root(self, p: Path) -> None:
        self.sdk_root = p
        ensure_dirs(p)
        save_settings({"sdk_root": str(p)})
        self.sdk_lbl.config(text=str(p))
        self.refresh_list()

    # ----- список AVD -----

    def refresh_list(self) -> None:
        self.listbox.delete(0, "end")
        self.avd_files = sorted((self.sdk_root / "configs").glob("*.json"))
        for path in self.avd_files:
            try:
                c = EmuConfig.load(path)
                self.listbox.insert(
                    "end",
                    f"{c.avd_name:22}  {get_preset(c.device_id).title:12}  "
                    f"Android API {c.image.api}  {c.ram_mb} МБ  "
                    f"{c.width}×{c.height}")
            except Exception as exc:  # noqa: BLE001
                self.listbox.insert("end", f"! {path.name}: {exc}")

    def selected_cfg(self) -> EmuConfig | None:
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self.avd_files):
            messagebox.showinfo("PixelEmu Studio", "Сначала выберите эмулятор "
                                                   "в списке.")
            return None
        return EmuConfig.load(self.avd_files[sel[0]])

    # ----- действия -----

    def new_avd(self) -> None:
        Wizard(self)

    def open_settings(self) -> None:
        SettingsDialog(self)

    def open_folder(self) -> None:
        cfg = self.selected_cfg()
        if not cfg:
            return
        d = avd.avd_dir(self.sdk_root, cfg.avd_name)
        d.mkdir(parents=True, exist_ok=True)
        if is_windows():
            os.startfile(d)  # type: ignore[attr-defined]
        else:
            self.append_log(f"Папка AVD: {d}")

    def launch_selected(self, force_boot: str | None = None) -> None:
        cfg = self.selected_cfg()
        if cfg:
            self._launch_cfg(cfg, force_boot)

    def _launch_cfg(self, cfg: EmuConfig, force_boot: str | None = None) -> None:
        """Запуск конкретного конфига (не зависит от выделения в списке)."""
        if cfg.avd_name in self.procs:      # уже запущен
            self.append_log(f"Эмулятор «{cfg.avd_name}» уже запущен.")
            return
        if not engine.engine_installed(self.sdk_root):
            if messagebox.askyesno("PixelEmu Studio",
                                   "Движок эмулятора ещё не скачан. "
                                   "Открыть «Настройки» для загрузки?"):
                self.open_settings()
            return
        if not cfg.image.is_downloaded(self.sdk_root):
            if messagebox.askyesno("PixelEmu Studio",
                                   "Системный образ не найден. Скачать сейчас "
                                   f"({human_size(cfg.image.size)})?"):
                self.download_image_for(cfg)
            return
        if force_boot:
            cfg.boot = force_boot
        try:
            proc = engine.launch(cfg, self.sdk_root,
                                 log=lambda m: self.append_log(m))
        except AppError as e:
            show_error(e)
            return
        self.procs[cfg.avd_name] = proc
        self.append_log(f"Эмулятор «{cfg.avd_name}» запускается (первая "
                        f"загрузка Android может занять 2–5 минут)…")
        threading.Thread(target=self._pump_log,
                         args=(cfg.avd_name, proc), daemon=True).start()

    def _pump_log(self, name: str, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self.append_log(f"[{name}] {line.rstrip()}")
        code = proc.wait()
        self.append_log(f"[{name}] процесс завершён, код {code}")
        self.procs.pop(name, None)

    def stop_selected(self) -> None:
        cfg = self.selected_cfg()
        if not cfg:
            return
        proc = self.procs.get(cfg.avd_name)
        if proc and proc.poll() is None:
            proc.terminate()
            self.append_log(f"Эмулятор «{cfg.avd_name}» остановлен.")
        else:
            self.append_log("Эмулятор не запущен из этой программы "
                            "(или уже закрыт).")

    def delete_selected(self) -> None:
        cfg = self.selected_cfg()
        if not cfg:
            return
        if messagebox.askyesno("PixelEmu Studio",
                               f"Удалить эмулятор «{cfg.avd_name}» и его AVD? "
                               "Скачанный системный образ останется."):
            self.stop_selected()
            avd.remove_avd(cfg, self.sdk_root)
            self.append_log(f"Удалён эмулятор «{cfg.avd_name}».")
            self.refresh_list()

    def download_image_for(self, cfg: EmuConfig) -> None:
        sdk = self.sdk_root

        def work():
            engine.install_image(cfg, sdk,
                                 progress=lambda d, t: self.append_log_throttle(d, t))
            return cfg

        def done(c: EmuConfig):
            self.append_log(f"Образ для «{c.avd_name}» установлен. Запускаю…")
            ui(self, self._launch_cfg, c)

        self.append_log(f"Скачиваю образ для «{cfg.avd_name}»…")
        bg(work, on_done=done)

    def append_log_throttle(self, done: int, total: int) -> None:
        now = getattr(self, "_last_dl_log", 0.0)
        import time

        if time.time() - now > 2.0:
            self._last_dl_log = time.time()
            self.append_log(f"Загрузка образа: {human_size(done)} / "
                            f"{human_size(total)}")


def main() -> None:
    app = PixEmuApp()
    app.mainloop()
