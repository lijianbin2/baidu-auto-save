#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Baidu-Auto-Save WebUI（界面与操作对齐 Cp0204/quark-auto-save，网盘层为百度网盘）
from flask import (
    json,
    Flask,
    url_for,
    session,
    jsonify,
    request,
    redirect,
    Response,
    render_template,
    send_from_directory,
    stream_with_context,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import timedelta
import subprocess
import requests
import hashlib
import logging
import traceback
import sys
import urllib.request
import os
import re

parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, parent_dir)
from baidu_auto_save import Baidu, Config, MagicRename

print(
    r"""
   ____    ___  ____  ____  __  __
  | __ )  / _ \/ ___||  _ \|  \/  |
  |  _ \ | | | \___ \| | | | |\/| |
  | |_) || |_| |___) | |_| | |  | |
  |____/  \___/|____/|____/|_|  |_|
  -- Baidu-Auto-Save (UI like quark-auto-save) --
 """
)
sys.stdout.flush()


def get_app_ver():
    try:
        with open("build.json", "r") as f:
            build_info = json.loads(f.read())
            BUILD_SHA = build_info["BUILD_SHA"]
            BUILD_TAG = build_info["BUILD_TAG"]
    except Exception:
        BUILD_SHA = os.getenv("BUILD_SHA", "")
        BUILD_TAG = os.getenv("BUILD_TAG", "")
    if BUILD_TAG[:1] == "v":
        return BUILD_TAG
    elif BUILD_SHA:
        return f"{BUILD_TAG}({BUILD_SHA[:7]})"
    else:
        return "dev"


# 文件路径
PYTHON_PATH = "python3" if os.path.exists("/usr/bin/python3") else "python"
SCRIPT_PATH = os.environ.get("SCRIPT_PATH", "./baidu_auto_save.py")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "./config/baidu_config.json")
PLUGIN_FLAGS = os.environ.get("PLUGIN_FLAGS", "")
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", 5006))
TASK_TIMEOUT = int(os.environ.get("TASK_TIMEOUT", 1800))

config_data = {}
task_plugins_config_default = {}

app = Flask(__name__)
app.config["APP_VERSION"] = get_app_ver()
app.secret_key = "baidu-auto-save-secret-key"
app.config["SESSION_COOKIE_NAME"] = "BAIDU_AUTO_SAVE_SESSION"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=31)
app.json.ensure_ascii = False
app.json.sort_keys = False
app.jinja_env.variable_start_string = "[["
app.jinja_env.variable_end_string = "]]"

scheduler = BackgroundScheduler()
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="[%(asctime)s][%(levelname)s] %(message)s",
    datefmt="%m-%d %H:%M:%S",
)
if not DEBUG:
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.getLogger("apscheduler").setLevel(logging.ERROR)
    sys.modules["flask.cli"].show_server_banner = lambda *x: None


def gen_md5(string):
    md5 = hashlib.md5()
    md5.update(string.encode("utf-8"))
    return md5.hexdigest()


def get_login_token():
    username = config_data["webui"]["username"]
    password = config_data["webui"]["password"]
    return gen_md5(f"token{username}{password}+-*/")[8:24]


def is_login():
    login_token = get_login_token()
    if session.get("token") == login_token or request.args.get("token") == login_token:
        return True
    else:
        return False


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = config_data["webui"]["username"]
        password = config_data["webui"]["password"]
        if (username == request.form.get("username")) and (
            password == request.form.get("password")
        ):
            logging.info(f">>> 用户 {username} 登录成功")
            session.permanent = True
            session["token"] = get_login_token()
            return redirect(url_for("index"))
        else:
            logging.info(f">>> 用户 {username} 登录失败")
            return render_template("login.html", message="登录失败")

    if is_login():
        return redirect(url_for("index"))
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.pop("token", None)
    return redirect(url_for("login"))


@app.route("/")
def index():
    if not is_login():
        return redirect(url_for("login"))
    return render_template(
        "index.html", version=app.config["APP_VERSION"], plugin_flags=PLUGIN_FLAGS
    )


@app.route("/data")
def get_data():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    data = Config.read_json(CONFIG_PATH)
    del data["webui"]
    data["api_token"] = get_login_token()
    data["task_plugins_config_default"] = task_plugins_config_default
    return jsonify({"success": True, "data": data})


