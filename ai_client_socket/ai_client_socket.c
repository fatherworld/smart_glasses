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

#include "test_comm_argparse.h"

// Socket协议相关定义
#define SOCKET_BUFFER_SIZE (8192)
#define AUDIO_PLAY_BUFFER_SIZE (655360)  // 增大到64KB，支持大的音频数据包
#define SOCKET_REQUEST_BUFFER_SIZE (16384)
#define SOCKET_RESPONSE_BUFFER_SIZE (655360)  // 增大到64KB，支持更大的音频数据包

// Socket协议消息类型定义（与Python SocketClient保持一致）
#define MSG_VOICE_START     0x01    // 开始语音传输
#define MSG_VOICE_DATA      0x02    // 语音数据块
#define MSG_VOICE_END       0x03    // 语音传输结束
#define MSG_TEXT_DATA       0x04    // 文本数据
#define MSG_AUDIO_DATA      0x05    // 音频数据
#define MSG_AI_START        0x06    // AI开始响应
#define MSG_AI_END          0x07    // AI响应结束
#define MSG_AUDIO_START     0x08    // 音频开始
#define MSG_AUDIO_END       0x09    // 音频结束
#define MSG_ERROR           0x0A    // 错误消息
#define MSG_AI_CANCELLED    0x0B    // AI响应被取消
#define MSG_JSON_RESPONSE   0x0C    // JSON响应
#define MSG_CONFIG          0x0D    // 配置消息
#define MSG_AI_NEWCHAT      0x0E    // 新对话开始

// 音频包分段结束标记（与Python SocketClient保持一致）
static const unsigned char AUDIO_END_MARKER[8] = {0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF};

static RK_BOOL gRecorderExit = RK_FALSE;
static RK_BOOL gGpioRecording = RK_FALSE;  // GPIO录音状态标志
static RK_BOOL gGpioPressed = RK_FALSE;    // GPIO按钮按下状态
static RK_BOOL gAudioPlaying = RK_FALSE;   // 音频播放状态标志
static RK_BOOL gAudioInterrupted = RK_FALSE;  // 音频中断标志
static pthread_mutex_t gAudioStateMutex = PTHREAD_MUTEX_INITIALIZER;  // 音频状态锁

static volatile RK_BOOL gInterruptAIResponse = RK_FALSE;
static volatile RK_BOOL gAIResponseActive = RK_FALSE; // 新增：AI响应进行中标志

typedef struct _MyRecorderCtx {
    const char *outputFilePath;
    RK_S32      s32RecordSeconds;
    RK_S32      s32DeviceSampleRate;
    RK_S32      s32SampleRate;
    RK_S32      s32DeviceChannel;
    RK_S32      s32Channel;
    RK_S32      s32BitWidth;
    RK_S32      s32DevId;
    RK_S32      s32ChnIndex;
    RK_S32      s32FrameNumber;
    RK_S32      s32FrameLength;
    char       *chCardName;
    RK_S32      s32AutoConfig;
    RK_S32      s32VqeEnable;
    RK_S32      s32SetVolume;
    RK_S32      s32EnableUpload;     // 是否启用Socket上传
    const char *serverHost;          // 服务器地址
    RK_S32      serverPort;          // 服务器端口
    const char *responseFormat;      // 响应格式 (json/stream)
    RK_S32      s32EnableStreaming;  // 是否启用流式音频播放
    RK_S32      s32PlaybackSampleRate; // 播放采样率
    RK_S32      s32PlaybackChannels;    // 播放声道数
    RK_S32      s32PlaybackBitWidth;    // 播放位宽
    
    // 音频播放测试相关
    const char *testPlayFile;       // 测试播放文件路径
    
    // 时间统计相关
    RK_S32      s32EnableTiming;    // 是否启用详细时间统计
    
    // GPIO触发相关
    RK_S32      s32EnableGpioTrigger; // 是否启用GPIO触发录音
    const char *gpioDebugPath;        // GPIO调试文件路径
    RK_S32      s32GpioNumber;        // 要监控的GPIO编号
    RK_S32      s32GpioPollInterval;  // GPIO状态检查间隔(ms)
    
    // Socket通信相关
    int         sockfd;              // Socket文件描述符
    char        audio_buffer[AUDIO_PLAY_BUFFER_SIZE]; // 音频缓冲区
    size_t      audio_buffer_size;   // 音频缓冲区大小
} MY_RECORDER_CTX_S;

// 新增：时间统计相关定义
typedef struct _TimingStats {
    struct timeval voice_start_time;           // 语音开始发送时间
    struct timeval voice_data_first_time;      // 第一个语音数据包发送时间
    struct timeval voice_data_last_time;       // 最后一个语音数据包发送时间
    struct timeval voice_end_time;             // 语音发送结束时间
    struct timeval config_sent_time;           // 配置消息发送时间
    struct timeval ai_start_time;              // AI开始响应时间
    struct timeval audio_start_time;           // 音频开始时间
    struct timeval audio_first_data_time;      // 第一个音频数据包接收时间
    struct timeval audio_setup_complete_time;  // 音频播放设备设置完成时间
    struct timeval first_audio_play_time;      // 第一次音频播放时间
    struct timeval ai_end_time;                // AI响应结束时间
    
    // 统计数据
    long total_voice_bytes;                    // 发送的语音数据总字节数
    long total_audio_bytes;                    // 接收的音频数据总字节数
    int voice_data_packets;                    // 语音数据包数量
    int audio_data_packets;                    // 音频数据包数量
    int audio_segments_played;                 // 已播放的音频段数
    
    // 标志位
    int voice_transmission_started;            // 语音传输是否已开始
    int audio_playback_started;                // 音频播放是否已开始
    int first_audio_played;                    // 是否已播放第一个音频
    int timing_enabled;                        // 是否启用时间统计
} TIMING_STATS_S;

static TIMING_STATS_S g_timing_stats;

// 函数声明
static RK_S32 setup_audio_playback(MY_RECORDER_CTX_S *ctx);
static RK_S32 cleanup_audio_playback(void);
static void query_playback_status(void);
static RK_S32 play_audio_buffer(MY_RECORDER_CTX_S *ctx, const void *audio_data, size_t data_len);
static RK_S32 socket_send_message(int sockfd, unsigned char msg_type, const void *data, unsigned int data_len);
static RK_S32 socket_receive_message(int sockfd, unsigned char *msg_type, void *data, unsigned int *data_len, unsigned int max_len);
static RK_S32 process_received_message(MY_RECORDER_CTX_S *ctx, unsigned char msg_type, const void *data, unsigned int data_len);
static void socket_log_with_time(const char *message);
static AUDIO_SOUND_MODE_E find_sound_mode(RK_S32 ch);
static AUDIO_BIT_WIDTH_E find_bit_width(RK_S32 bit);

// 时间统计函数声明
static void init_timing_stats(void);
static void record_timestamp(struct timeval *tv, const char *event_name);
static long calculate_time_diff_ms(struct timeval *start, struct timeval *end);
static void print_timing_report(void);
static void print_stage_timing(const char *stage_name, struct timeval *start, struct timeval *end);

// GPIO触发相关函数声明
static RK_S32 read_gpio_state(const char *gpio_debug_path, RK_S32 gpio_number);
static void* gpio_monitor_thread(void *ptr);
static RK_S32 wait_for_gpio_press(MY_RECORDER_CTX_S *ctx);
static RK_S32 wait_for_gpio_release(MY_RECORDER_CTX_S *ctx);

// 音频播放状态管理函数声明
static void set_audio_playing_state(RK_BOOL playing);
static RK_BOOL get_audio_playing_state(void);
static RK_BOOL is_audio_interrupted(void);
static RK_S32 interrupt_audio_playback(void);

static void sigterm_handler(int sig) {
    printf("INFO: Recording interrupted by user (Ctrl+C)");
    gRecorderExit = RK_TRUE;
}

// 时间戳日志输出函数
static void socket_log_with_time(const char *message) {
    struct timeval tv;
    struct tm *tm_info;
    char time_buffer[100];
    char full_message[512];
    
    // Fix null message issue
    if (!message || strlen(message) == 0) {
        message = "EMPTY_MESSAGE";
    }
    
    gettimeofday(&tv, NULL);
    tm_info = localtime(&tv.tv_sec);
    
    strftime(time_buffer, sizeof(time_buffer), "[%H:%M:%S", tm_info);
    snprintf(full_message, sizeof(full_message), "%s.%03ld] [CLIENT] %s", 
             time_buffer, tv.tv_usec / 1000, message);
    
    printf("%s\n", full_message);
    fflush(stdout);
}

// Socket协议：打包消息
static RK_S32 socket_send_message(int sockfd, unsigned char msg_type, const void *data, unsigned int data_len) {
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
        printf("❌ 发送消息头失败\n");
        fflush(stdout);
        return RK_FAILURE;
    }
    
    // 发送数据（如果有的话）
    if (data_len > 0 && data != NULL) {
        sent_bytes = send(sockfd, data, data_len, 0);
        if (sent_bytes != (ssize_t)data_len) {
            printf("❌ 发送消息数据失败\n");
            fflush(stdout);
            return RK_FAILURE;
        }
    }
    
    printf("✅ 消息发送成功\n");
    fflush(stdout);
    return RK_SUCCESS;
}

// Socket协议：解包消息
static RK_S32 socket_receive_message(int sockfd, unsigned char *msg_type, void *data, unsigned int *data_len, unsigned int max_len) {
    unsigned char header[5];
    ssize_t received_bytes;
    unsigned int payload_len;
    
    // === 添加接收开始时间记录 ===
    struct timeval recv_start, recv_end, select_start, select_end;
    gettimeofday(&recv_start, NULL);
    
    // 设置接收超时
    struct timeval timeout;
    timeout.tv_sec = 30;  // 30秒超时
    timeout.tv_usec = 0;
    
    fd_set readfds;
    FD_ZERO(&readfds);
    FD_SET(sockfd, &readfds);
    
    // === 监控select等待时间 ===
    gettimeofday(&select_start, NULL);
    printf("📡 [DEBUG-SELECT] 开始等待socket数据...\n");
    
    // 检查socket是否有数据可读
    int select_result = select(sockfd + 1, &readfds, NULL, NULL, &timeout);
    
    gettimeofday(&select_end, NULL);
    long select_time = (select_end.tv_sec - select_start.tv_sec) * 1000 + 
                       (select_end.tv_usec - select_start.tv_usec) / 1000;
    
    if (select_result <= 0) {
        if (select_result == 0) {
            printf("WARNING: [DEBUG-TIMEOUT] Socket receive timeout (30s), select耗时:%ldms\n", select_time);
            fflush(stdout);
        } else {
            printf("ERROR: [DEBUG-SELECTERR] Socket select failed, select耗时:%ldms\n", select_time);
            fflush(stdout);
        }
        return RK_FAILURE;
    }
    
    printf("📡 [DEBUG-SELECTOK] Socket数据就绪, select耗时:%ldms\n", select_time);
    
    // === 监控header接收时间 ===
    struct timeval header_start, header_end;
    gettimeofday(&header_start, NULL);
    
    // 接收消息头（5字节）
    received_bytes = recv(sockfd, header, 5, MSG_WAITALL);
    
    gettimeofday(&header_end, NULL);
    long header_time = (header_end.tv_sec - header_start.tv_sec) * 1000 + 
                       (header_end.tv_usec - header_start.tv_usec) / 1000;
    
    if (received_bytes != 5) {
        if (received_bytes == 0) {
            printf("INFO: [DEBUG-CLOSED] Server closed connection gracefully, header接收耗时:%ldms\n", header_time);
            fflush(stdout);
        } else if (received_bytes < 0) {
            printf("ERROR: [DEBUG-RECVERR] Socket receive error, header接收耗时:%ldms\n", header_time);
            fflush(stdout);
        } else {
            printf("ERROR: [DEBUG-PARTIAL] Partial header received: %zd/5 bytes, header接收耗时:%ldms\n", 
                   received_bytes, header_time);
            fflush(stdout);
        }
        return RK_FAILURE;
    }
    
    if (header_time > 1) {
        printf("📡 [DEBUG-HEADERTIME] Header接收耗时: %ldms\n", header_time);
    }
    
    // 解析消息头
    *msg_type = header[0];
    payload_len = (header[1] << 24) | (header[2] << 16) | (header[3] << 8) | header[4];
    
    printf("INFO: [DEBUG-MSG] Received message: type=0x%02X, data_length=%u\n", *msg_type, payload_len);
    fflush(stdout);
    
    // 检查数据长度是否合理
    if (payload_len > max_len) {
        printf("ERROR: [DEBUG-TOOLARGE] Data length too large: %u > %u\n", payload_len, max_len);
        fflush(stdout);
        return RK_FAILURE;
    }
    
    *data_len = payload_len;
    
    // === 监控payload接收时间 ===
    struct timeval payload_start, payload_end;
    
    // 接收数据（如果有的话）
    if (payload_len > 0) {
        gettimeofday(&payload_start, NULL);
        printf("📡 [DEBUG-PAYLOAD] 开始接收payload: %u字节\n", payload_len);
        
        received_bytes = recv(sockfd, data, payload_len, MSG_WAITALL);
        
        gettimeofday(&payload_end, NULL);
        long payload_time = (payload_end.tv_sec - payload_start.tv_sec) * 1000 + 
                           (payload_end.tv_usec - payload_start.tv_usec) / 1000;
        
        if (received_bytes != (ssize_t)payload_len) {
            printf("ERROR: [DEBUG-PAYLOADFAIL] Failed to receive message data: %zd/%u bytes, payload接收耗时:%ldms\n", 
                   received_bytes, payload_len, payload_time);
            fflush(stdout);
            return RK_FAILURE;
        }
        
        // 计算接收速度
        if (payload_time > 0) {
            long payload_speed = payload_len * 1000 / payload_time; // bytes/sec
            printf("📡 [DEBUG-PAYLOADOK] Payload接收完成: %u字节, 耗时:%ldms, 速度:%ld字节/秒\n", 
                   payload_len, payload_time, payload_speed);
            
            // 如果是音频数据且接收耗时较长，发出警告
            if (*msg_type == MSG_AUDIO_DATA && payload_time > 10) {
                printf("⚠️ [DEBUG-SLOWPAYLOAD] 音频数据接收较慢: %ldms > 10ms, 可能影响播放连续性\n", payload_time);
            }
        }
    }
    
    // === 计算总接收时间 ===
    gettimeofday(&recv_end, NULL);
    long total_recv_time = (recv_end.tv_sec - recv_start.tv_sec) * 1000 + 
                          (recv_end.tv_usec - recv_start.tv_usec) / 1000;
    
    printf("📡 [DEBUG-RECVDONE] 消息接收完成: 类型=0x%02X, 数据=%u字节, 总耗时:%ldms\n", 
           *msg_type, payload_len, total_recv_time);
    
    // 如果总接收时间较长，发出警告
    if (total_recv_time > 20) {
        printf("⚠️ [DEBUG-SLOWRECV] 网络接收较慢: %ldms > 20ms, 可能阻塞音频播放\n", total_recv_time);
    }
    
    return RK_SUCCESS;
}

