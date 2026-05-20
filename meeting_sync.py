#!/usr/bin/env python3
"""腾讯会议录制链接自动推送钉钉

用 Playwright 登录获取 cookie，调内部 API 拿录制列表（含分享链接），
同一会议的视频和转写归为一组，推送到钉钉群。
"""

import json
import os
import sys
import time
import re
import random
import string
from datetime import datetime

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import requests
from playwright.sync_api import sync_playwright

if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
SENT_RECORDS_PATH = os.path.join(SCRIPT_DIR, ".sent_records.json")
USER_DATA_DIR = os.path.join(SCRIPT_DIR, "browser_data")

API_BASE = "https://meeting.tencent.com"
RECORD_LIST_PATH = "/wemeet-tapi/v2/meetlog/dashboard/my-record-list"
SHARE_BASE = "https://meeting.tencent.com"
MAX_MEETINGS = 6


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_sent():
    if os.path.exists(SENT_RECORDS_PATH):
        with open(SENT_RECORDS_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_sent(ids):
    with open(SENT_RECORDS_PATH, "w", encoding="utf-8") as f:
        json.dump(list(ids), f)


# ---------- 登录 ----------
def _extract_credentials(page, context):
    """从已登录页面提取 cookie 和 corp_id"""
    cookies = context.cookies()
    cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in cookies])
    corp_id = ""
    for c in cookies:
        if c["name"] == "account_corp_id":
            corp_id = c["value"]
            break
    if not corp_id:
        corp_id = page.evaluate("() => localStorage.getItem('account_corp_id') || '583198821'")
    return cookie_str, corp_id


COOKIE_CACHE = os.path.join(SCRIPT_DIR, ".cookie_cache.json")


def _load_cached_cookies():
    if os.path.exists(COOKIE_CACHE):
        with open(COOKIE_CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_cached_cookies(cookie_str, corp_id):
    with open(COOKIE_CACHE, "w", encoding="utf-8") as f:
        json.dump({"cookie_str": cookie_str, "corp_id": corp_id}, f)


def _validate_cookies(cookie_str, corp_id):
    """用一次 API 调用验证 cookie 是否还有效"""
    import requests as _r
    try:
        ts = str(int(time.time() * 1000))
        url = f"https://meeting.tencent.com/wemeet-cloudrecording-webapi/v1/space?c_app_id=&c_os_model=web&c_os=web&c_os_version=web&c_timestamp={ts}&c_instance_id=5&c_account_corp_id={corp_id}&c_lang=zh-cn"
        resp = _r.get(url, headers={
            "Cookie": cookie_str,
            "Referer": "https://meeting.tencent.com/user-center/meeting-record",
        }, timeout=5)
        return resp.status_code == 200 and resp.json().get("code") == 0
    except Exception:
        return False


def login_and_get_context(force_login=False):
    """获取登录态。有缓存则静默登录，无缓存或强制登录则弹窗扫码。
    返回 (cookie_str, corp_id)"""
    # --- 先尝试从缓存文件读取 cookie ---
    if not force_login:
        cached = _load_cached_cookies()
        if cached and _validate_cookies(cached["cookie_str"], cached["corp_id"]):
            return cached["cookie_str"], cached["corp_id"]

    # --- 尝试 headless 静默登录 ---
    if not force_login:
        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=USER_DATA_DIR,
                    headless=True,
                    viewport={"width": 1280, "height": 800},
                )
                page = context.new_page()
                page.goto("https://meeting.tencent.com/user-center/", wait_until="domcontentloaded", timeout=30_000)

                if "login" not in page.url:
                    page.goto("https://meeting.tencent.com/user-center/meeting-record", wait_until="domcontentloaded", timeout=30_000)
                    page.wait_for_load_state("domcontentloaded", timeout=10_000)
                    cookie_str, corp_id = _extract_credentials(page, context)
                    page.close()
                    context.close()
                    _save_cached_cookies(cookie_str, corp_id)
                    return cookie_str, corp_id

                page.close()
                context.close()
        except Exception:
            pass
        raise RuntimeError("需要重新登录")

    # --- 弹窗扫码登录 ---
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        page.goto("https://meeting.tencent.com/user-center/", wait_until="domcontentloaded", timeout=30_000)

        if "login" in page.url:
            print("\n>>> 请在浏览器中完成登录（微信/企业微信扫码或手机号）...")
            print("   登录成功后脚本会自动继续。\n")
            page.wait_for_url("**/user-center/**", timeout=300_000)
            print("[OK] 登录成功！")
            time.sleep(2)

        page.goto("https://meeting.tencent.com/user-center/meeting-record", wait_until="domcontentloaded", timeout=15_000)
        time.sleep(1)

        cookie_str, corp_id = _extract_credentials(page, context)
        page.close()
        context.close()
        _save_cached_cookies(cookie_str, corp_id)
        return cookie_str, corp_id


# ---------- API ----------
def make_nonce():
    return "".join(random.choices(string.ascii_letters + string.digits, k=9))


def make_trace_id():
    return "".join(random.choices("0123456789abcdef", k=32))


