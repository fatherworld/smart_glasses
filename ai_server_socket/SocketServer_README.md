# AI Socket Server 使用文档

## 概述

AI Socket Server是一个基于TCP Socket的语音处理服务器，实现了完整的ASR-LLM-TTS流程，支持流式响应和JSON响应两种模式。具备内存音频播放、智能任务管理和实时流式处理等先进功能。

## 功能特性

- **语音识别(ASR)**: 支持多种ASR引擎，将语音转换为文字
- **大语言模型(LLM)**: 集成多种LLM，生成智能回复
- **语音合成(TTS)**: 支持Edge和火山引擎TTS，将文字转换为语音
- **流式响应**: 生成多少发送多少，实时性强，支持音频包分段标记
- **JSON响应**: 一次性返回完整结果
- **内存音频播放**: 支持从内存直接播放音频，减少硬盘I/O
- **音频播放队列**: 自动排队播放，避免音频跳过
- **多客户端支持**: 支持多个客户端同时连接
- **智能任务管理**: 任务取消、并发控制和资源清理
- **详细日志系统**: 毫秒级时间戳，智能日志输出

## 架构设计

### 通信协议

采用自定义的二进制协议：

```
消息格式: [消息类型(1字节)] + [数据长度(4字节)] + [数据内容(变长)]
```

### 消息类型

| 类型 | 值 | 描述 |
|------|-----|------|
| MSG_VOICE_START | 0x01 | 开始语音传输 |
| MSG_VOICE_DATA | 0x02 | 语音数据块 |
| MSG_VOICE_END | 0x03 | 语音传输结束 |
| MSG_TEXT_DATA | 0x04 | 文本数据 |
| MSG_AUDIO_DATA | 0x05 | 音频数据 |
| MSG_AI_START | 0x06 | AI开始响应 |
| MSG_AI_END | 0x07 | AI响应结束 |
| MSG_AUDIO_START | 0x08 | 音频开始 |
| MSG_AUDIO_END | 0x09 | 音频结束 |
| MSG_ERROR | 0x0A | 错误消息 |
| MSG_AI_CANCELLED | 0x0B | AI响应被取消 |
| MSG_JSON_RESPONSE | 0x0C | JSON响应 |
| MSG_CONFIG | 0x0D | 配置消息 |
| MSG_AI_NEWCHAT | 0x0E | 新对话开始 |

### 音频包分段机制

服务器使用特殊的8字节标记来分隔不同的音频段：
```python
end_marker = bytes([0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF])
```

客户端检测到此标记时会立即播放当前缓冲区的音频，实现真正的流式播放。

## 安装和运行

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

主要依赖：
- asyncio (内置)
- pygame (用于音频播放)
- 相关ASR、LLM、TTS库

### 2. 启动服务器

```bash
# 直接运行Socket服务器
python SocketServer.py
```

服务器默认监听 `0.0.0.0:7861`

### 3. 测试客户端

```bash
# 交互模式
python SocketClient.py

# 批量模式（发送指定音频文件）
python SocketClient.py test.wav

# 指定服务器地址
python SocketClient.py --host 192.168.1.100 --port 7861
```

## 使用方法

### 流式响应模式（默认）

1. 客户端连接到服务器
2. 发送配置消息（可选）
3. 发送语音开始信号
4. 分块发送语音数据
5. 发送语音结束信号
6. 接收流式响应：
   - 文本数据（实时生成）
   - 音频数据（实时合成，带分段标记）

### JSON响应模式

1. 发送配置消息切换到JSON模式
2. 发送语音数据
3. 接收完整的JSON响应

## 客户端示例

### 基本连接

```python
import asyncio
from SocketClient import AISocketClient

async def main():
    client = AISocketClient('localhost', 7861)
    
    # 连接到服务器
    if await client.connect():
        print("连接成功")
        
        # 发送音频文件
        await client.send_voice_file('test.wav')
        
        # 启动消息接收
        await client.receive_messages()
    
    await client.disconnect()

asyncio.run(main())
```

### 配置响应格式

```python
# 切换到JSON模式
client.response_format = "json"
await client.send_config()

# 切换到流式模式
client.response_format = "stream"
await client.send_config()
```

### 交互模式使用

```python
# 进入交互模式
await client.interactive_mode()

# 交互命令:
# - 输入音频文件路径发送语音
# - 输入 'json' 切换到JSON模式
# - 输入 'stream' 切换到流式模式
# - 输入 'quit' 退出
```

## 响应格式

### 流式响应

客户端会实时收到以下消息序列：

1. `AI_START` - AI开始响应
2. `AUDIO_START` - 音频开始
3. 循环接收：
   - `TEXT_DATA` - 文本块
   - `AUDIO_DATA` - 音频块（多次）
   - `AUDIO_DATA` - 音频分段结束标记
