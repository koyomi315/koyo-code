"""危险命令黑名单单测（N1）：命中典型灾难命令、放行常见安全命令。"""

from koyocode.permission.blacklist import hits_blacklist


def test_hits_dangerous_commands() -> None:
    for cmd in [
        "rm -rf /",
        "rm -fr ~",
        "rm -rf $HOME",
        "rm -rf /*",
        "dd if=/dev/zero of=/dev/sda",
        ":(){ :|:& };:",
        ": ( ) { : | : & } ; :",
        "mkfs.ext4 /dev/sda1",
        "> /dev/sda",
        "chmod -R 777 /",
        "shutdown -h now",
    ]:
        assert hits_blacklist(cmd), cmd


def test_safe_commands_not_hit() -> None:
    for cmd in [
        "rm -rf ./build",
        "rm -rf build/",
        "git status",
        "ls -la",
        "pytest -q",
        "chmod +x ./script.sh",
        "dd if=img.iso of=/tmp/disk.img",
        "> /tmp/out.txt",
    ]:
        assert not hits_blacklist(cmd), cmd