def call_record_api(cookie_str, corp_id, page_index=1, page_size=10):
    """调内部录制列表 API"""
    ts = str(int(time.time() * 1000))
    nonce = make_nonce()
    trace_id = make_trace_id()

    params = {
        "c_app_id": "",
        "c_os_model": "web",
        "c_os": "web",
        "c_os_version": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "c_timestamp": ts,
        "c_nonce": nonce,
        "c_app_version": "",
        "c_instance_id": "5",
        "c_account_corp_id": corp_id,
        "rnds": nonce,
        "c_app_uid": "",
        "c_district": "0",
        "trace-id": trace_id,
        "c_lang": "zh-CN",
    }

    body = {
        "begin_time": "0",
        "end_time": "0",
        "meeting_code": "",
        "page_index": page_index,
        "page_size": page_size,
        "aggregationFastRecording": 0,
        "cover_image_type": "meetlog_list_webp",
        "record_type_v4": "fast_record|cloud_record|user_upload|realtime_transcription|voice_record",
        "sort_by": "uni_record_id",
        "record_scene": 1,
    }

    headers = {
        "Content-Type": "application/json",
        "Cookie": cookie_str,
        "Referer": "https://meeting.tencent.com/user-center/meeting-record",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "web-caller": "my_meetings",
    }

    url = f"{API_BASE}{RECORD_LIST_PATH}"
    try:
        resp = requests.post(url, params=params, json=body, headers=headers, timeout=8)
        return resp.json()
    except Exception as e:
        return {"code": -1, "msg": str(e)}


# ---------- 解析 ----------
def parse_record(r):
    """从 API 返回的单条记录中提取关键字段"""
    meeting_info = r.get("meeting_info", {})
    title = r.get("title", "")
    record_type = r.get("record_type", "")

    is_transcript = record_type == "realtime_transcription"
    clean_subject = meeting_info.get("subject", title)
    if clean_subject.startswith("转写_"):
        clean_subject = clean_subject[3:]

    meeting_code = meeting_info.get("meeting_code", "")

    # 时间
    st = r.get("start_time", "")
    start_time = ""
    if st:
        try:
            dt = datetime.fromtimestamp(int(st) / 1000)
            start_time = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass

    # 时长 (毫秒)
    dur = r.get("duration", "")
    duration = ""
    if dur:
        try:
            s = int(dur) / 1000
            h, m = int(s // 3600), int((s % 3600) // 60)
            sec = int(s % 60)
            duration = f"{h:02d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"
        except Exception:
            pass

    # 文件大小
    size = r.get("size", "")
    size_str = ""
    if size:
        try:
            b = int(size)
            if b > 1024 * 1024 * 1024:
                size_str = f"{b/(1024**3):.1f}GB"
            elif b > 1024 * 1024:
                size_str = f"{b/(1024**2):.1f}MB"
        except Exception:
            pass

    # 分享链接
    share_url = r.get("share_url", "") or r.get("share_url_v2", "")
    if share_url and not share_url.startswith("http"):
        share_url = f"{SHARE_BASE}/{share_url.lstrip('/')}"

    # 短链接（优先用短的）
    share_url_short = r.get("share_url_short", "")
    if share_url_short and not share_url_short.startswith("http"):
        share_url_short = f"{SHARE_BASE}/{share_url_short.lstrip('/')}"

    final_url = share_url_short or share_url

    # 仅日期部分用于分组（同一会议可能有多条，时间戳微差）
    date_only = start_time[:10] if start_time else ""
    timestamp_ms = int(st) if st else 0

    return {
        "subject": clean_subject,
        "is_transcript": is_transcript,
        "meeting_code": meeting_code,
        "start_time": start_time,
        "date_only": date_only,
        "duration": duration,
        "size": size_str,
        "share_url": final_url,
        "record_id": r.get("record_id", ""),
        "timestamp_ms": timestamp_ms,
    }


def group_by_meeting(parsed):
    """同一会议按会议号+日期合并，同一日期内的视频和转写链接全部合并"""
    groups = {}
    for r in parsed:
        key = (r["subject"], r["meeting_code"], r["date_only"])
        if key not in groups:
            groups[key] = {
                "subject": r["subject"],
                "meeting_code": r["meeting_code"],
                "start_time": r["start_time"],
                "has_video": False,
                "has_transcript": False,
                "duration": "",
                "size": "",
                "video_urls": [],
                "transcript_urls": [],
            }
        g = groups[key]
        # 取最早的开始时间
        if r["start_time"] and (not g["start_time"] or r["start_time"] < g["start_time"]):
            g["start_time"] = r["start_time"]

        if r["is_transcript"]:
            g["has_transcript"] = True
            if r["share_url"] and r["share_url"] not in g["transcript_urls"]:
                g["transcript_urls"].append(r["share_url"])
        else:
            g["has_video"] = True
            g["duration"] = r["duration"] or g["duration"]
            g["size"] = r["size"] or g["size"]
            if r["share_url"] and r["share_url"] not in g["video_urls"]:
                g["video_urls"].append(r["share_url"])

    # 按时间倒序排序
    result = sorted(groups.values(), key=lambda m: m["start_time"], reverse=True)
    return result


# ---------- 钉钉 ----------
def send_to_dingtalk(webhook_url, meetings):
    if not meetings:
        return {"errcode": 0}

    lines = [
        "## 腾讯会议录制汇总",
        f"共 {len(meetings)} 个会议\n",
    ]
    for idx, m in enumerate(meetings, 1):
        name = m.get("subject", "未知会议")
        code = m.get("meeting_code", "")
        t = m.get("start_time", "")

        lines.append(f"**{idx}. {name}**  ")
        if code:
            lines.append(f"> 会议号：{code}  ")
        if t:
            lines.append(f"> 时间：{t}  ")

        if m.get("has_video"):
            for url in m.get("video_urls", []):
                lines.append(f"> 视频：{url}  ")

        if m.get("has_transcript"):
            for url in m.get("transcript_urls", []):
                lines.append(f"> 转写：{url}  ")

        lines.append("")

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": "腾讯会议录制汇总",
            "text": "\n".join(lines),
        },
    }
    resp = requests.post(webhook_url, json=payload, timeout=15)
    return resp.json()


