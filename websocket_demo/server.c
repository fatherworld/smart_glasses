#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libwebsockets.h>
#include <cjson/cJSON.h>

#define MAX_PAYLOAD_SIZE 4096
#define UPLOAD_DIR "./uploads/"

struct client_session {
    FILE *upload_file;
    char filename[256];
    size_t expected_size;
    size_t received_size;
    int upload_active;
};

// 确保上传目录存在
void ensure_upload_dir() {
    system("mkdir -p " UPLOAD_DIR);
}

// WebSocket服务器回调函数
static int callback_upload_server(struct lws *wsi,
                                 enum lws_callback_reasons reason,
                                 void *user, void *in, size_t len)
{
    struct client_session *session = (struct client_session *)user;
    
    switch (reason) {
        case LWS_CALLBACK_ESTABLISHED:
            printf("客户端连接建立\n");
            memset(session, 0, sizeof(struct client_session));
            break;

        case LWS_CALLBACK_RECEIVE:
            printf("收到数据，长度: %zu\n", len);
            
            if (lws_frame_is_binary(wsi)) {
                // 处理二进制数据（文件块）
                char *data = (char *)in;
                
                // 查找JSON头部和二进制数据的分隔符
                char *separator = memchr(data, '\n', len);
                if (separator) {
                    size_t json_len = separator - data;
                    char json_str[json_len + 1];
                    memcpy(json_str, data, json_len);
                    json_str[json_len] = '\0';
                    
                    // 解析JSON头部
                    cJSON *json = cJSON_Parse(json_str);
                    if (json) {
                        cJSON *type = cJSON_GetObjectItem(json, "type");
                        cJSON *offset = cJSON_GetObjectItem(json, "offset");
                        cJSON *size = cJSON_GetObjectItem(json, "size");
                        
                        if (type && cJSON_IsString(type) && 
                            strcmp(type->valuestring, "file_chunk") == 0) {
                            
                            size_t chunk_offset = offset ? (size_t)offset->valuedouble : 0;
                            size_t chunk_size = size ? (size_t)size->valuedouble : 0;
                            
                            // 获取二进制数据
                            unsigned char *binary_data = (unsigned char *)(separator + 1);
                            size_t binary_len = len - json_len - 1;
                            
                            if (session->upload_file && binary_len == chunk_size) {
                                size_t written = fwrite(binary_data, 1, binary_len, session->upload_file);
                                session->received_size += written;
                                
                                printf("接收文件块: offset=%zu, size=%zu, 总进度=%zu/%zu (%.1f%%)\n",
                                       chunk_offset, chunk_size,
                                       session->received_size, session->expected_size,
                                       (float)session->received_size / session->expected_size * 100);
                                
                                fflush(session->upload_file);
                            }
                        }
                        cJSON_Delete(json);
                    }
                }
            } else {
                // 处理文本消息（JSON控制消息）
                char *message = malloc(len + 1);
                memcpy(message, in, len);
                message[len] = '\0';
                
                printf("收到JSON消息: %s\n", message);
                
                cJSON *json = cJSON_Parse(message);
                if (json) {
                    cJSON *type = cJSON_GetObjectItem(json, "type");
                    
                    if (type && cJSON_IsString(type)) {
                        if (strcmp(type->valuestring, "upload_start") == 0) {
                            // 处理上传开始请求
                            cJSON *filename = cJSON_GetObjectItem(json, "filename");
                            cJSON *filesize = cJSON_GetObjectItem(json, "filesize");
                            
                            if (filename && cJSON_IsString(filename) &&
                                filesize && cJSON_IsNumber(filesize)) {
                                
                                strncpy(session->filename, filename->valuestring, sizeof(session->filename) - 1);
                                session->expected_size = (size_t)filesize->valuedouble;
                                session->received_size = 0;
                                
                                // 创建上传文件
                                char filepath[512];
                                snprintf(filepath, sizeof(filepath), "%s%s", UPLOAD_DIR, session->filename);
                                
                                session->upload_file = fopen(filepath, "wb");
                                if (session->upload_file) {
                                    session->upload_active = 1;
                                    printf("开始接收文件: %s (大小: %zu 字节)\n", 
                                           session->filename, session->expected_size);
                                    
                                    // 发送准备就绪响应
                                    cJSON *response = cJSON_CreateObject();
                                    cJSON *resp_type = cJSON_CreateString("upload_start_response");
                                    cJSON *status = cJSON_CreateString("ready");
                                    
                                    cJSON_AddItemToObject(response, "type", resp_type);
                                    cJSON_AddItemToObject(response, "status", status);
                                    
                                    lws_callback_on_writable(wsi);
                                } else {
                                    printf("无法创建上传文件: %s\n", filepath);
                                }
                            }
                        } else if (strcmp(type->valuestring, "upload_complete") == 0) {
                            // 处理上传完成消息
                            if (session->upload_file) {
                                fclose(session->upload_file);
                                session->upload_file = NULL;
                                session->upload_active = 0;
                                
                                printf("文件上传完成: %s (接收 %zu 字节)\n", 
                                       session->filename, session->received_size);
                                
                                // 发送上传完成响应
                                cJSON *response = cJSON_CreateObject();
                                cJSON *resp_type = cJSON_CreateString("upload_complete_response");
                                cJSON *status = cJSON_CreateString("success");
                                
                                cJSON_AddItemToObject(response, "type", resp_type);
                                cJSON_AddItemToObject(response, "status", status);
                                
                                lws_callback_on_writable(wsi);
                            }
                        }
                    }
                    cJSON_Delete(json);
                }
                free(message);
            }
            break;

        case LWS_CALLBACK_SERVER_WRITEABLE:
            // 发送响应消息给客户端
            if (session->upload_active) {
                cJSON *response = cJSON_CreateObject();
                cJSON *type = cJSON_CreateString("upload_start_response");
                cJSON *status = cJSON_CreateString("ready");
                
                cJSON_AddItemToObject(response, "type", type);
                cJSON_AddItemToObject(response, "status", status);
                
                char *response_str = cJSON_Print(response);
                size_t response_len = strlen(response_str);
                
                unsigned char buf[LWS_PRE + MAX_PAYLOAD_SIZE];
                memcpy(&buf[LWS_PRE], response_str, response_len);
                
                lws_write(wsi, &buf[LWS_PRE], response_len, LWS_WRITE_TEXT);
                printf("发送响应: %s\n", response_str);
                
                free(response_str);
                cJSON_Delete(response);
                session->upload_active = 0; // 避免重复发送
            }
            break;

        case LWS_CALLBACK_CLOSED:
            printf("客户端连接关闭\n");
            if (session->upload_file) {
                fclose(session->upload_file);
                session->upload_file = NULL;
            }
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
        callback_upload_server,
        sizeof(struct client_session),
        MAX_PAYLOAD_SIZE,
    },
    { NULL, NULL, 0, 0 } /* terminator */
};

int main(int argc, char *argv[]) {
    int port = 8080;
    
    if (argc > 1) {
        port = atoi(argv[1]);
    }
    
    printf("启动WebSocket文件上传服务器，端口: %d\n", port);
    printf("上传目录: %s\n", UPLOAD_DIR);
    
    // 确保上传目录存在
    ensure_upload_dir();
    
    // 创建WebSocket上下文
    struct lws_context_creation_info info;
    memset(&info, 0, sizeof(info));
    
    info.port = port;
    info.protocols = protocols;
    info.gid = -1;
    info.uid = -1;
    
    struct lws_context *context = lws_create_context(&info);
    if (!context) {
        printf("创建WebSocket上下文失败\n");
        return 1;
    }
    
    printf("服务器已启动，等待连接...\n");
    printf("WebSocket URL: ws://localhost:%d/upload\n", port);
    
    // 事件循环
    int n = 0;
    while (n >= 0) {
        n = lws_service(context, 1000);
    }
    
    // 清理资源
    lws_context_destroy(context);
    
    return 0;
}