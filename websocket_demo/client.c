#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libwebsockets.h>
#include <cjson/cJSON.h>

#define MAX_PAYLOAD_SIZE 4096
#define MAX_FILENAME_SIZE 256

struct upload_session {
    struct lws *wsi;
    FILE *file;
    char filename[MAX_FILENAME_SIZE];
    size_t file_size;
    size_t bytes_sent;
    int upload_started;
    int upload_completed;
};

static struct upload_session session = {0};

// WebSocket协议回调函数
static int callback_upload_protocol(struct lws *wsi, 
                                   enum lws_callback_reasons reason,
                                   void *user, void *in, size_t len)
{
    switch (reason) {
        case LWS_CALLBACK_CLIENT_ESTABLISHED:
            printf("WebSocket连接已建立\n");
            session.wsi = wsi;
            // 发送上传开始请求
            lws_callback_on_writable(wsi);
            break;

        case LWS_CALLBACK_CLIENT_RECEIVE:
            printf("收到服务器消息: %.*s\n", (int)len, (char*)in);
            
            // 解析JSON响应
            cJSON *json = cJSON_Parse((char*)in);
            if (json) {
                cJSON *type = cJSON_GetObjectItem(json, "type");
                cJSON *status = cJSON_GetObjectItem(json, "status");
                
                if (type && cJSON_IsString(type)) {
                    if (strcmp(type->valuestring, "upload_start_response") == 0) {
                        if (status && cJSON_IsString(status) && 
                            strcmp(status->valuestring, "ready") == 0) {
                            printf("服务器准备就绪，开始上传文件\n");
                            session.upload_started = 1;
                            lws_callback_on_writable(wsi);
                        }
                    } else if (strcmp(type->valuestring, "upload_complete_response") == 0) {
                        printf("文件上传完成！\n");
                        session.upload_completed = 1;
                    }
                }
                cJSON_Delete(json);
            }
            break;

        case LWS_CALLBACK_CLIENT_WRITEABLE:
            if (!session.upload_started) {
                // 发送上传开始请求
                cJSON *json = cJSON_CreateObject();
                cJSON *type = cJSON_CreateString("upload_start");
                cJSON *filename = cJSON_CreateString(session.filename);
                cJSON *filesize = cJSON_CreateNumber(session.file_size);
                
                cJSON_AddItemToObject(json, "type", type);
                cJSON_AddItemToObject(json, "filename", filename);
                cJSON_AddItemToObject(json, "filesize", filesize);
                
                char *json_string = cJSON_Print(json);
                size_t json_len = strlen(json_string);
                
                unsigned char buf[LWS_PRE + MAX_PAYLOAD_SIZE];
                memcpy(&buf[LWS_PRE], json_string, json_len);
                
                int ret = lws_write(wsi, &buf[LWS_PRE], json_len, LWS_WRITE_TEXT);
                printf("发送上传开始请求: %s\n", json_string);
                
                free(json_string);
                cJSON_Delete(json);
                
                if (ret < 0) {
                    printf("发送消息失败\n");
                    return -1;
                }
            } else if (session.upload_started && !session.upload_completed) {
                // 发送文件数据
                unsigned char buf[LWS_PRE + MAX_PAYLOAD_SIZE];
                size_t chunk_size = MAX_PAYLOAD_SIZE - 100; // 预留空间给JSON头部
                size_t remaining = session.file_size - session.bytes_sent;
                size_t to_read = (remaining < chunk_size) ? remaining : chunk_size;
                
                if (to_read > 0) {
                    unsigned char *file_data = malloc(to_read);
                    size_t bytes_read = fread(file_data, 1, to_read, session.file);
                    
                    if (bytes_read > 0) {
                        // 创建包含二进制数据的JSON消息
                        cJSON *json = cJSON_CreateObject();
                        cJSON *type = cJSON_CreateString("file_chunk");
                        cJSON *offset = cJSON_CreateNumber(session.bytes_sent);
                        cJSON *size = cJSON_CreateNumber(bytes_read);
                        
                        cJSON_AddItemToObject(json, "type", type);
                        cJSON_AddItemToObject(json, "offset", offset);
                        cJSON_AddItemToObject(json, "size", size);
                        
                        char *json_string = cJSON_Print(json);
                        size_t json_len = strlen(json_string);
                        
                        // 组合JSON头部和二进制数据
                        memcpy(&buf[LWS_PRE], json_string, json_len);
                        memcpy(&buf[LWS_PRE + json_len], "\n", 1); // 分隔符
                        memcpy(&buf[LWS_PRE + json_len + 1], file_data, bytes_read);
                        
                        int total_len = json_len + 1 + bytes_read;
                        int ret = lws_write(wsi, &buf[LWS_PRE], total_len, LWS_WRITE_BINARY);
                        
                        session.bytes_sent += bytes_read;
                        printf("已发送: %zu/%zu 字节 (%.1f%%)\n", 
                               session.bytes_sent, session.file_size,
                               (float)session.bytes_sent / session.file_size * 100);
                        
                        free(json_string);
                        cJSON_Delete(json);
                        free(file_data);
                        
                        if (ret < 0) {
                            printf("发送文件数据失败\n");
                            return -1;
                        }
                        
                        // 继续发送下一块
                        if (session.bytes_sent < session.file_size) {
                            lws_callback_on_writable(wsi);
                        } else {
                            // 发送上传完成消息
                            cJSON *complete_json = cJSON_CreateObject();
                            cJSON *complete_type = cJSON_CreateString("upload_complete");
                            cJSON_AddItemToObject(complete_json, "type", complete_type);
                            
                            char *complete_string = cJSON_Print(complete_json);
                            size_t complete_len = strlen(complete_string);
                            
                            memcpy(&buf[LWS_PRE], complete_string, complete_len);
                            lws_write(wsi, &buf[LWS_PRE], complete_len, LWS_WRITE_TEXT);
                            
                            printf("发送上传完成消息\n");
                            
                            free(complete_string);
                            cJSON_Delete(complete_json);
                        }
                    }
                }
            }
            break;

        case LWS_CALLBACK_CLIENT_CONNECTION_ERROR:
            printf("WebSocket连接错误\n");
            session.wsi = NULL;
            break;

        case LWS_CALLBACK_CLOSED:
            printf("WebSocket连接已关闭\n");
            session.wsi = NULL;
            break;

        default:
            break;
    }

    return 0;
}