# ---------- 供 GUI 调用的核心函数 ----------
def fetch_and_process(cookie_str, corp_id, keyword="", deep_search=False, include_transcript=True):
    """拉取录制列表 → 解析 → 分组 → 关键词筛选 → 去重
    返回 (meetings, log_lines)
    """
    log = []

    log.append(">>> 正在拉取录制列表...")
    max_pages = 200 if deep_search else 8  # 强力搜索翻所有录制，普通最多80条
    all_records = []
    for page in range(1, max_pages):
        result = call_record_api(cookie_str, corp_id, page_index=page, page_size=10)
        if result.get("code") != 0:
            break
        records = result.get("data", {}).get("records", [])
        if not records:
            break
        all_records.extend(records)

    log.append(f"   共获取到 {len(all_records)} 条原始记录")

    if not all_records:
        log.append("[OK] 没有找到录制记录。")
        return [], log

    parsed = [parse_record(r) for r in all_records]
    meetings = group_by_meeting(parsed)
    if not keyword:
        meetings = meetings[:MAX_MEETINGS]  # 无关键词时只取最近 6 个
    log.append(f"   分组后共 {len(meetings)} 个会议")

    if keyword:
        # 把关键词按 中英文/数字 边界拆成 token，每个 token 都命中才算匹配
        # "DEMO联咏" → ["demo", "联咏"]  两者都要出现在会议名中
        raw = keyword.lower().strip()
        # 在中文↔英文/数字边界插入空格
        sep = ""
        prev = ""
        for ch in raw:
            if prev and (
                (prev.isascii() and ord(ch) > 127) or
                (ord(prev) > 127 and ch.isascii())
            ):
                sep += " "
            sep += ch
            prev = ch
        tokens = [t for t in sep.split() if t]

        def match(subject):
            s = subject.lower().replace(" ", "").replace("　", "")
            return all(t in s for t in tokens)

        meetings = [m for m in meetings if match(m["subject"])]
        log.append(f"   关键词「{keyword}」筛选后: {len(meetings)} 个会议")
        if not meetings:
            log.append("[OK] 没有匹配的会议。")
            return [], log

    # 是否包含转写链接
    if not include_transcript:
        for m in meetings:
            m["transcript_urls"] = []
            m["has_transcript"] = False

    sent = load_sent()
    new_meetings = []
    for m in meetings:
        rid = f"{m['subject']}|{m['meeting_code']}|{m['start_time'][:10] if m.get('start_time') else ''}"
        if rid not in sent:
            new_meetings.append(m)
            sent.add(rid)

    if not new_meetings:
        log.append("[OK] 没有新录制。")
        return [], log

    log.append(f"   发现 {len(new_meetings)} 个新会议:")
    for m in new_meetings:
        parts = []
        if m["has_video"]:
            parts.append("视频")
        if m["has_transcript"]:
            parts.append("转写")
        log.append(f"   - {m['subject']} ({', '.join(parts)})")

    # 保存去重状态
    save_sent(sent)

    return new_meetings, log


# ---------- CLI 入口（保留向后兼容） ----------
if __name__ == "__main__":
    cfg = load_config()
    if cfg.get("dingtalk_webhook", "").startswith("你的"):
        print("[X] 请先填写 config.json 中的 dingtalk_webhook")
        sys.exit(1)

    cookie_str, corp_id = login_and_get_context()
    keyword = sys.argv[1].strip() if len(sys.argv) > 1 else ""

    meetings, log = fetch_and_process(cookie_str, corp_id, keyword)
    for line in log:
        print(line)

    if meetings:
        print(">>> 推送到钉钉...")
        result = send_to_dingtalk(cfg["dingtalk_webhook"], meetings)
        if result.get("errcode") == 0:
            print("[OK] 推送成功！")
        else:
            print(f"[X] 钉钉推送失败: {result}")

    print("完成。")
