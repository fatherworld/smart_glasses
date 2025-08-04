#!/bin/bash

# AI客户端recv断开调试测试脚本

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== AI客户端recv断开调试测试 ===${NC}"

# 检查文件是否存在
if [ ! -f "simple_client" ]; then
    echo -e "${RED}错误: simple_client 不存在，请先编译${NC}"
    echo "运行: arm-linux-gnueabihf-gcc -o simple_client simple_client.c -lpthread -lrt -Wall -O2"
    exit 1
fi

if [ ! -f "mock_server.py" ]; then
    echo -e "${RED}错误: mock_server.py 不存在${NC}"
    exit 1
fi

# 获取服务器地址（默认为本机）
SERVER_HOST=${1:-"127.0.0.1"}
SERVER_PORT=${2:-"8082"}

echo -e "${BLUE}服务器地址: ${SERVER_HOST}:${SERVER_PORT}${NC}"

# 函数：清理后台进程
cleanup() {
    echo -e "\n${YELLOW}清理后台进程...${NC}"
    pkill -f "mock_server.py" 2>/dev/null || true
    pkill -f "simple_client" 2>/dev/null || true
}

# 设置信号处理
trap cleanup EXIT INT TERM

# 启动模拟服务器
echo -e "${YELLOW}启动模拟服务器...${NC}"
python3 mock_server.py --host $SERVER_HOST --port $SERVER_PORT &
SERVER_PID=$!

# 等待服务器启动
sleep 2

# 检查服务器是否启动成功
if ! kill -0 $SERVER_PID 2>/dev/null; then
    echo -e "${RED}模拟服务器启动失败${NC}"
    exit 1
fi

echo -e "${GREEN}模拟服务器启动成功 (PID: $SERVER_PID)${NC}"

# 启动简化客户端
echo -e "${YELLOW}启动简化客户端...${NC}"
echo -e "${BLUE}客户端将连接到 ${SERVER_HOST}:${SERVER_PORT}${NC}"
echo -e "${BLUE}在另一个终端中，你可以在服务器控制台输入:${NC}"
echo -e "${BLUE}  start - 开始录音${NC}"
echo -e "${BLUE}  stop  - 结束录音${NC}"
echo -e "${BLUE}  quit  - 退出服务器${NC}"
echo

# 如果是本机测试，提供额外的说明
if [ "$SERVER_HOST" = "127.0.0.1" ] || [ "$SERVER_HOST" = "localhost" ]; then
    echo -e "${YELLOW}本地测试模式:${NC}"
    echo -e "${BLUE}由于客户端会阻塞等待输入，你需要打开另一个终端窗口${NC}"
    echo -e "${BLUE}在新终端中运行以下命令来控制服务器:${NC}"
    echo -e "${GREEN}  # 发送开始录音指令${NC}"
    echo -e "${GREEN}  echo 'start' | nc 127.0.0.1 8082${NC}"
    echo -e "${GREEN}  # 发送结束录音指令${NC}" 
    echo -e "${GREEN}  echo 'stop' | nc 127.0.0.1 8082${NC}"
    echo
fi

# 运行客户端
echo -e "${GREEN}启动客户端程序...${NC}"
./simple_client $SERVER_HOST $SERVER_PORT

echo -e "${GREEN}测试完成${NC}" 