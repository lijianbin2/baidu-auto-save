#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Baidu-Auto-Save WebUI（个人自用最小版）"""
import os
import sys
import json
import hashlib
import subprocess
import logging
from datetime import timedelta, datetime
from flask import Flask, session, jsonify, request, Response, render_template, stream_with_context

parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, parent_dir)
from baidu_auto_save import Baidu, Config

print(r"""
 ____    ___  ____  ____  __  __
| __ )  / _ \/ ___||  _ \|  \/  |
|  _ \ | | | \___ \| | | | |\/| |
| |_) || |_| |___) | |_| | |  | |
|____/  \___/|____/|____/|_|  |_|
-- Baidu-Auto-Save --
""")
sys.stdout.flush()

PYTHON_BIN = "python3" if os.path.exists("/usr/bin/python3") else "python"
SCRIPT_PATH = os.environ.get("SCRIPT_PATH", "./baidu_auto_save.py")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "./config/baidu_config.json")
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", 5006))
TASK_TIMEOUT = int(os.environ.get("TASK_TIMEOUT", 1800))
CRON = os.environ.get("CRON", "0 8,18,20 * * *")

config_data = {}
app = Flask(__name__)
app.secret_key = "baidu-auto-save-secret"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=31)
app.json.ensure_ascii = False

logging.basicConfig(level=logging.DEBUG if DEBUG else logging.INFO, format="[%(asctime)s][%(levelname)s] %(message)s", datefmt="%m-%d %H:%M:%S")
if not DEBUG:
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

def _ensure_config():
    global config_data
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(os.path.dirname(os.path.abspath(CONFIG_PATH)) or ".", exist_ok=True)
        Config.write_json(CONFIG_PATH, {"push_config": {}, "plugins": {}, "magic_regex": {}, "tasklist": []})
    config_data = Config.read_json(CONFIG_PATH)
    config_data.setdefault("webui", {})
    config_data["webui"].setdefault("username", os.environ.get("WEBUI_USERNAME", "admin"))
    config_data["webui"].setdefault("password", os.environ.get("WEBUI_PASSWORD", "admin123"))

def _token():
    u = config_data["webui"]["username"]; p = config_data["webui"]["password"]
    return hashlib.md5(f"token{u}{p}+-*/".encode()).hexdigest()[8:24]

def _login():
    return session.get("token") == _token() or request.args.get("token") == _token()

@app.route("/login", methods=["GET", "POST"])
def login():
    _ensure_config()
    if request.method == "POST":
        u = request.form.get("username", ""); p = request.form.get("password", "")
        if u == config_data["webui"]["username"] and p == config_data["webui"]["password"]:
            session["token"] = _token()
            return jsonify({"ok": True})
        return jsonify({"ok": False, "msg": "账号或密码错误"}), 401
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})

@app.route("/")
def index():
    _ensure_config()
    if not _login():
        return render_template("login.html")
    return render_template("index.html")

@app.route("/data")
def get_data():
    _ensure_config()
    if not _login():
        return jsonify({"ok": False}), 401
    acct = Baidu()
    info = acct.verify()
    return jsonify({"ok": True, "config": config_data, "account": {"nickname": acct.nickname, "active": acct.is_active, "vip_type": acct.vip_type}, "cron": CRON})

@app.route("/update", methods=["POST"])
def update():
    _ensure_config()
    if not _login():
        return jsonify({"ok": False}), 401
    try:
        data = request.get_json(force=True)
        Config.write_json(CONFIG_PATH, data)
        _ensure_config()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 400

def _run_cmd(cmd, timeout=TASK_TIMEOUT):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    try:
        for line in p.stdout:
            yield line
        p.wait(timeout=timeout)
    except Exception as e:
        yield f"\n[ERROR] {e}\n"
    finally:
        try: p.kill()
        except Exception: pass

@app.route("/run_script_now", methods=["POST"])
def run_script_now():
    if not _login():
        return jsonify({"ok": False}), 401
    def gen():
        yield "🚀 开始执行 baidu_auto_save.py\n\n"
        for line in _run_cmd([PYTHON_BIN, SCRIPT_PATH, CONFIG_PATH]):
            yield line
        yield "\n✅ 执行结束\n"
    return Response(stream_with_context(gen()), mimetype="text/plain; charset=utf-8")

@app.route("/get_share_detail", methods=["POST"])
def get_share_detail():
    if not _login():
        return jsonify({"ok": False}), 401
    body = request.get_json(force=True)
    url = body.get("shareurl", ""); pwd = body.get("pwd", "")
    source_dir = body.get("source_dir", ""); page = int(body.get("page", 1))
    acct = Baidu()
    clean, pp = Baidu.extract_share(url, pwd)
    r = acct.list_share(clean, pp, source_dir, page, 100)
    if not r["ok"]:
        return jsonify({"ok": False, "err": r.get("err", "查询失败"), "raw": r.get("raw")})
    return jsonify({"ok": True, "list": [{"file_name": x["file_name"], "dir": x["dir"], "fsid": x["fsid"], "size": x.get("size"), "path": x.get("path")} for x in r["list"]], "has_more": r["has_more"], "page": page})

@app.route("/get_savepath_detail")
def get_savepath_detail():
    if not _login():
        return jsonify({"ok": False}), 401
    path = request.args.get("path", "")
    acct = Baidu()
    r = acct.ls_dir(path)
    if not r["ok"]:
        return jsonify({"ok": False, "err": "目录不存在或查询失败"})
    return jsonify({"ok": True, "list": [{"file_name": x["file_name"], "dir": x["dir"], "size": x.get("size"), "path": x.get("path")} for x in r["list"]]})

def init_scheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        parts = CRON.strip().split()
        sched = BackgroundScheduler()
        if len(parts) == 5:
            m, h, d, mon, w = parts
            trig = CronTrigger(minute=m, hour=h, day=d, month=mon, day_of_week=w)
        elif len(parts) == 6:
            s, m, h, d, mon, w = parts
            trig = CronTrigger(second=s, minute=m, hour=h, day=d, month=mon, day_of_week=w)
        else:
            trig = CronTrigger.from_crontab(CRON)
        sched.add_job(lambda: list(_run_cmd([PYTHON_BIN, SCRIPT_PATH, CONFIG_PATH])), trigger=trig, id="auto_save")
        sched.start()
        print(f"⏰ 定时任务已启动: {CRON}")
    except Exception as e:
        print(f"⚠️ 定时器启动失败: {e}")

if __name__ == "__main__":
    _ensure_config()
    init_scheduler()
    app.run(host=HOST, port=PORT)
