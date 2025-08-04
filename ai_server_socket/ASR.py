import torch
import numpy as np
import soundfile as sf
import torchaudio
import io
import asyncio
import time
import base64
import importlib
import aiohttp
import sys
import os

# 添加项目根目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

# 导入ASR配置
from ASRs.config import ASR_CONFIGS, DEFAULT_ASR, FILTERED_RESULTS, EXACT_FILTERED_RESULTS
# 导入延迟加载函数
from ASRs import load_phi4_server

# 声明全局变量但不立即初始化
transcribe_audio_local = None

# 惰性加载ASR模块
def load_asr_module():
    global transcribe_audio_local
    asr_type = ASR_CONFIGS[DEFAULT_ASR]["type"]
    if transcribe_audio_local is None and asr_type == "local":
        module_path = ASR_CONFIGS[DEFAULT_ASR]["module"]
        function_name = ASR_CONFIGS[DEFAULT_ASR]["function"]
        print(f"惰性加载ASR模块: {module_path}.{function_name}")
        
        # 检查是否是phi4Server
        if module_path == "ASRs.Phi4Mul.phi4Server":
            # 使用延迟加载函数
            phi4Server = load_phi4_server()
            transcribe_audio_local = getattr(phi4Server, function_name)
        else:
            # 使用普通导入
            module = importlib.import_module(module_path)
            transcribe_audio_local = getattr(module, function_name)
    elif asr_type == "websocket":
        # websocket 类型无需加载本地模块
        print(f"[ASR] 当前类型: {asr_type}, 默认ASR: {DEFAULT_ASR}")
        transcribe_audio_local = None
        # 如果是 sensevoice，自动启动对应脚本
        if DEFAULT_ASR == "sensevoice":
            import platform
            import subprocess
            import os
            system_type = platform.system().lower()
            if system_type == "windows":
                bat_path = os.path.join(root_dir, "ASRs", "SenseVoiceSmall", "StartServer.bat")
                print(f"[ASR] Windows: 启动 {bat_path}")
                try:
                    subprocess.Popen(['cmd.exe', '/c', 'start', '', bat_path], shell=True, env=os.environ)
                    print("[ASR] 已调用 StartServer.bat 启动 sensevoice 服务。")
                except Exception as e:
                    print(f"[ASR] 启动 sensevoice 服务失败: {e}")
            else:
                sh_path = os.path.join(root_dir, "ASRs", "SenseVoiceSmall", "StartServer.sh")
                print(f"[ASR] 非Windows: 启动 {sh_path}")
                try:
                    subprocess.Popen(['bash', sh_path], env=os.environ)
                    print("[ASR] 已调用 StartServer.sh 启动 sensevoice 服务。")
                except Exception as e:
                    print(f"[ASR] 启动 sensevoice 服务失败: {e}")