// 连接到Socket服务器
static int connect_to_socket_server(const char *host, int port) {
    int sockfd;
    struct sockaddr_in server_addr;
    struct hostent *server;
    
    printf("INFO: Starting connection to server %s:%d\n", host, port);
    fflush(stdout);
    
    // 创建socket
    sockfd = socket(AF_INET, SOCK_STREAM, 0);
    if (sockfd < 0) {
        printf("ERROR: Failed to create socket\n");
        fflush(stdout);
        return -1;
    }
    
    // 解析主机名
    server = gethostbyname(host);
    if (server == NULL) {
        printf("ERROR: Failed to resolve hostname: %s\n", host);
        fflush(stdout);
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
        printf("ERROR: Failed to connect to server: %s:%d\n", host, port);
        fflush(stdout);
        close(sockfd);
        return -1;
    }
    
    printf("INFO: Successfully connected to server %s:%d\n", host, port);
    fflush(stdout);
    return sockfd;
}

// 发送配置消息
static RK_S32 send_config_message(int sockfd, const char *response_format) {
    char config_json[256];
    
    printf("INFO: Sending configuration message to server...\n");
    fflush(stdout);
    
    // 构建配置JSON
    snprintf(config_json, sizeof(config_json), "{\"response_format\": \"%s\"}", response_format);
    
    RK_S32 result = socket_send_message(sockfd, MSG_CONFIG, config_json, strlen(config_json));
    
    // 记录配置发送完成时间
    if (result == RK_SUCCESS) {
        record_timestamp(&g_timing_stats.config_sent_time, "配置消息发送完成");
    }
    
    return result;
}

// 发送语音文件到Socket服务器
static RK_S32 send_voice_file_to_socket_server(MY_RECORDER_CTX_S *ctx) {
    FILE *file;
    char file_buffer[1024];
    size_t bytes_read;
    long total_sent = 0;
    long file_size;
    
    if (!ctx->s32EnableUpload || !ctx->outputFilePath) {
        return RK_SUCCESS;
    }
    
    printf("INFO: Starting to send audio file via socket\n");
    fflush(stdout);
    
    file = fopen(ctx->outputFilePath, "rb");
    if (!file) {
        printf("ERROR: Cannot open file: %s\n", ctx->outputFilePath);
        fflush(stdout);
        return RK_FAILURE;
    }
    
    // 获取文件大小
    fseek(file, 0, SEEK_END);
    file_size = ftell(file);
    fseek(file, 0, SEEK_SET);
    
    printf("INFO: File size: %ld bytes\n", file_size);
    fflush(stdout);
    
    // 发送语音开始信号
    record_timestamp(&g_timing_stats.voice_start_time, "语音开始发送");
    if (socket_send_message(ctx->sockfd, MSG_VOICE_START, NULL, 0) != RK_SUCCESS) {
        fclose(file);
        return RK_FAILURE;
    }
    
    // 分块发送文件数据
    while ((bytes_read = fread(file_buffer, 1, sizeof(file_buffer), file)) > 0) {
        // 记录第一个语音数据包发送时间
        if (g_timing_stats.voice_data_packets == 0) {
            record_timestamp(&g_timing_stats.voice_data_first_time, "第一个语音数据包发送");
        }
        
        if (socket_send_message(ctx->sockfd, MSG_VOICE_DATA, file_buffer, bytes_read) != RK_SUCCESS) {
            printf("ERROR: Failed to send voice data\n");
            fflush(stdout);
            fclose(file);
            return RK_FAILURE;
        }
        
        // 更新统计数据
        g_timing_stats.voice_data_packets++;
        g_timing_stats.total_voice_bytes += bytes_read;
        total_sent += bytes_read;
        
        // 记录最后一个数据包时间（每次都更新，最后一次就是最终时间）
        record_timestamp(&g_timing_stats.voice_data_last_time, "语音数据包发送");
        
        if (total_sent % 8192 == 0) {
            printf("INFO: Sent %ld/%ld bytes (包数: %d)\n", total_sent, file_size, g_timing_stats.voice_data_packets);
            fflush(stdout);
        }
        
        // 检查是否需要退出
        if (gRecorderExit) {
            break;
        }
    }
    
    fclose(file);
    
    // 发送语音结束信号
    if (socket_send_message(ctx->sockfd, MSG_VOICE_END, NULL, 0) != RK_SUCCESS) {
        return RK_FAILURE;
    }
    
    // 记录语音发送结束时间
    record_timestamp(&g_timing_stats.voice_end_time, "语音发送结束");
    
    printf("INFO: Voice file transmission completed: %ld bytes\n", total_sent);
    fflush(stdout);
    
    return RK_SUCCESS;
}

// 处理接收到的消息
static RK_S32 process_received_message(MY_RECORDER_CTX_S *ctx, unsigned char msg_type, const void *data, unsigned int data_len) {
    char log_msg[256];
    static int audio_started = 0;
    
    switch (msg_type) {
        case MSG_TEXT_DATA:
            if (data_len > 0) {
                printf("📝 文本: %.*s\n", data_len, (char*)data);
            }
            break;
            
        case MSG_AUDIO_DATA:
            // 检查音频是否被中断，如果是则清空缓冲区并忽略新数据
            if (is_audio_interrupted()) {
                // 清空音频缓冲区
                ctx->audio_buffer_size = 0;
                // 静默忽略被中断后的音频数据
                break;
            }
            
            // === 添加调试日志 ===
            struct timeval debug_tv;
            gettimeofday(&debug_tv, NULL);
            printf("🔊 [DEBUG-RECV] 接收音频数据: %u字节, 时间:%ld.%03ld, 当前缓冲:%zu字节\n", 
                   data_len, debug_tv.tv_sec, debug_tv.tv_usec/1000, ctx->audio_buffer_size);
            
            // 记录第一个音频数据包接收时间
            if (g_timing_stats.audio_data_packets == 0) {
                record_timestamp(&g_timing_stats.audio_first_data_time, "第一个音频数据包接收");
            }
            g_timing_stats.audio_data_packets++;
            g_timing_stats.total_audio_bytes += data_len;
            
            // 检查是否是音频包尾标记
            if (data_len == 8 && memcmp(data, AUDIO_END_MARKER, 8) == 0) {
                printf("🔊 [DEBUG-MARKER] 音频包结束标记, 当前缓冲区:%zu字节\n", ctx->audio_buffer_size);
                
                // 播放当前缓冲区的音频
                if (ctx->audio_buffer_size > 0) {
                    struct timeval play_start, play_end;
                    gettimeofday(&play_start, NULL);
                    
                    snprintf(log_msg, sizeof(log_msg), "🎵 播放音频段: %zu 字节", ctx->audio_buffer_size);
                    printf(log_msg);
                    
                    if (ctx->s32EnableStreaming && audio_started) {
                        // === 查询播放前的设备状态 ===
                        query_playback_status();
                        
                        // 播放音频数据
                        if (play_audio_buffer(ctx, ctx->audio_buffer, ctx->audio_buffer_size) != RK_SUCCESS) {
                            printf("⚠️ 音频播放失败");
                        }
                        
                        gettimeofday(&play_end, NULL);
                        long play_time = (play_end.tv_sec - play_start.tv_sec) * 1000 + 
                                        (play_end.tv_usec - play_start.tv_usec) / 1000;
                        printf("🎵 [DEBUG-PLAY] 播放耗时:%ldms, 数据量:%zu字节\n", 
                               play_time, ctx->audio_buffer_size);
                    }
                    
                    // 重置缓冲区
                    ctx->audio_buffer_size = 0;
                }
            } else {
                if (g_timing_stats.timing_enabled) {
                    snprintf(log_msg, sizeof(log_msg), "🔊 音频数据: %u 字节 [包#%d, 总计:%ld字节] [前4字节: %02X %02X %02X %02X]", 
                             data_len, g_timing_stats.audio_data_packets, g_timing_stats.total_audio_bytes,
                             data_len >= 4 ? ((unsigned char*)data)[0] : 0,
                             data_len >= 4 ? ((unsigned char*)data)[1] : 0,
                             data_len >= 4 ? ((unsigned char*)data)[2] : 0,
                             data_len >= 4 ? ((unsigned char*)data)[3] : 0);
                    printf(log_msg);
                } else {
                    // 简化输出，只显示关键信息
                    if (g_timing_stats.audio_data_packets % 10 == 1) { // 每10个包显示一次
                        printf("🔊 正在接收音频数据... (包#%d, 总计:%.1fKB)\n", 
                               g_timing_stats.audio_data_packets, g_timing_stats.total_audio_bytes / 1024.0);
                        fflush(stdout);
                    }
                }
                
                // === 添加缓冲区管理调试 ===
                printf("🔊 [DEBUG-BUFFER] 处理策略判断: 数据大小=%u, 缓冲区阈值=%zu\n", 
                       data_len, sizeof(ctx->audio_buffer) / 2);
                
                // 处理大音频包 - 如果单个包就很大，直接播放
                if (data_len > sizeof(ctx->audio_buffer) / 2) {
                    // 大音频包直接播放，不缓冲
                    struct timeval big_play_start, big_play_end;
                    gettimeofday(&big_play_start, NULL);
                    
                    snprintf(log_msg, sizeof(log_msg), "🎵 直接播放大音频包: %u 字节", data_len);
                    printf(log_msg);
                    
                    if (ctx->s32EnableStreaming && audio_started) {
                        // === 查询播放前的设备状态 ===
                        query_playback_status();
                        
                        if (play_audio_buffer(ctx, data, data_len) != RK_SUCCESS) {
                            printf("⚠️ 大音频包播放失败");
                        }
                        
                        gettimeofday(&big_play_end, NULL);
                        long big_play_time = (big_play_end.tv_sec - big_play_start.tv_sec) * 1000 + 
                                            (big_play_end.tv_usec - big_play_start.tv_usec) / 1000;
                        printf("🎵 [DEBUG-BIGPLAY] 大包播放耗时:%ldms, 数据量:%u字节\n", 
                               big_play_time, data_len);
                    }
                } else {
                    // 小音频包添加到缓冲区
                    printf("🔊 [DEBUG-BUFFER] 小包缓冲: 当前=%zu + 新增=%u = %zu, 容量=%zu\n", 
                           ctx->audio_buffer_size, data_len, ctx->audio_buffer_size + data_len, sizeof(ctx->audio_buffer));
                    
                    if (ctx->audio_buffer_size + data_len < sizeof(ctx->audio_buffer)) {
                        memcpy(ctx->audio_buffer + ctx->audio_buffer_size, data, data_len);
                        ctx->audio_buffer_size += data_len;
                        printf("🔊 [DEBUG-BUFFER] 成功缓冲，新的缓冲区大小:%zu字节\n", ctx->audio_buffer_size);
                    } else {
                        // 缓冲区不足，先播放现有的，再添加新的
                        struct timeval flush_start, flush_end;
                        gettimeofday(&flush_start, NULL);
                        
                        if (ctx->audio_buffer_size > 0) {
                            snprintf(log_msg, sizeof(log_msg), "🎵 缓冲区满，先播放: %zu 字节", ctx->audio_buffer_size);
                            printf(log_msg);
                            
                            if (ctx->s32EnableStreaming && audio_started) {
                                // === 查询播放前的设备状态 ===
                                query_playback_status();
                                
                                if (play_audio_buffer(ctx, ctx->audio_buffer, ctx->audio_buffer_size) != RK_SUCCESS) {
                                    printf("⚠️ 缓冲音频播放失败");
                                }
                                
                                gettimeofday(&flush_end, NULL);
                                long flush_time = (flush_end.tv_sec - flush_start.tv_sec) * 1000 + 
                                                 (flush_end.tv_usec - flush_start.tv_usec) / 1000;
                                printf("🎵 [DEBUG-FLUSH] 缓冲区刷新播放耗时:%ldms, 数据量:%zu字节\n", 
                                       flush_time, ctx->audio_buffer_size);
                            }
                        }
                        
                        // 重置缓冲区并添加新数据
                        ctx->audio_buffer_size = 0;
                        if (data_len < sizeof(ctx->audio_buffer)) {
                            memcpy(ctx->audio_buffer, data, data_len);
                            ctx->audio_buffer_size = data_len;
                            printf("🔊 [DEBUG-BUFFER] 缓冲区重置，新数据:%u字节\n", data_len);
                        } else {
                            printf("⚠️ [DEBUG-BUFFER] 单个音频包过大，无法缓冲: %u > %zu\n", 
                                   data_len, sizeof(ctx->audio_buffer));
                        }
                    }
                }
            }
            break;
            
        case MSG_AI_START:
            record_timestamp(&g_timing_stats.ai_start_time, "AI开始响应");
            printf("🤖 AI开始响应");
            gAIResponseActive = RK_TRUE; // 标记AI响应开始
            break;
            
        case MSG_AI_END:
            record_timestamp(&g_timing_stats.ai_end_time, "AI响应结束");
            printf("🤖 AI响应结束");
            gAIResponseActive = RK_FALSE; // AI响应结束
            break;
            
        case MSG_AUDIO_START:
            record_timestamp(&g_timing_stats.audio_start_time, "音频开始");
            printf("🔊 音频开始");
            ctx->audio_buffer_size = 0;  // 重置音频缓冲区
            
            if (ctx->s32EnableStreaming) {
                if (setup_audio_playback(ctx) == RK_SUCCESS) {
                    audio_started = 1;
                    set_audio_playing_state(RK_TRUE);  // 设置音频播放状态
                    record_timestamp(&g_timing_stats.audio_setup_complete_time, "音频播放设备设置完成");
                    printf("✅ 音频播放设备初始化成功");
                } else {
                    printf("❌ 音频播放设备初始化失败");
                }
            }
            break;
            
        case MSG_AUDIO_END:
            printf("🔊 音频结束");
            
            // 播放剩余的音频数据
            if (ctx->audio_buffer_size > 0) {
                snprintf(log_msg, sizeof(log_msg), "🎵 播放最后音频段: %zu 字节", ctx->audio_buffer_size);
                printf(log_msg);
                
                if (ctx->s32EnableStreaming && audio_started) {
                    // 播放最后的音频段
                    if (play_audio_buffer(ctx, ctx->audio_buffer, ctx->audio_buffer_size) != RK_SUCCESS) {
                        printf("⚠️ 音频播放失败");
                    }
                }
                
                ctx->audio_buffer_size = 0;
            }
            
            // 清理音频播放设备
            if (audio_started) {
                cleanup_audio_playback();
                set_audio_playing_state(RK_FALSE);  // 清除音频播放状态
                printf("🎵 音频播放设备已关闭");
                audio_started = 0;
            }
            
            printf("🎵 所有音频播放完毕");
            break;
            
        case MSG_ERROR:
            if (data_len > 0) {
                printf("❌ 错误: %.*s\n", data_len, (char*)data);
            }
            
            // 清理可能已经初始化的音频播放设备
            if (audio_started) {
                printf("🔧 清理因错误中断的音频播放设备");
                cleanup_audio_playback();
                set_audio_playing_state(RK_FALSE);  // 清除音频播放状态
                audio_started = 0;
                
                // 重置音频缓冲区
                ctx->audio_buffer_size = 0;
            }
            break;
            
        case MSG_AI_CANCELLED:
            printf("🚫 AI响应被取消");
            gAIResponseActive = RK_FALSE; // AI响应被取消
            
            // 清理可能已经初始化的音频播放设备
            if (audio_started) {
                printf("🔧 清理因取消中断的音频播放设备");
                cleanup_audio_playback();
                set_audio_playing_state(RK_FALSE);  // 清除音频播放状态
                audio_started = 0;
                
                // 重置音频缓冲区
                ctx->audio_buffer_size = 0;
            }
            break;
            
        case MSG_JSON_RESPONSE:
            if (data_len > 0) {
                printf("📋 JSON响应: %.*s\n", data_len, (char*)data);
            }
            break;
            
        case MSG_AI_NEWCHAT:
            printf("💬 新对话开始");
            break;
            
        default:
            snprintf(log_msg, sizeof(log_msg), "❓ 未知消息类型: 0x%02X, 数据长度: %u", msg_type, data_len);
            printf(log_msg);
            break;
    }
    
    return RK_SUCCESS;
}