@app.route("/update", methods=["POST"])
def update():
    global config_data
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    allowed_keys = ["crontab", "push_config", "tasklist",
                    "magic_regex", "plugins", "source"]
    for key, value in request.json.items():
        if key in allowed_keys:
            config_data.update({key: value})
    Config.write_json(CONFIG_PATH, config_data)
    if reload_tasks():
        logging.info(">>> 配置更新成功")
        return jsonify({"success": True, "message": "配置更新成功"})
    else:
        logging.info(">>> 配置更新失败")
        return jsonify({"success": False, "message": "配置更新失败"})
@app.route("/run_script_now", methods=["POST"])
def run_script_now():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    body = request.json or {}
    tasklist = body.get("tasklist", [])
    command = [PYTHON_PATH, "-u", SCRIPT_PATH, CONFIG_PATH]
    logging.info(
        f">>> 手动运行任务 [{tasklist[0].get('taskname') if len(tasklist)>0 else 'ALL'}] 开始执行..."
    )

    def generate_output():
        process_env = os.environ.copy()
        process_env["PYTHONIOENCODING"] = "utf-8"
        if body.get("baidu_test"):
            process_env["BAIDU_TEST"] = "true"
            process_env["TASKLIST"] = json.dumps(
                body.get("tasklist", []), ensure_ascii=False
            )
            process_env["PUSH_CONFIG"] = json.dumps(
                body.get("push_config", {}), ensure_ascii=False
            )
        if tasklist:
            process_env["TASKLIST"] = json.dumps(tasklist, ensure_ascii=False)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=process_env,
        )
        try:
            for line in iter(process.stdout.readline, ""):
                logging.info(line.strip())
                yield f"data: {line}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            process.stdout.close()
            process.wait()

    return Response(
        stream_with_context(generate_output()),
        content_type="text/event-stream;charset=utf-8",
    )


@app.route("/bdpan_auth_url")
def bdpan_auth_url():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    account = Baidu()
    ret = account.get_auth_url()
    if not ret.get("ok"):
        return jsonify({"success": False, "message": ret.get("err", "获取授权链接失败")})
    return jsonify({"success": True, "auth_url": ret["auth_url"]})


@app.route("/bdpan_login", methods=["POST"])
def bdpan_login():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    code = (request.json or {}).get("code", "")
    account = Baidu()
    ret = account.login_with_code(code)
    if not ret.get("ok"):
        return jsonify({"success": False, "message": ret.get("err", "登录失败")})
    return jsonify({"success": True, "message": "登录成功", "nickname": ret.get("nickname", "")})


@app.route("/test_push", methods=["POST"])
def test_push():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    cfg = dict(config_data.get("push_config") or {})
    override = (request.json or {}).get("push_config") or {}
    cfg.update({k: v for k, v in override.items() if v not in (None, "")})
    token = (cfg.get("PUSH_PLUS_TOKEN") or "").strip()
    if not token:
        return jsonify({"success": False, "message": "请先填写 PushPlus Token"})
    topic = (cfg.get("PUSH_PLUS_USER") or "").strip()
    try:
        r = requests.post(
            "https://www.pushplus.plus/send",
            json={
                "token": token,
                "title": "【百度自动转存】通知测试",
                "content": "推送通道正常，之后有转存动态会推送到微信",
                "topic": topic,
                "template": "html",
                "channel": "wechat",
            },
            timeout=20,
        ).json()
    except Exception as e:
        return jsonify({"success": False, "message": f"请求失败：{e}"})
    if r.get("code") == 200:
        return jsonify({"success": True, "message": "测试消息已发送，请查收微信"})
    return jsonify({"success": False, "message": f"推送失败：{r.get('msg', r)}"})
@app.route("/bdpan_logout", methods=["POST"])
def bdpan_logout():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    account = Baidu()
    account.logout()
    still = account.verify()
    if still:
        return jsonify({"success": False, "message": "退出失败，登录态仍在，请重试或在容器内执行 bdpan logout"})
    return jsonify({"success": True, "message": "已退出百度网盘"})


