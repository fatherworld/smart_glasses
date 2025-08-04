# Socket Client for AI Voice Processing
# 用于测试Socket Server的客户端示例

import asyncio
import struct
import json
import os
import sys
import time
import tempfile
import threading
import datetime
import queue
from typing import Optional
from io import BytesIO
import wave

def log_with_time(message: str):
    """输出带时间戳的日志"""
    current_time = datetime.datetime.now().strftime("[%H:%M:%S.%f]")[:-3]  # 精确到毫秒
    print(f"{current_time} [CLIENT] {message}")

# 尝试导入音频播放库
log_with_time("🔄 开始导入pygame...")
try:
    import pygame
    PYGAME_AVAILABLE = True
    log_with_time("✅ pygame导入成功")
except ImportError:
    PYGAME_AVAILABLE = False
    log_with_time("⚠️ pygame未安装，无法播放音频。请运行: pip install pygame")

log_with_time("🔄 开始导入playsound...")
try:
    import playsound
    PLAYSOUND_AVAILABLE = True
    log_with_time("✅ playsound导入成功")
except ImportError:
    PLAYSOUND_AVAILABLE = False
    log_with_time("⚠️ playsound未安装")

# 添加协议类
class SocketProtocol:
    """Socket通信协议定义（与服务器保持一致）"""
    
    # 消息类型
    MSG_VOICE_START = 0x01    # 开始语音传输
    MSG_VOICE_DATA = 0x02     # 语音数据块
    MSG_VOICE_END = 0x03      # 语音传输结束
    MSG_TEXT_DATA = 0x04      # 文本数据
    MSG_AUDIO_DATA = 0x05     # 音频数据
    MSG_AI_START = 0x06       # AI开始响应
    MSG_AI_END = 0x07         # AI响应结束
    MSG_AUDIO_START = 0x08    # 音频开始
    MSG_AUDIO_END = 0x09      # 音频结束
    MSG_ERROR = 0x0A          # 错误消息
    MSG_AI_CANCELLED = 0x0B   # AI响应被取消
    MSG_JSON_RESPONSE = 0x0C  # JSON响应
    MSG_CONFIG = 0x0D         # 配置消息
    MSG_AI_NEWCHAT = 0x0E     # 新对话开始
    
    # 响应格式
    RESPONSE_JSON = "json"
    RESPONSE_STREAM = "stream"
    
    @staticmethod
    def pack_message(msg_type: int, data: bytes) -> bytes:
        """打包消息：消息类型(1字节) + 数据长度(4字节) + 数据"""
        data_len = len(data)
        return struct.pack('!BI', msg_type, data_len) + data
    
    @staticmethod
    async def unpack_message(reader: asyncio.StreamReader) -> tuple:
        """解包消息"""
        # 读取消息头（5字节：1字节类型 + 4字节长度）
        header = await reader.readexactly(5)
        msg_type, data_len = struct.unpack('!BI', header)
        
        # 读取数据
        if data_len > 0:
            data = await reader.readexactly(data_len)
        else:
            data = b''
            
        return msg_type, data