// 接收Socket服务器响应
static RK_S32 receive_socket_response(MY_RECORDER_CTX_S *ctx) {
    unsigned char msg_type;
    char buffer[SOCKET_RESPONSE_BUFFER_SIZE];
    unsigned int data_len;
    int message_count = 0;
    int ai_end_received = 0;
    int error_received = 0;
    int consecutive_non_progress_msgs = 0;  // 连续非进展消息计数
    char log_msg[256];
    
    printf("=== 开始接收服务器响应 ===");
    
    while (!gRecorderExit && !ai_end_received) {
        if (gInterruptAIResponse) {
            printf("INFO: AI响应被用户抢话中断，立即进入录音\n");
            break;
        }
        RK_S32 receive_result = socket_receive_message(ctx->sockfd, &msg_type, buffer, &data_len, sizeof(buffer));
        
        if (receive_result != RK_SUCCESS) {
            if (message_count > 0) {
                printf("INFO: Connection closed after receiving messages");
                break; // 正常结束，已经收到一些消息
            } else {
                printf("ERROR: Failed to receive any messages");
                return RK_FAILURE;
            }
        }
        
        message_count++;
        snprintf(log_msg, sizeof(log_msg), "INFO: Processing message #%d (type=0x%02X)", message_count, msg_type);
        printf(log_msg);
        
        // 处理接收到的消息
        process_received_message(ctx, msg_type, buffer, data_len);
        
        // 跟踪进展性消息
        if (msg_type == MSG_AUDIO_DATA || msg_type == MSG_TEXT_DATA || 
            msg_type == MSG_AI_START || msg_type == MSG_AUDIO_START) {
            consecutive_non_progress_msgs = 0;  // 重置计数器
        } else {
            consecutive_non_progress_msgs++;
        }
        
        // 检查是否是结束消息
        if (msg_type == MSG_AI_END) {
            printf("INFO: AI_END received, preparing to close connection");
            ai_end_received = 1;
            // 继续接收一点时间，以防还有后续消息
            usleep(500000); // 等待500ms
        }
        
        if (msg_type == MSG_JSON_RESPONSE) {
            printf("INFO: JSON_RESPONSE received");
            // JSON模式通常在这里结束
            if (strcmp(ctx->responseFormat, "json") == 0) {
                break;
            }
        }
        
        // 处理错误消息 - 错误消息通常意味着处理结束
        if (msg_type == MSG_ERROR) {
            printf("INFO: ERROR message received, ending response processing");
            error_received = 1;
            ai_end_received = 1;  // 立即视为响应结束，跳出循环
            // 不再等待额外消息，直接跳出
            break;
        }
        
        // 处理取消消息 - 取消消息也意味着处理结束
        if (msg_type == MSG_AI_CANCELLED) {
            printf("INFO: AI_CANCELLED message received, ending response processing");
            break;
        }
        
        // 安全退出机制：如果已收到错误消息且连续收到非进展消息，退出
        if (error_received && consecutive_non_progress_msgs >= 2) {
            printf("INFO: Error received and no progress messages, ending response processing");
            break;
        }
        
        // 防止无限循环：如果连续收到太多非进展消息，退出
        if (consecutive_non_progress_msgs >= 5) {
            printf("WARNING: Too many consecutive non-progress messages, ending response processing");
            break;
        }
    }
    
    gInterruptAIResponse = RK_FALSE; // 恢复
    gAIResponseActive = RK_FALSE;   // 重置AI响应状态
    
    snprintf(log_msg, sizeof(log_msg), "INFO: Response processing completed (received %d messages)", message_count);
    printf(log_msg);
    printf("=== 响应接收完成 ===");
    
    // 打印详细的时间统计报告
    print_timing_report();
    
    return RK_SUCCESS;
}

// Socket音频上传功能（替代原来的HTTP上传）
static RK_S32 upload_audio_to_socket_server(MY_RECORDER_CTX_S *ctx) {
    char log_msg[256];
    
    printf("INFO: upload_audio_to_socket_server function called");
    gInterruptAIResponse = RK_FALSE; // 重置中断标志，开始新的AI响应
    
    // 根据用户设置初始化时间统计系统
    if (ctx->s32EnableTiming) {
        init_timing_stats();
    } else {
        g_timing_stats.timing_enabled = 0;
    }
    
    if (!ctx) {
        printf("ERROR: Context is null");
        return RK_FAILURE;
    }
    
    if (!ctx->s32EnableUpload) {
        printf("INFO: Upload is disabled, skipping");
        return RK_SUCCESS;
    }
    
    if (!ctx->outputFilePath) {
        printf("ERROR: Output file path is null");
        return RK_FAILURE;
    }
    
    snprintf(log_msg, sizeof(log_msg), "INFO: Server: %s:%d", ctx->serverHost, ctx->serverPort);
    printf(log_msg);
    snprintf(log_msg, sizeof(log_msg), "INFO: File: %s", ctx->outputFilePath);
    printf(log_msg);
    snprintf(log_msg, sizeof(log_msg), "INFO: Format: %s", ctx->responseFormat);
    printf(log_msg);
    snprintf(log_msg, sizeof(log_msg), "INFO: Streaming: %s", ctx->s32EnableStreaming ? "enabled" : "disabled");
    printf(log_msg);
    
    // 连接服务器
    printf("INFO: Starting connection to socket server");
    ctx->sockfd = connect_to_socket_server(ctx->serverHost, ctx->serverPort);
    if (ctx->sockfd < 0) {
        printf("ERROR: Failed to connect to socket server");
        return RK_FAILURE;
    }
    printf("INFO: Successfully connected to socket server");
    
    // 发送配置消息
    printf("INFO: Sending configuration message");
    if (send_config_message(ctx->sockfd, ctx->responseFormat) != RK_SUCCESS) {
        printf("ERROR: Failed to send configuration message");
        close(ctx->sockfd);
        return RK_FAILURE;
    }
    printf("INFO: Configuration message sent successfully");
    
    // 发送语音文件
    printf("INFO: Starting voice file transmission");
    if (send_voice_file_to_socket_server(ctx) != RK_SUCCESS) {
        printf("ERROR: Failed to send voice file");
        close(ctx->sockfd);
        return RK_FAILURE;
    }
    printf("INFO: Voice file sent successfully");
    
    // 接收响应
    printf("INFO: Starting to receive server response");
    RK_S32 result = receive_socket_response(ctx);
    
    // 关闭连接
    printf("INFO: Closing socket connection");
    close(ctx->sockfd);
    
    if (result == RK_SUCCESS) {
        printf("INFO: Socket processing completed successfully");
    } else {
        printf("ERROR: Socket processing failed");
    }
    
    return result;
}

// 播放设备管理结构体
typedef struct _PlaybackCtx {
    AUDIO_DEV aoDevId;
    AO_CHN aoChn;
    RK_BOOL bInitialized;
    RK_S32 s32SampleRate;
    RK_S32 s32Channels;
    RK_S32 s32BitWidth;
} PLAYBACK_CTX_S;

static PLAYBACK_CTX_S g_stPlaybackCtx = {0, 0, RK_FALSE, 0, 0, 0};

// 音频播放器设置 - 基于test_mpi_ao.c的设备初始化逻辑
static RK_S32 setup_audio_playback(MY_RECORDER_CTX_S *ctx) {
    AUDIO_DEV aoDevId = 0; // 使用0号设备
    AO_CHN aoChn = 0;
    AIO_ATTR_S aoAttr;
    AO_CHN_PARAM_S stParams;
    RK_S32 result;
    char log_msg[256];
    
    // === 添加设备初始化开始日志 ===
    struct timeval setup_start, setup_end;
    gettimeofday(&setup_start, NULL);
    printf("🔧 [DEBUG-SETUPSTART] 开始初始化音频播放设备\n");
    
    // 如果已经初始化，先清理
    if (g_stPlaybackCtx.bInitialized) {
        printf("🔧 [DEBUG-CLEANUP] 播放设备已初始化，先清理...\n");
        RK_MPI_AO_DisableChn(g_stPlaybackCtx.aoDevId, g_stPlaybackCtx.aoChn);
        RK_MPI_AO_Disable(g_stPlaybackCtx.aoDevId);
        g_stPlaybackCtx.bInitialized = RK_FALSE;
        printf("🔧 [DEBUG-CLEANUP] 旧设备清理完成\n");
    }
    
    memset(&aoAttr, 0, sizeof(AIO_ATTR_S));
    
    snprintf(log_msg, sizeof(log_msg), "📱 初始化音频播放设备 (设备=%d, 采样率=%dHz, 声道=%d, 位宽=%d)", 
             aoDevId, ctx->s32PlaybackSampleRate, ctx->s32PlaybackChannels, ctx->s32PlaybackBitWidth);
    printf(log_msg);
    
    // 设置播放设备属性 - 参考test_open_device_ao
    if (ctx->chCardName) {
        snprintf((char *)(aoAttr.u8CardName), sizeof(aoAttr.u8CardName), "%s", ctx->chCardName);
    }
    
    // 硬件参数设置
    aoAttr.soundCard.channels = ctx->s32PlaybackChannels;
    aoAttr.soundCard.sampleRate = ctx->s32PlaybackSampleRate;
    aoAttr.soundCard.bitWidth = find_bit_width(ctx->s32PlaybackBitWidth);
    
    // 流参数设置
    aoAttr.enBitwidth = find_bit_width(ctx->s32PlaybackBitWidth);
    aoAttr.enSamplerate = (AUDIO_SAMPLE_RATE_E)ctx->s32PlaybackSampleRate;
    aoAttr.enSoundmode = find_sound_mode(ctx->s32PlaybackChannels);
    aoAttr.u32FrmNum = 8;          // 增加缓冲区数量避免underrun
    aoAttr.u32PtNumPerFrm = 409600;  // 参考test_mpi_ao.c的默认值
    
    aoAttr.u32EXFlag = 0;
    aoAttr.u32ChnCnt = 2;
    
    // === 添加详细的设备参数调试 ===
    printf("🔧 [DEBUG-PARAMS] 详细播放参数配置:\n");
    printf("    声卡名称: %s\n", aoAttr.u8CardName);
    printf("    硬件参数: 声道=%d, 采样率=%d, 位宽=%d\n", 
           aoAttr.soundCard.channels, aoAttr.soundCard.sampleRate, aoAttr.soundCard.bitWidth);
    printf("    流参数: 位宽=%d, 采样率=%d, 声道模式=%d\n", 
           aoAttr.enBitwidth, aoAttr.enSamplerate, aoAttr.enSoundmode);
    printf("    缓冲参数: 帧数=%d, 每帧点数=%d, 通道数=%d\n", 
           aoAttr.u32FrmNum, aoAttr.u32PtNumPerFrm, aoAttr.u32ChnCnt);
    
    // 计算总缓冲区大小
    int bytes_per_sample = (ctx->s32PlaybackBitWidth / 8) * ctx->s32PlaybackChannels;
    int total_buffer_samples = aoAttr.u32FrmNum * aoAttr.u32PtNumPerFrm;
    int total_buffer_bytes = total_buffer_samples * bytes_per_sample;
    double buffer_duration_ms = (double)total_buffer_samples / ctx->s32PlaybackSampleRate * 1000;
    
    printf("🔧 [DEBUG-BUFFERSIZE] 计算的缓冲区信息:\n");
    printf("    每样本字节数: %d\n", bytes_per_sample);
    printf("    总缓冲样本数: %d\n", total_buffer_samples);
    printf("    总缓冲字节数: %d\n", total_buffer_bytes);
    printf("    缓冲时长: %.2f ms\n", buffer_duration_ms);
    
    // 如果缓冲时长过短，发出警告
    if (buffer_duration_ms < 50) {
        printf("⚠️ [DEBUG-SHORTBUF] 播放缓冲区时长过短 (%.2fms < 50ms)，可能导致underrun\n", buffer_duration_ms);
    }
    
    snprintf(log_msg, sizeof(log_msg), "🔧 播放参数: 声卡=%s, 硬件声道=%d, 硬件采样率=%d, 硬件位宽=%d", 
             aoAttr.u8CardName, aoAttr.soundCard.channels, aoAttr.soundCard.sampleRate, aoAttr.soundCard.bitWidth);
    printf(log_msg);
    
    // === 监控设置属性耗时 ===
    struct timeval attr_start, attr_end;
    gettimeofday(&attr_start, NULL);
    
    result = RK_MPI_AO_SetPubAttr(aoDevId, &aoAttr);
    
    gettimeofday(&attr_end, NULL);
    long attr_time = (attr_end.tv_sec - attr_start.tv_sec) * 1000 + 
                     (attr_end.tv_usec - attr_start.tv_usec) / 1000;
    
    if (result != 0) {
        snprintf(log_msg, sizeof(log_msg), "❌ [DEBUG-ATTRERR] AO设置属性失败, 错误码: 0x%X, 耗时:%ldms", result, attr_time);
        printf(log_msg);
        return RK_FAILURE;
    }
    printf("✅ [DEBUG-ATTROK] AO设置属性成功, 耗时:%ldms\n", attr_time);
    
    // === 监控设备启用耗时 ===
    struct timeval enable_start, enable_end;
    gettimeofday(&enable_start, NULL);
    
    result = RK_MPI_AO_Enable(aoDevId);
    
    gettimeofday(&enable_end, NULL);
    long enable_time = (enable_end.tv_sec - enable_start.tv_sec) * 1000 + 
                       (enable_end.tv_usec - enable_start.tv_usec) / 1000;
    
    if (result != 0) {
        snprintf(log_msg, sizeof(log_msg), "❌ [DEBUG-ENABLEERR] AO启用设备失败, 错误码: 0x%X, 耗时:%ldms", result, enable_time);
        printf(log_msg);
        return RK_FAILURE;
    }
    printf("✅ [DEBUG-ENABLEOK] AO启用设备成功, 耗时:%ldms\n", enable_time);
    
    // 设置通道参数 - 参考test_set_channel_params_ao
    memset(&stParams, 0, sizeof(AO_CHN_PARAM_S));
    stParams.enLoopbackMode = AUDIO_LOOPBACK_NONE;
    
    // === 监控通道参数设置耗时 ===
    struct timeval param_start, param_end;
    gettimeofday(&param_start, NULL);
    
    result = RK_MPI_AO_SetChnParams(aoDevId, aoChn, &stParams);
    
    gettimeofday(&param_end, NULL);
    long param_time = (param_end.tv_sec - param_start.tv_sec) * 1000 + 
                      (param_end.tv_usec - param_start.tv_usec) / 1000;
    
    if (result != RK_SUCCESS) {
        snprintf(log_msg, sizeof(log_msg), "❌ [DEBUG-PARAMERR] AO设置通道参数失败, 错误码: 0x%X, 耗时:%ldms", result, param_time);
        printf(log_msg);
        RK_MPI_AO_Disable(aoDevId);
        return RK_FAILURE;
    }
    printf("✅ [DEBUG-PARAMOK] AO设置通道参数成功, 耗时:%ldms\n", param_time);
    
    // === 监控通道启用耗时 ===
    struct timeval chn_start, chn_end;
    gettimeofday(&chn_start, NULL);
    
    // 启用通道
    result = RK_MPI_AO_EnableChn(aoDevId, aoChn);
    
    gettimeofday(&chn_end, NULL);
    long chn_time = (chn_end.tv_sec - chn_start.tv_sec) * 1000 + 
                    (chn_end.tv_usec - chn_start.tv_usec) / 1000;
    
    if (result != 0) {
        snprintf(log_msg, sizeof(log_msg), "❌ [DEBUG-CHNERR] AO启用通道失败, 错误码: 0x%X, 耗时:%ldms", result, chn_time);
        printf(log_msg);
        RK_MPI_AO_Disable(aoDevId);
        return RK_FAILURE;
    }
    printf("✅ [DEBUG-CHNOK] AO启用通道成功, 耗时:%ldms\n", chn_time);
    
    // 设置音量
    RK_MPI_AO_SetVolume(aoDevId, 100);
    printf("🔧 [DEBUG-VOLUME] 音量设置为100\n");
    
    // 记录播放上下文
    g_stPlaybackCtx.aoDevId = aoDevId;
    g_stPlaybackCtx.aoChn = aoChn;
    g_stPlaybackCtx.bInitialized = RK_TRUE;
    g_stPlaybackCtx.s32SampleRate = ctx->s32PlaybackSampleRate;
    g_stPlaybackCtx.s32Channels = ctx->s32PlaybackChannels;
    g_stPlaybackCtx.s32BitWidth = ctx->s32PlaybackBitWidth;
    
    // === 初始化完成后立即查询设备状态 ===
    printf("🔧 [DEBUG-INITSTATUS] 初始化完成后的设备状态:\n");
    query_playback_status();
    
    gettimeofday(&setup_end, NULL);
    long total_setup_time = (setup_end.tv_sec - setup_start.tv_sec) * 1000 + 
                           (setup_end.tv_usec - setup_start.tv_usec) / 1000;
    
    printf("✅ [DEBUG-SETUPDONE] 音频播放设备初始化完成, 总耗时:%ldms\n", total_setup_time);
    
    return RK_SUCCESS;
}

