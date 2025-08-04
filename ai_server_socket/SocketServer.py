# Socket Server for AI Voice Processing
# 实现ASR-LLM-TTS流程，支持流式响应

import sys
import os
import struct
import json
import asyncio
import socket
import io
import time
import datetime
import tempfile
import numpy as np
from collections import deque
from typing import Optional, Dict, Any, Tuple

# 音频处理库
try:
    from pydub import AudioSegment
    from pydub.utils import which
    import librosa
    AUDIO_LIBS_AVAILABLE = True
except ImportError as e:
    print(f"警告: 音频处理库导入失败: {e}")
    print("请安装: pip install pydub librosa")
    AUDIO_LIBS_AVAILABLE = False

# 添加本地包路径
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

#from TTSs import TTSService_Edge, TTSService_Volcano
#from LLMs import LLMFactory, ConversationManager
#from ASR import async_process_audio, filter_text


class SocketProtocol:
    """Socket通信协议定义"""
    
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
    
    # 音频格式
    AUDIO_FORMAT_MP3 = "mp3"
    AUDIO_FORMAT_PCM = "pcm"
    
    # 音频合并模式
    AUDIO_MERGE_DISABLED = "disabled"  # 不合并，流式发送
    AUDIO_MERGE_ENABLED = "enabled"    # 合并后一次性发送
    
    @staticmethod
    def pack_message(msg_type: int, data: bytes) -> bytes:
        """打包消息：消息类型(1字节) + 数据长度(4字节) + 数据"""
        data_len = len(data)
        return struct.pack('!BI', msg_type, data_len) + data
    
    @staticmethod
    async def unpack_message(reader: asyncio.StreamReader) -> Tuple[int, bytes]:
        """解包消息"""
        # 读取消息头（5字节：1字节类型 + 4字节长度）
        header = await reader.readexactly(5)
        
        # 调试：显示原始头部数据
        import datetime
        current_time = datetime.datetime.now().strftime("[%H:%M:%S.%f]")[:-3]
        print(f"{current_time} [PROTOCOL] 收到消息头: {header.hex()}")
        
        msg_type, data_len = struct.unpack('!BI', header)
        print(f"{current_time} [PROTOCOL] 解析头部: 类型={msg_type}, 长度={data_len}")
        
        # 读取数据
        if data_len > 0:
            if data_len > 1024*1024:  # 1MB limit
                print(f"{current_time} [PROTOCOL] ⚠️ 数据长度异常: {data_len}")
                raise ValueError(f"数据长度过大: {data_len}")
            data = await reader.readexactly(data_len)
            print(f"{current_time} [PROTOCOL] 读取数据完成: {len(data)} 字节")
        else:
            data = b''
            print(f"{current_time} [PROTOCOL] 无数据体")
            
        return msg_type, data
    
    @staticmethod
    def pack_text_message(msg_type: int, text: str) -> bytes:
        """打包文本消息"""
        return SocketProtocol.pack_message(msg_type, text.encode('utf-8'))
    
    @staticmethod
    def unpack_text_message(data: bytes) -> str:
        """解包文本消息"""
        return data.decode('utf-8')
    
    @staticmethod
    def pack_json_message(msg_type: int, json_data: dict) -> bytes:
        """打包JSON消息"""
        json_str = json.dumps(json_data, ensure_ascii=False)
        return SocketProtocol.pack_message(msg_type, json_str.encode('utf-8'))
    
    @staticmethod
    def unpack_json_message(data: bytes) -> dict:
        """解包JSON消息"""
        json_str = data.decode('utf-8')
        return json.loads(json_str)