class AISocketClient:
    """AI Socket客户端"""
    
    def __init__(self, host='localhost', port=7861):
        log_with_time("🔄 开始初始化AISocketClient...")
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self.is_connected = False
        self.response_format = SocketProtocol.RESPONSE_STREAM
        log_with_time("✅ 基本属性初始化完成")
        
        # 音频播放相关
        log_with_time("🔄 初始化音频播放相关属性...")
        self.audio_buffer = BytesIO()
        self.audio_playing = False
        self.current_audio_file = None
        
        # 音频播放队列
        self.audio_play_queue = queue.Queue()
        self.audio_player_thread = None
        self.audio_player_running = False
        log_with_time("✅ 音频播放属性初始化完成")
        
        # 初始化pygame音频
        if PYGAME_AVAILABLE:
            log_with_time("🔄 开始初始化pygame音频系统...")
            try:
                # 使用更通用的音频设置来支持MP3
                pygame.mixer.init(frequency=22050, size=-16, channels=2, buffer=512)
                log_with_time("✅ Pygame音频系统已初始化 (支持MP3)")
            except Exception as e:
                log_with_time(f"⚠️ Pygame音频初始化失败: {e}")
        else:
            log_with_time("⚠️ pygame不可用，跳过音频系统初始化")
        
        log_with_time("✅ AISocketClient初始化完成")

    def diagnose_connection(self):
        """诊断连接问题"""
        log_with_time("🔧 开始网络诊断...")
        
        import socket
        import subprocess
        import platform
        
        # 1. 检查本机网络
        try:
            socket.create_connection(("8.8.8.8", 53), timeout=3)
            log_with_time("✅ 本机网络正常")
        except:
            log_with_time("❌ 本机网络异常")
            return
        
        # 2. 检查IP解析和连通性
        try:
            # 解析主机名到IP
            resolved_ip = socket.gethostbyname(self.host)
            log_with_time(f"✅ 主机名解析: {self.host} -> {resolved_ip}")
            
            # 如果是localhost，检查是否解析正确
            if self.host == 'localhost' and resolved_ip != '127.0.0.1':
                log_with_time(f"⚠️ localhost解析异常: {resolved_ip} (应该是 127.0.0.1)")
                
        except Exception as e:
            log_with_time(f"❌ 主机名解析失败: {e}")
            
        # 测试127.0.0.1连通性
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            sock.connect(('127.0.0.1', 80))  # 测试回环接口
            sock.close()
            log_with_time("✅ 127.0.0.1连通性正常")
        except:
            log_with_time("⚠️ 127.0.0.1连通性可能有问题")
        
        # 3. 端口占用检查
        try:
            if platform.system() == "Windows":
                result = subprocess.run(['netstat', '-an'], capture_output=True, text=True, timeout=5)
                if f":{self.port}" in result.stdout:
                    log_with_time(f"✅ 端口 {self.port} 有程序监听")
                else:
                    log_with_time(f"❌ 端口 {self.port} 无程序监听")
            else:
                result = subprocess.run(['ss', '-tln'], capture_output=True, text=True, timeout=5)
                if f":{self.port}" in result.stdout:
                    log_with_time(f"✅ 端口 {self.port} 有程序监听")
                else:
                    log_with_time(f"❌ 端口 {self.port} 无程序监听")
        except Exception as e:
            log_with_time(f"⚠️ 端口检查失败: {e}")
        
        log_with_time("🔧 网络诊断完成")

    def start_audio_player_thread(self):
        """启动音频播放队列线程"""
        log_with_time("🔄 启动音频播放队列线程...")
        self.audio_player_running = True
        self.audio_player_thread = threading.Thread(target=self._audio_player_worker, daemon=True)
        self.audio_player_thread.start()
        log_with_time("✅ 音频播放队列线程已启动")
    
    def stop_audio_player_thread(self):
        """停止音频播放队列线程"""
        if self.audio_player_running:
            log_with_time("🔄 停止音频播放队列线程...")
            self.audio_player_running = False
            
            # 清空队列并添加停止信号
            while not self.audio_play_queue.empty():
                try:
                    self.audio_play_queue.get_nowait()
                except queue.Empty:
                    break
            self.audio_play_queue.put(None)  # 停止信号
            
            if self.audio_player_thread and self.audio_player_thread.is_alive():
                self.audio_player_thread.join(timeout=2)
            log_with_time("✅ 音频播放队列线程已停止")
    
    def _audio_player_worker(self):
        """音频播放队列工作线程"""
        log_with_time("🎵 音频播放队列工作线程启动")
        
        while self.audio_player_running:
            try:
                # 等待音频文件
                audio_file = self.audio_play_queue.get(timeout=1)
                
                # 检查停止信号
                if audio_file is None:
                    break
                
                # 根据类型显示不同的日志信息
                if isinstance(audio_file, str):
                    log_with_time(f"🎵 从队列取出音频文件: {os.path.basename(audio_file)}")
                else:
                    log_with_time(f"🎵 从队列取出音频数据: 内存数据")
                
                # 播放音频文件
                self._play_audio_immediately(audio_file)
                
                # 标记任务完成
                self.audio_play_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                log_with_time(f"❌ 音频播放队列工作线程异常: {e}")
        
        log_with_time("🎵 音频播放队列工作线程结束")

    async def connect(self, timeout=10):
        """连接到服务器"""
        log_with_time(f"🔄 开始连接到服务器 {self.host}:{self.port}")
        
        # 直接尝试异步连接，不做预检查（避免干扰）
        log_with_time("🔄 直接尝试建立异步连接...")
        
        try:
            log_with_time(f"🔄 建立TCP连接 (超时: {timeout}秒)...")
            
            # 添加连接超时
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=timeout
            )
            log_with_time("✅ TCP连接建立成功")
            
            self.is_connected = True
            log_with_time(f"✅ 已连接到服务器 {self.host}:{self.port}")
            
            # 启动音频播放队列线程
            self.start_audio_player_thread()
            
            # 发送配置消息
            log_with_time("🔄 发送配置消息...")
            await self.send_config()
            log_with_time("✅ 配置消息发送完成")
            
        except asyncio.TimeoutError:
            log_with_time(f"❌ 连接超时 ({timeout}秒)")
            log_with_time("💡 可能原因：1) 服务器响应慢 2) 网络延迟 3) 服务器过载")
            return False
        except ConnectionRefusedError:
            log_with_time("❌ 连接被拒绝")
            log_with_time("💡 请检查服务器是否在运行")
            # 运行详细诊断
            self.diagnose_connection()
            return False
        except Exception as e:
            log_with_time(f"❌ 连接失败: {e}")
            log_with_time(f"💡 异常类型: {type(e).__name__}")
            # 运行详细诊断
            self.diagnose_connection()
            return False
        return True
    
    async def disconnect(self):
        """断开连接"""
        log_with_time("🔄 开始断开连接...")
        
        # 停止音频播放队列线程
        self.stop_audio_player_thread()
        
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        
        # 停止音频播放
        if PYGAME_AVAILABLE and self.audio_playing:
            try:
                pygame.mixer.music.stop()
            except:
                pass
        
        self.is_connected = False
        log_with_time("✅ 已断开连接")
    
    async def send_message(self, msg_type: int, data: bytes):
        """发送消息"""
        if not self.is_connected:
            log_with_time("⚠️ 连接已断开，无法发送消息")
            return False
        
        try:
            log_with_time(f"📤 准备发送消息: 类型={msg_type}(0x{msg_type:02X}), 数据长度={len(data)}")
            if len(data) > 0:
                try:
                    # 尝试解码为文本（前100字节）
                    preview = data[:100].decode('utf-8', errors='ignore')
                    log_with_time(f"📤 数据预览: '{preview}'")
                except:
                    # 如果无法解码，显示十六进制
                    hex_preview = data[:20].hex()
                    log_with_time(f"📤 数据十六进制: {hex_preview}")
            
            message = SocketProtocol.pack_message(msg_type, data)
            log_with_time(f"📤 打包后消息长度: {len(message)} 字节")
            
            self.writer.write(message)
            await self.writer.drain()
            log_with_time("✅ 消息发送成功")
            return True
        except Exception as e:
            log_with_time(f"❌ 发送消息失败: {e}")
            return False
    
    async def send_config(self):
        """发送配置消息"""
        log_with_time("🔄 构建配置消息...")
        config = {
            "response_format": self.response_format
        }
        log_with_time("🔄 序列化配置数据...")
        config_data = json.dumps(config).encode('utf-8')
        log_with_time("🔄 发送配置消息到服务器...")
        await self.send_message(SocketProtocol.MSG_CONFIG, config_data)
        log_with_time(f"✅ 已发送配置: {config}")
    
    async def send_voice_file(self, file_path: str):
        """发送语音文件"""
        if not os.path.exists(file_path):
            print(f"文件不存在: {file_path}")
            return False
        
        try:
            # 发送语音开始信号
            await self.send_message(SocketProtocol.MSG_VOICE_START, b'')
            print("发送语音开始信号")
            
            # 读取并发送音频文件
            with open(file_path, 'rb') as f:
                chunk_size = 8192  # 8KB chunks
                total_sent = 0
                
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    
                    await self.send_message(SocketProtocol.MSG_VOICE_DATA, chunk)
                    total_sent += len(chunk)
                    print(f"已发送 {total_sent} 字节")
                    
                    # 模拟实时发送延迟
                    await asyncio.sleep(0.01)
            
            # 发送语音结束信号
            await self.send_message(SocketProtocol.MSG_VOICE_END, b'')
            print("发送语音结束信号")
            
            return True
            
        except Exception as e:
            print(f"发送语音文件失败: {e}")
            return False
    
    async def receive_messages(self):
        """接收服务器消息"""
        print("🔄 开始接收服务器消息...")
        try:
            while self.is_connected:
                try:
                    msg_type, data = await SocketProtocol.unpack_message(self.reader)
                    
                    if msg_type == SocketProtocol.MSG_TEXT_DATA:
                        text = data.decode('utf-8')
                        print(f"📝 文本: {text}")
                        
                    elif msg_type == SocketProtocol.MSG_AUDIO_DATA:
                        # 检查是否是音频包尾标记
                        if data == bytes([0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF]):
                            print("🔊 音频包结束标记")
                            # 立即播放当前缓冲区的音频
                            if self.audio_buffer.tell() > 0:
                                self._play_current_audio_chunk()
                        else:
                            print(f"🔊 音频数据: {len(data)} 字节")
                            # 将音频数据写入缓冲区
                            self.audio_buffer.write(data)
                        
                    elif msg_type == SocketProtocol.MSG_AI_START:
                        print("🤖 AI开始响应")
                        
                    elif msg_type == SocketProtocol.MSG_AI_END:
                        print("🤖 AI响应结束")
                        
                    elif msg_type == SocketProtocol.MSG_AUDIO_START:
                        print("🔊 音频开始")
                        # 重置音频缓冲区
                        self.audio_buffer = BytesIO()
                        
                    elif msg_type == SocketProtocol.MSG_AUDIO_END:
                        print("🔊 音频结束")
                        # 播放剩余的音频数据（如果有的话）
                        if self.audio_buffer.tell() > 0:
                            self._play_current_audio_chunk()
                        print("🎵 所有音频播放完毕")
                        
                    elif msg_type == SocketProtocol.MSG_ERROR:
                        error_msg = data.decode('utf-8')
                        print(f"❌ 错误: {error_msg}")
                        
                    elif msg_type == SocketProtocol.MSG_AI_CANCELLED:
                        print("🚫 AI响应被取消")
                        
                    elif msg_type == SocketProtocol.MSG_JSON_RESPONSE:
                        json_data = json.loads(data.decode('utf-8'))
                        print(f"📋 JSON响应: {json_data}")
                        
                    elif msg_type == SocketProtocol.MSG_AI_NEWCHAT:
                        print("💬 新对话开始")
                        
                    else:
                        print(f"❓ 未知消息类型: {msg_type}, 数据长度: {len(data)}")
                        
                except asyncio.IncompleteReadError:
                    print("⚠️ 服务器断开连接（IncompleteReadError）")
                    break
                except ConnectionResetError:
                    print("⚠️ 连接被重置")
                    break
                except Exception as e:
                    print(f"❌ 接收单个消息时出错: {e}")
                    # 继续尝试接收下一个消息
                    await asyncio.sleep(0.1)
                    continue
                    
        except Exception as e:
            print(f"❌ 接收消息主循环出错: {e}")
        finally:
            self.is_connected = False
            print("🔄 消息接收循环结束")
    
    def _play_current_audio_chunk(self):
        """播放当前音频缓冲区的内容并重置缓冲区"""
        try:
            if self.audio_buffer.tell() == 0:
                log_with_time("⚠️ 音频缓冲区为空")
                return
                
            # 获取音频数据
            self.audio_buffer.seek(0)
            audio_data = self.audio_buffer.read()
            
            log_with_time(f"🎵 准备播放音频段: {len(audio_data)} 字节")
            
            # 重置缓冲区为下一段音频做准备
            self.audio_buffer = BytesIO()
            
            # 直接从内存播放音频，避免硬盘读写
            self.play_audio_buffer(audio_data)
            
        except Exception as e:
            log_with_time(f"❌ 播放音频段失败: {e}")
            # 确保重置缓冲区
            self.audio_buffer = BytesIO()

    async def interactive_mode(self):
        """交互模式"""
        print("\n=== 交互模式 ===")
        print("输入语音文件路径，或输入 'quit' 退出")
        print("输入 'json' 切换到JSON模式，'stream' 切换到流式模式")
        print("发送音频后，请稍等片刻查看服务器响应...")
        if PYGAME_AVAILABLE:
            print("🔊 音频播放功能已启用 (pygame)")
        elif PLAYSOUND_AVAILABLE:
            print("🔊 音频播放功能已启用 (playsound)")
        else:
            print("⚠️ 音频播放功能不可用，请安装 pygame 或 playsound")
        
        # 使用asyncio的队列来处理用户输入
        import concurrent.futures
        
        def get_user_input():
            return input("\n> ").strip()
        
        with concurrent.futures.ThreadPoolExecutor() as executor:
            while self.is_connected:
                try:
                    # 异步获取用户输入
                    future = executor.submit(get_user_input)
                    
                    while not future.done() and self.is_connected:
                        await asyncio.sleep(0.1)
                    
                    if not self.is_connected:
                        break
                        
                    user_input = future.result()
                    
                    if user_input.lower() == 'quit':
                        break
                    elif user_input.lower() == 'json':
                        self.response_format = SocketProtocol.RESPONSE_JSON
                        await self.send_config()
                        print("✅ 已切换到JSON模式")
                    elif user_input.lower() == 'stream':
                        self.response_format = SocketProtocol.RESPONSE_STREAM
                        await self.send_config()
                        print("✅ 已切换到流式模式")
                    elif user_input and os.path.exists(user_input):
                        print(f"📤 发送语音文件: {user_input}")
                        success = await self.send_voice_file(user_input)
                        if success:
                            print("✅ 语音文件发送完成，等待服务器响应...")
                        else:
                            print("❌ 语音文件发送失败")
                    elif user_input.strip():
                        print("❌ 文件不存在或输入无效")
                        
                except KeyboardInterrupt:
                    print("\n用户中断")
                    break
                except Exception as e:
                    print(f"❌ 输入处理出错: {e}")
                    await asyncio.sleep(0.1)

    def play_audio_file(self, file_path: str):
        """播放音频文件 - 使用队列排队播放"""
        log_with_time(f"🎵 音频文件加入播放队列: {os.path.basename(file_path)}")
        
        # 将音频文件加入播放队列
        self.audio_play_queue.put(file_path)
        log_with_time(f"🎵 当前队列长度: {self.audio_play_queue.qsize()}")
    
    def play_audio_buffer(self, audio_data: bytes):
        """直接从内存播放音频数据 - 使用队列排队播放"""
        log_with_time(f"🎵 音频数据加入播放队列: {len(audio_data)} 字节")
        
        # 将音频数据加入播放队列（以bytes形式）
        self.audio_play_queue.put(audio_data)
        log_with_time(f"🎵 当前队列长度: {self.audio_play_queue.qsize()}")
    
    def _play_audio_immediately(self, audio_item):
        """立即播放音频（由队列工作线程调用）"""
        try:
            # 判断是文件路径还是音频数据
            if isinstance(audio_item, str):
                # 文件路径方式播放
                self._play_audio_file(audio_item)
            elif isinstance(audio_item, bytes):
                # 内存数据方式播放
                self._play_audio_from_memory(audio_item)
            else:
                log_with_time(f"❌ 未知的音频项类型: {type(audio_item)}")
        except Exception as e:
            log_with_time(f"❌ 播放音频失败: {e}")
    
    def _play_audio_file(self, file_path: str):
        """从文件播放音频"""
        try:
            log_with_time(f"🔊 开始播放MP3文件: {os.path.basename(file_path)}")
            
            if PYGAME_AVAILABLE:
                try:
                    pygame.mixer.music.load(file_path)
                    pygame.mixer.music.play()
                    log_with_time(f"🔊 使用pygame播放MP3文件: {os.path.basename(file_path)}")
                    
                    # 等待播放完成
                    while pygame.mixer.music.get_busy():
                        time.sleep(0.1)
                    log_with_time(f"🎵 pygame音频播放完成: {os.path.basename(file_path)}")
                    
                    # 确保pygame释放文件句柄
                    pygame.mixer.music.stop()
                    pygame.mixer.music.unload()
                    time.sleep(0.2)  # 给系统一点时间释放文件句柄
                    
                except Exception as pygame_error:
                    log_with_time(f"⚠️ Pygame播放失败: {pygame_error}")
                    # 如果pygame失败，尝试playsound
                    if PLAYSOUND_AVAILABLE:
                        log_with_time("🔄 尝试使用playsound播放...")
                        playsound.playsound(file_path)
                        log_with_time(f"🔊 playsound播放完成: {os.path.basename(file_path)}")
                    else:
                        raise pygame_error
                        
            elif PLAYSOUND_AVAILABLE:
                playsound.playsound(file_path)
                log_with_time(f"🔊 playsound播放完成: {os.path.basename(file_path)}")
            else:
                log_with_time("❌ 没有可用的音频播放库")
                
        finally:
            # 清理临时文件（带重试机制）
            if file_path and os.path.exists(file_path):
                self._cleanup_temp_file(file_path)
    
    def _play_audio_from_memory(self, audio_data: bytes):
        """从内存直接播放音频数据"""
        try:
            log_with_time(f"🔊 开始播放内存音频数据: {len(audio_data)} 字节")
            
            # 检测音频格式
            audio_format = self._detect_audio_format(audio_data)
            log_with_time(f"🎵 检测到音频格式: {audio_format}")
            
            if audio_format == "pcm":
                # PCM格式使用一半采样率播放
                self._play_pcm_with_half_sample_rate(audio_data)
                return
            
            # MP3/WAV等压缩格式播放
            if PYGAME_AVAILABLE:
                try:
                    # 创建BytesIO对象
                    audio_buffer = BytesIO(audio_data)
                    
                    # 使用pygame从内存播放
                    pygame.mixer.music.load(audio_buffer)
                    pygame.mixer.music.play()
                    log_with_time(f"🔊 使用pygame从内存播放MP3: {len(audio_data)} 字节")
                    
                    # 等待播放完成
                    while pygame.mixer.music.get_busy():
                        time.sleep(0.1)
                    log_with_time(f"🎵 pygame内存音频播放完成")
                    
                    # 确保pygame释放资源
                    pygame.mixer.music.stop()
                    pygame.mixer.music.unload()
                    time.sleep(0.1)  # 稍微减少等待时间，因为没有文件操作
                    
                except Exception as pygame_error:
                    log_with_time(f"⚠️ Pygame内存播放失败: {pygame_error}")
                    # 如果内存播放失败，回退到临时文件方式
                    log_with_time("🔄 回退到临时文件播放方式...")
                    self._play_audio_fallback_to_file(audio_data)
                        
            else:
                log_with_time("⚠️ pygame不可用，回退到临时文件播放方式...")
                self._play_audio_fallback_to_file(audio_data)
                
        except Exception as e:
            log_with_time(f"❌ 内存音频播放失败: {e}")
    
    def _detect_audio_format(self, audio_data: bytes) -> str:
        """检测音频数据格式"""
        if len(audio_data) < 10:
            return "unknown"
        
        # 检查MP3文件头
        if audio_data.startswith(b'ID3') or audio_data.startswith(b'\xff\xfb') or audio_data.startswith(b'\xff\xf3'):
            return "mp3"
        
        # 检查WAV文件头
        if audio_data.startswith(b'RIFF') and b'WAVE' in audio_data[:12]:
            return "wav"
        
        # 如果没有明显的文件头，且数据长度合理，判断为PCM
        if len(audio_data) > 100:
            return "pcm"
        
        return "unknown"
    
    def _play_pcm_with_half_sample_rate(self, pcm_data: bytes):
        """使用一半采样率播放PCM数据"""
        try:
            if not PYGAME_AVAILABLE:
                log_with_time("❌ pygame不可用，无法播放PCM")
                return
            
            log_with_time(f"🔊 开始PCM播放（一半采样率方案）: {len(pcm_data)} 字节")
            
            # 服务器端的PCM参数
            server_sample_rate = 16000  # 服务器声称的采样率
            channels = 1  # 单声道
            sample_width = 2  # 16-bit
            
            # 分离计算采样率和播放采样率
            original_sample_rate = server_sample_rate  # 16000Hz (用于计算)
            playback_sample_rate = server_sample_rate // 2  # 8000Hz (仅用于pygame播放)
            
            log_with_time(f"🧠 采样率方案:")
            log_with_time(f"   服务器采样率: {server_sample_rate}Hz")
            log_with_time(f"   计算采样率: {original_sample_rate}Hz (用于时长计算)")
            log_with_time(f"   播放采样率: {playback_sample_rate}Hz (仅pygame初始化)")
            
            # 使用原始采样率计算预期播放时长
            total_samples = len(pcm_data) // sample_width
            duration_seconds = total_samples / original_sample_rate
            
            log_with_time(f"🎵 播放参数:")
            log_with_time(f"   计算采样率: {original_sample_rate}Hz")
            log_with_time(f"   pygame采样率: {playback_sample_rate}Hz")
            log_with_time(f"   声道数: {channels}")
            log_with_time(f"   采样点数: {total_samples}")
            log_with_time(f"   预期时长: {duration_seconds:.2f}秒")
            
            # 重新初始化pygame mixer - 只有这里使用一半采样率
            try:
                pygame.mixer.quit()
                pygame.mixer.init(
                    frequency=playback_sample_rate,  # 只有pygame使用一半采样率
                    size=-16,                        # 16-bit signed
                    channels=channels,               # 单声道
                    buffer=2048
                )
                log_with_time(f"🎮 pygame mixer重新初始化: {playback_sample_rate}Hz")
            except Exception as e:
                log_with_time(f"❌ pygame mixer初始化失败: {e}")
                return
            
            # 创建音频对象并播放
            sound = pygame.mixer.Sound(buffer=pcm_data)
            sound_length = sound.get_length()
            
            log_with_time(f"🎵 pygame报告时长: {sound_length:.3f}秒")
            
            # 开始播放
            start_time = time.time()
            sound.play()
            
            # 等待播放完成
            while pygame.mixer.get_busy():
                time.sleep(0.1)
            
            actual_duration = time.time() - start_time
            speed_ratio = duration_seconds / actual_duration if actual_duration > 0 else 0
            
            log_with_time(f"🎵 PCM播放完成:")
            log_with_time(f"   实际播放时长: {actual_duration:.2f}秒")
            log_with_time(f"   预期播放时长: {duration_seconds:.2f}秒 (基于{original_sample_rate}Hz)")
            log_with_time(f"   播放速度比率: {speed_ratio:.2f}x")
            log_with_time(f"   说明: 使用{playback_sample_rate}Hz播放{original_sample_rate}Hz数据")
            
            # 播放效果评估
            if 0.9 <= speed_ratio <= 1.1:
                log_with_time("✅ 播放速度正常 - 采样率方案成功！")
            elif speed_ratio > 1.3:
                log_with_time("⚠️ 播放仍然偏快，可能需要进一步调整")
            elif speed_ratio < 0.7:
                log_with_time("⚠️ 播放偏慢，采样率可能设置过低")
            else:
                log_with_time("🔧 播放速度略有偏差，但基本可用")
                
        except Exception as e:
            log_with_time(f"❌ PCM播放失败: {e}")
            import traceback
            log_with_time(f"❌ 详细错误: {traceback.format_exc()}")
    
    def _play_audio_fallback_to_file(self, audio_data: bytes):
        """回退方案：通过临时文件播放音频"""
        try:
            log_with_time("🔄 使用临时文件回退播放方案...")
            
            # 创建临时文件
            temp_file = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
            temp_file.write(audio_data)
            temp_file.close()
            
            log_with_time(f"💾 已创建临时音频文件: {os.path.basename(temp_file.name)}")
            
            # 使用文件播放方式
            self._play_audio_file(temp_file.name)
            
        except Exception as e:
            log_with_time(f"❌ 回退播放方案也失败了: {e}")

    def _cleanup_temp_file(self, file_path: str, max_retries: int = 5):
        """清理临时文件（带重试机制）"""
        for i in range(max_retries):
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    log_with_time(f"🗑️ 已清理临时文件: {os.path.basename(file_path)}")
                    return True
                else:
                    return True  # 文件已不存在
            except Exception as cleanup_error:
                if i < max_retries - 1:
                    log_with_time(f"⚠️ 清理临时文件重试 {i+1}/{max_retries}: {cleanup_error}")
                    time.sleep(0.5 * (i + 1))  # 递增延迟
                else:
                    log_with_time(f"❌ 清理临时文件最终失败: {cleanup_error}")
                    # 如果实在删除不了，至少记录下来，让系统定期清理
                    log_with_time(f"   临时文件将由系统自动清理: {file_path}")
                    return False
        return False

    def save_and_play_audio_buffer(self):
        """保存并播放音频缓冲区（已废弃，由_play_current_audio_chunk替代）"""
        log_with_time("⚠️ save_and_play_audio_buffer已废弃，请使用_play_current_audio_chunk")
        self._play_current_audio_chunk()