async def async_process_audio(audio_buffer):
    start_time = time.time()
    """处理音频数据并返回转录结果"""
    try:
        print(f"🎤 [ASR] 开始处理音频数据 - {time.time():.3f}")
        
        # 1. 读取音频数据
        audio_parse_start = time.time()
        audio_buffer.seek(0)
        print(f"🎤 [ASR] 开始解析音频数据 - {audio_parse_start:.3f}")
        
        try:
            audio_data, samplerate = sf.read(audio_buffer)
            print(f"🎤 [ASR] 使用soundfile成功读取音频，采样率: {samplerate}")
        except Exception as e:
            print(f"🎤 [ASR] soundfile读取失败: {e}，尝试作为PCM数据读取")
            audio_buffer.seek(0)
            audio_data = np.frombuffer(audio_buffer.read(), dtype=np.int16)
            samplerate = 16000
            print(f"🎤 [ASR] PCM读取成功，数据长度: {len(audio_data)}")

        audio_parse_end = time.time()
        print(f"✅ [ASR] 音频解析完成，耗时: {audio_parse_end - audio_parse_start:.3f}s")

        if len(audio_data) == 0:
            print("❌ [ASR] 检测到空音频数据")
            return ""

        # 2. 音频预处理
        preprocess_start = time.time()
        print(f"🔄 [ASR] 开始音频预处理 - {preprocess_start:.3f}")
        
        # 确保音频是单声道
        if len(audio_data.shape) > 1:
            print(f"🎤 [ASR] 转换多声道到单声道，原始shape: {audio_data.shape}")
            audio_data = audio_data.mean(axis=1)

        # 确保采样率为16kHz
        resample_start = time.time()
        if samplerate != 16000:
            print(f"🔄 [ASR] 重采样 {samplerate}Hz -> 16000Hz")
            resampler = torchaudio.transforms.Resample(samplerate, 16000)
            audio_tensor = torch.from_numpy(audio_data).float()
            audio_data = resampler(audio_tensor).numpy()
            resample_end = time.time()
            print(f"✅ [ASR] 重采样完成，耗时: {resample_end - resample_start:.3f}s")
        else:
            print(f"✅ [ASR] 采样率已经是16kHz，无需重采样")

        # 数据类型转换和归一化
        normalize_start = time.time()
        audio_data = audio_data.astype(np.float32)
        if audio_data.max() > 1.0:
            print(f"🔄 [ASR] 音频归一化，最大值: {audio_data.max()}")
            audio_data = audio_data / 32768.0
        normalize_end = time.time()
        
        preprocess_end = time.time()
        print(f"✅ [ASR] 音频预处理完成，总耗时: {preprocess_end - preprocess_start:.3f}s")
        print(f"🎤 [ASR] 最终音频信息 - 长度: {len(audio_data)}, 最大值: {audio_data.max():.4f}, 最小值: {audio_data.min():.4f}")

        # 3. ASR识别
        asr_inference_start = time.time()
        print(f"🤖 [ASR] 开始ASR推理 - {asr_inference_start:.3f}")
        
        # 根据配置的ASR类型选择不同的处理方式
        asr_config = ASR_CONFIGS[DEFAULT_ASR]
        print(f"🤖 [ASR] 使用ASR配置: {DEFAULT_ASR} ({asr_config['description']})")
        
        if asr_config["type"] == "local":
            # 使用本地模块进行语音识别（惰性加载）
            print(f"🏠 [ASR] 使用本地ASR服务: {DEFAULT_ASR}")
            
            # 确保模块已加载
            module_load_start = time.time()
            load_asr_module()
            module_load_end = time.time()
            print(f"📦 [ASR] 模块加载检查完成，耗时: {module_load_end - module_load_start:.3f}s")
            
            # 实际推理
            inference_start = time.time()
            text = await transcribe_audio_local(audio_data, 16000)
            inference_end = time.time()
            print(f"🤖 [ASR] 本地推理完成，耗时: {inference_end - inference_start:.3f}s")
            
        elif asr_config["type"] == "websocket":
            # 使用WebSocket服务进行语音识别
            print(f"🌐 [ASR] 使用WebSocket ASR服务: {DEFAULT_ASR}")
            websocket_start = time.time()
            text = await transcribe_audio_websocket(audio_data, asr_config["url"])
            websocket_end = time.time()
            print(f"🌐 [ASR] WebSocket推理完成，耗时: {websocket_end - websocket_start:.3f}s")
        else:
            raise ValueError(f"不支持的ASR类型: {asr_config['type']}")

        asr_inference_end = time.time()
        print(f"✅ [ASR] ASR推理阶段完成，总耗时: {asr_inference_end - asr_inference_start:.3f}s")

        # 4. 文本过滤
        filter_start = time.time()
        print(f"🔍 [ASR] 开始文本过滤，原始结果: '{text}'")
        
        # 使用filter_text函数进行文本过滤
        if not filter_text(text):
            print(f"❌ [ASR] 文本被过滤掉")
            text = ""
        else:
            print(f"✅ [ASR] 文本通过过滤")
        
        filter_end = time.time()
        print(f"✅ [ASR] 文本过滤完成，耗时: {filter_end - filter_start:.3f}s")

        # 5. 总结
        total_time = time.time() - start_time
        print(f"🏁 [ASR] 语音转文本完成，总耗时: {total_time:.3f}s")
        print(f"📊 [ASR] 详细耗时分解:")
        print(f"   - 音频解析: {audio_parse_end - audio_parse_start:.3f}s")
        print(f"   - 音频预处理: {preprocess_end - preprocess_start:.3f}s")
        print(f"   - ASR推理: {asr_inference_end - asr_inference_start:.3f}s")
        print(f"   - 文本过滤: {filter_end - filter_start:.3f}s")
        print(f"🎯 [ASR] 最终识别结果: '{text}'")
        
        return text
        
    except Exception as e:
        error_time = time.time() - start_time
        print(f"❌ [ASR] 语音转文本处理出错，耗时: {error_time:.3f}s，错误: {e}")
        return f"ERROR: {str(e)}"

async def transcribe_audio_websocket(audio_data, ws_url):
    """通过WebSocket服务进行语音识别"""
    try:
        # 将音频数据转换为字节
        audio_buffer = io.BytesIO()
        sf.write(audio_buffer, audio_data, 16000, format='RAW', subtype='PCM_16')
        audio_buffer.seek(0)
        audio_bytes = audio_buffer.read()
        
        # 连接WebSocket服务
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url) as ws:
                # 发送开始信号
                await ws.send_str('START')
                
                # 发送音频数据
                await ws.send_bytes(audio_bytes)
                
                # 发送结束信号
                await ws.send_str('EOF')
                
                # 等待并接收识别结果
                result = await ws.receive_str()
                return result
    except Exception as e:
        print(f"WebSocket ASR服务调用出错: {e}")
        return f"ERROR: {str(e)}"

def filter_text(text: str) -> bool:
    """
    过滤文本结果
    返回值: 文本是否通过过滤（True表示通过）
    """
    text_stripped = text.strip()
    # 先检查完全匹配
    if text_stripped in EXACT_FILTERED_RESULTS:
        return False
    # 再检查部分匹配
    elif any(filter_text in text_stripped for filter_text in FILTERED_RESULTS):
        return False
    
    return bool(text_stripped and not text.startswith("ERROR:"))

# 修改切换ASR服务函数
def switch_asr_service(asr_name):
    """
    动态切换ASR服务
    参数:
        asr_name: ASR服务名称，必须在ASR_CONFIGS中定义
    """
    global DEFAULT_ASR, transcribe_audio_local
    
    if asr_name not in ASR_CONFIGS:
        raise ValueError(f"未知的ASR服务: {asr_name}")
    
    DEFAULT_ASR = asr_name
    print(f"已切换到ASR服务: {asr_name} ({ASR_CONFIGS[asr_name]['description']})")
    
    # 重置transcribe_audio_local，下次调用时会重新加载
    transcribe_audio_local = None
    
    return True

if __name__ == "__main__":
    print("当前默认ASR服务:", DEFAULT_ASR)
    # 这里可以手动调用 load_asr_module() 或其他测试函数
    load_asr_module()
    print("ASR模块已加载")