// 播放音频缓冲区数据 - 基于test_mpi_ao.c的sendDataThread逻辑
static RK_S32 play_audio_buffer(MY_RECORDER_CTX_S *ctx, const void *audio_data, size_t data_len) {
    if (!ctx || !audio_data || data_len == 0) {
        return RK_SUCCESS; // 无数据则直接返回成功
    }
    
    // === 添加播放开始调试日志 ===
    struct timeval play_start_tv;
    gettimeofday(&play_start_tv, NULL);
    printf("🎵 [DEBUG-PLAYSTART] 开始播放: %zu字节, 时间:%ld.%03ld\n", 
           data_len, play_start_tv.tv_sec, play_start_tv.tv_usec/1000);
    
    // 检查音频播放状态，如果已被中断则静默忽略
    if (!get_audio_playing_state()) {
        // 音频播放已被中断，静默忽略后续音频数据
        printf("🎵 [DEBUG-PLAYSKIP] 播放已被中断，跳过 %zu 字节\n", data_len);
        return RK_SUCCESS;
    }
    
    if (!g_stPlaybackCtx.bInitialized) {
        printf("❌ [DEBUG-PLAYERR] 播放设备未初始化\n");
        fflush(stdout);
        return RK_FAILURE;
    }
    
    // === 查询播放前设备状态 ===
    AO_CHN_STATE_S pstStatBefore;
    memset(&pstStatBefore, 0, sizeof(AO_CHN_STATE_S));
    RK_S32 ret = RK_MPI_AO_QueryChnStat(g_stPlaybackCtx.aoDevId, g_stPlaybackCtx.aoChn, &pstStatBefore);
    if (ret == RK_SUCCESS) {
        printf("📊 [DEBUG-DEVBEFORE] 播放前状态: 总计=%d, 空闲=%d, 忙碌=%d\n", 
               pstStatBefore.u32ChnTotalNum, pstStatBefore.u32ChnFreeNum, pstStatBefore.u32ChnBusyNum);
        
        // 如果空闲缓冲区少于2个，发出警告
        if (pstStatBefore.u32ChnFreeNum < 2) {
            printf("⚠️ [DEBUG-LOWBUF] 播放缓冲区即将耗尽！空闲=%d\n", pstStatBefore.u32ChnFreeNum);
        }
    }
    
    // 记录第一次音频播放时间
    if (!g_timing_stats.first_audio_played) {
        record_timestamp(&g_timing_stats.first_audio_play_time, "第一次音频播放开始");
        g_timing_stats.first_audio_played = 1;
    }
    
    // 参考test_mpi_ao.c的sendDataThread逻辑
    AUDIO_FRAME_S stFrame;
    RK_S32 result = RK_SUCCESS;
    RK_S32 s32MilliSec = -1; // 阻塞模式，确保数据发送成功
    static RK_U64 timeStamp = 0;
    
    // === 添加分块处理调试 ===
    if (data_len > 4096) {
        printf("🎵 [DEBUG-CHUNK] 大数据需要分块: %zu字节 -> 4096字节/块\n", data_len);
        
        const unsigned char *data_ptr = (const unsigned char *)audio_data;
        size_t remaining = data_len;
        RK_S32 overall_result = RK_SUCCESS;
        int chunk_count = 0;
        
        while (remaining > 0) {
            size_t chunk_size = (remaining > 4096) ? 4096 : remaining;
            chunk_count++;
            
            struct timeval chunk_start, chunk_end;
            gettimeofday(&chunk_start, NULL);
            
            printf("🎵 [DEBUG-CHUNK] 播放分块 %d: %zu字节\n", chunk_count, chunk_size);
            RK_S32 chunk_result = play_audio_buffer(ctx, data_ptr, chunk_size);
            
            gettimeofday(&chunk_end, NULL);
            long chunk_time = (chunk_end.tv_sec - chunk_start.tv_sec) * 1000 + 
                             (chunk_end.tv_usec - chunk_start.tv_usec) / 1000;
            printf("🎵 [DEBUG-CHUNK] 分块 %d 完成: %ldms\n", chunk_count, chunk_time);
            
            if (chunk_result != RK_SUCCESS) {
                overall_result = chunk_result;
            }
            data_ptr += chunk_size;
            remaining -= chunk_size;
        }
        
        struct timeval play_end_tv;
        gettimeofday(&play_end_tv, NULL);
        long total_time = (play_end_tv.tv_sec - play_start_tv.tv_sec) * 1000 + 
                         (play_end_tv.tv_usec - play_start_tv.tv_usec) / 1000;
        printf("🎵 [DEBUG-CHUNKDONE] 分块播放完成: %d块, 总耗时:%ldms\n", chunk_count, total_time);
        
        return overall_result;
    }
    
    // === 设置音频帧信息调试 ===
    printf("🎵 [DEBUG-FRAME] 设置音频帧: 长度=%zu, 采样率=%d, 声道=%d, 位宽=%d\n", 
           data_len, g_stPlaybackCtx.s32SampleRate, g_stPlaybackCtx.s32Channels, g_stPlaybackCtx.s32BitWidth);
    
    // 设置音频帧信息 - 参考test_mpi_ao.c
    stFrame.u32Len = data_len;
    stFrame.u64TimeStamp = timeStamp++;
    stFrame.s32SampleRate = g_stPlaybackCtx.s32SampleRate;
    stFrame.enBitWidth = find_bit_width(g_stPlaybackCtx.s32BitWidth);
    stFrame.enSoundMode = find_sound_mode(g_stPlaybackCtx.s32Channels);
    stFrame.bBypassMbBlk = RK_FALSE;
    
    // 使用外部内存创建内存块 - 参考test_mpi_ao.c
    MB_EXT_CONFIG_S extConfig;
    memset(&extConfig, 0, sizeof(extConfig));
    extConfig.pOpaque = (void*)audio_data;
    extConfig.pu8VirAddr = (RK_U8*)audio_data;
    extConfig.u64Size = data_len;
    
    struct timeval mb_start, mb_end;
    gettimeofday(&mb_start, NULL);
    
    result = RK_MPI_SYS_CreateMB(&(stFrame.pMbBlk), &extConfig);
    if (result != RK_SUCCESS) {
        printf("❌ [DEBUG-MB] 创建内存块失败: 0x%x, 数据长度:%zu\n", result, data_len);
        fflush(stdout);
        return RK_FAILURE;
    }
    
    gettimeofday(&mb_end, NULL);
    long mb_time = (mb_end.tv_sec - mb_start.tv_sec) * 1000 + 
                   (mb_end.tv_usec - mb_start.tv_usec) / 1000;
    if (mb_time > 1) {
        printf("🎵 [DEBUG-MB] 内存块创建耗时: %ldms\n", mb_time);
    }
    
__RETRY:
    // === 发送音频帧调试 ===
    {
        struct timeval send_start, send_end;
        gettimeofday(&send_start, NULL);
    
    // 发送音频帧 - 参考test_mpi_ao.c的重试逻辑
    result = RK_MPI_AO_SendFrame(g_stPlaybackCtx.aoDevId, g_stPlaybackCtx.aoChn, &stFrame, s32MilliSec);
    
    gettimeofday(&send_end, NULL);
    long send_time = (send_end.tv_sec - send_start.tv_sec) * 1000 + 
                     (send_end.tv_usec - send_start.tv_usec) / 1000;
    
    if (result < 0) {
        static int error_count = 0;
        if (error_count < 5) {
            printf("⚠️ [DEBUG-SENDERR] 发送音频帧失败: 0x%x, 时间戳=%lld, 耗时=%ldms (错误 %d/5)\n", 
                   result, stFrame.u64TimeStamp, send_time, ++error_count);
            fflush(stdout);
        }
        
        // 重试机制
        if (result == RK_ERR_AO_BUSY && error_count < 3) {
            printf("🎵 [DEBUG-RETRY] AO设备忙，10ms后重试...\n");
            usleep(10000); // 10ms
            goto __RETRY;
        }
    } else {
        // 成功发送，记录性能数据
        if (send_time > 5) { // 如果发送时间超过5ms则记录
            printf("🎵 [DEBUG-SENDOK] 发送成功但耗时较长: %ldms, 数据:%zu字节, 时间戳=%lld\n", 
                   send_time, data_len, stFrame.u64TimeStamp);
                 }
     }
     
     } // 结束调试代码块
     
     // 释放内存块
     RK_MPI_MB_ReleaseMB(stFrame.pMbBlk);
    
    // === 查询播放后设备状态 ===
    AO_CHN_STATE_S pstStatAfter;
    memset(&pstStatAfter, 0, sizeof(AO_CHN_STATE_S));
    ret = RK_MPI_AO_QueryChnStat(g_stPlaybackCtx.aoDevId, g_stPlaybackCtx.aoChn, &pstStatAfter);
    if (ret == RK_SUCCESS) {
        printf("📊 [DEBUG-DEVAFTER] 播放后状态: 总计=%d, 空闲=%d, 忙碌=%d\n", 
               pstStatAfter.u32ChnTotalNum, pstStatAfter.u32ChnFreeNum, pstStatAfter.u32ChnBusyNum);
        
        // 分析状态变化
        if (ret == RK_SUCCESS && pstStatBefore.u32ChnFreeNum > 0) {
            int free_change = pstStatAfter.u32ChnFreeNum - pstStatBefore.u32ChnFreeNum;
            printf("📊 [DEBUG-DEVCHANGE] 空闲缓冲区变化: %+d (播放前:%d -> 播放后:%d)\n", 
                   free_change, pstStatBefore.u32ChnFreeNum, pstStatAfter.u32ChnFreeNum);
        }
    }
    
    // 更新播放统计
    if (result == RK_SUCCESS) {
        g_timing_stats.audio_segments_played++;
    }
    
    struct timeval play_end_tv;
    gettimeofday(&play_end_tv, NULL);
    long total_play_time = (play_end_tv.tv_sec - play_start_tv.tv_sec) * 1000 + 
                          (play_end_tv.tv_usec - play_start_tv.tv_usec) / 1000;
    
    printf("🎵 [DEBUG-PLAYEND] 播放完成: %zu字节, 总耗时:%ldms, 结果:0x%x\n", 
           data_len, total_play_time, result);
    
    return result;
}

// 查询播放队列状态 - 用于调试
static void query_playback_status(void) {
    if (!g_stPlaybackCtx.bInitialized) {
        printf("📊 [DEBUG-NODEV] 播放设备未初始化，无法查询状态\n");
        return;
    }
    
    AO_CHN_STATE_S pstStat;
    memset(&pstStat, 0, sizeof(AO_CHN_STATE_S));
    
    // === 添加查询时间记录 ===
    struct timeval query_start, query_end;
    gettimeofday(&query_start, NULL);
    
    RK_S32 ret = RK_MPI_AO_QueryChnStat(g_stPlaybackCtx.aoDevId, g_stPlaybackCtx.aoChn, &pstStat);
    
    gettimeofday(&query_end, NULL);
    long query_time = (query_end.tv_sec - query_start.tv_sec) * 1000 + 
                      (query_end.tv_usec - query_start.tv_usec) / 1000;
    
    if (ret == RK_SUCCESS) {
        printf("📊 [DEBUG-STATUS] 播放队列状态 (查询耗时:%ldms):\n", query_time);
        printf("    总缓冲区数量: %d\n", pstStat.u32ChnTotalNum);
        printf("    空闲缓冲区数: %d\n", pstStat.u32ChnFreeNum);
        printf("    忙碌缓冲区数: %d\n", pstStat.u32ChnBusyNum);
        
        // 计算缓冲区使用率
        if (pstStat.u32ChnTotalNum > 0) {
            float usage_percent = (float)pstStat.u32ChnBusyNum / pstStat.u32ChnTotalNum * 100;
            printf("    缓冲区使用率: %.1f%% (%d/%d)\n", 
                   usage_percent, pstStat.u32ChnBusyNum, pstStat.u32ChnTotalNum);
            
            // 根据使用率和空闲数量给出警告
            if (pstStat.u32ChnFreeNum == 0) {
                printf("🚨 [DEBUG-CRITICAL] 严重警告: 所有缓冲区都被占用，立即会发生underrun!\n");
            } else if (pstStat.u32ChnFreeNum == 1) {
                printf("⚠️ [DEBUG-WARNING] 警告: 只剩1个空闲缓冲区，接近underrun!\n");
            } else if (pstStat.u32ChnFreeNum <= 2) {
                printf("⚠️ [DEBUG-CAUTION] 注意: 空闲缓冲区不足，可能发生underrun\n");
            } else {
                printf("✅ [DEBUG-HEALTHY] 缓冲区状态正常\n");
            }
            
            // 分析潜在问题
            if (usage_percent > 75) {
                printf("⚠️ [DEBUG-HIGHUSAGE] 缓冲区使用率过高 (%.1f%% > 75%%)，播放压力较大\n", usage_percent);
            }
        }
        
        // 估算当前缓冲区中的音频时长
        if (pstStat.u32ChnBusyNum > 0 && g_stPlaybackCtx.s32SampleRate > 0) {
            // 假设每个缓冲区包含4096个样本点（基于配置）
            int samples_per_buffer = 4096;
            int total_buffered_samples = pstStat.u32ChnBusyNum * samples_per_buffer;
            double buffered_duration_ms = (double)total_buffered_samples / g_stPlaybackCtx.s32SampleRate * 1000;
            
            printf("📊 [DEBUG-BUFFERTIME] 估算缓冲音频时长: %.2f ms (%d样本)\n", 
                   buffered_duration_ms, total_buffered_samples);
            
            // 如果缓冲时长过短，警告即将underrun
            if (buffered_duration_ms < 20) {
                printf("🚨 [DEBUG-SHORTTIME] 缓冲音频时长过短 (%.2fms < 20ms)，即将underrun!\n", buffered_duration_ms);
            } else if (buffered_duration_ms < 50) {
                printf("⚠️ [DEBUG-LOWTIME] 缓冲音频时长较短 (%.2fms < 50ms)，需要注意\n", buffered_duration_ms);
            }
        }
        
        fflush(stdout);
    } else {
        printf("❌ [DEBUG-QUERYERR] 查询播放队列状态失败: 0x%x, 查询耗时:%ldms\n", ret, query_time);
        fflush(stdout);
    }
}