class AISocketClient:
    """处理单个客户端连接的类"""
    
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, client_addr,
                 default_audio_format='mp3', default_audio_merge='disabled', verbose=False):
        self.reader = reader
        self.writer = writer
        self.client_addr = client_addr
        self.client_id = f"{client_addr[0]}:{client_addr[1]}"
        self.verbose = verbose
        
        # 会话管理
        #self.conversation_manager = ConversationManager()
        #self.tts_service = TTSService_Edge()
        
        # 连接级别的变量
        self.voice_id = 0
        self.session_timers = {}
        self.response_format = SocketProtocol.RESPONSE_STREAM  # 默认流式响应
        
        # 音频配置 - 使用服务器的默认配置
        self.audio_format = default_audio_format
        self.audio_merge = default_audio_merge
        
        # 显示客户端初始配置
        self.log_with_time(f"🎵 初始音频配置: {self.audio_format.upper()} + {'句子内合并' if self.audio_merge == 'enabled' else '立即发送'}")
        
        # 任务管理
        self.active_tasks = []
        self.tasks_lock = asyncio.Lock()
        self.max_concurrent_tasks = asyncio.Semaphore(2)
        
        # 队列管理
        self.pending_queries = deque()
        self.new_query_event = asyncio.Event()
        self.queue_lock = asyncio.Lock()
        
        # 标志管理
        self.is_connected = True
        self.cancel_tasks = asyncio.Event()
        

    
    def log_with_time(self, message: str, verbose_only=False):
        """输出带时间戳的日志（精确到毫秒）"""
        if verbose_only and not self.verbose:
            return
        current_time = datetime.datetime.now().strftime("[%H:%M:%S.%f]")[:-3]
        print(f"{current_time} [客户端 {self.client_id}] {message}")
    
    async def convert_mp3_to_pcm(self, mp3_data: bytes) -> bytes:
        """将MP3数据转换为PCM格式"""
        self.log_with_time(f"🔄 [CONVERT] 开始MP3转PCM - 输入大小: {len(mp3_data)} 字节")
        
        if not AUDIO_LIBS_AVAILABLE:
            self.log_with_time("⚠️ [CONVERT] 音频处理库不可用，返回原始数据")
            return mp3_data
        
        audio = None
        
        # 方法1: 尝试从内存直接读取（避免文件I/O问题）
        try:
            self.log_with_time("💾 [CONVERT] 尝试内存操作...")
            audio_buffer = io.BytesIO(mp3_data)
            audio = AudioSegment.from_file(audio_buffer, format="mp3")
            self.log_with_time("✅ [CONVERT] 内存操作成功")
            
        except Exception as memory_error:
            self.log_with_time(f"⚠️ [CONVERT] 内存操作失败: {memory_error}")
            self.log_with_time("🔄 [CONVERT] 回退到文件操作...")
            
            # 方法2: 改进的文件操作，带重试机制
            try:
                audio = await self._convert_mp3_with_file_retry(mp3_data)
            except Exception as file_error:
                self.log_with_time(f"❌ [CONVERT] 文件操作也失败: {file_error}")
                audio = None
        
        if audio is None:
            self.log_with_time(f"❌ [CONVERT] 所有转换方法均失败，返回原始数据")
            return mp3_data
        
        try:
            # 记录原始音频信息
            self.log_with_time(f"📊 [CONVERT] 原始音频信息:")
            self.log_with_time(f"   采样率: {audio.frame_rate} Hz")
            self.log_with_time(f"   声道数: {audio.channels}")
            self.log_with_time(f"   采样宽度: {audio.sample_width} 字节")
            self.log_with_time(f"   时长: {len(audio)} ms")
            
            # 转换为PCM格式 (16-bit, 16kHz, mono)
            self.log_with_time("🔧 [CONVERT] 开始音频格式转换")
            audio = audio.set_frame_rate(16000)
            audio = audio.set_channels(1)
            audio = audio.set_sample_width(2)  # 16-bit
            
            # 获取原始PCM数据
            self.log_with_time("📦 [CONVERT] 转换为PCM字节数据")
            pcm_data = audio.raw_data
            
            self.log_with_time(f"✅ [CONVERT] MP3转PCM完成:")
            self.log_with_time(f"   输入MP3: {len(mp3_data)} 字节")
            self.log_with_time(f"   输出PCM: {len(pcm_data)} 字节")
            self.log_with_time(f"   压缩比: {len(mp3_data)/len(pcm_data)*100:.1f}%")
            
            return pcm_data
                    
        except Exception as e:
            self.log_with_time(f"❌ [CONVERT] MP3转PCM失败: {e}")
            import traceback
            self.log_with_time(f"❌ [CONVERT] 详细错误: {traceback.format_exc()}")
            self.log_with_time(f"⚠️ [CONVERT] 返回原始MP3数据")
            return mp3_data
    
    async def _convert_mp3_with_file_retry(self, mp3_data: bytes, max_retries: int = 3):
        """使用文件操作的MP3转换，带重试机制"""
        temp_mp3_path = None
        
        for attempt in range(max_retries):
            try:
                self.log_with_time(f"📁 [CONVERT] 文件操作尝试 {attempt + 1}/{max_retries}")
                
                # 创建临时文件保存MP3数据
                with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as temp_mp3:
                    temp_mp3.write(mp3_data)
                    temp_mp3.flush()
                    os.fsync(temp_mp3.fileno())
                    temp_mp3_path = temp_mp3.name
                
                # 验证文件完整性
                if not os.path.exists(temp_mp3_path):
                    raise Exception("临时文件创建失败")
                
                file_size = os.path.getsize(temp_mp3_path)
                if file_size != len(mp3_data):
                    raise Exception(f"文件大小不匹配: 期望{len(mp3_data)}，实际{file_size}")
                
                # 等待文件系统同步完成
                await asyncio.sleep(0.02 * (attempt + 1))  # 递增延迟
                
                # 尝试加载音频
                audio = AudioSegment.from_mp3(temp_mp3_path)
                self.log_with_time(f"✅ [CONVERT] 文件操作成功 (尝试 {attempt + 1})")
                
                return audio
                
            except Exception as e:
                self.log_with_time(f"⚠️ [CONVERT] 文件操作尝试 {attempt + 1} 失败: {e}")
                
                if attempt == max_retries - 1:
                    self.log_with_time(f"❌ [CONVERT] 所有文件操作尝试均失败")
                    raise e
                else:
                    # 等待后重试
                    await asyncio.sleep(0.05 * (attempt + 1))
                    
            finally:
                # 清理临时文件
                if temp_mp3_path and os.path.exists(temp_mp3_path):
                    try:
                        os.unlink(temp_mp3_path)
                        self.log_with_time(f"🧹 [CONVERT] 临时文件已删除: {temp_mp3_path}")
                    except Exception as cleanup_error:
                        self.log_with_time(f"⚠️ [CONVERT] 临时文件删除失败: {cleanup_error}")
                
        raise Exception("文件操作重试次数已耗尽")
    

    
    async def send_message(self, msg_type: int, data: bytes):
        """发送消息给客户端"""
        try:
            if not self.is_connected:
                return False
                
            message = SocketProtocol.pack_message(msg_type, data)
            self.writer.write(message)
            await self.writer.drain()
            
            # 添加调试日志
            if msg_type == SocketProtocol.MSG_TEXT_DATA:
                self.log_with_time(f"📤 发送文本消息: {data.decode('utf-8')}")
            elif msg_type == SocketProtocol.MSG_AUDIO_DATA:
                self.log_with_time(f"📤 发送音频数据: {len(data)} 字节")
            elif msg_type == SocketProtocol.MSG_AI_START:
                self.log_with_time(f"📤 发送AI开始信号")
            elif msg_type == SocketProtocol.MSG_AI_END:
                self.log_with_time(f"📤 发送AI结束信号")
            elif msg_type == SocketProtocol.MSG_AUDIO_START:
                self.log_with_time(f"📤 发送音频开始信号")
            elif msg_type == SocketProtocol.MSG_AUDIO_END:
                self.log_with_time(f"📤 发送音频结束信号")
            
            return True
        except Exception as e:
            self.log_with_time(f"发送消息失败: {e}")
            await self.disconnect()
            return False
    
    async def send_text_message(self, msg_type: int, text: str):
        """发送文本消息"""
        data = text.encode('utf-8')
        return await self.send_message(msg_type, data)
    
    async def send_json_message(self, msg_type: int, json_data: dict):
        """发送JSON消息"""
        json_str = json.dumps(json_data, ensure_ascii=False)
        data = json_str.encode('utf-8')
        return await self.send_message(msg_type, data)
    
    async def disconnect(self):
        """断开连接"""
        if self.is_connected:
            self.is_connected = False
            self.cancel_tasks.set()
            
            # 取消所有活跃任务
            async with self.tasks_lock:
                for task in self.active_tasks.copy():
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                self.active_tasks.clear()
            
            # 关闭连接
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
    
    async def handle_client(self):
        """处理客户端连接"""
        self.log_with_time("🔗 新客户端连接开始处理")
        
        # 检查连接状态
        if self.writer.is_closing():
            self.log_with_time("⚠️ 连接已关闭，跳过处理")
            return
            
        self.log_with_time("✅ 连接状态正常，开始创建LLM任务")
        # 创建LLM响应处理任务
        llm_task = asyncio.create_task(self.handle_llm_responses())
        
        try:
            while self.is_connected:
                try:
                    # 接收消息
                    self.log_with_time("🔄 等待接收客户端消息...")
                    msg_type, data = await asyncio.wait_for(
                        SocketProtocol.unpack_message(self.reader), 
                        timeout=300
                    )
                    
                    self.log_with_time(f"📨 收到消息: 类型={msg_type}(0x{msg_type:02X}), 数据长度={len(data)}")
                    if len(data) > 0:
                        try:
                            # 尝试解码为文本（前100字节）
                            preview = data[:100].decode('utf-8', errors='ignore')
                            self.log_with_time(f"📨 数据预览: '{preview}'")
                        except:
                            # 如果无法解码，显示十六进制
                            hex_preview = data[:20].hex()
                            self.log_with_time(f"📨 数据十六进制: {hex_preview}")
                    
                    # 处理不同类型的消息
                    if msg_type == SocketProtocol.MSG_CONFIG:
                        self.log_with_time("📋 处理配置消息")
                        await self.handle_config_message(data)
                    elif msg_type == SocketProtocol.MSG_VOICE_START:
                        self.log_with_time("🎤 处理语音开始")
                        await self.handle_voice_start()
                    elif msg_type == SocketProtocol.MSG_VOICE_DATA:
                        self.log_with_time(f"🎤 处理语音数据: {len(data)}字节")
                        await self.handle_voice_data(data)
                    elif msg_type == SocketProtocol.MSG_VOICE_END:
                        self.log_with_time("🎤 处理语音结束")
                        await self.handle_voice_end()
                    else:
                        self.log_with_time(f"❌ 未知消息类型: {msg_type}(0x{msg_type:02X})")
                        self.log_with_time("💡 已知消息类型:")
                        self.log_with_time(f"   MSG_CONFIG = {SocketProtocol.MSG_CONFIG}(0x{SocketProtocol.MSG_CONFIG:02X})")
                        self.log_with_time(f"   MSG_VOICE_START = {SocketProtocol.MSG_VOICE_START}(0x{SocketProtocol.MSG_VOICE_START:02X})")
                        self.log_with_time(f"   MSG_VOICE_DATA = {SocketProtocol.MSG_VOICE_DATA}(0x{SocketProtocol.MSG_VOICE_DATA:02X})")
                        self.log_with_time(f"   MSG_VOICE_END = {SocketProtocol.MSG_VOICE_END}(0x{SocketProtocol.MSG_VOICE_END:02X})")
                        
                except asyncio.TimeoutError:
                    self.log_with_time("接收超时")
                    break
                except asyncio.IncompleteReadError:
                    self.log_with_time("客户端断开连接")
                    break
                except Exception as e:
                    self.log_with_time(f"处理消息时出错: {e}")
                    break
                    
        except Exception as e:
            self.log_with_time(f"客户端处理出错: {e}")
        finally:
            # 取消LLM任务
            if not llm_task.done():
                llm_task.cancel()
                try:
                    await llm_task
                except asyncio.CancelledError:
                    pass
            
            await self.disconnect()
            self.log_with_time("客户端连接结束")
    
    async def handle_config_message(self, data: bytes):
        """处理配置消息"""
        try:
            config = json.loads(data.decode('utf-8'))
            
            # 配置响应格式
            if 'response_format' in config:
                self.response_format = config['response_format']
                self.log_with_time(f"设置响应格式: {self.response_format}")
            
            # 配置音频格式
            if 'audio_format' in config:
                audio_format = config['audio_format'].lower()
                if audio_format in [SocketProtocol.AUDIO_FORMAT_MP3, SocketProtocol.AUDIO_FORMAT_PCM]:
                    self.audio_format = audio_format
                    self.log_with_time(f"设置音频格式: {self.audio_format}")
                else:
                    self.log_with_time(f"⚠️ 不支持的音频格式: {audio_format}")
            
            # 配置音频合并模式
            if 'audio_merge' in config:
                audio_merge = config['audio_merge'].lower()
                if audio_merge in [SocketProtocol.AUDIO_MERGE_ENABLED, SocketProtocol.AUDIO_MERGE_DISABLED]:
                    self.audio_merge = audio_merge
                    self.log_with_time(f"设置音频合并模式: {self.audio_merge}")
                else:
                    self.log_with_time(f"⚠️ 不支持的音频合并模式: {audio_merge}")
            
            # 显示当前音频配置
            self.log_with_time(f"🎵 当前音频配置: {self.audio_format.upper()} + {'句子内合并' if self.audio_merge == SocketProtocol.AUDIO_MERGE_ENABLED else '立即发送'}")
            
        except Exception as e:
            self.log_with_time(f"处理配置消息出错: {e}")
    
    async def handle_voice_start(self):
        """处理语音开始"""
        self.voice_id += 1
        self.current_voice_id = self.voice_id
        self.audio_buffer = io.BytesIO()
        self.session_timers[self.current_voice_id] = time.time()
        
        self.log_with_time(f"【对话{self.current_voice_id}计时：开始接收用户语音】0.000s")
    
    async def handle_voice_data(self, data: bytes):
        """处理语音数据"""
        if hasattr(self, 'audio_buffer'):
            self.audio_buffer.write(data)
    
    async def handle_voice_end(self):
        """处理语音结束"""
        if not hasattr(self, 'audio_buffer') or not hasattr(self, 'current_voice_id'):
            return
            
        eof_time = time.time()
        if self.current_voice_id in self.session_timers:
            elapsed = eof_time - self.session_timers[self.current_voice_id]
            self.log_with_time(f"【对话{self.current_voice_id}计时：语音包接收完毕】{elapsed:.3f}s")
        
        # 开始ASR处理
        try:
            asr_start_time = time.time()
            if self.current_voice_id in self.session_timers:
                elapsed = asr_start_time - self.session_timers[self.current_voice_id]
                self.log_with_time(f"【对话{self.current_voice_id}计时：开始语音转文本】{elapsed:.3f}s")
            
            text = await async_process_audio(self.audio_buffer)
            
            asr_end_time = time.time()
            if self.current_voice_id in self.session_timers:
                elapsed = asr_end_time - self.session_timers[self.current_voice_id]
                self.log_with_time(f"【对话{self.current_voice_id}计时：语音转文本结束】{elapsed:.3f}s")
            
            # 处理识别结果
            if text.strip() and not text.startswith("ERROR:"):
                # 根据响应格式处理
                if self.response_format == SocketProtocol.RESPONSE_JSON:
                    await self.handle_json_response(text)
                else:
                    # 流式响应
                    await self.send_text_message(SocketProtocol.MSG_TEXT_DATA, f"USER:{text}")
                    
                    # 添加到处理队列
                    async with self.queue_lock:
                        self.pending_queries.append((self.current_voice_id, text))
                        self.new_query_event.set()
            else:
                await self.send_text_message(SocketProtocol.MSG_ERROR, f"语音识别失败: {text}")
                
        except Exception as e:
            self.log_with_time(f"处理语音时出错: {e}")
            await self.send_text_message(SocketProtocol.MSG_ERROR, f"处理语音时出错: {str(e)}")
    
    async def handle_json_response(self, user_text: str):
        """处理JSON响应模式"""
        try:
            # 收集完整的AI回复
            ai_text_chunks = []
            
            async for chunk in self.conversation_manager.generate_stream(user_text):
                ai_text_chunks.append(chunk)
            
            ai_text = ''.join(ai_text_chunks).strip()
            
            # 发送JSON响应
            response = {
                "code": 0,
                "msg": "ok",
                "session_id": self.current_voice_id,
                "user_text": user_text,
                "ai_text": ai_text
            }
            
            await self.send_json_message(SocketProtocol.MSG_JSON_RESPONSE, response)
            
        except Exception as e:
            error_response = {
                "code": 1,
                "msg": str(e),
                "session_id": self.current_voice_id
            }
            await self.send_json_message(SocketProtocol.MSG_JSON_RESPONSE, error_response)
    
    async def handle_llm_responses(self):
        """处理LLM响应的独立任务"""
        while self.is_connected:
            try:
                # 等待新的查询
                await self.new_query_event.wait()
                
                # 获取查询并处理
                async with self.queue_lock:
                    if not self.pending_queries:
                        self.new_query_event.clear()
                        continue
                    
                    voice_id, text = self.pending_queries.popleft()
                    if not self.pending_queries:
                        self.new_query_event.clear()
                
                # 等待获取信号量
                await self.max_concurrent_tasks.acquire()
                
                # 保存之前的任务以便取消
                async with self.tasks_lock:
                    previous_tasks = self.active_tasks.copy()
                
                # 创建新的文本生成任务
                new_task = asyncio.create_task(
                    self.generate_text_response(text, voice_id, previous_tasks)
                )
                
                # 添加到活跃任务列表
                async with self.tasks_lock:
                    self.active_tasks.append(new_task)
                
                # 添加完成回调
                new_task.add_done_callback(
                    lambda f: asyncio.create_task(self.task_completed_callback(f))
                )
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log_with_time(f"LLM响应处理出错: {e}")
    
    async def generate_text_response(self, text: str, voice_id: int, previous_tasks=None):
        """生成文本响应"""
        try:
            # 发送AI开始信号
            await self.send_text_message(SocketProtocol.MSG_AI_START, "")
            await self.send_text_message(SocketProtocol.MSG_AUDIO_START, "")
            
            # 创建TTS队列和任务
            tts_queue = asyncio.Queue()
            tts_task = asyncio.create_task(
                self.process_tts(tts_queue, voice_id)
            )
            
            # 将TTS任务添加到活跃任务列表
            async with self.tasks_lock:
                self.active_tasks.append(tts_task)
            
            # 生成文本响应
            full_response = ""
            last_tts_length = 0
            first_chunk_generated = False
            first_tts_sent = False
            first_tts_timer = time.time()
            first_text_time = None  # 第一个文本chunk生成时间
            first_tts_text_time = None  # 新增：第一个TTS文本送入TTS队列时间
            async for chunk in self.conversation_manager.generate_stream(text):
                # 记录第一个文本chunk生成完毕的时间点
                if first_text_time is None:
                    first_text_time = time.time()
                    start_time = self.session_timers.get(voice_id, None)
                    if start_time:
                        interval = first_text_time - start_time
                        self.log_with_time(f"【对话{voice_id}计时：第一个文本chunk生成完毕，耗时: {interval:.3f}s】")
                if self.cancel_tasks.is_set():
                    break
                
                # 检查是否需要取消之前的任务（参考AIServer.py的实现）
                if not first_chunk_generated and previous_tasks:
                    first_chunk_generated = True
                    
                    try:
                        # 过滤出真正需要取消的活跃任务
                        active_previous_tasks = [
                            task for task in previous_tasks 
                            if not task.done() and task != asyncio.current_task()
                        ]
                        
                        if active_previous_tasks:
                            self.log_with_time(f"🔄 [INTERRUPT] 检测到{len(active_previous_tasks)}个需要打断的任务 - voice_id={voice_id}")
                            
                            # 先发送取消信号，让客户端知道有新对话开始
                            await self.send_text_message(SocketProtocol.MSG_AI_CANCELLED, "")
                            
                            # 取消之前的任务
                            for task in active_previous_tasks:
                                try:
                                    self.log_with_time(f"🚫 [INTERRUPT] 取消任务 {id(task)} - voice_id={voice_id}")
                                    task.cancel()
                                    # 等待任务完成取消，但不阻塞太久
                                    await asyncio.wait([task], timeout=0.2)
                                except Exception as e:
                                    self.log_with_time(f"⚠️ [INTERRUPT] 取消任务时出错: {e} - voice_id={voice_id}")
                            
                            self.log_with_time(f"✅ [INTERRUPT] 任务打断完成 - voice_id={voice_id}")
                        else:
                            self.log_with_time(f"ℹ️ [INTERRUPT] 没有需要取消的活跃任务 - voice_id={voice_id}")
                            
                    except Exception as e:
                        self.log_with_time(f"❌ [INTERRUPT] 任务切换过程中出错: {e} - voice_id={voice_id}")
                        # 即使出错也要发送取消信号
                        await self.send_text_message(SocketProtocol.MSG_AI_CANCELLED, "")
                
                full_response += chunk
                
                # 发送文本chunk
                await self.send_text_message(SocketProtocol.MSG_TEXT_DATA, chunk)
                # 确保立即发送
                try:
                    await self.writer.drain()
                except Exception as e:
                    self.log_with_time(f"刷新写入缓冲区失败: {e}")
                
                # 关键修复：让出执行权给TTS任务
                await asyncio.sleep(0.001)  # 1ms，让TTS任务有机会处理队列
                
                # 处理TTS
                if len(full_response) > last_tts_length:
                    sentence_end_markers = ['、', ',', '，', '.', '。', '!', '！', '?', '？', ';', '；', '\n']
                    end_pos = -1
                    
                    for marker in sentence_end_markers:
                        pos = full_response.rfind(marker, last_tts_length)
                        if pos > end_pos:
                            end_pos = pos
                    
                    if end_pos > last_tts_length:
                        new_sentence = full_response[last_tts_length:end_pos+1].strip()
                        if new_sentence:
                            if first_tts_text_time is None:
                                first_tts_text_time = time.time()
                                start_time = self.session_timers.get(voice_id, None)
                                if start_time:
                                    interval = first_tts_text_time - start_time
                                    self.log_with_time(f"【对话{voice_id}计时：第一个TTS文本送入TTS队列，耗时: {interval:.3f}s】")
                            await tts_queue.put(new_sentence)
                            last_tts_length = end_pos + 1
                            first_tts_sent = True
                    elif not first_tts_sent and (time.time() - first_tts_timer > 1.0):
                        new_sentence = full_response[last_tts_length:].strip()
                        if new_sentence:
                            if first_tts_text_time is None:
                                first_tts_text_time = time.time()
                                start_time = self.session_timers.get(voice_id, None)
                                if start_time:
                                    interval = first_tts_text_time - start_time
                                    self.log_with_time(f"【对话{voice_id}计时：第一个TTS文本送入TTS队列，耗时: {interval:.3f}s】")
                            await tts_queue.put(new_sentence)
                            last_tts_length = len(full_response)
                            first_tts_sent = True
            
            # 发送剩余文本到TTS
            if last_tts_length < len(full_response):
                remaining_text = full_response[last_tts_length:].strip()
                if remaining_text:
                    self.log_with_time(f"📝 [TXT_GEN] 发送剩余文本到TTS: '{remaining_text}' - voice_id={voice_id}")
                    await tts_queue.put(remaining_text)
                else:
                    self.log_with_time(f"📝 [TXT_GEN] 剩余文本为空，跳过 - voice_id={voice_id}")
            else:
                self.log_with_time(f"📝 [TXT_GEN] 无剩余文本需要TTS - voice_id={voice_id}")
            
            # 结束TTS
            self.log_with_time(f"🔚 [TXT_GEN] 向TTS队列发送__END__信号 - voice_id={voice_id}")
            await tts_queue.put("__END__")
            
            # 等待TTS完成
            self.log_with_time(f"⏳ [TXT_GEN] 等待TTS任务完成 - voice_id={voice_id}")
            try:
                await tts_task
                self.log_with_time(f"✅ [TXT_GEN] TTS任务正常完成 - voice_id={voice_id}")
            except asyncio.CancelledError:
                self.log_with_time(f"⚠️ [TXT_GEN] TTS任务被取消 - voice_id={voice_id}")
                pass
            
            # 发送结束信号
            self.log_with_time(f"📤 [TXT_GEN] 发送MSG_AI_END信号 - voice_id={voice_id}")
            await self.send_text_message(SocketProtocol.MSG_AI_END, "")
            
        except asyncio.CancelledError:
            self.log_with_time(f"⚠️ [TXT_GEN] 文本生成任务被取消 - voice_id={voice_id}")
            # 取消时也要确保TTS任务被正确取消
            if 'tts_task' in locals() and not tts_task.done():
                self.log_with_time(f"🔄 [TXT_GEN] 取消TTS任务 - voice_id={voice_id}")
                tts_task.cancel()
                try:
                    await tts_task
                except asyncio.CancelledError:
                    pass
            raise
        except Exception as e:
            self.log_with_time(f"❌ [TXT_GEN] 生成文本响应时出错: {e} - voice_id={voice_id}")
            await self.send_text_message(SocketProtocol.MSG_ERROR, f"生成响应时出错: {str(e)}")
    
    async def process_tts(self, text_queue: asyncio.Queue, voice_id: int):
        """处理TTS - 根据配置选择不同的音频处理方式"""
        try:
            # 判断处理模式
            if self.audio_merge == SocketProtocol.AUDIO_MERGE_ENABLED:
                await self._process_tts_merged(text_queue, voice_id)
            else:
                await self._process_tts_streaming(text_queue, voice_id)
                
        except asyncio.CancelledError:
            self.log_with_time(f"🚫 [TTS] TTS任务被取消 - voice_id={voice_id}")
            # 清理TTS队列中的残留数据
            self._clear_tts_queue(text_queue)
        except Exception as e:
            self.log_with_time(f"❌ [TTS] TTS处理过程中出错: {e} - voice_id={voice_id}")
    
    def _clear_tts_queue(self, text_queue: asyncio.Queue):
        """清理TTS队列中的残留数据（参考AIServer.py的清理机制）"""
        try:
            cleared_count = 0
            while not text_queue.empty():
                try:
                    text_queue.get_nowait()
                    cleared_count += 1
                except asyncio.QueueEmpty:
                    break
            if cleared_count > 0:
                self.log_with_time(f"🧹 [TTS] 清理了 {cleared_count} 个残留的TTS文本")
        except Exception as e:
            self.log_with_time(f"⚠️ [TTS] 清理TTS队列时出错: {e}")
    
    async def _process_tts_streaming(self, text_queue: asyncio.Queue, voice_id: int):
        """流式TTS处理 - 每个TTS数据包立即发送（原来的方式）"""
        last_session_id = None
        first_tts_time = None
        while True:
            if self.cancel_tasks.is_set():
                self.log_with_time(f"🚫 [TTS_STREAM] 检测到取消信号，退出TTS处理 - voice_id={voice_id}")
                # 发送取消信号给客户端
                try:
                    await self.send_text_message(SocketProtocol.MSG_AUDIO_END, "")
                except Exception as e:
                    self.log_with_time(f"⚠️ [TTS_STREAM] 发送取消信号失败: {e}")
                break
            try:
                text = await asyncio.wait_for(text_queue.get(), timeout=5.0)
                if text == "__END__":
                    break
                if not text or not text.strip():
                    continue
                cleaned_text = text.strip()
                # 检查会话ID变化
                if last_session_id is not None and voice_id != last_session_id:
                    await self.send_text_message(SocketProtocol.MSG_AI_NEWCHAT, "")
                last_session_id = voice_id
                # 生成音频并立即发送每个数据包
                async for audio_chunk in self.tts_service.text_to_speech_stream(cleaned_text):
                    if first_tts_time is None:
                        first_tts_time = time.time()
                        start_time = self.session_timers.get(voice_id, None)
                        if start_time:
                            interval = first_tts_time - start_time
                            self.log_with_time(f"【对话{voice_id}计时：第一个TTS包输出，端到端延迟: {interval:.3f}s】")
                    if self.cancel_tasks.is_set():
                        self.log_with_time(f"🚫 [TTS_STREAM] 音频生成过程中检测到取消信号 - voice_id={voice_id}")
                        break
                    # 根据音频格式处理每个chunk
                    if self.audio_format == SocketProtocol.AUDIO_FORMAT_PCM:
                        # 转换为PCM
                        converted_chunk = await self.convert_mp3_to_pcm(audio_chunk)
                        final_chunk = converted_chunk
                    else:
                        # 保持MP3格式
                        final_chunk = audio_chunk
                    # 立即发送音频数据包（通常每个720字节）
                    await self.send_message(SocketProtocol.MSG_AUDIO_DATA, final_chunk)
                    self.log_with_time(f"🎵 发送音频包: {len(final_chunk)} 字节 ({self.audio_format.upper()})", verbose_only=True)
                # 发送音频包尾标记（表示当前句子结束）
                end_marker = bytes([0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF])
                await self.send_message(SocketProtocol.MSG_AUDIO_DATA, end_marker)
                self.log_with_time(f"🎵 句子音频发送完成: '{cleaned_text}'")
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                self.log_with_time(f"流式TTS处理出错: {e}")
                continue
        # 发送音频结束信号
        await self.send_text_message(SocketProtocol.MSG_AUDIO_END, "")
    
    async def _process_tts_merged(self, text_queue: asyncio.Queue, voice_id: int):
        """合并TTS处理 - 将每个句子的多个数据包合并成一个包发送"""
        last_session_id = None
        sentence_count = 0
        self.log_with_time(f"🚀 [TTS_MERGE] TTS合并处理开始 - voice_id={voice_id}")
        first_tts_time = None
        while True:
            if self.cancel_tasks.is_set():
                self.log_with_time(f"🚫 [TTS_MERGE] 检测到取消信号，退出TTS处理 - voice_id={voice_id}")
                # 发送取消信号给客户端
                try:
                    await self.send_text_message(SocketProtocol.MSG_AUDIO_END, "")
                except Exception as e:
                    self.log_with_time(f"⚠️ [TTS_MERGE] 发送取消信号失败: {e}")
                break
            try:
                self.log_with_time(f"⏳ [TTS_MERGE] 等待队列中的文本... - voice_id={voice_id}")
                text = await asyncio.wait_for(text_queue.get(), timeout=5.0)
                self.log_with_time(f"📥 [TTS_MERGE] 从队列获取文本: '{text[:20]}...' - voice_id={voice_id}")
                if text == "__END__":
                    self.log_with_time(f"🔚 [TTS_MERGE] 收到结束信号，准备退出 - voice_id={voice_id}")
                    break
                if not text or not text.strip():
                    continue
                cleaned_text = text.strip()
                sentence_count += 1
                self.log_with_time(f"🎵 [TTS_MERGE] 开始生成第{sentence_count}个句子的音频 - voice_id={voice_id}")
                # 检查会话ID变化
                if last_session_id is not None and voice_id != last_session_id:
                    await self.send_text_message(SocketProtocol.MSG_AI_NEWCHAT, "")
                last_session_id = voice_id
                # 收集当前句子的所有TTS数据包
                sentence_chunks = []
                print(f"🎵 [TTS_MERGE] 句子内容 - cleaned_text={cleaned_text}")
                async for audio_chunk in self.tts_service.text_to_speech_stream(cleaned_text):
                    if first_tts_time is None:
                        first_tts_time = time.time()
                        start_time = self.session_timers.get(voice_id, None)
                        if start_time:
                            interval = first_tts_time - start_time
                            self.log_with_time(f"【对话{voice_id}计时：第一个TTS包输出，端到端延迟: {interval:.3f}s】")
                    if self.cancel_tasks.is_set():
                        self.log_with_time(f"🚫 [TTS_MERGE] 音频收集过程中检测到取消信号 - voice_id={voice_id}")
                        break
                    sentence_chunks.append(audio_chunk)
                    self.log_with_time(f"🎵 收集TTS包: {len(audio_chunk)} 字节", verbose_only=True)
                # 将当前句子的所有chunk合并为一个包
                if sentence_chunks:
                    merged_sentence_data = b''.join(sentence_chunks)
                    
                                    # 根据音频格式处理合并后的数据
                self.log_with_time(f"🔄 [TTS_MERGE] 合并数据大小: {len(merged_sentence_data)} 字节 - voice_id={voice_id}")
                if self.audio_format == SocketProtocol.AUDIO_FORMAT_PCM:
                    # 转换为PCM
                    self.log_with_time(f"🔄 [TTS_MERGE] 开始MP3转PCM - voice_id={voice_id}")
                    converted_data = await self.convert_mp3_to_pcm(merged_sentence_data)
                    final_data = converted_data
                    self.log_with_time(f"✅ [TTS_MERGE] MP3转PCM完成，PCM大小: {len(final_data)} 字节 - voice_id={voice_id}")
                else:
                    # 保持MP3格式
                    final_data = merged_sentence_data
                    self.log_with_time(f"✅ [TTS_MERGE] 保持MP3格式，大小: {len(final_data)} 字节 - voice_id={voice_id}")
                
                # 发送合并后的句子音频数据
                self.log_with_time(f"📤 [TTS_MERGE] 发送句子音频数据 - voice_id={voice_id}")
                await self.send_message(SocketProtocol.MSG_AUDIO_DATA, final_data)
                
                # 发送音频包尾标记（表示当前句子结束）
                end_marker = bytes([0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF])
                self.log_with_time(f"📤 [TTS_MERGE] 发送句子结束标记 - voice_id={voice_id}")
                await self.send_message(SocketProtocol.MSG_AUDIO_DATA, end_marker)
                
                self.log_with_time(f"🎵 发送合并句子: '{cleaned_text}' -> {len(sentence_chunks)}个包合并为{len(final_data)}字节 ({self.audio_format.upper()})")
                
            except asyncio.TimeoutError:
                self.log_with_time(f"⏰ [TTS_MERGE] 等待队列超时，继续循环 - voice_id={voice_id}")
                continue
            except Exception as e:
                self.log_with_time(f"❌ [TTS_MERGE] 合并TTS处理出错: {e} - voice_id={voice_id}")
                continue
        
        # 发送音频结束信号
        self.log_with_time(f"📤 [TTS_MERGE] 发送MSG_AUDIO_END信号 - voice_id={voice_id}")
        await self.send_text_message(SocketProtocol.MSG_AUDIO_END, "")
        self.log_with_time(f"🏁 [TTS_MERGE] TTS合并处理完成 - voice_id={voice_id}")
    
    async def task_completed_callback(self, future):
        """任务完成回调"""
        # 释放信号量
        self.max_concurrent_tasks.release()
        
        # 从活跃任务列表中移除
        async with self.tasks_lock:
            if future in self.active_tasks:
                self.active_tasks.remove(future)


