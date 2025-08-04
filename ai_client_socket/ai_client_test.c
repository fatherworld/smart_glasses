/*
 * Custom Audio Recorder with Socket Communication
 * Based on SocketClient.py protocol implementation
 * 
 * 这是一个为RV1106B芯片设计的嵌入式Socket客户端，实现了与AI Socket服务器的通信。
 * 主要功能：
 * - 录制音频并通过Socket协议发送到AI服务器
 * - 支持流式和JSON两种响应格式
 * - 支持实时音频播放（流式模式）
 * - 使用与Python SocketClient.py相同的二进制协议
 * - 自动音频参数配置
 * 
 * 编译命令示例：
 * gcc -o embedded_client embedded_client_example.c -lrkmedia -lpthread
 * 
 * 使用示例：
 * ./embedded_client --enable-upload --server 192.168.1.100 --format stream --enable-streaming
 * 
 * Copyright 2024
 */

#include <stdio.h>
#include <errno.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <pthread.h>
#include <signal.h>
#include <sys/poll.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <time.h>
#include <sys/time.h>
#include <errno.h>
#include "rk_defines.h"
#include "rk_debug.h"
#include "rk_mpi_ai.h"
#include "rk_mpi_ao.h"
#include "rk_mpi_amix.h"
#include "rk_mpi_sys.h"
#include "rk_mpi_mb.h"
#include <getopt.h>
#include "test_comm_argparse.h"
// 连接到Socket服务器
static int connect_to_socket_server(const char *host, int port) {
    int sockfd;
    struct sockaddr_in server_addr;
    struct hostent *server;
    
    //printf("INFO: Starting connection to server %s:%d\n", host, port);
    //fflush(stdout);
    
    // 创建socket
    sockfd = socket(AF_INET, SOCK_STREAM, 0);
    if (sockfd < 0) {
        //printf("ERROR: Failed to create socket\n");
        //fflush(stdout);
        return -1;
    }
    
    // 解析主机名
    server = gethostbyname(host);
    if (server == NULL) {
        //printf("ERROR: Failed to resolve hostname: %s\n", host);
        //fflush(stdout);
        close(sockfd);
        return -1;
    }
    
    // 设置服务器地址
    memset(&server_addr, 0, sizeof(server_addr));
    server_addr.sin_family = AF_INET;
    memcpy(&server_addr.sin_addr.s_addr, server->h_addr, server->h_length);
    server_addr.sin_port = htons(port);
    
    // 连接服务器
    if (connect(sockfd, (struct sockaddr *)&server_addr, sizeof(server_addr)) < 0) {

        //perror("connect 失败");  // 会自动拼接 ": 错误原因"
    
        // 方法 2：获取错误码对应的描述字符串（更灵活）
        //fprintf(stderr, "connect 失败: %s (错误码: %d)\n", strerror(errno), errno);

        //printf("ERROR: Failed to connect to server: %s:%d\n", host, port);
        //fflush(stdout);
        //close(sockfd);
        return -1;
    }
    
    //printf("INFO: Successfully connected to server %s:%d\n", host, port);
    //fflush(stdout);
    return sockfd;
}
int main(int argc, const char **argv) {
    //在这里连接到服务器拿到socketfd

    // 定义长选项结构体
    static struct option long_options[] = {
        {"server",   required_argument, 0, 's'},
        {"port",  required_argument, 0, 'p'},
        {"recordtime",  required_argument, 0, 'r'},
        {0, 0, 0, 0}
    };
    int opt;
    int option_index = 0;
    const char *serverHost;
    RK_S32      serverPort;          // 服务器端口
    // 解析命令行参数
    while ((opt = getopt_long(argc, argv, "s:p:r", long_options, &option_index)) != -1) {
        switch (opt) {
            case 's':
                serverHost=optarg;
                break;
            case 'p':
                serverPort = atoi(optarg);
                break;
            case 'r':
                //ctx->s32RecordSeconds = atoi(optarg);
                break;
            default:
                abort();
        }
    }
    ssize_t sockfd = connect_to_socket_server(serverHost, serverPort);
     if (sockfd < 0) {
        //printf("ERROR: Failed to connect to socket server");
        return RK_FAILURE;
    }
    //printf("INFO: Successfully connected to socket server:ctx->sockfd:%d",ctx->sockfd);
    return RK_FAILURE;
} 