// WebSocket协议定义
static struct lws_protocols protocols[] = {
    {
        "upload-protocol",
        callback_upload_protocol,
        0,
        MAX_PAYLOAD_SIZE,
    },
    { NULL, NULL, 0, 0 } /* terminator */
};

// 初始化文件上传会话
int init_upload_session(const char *filename) {
    memset(&session, 0, sizeof(session));
    
    session.file = fopen(filename, "rb");
    if (!session.file) {
        printf("无法打开文件: %s\n", filename);
        return -1;
    }
    
    // 获取文件大小
    fseek(session.file, 0, SEEK_END);
    session.file_size = ftell(session.file);
    fseek(session.file, 0, SEEK_SET);
    
    // 提取文件名
    const char *basename = strrchr(filename, '/');
    basename = basename ? basename + 1 : filename;
    strncpy(session.filename, basename, MAX_FILENAME_SIZE - 1);
    
    printf("准备上传文件: %s (大小: %zu 字节)\n", session.filename, session.file_size);
    
    return 0;
}

// 清理上传会话
void cleanup_upload_session() {
    if (session.file) {
        fclose(session.file);
        session.file = NULL;
    }
}

int main(int argc, char *argv[]) {
    if (argc != 4) {
        printf("使用方法: %s <服务器地址> <端口> <文件路径>\n", argv[0]);
        printf("示例: %s localhost 8080 /path/to/file.jpg\n", argv[0]);
        return 1;
    }
    
    const char *server = argv[1];
    int port = atoi(argv[2]);
    const char *filename = argv[3];
    
    // 初始化上传会话
    if (init_upload_session(filename) < 0) {
        return 1;
    }
    
    // 创建WebSocket上下文
    struct lws_context_creation_info info;
    memset(&info, 0, sizeof(info));
    
    info.port = CONTEXT_PORT_NO_LISTEN;
    info.protocols = protocols;
    info.gid = -1;
    info.uid = -1;
    
    struct lws_context *context = lws_create_context(&info);
    if (!context) {
        printf("创建WebSocket上下文失败\n");
        cleanup_upload_session();
        return 1;
    }
    
    // 连接到服务器
    struct lws_client_connect_info ccinfo;
    memset(&ccinfo, 0, sizeof(ccinfo));
    
    ccinfo.context = context;
    ccinfo.address = server;
    ccinfo.port = port;
    ccinfo.path = "/upload";
    ccinfo.host = server;
    ccinfo.origin = server;
    ccinfo.protocol = protocols[0].name;
    
    struct lws *wsi = lws_client_connect_via_info(&ccinfo);
    if (!wsi) {
        printf("连接到WebSocket服务器失败\n");
        lws_context_destroy(context);
        cleanup_upload_session();
        return 1;
    }
    
    printf("正在连接到 ws://%s:%d/upload\n", server, port);
    
    // 事件循环
    int n = 0;
    while (n >= 0 && !session.upload_completed) {
        n = lws_service(context, 1000);
    }
    
    // 清理资源
    lws_context_destroy(context);
    cleanup_upload_session();
    
    printf("上传完成！\n");
    return 0;
}
