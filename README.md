# Baidu-Auto-Save（百度网盘自动转存）

个人自用版，逻辑移植自 [Cp0204/quark-auto-save](https://github.com/Cp0204/quark-auto-save)，
网盘层替换为百度网盘。**无需自备 AppKey**：复用 `bdpan` CLI 自带授权，Docker 内登录一次即可。

> ⚠️ 百度限制：只能操作「我的应用数据 / bdpan /」目录（即 `/apps/bdpan/`）。
> 配置里写 `/追剧/xxx`，实际就是 `我的应用数据/bdpan/追剧/xxx`。

## 功能

- 多任务自动转存百度分享链接（支持提取码、子目录递归）
- 正则过滤 + 重命名（含 `$TV_REGEX` 魔法匹配、`{TASKNAME}/{SXX}/{E}/{EXT}` 等魔法变量，直接复用夸克版规则）
- `enddate` 到期停跑、`runweek` 指定星期几跑
- 已转存跳过、`ignore_extension` 忽略后缀、`update_subdir` 指定子目录更新
- 定时执行（`CRON`）、WebUI 管理配置、一键执行、分享/目录预览
- 推送复用 `notify.py`（夸克版同款多渠道）

## 快速开始（docker compose）

```bash
cd baidu-auto-save
docker compose up -d --build
docker compose logs -f
```

打开 `http://你的IP:5006`，默认账号 `admin / admin123`（compose 里改）。

## 登录（必须，容器内执行一次）

```bash
docker exec -it baidu-auto-save bdpan login
```

按提示打开授权链接、把授权码粘回去即可。登录信息保存在 `./config/.bdpan/`，
随 compose 的 `./config:/app/config` 挂载持久化，重建容器不掉登录。

检查：`docker exec -it baidu-auto-save bdpan whoami`

## 配置

`config/baidu_config.json`（容器内路径 `/app/config/baidu_config.json`，WebUI 可直接改）：

```json
{
  "tasklist": [
    {
      "taskname": "示例-剧集追更",
      "shareurl": "https://pan.baidu.com/s/1abcXXXX?pwd=abcd",
      "pwd": "abcd",
      "savepath": "/追剧/示例剧",
      "pattern": "$TV_REGEX",
      "replace": "",
      "enddate": "2099-01-30",
      "update_subdir": "4k|1080p"
    }
  ]
}
```

字段说明（与夸克版基本一致）：

| 字段 | 说明 |
|---|---|
| `taskname` | 任务名（也会用于 `{TASKNAME}` 重命名） |
| `shareurl` | 百度分享链接，可带 `?pwd=`，也可把提取码单独填 `pwd` |
| `pwd` | 提取码（可选，与链接二选一即可） |
| `savepath` | 保存目录，写 `/追剧/xxx`，实际落到 bdpan 应用目录下 |
| `pattern/replace` | 正则过滤+重命名，`$TV_REGEX` 等魔法匹配通用 |
| `enddate` | 到期后跳过，`2099-01-30` 这种 |
| `runweek` | 如 `[2,4,6]` 只在周二四六跑 |
| `update_subdir` | 目录名命中该正则才递归更新子目录 |
| `ignore_extension` | `true` 则判断存在时忽略后缀 |

转存是异步提交（`status=submitted`），脚本提交后会等待几秒再核对目录；
百度限制：普通号单次 500 / VIP 3000 / SVIP 50000（按递归后实际文件数），
同时只能跑一个转存任务（`13071` 会自动等 5 分钟重试一次）。

## 定时

compose 里 `CRON: "0 8,18,20 * * *"`（分 时 日 月 周）。资源不会每时每刻更新，
**不要设太高频率**，注意账号风控。

## 插件

`plugins/` 与夸克版同接口（`run / task_before / task_after`），但 `account` 对象是百度版
（`ls_dir / mkdir / transfer_select / rename_file`），照抄夸克插件里调
`get_fids / save_file / get_detail` 的部分需要改写。

## 结构

```
baidu_auto_save.py   主脚本（定时/手动执行）
app/run.py           WebUI（5006 端口）
config/              配置+登录持久化（记得备份）
plugins/             可选插件
```

## 声明

个人学习使用，数据来自百度网盘官方接口；请勿用于非法用途，注意账号安全。
原项目 AGPL-3.0，本项目同样遵循 AGPL-3.0。
