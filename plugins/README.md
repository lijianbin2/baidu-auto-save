# plugins（百度版）

与夸克版插件接口保持兼容：`run(task, account, tree)` / `task_before(tasklist, account)` / `task_after(tasklist, account)`。

注意：百度版 `account` 是 `Baidu` 类（方法为 `ls_dir / mkdir / transfer_select / rename_file` 等），
直接从夸克版复制过来的插件如调用 `account.get_fids / save_file / get_detail` 需要先改写。
通用插件（仅用 task/tree 信息，如生成 .strm、推送 Emby/Plex 刷新）一般可直接复用。