// 清理音频播放设备 - 基于test_mpi_ao.c的deinit_mpi_ao逻辑
static RK_S32 cleanup_audio_playback(void) {
    if (!g_stPlaybackCtx.bInitialized) {
        return RK_SUCCESS;
    }
    
    RK_S32 result;
    
    // 等待播放完成 - 使用短超时，避免阻塞
    RK_S32 waitResult = RK_MPI_AO_WaitEos(g_stPlaybackCtx.aoDevId, g_stPlaybackCtx.aoChn, 1000); // 1秒超时
    if (waitResult != RK_SUCCESS) {
        printf("⚠️ 清理时等待播放完成超时: 0x%x\n", waitResult);
        fflush(stdout);
    }
    
    // 禁用重采样（如果有的话）
    result = RK_MPI_AO_DisableReSmp(g_stPlaybackCtx.aoDevId, g_stPlaybackCtx.aoChn);
    if (result != 0) {
        printf("⚠️ AO禁用重采样失败: 0x%x\n", result);
        fflush(stdout);
    }
    
    // 禁用通道
    result = RK_MPI_AO_DisableChn(g_stPlaybackCtx.aoDevId, g_stPlaybackCtx.aoChn);
    if (result != 0) {
        printf("❌ AO禁用通道失败: 0x%x\n", result);
        fflush(stdout);
    }
    
    // 禁用设备
    result = RK_MPI_AO_Disable(g_stPlaybackCtx.aoDevId);
    if (result != 0) {
        printf("❌ AO禁用设备失败: 0x%x\n", result);
        fflush(stdout);
    }
    
    // 重置播放上下文
    g_stPlaybackCtx.bInitialized = RK_FALSE;
    
    // 确保清除播放状态
    set_audio_playing_state(RK_FALSE);
    
    return RK_SUCCESS;
}

// 播放整个音频文件（用于测试） - 基于test_mpi_ao.c的sendDataThread逻辑
static RK_S32 play_audio_file(MY_RECORDER_CTX_S *ctx, const char *file_path) {
    FILE *file;
    RK_U8 *srcData = RK_NULL;
    AUDIO_FRAME_S frame;
    RK_U64 timeStamp = 0;
    RK_S32 s32MilliSec = -1;
    RK_S32 size = 0;
    RK_S32 len = 1024; // 使用与test_mpi_ao.c相同的缓冲区大小
    RK_S32 result = RK_SUCCESS;
    long file_size;
    long total_played = 0;
    
    printf("🎵 开始播放音频文件: %s\n", file_path);
    fflush(stdout);
    
    // 打开文件 - 参考test_mpi_ao.c的sendDataThread
    file = fopen(file_path, "rb");
    if (file == RK_NULL) {
        printf("❌ 无法打开音频文件: %s，错误: %s\n", file_path, strerror(errno));
        fflush(stdout);
        return RK_FAILURE;
    }
    
    // 获取文件大小
    fseek(file, 0, SEEK_END);
    file_size = ftell(file);
    fseek(file, 0, SEEK_SET);
    
    printf("📁 文件大小: %ld 字节\n", file_size);
    printf("🔧 播放参数: 采样率=%dHz, 声道=%d, 位宽=%d位\n", 
           ctx->s32PlaybackSampleRate, ctx->s32PlaybackChannels, ctx->s32PlaybackBitWidth);
    fflush(stdout);
    
    // 初始化音频播放设备
    if (setup_audio_playback(ctx) != RK_SUCCESS) {
        printf("❌ 音频播放设备初始化失败\n");
        fflush(stdout);
        fclose(file);
        return RK_FAILURE;
    }
    
    printf("✅ 音频播放设备初始化成功\n");
    fflush(stdout);
    
    // 分配缓冲区 - 参考test_mpi_ao.c
    srcData = (RK_U8 *)(calloc(len, sizeof(RK_U8)));
    if (!srcData) {
        printf("❌ 分配音频缓冲区失败\n");
        fflush(stdout);
        fclose(file);
        cleanup_audio_playback();
        return RK_FAILURE;
    }
    
    memset(srcData, 0, len);
    
    // 分块播放文件数据 - 参考test_mpi_ao.c的sendDataThread主循环
    while (1) {
        size = fread(srcData, 1, len, file);
        if (size <= 0) {
            printf("📖 文件读取完成\n");
            fflush(stdout);
            break;
        }
        
        // 设置音频帧信息 - 与test_mpi_ao.c完全一致
        frame.u32Len = size;
        frame.u64TimeStamp = timeStamp++;
        frame.s32SampleRate = g_stPlaybackCtx.s32SampleRate;
        frame.enBitWidth = find_bit_width(g_stPlaybackCtx.s32BitWidth);
        frame.enSoundMode = find_sound_mode(g_stPlaybackCtx.s32Channels);
        frame.bBypassMbBlk = RK_FALSE;
        
        // 使用外部内存创建内存块 - 与test_mpi_ao.c完全一致
        MB_EXT_CONFIG_S extConfig;
        memset(&extConfig, 0, sizeof(extConfig));
        extConfig.pOpaque = srcData;
        extConfig.pu8VirAddr = srcData;
        extConfig.u64Size = size;
        RK_MPI_SYS_CreateMB(&(frame.pMbBlk), &extConfig);
        
__RETRY:
        // 发送音频帧 - 与test_mpi_ao.c完全一致的重试逻辑
        result = RK_MPI_AO_SendFrame(g_stPlaybackCtx.aoDevId, g_stPlaybackCtx.aoChn, &frame, s32MilliSec);
        if (result < 0) {
            printf("⚠️ 发送音频帧失败: 0x%X, 时间戳=%lld, 超时=%d\n",
                   result, frame.u64TimeStamp, s32MilliSec);
            fflush(stdout);
            goto __RETRY;
        }
        
        // 释放内存块
        RK_MPI_MB_ReleaseMB(frame.pMbBlk);
        
        total_played += size;
        
        // 显示播放进度
        if (total_played % 8192 == 0) {
            printf("🎵 播放进度: %ld/%ld 字节 (%.1f%%)\n", 
                   total_played, file_size, (float)total_played / file_size * 100);
            fflush(stdout);
        }
        
        // 检查是否需要退出
        if (gRecorderExit) {
            printf("🛑 播放被用户中断\n");
            fflush(stdout);
            break;
        }
    }
    
    // 发送EOF标记，确保播放队列能够正确结束
    printf("📡 发送EOF标记确保播放结束...\n");
    fflush(stdout);
    
    // 发送一个长度为0的帧作为EOF标记
    memset(&frame, 0, sizeof(AUDIO_FRAME_S));
    frame.u32Len = 0;
    frame.u64TimeStamp = timeStamp++;
    frame.s32SampleRate = g_stPlaybackCtx.s32SampleRate;
    frame.enBitWidth = find_bit_width(g_stPlaybackCtx.s32BitWidth);
    frame.enSoundMode = find_sound_mode(g_stPlaybackCtx.s32Channels);
    frame.bBypassMbBlk = RK_FALSE;
    
    // 为EOF帧创建空的内存块
    MB_EXT_CONFIG_S eofConfig;
    memset(&eofConfig, 0, sizeof(eofConfig));
    eofConfig.pOpaque = srcData; // 重用srcData，但长度为0
    eofConfig.pu8VirAddr = srcData;
    eofConfig.u64Size = 0;
    RK_MPI_SYS_CreateMB(&(frame.pMbBlk), &eofConfig);
    
    result = RK_MPI_AO_SendFrame(g_stPlaybackCtx.aoDevId, g_stPlaybackCtx.aoChn, &frame, 1000); // 1秒超时
    if (result == RK_SUCCESS) {
        printf("✅ EOF标记发送成功\n");
    } else {
        printf("⚠️ EOF标记发送失败: 0x%x\n", result);
    }
    fflush(stdout);
    
    // 释放EOF帧的内存块
    RK_MPI_MB_ReleaseMB(frame.pMbBlk);
    
    // 查询播放队列状态
    printf("📊 播放完成前的队列状态:\n");
    query_playback_status();
    
    // 等待播放完成 - 添加超时机制，避免无限等待
    printf("⏳ 等待播放完成...\n");
    fflush(stdout);
    
    // 分阶段等待，避免长时间阻塞
    int wait_cycles = 0;
    const int max_wait_cycles = 10; // 最多等待10次，每次500ms
    RK_S32 waitResult = RK_FAILURE;
    
    while (wait_cycles < max_wait_cycles) {
        waitResult = RK_MPI_AO_WaitEos(g_stPlaybackCtx.aoDevId, g_stPlaybackCtx.aoChn, 500); // 500ms超时
        if (waitResult == RK_SUCCESS) {
            printf("✅ 播放队列已清空 (等待 %d 次)\n", wait_cycles + 1);
            fflush(stdout);
            break;
        }
        
        wait_cycles++;
        if (wait_cycles % 3 == 0) { // 每1.5秒查询一次状态
            printf("⏳ 继续等待... (第 %d/%d 次)\n", wait_cycles, max_wait_cycles);
            query_playback_status();
        }
        
        // 检查是否被用户中断
        if (gRecorderExit) {
            printf("🛑 等待被用户中断\n");
            fflush(stdout);
            break;
        }
    }
    
    if (waitResult != RK_SUCCESS && wait_cycles >= max_wait_cycles) {
        printf("⚠️ 等待播放完成超时，强制停止 (错误码: 0x%x)\n", waitResult);
        query_playback_status();
        fflush(stdout);
    }
    
    // 清理资源
    if (file) {
        fclose(file);
        file = RK_NULL;
    }
    
    if (srcData) {
        free(srcData);
        srcData = RK_NULL;
    }
    
    // 清理音频播放设备
    cleanup_audio_playback();
    
    printf("✅ 音频文件播放完成: %ld 字节\n", total_played);
    fflush(stdout);
    
    return result;
}

// 音频参数自动配置功能（基于record.sh）
static RK_S32 auto_configure_audio(MY_RECORDER_CTX_S *ctx) {
    RK_S32 ret = RK_SUCCESS;
    
    if (!ctx->s32AutoConfig) {
        printf("Auto configuration disabled, using default settings");
        return RK_SUCCESS;
    }
    
    printf("=== Auto configuring audio parameters ===");
    
    // 延时确保AMIX系统准备就绪
    usleep(100000); // 100ms
    
    // 1. 开启麦克风偏置电压（最重要！）
    printf("Enabling microphone bias voltage...");
    ret = RK_MPI_AMIX_SetControl(ctx->s32DevId, "ADC Main MICBIAS", "On");
    if (ret != RK_SUCCESS) {
        char log_msg[256];
        snprintf(log_msg, sizeof(log_msg), "WARNING: Failed to enable MICBIAS: 0x%X - Trying to continue...", ret);
        printf(log_msg);
        // 继续执行，不要因为一个设置失败就退出
    } else {
        printf("✓ MICBIAS enabled");
    }
    
    // 2. 提高麦克风硬件增益
    printf("Setting microphone gain to maximum...");
    ret = RK_MPI_AMIX_SetControl(ctx->s32DevId, "ADC MIC Left Gain", "3");
    if (ret != RK_SUCCESS) {
        char log_msg[256];
        snprintf(log_msg, sizeof(log_msg), "ERROR: Failed to set mic gain: 0x%X", ret);
        printf(log_msg);
    } else {
        printf("✓ Mic gain set to 3");
    }
    
    // 3. 提高数字音量
    printf("Setting digital volume...");
    ret = RK_MPI_AMIX_SetControl(ctx->s32DevId, "ADC Digital Left Volume", "240");
    if (ret != RK_SUCCESS) {
        char log_msg[256];
        snprintf(log_msg, sizeof(log_msg), "ERROR: Failed to set digital volume: 0x%X", ret);
        printf(log_msg);
    } else {
        printf("✓ Digital volume set to 240");
    }
    
    // 4. 设置ALC音量（与record.sh一致）
    printf("Setting ALC volume...");
    ret = RK_MPI_AMIX_SetControl(ctx->s32DevId, "ADC ALC Left Volume", "16");
    if (ret != RK_SUCCESS) {
        char log_msg[256];
        snprintf(log_msg, sizeof(log_msg), "ERROR: Failed to set ALC volume: 0x%X", ret);
        printf(log_msg);
    } else {
        printf("✓ ALC volume set to 16");
    }
    
    // 5. 设置AGC音量（与record.sh一致，最大值31）
    printf("Setting AGC volume...");
    ret = RK_MPI_AMIX_SetControl(ctx->s32DevId, "ALC AGC Left Volume", "31");
    if (ret != RK_SUCCESS) {
        char log_msg[256];
        snprintf(log_msg, sizeof(log_msg), "ERROR: Failed to set AGC volume: 0x%X", ret);
        printf(log_msg);
    } else {
        printf("✓ AGC volume set to 31");
    }
    
    // 6. 确保麦克风工作状态
    printf("Ensuring microphone is active...");
    ret = RK_MPI_AMIX_SetControl(ctx->s32DevId, "ADC MIC Left Switch", "Work");
    if (ret != RK_SUCCESS) {
        char log_msg[256];
        snprintf(log_msg, sizeof(log_msg), "ERROR: Failed to set mic switch: 0x%X", ret);
        printf(log_msg);
    } else {
        printf("✓ Mic switch set to Work");
    }
    
    // 7. 开启AGC功能
    printf("Enabling AGC...");
    ret = RK_MPI_AMIX_SetControl(ctx->s32DevId, "ALC AGC Left Switch", "On");
    if (ret != RK_SUCCESS) {
        char log_msg[256];
        snprintf(log_msg, sizeof(log_msg), "ERROR: Failed to enable AGC: 0x%X", ret);
        printf(log_msg);
    } else {
        printf("✓ AGC enabled");
    }
    
    printf("=== Audio configuration completed ===");
    return RK_SUCCESS;
}