@app.route("/account_info")
def account_info():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    account = Baidu()
    info = account.verify()
    return jsonify({
        "success": True,
        "data": {
            "active": bool(info),
            "nickname": account.nickname if info else "",
            "vip_type": account.vip_type if info else None,
            "save_root": "我的应用数据/bdpan/",
            "login_hint": "docker exec -it baidu-auto-save bdpan login",
        },
    })


def _share_item_adapter(item, share_dir=""):
    """分享/目录条目统一为前端需要的形状"""
    sp = (item.get("path") or "").strip().lstrip("/")
    if not sp:
        base = (share_dir or "").strip().strip("/")
        name = item.get("file_name", "")
        sp = f"{base}/{name}".strip("/") if base else name
    entry = {
        "file_name": item.get("file_name", ""),
        "dir": bool(item.get("dir")),
        "fid": sp,
        "fsid": item.get("fsid", ""),
        "size": item.get("size", 0),
        "path": sp,
        "obj_category": item.get("obj_category", ""),
        "updated_at": item.get("updated_at", 0),
    }
    return entry


def _ancestor_paths(rel):
    rel = (rel or "").strip().strip("/")
    if not rel:
        return []
    parts = rel.split("/")
    paths = []
    cur = ""
    for p in parts:
        cur = f"{cur}/{p}".strip("/") if cur else p
        paths.append({"fid": cur, "name": p})
    return paths


def _extract_share_title(raw, url, pwd, items):
    """尽力推导百度分享标题：bdpan 原始字段 → 分享页 title → 单一顶层目录名"""
    def _pick(d):
        if not isinstance(d, dict):
            return ""
        for k in ("title", "share_title", "shareTitle", "share_name", "shareName", "name"):
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""
    if isinstance(raw, dict):
        for cand in (raw, raw.get("data")):
            t = _pick(cand)
            if t:
                return t
        data = raw.get("data")
        if isinstance(data, dict):
            for sub in ("share", "info", "link"):
                t = _pick(data.get(sub))
                if t:
                    return t
    t = _shorturl_title(url)
    if t:
        return t
    if isinstance(items, list) and len(items) == 1 and items[0].get("dir"):
        name = (items[0].get("file_name") or "").strip()
        if name:
            return name
    return ""


