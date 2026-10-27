#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Baidu-Auto-Save: 百度网盘自动转存（个人自用，基于 bdpan CLI）
# 逻辑移植自 Cp0204/quark-auto-save，网盘层替换为百度网盘（bdpan CLI）。
# 运行要求：已安装 bdpan 且完成登录（容器内 BDPAN_CONFIG_DIR 持久化）。
# 配置文件：baidu_config.json
"""
new Env('百度自动追更');
0 8,18,20 * * * baidu_auto_save.py
"""
import os
import re
import sys
import json
import time
import subprocess
import traceback
import shutil
import urllib.parse
from datetime import datetime
from natsort import natsorted

try:
    from treelib import Tree
except Exception:
    print("正在尝试自动安装依赖...")
    os.system("pip3 install treelib &> /dev/null")
    from treelib import Tree

CONFIG_DATA = {}
NOTIFYS = []


def send_ql_notify(title, body):
    try:
        import notify
        if CONFIG_DATA.get("push_config"):
            notify.push_config.update(CONFIG_DATA["push_config"])
            notify.push_config["CONSOLE"] = notify.push_config.get("CONSOLE", True)
        notify.send(title, body)
    except Exception as e:
        if e:
            print("发送通知消息失败！")


def add_notify(text):
    global NOTIFYS
    NOTIFYS.append(text)
    print("📢", text)
    return text


