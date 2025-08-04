#!/bin/sh
### BEGIN INIT INFO
# Provides:          my_script
# Required-Start:    $remote_fs $syslog
# Required-Stop:     $remote_fs $syslog
# Default-Start:     2 3 4 5
# Default-Stop:      0 1 6
# Short-Description: 我的开机启动脚本
# Description:       用于在系统启动时运行自定义程序
### END INIT INFO

# 脚本实际执行的命令（替换为你的程序路径）
PROGRAM=/oem/usr/scripts/runclient_rv1106b.sh
case "$1" in
  start)
    echo "启动 my_script..."
    # 后台运行程序（避免阻塞启动流程）
    #$PROGRAM &
    /oem/usr/scripts/runclient_rv1106b.sh
    ;;
  stop)
    echo "停止 my_script..."
    # 结束程序进程（根据实际情况修改）
    pkill -f "$PROGRAM"
    ;;
  restart)
    $0 stop
    $0 start
    ;;
  *)
    echo "使用方法: $0 {start|stop|restart}"
    exit 1
    ;;
esac
exit 0