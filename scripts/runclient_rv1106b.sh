#!/bin/sh
export LD_LIBRARY_PATH=/oem/usr/lib:$LD_LIBRARY_PATH  # 假设库文件在该路径
/oem/usr/bin/ai_client_start_stop --server 10.10.10.34 --port 8088 --recordtime 20