def _shorturl_title(url):
    """调百度 shorturlinfo 接口拿分享标题（无须登录，最干净）"""
    try:
        m = re.search(r"/s/([A-Za-z0-9_\-]+)", url or "")
        if not m:
            return ""
        api = "https://pan.baidu.com/api/shorturlinfo?shorturl=" + m.group(1) + "&root=1"
        req = urllib.request.Request(api, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://pan.baidu.com/",
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
        if not isinstance(d, dict) or d.get("errno") not in (None, 0):
            return ""
        t = (d.get("title") or "").strip()
        return t if len(t) >= 2 else ""
    except Exception:
        return ""


@app.route("/get_share_detail", methods=["POST"])
def get_share_detail():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    req = request.json or {}
    shareurl = req.get("shareurl", "")
    source_dir = req.get("source_dir", "") or ""
    task = req.get("task") or {}
    magic_regex = req.get("magic_regex", {})
    account = Baidu()
    url, pwd, url_subdir = Baidu.extract_share(shareurl, task.get("pwd") or req.get("pwd", ""))
    if not source_dir:
        source_dir = url_subdir
    rs = account.list_share_all(url, pwd, source_dir)
    if not rs.get("ok"):
        raw = json.dumps(rs.get("raw", {}), ensure_ascii=False)
        detail = Baidu.err_text(rs.get("raw"))
        if "13003" in raw or "pwd" in raw.lower():
            err = "该分享需要提取码或提取码错误，请补充提取码后重试"
        elif "13004" in raw or "13000" in raw or "record not found" in raw:
            err = "分享链接无效、已失效，或提取码错误，请检查链接和提取码"
        elif detail and detail != rs.get("err", ""):
            err = detail
        else:
            err = rs.get("err") or detail or raw[:200]
        return jsonify({"success": False, "data": {"error": err}})

    entries = [_share_item_adapter(x, source_dir) for x in rs.get("list", [])]
    data = {
        "list": entries,
        "paths": _ancestor_paths(source_dir),
        "has_more": False,
        "share": {"title": _extract_share_title(rs.get("raw"), url, pwd, entries)},
    }

    if task:
        try:
            preview_regex(data, task, magic_regex)
        except Exception as e:
            logging.error(f">>> 正则预览失败: {e}")

    return jsonify({"success": True, "data": data})


def preview_regex(data, task, magic_regex):
    mr = MagicRename(magic_regex)
    mr.set_taskname(task.get("taskname", ""))
    account = Baidu()
    rd = account.ls_dir(task.get("savepath", "") or "")
    dir_file_list = [
        {"file_name": x["file_name"], "dir": x["dir"]}
        for x in (rd.get("list", []) if rd.get("ok") else [])
    ]
    dir_filename_list = [d["file_name"] for d in dir_file_list]
    pattern, replace = mr.magic_regex_conv(
        task.get("pattern", ""), task.get("replace", "")
    )
    for share_file in data["list"]:
        search_pattern = (
            task["update_subdir"]
            if share_file["dir"] and task.get("update_subdir")
            else pattern
        )
        try:
            matched = bool(re.search(search_pattern, share_file["file_name"])) if search_pattern else True
        except re.error:
            matched = False
        if not matched:
            continue
        if share_file["dir"]:
            share_file["file_name_re"] = share_file["file_name"]
        else:
            file_name_re = mr.sub(pattern, replace, share_file["file_name"])
            if mr.is_exists(
                file_name_re,
                dir_filename_list,
                task.get("ignore_extension"),
            ):
                share_file["file_name_saved"] = file_name_re
            else:
                share_file["file_name_re"] = file_name_re
    if re.search(r"\{I+\}", replace or ""):
        try:
            mr.set_dir_file_list(dir_file_list, replace)
            mr.sort_file_list(data["list"])
        except Exception as e:
            logging.error(f">>> 序号整理失败: {e}")


@app.route("/get_savepath_detail")
def get_savepath_detail():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    try:
        if fid := request.args.get("fid", None):
            file_list = _get_file_list(fid=fid)
        elif path := request.args.get("path", "/"):
            file_list = _get_file_list(path=path)
        return jsonify({"success": True, "data": file_list})
    except Exception as e:
        return jsonify({"success": False, "data": {"error": str(e)}})


def _get_file_list(fid=None, path=None):
    account = Baidu()
    if fid is not None and fid != "":
        rel = "" if str(fid) == "0" else str(fid)
    elif path:
        rel = str(path)
    else:
        rel = ""
    rel = re.sub(r"/{2,}", "/", rel).strip().strip("/")
    rd = account.ls_dir(rel)
    if not rd.get("ok"):
        raise FileNotFoundError(f"目录不存在或读取失败: /{rel}")
    entries = []
    for x in rd.get("list", []):
        name = x.get("file_name", "")
        full = f"{rel}/{name}".strip("/") if rel else name
        entries.append({
            "file_name": name,
            "dir": bool(x.get("dir")),
            "fid": full,
            "fsid": x.get("fsid", ""),
            "size": x.get("size", 0),
            "path": full,
            "obj_category": x.get("obj_category", ""),
            "updated_at": x.get("updated_at", 0),
        })
    return {"fid": rel or "0", "list": entries, "paths": _ancestor_paths(rel)}


def _path_to_fid(path):
    if not path:
        raise ValueError("路径不能为空")
    rel = re.sub(r"/{2,}", "/", str(path)).strip().strip("/")
    return rel


@app.route("/delete_file", methods=["POST"])
def delete_file():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    try:
        req = request.json or {}
        rel = req.get("fid") or _path_to_fid(req.get("path"))
        if rel in ("", "0"):
            raise ValueError("不能删除根目录")
        account = Baidu()
        response = account.delete_paths([rel])
        response["success"] = response.get("code") == 0
        return jsonify(response)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/rename_file", methods=["POST"])
def rename_file():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    try:
        req = request.json or {}
        rel = req.get("fid") or _path_to_fid(req.get("path"))
        file_name = req.get("file_name")
        if rel and file_name:
            save_dir = rel.rsplit("/", 1)[0] if "/" in rel else ""
            old_name = rel.rsplit("/", 1)[-1]
            account = Baidu()
            ok, raw = account.rename_file(save_dir, old_name, file_name)
            response = {"code": 0 if ok else 1, "message": "ok" if ok else json.dumps(raw, ensure_ascii=False)[:300]}
            response["success"] = ok
            return jsonify(response)
        else:
            raise ValueError("缺失必要字段: fid/path, file_name")
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/add_task", methods=["POST"])
def add_task():
    global config_data
    if not is_login():
        return jsonify({"success": False, "code": 1, "message": "未登录"}), 401
    request_data = request.json
    required_fields = ["taskname", "shareurl", "savepath"]
    for field in required_fields:
        if field not in request_data or not request_data[field]:
            return (
                jsonify({"success": False, "code": 2, "message": f"缺少必要字段: {field}"}),
                400,
            )
    if not request_data.get("addition"):
        request_data["addition"] = task_plugins_config_default
    config_data["tasklist"].append(request_data)
    Config.write_json(CONFIG_PATH, config_data)
    logging.info(f">>> 通过API添加任务: {request_data['taskname']}")
    return jsonify({"success": True, "code": 0, "message": "任务添加成功", "data": request_data})


def run_python(args):
    logging.info(">>> 定时运行任务")
    try:
        result = subprocess.run(
            f"{PYTHON_PATH} {args}",
            shell=True,
            timeout=TASK_TIMEOUT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    logging.info(line)
        if result.returncode == 0:
            logging.info(">>> 任务执行成功")
        else:
            logging.error(f">>> 任务执行失败，返回码: {result.returncode}")
            if result.stderr:
                logging.error(f"错误信息: {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        logging.error(f">>> 任务执行超时(>{TASK_TIMEOUT}s)，强制终止")
    except Exception as e:
        logging.error(f">>> 任务执行异常: {str(e)}")
        logging.error(traceback.format_exc())
    finally:
        logging.debug(">>> run_python 函数执行完成")


def reload_tasks():
    if crontab := config_data.get("crontab"):
        if scheduler.state == 1:
            scheduler.pause()
        trigger = CronTrigger.from_crontab(crontab)
        scheduler.remove_all_jobs()
        scheduler.add_job(
            run_python,
            trigger=trigger,
            args=[f"{SCRIPT_PATH} {CONFIG_PATH}"],
            id=SCRIPT_PATH,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
            replace_existing=True,
        )
        if scheduler.state == 0:
            scheduler.start()
        elif scheduler.state == 2:
            scheduler.resume()
        scheduler_state_map = {0: "停止", 1: "运行", 2: "暂停"}
        logging.info(">>> 重载调度器")
        logging.info(f"调度状态: {scheduler_state_map[scheduler.state]}")
        logging.info(f"定时规则: {crontab}")
        logging.info(f"现有任务: {scheduler.get_jobs()}")
        return True
    else:
        logging.info(">>> no crontab")
        return False


def init():
    global config_data, task_plugins_config_default
    logging.info(">>> 初始化配置")
    if not os.path.exists(CONFIG_PATH):
        if not os.path.exists(os.path.dirname(CONFIG_PATH)):
            os.makedirs(os.path.dirname(CONFIG_PATH))
        with open("baidu_config.json", "rb") as src, open(CONFIG_PATH, "wb") as dest:
            dest.write(src.read())

    config_data = Config.read_json(CONFIG_PATH)
    if not config_data.get("magic_regex"):
        config_data["magic_regex"] = MagicRename().magic_regex

    config_data["webui"] = {
        "username": os.environ.get("WEBUI_USERNAME")
        or config_data.get("webui", {}).get("username", "admin"),
        "password": os.environ.get("WEBUI_PASSWORD")
        or config_data.get("webui", {}).get("password", "admin123"),
    }

    if not config_data.get("crontab"):
        config_data["crontab"] = os.environ.get("CRON", "0 8,18,20 * * *")

    _, plugins_config_default, task_plugins_config_default = Config.load_plugins()
    for name, config in plugins_config_default.items():
        for key, value in config.items():
            config[key] = (
                config_data.setdefault("plugins", {})
                .setdefault(name, {})
                .get(key, value)
            )
    config_data["plugins"] = plugins_config_default

    Config.write_json(CONFIG_PATH, config_data)


if __name__ == "__main__":
    init()
    reload_tasks()
    logging.info(">>> 启动Web服务")
    logging.info(f"运行在: http://{HOST}:{PORT}")
    app.run(
        debug=DEBUG,
        host=HOST,
        port=PORT,
    )
