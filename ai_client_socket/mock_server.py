#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟AI服务器 - 用于调试 ai_client_start_stop.c 客户端
解决 GPIO 模式下 recv 断开的问题

消息协议格式：
- 消息头：5字节
  - 1字节：消息类型
  - 4字节：数据长度（网络字节序，大端）
- 消息体：具体数据
"""

import socket
import struct
import threading
import time
import sys
import signal
import logging
from datetime import datetime

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

class MockAIServer:
    def __init__(self, host='0.0.0.0', port=8082):
        self.host = host
        self.port = port
        self.socket = None
        self.client_connections = []
        self.running = False
        
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
                logger.info(f"📤 发送数据: {data[:50]}{'...' if len(data) > 50 else ''}")
                
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
                logger.info(f"📥 接收数据: {data[:50]}{'...' if len(data) > 50 else ''}")
            
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
        voice_data_received = 0
        
        while True:
            msg_type, data = self.receive_message(conn)
            if msg_type is None:
                return False
                
            if msg_type == MSG_VOICE_DATA:
                voice_data_received += len(data)
                if voice_data_received % 8192 == 0:
                    logger.info(f"🎤 已接收语音数据: {voice_data_received} 字节")
                    
            elif msg_type == MSG_VOICE_END:
                logger.info(f"🎤 语音接收完成: 总计 {voice_data_received} 字节")
                return True
            else:
                logger.warning(f"⚠️ 语音接收过程中收到意外消息: 0x{msg_type:02X}")
    
    def simulate_ai_response(self, conn, response_format='json'):
        """模拟AI响应"""
        logger.info(f"🤖 开始模拟AI响应 (格式: {response_format})")
        
        # 发送AI开始信号
        if not self.send_message(conn, MSG_AI_START):
            return False
        
        if response_format == 'stream':
            # 流式响应：文本 + 音频
            logger.info("📝 发送文本响应...")
            self.send_message(conn, MSG_TEXT_DATA, "你好！我是AI助手，很高兴为您服务。")
            
            # 模拟音频响应
            logger.info("🔊 发送音频响应...")
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
                "response": "你好！我是AI助手。",
                "timestamp": datetime.now().isoformat(),
                "status": "success"
            }
            import json
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
            
            while self.running:
                msg_type, data = self.receive_message(conn)
                if msg_type is None:
                    break
                    
                if msg_type == MSG_CONFIG:
                    # 处理配置消息
                    if self.handle_config_message(conn, data):
                        try:
                            import json
                            config = json.loads(data.decode('utf-8'))
                            response_format = config.get('response_format', 'json')
                            logger.info(f"🔧 设置响应格式: {response_format}")
                        except:
                            pass
                    
                elif msg_type == MSG_VOICE_START:
                    # 处理语音开始
                    logger.info("🎤 语音传输开始")
                    if self.handle_voice_data(conn):
                        # 语音接收完成，开始AI响应
                        self.simulate_ai_response(conn, response_format)
                    
                else:
                    logger.warning(f"⚠️ 收到未处理的消息类型: 0x{msg_type:02X}")
                    
        except Exception as e:
            logger.error(f"❌ 处理客户端连接出错: {e}")
        finally:
            logger.info(f"🔚 客户端断开连接: {addr}")
            if conn in self.client_connections:
                self.client_connections.remove(conn)
            conn.close()
    
    def gpio_control_thread(self):
        """GPIO控制线程 - 模拟发送开始/结束录音指令"""
        logger.info("🎮 GPIO控制线程启动 - 可以发送录音控制指令")
        logger.info("💡 输入命令: 'start' 开始录音, 'stop' 结束录音, 'quit' 退出")
        
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
                    command = sys.stdin.readline().strip().lower()
                    
                    if command == 'start':
                        logger.info("🎤 发送开始录音指令...")
                        for conn in self.client_connections[:]:  # 复制列表避免修改问题
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
                                
                    elif command == 'quit' or command == 'q':
                        logger.info("👋 收到退出指令")
                        self.running = False
                        break
                        
                    else:
                        logger.info("💡 可用命令: start, stop, quit")
                        
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"❌ GPIO控制线程出错: {e}")
    
    def start(self):
        """启动服务器"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(5)
            self.running = True
            
            logger.info(f"🚀 模拟AI服务器启动成功")
            logger.info(f"📡 监听地址: {self.host}:{self.port}")
            logger.info("=" * 50)
            
            # 启动GPIO控制线程
            gpio_thread = threading.Thread(target=self.gpio_control_thread, daemon=True)
            gpio_thread.start()
            
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
            
        logger.info("✅ 服务器已停止")

def signal_handler(signum, frame):
    """信号处理函数"""
    logger.info("\n🛑 收到停止信号，正在关闭服务器...")
    server.stop()
    sys.exit(0)

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='模拟AI服务器 - 用于调试客户端')
    parser.add_argument('--host', default='0.0.0.0', help='服务器监听地址')
    parser.add_argument('--port', type=int, default=8082, help='服务器监听端口')
    
    args = parser.parse_args()
    
    # 创建服务器实例
    server = MockAIServer(args.host, args.port)
    
    # 设置信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("\n👋 收到中断信号")
    finally:
        server.stop() 