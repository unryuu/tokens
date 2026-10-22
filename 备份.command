#!/bin/zsh
cd -- "${0:A:h}"
python3 scripts/backup.py
result=$?
if [[ $result -eq 0 ]]; then
  latest=$(cat backups/LATEST)
  open "reports/$latest/报告.md"
fi
printf '\n备份结束，退出状态：%s。按回车关闭窗口。' "$result"
read
exit "$result"
