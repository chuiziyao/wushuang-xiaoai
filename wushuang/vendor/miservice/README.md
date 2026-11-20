# vendor/miservice

内置的上游协议组件，**不要在此目录做业务开发**。

- 来源仓库：https://github.com/yihong0618/MiService （MIT）
- 内置文件：`miaccount.py`、`minaservice.py`、`miioservice.py`
- 唯一本地修改：`miaccount.py` 的随机 User-Agent 由 `fake_useragent`
  （运行时需联网更新 UA 数据集）改为内置静态 UA 列表，保证 NAS 离线可用。
- 升级方式：重新克隆上游，覆盖三个文件，重放 UA 补丁（见 git 历史或 NOTICE）。
