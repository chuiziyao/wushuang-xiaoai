"""SSH 流式上传工具（部署辅助）：不依赖 SFTP 子系统。

通过 exec_command('cat > 远端路径') 把本地文件字节流写入 stdin。
凭据来自环境变量 NAS_HOST/NAS_PORT/NAS_USER/NAS_PASS。
用法：python scripts/nas_upload.py <本地文件> <远端绝对路径>
"""

from __future__ import annotations

import os
import sys

import paramiko

CHUNK = 256 * 1024


def main() -> None:
    local, remote = sys.argv[1], sys.argv[2]
    size = os.path.getsize(local)
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(
        os.environ["NAS_HOST"], port=int(os.environ.get("NAS_PORT", "22")),
        username=os.environ["NAS_USER"], password=os.environ["NAS_PASS"],
        timeout=25, allow_agent=False, look_for_keys=False,
    )
    stdin, stdout, stderr = cli.exec_command(f"cat > '{remote}'", timeout=600)
    sent = 0
    next_report = 10
    with open(local, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            stdin.write(chunk)
            sent += len(chunk)
            pct = int(sent * 100 / size)
            if pct >= next_report:
                print(f"upload {pct}% ({sent}/{size})", flush=True)
                next_report = pct + 10
    stdin.channel.shutdown_write()
    code = stdout.channel.recv_exit_status()
    err = stderr.read().decode(errors="replace")
    cli.close()
    if code != 0:
        print(f"UPLOAD_FAILED exit={code} {err}", file=sys.stderr)
        sys.exit(code)
    print(f"UPLOADED {local} -> {remote} ({size} bytes)")


if __name__ == "__main__":
    main()
