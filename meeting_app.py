#!/usr/bin/env python3
"""腾讯会议录制同步 — 桌面应用"""

import json
import os
import sys
import threading
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meeting_sync as backend

import webview

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")


class API:
    def __init__(self):
        self.cookie_str = None
        self.corp_id = None
        self.logged_in = False
        self._load_config()

    def _load_config(self):
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                self.cfg = json.load(f)
        else:
            self.cfg = {"dingtalk_webhook": ""}

    def _save_config(self):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)

    def get_config(self):
        return self.cfg

    def save_webhook(self, url):
        self.cfg["dingtalk_webhook"] = url.strip()
        self._save_config()

    def login(self):
        """手动登录（弹窗扫码）"""
        try:
            cookie_str, corp_id = backend.login_and_get_context(force_login=True)
            self.cookie_str = cookie_str
            self.corp_id = corp_id
            self.logged_in = True
            return {"ok": True, "msg": "登录成功"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def auto_login(self):
        """静默登录"""
        try:
            cookie_str, corp_id = backend.login_and_get_context(force_login=False)
            self.cookie_str = cookie_str
            self.corp_id = corp_id
            self.logged_in = True
            return {"ok": True, "msg": "已恢复登录态"}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def fetch_and_push(self, keyword="", deep_search=False, include_transcript=False):
        """拉取录制 → 推送到钉钉（或在本地展示）"""
        webhook = self.cfg.get("dingtalk_webhook", "").strip()

        if not self.cookie_str or not self.corp_id:
            return {"ok": False, "log": ["[X] 请先登录腾讯会议账号"]}

        try:
            meetings, log = backend.fetch_and_process(
                self.cookie_str, self.corp_id, keyword,
                deep_search=deep_search,
                include_transcript=include_transcript,
            )
        except Exception as e:
            return {"ok": False, "log": [f"错误: {e}"]}

        if not meetings:
            log.append("[OK] 没有需要推送的录制。")
            return {"ok": True, "meetings": [], "log": log, "pushed": False}

        links = []
        for m in meetings:
            links.append({
                "subject": m.get("subject", ""),
                "meeting_code": m.get("meeting_code", ""),
                "start_time": m.get("start_time", ""),
                "video_urls": m.get("video_urls", []),
                "transcript_urls": m.get("transcript_urls", []),
            })

        pushed = False
        if webhook:
            log.append(">>> 推送到钉钉...")
            try:
                result = backend.send_to_dingtalk(webhook, meetings)
                if result.get("errcode") == 0:
                    log.append("[OK] 推送成功！")
                    pushed = True
                else:
                    log.append(f"[X] 推送失败: {result}")
            except Exception as e:
                log.append(f"[X] 推送失败: {e}")

        return {"ok": True, "meetings": links, "log": log, "pushed": pushed}

    def on_close(self):
        pass


HTML = r"""
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=520">
<style>
  :root {
    --bg-primary: #fcfcfc;
    --bg-card: #f4f5f7;
    --bg-input: #f1f2f4;
    --text-primary: #1d1d1f;
    --text-secondary: #6e6e73;
    --text-tertiary: #aeaeb2;
    --border: #e5e5ea;
    --accent: #0071e3;
    --accent-hover: #0077ed;
    --accent-bg: #e8f2fd;
    --green: #34c759;
    --yellow: #ff9f0a;
    --red: #ff3b30;
    --radius-sm: 10px;
    --radius: 14px;
    --radius-lg: 18px;
    --shadow-sm: 0 1px 3px rgba(0,0,0,0.04);
    --shadow-md: 0 4px 12px rgba(0,0,0,0.06);
    --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Microsoft YaHei UI", "PingFang SC", sans-serif;
    --mono: "SF Mono", "Cascadia Code", "Consolas", monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--font);
    font-size: 13px;
    background: var(--bg-primary);
    color: var(--text-primary);
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    letter-spacing: -0.01em;
  }
  .log-box { user-select: text; }

  /* ---- Title Bar ---- */
  .title-bar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 14px 20px;
    background: rgba(255,255,255,0.8);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--border);
  }
  .title-left { display: flex; align-items: center; gap: 10px; }
  .app-icon {
    width: 32px; height: 32px;
    background: linear-gradient(135deg, #0071e3, #5ac8fa);
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    box-shadow: 0 2px 6px rgba(0,113,227,0.25);
  }
  .app-icon svg { width: 17px; height: 17px; }
  .title-text { font-size: 13px; font-weight: 600; color: var(--text-primary); letter-spacing: -0.01em; }
  .version-badge {
    font-size: 11px; font-weight: 500; color: var(--text-tertiary);
    background: var(--bg-card); padding: 2px 8px; border-radius: 20px;
    border: 1px solid var(--border);
  }
  .win-dots { display: flex; gap: 7px; }
  .dot {
    width: 11px; height: 11px; border-radius: 50%; border: none;
    box-shadow: inset 0 1px 1px rgba(255,255,255,0.2);
  }
  .dot.red { background: #ff5f57; }
  .dot.yellow { background: #febc2e; }
  .dot.green { background: #2bc840; }

  /* ---- Body ---- */
  .body { padding: 20px; display: flex; flex-direction: column; gap: 16px; }

  /* ---- Field ---- */
  .field-group { display: flex; flex-direction: column; gap: 7px; }
  .field-label {
    font-size: 11px; font-weight: 600; color: var(--text-secondary);
    letter-spacing: 0.02em;
    display: flex; align-items: center; gap: 5px;
  }
  .field-label svg { width: 14px; height: 14px; opacity: 0.6; }
  .field-input {
    width: 100%; padding: 10px 14px; font-size: 13px;
    font-family: var(--font); color: var(--text-primary);
    background: var(--bg-input);
    border: 1px solid transparent; border-radius: var(--radius-sm);
    outline: none;
    transition: all 0.2s ease;
  }
  .field-input::placeholder { color: var(--text-tertiary); }
  .field-input:focus {
    background: #fff;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(0,113,227,0.1);
  }

  .divider { height: 1px; background: var(--border); margin: 0 -20px; }

  /* ---- Login ---- */
  .login-card {
    display: flex; align-items: center; gap: 12px;
    padding: 11px 14px;
    background: var(--bg-card);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    box-shadow: var(--shadow-sm);
  }
  .login-btn {
    display: flex; align-items: center; gap: 5px;
    padding: 7px 15px; font-size: 12px; font-weight: 500;
    color: var(--accent); background: var(--accent-bg);
    border: 1px solid rgba(0,113,227,0.2);
    border-radius: 9px; cursor: pointer; white-space: nowrap;
    transition: all 0.15s ease;
    font-family: var(--font);
  }
  .login-btn:hover { background: #dce8f8; }
  .login-btn:disabled { opacity: 0.45; pointer-events: none; }
  .status-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--border); flex-shrink: 0;
    transition: all 0.3s ease;
  }
  .status-dot.ok {
    background: var(--green);
    box-shadow: 0 0 0 3px rgba(52,199,89,0.18);
  }
  .status-dot.detecting {
    background: var(--yellow);
    box-shadow: 0 0 0 3px rgba(255,159,10,0.18);
    animation: pulse 1.5s ease-in-out infinite;
  }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
  .status-text { font-size: 12px; color: var(--text-secondary); font-weight: 450; }

  /* ---- Checkbox ---- */
  .checkbox-row { display: flex; gap: 20px; padding-top: 3px; }
  .checkbox-label {
    display: flex; align-items: center; gap: 7px;
    font-size: 12px; color: var(--text-secondary); cursor: pointer; font-weight: 450;
  }
  .checkbox-label input[type=checkbox] {
    width: 16px; height: 16px; accent-color: var(--accent);
    cursor: pointer; border-radius: 4px;
  }

  /* ---- Send Button ---- */
  .send-btn {
    width: 100%; padding: 13px; font-size: 14px; font-weight: 590;
    color: #fff; background: linear-gradient(180deg, #0077ed 0%, #0062cc 100%);
    border: none; border-radius: var(--radius);
    cursor: pointer; display: flex; align-items: center;
    justify-content: center; gap: 7px;
    box-shadow: 0 2px 8px rgba(0,113,227,0.3);
    transition: all 0.15s ease;
    font-family: var(--font); letter-spacing: 0.01em;
  }
  .send-btn:hover { background: linear-gradient(180deg, #1083f5 0%, #006edb 100%); }
  .send-btn:active { transform: scale(0.985); box-shadow: 0 1px 4px rgba(0,113,227,0.2); }
  .send-btn:disabled { opacity: 0.45; pointer-events: none; transform: none; }

  /* ---- Log ---- */
  .log-box {
    background: #f0f1f3;
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: 12px 14px; height: 145px; overflow-y: auto;
    font-size: 11.5px; font-family: var(--mono); color: #3c3c40;
    line-height: 1.75;
  }
  .log-box::-webkit-scrollbar { width: 4px; }
  .log-box::-webkit-scrollbar-track { background: transparent; }
  .log-box::-webkit-scrollbar-thumb { background: #ccc; border-radius: 4px; }
  .log-box::-webkit-scrollbar-thumb:hover { background: #aaa; }
  .log-time { color: #0071e3; margin-right: 10px; font-weight: 500; }
  .log-ok { color: #34c759; }
  .log-err { color: #ff3b30; }
  .log-link { color: #ff9f0a; }

</style>
</head>
<body>

<div class="title-bar">
  <div class="title-left">
    <div class="app-icon">
      <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polygon points="23 7 16 12 23 17 23 7"/>
        <rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
      </svg>
    </div>
    <span class="title-text">腾讯会议录制同步</span>
    <span class="version-badge">v1.0</span>
  </div>
  <div class="win-dots">
    <div class="dot red"></div>
    <div class="dot yellow"></div>
    <div class="dot green"></div>
  </div>
</div>

<div class="body">

  <div class="field-group">
    <div class="field-label">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
      钉钉 Webhook
    </div>
    <input class="field-input" id="webhook" type="text"
           placeholder="填入钉钉群机器人的 Webhook 地址，修改自动保存" />
  </div>

  <div class="divider"></div>

  <div class="field-group">
    <div class="field-label">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
      腾讯会议账号
    </div>
    <div class="login-card">
      <button class="login-btn" id="loginBtn" onclick="doLogin()">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg>
        登录腾讯会议
      </button>
      <div class="status-dot" id="statusDot"></div>
      <span class="status-text" id="statusText">未登录</span>
    </div>
  </div>

  <div class="divider"></div>

  <div class="field-group">
    <div class="field-label">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      关键词筛选
    </div>
    <input class="field-input" id="keyword" type="text"
           placeholder="输入关键词，回车直接推送，留空获取最近全部录制" />
    <div class="checkbox-row">
      <label class="checkbox-label">
        <input type="checkbox" id="transcriptCheck" />
        包含转写链接
      </label>
      <label class="checkbox-label">
        <input type="checkbox" id="deepCheck" />
        强力搜索（翻遍所有录制直到找到）
      </label>
    </div>
  </div>

  <button class="send-btn" id="sendBtn" onclick="doSend()">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
    推送到钉钉
  </button>

  <div class="field-group">
    <div class="field-label">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
      运行日志
    </div>
    <div class="log-box" id="logBox">
      <div><span class="log-time">--:--:--</span>等待操作...</div>
    </div>
  </div>

</div>

<script>
var authed = false;

function t() {
  var d = new Date();
  return [d.getHours(), d.getMinutes(), d.getSeconds()]
    .map(function(n) { return String(n).padStart(2, '0'); }).join(':');
}

function log(msg, cls) {
  var div = document.createElement('div');
  div.innerHTML = '<span class="log-time">' + t() + '</span>' +
    (cls ? '<span class="' + cls + '">' + msg + '</span>' : msg);
  var box = document.getElementById('logBox');
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function setStatus(ok, text) {
  var dot = document.getElementById('statusDot');
  var st = document.getElementById('statusText');
  dot.className = 'status-dot';
  if (text === 'detecting') { dot.classList.add('detecting'); }
  else if (ok) { dot.classList.add('ok'); }
  st.textContent = text;
  authed = ok;
}

document.getElementById('webhook').addEventListener('change', function() {
  pywebview.api.save_webhook(this.value);
});

document.getElementById('keyword').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') doSend();
});

function doLogin() {
  var btn = document.getElementById('loginBtn');
  btn.disabled = true; btn.textContent = '⏳ 正在登录...';
  setStatus(false, '正在登录...');
  log('>>> 正在启动浏览器，请在弹出窗口中扫码登录...');
  pywebview.api.login().then(function(r) {
    btn.disabled = false; btn.textContent = '🔑 登录腾讯会议';
    if (r.ok) {
      setStatus(true, '已登录');
      log('登录成功', 'log-ok');
      btn.textContent = '🔄 重新登录';
    } else {
      setStatus(false, '登录失败');
      log('登录失败: ' + r.msg, 'log-err');
    }
  }).catch(function(e) {
    btn.disabled = false;
    setStatus(false, '登录失败');
    log('登录失败: ' + e, 'log-err');
  });
}

function doSend() {
  if (!authed) { log('请先登录腾讯会议账号', 'log-err'); return; }
  var btn = document.getElementById('sendBtn');
  btn.disabled = true; btn.textContent = '⏳ 正在处理...';

  pywebview.api.fetch_and_push(
    document.getElementById('keyword').value.trim(),
    document.getElementById('deepCheck').checked,
    document.getElementById('transcriptCheck').checked
  ).then(function(r) {
    (r.log || []).forEach(function(l) { log(l); });

    if (r.meetings && r.meetings.length > 0 && !r.pushed) {
      log('');
      log('────────── 录制链接 ──────────');
      r.meetings.forEach(function(m) {
        log('【' + m.subject + '】  会议号: ' + m.meeting_code + '  ' + m.start_time, 'log-ok');
        (m.video_urls || []).forEach(function(u) { log('  视频: ' + u, 'log-link'); });
        (m.transcript_urls || []).forEach(function(u) { log('  转写: ' + u, 'log-link'); });
      });
    }
  }).catch(function(e) {
    log('错误: ' + e, 'log-err');
  }).then(function() {
    btn.disabled = false; btn.textContent = '🚀 推送到钉钉';
  });
}

// Init
function tryInit() {
  if (typeof pywebview === 'undefined' || !pywebview.api) {
    setTimeout(tryInit, 200);
    return;
  }
  pywebview.api.get_config().then(function(cfg) {
    document.getElementById('webhook').value = cfg.dingtalk_webhook || '';
    setStatus(false, '检测中...');
    return pywebview.api.auto_login();
  }).then(function(r) {
    if (r.ok) {
      setStatus(true, '已登录');
      log('已恢复登录态（无需重复登录）');
      document.getElementById('loginBtn').textContent = '🔄 重新登录';
    } else {
      setStatus(false, '未登录');
      log('自动登录失败: ' + r.msg, 'log-err');
    }
  }).catch(function(e) {
    setStatus(false, '未登录');
    log('初始化失败: ' + e, 'log-err');
  });
}
setTimeout(tryInit, 300);
</script>
</body>
</html>
"""


def main():
    api = API()
    window = webview.create_window(
        "腾讯会议录制同步",
        html=HTML,
        js_api=api,
        width=520,
        height=730,
        min_size=(440, 520),
        resizable=True,
    )
    webview.start(debug=False, http_server=False)


if __name__ == "__main__":
    main()
