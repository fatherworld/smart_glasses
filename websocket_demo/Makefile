让我给演示脚本添加执行CC = gcc
CFLAGS = -Wall -Wextra -std=c99 -O2
LIBS = -lwebsockets -lcjson

# 目标文件
CLIENT = websocket_upload
SERVER = websocket_server

# 源文件
CLIENT_SRC = websocket_upload.c
SERVER_SRC = websocket_server.c

# 默认目标
all: $(CLIENT) $(SERVER)

# 编译客户端
$(CLIENT): $(CLIENT_SRC)
	$(CC) $(CFLAGS) -o $(CLIENT) $(CLIENT_SRC) $(LIBS)

# 编译服务器
$(SERVER): $(SERVER_SRC)
	$(CC) $(CFLAGS) -o $(SERVER) $(SERVER_SRC) $(LIBS)

# 清理
clean:
	rm -f $(CLIENT) $(SERVER)

# 安装依赖（Ubuntu/Debian）
install-deps:
	sudo apt-get update
	sudo apt-get install -y libwebsockets-dev libcjson-dev build-essential

# 安装依赖（CentOS/RHEL）
install-deps-centos:
	sudo yum install -y libwebsockets-devel libcjson-devel gcc

# 创建测试文件
test-file:
	echo "这是一个测试文件，用于测试WebSocket文件上传功能。" > test.txt
	dd if=/dev/zero of=large_test.bin bs=1024 count=100

# 运行服务器（默认端口8080）
run-server:
	./$(SERVER)

# 运行服务器（指定端口）
run-server-port:
	./$(SERVER) 9090

# 测试上传小文件
test-upload:
	./$(CLIENT) localhost 8080 test.txt

# 测试上传大文件
test-upload-large:
	./$(CLIENT) localhost 8080 large_test.bin

# 帮助信息
help:
	@echo "WebSocket文件上传程序编译指南"
	@echo ""
	@echo "可用目标："
	@echo "  all              - 编译客户端和服务器"
	@echo "  $(CLIENT)       - 只编译客户端"
	@echo "  $(SERVER)        - 只编译服务器"
	@echo "  clean            - 清理编译产生的文件"
	@echo "  install-deps     - 安装依赖(Ubuntu/Debian)"
	@echo "  install-deps-centos - 安装依赖(CentOS/RHEL)"
	@echo "  test-file        - 创建测试文件"
	@echo "  run-server       - 运行服务器(端口8080)"
	@echo "  run-server-port  - 运行服务器(端口9090)"
	@echo "  test-upload      - 测试上传小文件"
	@echo "  test-upload-large - 测试上传大文件"
	@echo "  help             - 显示此帮助信息"

.PHONY: all clean install-deps install-deps-centos test-file run-server run-server-port test-upload test-upload-large help