4. `AI_END` - AI响应结束
5. `AUDIO_END` - 音频结束

### JSON响应

```json
{
    "code": 0,
    "msg": "ok",
    "session_id": 123456,
    "user_text": "用户说的话",
    "ai_text": "AI的回复"
}
```

## 性能特性

### 音频播放优化

- **内存播放优先**: 使用`pygame.mixer.music.load(BytesIO)`直接从内存播放
- **智能回退**: 内存播放失败时自动回退到临时文件方式
- **播放队列**: 音频自动排队播放，避免跳过
- **资源管理**: 自动清理临时文件，带重试机制

### 并发控制

- 每个客户端最多同时执行2个任务
- 智能任务取消：新任务开始时自动取消旧任务
- 资源管理：任务完成后自动释放资源
- 异步事件循环调度：LLM生成时定期yield控制权

### 流式处理

- **文本流**: LLM生成的文本实时发送给客户端
- **音频流**: TTS合成的音频实时发送给客户端
- **智能分句**: 在句子边界处发送TTS，提高响应速度
- **分段播放**: 每个音频段独立播放，实现真正的流式体验

### 网络诊断

客户端具备完整的网络诊断功能：
- 本机网络连通性检查
- 主机名解析验证
- 端口占用状态检查
- 详细的错误报告和建议

## 日志系统

### 服务器日志

- 毫秒级时间戳：`[HH:MM:SS.fff]`
- 客户端识别：`[客户端 IP:端口]`
- 协议调试：详细的消息收发记录
- 任务跟踪：从语音接收到响应完成的全程计时

### 客户端日志

- 智能内容显示：
  - 文件路径只显示文件名
  - 内存数据显示"内存数据"提示
  - 避免二进制内容污染日志
- 详细的连接过程追踪
- 音频播放状态监控

## 与WebSocket版本的对比

| 特性 | Socket Server | WebSocket Server |
|------|---------------|------------------|
| 协议 | TCP Socket | WebSocket |
| 消息格式 | 二进制协议 | 文本/二进制混合 |
| 性能 | 更高 | 略低 |
| 兼容性 | 需要自定义客户端 | 浏览器原生支持 |
| 功能 | 完全一致 | 完全一致 |
| 音频播放 | 内存+队列播放 | 基础播放 |
| 日志系统 | 毫秒级详细日志 | 基础日志 |

## 技术亮点

### 1. 内存音频播放
- 减少硬盘I/O操作，提高性能
- 支持pygame内存播放和临时文件回退
- 自动资源清理机制

### 2. 智能任务调度
- LLM生成时的`asyncio.sleep(0.001)`确保事件循环调度
- TTS任务并行处理，不阻塞文本生成
- 精确的时间计量和性能监控

### 3. 协议健壮性
- 完整的协议调试和错误处理
- 数据长度验证（1MB限制）
- 异常连接自动诊断

## 故障排除

### 常见问题

1. **连接失败**
   - 检查服务器是否启动
   - 运行客户端自动网络诊断
   - 检查端口是否被占用
   - WSL环境需要配置端口映射

2. **音频播放问题**
   - 确保安装pygame: `pip install pygame`
   - 检查音频文件格式（支持MP3）
   - 查看内存播放回退日志

3. **响应超时**
   - 检查LLM服务状态
   - 查看服务器任务队列日志
   - 检查网络连接稳定性

4. **协议错误**
   - 查看协议调试日志
   - 确认客户端服务器版本匹配
   - 检查数据包完整性

### 调试技巧

1. **启用详细日志**：代码中已内置毫秒级日志
2. **网络诊断**：客户端连接失败时自动运行
3. **协议追踪**：服务器端显示详细的协议解析过程
4. **性能监控**：完整的任务执行时间统计

## 配置选项

### 服务器配置
- 监听地址：默认 `0.0.0.0:7861`
- 并发任务：每客户端最大2个
- TTS服务：Edge/火山引擎可选

### 客户端配置
- 连接超时：默认10秒
- 音频缓冲：自动管理
- 播放方式：内存优先，文件回退

## 扩展开发

### 添加新消息类型

```python
class SocketProtocol:
    MSG_CUSTOM = 0x10  # 自定义消息类型
```

### 自定义音频处理

```python
def custom_audio_processor(self, audio_data: bytes):
    # 自定义音频处理逻辑
    return processed_audio
```

### 扩展TTS服务

```python
class CustomTTSService:
    async def text_to_speech_stream(self, text: str):
        # 实现自定义TTS
        yield audio_chunk
```

## 许可证

本项目遵循原项目的许可证。

## 更新日志

### v2.0 (当前版本)
- 新增内存音频播放功能
- 实现音频播放队列
- 优化日志输出系统
- 增强网络诊断功能
- 改进任务调度机制
- 完善错误处理和资源清理 