static AUDIO_SOUND_MODE_E find_sound_mode(RK_S32 ch) {
    AUDIO_SOUND_MODE_E channel = AUDIO_SOUND_MODE_BUTT;
    switch (ch) {
      case 1:
        channel = AUDIO_SOUND_MODE_MONO;
        break;
      case 2:
        channel = AUDIO_SOUND_MODE_STEREO;
        break;
      case 4:
        channel = AUDIO_SOUND_MODE_4_CHN;
        break;
      case 6:
        channel = AUDIO_SOUND_MODE_6_CHN;
        break;
      case 8:
        channel = AUDIO_SOUND_MODE_8_CHN;
        break;
              default:
            {
                char log_msg[256];
                snprintf(log_msg, sizeof(log_msg), "ERROR: channel = %d not support", ch);
                printf(log_msg);
                return AUDIO_SOUND_MODE_BUTT;
            }
    }
    return channel;
}

static AUDIO_BIT_WIDTH_E find_bit_width(RK_S32 bit) {
    AUDIO_BIT_WIDTH_E bitWidth = AUDIO_BIT_WIDTH_BUTT;
    switch (bit) {
      case 8:
        bitWidth = AUDIO_BIT_WIDTH_8;
        break;
      case 16:
        bitWidth = AUDIO_BIT_WIDTH_16;
        break;
      case 32:
        bitWidth = AUDIO_BIT_WIDTH_32;
        break;
              default:
            {
                char log_msg[256];
                snprintf(log_msg, sizeof(log_msg), "ERROR: bitwidth(%d) not support", bit);
                printf(log_msg);
                return AUDIO_BIT_WIDTH_BUTT;
            }
    }
    return bitWidth;
}

static RK_S32 setup_audio_device(MY_RECORDER_CTX_S *ctx) {
    AUDIO_DEV aiDevId = ctx->s32DevId;
    AIO_ATTR_S aiAttr;
    RK_S32 result;
    
    memset(&aiAttr, 0, sizeof(AIO_ATTR_S));
    
    if (ctx->chCardName) {
        snprintf((char *)(aiAttr.u8CardName),
                 sizeof(aiAttr.u8CardName), "%s", ctx->chCardName);
    }
    
    aiAttr.soundCard.channels = ctx->s32DeviceChannel;
    aiAttr.soundCard.sampleRate = ctx->s32DeviceSampleRate;
    
    AUDIO_BIT_WIDTH_E bitWidth = find_bit_width(ctx->s32BitWidth);
    if (bitWidth == AUDIO_BIT_WIDTH_BUTT) {
        return RK_FAILURE;
    }
    
    aiAttr.soundCard.bitWidth = bitWidth;
    aiAttr.enBitwidth = bitWidth;
    aiAttr.enSamplerate = (AUDIO_SAMPLE_RATE_E)ctx->s32SampleRate;
    
    AUDIO_SOUND_MODE_E soundMode = find_sound_mode(ctx->s32Channel);
    if (soundMode == AUDIO_SOUND_MODE_BUTT) {
        return RK_FAILURE;
    }
    aiAttr.enSoundmode = soundMode;
    aiAttr.u32FrmNum = ctx->s32FrameNumber;
    aiAttr.u32PtNumPerFrm = ctx->s32FrameLength;
    
    // 设置通道映射
    aiAttr.u8MapOutChns[0] = ctx->s32DeviceChannel;
    for (int j = 0; j < ctx->s32DeviceChannel; j++)
        aiAttr.u8MapChns[0][j] = j;
    
    aiAttr.u32EXFlag = 1;
    aiAttr.u32ChnCnt = 2;
    
    result = RK_MPI_AI_SetPubAttr(aiDevId, &aiAttr);
    if (result != 0) {
        char log_msg[256];
        snprintf(log_msg, sizeof(log_msg), "ERROR: AI set attr fail, reason = 0x%X", result);
        printf(log_msg);
        return RK_FAILURE;
    }
    
    result = RK_MPI_AI_Enable(aiDevId);
    if (result != 0) {
        char log_msg[256];
        snprintf(log_msg, sizeof(log_msg), "ERROR: AI enable fail, reason = 0x%X", result);
        printf(log_msg);
        return RK_FAILURE;
    }
    
    return RK_SUCCESS;
}

static RK_S32 setup_audio_channel(MY_RECORDER_CTX_S *ctx) {
    RK_S32 result;
    AI_CHN_PARAM_S pstParams;
    
    memset(&pstParams, 0, sizeof(AI_CHN_PARAM_S));
    pstParams.enLoopbackMode = AUDIO_LOOPBACK_NONE;
    pstParams.s32UsrFrmDepth = 4;
    pstParams.u32MapPtNumPerFrm = ctx->s32FrameLength;
    pstParams.enSamplerate = (AUDIO_SAMPLE_RATE_E)ctx->s32SampleRate;
    
    result = RK_MPI_AI_SetChnParam(ctx->s32DevId, ctx->s32ChnIndex, &pstParams);
    if (result != RK_SUCCESS) {
        char log_msg[256];
        snprintf(log_msg, sizeof(log_msg), "ERROR: AI set channel params failed: 0x%x", result);
        printf(log_msg);
        return RK_FAILURE;
    }
    
    // 初始化VQE（如果启用）
    if (ctx->s32VqeEnable) {
        AI_VQE_CONFIG_S stAiVqeConfig;
        memset(&stAiVqeConfig, 0, sizeof(AI_VQE_CONFIG_S));
        
        stAiVqeConfig.s32WorkSampleRate = ctx->s32SampleRate;
        stAiVqeConfig.s32FrameSample = ctx->s32SampleRate * 16 / 1000; // 16ms
        stAiVqeConfig.s64RefChannelType = 2;
        stAiVqeConfig.s64RecChannelType = 1;
        for (int i = 0; i < ctx->s32DeviceChannel; i++)
            stAiVqeConfig.s64ChannelLayoutType |= (1 << i);
        
        result = RK_MPI_AI_SetVqeAttr(ctx->s32DevId, ctx->s32ChnIndex, 0, 0, &stAiVqeConfig);
        if (result == RK_SUCCESS) {
            result = RK_MPI_AI_EnableVqe(ctx->s32DevId, ctx->s32ChnIndex);
            if (result == RK_SUCCESS) {
                printf("INFO: VQE enabled successfully");
            }
        }
    }
    
    result = RK_MPI_AI_EnableChn(ctx->s32DevId, ctx->s32ChnIndex);
    if (result != 0) {
        char log_msg[256];
        snprintf(log_msg, sizeof(log_msg), "ERROR: AI enable channel fail: 0x%x", result);
        printf(log_msg);
        return RK_FAILURE;
    }
    
    // 设置音量
    RK_MPI_AI_SetVolume(ctx->s32DevId, ctx->s32SetVolume);
    
    return RK_SUCCESS;
}

static void* recording_thread(void *ptr) {
    MY_RECORDER_CTX_S *ctx = (MY_RECORDER_CTX_S *)ptr;
    RK_S32 result = 0;
    RK_S32 s32MilliSec = -1;
    AUDIO_FRAME_S getFrame;
    FILE *fp = NULL;
    RK_S32 totalFrames = 0;
    RK_S32 targetFrames = ctx->s32RecordSeconds * ctx->s32SampleRate / ctx->s32FrameLength;
    RK_BOOL recording_in_progress = RK_FALSE;
    
    if (ctx->s32EnableGpioTrigger) {
        printf("INFO: GPIO trigger mode enabled, waiting for button press...\n");
        fflush(stdout);
        
        while (!gRecorderExit) {
            // 只要gGpioRecording为真且未在录音，立即进入录音
            if (!recording_in_progress && gGpioRecording) {
                recording_in_progress = RK_TRUE;
                totalFrames = 0;
                
                if (ctx->outputFilePath) {
                    fp = fopen(ctx->outputFilePath, "wb");
                    if (!fp) {
                        printf("ERROR: Cannot open output file: %s\n", ctx->outputFilePath);
                        break;
                    }
                    printf("INFO: Started recording to: %s\n", ctx->outputFilePath);
                    fflush(stdout);
                }
            }
            // 如果正在录音且gGpioRecording为真，持续录音
            if (recording_in_progress && gGpioRecording) {
                result = RK_MPI_AI_GetFrame(ctx->s32DevId, ctx->s32ChnIndex, &getFrame, NULL, s32MilliSec);
                if (result == 0) {
                    void* data = RK_MPI_MB_Handle2VirAddr(getFrame.pMbBlk);
                    int len = getFrame.u32Len;
                    
                    if (fp && data && len > 0) {
                        fwrite(data, 1, len, fp);
                        totalFrames++;
                        if (totalFrames % 50 == 0) {
                            printf("Recording... %d seconds\r", totalFrames * ctx->s32FrameLength / ctx->s32SampleRate);
                            fflush(stdout);
                        }
                    }
                    RK_MPI_AI_ReleaseFrame(ctx->s32DevId, ctx->s32ChnIndex, &getFrame, NULL);
                    if (len <= 0) {
                        printf("INFO: Recording ended (no more data)\n");
                        break;
                    }
                } else {
                    if (!gRecorderExit) {
                        printf("ERROR: Failed to get audio frame: 0x%x\n", result);
                    }
                    break;
                }
            }
            // 如果正在录音但gGpioRecording变为假，停止录音并上传
            if (recording_in_progress && !gGpioRecording) {
                recording_in_progress = RK_FALSE;
                if (fp) {
                    fclose(fp);
                    fp = NULL;
                    printf("\nINFO: Recording completed (%d frames, %d seconds)\n", 
                           totalFrames, totalFrames * ctx->s32FrameLength / ctx->s32SampleRate);
                    printf("INFO: Recording saved to: %s\n", ctx->outputFilePath);
                    fflush(stdout);
                    
                                         // 如果启用了上传功能，先释放录音设备，然后上传到服务器
                     if (ctx->s32EnableUpload) {
                         printf("INFO: Releasing audio device before upload...\n");
                         fflush(stdout);
                         
                         // 释放录音设备资源（与原逻辑保持一致）
                         if (ctx->s32VqeEnable) {
                             RK_MPI_AI_DisableVqe(ctx->s32DevId, ctx->s32ChnIndex);
                         }
                         RK_MPI_AI_DisableChn(ctx->s32DevId, ctx->s32ChnIndex);
                         RK_MPI_AI_Disable(ctx->s32DevId);
                         
                         printf("INFO: Audio device released, starting upload...\n");
                         fflush(stdout);
                         upload_audio_to_socket_server(ctx);
                         
                         // 上传完成后重新初始化录音设备，为下次录音做准备
                         printf("INFO: Re-initializing audio device for next recording...\n");
                         fflush(stdout);
                         
                         // 重新设置音频设备
                         if (setup_audio_device(ctx) != RK_SUCCESS) {
                             printf("ERROR: Failed to re-setup audio device\n");
                             fflush(stdout);
                             break;
                         }
                         
                         // 重新自动配置音频参数
                         auto_configure_audio(ctx);
                         
                         // 重新设置音频通道
                         if (setup_audio_channel(ctx) != RK_SUCCESS) {
                             printf("ERROR: Failed to re-setup audio channel\n");
                             fflush(stdout);
                             break;
                         }
                         
                                                  printf("INFO: Audio device re-initialized successfully\n");
                         fflush(stdout);
                     } else {
                         // 即使没有启用上传，在GPIO模式下也需要重新初始化设备为下次录音做准备
                         printf("INFO: Preparing for next recording session...\n");
                         fflush(stdout);
                         
                         // 释放当前录音设备
                         if (ctx->s32VqeEnable) {
                             RK_MPI_AI_DisableVqe(ctx->s32DevId, ctx->s32ChnIndex);
                         }
                         RK_MPI_AI_DisableChn(ctx->s32DevId, ctx->s32ChnIndex);
                         RK_MPI_AI_Disable(ctx->s32DevId);
                         
                         // 重新初始化录音设备
                         if (setup_audio_device(ctx) != RK_SUCCESS) {
                             printf("ERROR: Failed to re-setup audio device\n");
                             fflush(stdout);
                             break;
                         }
                         
                         auto_configure_audio(ctx);
                         
                         if (setup_audio_channel(ctx) != RK_SUCCESS) {
                             printf("ERROR: Failed to re-setup audio channel\n");
                             fflush(stdout);
                             break;
                         }
                         
                         printf("INFO: Audio device ready for next recording\n");
                         fflush(stdout);
                     }
                 }
                 
                 // 短暂等待避免CPU占用过高
                 usleep(10000); // 10ms
            }
            
            // 短暂等待
            usleep(1000); // 1ms
        }
        
    } else {
        // 原有的时间触发模式
        if (ctx->outputFilePath) {
            fp = fopen(ctx->outputFilePath, "wb");
            if (!fp) {
                char log_msg[512];
                snprintf(log_msg, sizeof(log_msg), "ERROR: Cannot open output file: %s", ctx->outputFilePath);
                printf(log_msg);
                return NULL;
            }
            char log_msg[512];
            snprintf(log_msg, sizeof(log_msg), "INFO: Recording to file: %s", ctx->outputFilePath);
            printf(log_msg);
        }
        
        printf("INFO: Recording started... Press Ctrl+C to stop \n");
        printf("[bayes11]......INFO gRecorderExit:%d s32RecordSeconds:%d totalFrames:%d targetFrames:%d \n",gRecorderExit,
            ctx->s32RecordSeconds,totalFrames,targetFrames);
        while (!gRecorderExit && (ctx->s32RecordSeconds <= 0 || totalFrames < targetFrames)) {
            result = RK_MPI_AI_GetFrame(ctx->s32DevId, ctx->s32ChnIndex, &getFrame, NULL, s32MilliSec);
            printf("[bayes22]......INFO gRecorderExit:%d result:%d totalFrames:%d targetFrames:%d \n",gRecorderExit,
            result,totalFrames,targetFrames);
            if (result == 0) {
                void* data = RK_MPI_MB_Handle2VirAddr(getFrame.pMbBlk);
                int len = getFrame.u32Len;
                
                if (fp && data && len > 0) {
                    fwrite(data, 1, len, fp);
                    totalFrames++;
                    
                    if (totalFrames % 50 == 0) {
                        printf("Recording... %d seconds\r", totalFrames * ctx->s32FrameLength / ctx->s32SampleRate);
                        fflush(stdout);
                    }
                }
                
                RK_MPI_AI_ReleaseFrame(ctx->s32DevId, ctx->s32ChnIndex, &getFrame, NULL);
                
                if (len <= 0) {
                    printf("INFO: Recording ended (no more data) \n");
                    break;
                }
            } else {
                if (!gRecorderExit) {
                    char log_msg[256];
                    snprintf(log_msg, sizeof(log_msg), "ERROR: Failed to get audio frame: 0x%x", result);
                    printf(log_msg);
                }
                break;
            }
        }
        
        if (fp) {
            fclose(fp);
            char log_msg[512];
            snprintf(log_msg, sizeof(log_msg), "INFO: Recording saved to: %s", ctx->outputFilePath);
            printf(log_msg);
            
            // 如果启用了上传功能，先释放录音设备，然后上传到服务器
            if (ctx->s32EnableUpload) {
                printf("INFO: Releasing audio device before upload... \n");
                
                // 释放录音设备资源
                if (ctx->s32VqeEnable) {
                    RK_MPI_AI_DisableVqe(ctx->s32DevId, ctx->s32ChnIndex);
                }
                RK_MPI_AI_DisableChn(ctx->s32DevId, ctx->s32ChnIndex);
                RK_MPI_AI_Disable(ctx->s32DevId);
                
                printf("INFO: Audio device released, starting upload...");
                upload_audio_to_socket_server(ctx);
            }
        }
    }

    printf("INFO: Recording completed!");
    gRecorderExit = RK_TRUE;

    pthread_exit(NULL);
    return NULL;
}

