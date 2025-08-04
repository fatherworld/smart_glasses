#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强版模拟AI服务器 - 支持音频播放、保存和转发
基于 ai_client_start_stop2.c 协议

新增功能：
1. 播放客户端发送的音频
2. 保存音频到文件
3. 发送保存的音频回客户端
4. 支持多种音频格式
5. 实时音频可视化
"""

import socket
import struct
import threading
import time
import sys
import signal
import logging
import os
import json
import wave
import audioop
import pyaudio
from datetime import datetime
from pathlib import Path
import numpy as np

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s.%(msecs)03d] [SERVER] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# 消息类型定义（与客户端保持一致）
MSG_VOICE_START = 0x01
MSG_VOICE_DATA = 0x02
MSG_VOICE_END = 0x03
MSG_TEXT_DATA = 0x04
MSG_AUDIO_DATA = 0x05
MSG_AI_START = 0x06
MSG_AI_END = 0x07
MSG_AUDIO_START = 0x08
MSG_AUDIO_END = 0x09
MSG_ERROR = 0x0A
MSG_AI_CANCELLED = 0x0B
MSG_JSON_RESPONSE = 0x0C
MSG_CONFIG = 0x0D
MSG_AI_NEWCHAT = 0x0E

# 音频参数配置
AUDIO_CONFIG = {
    'sample_rate': 16000,
    'channels': 1,
    'sample_width': 2,  # 16-bit PCM
    'chunk_size': 1024
}

class AudioManager:
    """音频管理器 - 处理音频播放、录制和保存"""
    
    def __init__(self):
        self.pyaudio = pyaudio.PyAudio()
        self.audio_data_buffer = []
        self.is_recording = False
        self.is_playing = False
        self.audio_files_dir = Path("received_audio")
        self.audio_files_dir.mkdir(exist_ok=True)
        
    def start_recording(self):
        """开始录制音频数据"""
        self.audio_data_buffer = []
        self.is_recording = True
        logger.info("🎤 开始录制音频数据")
        
    def add_audio_data(self, data):
        """添加音频数据"""
        if self.is_recording:
            self.audio_data_buffer.append(data)
            # 显示实时音频电平
            try:
                if len(data) >= 2:
                    # 计算音频电平（RMS）
                    audio_samples = np.frombuffer(data, dtype=np.int16)
                    rms = np.sqrt(np.mean(audio_samples**2))
                    level = min(int(rms / 1000), 20)  # 缩放到0-20
                    bar = "█" * level + "░" * (20 - level)
                    logger.info(f"🎵 音频电平: [{bar}] {rms:.0f}")
            except Exception as e:
                pass
                
    def stop_recording(self):
        """停止录制并保存音频"""
        if not self.is_recording:
            return None
            
        self.is_recording = False
        
        if not self.audio_data_buffer:
            logger.warning("⚠️ 没有录制到音频数据")
            return None
            
        # 合并所有音频数据
        audio_data = b''.join(self.audio_data_buffer)
        total_samples = len(audio_data) // 2
        duration = total_samples / AUDIO_CONFIG['sample_rate']
        
        logger.info(f"🎤 录制完成: {len(audio_data)}字节, {duration:.2f}秒, {total_samples}采样点")
        
        # 保存到WAV文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"received_audio_{timestamp}.wav"
        filepath = self.audio_files_dir / filename
        
        try:
            with wave.open(str(filepath), 'wb') as wav_file:
                wav_file.setnchannels(AUDIO_CONFIG['channels'])
                wav_file.setsampwidth(AUDIO_CONFIG['sample_width'])
                wav_file.setframerate(AUDIO_CONFIG['sample_rate'])
                wav_file.writeframes(audio_data)
            
            logger.info(f"💾 音频已保存: {filepath}")
            
            # 播放录制的音频
            self.play_audio_data(audio_data)
            
            return str(filepath)
            
        except Exception as e:
            logger.error(f"❌ 保存音频失败: {e}")
            return None
    
    def play_audio_data(self, audio_data):
        """播放音频数据"""
        if self.is_playing:
            logger.warning("⚠️ 音频正在播放中，跳过")
            return
            
        def play_thread():
            try:
                self.is_playing = True
                logger.info("🔊 开始播放音频...")
                
                stream = self.pyaudio.open(
                    format=pyaudio.paInt16,
                    channels=AUDIO_CONFIG['channels'],
                    rate=AUDIO_CONFIG['sample_rate'],
                    output=True,
                    frames_per_buffer=AUDIO_CONFIG['chunk_size']
                )
                
                # 分块播放
                chunk_size = AUDIO_CONFIG['chunk_size'] * AUDIO_CONFIG['sample_width']
                for i in range(0, len(audio_data), chunk_size):
                    chunk = audio_data[i:i + chunk_size]
                    stream.write(chunk)
                    time.sleep(0.01)  # 小延时避免音频卡顿
                
                stream.stop_stream()
                stream.close()
                
                logger.info("✅ 音频播放完成")
                
            except Exception as e:
                logger.error(f"❌ 音频播放失败: {e}")
            finally:
                self.is_playing = False
        
        threading.Thread(target=play_thread, daemon=True).start()
    
    def load_audio_file(self, filepath):
        """加载音频文件"""
        try:
            with wave.open(filepath, 'rb') as wav_file:
                # 检查音频格式
                channels = wav_file.getnchannels()
                sample_width = wav_file.getsampwidth()
                framerate = wav_file.getframerate()
                
                logger.info(f"📂 加载音频: {filepath}")
                logger.info(f"   格式: {channels}ch, {sample_width*8}bit, {framerate}Hz")
                
                audio_data = wav_file.readframes(wav_file.getnframes())
                
                # 转换为目标格式（如果需要）
                if channels != AUDIO_CONFIG['channels']:
                    logger.info(f"🔄 转换声道: {channels} -> {AUDIO_CONFIG['channels']}")
                    if channels == 2 and AUDIO_CONFIG['channels'] == 1:
                        audio_data = audioop.tomono(audio_data, sample_width, 1, 1)
                    elif channels == 1 and AUDIO_CONFIG['channels'] == 2:
                        audio_data = audioop.tostereo(audio_data, sample_width, 1, 1)
                
                if framerate != AUDIO_CONFIG['sample_rate']:
                    logger.info(f"🔄 重采样: {framerate}Hz -> {AUDIO_CONFIG['sample_rate']}Hz")
                    audio_data, _ = audioop.ratecv(
                        audio_data, sample_width, AUDIO_CONFIG['channels'],
                        framerate, AUDIO_CONFIG['sample_rate'], None
                    )
                
                return audio_data
                
        except Exception as e:
            logger.error(f"❌ 加载音频文件失败: {e}")
            return None
    
    def get_audio_files(self):
        """获取所有保存的音频文件列表"""
        try:
            files = list(self.audio_files_dir.glob("*.wav"))
            return sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)
        except Exception as e:
            logger.error(f"❌ 获取音频文件列表失败: {e}")
            return []
    
    def cleanup(self):
        """清理资源"""
        try:
            self.pyaudio.terminate()
        except:
            pass

class EnhancedMockAIServer:
    def __init__(self, host='0.0.0.0', port=8082):
        self.host = host
        self.port = port
        self.socket = None
        self.client_connections = []
        self.running = False
        self.audio_manager = AudioManager()
        
    def send_message(self, conn, msg_type, data=b''):
        """发送消息到客户端"""
        try:
            # 构建消息头：消息类型(1字节) + 数据长度(4字节，网络字节序)
            if isinstance(data, str):
                data = data.encode('utf-8')
            
            data_len = len(data)
            header = struct.pack('!BI', msg_type, data_len)  # ! = 网络字节序, B = 1字节, I = 4字节
            
            # 发送消息头
            conn.sendall(header)
            logger.info(f"📤 发送消息头: 类型=0x{msg_type:02X}, 长度={data_len}")
            
            # 发送数据（如果有的话）
            if data_len > 0:
                conn.sendall(data)
                if msg_type == MSG_AUDIO_DATA:
                    logger.info(f"📤 发送音频数据: {data_len}字节")
                else:
                    display_data = data[:50]
                    if isinstance(display_data, bytes):
                        try:
                            display_data = display_data.decode('utf-8')
                        except:
                            display_data = f"<binary:{len(data)} bytes>"
                    logger.info(f"📤 发送数据: {display_data}{'...' if len(data) > 50 else ''}")
                
            return True
        except Exception as e:
            logger.error(f"❌ 发送消息失败: {e}")
            return False
    
    def receive_message(self, conn):
        """接收客户端消息"""
        try:
            # 接收消息头（5字节）
            header_data = b''
            while len(header_data) < 5:
                chunk = conn.recv(5 - len(header_data))
                if not chunk:
                    logger.warning("⚠️ 客户端关闭连接")
                    return None, None
                header_data += chunk
            
            # 解析消息头
            msg_type, data_len = struct.unpack('!BI', header_data)
            logger.info(f"📥 接收消息头: 类型=0x{msg_type:02X}, 长度={data_len}")
            
            # 接收数据
            data = b''
            if data_len > 0:
                while len(data) < data_len:
                    chunk = conn.recv(data_len - len(data))
                    if not chunk:
                        logger.warning("⚠️ 数据接收中断")
                        return None, None
                    data += chunk
                
                if msg_type == MSG_VOICE_DATA:
                    logger.info(f"📥 接收语音数据: {len(data)}字节")
                else:
                    display_data = data[:50]
                    if isinstance(display_data, bytes):
                        try:
                            display_data = display_data.decode('utf-8')
                        except:
                            display_data = f"<binary:{len(data)} bytes>"
                    logger.info(f"📥 接收数据: {display_data}{'...' if len(data) > 50 else ''}")
            
            return msg_type, data
        except Exception as e:
            logger.error(f"❌ 接收消息失败: {e}")
            return None, None
    
    def handle_config_message(self, conn, data):
        """处理配置消息"""
        try:
            config_str = data.decode('utf-8')
            logger.info(f"🔧 接收到配置: {config_str}")
            return True
        except Exception as e:
            logger.error(f"❌ 处理配置失败: {e}")
            return False
    
    def handle_voice_data(self, conn):
        """处理语音数据流"""
        logger.info("🎤 开始接收语音数据...")
        self.audio_manager.start_recording()
        voice_data_received = 0
        
        while True:
            msg_type, data = self.receive_message(conn)
            if msg_type is None:
                return False
                
            if msg_type == MSG_VOICE_DATA:
                voice_data_received += len(data)
                self.audio_manager.add_audio_data(data)
                
                if voice_data_received % 8192 == 0:
                    logger.info(f"🎤 已接收语音数据: {voice_data_received} 字节")
                    
            elif msg_type == MSG_VOICE_END:
                logger.info(f"🎤 语音接收完成: 总计 {voice_data_received} 字节")
                # 停止录制并保存音频
                saved_file = self.audio_manager.stop_recording()
                return saved_file is not None
            else:
                logger.warning(f"⚠️ 语音接收过程中收到意外消息: 0x{msg_type:02X}")
    
    def send_audio_file_to_client(self, conn, audio_file_path):
        """发送音频文件到客户端"""
        try:
            audio_data = self.audio_manager.load_audio_file(audio_file_path)
            if not audio_data:
                return False
            
            logger.info(f"🔊 开始发送音频文件: {audio_file_path}")
            
            # 发送音频开始标记
            self.send_message(conn, MSG_AUDIO_START)
            
            # 分块发送音频数据
            chunk_size = 4096  # 4KB块大小
            total_chunks = (len(audio_data) + chunk_size - 1) // chunk_size
            
            for i in range(0, len(audio_data), chunk_size):
                chunk = audio_data[i:i + chunk_size]
                if not self.send_message(conn, MSG_AUDIO_DATA, chunk):
                    logger.error("❌ 发送音频数据失败")
                    return False
                
                # 显示进度
                chunk_num = i // chunk_size + 1
                if chunk_num % 10 == 0 or chunk_num == total_chunks:
                    progress = (chunk_num * 100) // total_chunks
                    logger.info(f"📤 发送进度: {progress}% ({chunk_num}/{total_chunks})")
                
                time.sleep(0.01)  # 小延时避免网络拥塞
            
            # 发送音频结束标记
            audio_end_marker = b'\x00\x00\x00\x00\xFF\xFF\xFF\xFF'
            self.send_message(conn, MSG_AUDIO_DATA, audio_end_marker)
            self.send_message(conn, MSG_AUDIO_END)
            
            logger.info("✅ 音频文件发送完成")
            return True
            
        except Exception as e:
            logger.error(f"❌ 发送音频文件失败: {e}")
            return False
    
    def simulate_ai_response(self, conn, response_format='json', audio_file=None):
        """模拟AI响应"""
        logger.info(f"🤖 开始模拟AI响应 (格式: {response_format})")
        
        # 发送AI开始信号
        if not self.send_message(conn, MSG_AI_START):
            return False
        
        if response_format == 'stream':
            # 流式响应：文本 + 音频
            logger.info("📝 发送文本响应...")
            self.send_message(conn, MSG_TEXT_DATA, "我已经收到了您的音频，正在为您播放和处理。")
            
            # 发送音频响应
            if audio_file and os.path.exists(audio_file):
                logger.info(f"🔊 发送录制的音频: {audio_file}")
                self.send_audio_file_to_client(conn, audio_file)
            else:
                # 发送默认音频响应
                logger.info("🔊 发送默认音频响应...")
                self.send_message(conn, MSG_AUDIO_START)
                
                # 发送模拟音频数据（几个小包）
                for i in range(5):
                    # 生成模拟音频数据（PCM格式）
                    audio_data = b'\x00\x00' * 1024  # 1024个16位采样点的静音数据
                    self.send_message(conn, MSG_AUDIO_DATA, audio_data)
                    time.sleep(0.1)  # 模拟音频生成延迟
                
                # 发送音频结束标记
                audio_end_marker = b'\x00\x00\x00\x00\xFF\xFF\xFF\xFF'
                self.send_message(conn, MSG_AUDIO_DATA, audio_end_marker)
                self.send_message(conn, MSG_AUDIO_END)
            
        else:
            # JSON响应
            json_response = {
                "response": "我已经收到并保存了您的音频文件。",
                "timestamp": datetime.now().isoformat(),
                "status": "success",
                "audio_file": audio_file if audio_file else None
            }
            self.send_message(conn, MSG_JSON_RESPONSE, json.dumps(json_response, ensure_ascii=False))
        
        # 发送AI结束信号
        self.send_message(conn, MSG_AI_END)
        logger.info("🤖 AI响应完成")
        return True
    
    def handle_client_connection(self, conn, addr):
        """处理单个客户端连接"""
        logger.info(f"🔗 新客户端连接: {addr}")
        self.client_connections.append(conn)
        
        try:
            response_format = 'json'  # 默认格式
            saved_audio_file = None
            
            while self.running:
                msg_type, data = self.receive_message(conn)
                if msg_type is None:
                    break
                    
                if msg_type == MSG_CONFIG:
                    # 处理配置消息
                    if self.handle_config_message(conn, data):
                        try:
                            config = json.loads(data.decode('utf-8'))
                            response_format = config.get('response_format', 'json')
                            logger.info(f"🔧 设置响应格式: {response_format}")
                        except:
                            pass
                    
                elif msg_type == MSG_VOICE_START:
                    # 处理语音开始
                    logger.info("🎤 语音传输开始")
                    success = self.handle_voice_data(conn)
                    if success:
                        # 获取最新保存的音频文件
                        audio_files = self.audio_manager.get_audio_files()
                        if audio_files:
                            saved_audio_file = str(audio_files[0])
                        
                        # 语音接收完成，开始AI响应
                        self.simulate_ai_response(conn, response_format, saved_audio_file)
                    
                else:
                    logger.warning(f"⚠️ 收到未处理的消息类型: 0x{msg_type:02X}")
                    
        except Exception as e:
            logger.error(f"❌ 处理客户端连接出错: {e}")
        finally:
            logger.info(f"🔚 客户端断开连接: {addr}")
            if conn in self.client_connections:
                self.client_connections.remove(conn)
            conn.close()
    
    def interactive_control_thread(self):
        """交互控制线程 - 支持多种命令"""
        logger.info("🎮 交互控制线程启动")
        logger.info("💡 可用命令:")
        logger.info("   start - 发送开始录音指令")
        logger.info("   stop - 发送结束录音指令")
        logger.info("   list - 显示保存的音频文件")
        logger.info("   play <文件名> - 播放指定音频文件")
        logger.info("   send <文件名> - 发送音频文件到客户端")
        logger.info("   status - 显示服务器状态")
        logger.info("   quit - 退出服务器")
        
        while self.running:
            try:
                if not self.client_connections:
                    time.sleep(1)
                    continue
                    
                # 从标准输入读取命令
                sys.stdout.write("\n> ")
                sys.stdout.flush()
                
                # 使用select来非阻塞读取输入
                import select
                if select.select([sys.stdin], [], [], 1.0)[0]:
                    command_line = sys.stdin.readline().strip()
                    if not command_line:
                        continue
                        
                    parts = command_line.split()
                    command = parts[0].lower()
                    
                    if command == 'start':
                        logger.info("🎤 发送开始录音指令...")
                        for conn in self.client_connections[:]:
                            try:
                                self.send_message(conn, MSG_TEXT_DATA, "开始录音")
                            except:
                                logger.error("❌ 发送开始录音指令失败")
                                
                    elif command == 'stop':
                        logger.info("🛑 发送结束录音指令...")
                        for conn in self.client_connections[:]:
                            try:
                                self.send_message(conn, MSG_TEXT_DATA, "结束录音")
                            except:
                                logger.error("❌ 发送结束录音指令失败")
                    
                    elif command == 'list':
                        audio_files = self.audio_manager.get_audio_files()
                        if audio_files:
                            logger.info("📁 保存的音频文件:")
                            for i, file in enumerate(audio_files, 1):
                                size = file.stat().st_size
                                mtime = datetime.fromtimestamp(file.stat().st_mtime)
                                logger.info(f"   {i}. {file.name} ({size}字节, {mtime.strftime('%Y-%m-%d %H:%M:%S')})")
                        else:
                            logger.info("📁 没有保存的音频文件")
                    
                    elif command == 'play':
                        if len(parts) < 2:
                            logger.info("💡 用法: play <文件名>")
                        else:
                            filename = ' '.join(parts[1:])
                            filepath = self.audio_manager.audio_files_dir / filename
                            if filepath.exists():
                                audio_data = self.audio_manager.load_audio_file(str(filepath))
                                if audio_data:
                                    self.audio_manager.play_audio_data(audio_data)
                                else:
                                    logger.error(f"❌ 无法加载音频文件: {filename}")
                            else:
                                logger.error(f"❌ 文件不存在: {filename}")
                    
                    elif command == 'send':
                        if len(parts) < 2:
                            logger.info("💡 用法: send <文件名>")
                        else:
                            filename = ' '.join(parts[1:])
                            filepath = self.audio_manager.audio_files_dir / filename
                            if filepath.exists():
                                logger.info(f"📤 发送音频文件到所有客户端: {filename}")
                                for conn in self.client_connections[:]:
                                    try:
                                        # 先发送AI开始信号
                                        self.send_message(conn, MSG_AI_START)
                                        # 发送音频文件
                                        self.send_audio_file_to_client(conn, str(filepath))
                                        # 发送AI结束信号
                                        self.send_message(conn, MSG_AI_END)
                                    except Exception as e:
                                        logger.error(f"❌ 发送音频文件失败: {e}")
                            else:
                                logger.error(f"❌ 文件不存在: {filename}")
                    
                    elif command == 'status':
                        logger.info(f"📊 服务器状态:")
                        logger.info(f"   连接的客户端: {len(self.client_connections)}")
                        logger.info(f"   音频文件数量: {len(self.audio_manager.get_audio_files())}")
                        logger.info(f"   正在播放: {self.audio_manager.is_playing}")
                        logger.info(f"   正在录制: {self.audio_manager.is_recording}")
                        
                    elif command in ['quit', 'q', 'exit']:
                        logger.info("👋 收到退出指令")
                        self.running = False
                        break
                        
                    else:
                        logger.info("💡 未知命令，输入help查看可用命令")
                        
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"❌ 交互控制线程出错: {e}")
    
    def start(self):
        """启动服务器"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(5)
            self.running = True
            
            logger.info(f"🚀 增强版模拟AI服务器启动成功")
            logger.info(f"📡 监听地址: {self.host}:{self.port}")
            logger.info(f"💾 音频保存目录: {self.audio_manager.audio_files_dir}")
            logger.info("=" * 60)
            
            # 启动交互控制线程
            control_thread = threading.Thread(target=self.interactive_control_thread, daemon=True)
            control_thread.start()
            
            while self.running:
                try:
                    self.socket.settimeout(1.0)  # 设置超时，让主循环可以检查running状态
                    conn, addr = self.socket.accept()
                    
                    # 为每个客户端创建单独的线程
                    client_thread = threading.Thread(
                        target=self.handle_client_connection,
                        args=(conn, addr),
                        daemon=True
                    )
                    client_thread.start()
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.running:
                        logger.error(f"❌ 接受连接失败: {e}")
                    
        except Exception as e:
            logger.error(f"❌ 服务器启动失败: {e}")
        finally:
            self.stop()
    
    def stop(self):
        """停止服务器"""
        logger.info("🔄 正在停止服务器...")
        self.running = False
        
        # 关闭所有客户端连接
        for conn in self.client_connections[:]:
            try:
                conn.close()
            except:
                pass
        self.client_connections.clear()
        
        # 关闭服务器socket
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        
        # 清理音频资源
        self.audio_manager.cleanup()
            
        logger.info("✅ 服务器已停止")

def signal_handler(signum, frame):
    """信号处理函数"""
    logger.info("\n🛑 收到停止信号，正在关闭服务器...")
    server.stop()
    sys.exit(0)

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='增强版模拟AI服务器 - 支持音频播放、保存和转发')
    parser.add_argument('--host', default='0.0.0.0', help='服务器监听地址')
    parser.add_argument('--port', type=int, default=8082, help='服务器监听端口')
    
    args = parser.parse_args()
    
    # 检查依赖
    try:
        import pyaudio
        import numpy as np
    except ImportError as e:
        logger.error("❌ 缺少必要的依赖包，请安装:")
        logger.error("   pip install pyaudio numpy")
        sys.exit(1)
    
    # 创建服务器实例
    server = EnhancedMockAIServer(args.host, args.port)
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("\n👋 收到中断信号")
    finally:
        server.stop() 