async def main():
    """主函数"""
    log_with_time("🚀 启动AI Socket客户端")
    
    # 解析命令行参数
    import argparse
    parser = argparse.ArgumentParser(description='AI Socket客户端')
    parser.add_argument('--host', default='127.0.0.1', help='服务器IP地址 (默认: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=7860, help='服务器端口 (默认: 7860)')
    parser.add_argument('audio_file', nargs='?', help='要发送的音频文件路径（可选）')
    
    args = parser.parse_args()
    
    log_with_time(f"🔧 连接参数: {args.host}:{args.port}")
    
    # 创建客户端
    log_with_time("🔄 创建客户端实例...")
    client = AISocketClient(host=args.host, port=args.port)
    log_with_time("✅ 客户端实例创建完成")
    
    # 连接到服务器
    log_with_time("🔄 连接到服务器...")
    if not await client.connect():
        log_with_time("❌ 连接失败，退出程序")
        return
    log_with_time("✅ 服务器连接成功")
    
    # 启动消息接收任务
    log_with_time("🔄 启动消息接收任务...")
    receive_task = asyncio.create_task(client.receive_messages())
    log_with_time("✅ 消息接收任务已启动")
    
    try:
        # 检查运行模式
        log_with_time("🔄 检查运行模式...")
        if args.audio_file:
            # 批量模式：发送指定的文件
            log_with_time(f"🔄 批量模式，文件: {args.audio_file}")
            if os.path.exists(args.audio_file):
                log_with_time(f"📤 发送语音文件: {args.audio_file}")
                await client.send_voice_file(args.audio_file)
                
                # 等待一段时间接收响应
                log_with_time("⏳ 等待服务器响应...")
                await asyncio.sleep(10)
            else:
                log_with_time(f"❌ 文件不存在: {args.audio_file}")
        else:
            # 交互模式
            log_with_time("🔄 进入交互模式...")
            await client.interactive_mode()
            
    except KeyboardInterrupt:
        print("\n用户中断")
    finally:
        # 断开连接
        await client.disconnect()
        
        # 取消接收任务
        if not receive_task.done():
            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass


if __name__ == "__main__":
    log_with_time("🔄 开始运行主程序...")
    asyncio.run(main())
    log_with_time("🏁 程序结束") 