static RK_S32 cleanup_audio(MY_RECORDER_CTX_S *ctx) {
    RK_S32 result;
    char log_msg[256];
    
    // 如果启用了上传功能，设备可能已经在录音线程中释放了
    // 在GPIO模式下也是如此，因为每次录音后都会释放设备
    if (ctx->s32EnableUpload || ctx->s32EnableGpioTrigger) {
        printf("INFO: Audio device already released in recording thread");
        return RK_SUCCESS;
    }
    
    if (ctx->s32VqeEnable) {
        RK_MPI_AI_DisableVqe(ctx->s32DevId, ctx->s32ChnIndex);
    }
    
    result = RK_MPI_AI_DisableChn(ctx->s32DevId, ctx->s32ChnIndex);
    if (result != 0) {
        snprintf(log_msg, sizeof(log_msg), "ERROR: AI disable channel fail: 0x%X", result);
        printf(log_msg);
    }
    
    result = RK_MPI_AI_Disable(ctx->s32DevId);
    if (result != 0) {
        snprintf(log_msg, sizeof(log_msg), "ERROR: AI disable fail: 0x%X", result);
        printf(log_msg);
    }
    
    return RK_SUCCESS;
}

static void show_usage() {
    printf("Usage: my_audio_recorder [options]\n");
    printf("Options:\n");
    printf("  -o, --output <file>     Output PCM file path (default: /tmp/my_recording.pcm)\n");
    printf("  -t, --time <seconds>    Recording duration in seconds (default: 10, 0=infinite)\n");
    printf("  -r, --rate <rate>       Sample rate (default: 16000)\n");
    printf("  -c, --channels <num>    Number of channels (default: 2)\n");
    printf("  -b, --bits <bits>       Bit width (default: 16)\n");
    printf("  -s, --card <name>       Sound card name (default: hw:0,0)\n");
    printf("  -v, --volume <0-100>    Recording volume (default: 100)\n");
    printf("      --no-auto-config    Disable auto audio configuration\n");
    printf("      --enable-vqe        Enable VQE (Voice Quality Enhancement)\n");
    printf("      --enable-upload     Enable Socket upload to server\n");
    printf("      --server <host>     Server host (default: 127.0.0.1)\n");
    printf("      --port <port>       Server port (default: 7861)\n");
    printf("      --format <fmt>      Response format: json/stream (default: json)\n");
    printf("      --enable-streaming  Enable streaming audio playback (for stream format)\n");
    printf("      --playback-rate <r> Playback sample rate (default: 22050)\n");
    printf("      --playback-channels <c> Playback channels (default: 1)\n");
    printf("      --test-play <file>  Test audio playback with PCM file\n");
    printf("      --enable-timing     Enable detailed timing statistics\n");
    printf("      --enable-gpio       Enable GPIO trigger recording\n");
    printf("      --gpio-path <path>  GPIO debug file path (default: /sys/kernel/debug/gpio)\n");
    printf("      --gpio-number <n>   GPIO number to monitor (default: 1)\n");
    printf("      --gpio-poll <ms>    GPIO polling interval in ms (default: 50)\n");
    printf("      --help              Show this help\n");
    printf("\n");
    printf("Examples:\n");
    printf("  my_audio_recorder                              # Record 10s to /tmp/my_recording.pcm\n");
    printf("  my_audio_recorder -o /data/test.pcm -t 30     # Record 30s to /data/test.pcm\n");
    printf("  my_audio_recorder -t 0                        # Record indefinitely (Ctrl+C to stop)\n");
    printf("  my_audio_recorder --enable-upload             # Record and upload to server\n");
    printf("  my_audio_recorder --enable-upload --server 192.168.1.100 --port 7861 # Custom server\n");
    printf("  my_audio_recorder --enable-upload --format stream --enable-streaming # Stream audio playback\n");
    printf("  my_audio_recorder --test-play /tmp/audio.pcm # Test playback PCM file\n");
    printf("  my_audio_recorder --test-play /tmp/audio.pcm --playback-rate 22050 # Test with specific rate\n");
    printf("  my_audio_recorder --enable-upload --enable-timing # Record and upload with timing analysis\n");
    printf("  my_audio_recorder --enable-gpio --enable-upload # GPIO trigger recording with upload\n");
    printf("  my_audio_recorder --enable-gpio --gpio-number 1 --gpio-poll 20 # Custom GPIO settings\n");
}

// 时间统计函数实现
static void init_timing_stats(void) {
    memset(&g_timing_stats, 0, sizeof(TIMING_STATS_S));
    g_timing_stats.timing_enabled = 1;
    printf("📊 [TIMING] 时间统计系统已初始化\n");
    fflush(stdout);
}

static void record_timestamp(struct timeval *tv, const char *event_name) {
    if (!g_timing_stats.timing_enabled) return;
    
    gettimeofday(tv, NULL);
    
    // 输出时间戳（精确到毫秒）
    struct tm *tm_info = localtime(&tv->tv_sec);
    char time_str[100];
    strftime(time_str, sizeof(time_str), "%H:%M:%S", tm_info);
    
    printf("⏰ [TIMING] %s: %s.%03ld\n", event_name, time_str, tv->tv_usec / 1000);
    fflush(stdout);
}

static long calculate_time_diff_ms(struct timeval *start, struct timeval *end) {
    if (!start->tv_sec || !end->tv_sec) return -1;
    
    long seconds = end->tv_sec - start->tv_sec;
    long microseconds = end->tv_usec - start->tv_usec;
    
    return (seconds * 1000) + (microseconds / 1000);
}

static void print_stage_timing(const char *stage_name, struct timeval *start, struct timeval *end) {
    long diff_ms = calculate_time_diff_ms(start, end);
    if (diff_ms >= 0) {
        printf("📈 [TIMING] %s: %ld ms\n", stage_name, diff_ms);
    } else {
        printf("⚠️ [TIMING] %s: 时间数据无效\n", stage_name);
    }
    fflush(stdout);
}

static void print_timing_report(void) {
    if (!g_timing_stats.timing_enabled) return;
    
    printf("\n");
    printf("================================================================\n");
    printf("📊 详细时间分析报告\n");
    printf("================================================================\n");
    
    // 各阶段耗时分析
    printf("\n🔍 各阶段详细耗时:\n");
    printf("----------------------------------------------------------------\n");
    
    // 1. 配置阶段
    if (g_timing_stats.config_sent_time.tv_sec > 0) {
        printf("1️⃣ 配置发送阶段:\n");
        struct tm *tm_info = localtime(&g_timing_stats.config_sent_time.tv_sec);
        char time_str[100];
        strftime(time_str, sizeof(time_str), "%H:%M:%S", tm_info);
        printf("   配置发送时间: %s.%03ld\n", time_str, g_timing_stats.config_sent_time.tv_usec / 1000);
    }
    
    // 2. 语音传输阶段
    if (g_timing_stats.voice_start_time.tv_sec > 0) {
        printf("\n2️⃣ 语音传输阶段:\n");
        print_stage_timing("   语音开始到第一个数据包", &g_timing_stats.voice_start_time, &g_timing_stats.voice_data_first_time);
        print_stage_timing("   语音传输总时长", &g_timing_stats.voice_start_time, &g_timing_stats.voice_end_time);
        printf("   发送数据包数量: %d 个\n", g_timing_stats.voice_data_packets);
        printf("   发送数据总量: %ld 字节\n", g_timing_stats.total_voice_bytes);
        if (g_timing_stats.voice_data_packets > 0) {
            printf("   平均包大小: %ld 字节\n", g_timing_stats.total_voice_bytes / g_timing_stats.voice_data_packets);
        }
    }
    
    // 3. AI处理阶段
    printf("\n3️⃣ AI处理阶段:\n");
    print_stage_timing("   语音发送完成到AI开始响应", &g_timing_stats.voice_end_time, &g_timing_stats.ai_start_time);
    
    // 4. 音频接收阶段
    printf("\n4️⃣ 音频接收阶段:\n");
    print_stage_timing("   AI开始到音频开始", &g_timing_stats.ai_start_time, &g_timing_stats.audio_start_time);
    print_stage_timing("   音频开始到第一个音频数据", &g_timing_stats.audio_start_time, &g_timing_stats.audio_first_data_time);
    
    // 5. 音频播放阶段
    printf("\n5️⃣ 音频播放阶段:\n");
    print_stage_timing("   音频开始到播放设备就绪", &g_timing_stats.audio_start_time, &g_timing_stats.audio_setup_complete_time);
    print_stage_timing("   播放设备就绪到第一次播放", &g_timing_stats.audio_setup_complete_time, &g_timing_stats.first_audio_play_time);
    printf("   接收音频包数量: %d 个\n", g_timing_stats.audio_data_packets);
    printf("   接收音频总量: %ld 字节\n", g_timing_stats.total_audio_bytes);
    printf("   已播放音频段数: %d 个\n", g_timing_stats.audio_segments_played);
    
    // 6. 总体性能指标
    printf("\n📊 关键性能指标:\n");
    printf("----------------------------------------------------------------\n");
    print_stage_timing("🚀 语音开始发送到AI开始响应 (总延迟)", &g_timing_stats.voice_start_time, &g_timing_stats.ai_start_time);
    print_stage_timing("🎵 语音开始发送到第一次音频播放 (用户感知延迟)", &g_timing_stats.voice_start_time, &g_timing_stats.first_audio_play_time);
    print_stage_timing("⚡ AI开始响应到第一次音频播放 (音频延迟)", &g_timing_stats.ai_start_time, &g_timing_stats.first_audio_play_time);
    
    // 7. 网络传输效率
    if (g_timing_stats.voice_start_time.tv_sec > 0 && g_timing_stats.voice_end_time.tv_sec > 0 && g_timing_stats.total_voice_bytes > 0) {
        long voice_transmission_ms = calculate_time_diff_ms(&g_timing_stats.voice_start_time, &g_timing_stats.voice_end_time);
        if (voice_transmission_ms > 0) {
            long voice_throughput = g_timing_stats.total_voice_bytes * 1000 / voice_transmission_ms; // bytes/sec
            printf("📡 语音上传速度: %ld 字节/秒 (%.2f KB/s)\n", voice_throughput, voice_throughput / 1024.0);
        }
    }
    
    if (g_timing_stats.audio_start_time.tv_sec > 0 && g_timing_stats.ai_end_time.tv_sec > 0 && g_timing_stats.total_audio_bytes > 0) {
        long audio_reception_ms = calculate_time_diff_ms(&g_timing_stats.audio_start_time, &g_timing_stats.ai_end_time);
        if (audio_reception_ms > 0) {
            long audio_throughput = g_timing_stats.total_audio_bytes * 1000 / audio_reception_ms; // bytes/sec
            printf("🔊 音频下载速度: %ld 字节/秒 (%.2f KB/s)\n", audio_throughput, audio_throughput / 1024.0);
        }
    }
    
    printf("\n================================================================\n");
    printf("📊 时间分析报告完成\n");
    printf("================================================================\n\n");
    fflush(stdout);
}

// 音频播放状态管理函数实现

// 设置音频播放状态
static void set_audio_playing_state(RK_BOOL playing) {
    pthread_mutex_lock(&gAudioStateMutex);
    gAudioPlaying = playing;
    if (playing) {
        gAudioInterrupted = RK_FALSE;  // 开始播放时清除中断标志
    }
    pthread_mutex_unlock(&gAudioStateMutex);
}

// 获取音频播放状态
static RK_BOOL get_audio_playing_state(void) {
    RK_BOOL playing;
    pthread_mutex_lock(&gAudioStateMutex);
    playing = gAudioPlaying;
    pthread_mutex_unlock(&gAudioStateMutex);
    return playing;
}

// 检查音频是否被中断
static RK_BOOL is_audio_interrupted(void) {
    RK_BOOL interrupted;
    pthread_mutex_lock(&gAudioStateMutex);
    interrupted = gAudioInterrupted;
    pthread_mutex_unlock(&gAudioStateMutex);
    return interrupted;
}

// 中断音频播放
static RK_S32 interrupt_audio_playback(void) {
    pthread_mutex_lock(&gAudioStateMutex);
    
    if (gAudioPlaying) {
        printf("🔇 检测到按钮按下，正在中断音频播放...\n");
        fflush(stdout);
        
        // 立即停止音频播放
        if (g_stPlaybackCtx.bInitialized) {
            // 强制清理播放设备，不等待播放完成
            RK_MPI_AO_DisableChn(g_stPlaybackCtx.aoDevId, g_stPlaybackCtx.aoChn);
            RK_MPI_AO_Disable(g_stPlaybackCtx.aoDevId);
            g_stPlaybackCtx.bInitialized = RK_FALSE;
            
            printf("✅ 音频播放设备已强制关闭\n");
            fflush(stdout);
        }
        
        gAudioPlaying = RK_FALSE;
        gAudioInterrupted = RK_TRUE;  // 设置中断标志
        
        pthread_mutex_unlock(&gAudioStateMutex);
        return RK_SUCCESS;
    }
    
    pthread_mutex_unlock(&gAudioStateMutex);
    return RK_FAILURE; // 没有正在播放的音频
}

// GPIO触发相关函数实现

// 读取GPIO状态函数
static RK_S32 read_gpio_state(const char *gpio_debug_path, RK_S32 gpio_number) {
    FILE *fp;
    char line[256];
    char gpio_name[32];
    char state[16];
    
    fp = fopen(gpio_debug_path, "r");
    if (!fp) {
        printf("ERROR: Cannot open GPIO debug file: %s\n", gpio_debug_path);
        return -1;
    }
    
    // 查找对应的GPIO行
    snprintf(gpio_name, sizeof(gpio_name), "gpio-%d", gpio_number);
    
    while (fgets(line, sizeof(line), fp)) {
        if (strstr(line, gpio_name)) {
            // 解析GPIO状态 (查找 " in " 或 " out " 后面的状态)
            char *pos = strstr(line, " in ");
            if (!pos) {
                pos = strstr(line, " out ");
            }
            
            if (pos) {
                // 移动到状态位置
                pos += 4; // 跳过 " in " 或 " out "
                while (*pos == ' ') pos++; // 跳过空格
                
                sscanf(pos, "%s", state);
                fclose(fp);
                
                if (strcmp(state, "hi") == 0) {
                    return 1; // 高电平
                } else if (strcmp(state, "lo") == 0) {
                    return 0; // 低电平
                }
            }
            break;
        }
    }
    
    fclose(fp);
    return -1; // 未找到或解析失败
}

