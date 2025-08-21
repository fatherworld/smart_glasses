#!/bin/sh
PROGRAM=/oem/usr/scripts/runclient_rv1106b.sh
case "$1" in
    start)
        echo "Starting WiFi auto connection..."
        
        # 等待系统初始化完成
        sleep 3
        
        # 加载WiFi模块
        if [ -f /oem/usr/ko/cfg80211.ko ]; then
            insmod /oem/usr/ko/cfg80211.ko
        fi
        
        if [ -f /oem/usr/ko/rtl8723ds.ko ]; then
            insmod /oem/usr/ko/rtl8723ds.ko
        fi
        
        # 启用wlan0接口
        ifconfig wlan0 up
        
        # 启动wpa_supplicant
        if [ -f /etc/wpa_supplicant.conf ]; then
            wpa_supplicant -B -i wlan0 -c /etc/wpa_supplicant.conf
        fi
        
        # 等待连接建立
        sleep 5
        
        # 获取IP地址
        udhcpc -i wlan0 -n -q -t 5
        
        # 检查连接状态
        if ifconfig wlan0 | grep -q "inet addr"; then
            echo "WiFi connected successfully"
            echo "IP: $(ifconfig wlan0 | grep 'inet addr' | awk '{print $2}' | cut -d: -f2)"
        else
            echo "WiFi connection failed"
        fi
        sleep 1
        $PROGRAM
        ;;
        
    stop)
        echo "Stopping WiFi..."
        killall wpa_supplicant 2>/dev/null
        ifconfig wlan0 down
        ;;
        
    restart|reload)
        $0 stop
        sleep 2
        $0 start
        ;;
        
    *)
        echo "Usage: $0 {start|stop|restart}"
        exit 1
esac

exit 0 