class AISocketServer:
    """AI Socket服务器"""
    
    def __init__(self, host='192.168.14.129', port=7860, 
                 default_audio_format='mp3', default_audio_merge='disabled',
                 verbose=False):
        self.host = host
        self.port = port
        self.clients = {}
        self.server = None
        
        # 默认音频配置
        self.default_audio_format = default_audio_format
        self.default_audio_merge = default_audio_merge
        self.verbose = verbose
    
    def log_with_time(self, message: str):
        """输出带时间戳的日志"""
        current_time = datetime.datetime.now().strftime("[%H:%M:%S.%f]")[:-3]  # 精确到毫秒
        print(f"{current_time} [SERVER] {message}")
    
    async def handle_client_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """处理新的客户端连接"""
        # 获取客户端信息
        client_addr = writer.get_extra_info('peername')
        client_id = f"{client_addr[0]}:{client_addr[1]}"
        local_addr = writer.get_extra_info('sockname')
        
        self.log_with_time(f"🔗 新连接: {client_id} -> {local_addr[0]}:{local_addr[1]}")
        
        # 检查连接状态（不读取数据，避免破坏协议）
        if writer.is_closing():
            self.log_with_time(f"⚠️ 连接 {client_id} 已在关闭状态")
            return
            
        self.log_with_time(f"✅ 连接 {client_id} 状态正常")
        
        try:
            self.log_with_time(f"🔄 为客户端 {client_id} 创建处理器...")
            # 创建客户端处理器
            client = AISocketClient(reader, writer, client_addr, 
                                   default_audio_format=self.default_audio_format,
                                   default_audio_merge=self.default_audio_merge,
                                   verbose=self.verbose)
            self.clients[client_id] = client
            self.log_with_time(f"✅ 客户端 {client_id} 处理器创建成功")
            
            self.log_with_time(f"🔄 开始处理客户端 {client_id} 的请求...")
            await client.handle_client()
            
        except Exception as e:
            self.log_with_time(f"❌ 处理客户端 {client_id} 时出错: {e}")
            import traceback
            self.log_with_time(f"❌ 详细错误: {traceback.format_exc()}")
        finally:
            # 清理客户端
            if client_id in self.clients:
                del self.clients[client_id]
                self.log_with_time(f"🧹 客户端 {client_id} 已清理")
    
    async def start_server(self):
        """启动服务器"""
        self.log_with_time(f"🔄 开始启动AI Socket服务器 {self.host}:{self.port}")
        
        try:
            self.log_with_time("🔄 创建服务器socket...")
            self.server = await asyncio.start_server(
                self.handle_client_connection,
                self.host,
                self.port
            )
            self.log_with_time("✅ 服务器socket创建成功")
            
            addr = self.server.sockets[0].getsockname()
            self.log_with_time(f"✅ 服务器已启动，监听 {addr[0]}:{addr[1]}")
            self.log_with_time("🔄 开始等待客户端连接...")
            
            async with self.server:
                await self.server.serve_forever()
                
        except Exception as e:
            self.log_with_time(f"❌ 服务器启动失败: {e}")
            import traceback
            self.log_with_time(f"❌ 详细错误: {traceback.format_exc()}")
            raise
    
    async def stop_server(self):
        """停止服务器"""
        if self.server:
            self.log_with_time("正在停止服务器...")
            
            # 断开所有客户端连接
            for client in list(self.clients.values()):
                await client.disconnect()
            
            # 关闭服务器
            self.server.close()
            await self.server.wait_closed()
            
            self.log_with_time("服务器已停止")