// 等待GPIO按下 (lo -> hi)
static RK_S32 wait_for_gpio_press(MY_RECORDER_CTX_S *ctx) {
    RK_S32 current_state, prev_state = -1;
    
    printf("INFO: Waiting for GPIO-%d press (lo -> hi)...\n", ctx->s32GpioNumber);
    fflush(stdout);
    
    while (!gRecorderExit) {
        current_state = read_gpio_state(ctx->gpioDebugPath, ctx->s32GpioNumber);
        
        if (current_state < 0) {
            printf("ERROR: Failed to read GPIO state\n");
            return RK_FAILURE;
        }
        
        // 检测从低到高的变化
        if (prev_state == 0 && current_state == 1) {
            printf("INFO: GPIO-%d pressed!\n", ctx->s32GpioNumber);
            fflush(stdout);
            
            // 检查是否有音频正在播放或者AI响应在进行中，如果有则立即中断
            RK_BOOL need_interrupt = get_audio_playing_state() || gAIResponseActive;
            if (need_interrupt) {
                if (get_audio_playing_state()) {
                    printf("INFO: Interrupting current audio playback...\n");
                    fflush(stdout);
                    interrupt_audio_playback();
                    usleep(100000); // 100ms
                }
                gInterruptAIResponse = RK_TRUE; // 通知AI响应线程中断
            }
            // 抢话时若未在录音则进入录音
            if (!gGpioRecording) {
                gGpioRecording = RK_TRUE;
                printf("INFO: [抢话] 进入录音模式\n");
            } else {
                printf("INFO: [抢话] 已在录音中，忽略重复触发\n");
            }
            printf("INFO: Starting recording...\n");
            fflush(stdout);
            gGpioPressed = RK_TRUE;
            return RK_SUCCESS;
        }
        
        prev_state = current_state;
        usleep(ctx->s32GpioPollInterval * 1000); // 转换为微秒
    }
    
    return RK_FAILURE;
}

// 等待GPIO松开 (hi -> lo)
static RK_S32 wait_for_gpio_release(MY_RECORDER_CTX_S *ctx) {
    RK_S32 current_state, prev_state = -1;
    
    while (!gRecorderExit && gGpioPressed) {
        current_state = read_gpio_state(ctx->gpioDebugPath, ctx->s32GpioNumber);
        
        if (current_state < 0) {
            printf("ERROR: Failed to read GPIO state\n");
            return RK_FAILURE;
        }
        
        // 检测从高到低的变化
        if (prev_state == 1 && current_state == 0) {
            printf("INFO: GPIO-%d released! Stopping recording...\n", ctx->s32GpioNumber);
            fflush(stdout);
            gGpioPressed = RK_FALSE;
            return RK_SUCCESS;
        }
        
        prev_state = current_state;
        usleep(ctx->s32GpioPollInterval * 1000); // 转换为微秒
    }
    
    return RK_FAILURE;
}

// GPIO监控线程 (用于持续监控GPIO状态变化)
static void* gpio_monitor_thread(void *ptr) {
    MY_RECORDER_CTX_S *ctx = (MY_RECORDER_CTX_S *)ptr;
    
    printf("INFO: GPIO monitor thread started for GPIO-%d\n", ctx->s32GpioNumber);
    fflush(stdout);
    
    while (!gRecorderExit) {
        // 等待按钮按下
        if (wait_for_gpio_press(ctx) == RK_SUCCESS) {
            // 等待按钮松开
            if (wait_for_gpio_release(ctx) == RK_SUCCESS) {
                gGpioRecording = RK_FALSE;
            }
        }
        
        usleep(10000); // 10ms
    }
    
    printf("INFO: GPIO monitor thread exiting\n");
    fflush(stdout);
    pthread_exit(NULL);
    return NULL;
}

int main(int argc, const char **argv) {
    MY_RECORDER_CTX_S *ctx;
    pthread_t recordingThread;
    RK_S32 result = RK_SUCCESS;
    
    ctx = (MY_RECORDER_CTX_S *)malloc(sizeof(MY_RECORDER_CTX_S));
    memset(ctx, 0, sizeof(MY_RECORDER_CTX_S));
    
    // 默认参数
    ctx->outputFilePath = "/tmp/my_recording.pcm";
    ctx->s32RecordSeconds = 10;
    ctx->s32DeviceSampleRate = 16000;
    ctx->s32SampleRate = 16000;
    ctx->s32DeviceChannel = 2;  // 硬件双通道输入
    ctx->s32Channel = 1;        // 输出单通道（避免采样率翻倍问题）
    ctx->s32BitWidth = 16;
    ctx->s32DevId = 0;
    ctx->s32ChnIndex = 0;
    ctx->s32FrameNumber = 4;
    ctx->s32FrameLength = 1024;
    ctx->chCardName = "hw:0,0";
    ctx->s32AutoConfig = 1;
    ctx->s32VqeEnable = 0;
    ctx->s32SetVolume = 100;
    ctx->s32EnableUpload = 0;                           // 默认不启用上传
    ctx->serverHost = "127.0.0.1";                       // 默认服务器地址
    ctx->serverPort = 7860;                             // 默认服务器端口（与SocketServer一致）
    ctx->responseFormat = "json";                         // 默认响应格式
    ctx->s32EnableStreaming = 0;                            // 默认不启用流式播放
    ctx->s32PlaybackSampleRate = 8000;                     // 默认播放采样率（标准TTS采样率）
    ctx->s32PlaybackChannels = 1;                             // 默认播放声道数（单声道）
    ctx->s32PlaybackBitWidth = 16;                             // 默认播放位宽
    ctx->s32EnableTiming = 0;                               // 默认不启用详细时间统计
    
    // GPIO触发相关默认值
    ctx->s32EnableGpioTrigger = 0;                          // 默认不启用GPIO触发
    ctx->gpioDebugPath = "/sys/kernel/debug/gpio";          // 默认GPIO调试文件路径  
    ctx->s32GpioNumber = 1;                                 // 默认GPIO编号 (gpio-1)
    ctx->s32GpioPollInterval = 50;                          // 默认GPIO检查间隔50ms
    
    RK_S32 s32DisableAutoConfig = 0;  // 临时变量处理no-auto-config逻辑
    
    struct argparse_option options[] = {
        OPT_HELP(),
        OPT_STRING('o', "output", &(ctx->outputFilePath),
                   "output PCM file path", NULL, 0, 0),
        OPT_INTEGER('t', "time", &(ctx->s32RecordSeconds),
                    "recording duration in seconds (0=infinite)", NULL, 0, 0),
        OPT_INTEGER('r', "rate", &(ctx->s32SampleRate),
                    "sample rate", NULL, 0, 0),
        OPT_INTEGER('c', "channels", &(ctx->s32Channel),
                    "number of channels", NULL, 0, 0),
        OPT_INTEGER('b', "bits", &(ctx->s32BitWidth),
                    "bit width", NULL, 0, 0),
        OPT_STRING('s', "card", &(ctx->chCardName),
                   "sound card name", NULL, 0, 0),
        OPT_INTEGER('v', "volume", &(ctx->s32SetVolume),
                    "recording volume (0-100)", NULL, 0, 0),
        OPT_BOOLEAN('\0', "no-auto-config", &s32DisableAutoConfig,
                    "disable auto audio configuration", NULL, 0, 0),
        OPT_BOOLEAN('\0', "enable-vqe", &(ctx->s32VqeEnable),
                    "enable VQE", NULL, 0, 0),
        OPT_BOOLEAN('\0', "enable-upload", &(ctx->s32EnableUpload),
                    "enable Socket upload to server", NULL, 0, 0),
        OPT_STRING('\0', "server", &(ctx->serverHost),
                   "server host", NULL, 0, 0),
        OPT_INTEGER('\0', "port", &(ctx->serverPort),
                    "server port", NULL, 0, 0),
        OPT_STRING('\0', "format", &(ctx->responseFormat),
                   "response format (json/stream)", NULL, 0, 0),
        OPT_BOOLEAN('\0', "enable-streaming", &(ctx->s32EnableStreaming),
                    "enable streaming audio playback", NULL, 0, 0),
        OPT_INTEGER('\0', "playback-rate", &(ctx->s32PlaybackSampleRate),
                    "playback sample rate for streaming", NULL, 0, 0),
        OPT_INTEGER('\0', "playback-channels", &(ctx->s32PlaybackChannels),
                    "playback channels for streaming", NULL, 0, 0),
        OPT_STRING('\0', "test-play", &(ctx->testPlayFile),
                   "test audio playback with file (PCM format)", NULL, 0, 0),
        OPT_BOOLEAN('\0', "enable-timing", &(ctx->s32EnableTiming),
                    "enable detailed timing statistics", NULL, 0, 0),
        OPT_BOOLEAN('\0', "enable-gpio", &(ctx->s32EnableGpioTrigger),
                    "enable GPIO trigger recording", NULL, 0, 0),
        OPT_STRING('\0', "gpio-path", &(ctx->gpioDebugPath),
                   "GPIO debug file path", NULL, 0, 0),
        OPT_INTEGER('\0', "gpio-number", &(ctx->s32GpioNumber),
                    "GPIO number to monitor", NULL, 0, 0),
        OPT_INTEGER('\0', "gpio-poll", &(ctx->s32GpioPollInterval),
                    "GPIO polling interval in ms", NULL, 0, 0),
        OPT_END(),
    };
    
    struct argparse argparse;
    argparse_init(&argparse, options, NULL, 0);
    argparse_describe(&argparse, "\nSimple Audio Recorder with Auto Configuration",
                                 "\nRecords audio with optimized settings for clear voice capture.");
    
    argc = argparse_parse(&argparse, argc, argv);
    if (argc < 0) {
        result = 0;
        goto cleanup;
    }
    
    // 处理no-auto-config逻辑
    if (s32DisableAutoConfig) {
        ctx->s32AutoConfig = 0;  // 禁用自动配置
    }
    // 否则保持默认值1（启用自动配置）
    
    // 设置设备参数（保持兼容性）
    ctx->s32DeviceSampleRate = ctx->s32SampleRate;
    ctx->s32DeviceChannel = ctx->s32Channel;
    
    // 显示配置信息
    printf("=== Audio Recorder Configuration ===\n");
    printf("Program Version: v2.2 - Fixed Device Release (Build: %s %s)\n", __DATE__, __TIME__);
    printf("Output file: %s\n", ctx->outputFilePath);
    printf("Duration: %s\n", ctx->s32RecordSeconds > 0 ? 
           (char[]){sprintf((char[32]){0}, "%d seconds", ctx->s32RecordSeconds), 0} : "infinite");
    printf("Sample rate: %d Hz\n", ctx->s32SampleRate);
    printf("Device channels: %d (input)\n", ctx->s32DeviceChannel);
    printf("Output channels: %d\n", ctx->s32Channel);
    printf("Bit width: %d\n", ctx->s32BitWidth);
    printf("Sound card: %s\n", ctx->chCardName);
    printf("Volume: %d%%\n", ctx->s32SetVolume);
    printf("Auto config: %s\n", ctx->s32AutoConfig ? "enabled" : "disabled");
    printf("VQE: %s\n", ctx->s32VqeEnable ? "enabled" : "disabled");
    printf("Socket Upload: %s\n", ctx->s32EnableUpload ? "enabled" : "disabled");
    if (ctx->s32EnableUpload) {
        printf("Server host: %s\n", ctx->serverHost);
        printf("Server port: %d\n", ctx->serverPort);
        printf("Response format: %s\n", ctx->responseFormat);
        printf("Streaming playback: %s\n", ctx->s32EnableStreaming ? "enabled" : "disabled");
        if (ctx->s32EnableStreaming) {
            printf("Playback rate: %d Hz\n", ctx->s32PlaybackSampleRate);
            printf("Playback channels: %d\n", ctx->s32PlaybackChannels);
        }
    }
    printf("Expected data rate: %d bytes/sec\n", ctx->s32SampleRate * ctx->s32Channel * (ctx->s32BitWidth/8));
    if (ctx->testPlayFile) {
        printf("Test playback file: %s\n", ctx->testPlayFile);
    }
    printf("Timing analysis: %s\n", ctx->s32EnableTiming ? "enabled" : "disabled");
    printf("GPIO trigger: %s\n", ctx->s32EnableGpioTrigger ? "enabled" : "disabled");
    if (ctx->s32EnableGpioTrigger) {
        printf("GPIO path: %s\n", ctx->gpioDebugPath);
        printf("GPIO number: %d\n", ctx->s32GpioNumber);
        printf("GPIO poll interval: %d ms\n", ctx->s32GpioPollInterval);
    }
    printf("=====================================\n\n");
    
    // 如果指定了测试播放文件，只执行播放测试
    if (ctx->testPlayFile) {
        printf("🎵 进入音频播放测试模式\n");
        fflush(stdout);
        
        // 设置信号处理
        signal(SIGINT, sigterm_handler);
        
        // 禁用Rockchip的日志重定向
        setenv("rt_log_path", "/dev/null", 1);
        setenv("rt_log_size", "0", 1);
        setenv("rt_log_level", "6", 1);
        
        // 初始化系统
        RK_MPI_SYS_Init();
        
        // 执行音频播放测试
        result = play_audio_file(ctx, ctx->testPlayFile);
        
        // 清理并退出
        printf("\n🎵 音频播放测试完成，程序退出\n");
        fflush(stdout);
        goto cleanup;
    }
    
    // 设置信号处理
    signal(SIGINT, sigterm_handler);
    
    // 禁用Rockchip的日志重定向，避免与我们的printf冲突
    setenv("rt_log_path", "/dev/null", 1);
    setenv("rt_log_size", "0", 1);
    setenv("rt_log_level", "6", 1);  // 设置最高日志级别以减少输出
    
    // 初始化系统
    RK_MPI_SYS_Init();
    
    // 设置音频设备
    result = setup_audio_device(ctx);
    if (result != RK_SUCCESS) {
        printf("ERROR: Failed to setup audio device");
        goto cleanup;
    }
    
    // 自动配置音频参数（在设备初始化后）
    auto_configure_audio(ctx);
    
    // 设置音频通道
    result = setup_audio_channel(ctx);
    if (result != RK_SUCCESS) {
        printf("ERROR: Failed to setup audio channel");
        goto cleanup;
    }
    
    // 创建录音线程
    pthread_create(&recordingThread, NULL, recording_thread, (void *)ctx);
    
    
    // 如果启用GPIO触发，创建GPIO监控线程
    pthread_t gpioThread;
    if (ctx->s32EnableGpioTrigger) {
        printf("INFO: Starting GPIO monitor thread...\n");
        pthread_create(&gpioThread, NULL, gpio_monitor_thread, (void *)ctx);
    }
    
    // 等待录音完成
    pthread_join(recordingThread, NULL);
    
    // 如果启用了GPIO触发，等待GPIO线程完成
    if (ctx->s32EnableGpioTrigger) {
        printf("INFO: Waiting for GPIO monitor thread to complete...\n");
        pthread_join(gpioThread, NULL);
    }

    
cleanup:
    if (ctx) {
        cleanup_audio(ctx);
        free(ctx);
    }
    
    // 清理互斥锁
    pthread_mutex_destroy(&gAudioStateMutex);
    
    RK_MPI_SYS_Exit();
    return result;
} 