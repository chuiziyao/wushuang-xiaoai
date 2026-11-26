"""NAS SSH 命令执行器（部署辅助工具）。

凭据全部来自环境变量，不写入任何文件：
  NAS_HOST / NAS_PORT / NAS_USER / NAS_PASS
  NAS_SUDO=1 时以 sudo -S 执行（密码经 stdin 传入）
  NAS_CMD_TIMEOUT 秒（默认 600）

用法：python scripts/nas_run.py "shell command"
"""

from __future__ import annotations

import os
import shlex
import sys

import paramiko


def main() -> None:
    # Windows 控制台默认 GBK，NAS 输出含 Unicode 符号时会崩；统一 UTF-8 容错
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    host = os.environ["NAS_HOST"]
    port = int(os.environ.get("NAS_PORT", "22"))
    user = os.environ["NAS_USER"]
    password = os.environ["NAS_PASS"]
    use_sudo = os.environ.get("NAS_SUDO") == "1"
    timeout = int(os.environ.get("NAS_CMD_TIMEOUT", "600"))

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(
        host, port=port, username=user, password=password,
        timeout=25, allow_agent=False, look_for_keys=False,
        banner_timeout=30, auth_timeout=30,
    )

    # 脚本文件模式：本地脚本上传到 /tmp 后执行，彻底规避多层引号问题
    script = os.environ.get("NAS_SCRIPT")
    if script:
        with open(script, "rb") as f:
            content = f.read()
        remote = "/tmp/.wushuang_cmd.sh"
        stdin, stdout, stderr = cli.exec_command(f"cat > {remote}", timeout=60)
        stdin.write(content)
        stdin.channel.shutdown_write()
        stdout.channel.recv_exit_status()
        cmd = f"bash {remote}; rc=$?; rm -f {remote}; exit $rc"
    else:
        cmd = sys.argv[1]

    if use_sudo:
        cmd = f"sudo -S -p '' bash -c {shlex.quote(cmd)}"

    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    if use_sudo:
        stdin.write(password + "\n")
        stdin.flush()
    # 二进制读取 + 容错解码（NAS 输出可能混有 GBK 字节）
    import socket
    import time

    chan = stdout.channel
    chan.settimeout(1.0)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data = chan.recv(65536)
            if data:
                sys.stdout.write(data.decode("utf-8", errors="replace"))
                sys.stdout.flush()
            elif chan.exit_status_ready():
                break
        except socket.timeout:
            if chan.exit_status_ready():
                break
            continue
    while chan.recv_ready():
        sys.stdout.write(chan.recv(65536).decode("utf-8", errors="replace"))
    code = chan.recv_exit_status()
    errbuf = b""
    while chan.recv_stderr_ready():
        errbuf += chan.recv_stderr(65536)
    if errbuf.strip():
        sys.stderr.write(errbuf.decode("utf-8", errors="replace"))
    cli.close()
    sys.exit(code)


if __name__ == "__main__":
    main()
