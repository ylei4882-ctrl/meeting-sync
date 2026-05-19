#!/usr/bin/env python3
"""腾讯会议录制同步 — Windows 桌面应用"""

import json
import os
import sys
import threading
import traceback
from datetime import datetime

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.widgets.scrolled import ScrolledText
from tkinter import messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meeting_sync as backend

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

COLORS = {
    "bg": "#f0f2f5",
    "success": "#00b894",
    "danger": "#e17055",
    "warning": "#fdcb6e",
    "info": "#74b9ff",
    "primary": "#6c5ce7",
    "text": "#2d3436",
    "muted": "#b2bec3",
}


class MeetingApp:
    def __init__(self):
        self.root = ttk.Window(themename="flatly")
        self.root.title("腾讯会议录制同步")
        self.root.geometry("580x660")
        self.root.minsize(480, 540)
        self.playwright = None
        self.cookie_str = None
        self.corp_id = None
        self.logged_in = False

        self.cfg = self._load_config()
        self._build_ui()

        # 启动时尝试静默登录
        self.root.after(500, self._auto_login)

        # 居中显示
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.root.geometry(f"+{x}+{y}")

    def _load_config(self):
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"dingtalk_webhook": "", "headless": False}

    def _save_config(self, *_):
        self.cfg["dingtalk_webhook"] = self.webhook_var.get().strip()
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)

    # ---------- UI ----------
    def _build_ui(self):
        main = ttk.Frame(self.root, padding=(24, 20, 24, 16))
        main.pack(fill=BOTH, expand=YES)

        # ---- 头部 ----
        header = ttk.Frame(main)
        header.pack(fill=X, pady=(0, 18))
        icon_label = ttk.Label(header, text="\U0001f4f9", font=("-size", 28))
        icon_label.pack(side=LEFT, padx=(0, 10))
        title = ttk.Label(header, text="腾讯会议录制同步",
                          font=("-family", "Microsoft YaHei UI", "-size", 18, "-weight", "bold"),
                          foreground=COLORS["text"])
        title.pack(side=LEFT)
        version = ttk.Label(header, text="v1.0",
                            font=("-size", 10), foreground=COLORS["muted"])
        version.pack(side=LEFT, padx=(8, 0))

        # 分割线
        sep = ttk.Separator(main, orient=HORIZONTAL)
        sep.pack(fill=X, pady=(0, 16))

        # ---- 钉钉 Webhook ----
        webhook_frame = ttk.Labelframe(main, text=" 钉钉 Webhook ",
                                       padding=12, bootstyle=INFO)
        webhook_frame.pack(fill=X, pady=(0, 12))
        hint = ttk.Label(webhook_frame, text="填入钉钉群机器人的 Webhook 地址，修改自动保存",
                         font=("-size", 10), foreground=COLORS["muted"])
        hint.pack(anchor=W, pady=(0, 6))
        self.webhook_var = ttk.StringVar(value=self.cfg.get("dingtalk_webhook", ""))
        webhook_entry = ttk.Entry(webhook_frame, textvariable=self.webhook_var,
                                  font=("-family", "Consolas", "-size", 11))
        webhook_entry.pack(fill=X, ipady=2)
        self.webhook_var.trace_add("write", self._save_config)

        # ---- 账号 ----
        account_frame = ttk.Labelframe(main, text=" 腾讯会议账号 ",
                                       padding=12, bootstyle=INFO)
        account_frame.pack(fill=X, pady=(0, 12))
        account_row = ttk.Frame(account_frame)
        account_row.pack(fill=X)
        self.login_btn = ttk.Button(account_row, text="\U0001f511  登录腾讯会议",
                                    command=self._do_login,
                                    bootstyle="success-outline")
        self.login_btn.pack(side=LEFT, ipadx=10, ipady=4)
        self.status_dot = ttk.Label(account_row, text="●", font=("-size", 20),
                                    foreground=COLORS["muted"])
        self.status_dot.pack(side=LEFT, padx=(12, 2))
        self.login_status = ttk.Label(account_row, text="未登录",
                                      font=("-size", 11), foreground=COLORS["muted"])
        self.login_status.pack(side=LEFT)
        self.login_hint = ttk.Label(account_frame, text="登录后 cookie 保存在本地，无需重复登录",
                                    font=("-size", 10), foreground=COLORS["muted"])
        self.login_hint.pack(anchor=W, pady=(4, 0))

        # ---- 关键词 ----
        filter_frame = ttk.Labelframe(main, text=" 关键词筛选 ",
                                      padding=12, bootstyle=INFO)
        filter_frame.pack(fill=X, pady=(0, 12))
        fhint = ttk.Label(filter_frame, text="输入关键词，回车直接推送，留空获取最近全部录制",
                          font=("-size", 10), foreground=COLORS["muted"])
        fhint.pack(anchor=W, pady=(0, 6))
        self.keyword_var = ttk.StringVar()
        keyword_entry = ttk.Entry(filter_frame, textvariable=self.keyword_var,
                                  font=("-size", 12))
        keyword_entry.pack(fill=X, ipady=2)
        keyword_entry.bind("<Return>", lambda e: self._do_send())

        # 选项
        options_row = ttk.Frame(filter_frame)
        options_row.pack(fill=X, pady=(8, 0))
        self.transcript_var = ttk.BooleanVar(value=False)
        ttk.Checkbutton(options_row, text="包含转写链接",
                        variable=self.transcript_var).pack(side=LEFT)
        self.deep_var = ttk.BooleanVar(value=False)
        ttk.Checkbutton(options_row, text="强力搜索（翻遍所有录制直到找到）",
                        variable=self.deep_var).pack(side=LEFT, padx=(16, 0))

        # ---- 发送按钮 ----
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=X, pady=(2, 12))
        self.send_btn = ttk.Button(btn_frame, text="\U0001f680  推送到钉钉",
                                   command=self._do_send, bootstyle=PRIMARY)
        self.send_btn.pack(fill=X, ipady=8)

        # ---- 日志 ----
        log_frame = ttk.Labelframe(main, text=" 运行日志 ",
                                   padding=8, bootstyle=INFO)
        log_frame.pack(fill=BOTH, expand=YES)
        self.log_area = ScrolledText(log_frame, height=10, autohide=True,
                                     font=("Consolas", 10),
                                     fg="#dfe6e9", bg="#2d3436",
                                     insertbackground="#fff")
        self.log_area.pack(fill=BOTH, expand=YES)
        clear_btn = ttk.Button(log_frame, text="清空日志", command=self._clear_log,
                               bootstyle="secondary-outline", width=10)
        clear_btn.pack(anchor=E, pady=(4, 0))

        # ---- 底部 ----
        footer = ttk.Label(main, text="基于腾讯会议开放 API  ·  数据仅保存在本地  ·  Foohu Team",
                           anchor=CENTER, foreground=COLORS["muted"],
                           font=("-size", 9))
        footer.pack(pady=(10, 0))

    def _log(self, text):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_area.insert(END, f"[{ts}] {text}\n")
        self.log_area.see(END)
        self.root.update_idletasks()

    def _clear_log(self):
        self.log_area.delete("1.0", END)

    # ---------- 登录 ----------
    def _auto_login(self):
        """启动时静默尝试用已有 cookie 登录"""
        self.login_status.config(text="  检测中...", foreground=COLORS["warning"])
        threading.Thread(target=self._login_thread, args=(False,), daemon=True).start()

    def _do_login(self):
        self.login_btn.config(state=DISABLED, text="⏳  正在登录...")
        self.status_dot.config(foreground=COLORS["warning"])
        self.login_status.config(text="正在登录...", foreground=COLORS["warning"])
        self._log(">>> 正在启动浏览器，请在弹出窗口中扫码登录...")
        threading.Thread(target=self._login_thread, args=(True,), daemon=True).start()

    def _login_thread(self, force_login):
        try:
            p, cookie_str, corp_id = backend.login_and_get_context(force_login=force_login)
            self.playwright = p
            self.cookie_str = cookie_str
            self.corp_id = corp_id
            self.logged_in = True
            self.root.after(0, self._on_login_success)
            if not force_login:
                self.root.after(0, lambda: self._log("[OK] 已恢复登录态（无需重复登录）"))
        except Exception:
            msg = traceback.format_exc()
            self.root.after(0, lambda: self._on_login_fail(msg))

    def _on_login_success(self):
        self.status_dot.config(foreground=COLORS["success"])
        self.login_status.config(text="已登录", foreground=COLORS["success"])
        self.login_btn.config(text="\U0001f503  重新登录", state=NORMAL, bootstyle="outline-secondary")
        self.login_hint.config(text="已登录 ✔  cookie 已保存，下次无需重复登录",
                               foreground=COLORS["success"])
        self._log("[OK] 登录成功！")

    def _on_login_fail(self, msg):
        self.status_dot.config(foreground=COLORS["danger"])
        self.login_status.config(text="登录失败", foreground=COLORS["danger"])
        self.login_btn.config(text="\U0001f511  登录腾讯会议", state=NORMAL, bootstyle="success-outline")
        self._log(f"[X] 登录失败:\n{msg}")

    # ---------- 发送 ----------
    def _do_send(self):
        if not self.logged_in:
            messagebox.showwarning("提示", "请先登录腾讯会议账号。")
            return

        self.send_btn.config(state=DISABLED, text="⏳  正在处理...")
        self._log("")
        webhook = self.webhook_var.get().strip()
        keyword = self.keyword_var.get().strip()
        deep_search = self.deep_var.get()
        include_transcript = self.transcript_var.get()
        threading.Thread(target=self._send_thread,
                         args=(webhook, keyword, deep_search, include_transcript),
                         daemon=True).start()

    def _send_thread(self, webhook, keyword, deep_search, include_transcript):
        try:
            meetings, log_lines = backend.fetch_and_process(
                self.cookie_str, self.corp_id, keyword,
                deep_search=deep_search,
                include_transcript=include_transcript,
            )
            for line in log_lines:
                self.root.after(0, lambda l=line: self._log(l))

            if not meetings:
                self.root.after(0, lambda: self._log("[OK] 没有需要推送的录制。"))
                self.root.after(0, self._send_done)
                return

            # 无 webhook 时直接在窗口显示链接
            if not webhook:
                self.root.after(0, lambda: self._log("\n========== 录制链接 =========="))
                for m in meetings:
                    self.root.after(0, lambda m=m: self._print_meeting_links(m))
            else:
                self.root.after(0, lambda: self._log(">>> 推送到钉钉..."))
                result = backend.send_to_dingtalk(webhook, meetings)
                if result.get("errcode") == 0:
                    self.root.after(0, lambda: self._log("[OK] 推送成功！"))
                else:
                    self.root.after(0, lambda: self._log(f"[X] 推送失败: {result}"))

        except Exception:
            self.root.after(0, lambda: self._log(f"[X] 错误: {traceback.format_exc()}"))
        finally:
            self.root.after(0, self._send_done)

    def _print_meeting_links(self, m):
        name = m.get("subject", "")
        code = m.get("meeting_code", "")
        t = m.get("start_time", "")
        self._log(f"\n【{name}】  会议号: {code}  {t}")
        for url in m.get("video_urls", []):
            self._log(f"视频: {url}")
        for url in m.get("transcript_urls", []):
            self._log(f"转写: {url}")

    def _send_done(self):
        self.send_btn.config(state=NORMAL, text="\U0001f680  推送到钉钉")

    def _on_close(self):
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()


if __name__ == "__main__":
    try:
        app = MeetingApp()
        app.run()
    except Exception:
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        raise