async def main():
    """主函数"""
    import argparse
    import datetime
    
    def log_main(message: str):
        current_time = datetime.datetime.now().strftime("[%H:%M:%S.%f]")[:-3]
        print(f"{current_time} [MAIN] {message}")
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='AI Socket服务器 - 支持ASR-LLM-TTS流水线')
    parser.add_argument('--host', default='0.0.0.0', help='服务器监听地址 (默认: 0.0.0.0)')
    parser.add_argument('--port', type=int, default=7860, help='服务器监听端口 (默认: 7860)')
    parser.add_argument('--audio-format', 
                        choices=['mp3', 'pcm'], 
                        default='mp3',
                        help='默认音频格式 (默认: mp3)')
    parser.add_argument('--audio-merge',
                        choices=['enabled', 'disabled'],
                        default='disabled',
                        help='句子内TTS包合并模式: enabled=合并成一个包发送, disabled=立即发送每个包 (默认: disabled)')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细日志输出')
    
    args = parser.parse_args()
    
    log_main("🚀 启动AI Socket服务器...")
    log_main(f"📡 监听地址: {args.host}:{args.port}")
    log_main(f"🎵 默认音频格式: {args.audio_format.upper()}")
    log_main(f"📦 TTS包处理: {'句子内合并' if args.audio_merge == 'enabled' else '立即发送'}")
    
    # 初始化服务
    log_main("🔄 会话初始化...")
    
    # 创建并启动服务器
    log_main("🔄 创建服务器实例...")
    server = AISocketServer(host=args.host, port=args.port, 
                           default_audio_format=args.audio_format,
                           default_audio_merge=args.audio_merge,
                           verbose=args.verbose)
    log_main("✅ 服务器实例创建成功")
    
    try:
        log_main("🔄 启动服务器...")
        await server.start_server()
    except KeyboardInterrupt:
        log_main("⚠️ 收到中断信号，正在关闭服务器...")
        await server.stop_server()
    except Exception as e:
        log_main(f"❌ 服务器运行出错: {e}")
        import traceback
        log_main(f"❌ 详细错误: {traceback.format_exc()}")


if __name__ == "__main__":
    asyncio.run(main()) 
