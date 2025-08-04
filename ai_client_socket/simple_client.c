/*
 * 简化版AI客户端 - 专门用于调试recv断开问题
 * 移除了复杂的音频功能，只保留Socket通信核心功能
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <signal.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
#include <time.h>
#include <sys/time.h>
#include <errno.h>
#include <sys/select.h>

// Socket协议消息类型定义
#define MSG_VOICE_START     0x01
#define MSG_VOICE_DATA      0x02
#define MSG_VOICE_END       0x03
#define MSG_TEXT_DATA       0x04
#define MSG_AUDIO_DATA      0x05
#define MSG_AI_START        0x06
#define MSG_AI_END          0x07
#define MSG_AUDIO_START     0x08
#define MSG_AUDIO_END       0x09
#define MSG_ERROR           0x0A
#define MSG_AI_CANCELLED    0x0B
#define MSG_JSON_RESPONSE   0x0C
#define MSG_CONFIG          0x0D
#define MSG_AI_NEWCHAT      0x0E

static volatile int g_running = 1;
static volatile int g_gpio_recording = 0;
static volatile int g_gpio_pressed = 0;

typedef struct {
    const char *server_host;
    int server_port;
    int sockfd;
} simple_client_ctx_t;

// 时间戳日志输出函数
static void log_with_time(const char *message) {
    struct timeval tv;
    struct tm *tm_info;
    char time_buffer[100];
    
    gettimeofday(&tv, NULL);
    tm_info = localtime(&tv.tv_sec);
    
    strftime(time_buffer, sizeof(time_buffer), "[%H:%M:%S", tm_info);
    printf("%s.%03ld] [CLIENT] %s\n", time_buffer, tv.tv_usec / 1000, message);
    fflush(stdout);
}

// Socket协议：发送消息
static int socket_send_message(int sockfd, unsigned char msg_type, const void *data, unsigned int data_len) {
    unsigned char header[5];
    ssize_t sent_bytes;
    
    // 构建消息头：消息类型(1字节) + 数据长度(4字节，网络字节序)
    header[0] = msg_type;
    header[1] = (data_len >> 24) & 0xFF;
    header[2] = (data_len >> 16) & 0xFF;
    header[3] = (data_len >> 8) & 0xFF;
    header[4] = data_len & 0xFF;
    
    printf("📤 发送消息: 类型=0x%02X, 数据长度=%u\n", msg_type, data_len);
    fflush(stdout);
    
    // 发送消息头
    sent_bytes = send(sockfd, header, 5, 0);
    if (sent_bytes != 5) {
        printf("❌ 发送消息头失败: %s\n", strerror(errno));
        return -1;
    }
    
    // 发送数据（如果有的话）
    if (data_len > 0 && data != NULL) {
        sent_bytes = send(sockfd, data, data_len, 0);
        if (sent_bytes != (ssize_t)data_len) {
            printf("❌ 发送消息数据失败: %s\n", strerror(errno));
            return -1;
        }
    }
    
    printf("✅ 消息发送成功\n");
    fflush(stdout);
    return 0;
}

// Socket协议：接收消息
static int socket_receive_message(int sockfd, unsigned char *msg_type, void *data, unsigned int *data_len, unsigned int max_len) {
    unsigned char header[5];
    ssize_t received_bytes;
    unsigned int payload_len;
    
    struct timeval recv_start, recv_end;
    gettimeofday(&recv_start, NULL);
    
    log_with_time("开始等待socket数据...");
    
    // 设置接收超时
    struct timeval timeout;
    timeout.tv_sec = 30;  // 30秒超时
    timeout.tv_usec = 0;
    
    fd_set readfds;
    FD_ZERO(&readfds);
    FD_SET(sockfd, &readfds);
    
    // 检查socket是否有数据可读
    int select_result = select(sockfd + 1, &readfds, NULL, NULL, &timeout);
    
    if (select_result <= 0) {
        if (select_result == 0) {
            log_with_time("Socket receive timeout (30s)");
        } else {
            printf("Socket select failed: %s\n", strerror(errno));
        }
        return -1;
    }
    
    log_with_time("Socket数据就绪");
    
    // 接收消息头（5字节）
    received_bytes = recv(sockfd, header, 5, MSG_WAITALL);
    if (received_bytes != 5) {
        if (received_bytes == 0) {
            log_with_time("服务器关闭连接");
        } else if (received_bytes < 0) {
            printf("Socket receive error: %s\n", strerror(errno));
        } else {
            printf("Partial header received: %zd/5 bytes\n", received_bytes);
        }
        return -1;
    }
    
    // 解析消息头
    *msg_type = header[0];
    payload_len = (header[1] << 24) | (header[2] << 16) | (header[3] << 8) | header[4];
    
    printf("📥 接收消息: 类型=0x%02X, 数据长度=%u\n", *msg_type, payload_len);
    fflush(stdout);
    
    // 检查数据长度是否合理
    if (payload_len > max_len) {
        printf("❌ 数据长度过大: %u > %u\n", payload_len, max_len);
        return -1;
    }
    
    *data_len = payload_len;
    
    // 接收数据（如果有的话）
    if (payload_len > 0) {
        received_bytes = recv(sockfd, data, payload_len, MSG_WAITALL);
        if (received_bytes != (ssize_t)payload_len) {
            printf("❌ 接收消息数据失败: %zd/%u bytes, error: %s\n", received_bytes, payload_len, strerror(errno));
            return -1;
        }
        printf("📥 接收数据成功: %u字节\n", payload_len);
    }
    
    gettimeofday(&recv_end, NULL);
    long total_time = (recv_end.tv_sec - recv_start.tv_sec) * 1000 + 
                     (recv_end.tv_usec - recv_start.tv_usec) / 1000;
    printf("📥 消息接收完成，总耗时: %ldms\n", total_time);
    
    return 0;
}

// 连接到Socket服务器
static int connect_to_socket_server(const char *host, int port) {
    int sockfd;
    struct sockaddr_in server_addr;
    struct hostent *server;
    
    printf("正在连接到服务器 %s:%d\n", host, port);
    fflush(stdout);
    
    // 创建socket
    sockfd = socket(AF_INET, SOCK_STREAM, 0);
    if (sockfd < 0) {
        printf("❌ 创建socket失败: %s\n", strerror(errno));
        return -1;
    }
    
    // 解析主机名
    server = gethostbyname(host);
    if (server == NULL) {
        printf("❌ 解析主机名失败: %s\n", host);
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
        printf("❌ 连接服务器失败: %s:%d, error: %s\n", host, port, strerror(errno));
        close(sockfd);
        return -1;
    }
    
    printf("✅ 成功连接到服务器 %s:%d\n", host, port);
    fflush(stdout);
    return sockfd;
}

// 发送配置消息
static int send_config_message(int sockfd) {
    const char *config_json = "{\"response_format\": \"json\"}";
    
    log_with_time("发送配置消息");
    return socket_send_message(sockfd, MSG_CONFIG, config_json, strlen(config_json));
}

// 等待GPIO按下 (模拟：等待服务器发送"开始录音"指令)
static int wait_for_gpio_press(simple_client_ctx_t *ctx) {
    unsigned char msg_type;
    char buffer[1024];
    unsigned int data_len;
    
    log_with_time("等待GPIO按下 (等待'开始录音'指令)...");
    
    while (g_running) {
        if (socket_receive_message(ctx->sockfd, &msg_type, buffer, &data_len, sizeof(buffer)) != 0) {
            log_with_time("❌ 接收消息失败，连接可能断开");
            return -1;
        }
        
        if (msg_type == MSG_TEXT_DATA) {
            // 确保字符串结尾
            buffer[data_len] = '\0';
            printf("📝 收到文本消息: %s\n", buffer);
            
            if (strcmp(buffer, "开始录音") == 0) {
                log_with_time("✅ 收到开始录音指令");
                g_gpio_pressed = 1;
                g_gpio_recording = 1;
                return 0;
            }
        } else {
            printf("⚠️ 收到非期望的消息类型: 0x%02X\n", msg_type);
        }
    }
    
    return -1;
}

// 等待GPIO松开 (模拟：等待服务器发送"结束录音"指令)  
static int wait_for_gpio_release(simple_client_ctx_t *ctx) {
    unsigned char msg_type;
    char buffer[1024];
    unsigned int data_len;
    
    log_with_time("等待GPIO松开 (等待'结束录音'指令)...");
    
    while (g_running && g_gpio_pressed) {
        if (socket_receive_message(ctx->sockfd, &msg_type, buffer, &data_len, sizeof(buffer)) != 0) {
            log_with_time("❌ 接收消息失败，连接可能断开");
            return -1;
        }
        
        if (msg_type == MSG_TEXT_DATA) {
            // 确保字符串结尾
            buffer[data_len] = '\0';
            printf("📝 收到文本消息: %s\n", buffer);
            
            if (strcmp(buffer, "结束录音") == 0) {
                log_with_time("✅ 收到结束录音指令");
                g_gpio_pressed = 0;
                g_gpio_recording = 0;
                return 0;
            }
        } else {
            printf("⚠️ 收到非期望的消息类型: 0x%02X\n", msg_type);
        }
    }
    
    return 0;
}

// GPIO监控线程
static void* gpio_monitor_thread(void *ptr) {
    simple_client_ctx_t *ctx = (simple_client_ctx_t *)ptr;
    
    log_with_time("GPIO监控线程启动");
    
    while (g_running) {
        // 等待按钮按下
        if (wait_for_gpio_press(ctx) == 0) {
            log_with_time("模拟录音开始...");
            
            // 模拟录音过程 - 发送一些虚拟的语音数据
            if (socket_send_message(ctx->sockfd, MSG_VOICE_START, NULL, 0) == 0) {
                // 发送一些模拟的语音数据
                char dummy_audio[1024];
                memset(dummy_audio, 0, sizeof(dummy_audio));
                
                for (int i = 0; i < 5 && g_gpio_recording; i++) {
                    snprintf(dummy_audio, sizeof(dummy_audio), "录音数据包 #%d", i + 1);
                    socket_send_message(ctx->sockfd, MSG_VOICE_DATA, dummy_audio, strlen(dummy_audio));
                    usleep(100000); // 100ms延迟
                }
                
                socket_send_message(ctx->sockfd, MSG_VOICE_END, NULL, 0);
                log_with_time("模拟录音数据发送完成");
            }
            
            // 等待按钮松开
            if (wait_for_gpio_release(ctx) == 0) {
                log_with_time("录音会话结束");
            }
        } else {
            log_with_time("GPIO监控出错，退出线程");
            break;
        }
        
        usleep(10000); // 10ms
    }
    
    log_with_time("GPIO监控线程退出");
    return NULL;
}

static void signal_handler(int sig) {
    log_with_time("收到退出信号");
    g_running = 0;
}

int main(int argc, char *argv[]) {
    simple_client_ctx_t ctx = {0};
    pthread_t gpio_thread;
    
    // 默认参数
    ctx.server_host = "10.10.10.92";
    ctx.server_port = 8082;
    
    // 简单的参数解析
    if (argc >= 3) {
        ctx.server_host = argv[1];
        ctx.server_port = atoi(argv[2]);
    }
    
    printf("=== 简化版AI客户端 - recv断开调试版本 ===\n");
    printf("服务器地址: %s:%d\n", ctx.server_host, ctx.server_port);
    printf("========================================\n");
    
    // 设置信号处理
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);
    
    // 连接服务器
    ctx.sockfd = connect_to_socket_server(ctx.server_host, ctx.server_port);
    if (ctx.sockfd < 0) {
        printf("❌ 连接服务器失败\n");
        return 1;
    }
    
    // 发送配置消息
    if (send_config_message(ctx.sockfd) != 0) {
        printf("❌ 发送配置消息失败\n");
        close(ctx.sockfd);
        return 1;
    }
    
    // 启动GPIO监控线程
    if (pthread_create(&gpio_thread, NULL, gpio_monitor_thread, &ctx) != 0) {
        printf("❌ 创建GPIO监控线程失败\n");
        close(ctx.sockfd);
        return 1;
    }
    
    log_with_time("程序启动完成，等待服务器指令...");
    log_with_time("在服务器控制台输入 'start' 开始录音, 'stop' 结束录音");
    
    // 等待线程完成
    pthread_join(gpio_thread, NULL);
    
    // 清理
    close(ctx.sockfd);
    log_with_time("程序退出");
    
    return 0;
} 