class Config:
    @staticmethod
    def read_json(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def write_json(config_path, data):
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, sort_keys=False, indent=2)

    @staticmethod
    def load_plugins(plugins_config=None, plugins_dir="plugins"):
        import importlib
        plugins_config = plugins_config or {}
        plugins_available = {}
        task_plugins_config = {}
        if not os.path.isdir(plugins_dir):
            return plugins_available, plugins_config, task_plugins_config
        for fname in sorted(os.listdir(plugins_dir)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            module_name = fname[:-3]
            if f"-{module_name}" in os.environ.get("PLUGIN_FLAGS", ""):
                continue
            try:
                module = importlib.import_module(f"{plugins_dir}.{module_name}")
                ServerClass = getattr(module, module_name.capitalize(), None)
                if ServerClass is None:
                    continue
                if module_name in plugins_config:
                    plugin = ServerClass(**plugins_config[module_name])
                else:
                    plugin = ServerClass()
                    plugins_config[module_name] = getattr(plugin, "default_config", {})
                plugins_available[module_name] = plugin
                if hasattr(plugin, "default_task_config"):
                    task_plugins_config[module_name] = plugin.default_task_config
            except Exception as e:
                print(f"载入模块 {module_name} 失败: {e}")
        return plugins_available, plugins_config, task_plugins_config



def _conv_share_init(url):
    """新版 share/init?surl=xxx 链接转旧版 /s/1xxx（bdpan 只认旧版格式）"""
    try:
        m = re.search(r"pan\.baidu\.com/share/init\?surl=([A-Za-z0-9_\-]+)", url or "")
        if not m:
            return url
        rest = url or ""
        pwdm = re.search(r"[?&]pwd=([A-Za-z0-9]{4})", rest)
        frag = ""
        if "#" in rest:
            frag = "#" + rest.split("#", 1)[1]
        new = "https://pan.baidu.com/s/1" + m.group(1)
        if pwdm:
            new += "?pwd=" + pwdm.group(1)
        return new + frag
    except Exception:
        return url


class MagicRename:

    magic_regex = {
        "$TV": {
            "pattern": r".*?([Ss]\d{1,2})?(?:[第EePpXx\.\-\_\( ]{1,2}|^)(\d{1,3})(?!\d).*?\.(mp4|mkv)",
            "replace": r"\1E\2.\3",
        },
        "$BLACK_WORD": {
            "pattern": r"^(?!.*纯享)(?!.*加更)(?!.*超前企划)(?!.*训练室)(?!.*蒸蒸日上).*",
            "replace": "",
        },
    }

    magic_variable = {
        "{TASKNAME}": "",
        "{I}": 1,
        "{EXT}": [r"(?<=\.)\w+$"],
        "{CHINESE}": [r"[\u4e00-\u9fa5]{2,}"],
        "{DATE}": [
            r"(18|19|20)?\d{2}[\.\-/年]\d{1,2}[\.\-/月]\d{1,2}",
            r"(?<!\d)[12]\d{3}[01]?\d[0123]?\d",
            r"(?<!\d)[01]?\d[\.\-/月][0123]?\d",
        ],
        "{YEAR}": [r"(?<!\d)(18|19|20)\d{2}(?!\d)"],
        "{S}": [r"(?<=[Ss])\d{1,2}(?=[EeXx])", r"(?<=[Ss])\d{1,2}"],
        "{SXX}": [r"[Ss]\d{1,2}(?=[EeXx])", r"[Ss]\d{1,2}"],
        "{E}": [
            r"(?<=[Ss]\d\d[Ee])\d{1,3}",
            r"(?<=[Ee])\d{1,3}",
            r"(?<=[Ee][Pp])\d{1,3}",
            r"(?<=第)\d{1,3}(?=[集期话部篇])",
            r"(?<!\d)\d{1,3}(?=[集期话部篇])",
            r"(?!.*19)(?!.*20)(?<=[\._])\d{1,3}(?=[\._])",
            r"^\d{1,3}(?=\.\w+)",
            r"(?<!\d)\d{1,3}(?!\d)(?!$)",
        ],
        "{PART}": [
            r"(?<=[集期话部篇第])[上中下一二三四五六七八九十]",
            r"[上中下一二三四五六七八九十]",
        ],
        "{VER}": [r"[\u4e00-\u9fa5]+版"],
    }

    priority_list = [
        "上",
        "中",
        "下",
        "一",
        "二",
        "三",
        "四",
        "五",
        "六",
        "七",
        "八",
        "九",
        "十",
        "百",
        "千",
        "万",
    ]

    def __init__(self, magic_regex={}, magic_variable={}):
        self.magic_regex.update(magic_regex)
        self.magic_variable.update(magic_variable)
        self.dir_filename_dict = {}

    def set_taskname(self, taskname):
        """设置任务名称"""
        self.magic_variable["{TASKNAME}"] = taskname

    def magic_regex_conv(self, pattern, replace):
        """魔法正则匹配"""
        keyword = pattern
        if keyword in self.magic_regex:
            pattern = self.magic_regex[keyword]["pattern"]
            if replace == "":
                replace = self.magic_regex[keyword]["replace"]
        return pattern, replace

    def sub(self, pattern, replace, file_name):
        """魔法正则、变量替换"""
        if not replace:
            return file_name
        # 预处理替换变量
        for key, p_list in self.magic_variable.items():
            if key in replace:
                # 正则类替换变量
                if p_list and isinstance(p_list, list):
                    for p in p_list:
                        match = re.search(p, file_name)
                        if match:
                            # 匹配成功，替换为匹配到的值
                            value = match.group()
                            # 日期格式处理：补全、格式化
                            if key == "{DATE}":
                                value = "".join(
                                    [char for char in value if char.isdigit()]
                                )
                                value = (
                                    str(datetime.now().year)[: (8 - len(value))] + value
                                )
                            replace = replace.replace(key, value)
                            break
                # 非正则类替换变量
                if key == "{TASKNAME}":
                    replace = replace.replace(key, self.magic_variable["{TASKNAME}"])
                elif key == "{SXX}" and not match:
                    replace = replace.replace(key, "S01")
                elif key == "{I}":
                    continue
                else:
                    # 清理未匹配的 magic_variable key
                    replace = replace.replace(key, "")
        if pattern and replace:
            file_name = re.sub(pattern, replace, file_name)
        else:
            file_name = replace
        return file_name

    def _custom_sort_key(self, name):
        """自定义排序键"""
        for i, keyword in enumerate(self.priority_list):
            if keyword in name:
                name = name.replace(keyword, f"_{i:02d}_")  # 替换为数字，方便排序
        return name

    def sort_file_list(self, file_list, dir_filename_dict={}):
        """文件列表统一排序，给{I+}赋值"""
        filename_list = [
            # 强制加入`文件修改时间`字段供排序，效果：1无可排序字符时则按修改时间排序，2和目录已有文件重名时始终在其后
            f"{f['file_name_re']}_{f['updated_at']}"
            for f in file_list
            if f.get("file_name_re") and not f["dir"]
        ]
        # print(f"filename_list_before: {filename_list}")
        dir_filename_dict = dir_filename_dict or self.dir_filename_dict
        # print(f"dir_filename_list: {dir_filename_list}")
        # 合并目录文件列表
        filename_list = list(set(filename_list) | set(dir_filename_dict.values()))
        filename_list = natsorted(filename_list, key=self._custom_sort_key)
        filename_index = {}
        for name in filename_list:
            if name in dir_filename_dict.values():
                continue
            i = filename_list.index(name) + 1
            while i in dir_filename_dict.keys():
                i += 1
            dir_filename_dict[i] = name
            filename_index[name] = i
        for file in file_list:
            if file.get("file_name_re"):
                if match := re.search(r"\{I+\}", file["file_name_re"]):
                    i = filename_index.get(
                        f"{file['file_name_re']}_{file['updated_at']}", 0
                    )
                    file["file_name_re"] = re.sub(
                        match.group(),
                        str(i).zfill(match.group().count("I")),
                        file["file_name_re"],
                    )

    def set_dir_file_list(self, file_list, replace):
        """设置目录文件列表"""
        self.dir_filename_dict = {}
        filename_list = [f["file_name"] for f in file_list if not f["dir"]]
        filename_list.sort()
        if not filename_list:
            return
        if match := re.search(r"\{I+\}", replace):
            # 由替换式转换匹配式
            magic_i = match.group()
            pattern_i = r"\d" * magic_i.count("I")
            pattern = replace.replace(match.group(), "🔢")
            for key, _ in self.magic_variable.items():
                if key in pattern:
                    pattern = pattern.replace(key, "🔣")
            pattern = re.sub(r"\\[0-9]+", "🔣", pattern)  # \1 \2 \3
            pattern = f"({re.escape(pattern).replace('🔣', '.*?').replace('🔢', f')({pattern_i})(')})"
            # print(f"pattern: {pattern}")
            # 获取起始编号
            if match := re.match(pattern, filename_list[-1]):
                self.magic_variable["{I}"] = int(match.group(2))
            # 目录文件列表
            for filename in filename_list:
                if match := re.match(pattern, filename):
                    self.dir_filename_dict[int(match.group(2))] = (
                        match.group(1) + magic_i + match.group(3)
                    )
            # print(f"filename_list: {self.filename_list}")

    def is_exists(self, filename, filename_list, ignore_ext=False):
        """判断文件是否存在，处理忽略扩展名"""
        # print(f"filename: {filename} filename_list: {filename_list}")
        if ignore_ext:
            filename = os.path.splitext(filename)[0]
            filename_list = [os.path.splitext(f)[0] for f in filename_list]
        # {I+} 模式，用I通配数字序号
        if match := re.search(r"\{I+\}", filename):
            magic_i = match.group()
            pattern_i = r"\d" * magic_i.count("I")
            pattern = re.escape(filename).replace(re.escape(magic_i), pattern_i)
            for filename in filename_list:
                if re.match(pattern, filename):
                    return filename
            return None
        else:
            return filename if filename in filename_list else None



class Baidu:
    """百度网盘驱动：全部走 bdpan CLI（--json），无需自备 AppKey。"""

    def __init__(self, index=0):
        self.index = index + 1
        self.is_active = False
        self.nickname = ""
        self.vip_type = None
        self.savepath_cache = {}

    # ---------- 底层 ----------
    @staticmethod
    def _bdpan_bin():
        import shutil
        hit = shutil.which("bdpan")
        if hit:
            return hit
        for cand in ("/usr/local/bin/bdpan",
                     os.path.expanduser("~/.local/bin/bdpan"),
                     "/root/.local/bin/bdpan"):
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
        return "bdpan"

    def _run(self, args, timeout=120):
        cmd = [self._bdpan_bin()] + args
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            return {"_ok": False, "_err": "未找到 bdpan，请先安装并登录"}
        except subprocess.TimeoutExpired:
            return {"_ok": False, "_err": "bdpan 调用超时"}
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        if not out:
            return {"_ok": False, "_err": err or f"bdpan 无输出，退出码={p.returncode}", "_code": p.returncode}
        try:
            data = json.loads(out)
            if not isinstance(data, dict):
                data = {"_list": data}
            data["_ok"] = True
            data["_code"] = p.returncode
            if data.get("errno") is None and data.get("code") not in (None, 0):
                data["errno"] = data.get("code")
            return data
        except Exception:
            return {"_ok": p.returncode == 0, "_raw": out, "_err": err, "_code": p.returncode}

    def run_raw(self, args, timeout=120):
        return self._run(args, timeout=timeout)

    @staticmethod
    def err_text(raw, default="操作失败"):
        if not isinstance(raw, dict):
            return default
        for key in ("message", "error", "msg"):
            v = raw.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()[:300]
        d = raw.get("data")
        if isinstance(d, dict):
            for key in ("message", "error", "msg"):
                v = d.get(key)
                if isinstance(v, str) and v.strip():
                    return v.strip()[:300]
        return default

    def verify(self):
        ret = self._run(["whoami", "--json"])
        if not ret.get("_ok"):
            print(f"❌ bdpan whoami 失败：{ret.get('_err')}")
            return None
        if ret.get("authenticated") is False or ret.get("has_valid_token") is False:
            print("❌ 百度网盘未登录，请先在 WebUI 点「登录百度网盘」完成授权")
            return None
        txt = json.dumps(ret, ensure_ascii=False)
        if "未登录" in txt or ret.get("errno") not in (None, 0):
            print("❌ 百度网盘未登录，请先在 WebUI 点「登录百度网盘」完成授权")
            return None
        self.is_active = True
        # whoami --json 含用户名与 vip 字段（不同版本字段名有差异，尽量兼容）
        for k in ("username", "user_name", "nickname", "name"):
            if ret.get(k):
                self.nickname = str(ret[k])
                break
        if not self.nickname:
            data = ret.get("data") or {}
            for k in ("username", "user_name", "nickname", "name"):
                if data.get(k):
                    self.nickname = str(data[k])
                    break
        self.vip_type = ret.get("vip_type", (ret.get("data") or {}).get("vip_type") if isinstance(ret.get("data"), dict) else None)
        print(f"👤 百度账号: {self.nickname or '已登录'} (vip_type={self.vip_type})✅")
        return ret

    # ---------- 路径 ----------
    @staticmethod
    def norm_savepath(savepath):
        # 用户写 /追剧/xxx 或 追剧/xxx，一律视为 /apps/bdpan/ 下相对路径
        s = (savepath or "").strip().replace("\\", "/")
        s = re.sub(r"/{2,}", "/", s).strip("/")
        return s

    @staticmethod
    def show_path(rel):
        rel = (rel or "").strip("/")
        return f"我的应用数据/bdpan/{rel}" if rel else "我的应用数据/bdpan/"

    def _norm_ls_item(self, it):
        if not isinstance(it, dict):
            return None
        name = it.get("file_name") or it.get("name") or it.get("server_filename") or ""
        if not name:
            # 有些版本用 path 尾段
            p = it.get("path") or ""
            name = p.rstrip("/").split("/")[-1] if p else ""
        if not name:
            return None
        is_dir = it.get("dir")
        if is_dir is None:
            is_dir = it.get("isdir")
        if is_dir is None:
            is_dir = it.get("is_dir")
        if is_dir is None:
            t = str(it.get("type", ""))
            is_dir = (t == "dir")
        fsid = it.get("fsid", it.get("fs_id", it.get("fsId", "")))
        try:
            fsid = str(fsid) if fsid not in (None, "") else ""
        except Exception:
            fsid = ""
        size = it.get("size", it.get("length", 0))
        updated_at = it.get("updated_at", it.get("mtime", it.get("server_mtime", it.get("mtimeTs", it.get("time", it.get("modified", it.get("ctime", 0)))))))
        try:
            updated_at = int(updated_at)
        except Exception:
            updated_at = 0
        lname = (name or "").lower()
        if is_dir:
            obj_category = ""
        elif lname.endswith((".mp4", ".mkv", ".mov", ".m4v", ".avi", ".mpeg", ".ts", ".rmvb", ".flv", ".wmv")):
            obj_category = "video"
        elif lname.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".tiff")):
            obj_category = "image"
        elif lname.endswith((".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a")):
            obj_category = "audio"
        elif lname.endswith((".pdf", ".doc", ".docx", ".txt", ".md", ".epub", ".ppt", ".pptx", ".xls", ".xlsx")):
            obj_category = "doc"
        elif lname.endswith((".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso")):
            obj_category = "archive"
        else:
            obj_category = ""
        return {
            "file_name": name,
            "dir": bool(is_dir),
            "fsid": fsid,
            "size": size,
            "path": it.get("path", ""),
            "obj_category": obj_category,
            "updated_at": updated_at,
            "raw": it,
        }

    def ls_dir(self, savepath_rel):
        rel = self.norm_savepath(savepath_rel)
        arg = rel if rel else "."
        ret = self._run(["ls", arg, "--json"])
        if not ret.get("_ok"):
            return {"ok": False, "list": [], "raw": ret}
        if ret.get("errno") not in (None, 0):
            # bdpan 出错时也可能吐 JSON（如网络不可达）：errno 非零必须判失败，
            # 否则会被当成“空目录”，导致已存在文件被重复转存
            return {"ok": False, "list": [], "raw": ret, "err": Baidu.err_text(ret, "列目录失败")}
        items = []
        if isinstance(ret, list):
            items = ret
        else:
            # 注意：bdpan ls 输出裸数组时 _run 会包成 _list 键，必须先认它
            for k in ("_list", "data", "list", "items", "files"):
                v = ret.get(k)
                if isinstance(v, list):
                    items = v
                    break
        normed = [x for x in (self._norm_ls_item(i) for i in items) if x]
        return {"ok": True, "list": normed, "raw": ret}

    def mkdir(self, savepath_rel):
        rel = self.norm_savepath(savepath_rel)
        if not rel:
            return {"ok": True}
        ret = self._run(["mkdir", rel, "--json"])
        if ret.get("_ok"):
            return {"ok": True, "raw": ret}
        # 已存在也算成功
        txt = json.dumps(ret, ensure_ascii=False)
        if "已存在" in txt or "exist" in txt.lower():
            return {"ok": True, "raw": ret}
        print(f"❌ 建文件夹失败: {self.show_path(rel)} {txt[:200]}")
        return {"ok": False, "raw": ret}

    def ensure_dir(self, savepath_rel):
        rel = self.norm_savepath(savepath_rel)
        if not rel:
            return True
        if rel in self.savepath_cache:
            return True
        # 逐级建
        parts = rel.split("/")
        cur = ""
        for part in parts:
            cur = f"{cur}/{part}".strip("/")
            r = self.ls_dir(cur)
            if r["ok"] and (r["list"] or True):
                # ls 成功即认为存在（空目录也返回 ok 但 list 为空，需再判断是否是“目录不存在”错误）
                # bdpan 对不存在目录返回 errno 非零或 _ok False，这里 ok=True 说明存在
                self.savepath_cache[cur] = True
                continue
            m = self.mkdir(cur)
            if not m["ok"]:
                return False
            self.savepath_cache[cur] = True
        return True

    # ---------- 分享 ----------
    @staticmethod
    def extract_share(url, task_pwd=""):
        url = _conv_share_init((url or "").strip())
        subdir = ""
        mfrag = re.search(r"#list/path=(.*)$", url)
        if mfrag:
            try:
                import urllib.parse as _up
                subdir = _up.unquote(mfrag.group(1)).strip().strip("/")
                if subdir in ("", "/"):
                    subdir = ""
            except Exception:
                subdir = ""
        # bdpan 解析不了 # 碎片，先去掉
        url = url.split("#", 1)[0].strip()
        pwd = (task_pwd or "").strip()
        m = re.search(r"[?&]pwd=([A-Za-z0-9]{4})", url)
        if m and not pwd:
            pwd = m.group(1)
        # 兼容 “链接 提取码:abcd” 写在一起的情况
        m2 = re.search(r"(?:提取码|密码|pwd)[:：\s]*([A-Za-z0-9]{4})", url)
        if m2 and not pwd:
            pwd = m2.group(1)
        return url, pwd, subdir

    def _norm_share_item(self, it):
        n = self._norm_ls_item(it)
        if not n:
            return None
        # 分享列表字段名差异：fs_id 字符串、isdir 等
        if not n["fsid"]:
            for k in ("fs_id", "fid"):
                if it.get(k) not in (None, ""):
                    n["fsid"] = str(it.get(k))
                    break
        n["file_name"] = it.get("file_name") or it.get("name") or it.get("server_filename") or n["file_name"]
        return n

    def list_share(self, share_url, pwd="", source_dir="", page=1, page_size=100):
        args = ["transfer", "list", share_url, "--page", str(page), "--page-size", str(page_size), "--json"]
        if source_dir:
            # bdpan 要求 source-dir 为空或以 / 开头，否则取子目录失败；调用处传的是相对路径，这里统一规范化
            sd = "/" + source_dir.strip().strip("/")
            args += ["--source-dir", sd if sd != "/" else ""]
        if pwd:
            args += ["-p", pwd]
        ret = self._run(args, timeout=120)
        if not ret.get("_ok"):
            return {"ok": False, "list": [], "has_more": False, "raw": ret}
        txt = json.dumps(ret, ensure_ascii=False)
        # 错误码直判
        if ret.get("errno") not in (None, 0):
            return {"ok": False, "list": [], "has_more": False, "raw": ret, "err": txt[:300]}
        items = []
        for k in ("_list", "data", "list", "items", "files"):
            v = ret.get(k)
            if isinstance(v, list):
                items = v
                break
            if isinstance(v, dict) and isinstance(v.get("list"), list):
                items = v["list"]
                break
        # data 可能是 {list:[...], has_more:...}
        has_more = bool(ret.get("has_more", (ret.get("data") or {}).get("has_more") if isinstance(ret.get("data"), dict) else False))
        normed = [x for x in (self._norm_share_item(i) for i in items) if x]
        return {"ok": True, "list": normed, "has_more": has_more, "raw": ret}

    def list_share_all(self, share_url, pwd="", source_dir=""):
        all_items = []
        first_raw = None
        page = 1
        while True:
            r = self.list_share(share_url, pwd, source_dir, page, 100)
            if not r["ok"]:
                return r
            if first_raw is None:
                first_raw = r.get("raw")
            all_items.extend(r["list"])
            if not r["has_more"] or not r["list"]:
                break
            page += 1
            if page > 50:
                break
        return {"ok": True, "list": all_items, "has_more": False, "raw": first_raw}

    def transfer_select(self, share_url, fsids, pwd="", target_dir=""):
        if not fsids:
            return {"ok": False, "err": "fsid 为空"}
        fsid_str = ",".join(fsids) if isinstance(fsids, list) else str(fsids)
        args = ["transfer", "select", share_url, "--fsid", fsid_str, "--json"]
        if pwd:
            args += ["-p", pwd]
        rel = self.norm_savepath(target_dir)
        if rel:
            args += ["-d", rel]
        ret = self._run(args, timeout=300)
        if not ret.get("_ok"):
            return {"ok": False, "err": ret.get("_err", "转存调用失败"), "raw": ret}
        errno = ret.get("errno", 0)
        if errno not in (None, 0):
            msg = json.dumps(ret, ensure_ascii=False)[:500]
            return {"ok": False, "err": msg, "errno": errno, "raw": ret}
        status = str(ret.get("status", "") or (ret.get("data") or {}).get("status") if isinstance(ret.get("data"), dict) else "")
        return {"ok": True, "status": status or "submitted", "raw": ret}


    # ---------- 转存后操作 ----------
    def rename_file(self, savepath_rel, old_name, new_name):
        rel = self.norm_savepath(savepath_rel).strip("/")
        src = f"{rel}/{old_name}" if rel else old_name
        ret = self._run(["rename", src, new_name, "--json"])
        if ret.get("_ok") and ret.get("errno", 0) in (None, 0):
            return True, ret
        return False, ret

    def delete_paths(self, rel_paths):
        """删除网盘文件/目录（bdpan rm，目录连带内容）"""
        paths = [p for p in (rel_paths or []) if p and str(p).strip("/")]
        if not paths:
            return {"code": 1, "message": "删除路径为空"}
        ret = self._run(["rm"] + paths + ["--force", "--json"], timeout=180)
        if not ret.get("_ok"):
            return {"code": 1, "message": str(ret.get("_err", "删除调用失败"))[:300]}
        if ret.get("errno") not in (None, 0):
            return {"code": 1, "message": json.dumps(ret, ensure_ascii=False)[:300]}
        return {"code": 0, "message": f"已删除 {len(paths)} 项"}

    def get_auth_url(self):
        """获取百度授权链接（供 WebUI 一键登录）"""
        ret = self._run(["login", "--get-auth-url", "--accept-disclaimer", "--json"], timeout=60)
        if not ret.get("_ok"):
            return {"ok": False, "err": str(ret.get("_err", "获取授权链接失败"))[:200]}
        url = (ret.get("data") or {}).get("auth_url", "")
        if not url:
            return {"ok": False, "err": "未获取到授权链接"}
        return {"ok": True, "auth_url": url}

    def login_with_code(self, code):
        """用授权码完成登录（授权码绝不记入日志）"""
        code = (code or "").strip()
        if not code:
            return {"ok": False, "err": "授权码为空"}
        try:
            pr = subprocess.run(
                [self._bdpan_bin(), "login", "--set-code-stdin", "--accept-disclaimer", "--json"],
                input=(code + "\n"), capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            return {"ok": False, "err": "未找到 bdpan"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "err": "登录超时，请重试"}
        out = (pr.stdout or "").strip()
        try:
            data = json.loads(out) if out else {}
        except Exception:
            data = {}
        ok = (pr.returncode == 0 and data.get("code", 0) == 0
              and data.get("errno", 0) in (None, 0))
        if ok:
            self.is_active = False
            self.nickname = ""
            self.verify()
            return {"ok": True, "nickname": self.nickname}
        msg = data.get("message") or data.get("error") or (pr.stderr or "").strip() or "登录失败"
        return {"ok": False, "err": str(msg)[:300]}

    def logout(self):
        self._run(["logout"], timeout=30)
        self.is_active = False
        self.nickname = ""
        return {"ok": True}

    def _get_file_icon(self, f):
        if f.get("dir"):
            return "📁"
        name = (f.get("file_name_re") or f.get("file_name") or "").lower()
        if name.endswith((".mp4", ".mkv", ".mov", ".m4v", ".avi", ".ts", ".rmvb")):
            return "🎞️"
        if name.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")):
            return "🖼️"
        if name.endswith((".mp3", ".flac", ".wav", ".aac")):
            return "🎵"
        if name.endswith((".pdf", ".doc", ".docx", ".txt", ".md", ".epub")):
            return "📄"
        if name.endswith((".zip", ".rar", ".7z", ".tar", ".gz")):
            return "📦"
        return ""

    def do_save_check(self, shareurl, savepath, pwd=""):
        try:
            url, pp, _sub = self.extract_share(shareurl, pwd)
            r = self.list_share(url, pp, "", 1, 5)
            if not r["ok"]:
                print(f"❌ 转存测试失败: {r.get('err', r['raw'])}")
                return False
            print(f"获取分享: {len(r['list'])} 项")
            if not r["list"]:
                print("❌ 转存测试失败: 分享为空")
                return False
            first = r["list"][0]
            tr = self.transfer_select(url, [first["fsid"]], pp, savepath)
            print(f"转存测试: {json.dumps(tr, ensure_ascii=False)[:300]}")
            if tr["ok"]:
                print("✅ 转存测试成功（异步任务已提交，请到网盘确认）")
                return True
            print(f"❌ 转存测试失败: {tr.get('err')}")
            return False
        except Exception as e:
            print(f"❌ 转存测试失败: {e}")
            traceback.print_exc()
            return False

    def do_save_task(self, task):
        if task.get("shareurl_ban"):
            print(f"《{task['taskname']}》：{task['shareurl_ban']}")
            return None
        url, pwd, url_subdir = self.extract_share(task.get("shareurl", ""), task.get("pwd", ""))
        if not url:
            return None
        probe = self.list_share(url, pwd, "", 1, 5)
        if not probe["ok"]:
            raw = json.dumps(probe.get("raw", {}), ensure_ascii=False)
            detail = self.err_text(probe.get("raw"))
            if "13004" in raw or "13000" in raw or "record not found" in raw or "失效" in raw or "不存在" in raw:
                msg = "分享链接无效、已失效，或提取码错误（请检查链接和提取码）"
            elif "13003" in raw or "提取码" in raw:
                msg = "需要提取码或提取码错误（请在任务里填 pwd）"
            elif "网络" in detail or "超时" in detail or "timeout" in detail.lower():
                print(f"跳过任务：网络异常 {detail}")
                return None
            else:
                msg = probe.get("err") or detail or raw[:200]
            # 网络类错误不标记 ban，只跳过本次
            if "网络" in msg or "超时" in msg or "timeout" in msg.lower():
                print(f"跳过任务：网络异常 {msg}")
                return None
            add_notify(f"❌《{task['taskname']}》：{msg}\n")
            task["shareurl_ban"] = msg
            return None
        updated_tree = self.dir_check_and_save(task, url, pwd, url_subdir, "")
        try:
            size = updated_tree.size(1)
        except Exception:
            size = len(updated_tree.nodes) - 1 if hasattr(updated_tree, "nodes") else 0
        if size > 0:
            self.do_rename(updated_tree)
            print()
            add_notify(f"✅《{task['taskname']}》添加追更：\n{updated_tree}")
            return updated_tree
        print("任务结束：没有新的转存任务")
        return None

    def dir_check_and_save(self, task, share_url, pwd, source_dir="", subdir_path=""):
        from treelib import Tree
        tree = Tree()
        # 1. 分享侧列表
        rs = self.list_share_all(share_url, pwd, source_dir)
        if not rs["ok"]:
            raw = json.dumps(rs.get("raw", {}), ensure_ascii=False)
            if subdir_path == "":
                if "13004" in raw or "13000" in raw or "record not found" in raw:
                    task["shareurl_ban"] = "分享链接无效、已失效，或提取码错误（请检查链接和提取码）"
                    add_notify(f"❌《{task['taskname']}》：{task['shareurl_ban']}\n")
            return tree
        share_file_list = rs["list"]
        if not share_file_list:
            if subdir_path == "":
                task["shareurl_ban"] = "分享为空，文件已被分享者删除"
                add_notify(f"❌《{task['taskname']}》：{task['shareurl_ban']}\n")
            return tree
        if len(share_file_list) == 1 and share_file_list[0]["dir"] and subdir_path == "" and not source_dir:
            print("🧠 该分享是一个文件夹，读取文件夹内列表")
            only = share_file_list[0]
            # 用该目录的 path 继续查（bdpan 用 path 做 source-dir）
            sub_path = (only.get("path") or only["file_name"]).strip("/")
            if not sub_path:
                sub_path = only["file_name"]
            rs2 = self.list_share_all(share_url, pwd, sub_path)
            if rs2["ok"] and rs2["list"]:
                share_file_list = rs2["list"]
            else:
                # 子目录列表失败时直接跳过（下次重试），不要回退成整目录转存，否则会在 savepath 下套出一层同名目录
                print(f"⚠️ 子目录 {sub_path} 列表失败，本次跳过，下次重试")
                return tree
        # 2. 目标目录
        savepath_rel = re.sub(r"/{2,}", "/", f"/{task['savepath']}{subdir_path}").strip("/")
        if not self.ensure_dir(savepath_rel):
            print(f"❌ 目录 {self.show_path(savepath_rel)} 创建/检查失败，跳过")
            return tree
        rd = self.ls_dir(savepath_rel)
        if not rd["ok"]:
            # 目标目录列不出来时不能当成空目录，否则已存在的文件会被重复转存；跳过等下次
            print(f"⚠️ 目标目录 {self.show_path(savepath_rel)} 列表失败，本次跳过，下次重试")
            return tree
        dir_file_list = rd["list"]
        dir_filename_list = [d["file_name"] for d in dir_file_list]
        root_id = f"root:{savepath_rel}"
        tree.create_node(self.show_path(savepath_rel), root_id, data={"is_dir": True})
        # 3. 正则
        import sys as _sys
        _parent = _sys.modules.get("__main__")
        MagicRenameCls = getattr(_parent, "MagicRename", None) or globals().get("MagicRename")
        mr = MagicRenameCls(CONFIG_DATA.get("magic_regex", {}))
        mr.set_taskname(task.get("taskname", ""))
        pattern, replace = mr.magic_regex_conv(task.get("pattern", ""), task.get("replace", ""))
        need_save_list = []
        for share_file in share_file_list:
            search_pattern = task["update_subdir"] if share_file["dir"] and task.get("update_subdir") else pattern
            try:
                matched = bool(re.search(search_pattern, share_file["file_name"])) if search_pattern else True
            except re.error as e:
                print(f"⚠️ 正则错误 {search_pattern}: {e}")
                matched = False
            if not matched:
                # 目录递归：即使目录名不命中 update_subdir，也允许默认递归找更新（与夸克版行为对齐则只在命中时递归）
                if share_file["dir"] and not task.get("update_subdir"):
                    pass
                else:
                    if share_file.get("fsid") == task.get("startfid", ""):
                        break
                    continue
            exists = mr.is_exists(share_file["file_name"], dir_filename_list, bool(task.get("ignore_extension")) and not share_file["dir"])
            if not exists:
                if share_file["dir"] or subdir_path:
                    share_file["file_name_re"] = share_file["file_name"]
                    need_save_list.append(share_file)
                else:
                    file_name_re = mr.sub(pattern, replace, share_file["file_name"])
                    if not mr.is_exists(file_name_re, dir_filename_list, bool(task.get("ignore_extension"))):
                        share_file["file_name_re"] = file_name_re
                        need_save_list.append(share_file)
            elif share_file["dir"]:
                # 已存在的目录：删掉重转 or 递归
                if task.get("update_subdir") and re.search(task.get("update_subdir", ""), share_file["file_name"]):
                    if task.get("update_subdir_resave_mode"):
                        print(f"🗑️ 重存模式：清空后重转 {savepath_rel}/{share_file['file_name']}")
                        sub_rel = f"{savepath_rel}/{share_file['file_name']}".strip("/")
                        sub_ls = self.ls_dir(sub_rel)
                        if sub_ls["ok"] and sub_ls["list"]:
                            self.delete_paths([f"{sub_rel}/{x['file_name']}" for x in sub_ls["list"]])
                        share_file["file_name_re"] = share_file["file_name"]
                        need_save_list.append(share_file)
                    else:
                        print(f"🔁 已存在目录需更新：{share_file['file_name']}")
                        share_file["file_name_re"] = share_file["file_name"]
                        need_save_list.append(share_file)
                else:
                    sub_source = (share_file.get("path") or share_file["file_name"]).strip("/")
                    print(f"检查子目录：{savepath_rel}/{share_file['file_name']}")
                    sub_tree = self.dir_check_and_save(task, share_url, pwd, sub_source, f"/{subdir_path}/{share_file['file_name']}" if subdir_path else f"/{share_file['file_name']}")
                    try:
                        sub_size = sub_tree.size(1)
                    except Exception:
                        sub_size = 0
                    if sub_size > 0:
                        tree.create_node("📁" + share_file["file_name"], f"dir:{sub_source}", parent=root_id, data={"is_dir": True})
                        try:
                            tree.merge(f"dir:{sub_source}", sub_tree, deep=False)
                        except Exception:
                            pass
            if share_file.get("fsid") == task.get("startfid", ""):
                break
        if re.search(r"\{I+\}", replace or ""):
            try:
                mr.set_dir_file_list([{"file_name": d["file_name"], "dir": d["dir"]} for d in dir_file_list], replace)
                mr.sort_file_list(need_save_list)
            except Exception as e:
                print(f"⚠️ 序号整理失败: {e}")
        # 4. 转存（目录先转文件：bdpan 按 fsid 转，目录会整目录转存）
        files_only = [x for x in need_save_list if not x["dir"]]
        dirs_only = [x for x in need_save_list if x["dir"]]
        to_transfer = files_only + dirs_only
        if not to_transfer:
            return tree
        # 目录不同于夸克需要 fid_token：直接按 fsid 转
        fsids = [x["fsid"] for x in to_transfer if x.get("fsid")]
        if not fsids:
            print("⚠️ 没有可用 fsid，跳过转存")
            return tree
        err_msg = None
        ok_count = 0
        BATCH = 50
        for i in range(0, len(fsids), BATCH):
            batch = fsids[i:i+BATCH]
            tr = self.transfer_select(share_url, batch, pwd, savepath_rel)
            if tr.get("ok"):
                ok_count += len(batch)
                print(f"转存已提交 {len(batch)} 项 → {self.show_path(savepath_rel)}")
                time.sleep(5)
            else:
                err_msg = tr.get("err", "转存失败")
                print(f"⚠️ 批量转存失败: {err_msg}")
                if "13071" in str(err_msg):
                    print("⏳ 有其他转存任务进行中，等待 5 分钟后继续…")
                    time.sleep(300)
                    tr2 = self.transfer_select(share_url, batch, pwd, savepath_rel)
                    if tr2.get("ok"):
                        ok_count += len(batch)
                        err_msg = None
                        time.sleep(5)
                elif "13072" in str(err_msg) or "13073" in str(err_msg):
                    print("⚠️ 达到单次转存数量上限，改逐个转存…")
                    for single in batch:
                        trs = self.transfer_select(share_url, [single], pwd, savepath_rel)
                        if trs.get("ok"):
                            ok_count += 1
                            time.sleep(3)
                        else:
                            err_msg = trs.get("err", err_msg)
                            break
        if err_msg and ok_count == 0:
            add_notify(f"❌《{task['taskname']}》转存失败：{err_msg}\n")
            return tree
        # 5. 建树（转存是异步的，这里以提交为准；重命名在 do_rename 里按目标目录实际文件做）
        for item in to_transfer:
            icon = self._get_file_icon(item)
            tree.create_node(f"{icon}{item.get('file_name_re', item['file_name'])}", f"fid:{item.get('fsid')}", parent=root_id, data={"file_name": item["file_name"], "file_name_re": item.get("file_name_re", item["file_name"]), "fsid": item.get("fsid"), "path": f"{savepath_rel}/{item.get('file_name_re', item['file_name'])}", "is_dir": item["dir"]})
        return tree

    def do_rename(self, tree, node_id=None):
        try:
            from treelib import Tree as _T
        except Exception:
            return
        if node_id is None:
            try:
                node_id = tree.root
            except Exception:
                return
        try:
            children = tree.children(node_id)
        except Exception:
            return
        for child in children:
            file = child.data or {}
            if file.get("is_dir"):
                continue
            if file.get("file_name_re") and file["file_name_re"] != file.get("file_name"):
                save_dir = (file.get("path") or "").rsplit("/", 1)[0] if "/" in (file.get("path") or "") else ""
                ok, ret = self.rename_file(save_dir, file["file_name"], file["file_name_re"])
                print(f"重命名：{file['file_name']} → {file['file_name_re']}")
                if not ok:
                    print(f"      ↑ 失败，{json.dumps(ret, ensure_ascii=False)[:200]}")


def verify_account(account):
    print(f"▶️ 验证账号")
    info = account.verify()
    if not info:
        add_notify("👤 百度账号登录失败，请先登录❌")
        return False
    return True


def format_bytes(size_bytes):
    try:
        size_bytes = float(size_bytes)
    except Exception:
        return str(size_bytes)
    units = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = 0
    while size_bytes >= 1024 and i < len(units) - 1:
        size_bytes /= 1024
        i += 1
    return f"{size_bytes:.2f} {units[i]}"


def do_save(account, tasklist=None):
    print("🧩 载入插件")
    plugins, plugins_cfg, task_plugins_config = Config.load_plugins(CONFIG_DATA.get("plugins", {}))
    CONFIG_DATA["plugins"] = plugins_cfg
    print()
    print(f"转存账号: {account.nickname or '百度网盘'}")
    print(f"保存根: {Baidu.show_path('')}（bdpan 应用目录）")
    print()

    def is_time(task):
        try:
            if task.get("enddate"):
                if datetime.now().date() > datetime.strptime(task["enddate"], "%Y-%m-%d").date():
                    return False
        except Exception:
            pass
        if "runweek" in task:
            try:
                if (datetime.today().weekday() + 1) not in task.get("runweek"):
                    return False
            except Exception:
                pass
        return True

    for plugin_name, plugin in plugins.items():
        if getattr(plugin, "is_active", False) and hasattr(plugin, "task_before"):
            try:
                tasklist = plugin.task_before(tasklist=tasklist, account=account) or tasklist
            except Exception as e:
                print(f"插件 {plugin_name} task_before 失败: {e}")

    for index, task in enumerate(tasklist or []):
        print()
        print(f"#{index+1}------------------")
        print(f"《{task.get('taskname')}》")
        if not is_time(task):
            print("⏭️ 未到执行时间/已到期，跳过")
            continue
        try:
            is_new_tree = account.do_save_task(task)
        except Exception as e:
            print(f"❌ 任务异常: {e}")
            traceback.print_exc()
            continue
        if is_new_tree:
            print("🧩 调用插件")
            for plugin_name, plugin in plugins.items():
                if getattr(plugin, "is_active", False) and hasattr(plugin, "run"):
                    try:
                        task = plugin.run(task, account=account, tree=is_new_tree) or task
                    except Exception as e:
                        print(f"插件 {plugin_name} run 失败: {e}")
    print()
    print("===============插件收尾===============")
    for plugin_name, plugin in plugins.items():
        if getattr(plugin, "is_active", False) and hasattr(plugin, "task_after"):
            try:
                data = plugin.task_after(tasklist=tasklist, account=account) or {}
                if data.get("tasklist"):
                    CONFIG_DATA["tasklist"] = data["tasklist"]
                if data.get("config"):
                    CONFIG_DATA["plugins"][plugin_name] = data["config"]
            except Exception as e:
                print(f"插件 {plugin_name} task_after 失败: {e}")
    print()


def main():
    global CONFIG_DATA
    start_time = datetime.now()
    print("===============程序开始===============")
    print(f"⏰ 执行时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    config_path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CONFIG_PATH", "baidu_config.json")
    if tasklist_json := os.environ.get("TASKLIST"):
        try:
            tasklist_from_env = json.loads(tasklist_json)
        except Exception as e:
            print(f"从环境变量解析任务列表失败 {e}")
            tasklist_from_env = []
    else:
        tasklist_from_env = []
    if not os.path.exists(config_path):
        print(f"⚙️ 配置文件不存在，新建示例: {config_path}")
        sample = {"push_config": {}, "plugins": {}, "magic_regex": {}, "tasklist": []}
        os.makedirs(os.path.dirname(os.path.abspath(config_path)) or ".", exist_ok=True)
        Config.write_json(config_path, sample)
        CONFIG_DATA = sample
    else:
        CONFIG_DATA = Config.read_json(config_path)
    if tasklist_from_env:
        CONFIG_DATA["tasklist"] = tasklist_from_env
    if os.environ.get("BAIDU_TEST", "").lower() == "true":
        if push_cfg := os.environ.get("PUSH_CONFIG"):
            try:
                CONFIG_DATA["push_config"] = json.loads(push_cfg)
            except Exception as e:
                print(f"推送配置解析失败 {e}")
        account = Baidu()
        if not verify_account(account):
            return
        for task in (tasklist_from_env or CONFIG_DATA.get("tasklist", [])):
            print(f"#《{task.get('taskname')}》转存测试")
            account.do_save_check(task.get("shareurl", ""), task.get("savepath", ""), task.get("pwd", ""))
        if NOTIFYS:
            send_ql_notify("【百度自动转存】", "\n\n".join(NOTIFYS))
        return
    if not CONFIG_DATA.get("tasklist"):
        print("⚠️ 任务列表为空，请先配置 baidu_config.json")
        return
    account = Baidu()
    if not verify_account(account):
        return
    do_save(account, CONFIG_DATA.get("tasklist", []))
    # 回写 ban 标记等；单任务手动运行时任务列表来自环境变量，只含子集，
    # 此时只把 ban 标记合并回磁盘完整列表，绝不能整体覆盖
    if tasklist_from_env:
        try:
            disk = Config.read_json(config_path)
            by_url = {x.get("shareurl"): x for x in disk.get("tasklist", []) if isinstance(x, dict)}
            for x in CONFIG_DATA.get("tasklist", []):
                o = by_url.get(x.get("shareurl"))
                if o is not None and "shareurl_ban" in x:
                    o["shareurl_ban"] = x["shareurl_ban"]
            if isinstance(disk.get("tasklist"), list):
                CONFIG_DATA["tasklist"] = disk["tasklist"]
        except Exception as e:
            print(f"合并回写配置失败: {e}")
    try:
        Config.write_json(config_path, CONFIG_DATA)
    except Exception as e:
        print(f"回写配置失败: {e}")
    if NOTIFYS:
        send_ql_notify("【百度自动转存】", "\n\n".join(NOTIFYS))
    print(f"===============程序结束（耗时 {(datetime.now()-start_time).total_seconds():.1f}s）===============")


if __name__ == "__main__":
    main()
