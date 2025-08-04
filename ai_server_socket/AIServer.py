# 添加本地包路径
import sys
import os

# 获取当前文件的目录
current_dir = os.path.dirname(os.path.abspath(__file__))
# 获取项目根目录
root_dir = os.path.dirname(current_dir)
# 添加项目根目录到Python路径
sys.path.append(root_dir)
# sys.path.append(os.path.join(root_dir, "fastapi-0.115.12"))
# sys.path.append(os.path.join(root_dir, "starlette-0.46.1"))

import torch
import base64
import uvicorn
import time
import asyncio
import io
import numpy as np
import soundfile as sf
import torchaudio
from collections import deque
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, File, UploadFile, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from transformers import AutoModelForCausalLM, AutoProcessor, GenerationConfig
from TTSs import TTSService_Edge, TTSService_Volcano  # 修改导入路径
from LLMs import LLMFactory, ConversationManager  # 修改导入路径
from ASR import async_process_audio, filter_text
import datetime

# 创建一个全局队列，用于存储待处理的语音识别结果
# pending_queries = deque()
# 创建一个事件，用于通知有新的查询
# new_query_event = asyncio.Event()
# 创建一个锁，用于保护队列的并发访问
# queue_lock = asyncio.Lock()

# # 添加一个全局队列，用于存储LLM生成的文本
# llm_text_queue = asyncio.Queue()
# # 添加一个全局队列，用于存储音频数据
# audio_bytes_queue = asyncio.Queue()
# # 添加一个事件，用于通知有新的LLM文本
# new_llm_text_event = asyncio.Event()

# 在模型加载完成后初始化LLMService
print("会话初始化！")
# 初始化LLMService，使用Ollama服务
conversation_manager = ConversationManager()

# 定义asr数据模型，用于接收POST请求中的数据
class ASRItem(BaseModel):
    wav: str  # 输入音频

def log_with_time(message):
    """输出带有时间戳的日志"""
    current_time = datetime.datetime.now().strftime("[%H:%M:%S]")
    print(f"{current_time} {message}")

# 创建FastAPI应用
app = FastAPI()

# 添加中间件来记录请求到达时间
@app.middleware("http")
async def log_request_timing(request: Request, call_next):
    """记录请求的详细时间信息"""
    start_time = time.time()
    
    # 记录请求到达的最早时间
    print(f"🔔 [中间件] 请求到达FastAPI - {start_time:.3f}, 路径: {request.url.path}")
    
    # 如果是文件上传请求，记录更多信息
    if request.url.path == "/process_audio_file":
        content_length = request.headers.get("content-length", "未知")
        content_type = request.headers.get("content-type", "未知")
        print(f"📦 [中间件] 文件上传请求详情 - Content-Length: {content_length}, Content-Type: {content_type}")
    
    try:
        # 调用实际的路由处理函数
        middleware_end = time.time()
        print(f"🚀 [中间件] 准备调用路由处理函数，中间件耗时: {middleware_end - start_time:.3f}s")
        
        response = await call_next(request)
        
        # 记录响应完成时间
        response_time = time.time()
        total_time = response_time - start_time
        print(f"✅ [中间件] 响应准备完成，总中间件耗时: {total_time:.3f}s")
        
        # 如果是文件上传请求，记录响应详情
        if request.url.path == "/process_audio_file":
            print(f"📤 [中间件] 文件上传响应详情:")
            print(f"   - 响应状态码: {response.status_code}")
            print(f"   - 响应类型: {type(response)}")
            
            # 详细输出所有响应头
            print(f"🔍 [中间件] 完整响应头:")
            if hasattr(response, 'headers'):
                for header_name, header_value in response.headers.items():
                    print(f"   - {header_name}: {header_value}")
            else:
                print(f"   - 无法获取响应头")
            
            print(f"🚀 [中间件] 即将将响应发送给客户端")
        
        return response
        
    except Exception as e:
        error_time = time.time() - start_time
        print(f"❌ [中间件] 处理请求时出错，耗时: {error_time:.3f}s，错误: {e}")
        raise

# 初始化TTS服务
tts_service = TTSService_Edge()

@app.post("/asr")
async def asr(item: ASRItem):
    try:
        data = base64.b64decode(item.wav)
        with open("test.wav", "wb") as f:
            f.write(data)
        
        # 读取音频文件
        audio_buffer = io.BytesIO(data)
        text = await async_process_audio(audio_buffer)
        
        result_dict = {"code": 0, "msg": "ok", "res": text}
    except Exception as e:
        result_dict = {"code": 1, "msg": str(e)}
    return result_dict

# 新增：文件上传的ASR-LLM-TTS完整流程接口
@app.post("/process_audio_file")
async def process_audio_file(
    audio_file: UploadFile = File(..., description="音频文件"),
    response_format: str = "json"  # json返回文本，audio返回音频流
):
    """
    处理上传的音频文件，执行完整的ASR-LLM-TTS流程
    
    Args:
        audio_file: 上传的音频文件（支持wav, mp3, m4a等格式）
        response_format: 响应格式 ("json"返回文本结果, "audio"返回音频流)
    
    Returns:
        JSON格式: {"code": 0, "msg": "ok", "user_text": "用户说的话", "ai_text": "AI回复", "audio_url": "音频文件URL"}
        或音频流（当response_format="audio"时）
    """
    request_start_time = time.time()
    session_id = int(request_start_time * 1000)  # 使用时间戳作为会话ID
    
    log_with_time(f"🟢 [会话{session_id}] HTTP请求开始处理，文件名: {audio_file.filename}")
    
    try:
        # 1. 验证文件类型
        validation_start = time.time()
        log_with_time(f"📋 [会话{session_id}] 开始文件类型验证")
        
        if not audio_file.content_type or not audio_file.content_type.startswith('audio/'):
            # 根据文件扩展名判断
            if audio_file.filename:
                ext = audio_file.filename.lower().split('.')[-1]
                if ext not in ['pcm', 'wav', 'mp3', 'm4a', 'flac', 'ogg', 'webm']:
                    raise HTTPException(status_code=400, detail="不支持的音频格式")
        
        validation_end = time.time()
        log_with_time(f"✅ [会话{session_id}] 文件类型验证完成，耗时: {validation_end - validation_start:.3f}s")
        
        # 2. 读取音频文件
        file_read_start = time.time()
        log_with_time(f"📁 [会话{session_id}] 开始读取音频文件数据")
        
        audio_data = await audio_file.read()
        
        file_read_end = time.time()
        log_with_time(f"✅ [会话{session_id}] 音频文件读取完成，文件大小: {len(audio_data)} bytes，耗时: {file_read_end - file_read_start:.3f}s")
        
        if len(audio_data) == 0:
            raise HTTPException(status_code=400, detail="音频文件为空")
        
        # 3. 创建音频缓冲区
        buffer_start = time.time()
        log_with_time(f"🔄 [会话{session_id}] 创建音频缓冲区")
        
        audio_buffer = io.BytesIO(audio_data)
        
        buffer_end = time.time()
        log_with_time(f"✅ [会话{session_id}] 音频缓冲区创建完成，耗时: {buffer_end - buffer_start:.3f}s")
        
        # 4. ASR - 语音转文字
        asr_start_time = time.time()
        log_with_time(f"🎤 [会话{session_id}] ==== ASR阶段开始 ====")
        log_with_time(f"🎤 [会话{session_id}] 调用 async_process_audio 函数")
        
        user_text = await async_process_audio(audio_buffer)
        
        asr_end_time = time.time()
        asr_duration = asr_end_time - asr_start_time
        log_with_time(f"✅ [会话{session_id}] ==== ASR阶段完成 ====，耗时: {asr_duration:.3f}s，识别结果: '{user_text}'")
        
        if not user_text or user_text.startswith("ERROR:"):
            log_with_time(f"❌ [会话{session_id}] ASR识别失败: {user_text}")
            raise HTTPException(status_code=422, detail=f"语音识别失败: {user_text}")
        
        # 5. LLM - 生成回复
        llm_start_time = time.time()
        log_with_time(f"🤖 [会话{session_id}] ==== LLM阶段开始 ====")
        log_with_time(f"🤖 [会话{session_id}] 调用 conversation_manager.generate_stream")
        
        # 收集完整的AI回复
        ai_text_chunks = []
        chunk_count = 0
        
        stream_start = time.time()
        async for chunk in conversation_manager.generate_stream(user_text):
            chunk_count += 1
            ai_text_chunks.append(chunk)
            if chunk_count == 1:
                first_chunk_time = time.time()
                log_with_time(f"🤖 [会话{session_id}] 收到第一个LLM chunk，延迟: {first_chunk_time - stream_start:.3f}s")
            
            # 每10个chunk记录一次进度
            if chunk_count % 10 == 0:
                log_with_time(f"🤖 [会话{session_id}] 已收到 {chunk_count} 个chunk")
        
        ai_text = ''.join(ai_text_chunks).strip()
        
        llm_end_time = time.time()
        llm_duration = llm_end_time - llm_start_time
        log_with_time(f"✅ [会话{session_id}] ==== LLM阶段完成 ====，总chunk数: {chunk_count}，耗时: {llm_duration:.3f}s")
        log_with_time(f"🤖 [会话{session_id}] AI回复: '{ai_text[:100]}{'...' if len(ai_text) > 100 else ''}'")
        
        if not ai_text:
            log_with_time(f"❌ [会话{session_id}] LLM生成失败，回复为空")
            raise HTTPException(status_code=500, detail="AI回复生成失败")
        
        # 6. 根据响应格式处理
        response_process_start = time.time()
        log_with_time(f"📤 [会话{session_id}] 开始处理响应，格式: {response_format}")
        
        if response_format == "audio":
            log_with_time(f"🔊 [会话{session_id}] 生成音频响应")
            # 返回音频流
            audio_response = await generate_audio_response(ai_text, session_id)
            response_process_end = time.time()
            log_with_time(f"✅ [会话{session_id}] 音频响应生成完成，耗时: {response_process_end - response_process_start:.3f}s")
            return audio_response
        else:
            # 返回JSON格式
            json_start = time.time()
            total_duration = time.time() - request_start_time
            result = {
                "code": 0,
                "msg": "ok",
                "session_id": session_id,
                "user_text": user_text,
                "ai_text": ai_text,
                "timing": {
                    "asr_duration": asr_duration,
                    "llm_duration": llm_duration,
                    "total_duration": total_duration
                }
            }
            
            json_end = time.time()
            log_with_time(f"📋 [会话{session_id}] JSON响应构建完成，耗时: {json_end - json_start:.3f}s")
            
            response_process_end = time.time()
            log_with_time(f"✅ [会话{session_id}] 响应处理完成，总耗时: {response_process_end - response_process_start:.3f}s")
            
            request_end_time = time.time()
            total_request_time = request_end_time - request_start_time
            log_with_time(f"🏁 [会话{session_id}] ==== 整个请求处理完成 ====")
            log_with_time(f"📊 [会话{session_id}] 详细耗时统计:")
            log_with_time(f"   - 文件验证: {validation_end - validation_start:.3f}s")
            log_with_time(f"   - 文件读取: {file_read_end - file_read_start:.3f}s")
            log_with_time(f"   - 缓冲区创建: {buffer_end - buffer_start:.3f}s")
            log_with_time(f"   - ASR处理: {asr_duration:.3f}s")
            log_with_time(f"   - LLM处理: {llm_duration:.3f}s")
            log_with_time(f"   - 响应处理: {response_process_end - response_process_start:.3f}s")
            log_with_time(f"   - 总请求时间: {total_request_time:.3f}s")
            
            # 准备发送响应
            response_send_start = time.time()
            log_with_time(f"📤 [会话{session_id}] 准备发送JSON响应")
            
            json_response = JSONResponse(content=result)
            
            response_send_end = time.time()
            log_with_time(f"✅ [会话{session_id}] JSON响应对象创建完成，耗时: {response_send_end - response_send_start:.3f}s")
            log_with_time(f"📏 [会话{session_id}] 响应大小: {len(str(result))} 字符")
            
            return json_response
            
    except HTTPException as e:
        error_time = time.time() - request_start_time
        log_with_time(f"❌ [会话{session_id}] HTTP异常，耗时: {error_time:.3f}s，错误: {e.detail}")
        raise
    except Exception as e:
        error_time = time.time() - request_start_time
        error_msg = f"处理音频文件时出错: {str(e)}"
        log_with_time(f"❌ [会话{session_id}] 未知异常，耗时: {error_time:.3f}s，错误: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)

async def generate_audio_response(text: str, session_id: int):
    """生成音频响应流"""
    try:
        log_with_time(f"会话{session_id}: 开始TTS生成音频")
        tts_start_time = time.time()
        
        async def audio_generator():
            audio_chunks = []
            chunk_count = 0
            
            async for audio_chunk in tts_service.text_to_speech_stream(text):
                chunk_count += 1
                audio_chunks.append(audio_chunk)
                yield audio_chunk
            
            tts_duration = time.time() - tts_start_time
            log_with_time(f"会话{session_id}: TTS完成，耗时{tts_duration:.2f}s，生成{chunk_count}个音频块")
        
        return StreamingResponse(
            audio_generator(), 
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": f"attachment; filename=ai_response_{session_id}.mp3",
                "X-Session-ID": str(session_id)
            }
        )
        
    except Exception as e:
        error_msg = f"生成音频响应时出错: {str(e)}"
        log_with_time(f"会话{session_id}: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)

@app.websocket("/ws/asr")
async def websocket_asr(websocket: WebSocket):
    # 为每个连接创建独立的计时和ID变量
    connection_start_time = None
    connection_voice_id = 0
    connection_session_timers = {}
    
    print(f"\n=== 新的WebSocket连接已建立 (客户端 {id(websocket)}) ===")
    await websocket.accept()

    # 为每个连接创建独立的任务列表和锁
    connection_tasks = []
    connection_tasks_lock = asyncio.Lock()
    
    # 为每个连接创建独立的队列
    connection_llm_queue = asyncio.Queue()
    connection_audio_queue = asyncio.Queue()
    connection_tts_queue = asyncio.Queue()
    
    # 为每个连接创建独立的查询队列和事件
    connection_pending_queries = deque()
    connection_new_query_event = asyncio.Event()
    connection_queue_lock = asyncio.Lock()
    
    # 将全局变量改为连接级别变量
    connection_generation_task = None
    connection_tts_task = None
    connection_task_tts_queue = None
    connection_cancel_tasks = asyncio.Event()
    connection_active_generation_tasks = 0
    connection_generation_tasks_lock = asyncio.Lock()
    connection_max_concurrent_tasks = asyncio.Semaphore(2)  # 最多同时执行2个任务
    connection_active_tasks_list = []
    connection_active_tasks_list_lock = asyncio.Lock()

    # 创建一个任务来处理大模型响应
    llm_task = asyncio.create_task(handle_llm_responses(
        websocket, 
        connection_llm_queue, 
        connection_tts_queue, 
        connection_tasks, 
        connection_tasks_lock,
        connection_audio_queue,
        connection_pending_queries,
        connection_new_query_event,
        connection_queue_lock,
        connection_session_timers,
        connection_generation_task,
        connection_tts_task,
        connection_task_tts_queue,
        connection_cancel_tasks,
        connection_active_generation_tasks,
        connection_generation_tasks_lock,
        connection_max_concurrent_tasks,
        connection_active_tasks_list,
        connection_active_tasks_list_lock
    ))
    
    # 创建一个任务来监控LLM文本队列并发送数据
    text_sender_task = asyncio.create_task(send_llm_text(websocket, connection_llm_queue))
    
    # 创建一个任务来监控音频队列并发送数据
    audio_sender_task = asyncio.create_task(send_audio_bytes(websocket, connection_llm_queue, connection_audio_queue))
    
    try:
        while True:
            audio_buffer = io.BytesIO()
            total_bytes = 0
            first_packet_time = None
            eof_time = None
            
            while True:
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=300)
                    if message.get('text') == 'START':
                        # 增加语音请求ID
                        connection_voice_id += 1
                        current_voice_id = connection_voice_id
                        
                        # 点位1: 开始接收用户语音
                        connection_session_timers[current_voice_id] = time.time()
                        connection_start_time = connection_session_timers[current_voice_id]
                        first_packet_time = connection_start_time
                        print(f"\n\n【对话{current_voice_id}计时：开始接收用户语音】0.000s")
                    elif 'bytes' in message and message['bytes']:
                        data = message['bytes']
                        audio_buffer.write(data)
                        total_bytes += len(data)
                        print(f"收到 {len(data)} 字节 (累计 {total_bytes} 字节)")
                    elif message.get('text') == 'EOF':
                        eof_time = time.time()
                        # 点位2: 语音包接收完毕
                        if current_voice_id in connection_session_timers:
                            elapsed = eof_time - connection_session_timers[current_voice_id]
                            print(f"【对话{current_voice_id}计时：语音包接收完毕】{elapsed:.3f}s")
                        break
                except asyncio.TimeoutError:
                    print("接收超时，终止当前会话")
                    break
                except WebSocketDisconnect:
                    print("网络连接出现问题，结束传输")
                    raise
                except RuntimeError as e:
                    if "disconnect message has been received" in str(e):
                        print("WebSocket已断开连接")
                        raise WebSocketDisconnect()
                    raise
                
            if first_packet_time is None or eof_time is None:
                print("传输未完成")
                continue
                
            # 重新计算时间指标
            transfer_duration = eof_time - first_packet_time
            if transfer_duration < 0.001:
                transfer_duration = 0.001
            first_packet_time = None
                
            avg_speed = total_bytes / 1024 / transfer_duration
#             print(f"""
# 文件接收完成
# ├─ 网络耗时: {transfer_duration:.3f}s (首包->EOF)
# ├─ 平均速率: {avg_speed:.2f} KB/s
# └─ 数据大小: {total_bytes/1024:.2f}KB""")
            
            # 语音转文本，然后发送到客户端
            try:
                # 点位3: 开始语音转文本
                asr_start_time = time.time()
                if current_voice_id in connection_session_timers:
                    elapsed = asr_start_time - connection_session_timers[current_voice_id]
                    print(f"【对话{current_voice_id}计时：开始语音转文本】{elapsed:.3f}s")
                
                text = await async_process_audio(audio_buffer)
                
                # 点位4: 语音转文本结束
                asr_end_time = time.time()
                if current_voice_id in connection_session_timers:
                    elapsed = asr_end_time - connection_session_timers[current_voice_id]
                    print(f"【对话{current_voice_id}计时：语音转文本结束】{elapsed:.3f}s")
                
                # 仅在文本非空时处理
                if text.strip() and not text.startswith("ERROR:"):
                    # 发送用户输入文本到客户端
                    await connection_llm_queue.put("USER:" + text)
                    # 将识别结果添加到待处理队列
                    async with connection_queue_lock:
                        print("添加到队列")
                        # 将语音ID和文本一起添加到队列
                        connection_pending_queries.append((current_voice_id, text))
                        connection_new_query_event.set()
            except (WebSocketDisconnect, ConnectionResetError, RuntimeError) as e:
                print(f"处理过程中发生错误: {str(e)}")
                break
    
    except (WebSocketDisconnect, ConnectionResetError):
        log_with_time(f"客户端 {id(websocket)} 主动断开连接")
    finally:       
        # 取消LLM处理任务
        if not llm_task.done():
            llm_task.cancel()
            try:
                await llm_task
            except asyncio.CancelledError:
                pass
        
        # 取消文本发送任务
        if not text_sender_task.done():
            text_sender_task.cancel()
            try:
                await text_sender_task
            except asyncio.CancelledError:
                pass
        
        # 取消音频发送任务
        if not audio_sender_task.done():
            audio_sender_task.cancel()
            try:
                await audio_sender_task
            except asyncio.CancelledError:
                pass
        
        # 取消所有活跃任务
        async with connection_tasks_lock:
            for task in connection_tasks.copy():
                if not task.done():
                    print(f"客户端断开连接，取消任务: {id(task)}")
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            # 清空活跃任务列表
            connection_tasks.clear()
        
        # 清理会话计时器
        connection_session_timers.clear()
        
        try:
            await websocket.close()
        except RuntimeError:
            pass
        log_with_time(f"=== WebSocket连接已关闭 (客户端 {id(websocket)}) ===")

# # 添加取消标志和任务跟踪
# current_generation_task = None
# current_tts_task = None
# task_tts_queue = None
# cancel_current_tasks = asyncio.Event()
# # 添加一个计数器和锁来跟踪当前活跃的生成任务数量
# active_generation_tasks = 0
# generation_tasks_lock = asyncio.Lock()
# # 添加一个信号量来限制最大并发任务数
# max_concurrent_tasks_semaphore = asyncio.Semaphore(2)  # 最多同时执行2个任务
# # 添加一个列表来跟踪所有活跃的任务
# active_tasks_list = []
# active_tasks_list_lock = asyncio.Lock()

async def handle_llm_responses(
    websocket: WebSocket, 
    llm_text_queue: asyncio.Queue, 
    tts_text_queue: asyncio.Queue, 
    connection_tasks: list, 
    connection_tasks_lock: asyncio.Lock, 
    audio_bytes_queue: asyncio.Queue, 
    connection_pending_queries: deque, 
    connection_new_query_event: asyncio.Event, 
    connection_queue_lock: asyncio.Lock, 
    connection_session_timers: dict,
    connection_generation_task,
    connection_tts_task,
    connection_task_tts_queue,
    connection_cancel_tasks,
    connection_active_generation_tasks,
    connection_generation_tasks_lock,
    connection_max_concurrent_tasks,
    connection_active_tasks_list,
    connection_active_tasks_list_lock
):
    """处理大模型响应的独立任务"""
    
    while True:
        # 等待新的查询
        await connection_new_query_event.wait()
        
        # 获取一个查询并处理
        async with connection_queue_lock:
            if not connection_pending_queries:
                connection_new_query_event.clear()
                continue
            
            # 获取语音ID和文本
            voice_id, text = connection_pending_queries.popleft()
            if not connection_pending_queries:
                connection_new_query_event.clear()
        
        print("等待获取信号量")
        # 等待获取信号量，如果已经有2个任务在运行，这里会阻塞
        await connection_max_concurrent_tasks.acquire()
        print("已经获取信号量")
        
        # 创建新的文本生成任务
        async with connection_generation_tasks_lock:
            connection_active_generation_tasks += 1
            print(f"创建新任务，当前活跃任务数: {connection_active_generation_tasks}")
            
        # 保存当前任务列表的副本，以便新任务可以在生成第一个chunk后取消它们
        async with connection_tasks_lock:
            previous_tasks = connection_tasks.copy()

        # 创建新文本生成任务
        print("创建新文本生成任务")
        new_task = asyncio.create_task(
            generate_text_response(
                websocket, 
                text, 
                connection_tasks, 
                connection_tasks_lock,
                connection_session_timers,
                llm_text_queue,
                tts_text_queue,
                audio_bytes_queue,
                previous_tasks, 
                voice_id,
                connection_tts_task,
                connection_task_tts_queue,
                connection_cancel_tasks,
                connection_active_tasks_list,
                connection_active_tasks_list_lock
            )
        )
        
        # 移除TTS任务创建代码
        # print("创建TTS任务")
        # connection_task_tts_queue = asyncio.Queue()
        # connection_tts_task = asyncio.create_task(process_tts(...))
        
        # 将新任务添加到活跃任务列表
        async with connection_tasks_lock:
            connection_tasks.append(new_task)
            print(f"添加新任务到活跃列表，当前活跃任务数: {len(connection_tasks)}")
        
        # 更新当前任务引用
        connection_generation_task = new_task
        
        # 添加任务完成回调，用于释放信号量和减少活跃任务计数
        new_task.add_done_callback(
            lambda f: task_completed_callback(
                f, 
                connection_tasks, 
                connection_tasks_lock, 
                connection_max_concurrent_tasks,
                connection_active_generation_tasks,
                connection_generation_tasks_lock
            )
        )

        print("handle_llm_responses 完毕，等待下一个查询")

# 任务完成回调函数
async def decrease_active_tasks(connection_active_generation_tasks, connection_generation_tasks_lock):
    # 移除全局变量引用
    # global active_generation_tasks
    async with connection_generation_tasks_lock:
        connection_active_generation_tasks -= 1
        print(f"任务完成，当前活跃任务数: {connection_active_generation_tasks}")

async def remove_from_active_tasks(task, connection_tasks, connection_tasks_lock):
    """从活跃任务列表中移除指定任务"""
    async with connection_tasks_lock:
        if task in connection_tasks:
            connection_tasks.remove(task)

def task_completed_callback(
    future, 
    connection_tasks, 
    connection_tasks_lock, 
    connection_max_concurrent_tasks,
    connection_active_generation_tasks,
    connection_generation_tasks_lock
):
    # 移除全局变量引用
    # global active_generation_tasks
    
    # 检查任务是否被取消或发生异常
    if future.cancelled():
        print("任务被取消，释放资源")
    elif future.exception() is not None:
        print(f"任务发生异常: {future.exception()}")
    else:
        print("任务正常完成")
    
    # 无论任务如何结束，都释放信号量
    connection_max_concurrent_tasks.release()
    
    # 减少活跃任务计数并从列表中移除任务
    asyncio.create_task(
        decrease_active_tasks(
            connection_active_generation_tasks,
            connection_generation_tasks_lock
        )
    )
    asyncio.create_task(
        remove_from_active_tasks(
            future, 
            connection_tasks, 
            connection_tasks_lock
        )
    )

# 首先添加一个新的队列来存储待处理的文本
tts_text_queue = asyncio.Queue()

async def generate_text_response(
    websocket: WebSocket,                          # WebSocket连接对象，用于与客户端通信
    text: str,                                     # 用户输入的文本，需要发送给LLM处理
    connection_tasks: list,                        # 当前连接的所有活跃任务列表
    connection_tasks_lock: asyncio.Lock,           # 保护任务列表的并发访问锁
    connection_session_timers: dict,               # 存储会话计时信息的字典，用于性能监控
    llm_text_queue: asyncio.Queue,                 # LLM生成文本的队列，用于向客户端发送文本
    tts_text_queue: asyncio.Queue,                 # TTS文本队列，存储待处理的TTS文本
    audio_bytes_queue: asyncio.Queue,              # 音频数据队列，存储生成的音频数据
    previous_tasks=None,                           # 之前的任务列表，用于在新任务开始时取消旧任务
    voice_id=None,                                 # 当前语音请求的ID，用于日志和计时
    connection_tts_task=None,                      # 当前连接的TTS任务引用
    connection_task_tts_queue=None,                # 当前TTS任务使用的队列
    connection_cancel_tasks=None,                  # 取消任务的事件标志
    connection_active_tasks_list=None,             # 活跃任务列表的另一个引用
    connection_active_tasks_list_lock=None         # 保护活跃任务列表的锁
):
    """处理文本生成的独立任务"""
    # 移除全局变量引用
    # global current_tts_task
    
    try:
        await llm_text_queue.put("AI_START")
        await llm_text_queue.put("AUDIO_START")
        
        # 创建TTS任务
        print("创建TTS任务")
        # 使用连接级别的变量而不是全局变量
        task_tts_queue = asyncio.Queue()
        tts_task = asyncio.create_task(process_tts(
            websocket, 
            task_tts_queue, 
            voice_id, 
            connection_tasks, 
            connection_tasks_lock,
            llm_text_queue,
            audio_bytes_queue,
            connection_session_timers,
            connection_cancel_tasks  # 确保传递这个参数
        ))
        
        # 将TTS任务添加到活跃任务列表中
        async with connection_tasks_lock:
            connection_tasks.append(tts_task)
        
        has_nn = False
        print("正在调用大模型...")
        
        # 生成文本响应
        response_chunks = []
        buffer = ""
        last_send_time = time.time()
        time_interval = 0.2
        first_chunk_checked = False
        first_chunk_generated = False
        
        # 完整响应文本，用于TTS
        full_response = ""
        last_tts_length = 0
        
        # 记录开始时间
        generation_start_time = time.time()
        last_chunk_time = generation_start_time
        chunk_count = 0
        
        # 添加一个标志，用于跟踪是否已经发送了第一个TTS
        first_tts_sent = False
        first_tts_timer = time.time()
        first_tts_timeout = 1
        
        try:
            # 点位5: 开始文本生成
            text_generation_count = 1
            current_text_count = text_generation_count
            if voice_id in connection_session_timers:
                text_gen_start_time = time.time()
                elapsed = text_gen_start_time - connection_session_timers[voice_id]
                print(f"【对话{voice_id}计时：第{current_text_count}个文本 开始生成】{elapsed:.3f}s")
                
            async for chunk in conversation_manager.generate_stream(text):                    
                current_time = time.time()
                chunk_latency = current_time - last_chunk_time
                chunk_count += 1
                print(f"第 {chunk_count} 个chunk生成耗时: {chunk_latency:.3f}s, 内容: {chunk}")
                last_chunk_time = current_time
                
                # # 检查是否需要取消当前任务
                # if connection_cancel_tasks and connection_cancel_tasks.is_set():
                #     print("文本生成任务被取消")
                #     if not tts_task and not tts_task.done():
                #         tts_task.cancel()
                #         await llm_text_queue.put("AI_CANCELLED")
                #     break
                    
                buffer += chunk
                current_time = time.time()
                
                if not first_chunk_checked and buffer.strip():
                    if "[" in buffer or "N" in buffer:
                        print("检测到NN标记，终止生成")
                        has_nn = True
                        buffer = "[NN detected]"
                        
                        # if not current_tts_task.done():
                        #     current_tts_task.cancel()
                        
                        # async with active_tasks_list_lock:
                        #     if current_tts_task in active_tasks_list:
                        #         active_tasks_list.remove(current_tts_task)
                        #         print(f"从活跃任务列表中移除TTS任务，当前活跃任务数: {len(active_tasks_list)}")
                        
                        break
                    first_chunk_checked = True
                        
                # 根据状态发送不同的响应
                if first_chunk_checked and not has_nn:
                    if not first_chunk_generated and previous_tasks:
                        first_chunk_generated = True
                        
                        try:
                            active_previous_tasks = [
                                task for task in previous_tasks 
                                if not task.done() and task != asyncio.current_task()
                            ]
                            
                            if active_previous_tasks:
                                print("取消正在运行的任务")
                                
                                # 清空音频队列，防止旧音频继续播放
                                print("清空音频队列")
                                while not audio_bytes_queue.empty():
                                    try:
                                        audio_bytes_queue.get_nowait()
                                    except asyncio.QueueEmpty:
                                        break

                                
                                for task in active_previous_tasks:
                                    try:
                                        task.cancel()
                                        await asyncio.wait([task], timeout=0.2)
                                        
                                        # 查找并取消对应的TTS任务
                                        async with connection_tasks_lock:
                                            for tts_task in connection_tasks:
                                                # 如果不是当前任务且不是当前正在创建的TTS任务
                                                if (tts_task != asyncio.current_task() and 
                                                    tts_task != tts_task and 
                                                    not tts_task.done()):
                                                    print("取消关联的TTS任务")
                                                    tts_task.cancel()
                                                    try:
                                                        await asyncio.wait([tts_task], timeout=0.2)
                                                    except Exception as e:
                                                        print(f"取消TTS任务时出错: {e}")
                                    except Exception as e:
                                        print(f"取消任务时出错: {e}")
                            else:
                                print("没有需要取消的活跃任务")
                            await llm_text_queue.put("AI_CANCELLED")
                            
                        except Exception as e:
                            print(f"任务切换过程中出错: {e}")
                    
                    full_response += chunk
                    # 异常情况下智商不够高的大模型会生成夹杂了[NNNN]字符串的文本，需要在此移除[NNNN]字符串
                    if "[NNNN]" in full_response:
                        full_response = full_response.replace("[NNNN]", "")
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
                                print(f"发送完整句子到TTS: {new_sentence}")
                                await task_tts_queue.put(new_sentence)
                                last_tts_length = end_pos + 1
                                first_tts_sent = True

                                # 将buffer添加到LLM文本队列，而不是直接发送
                                print(f"添加chunk组到队列: {buffer[:20]}..." if len(buffer) > 20 else f"添加chunk组到队列: {buffer}")
                                await llm_text_queue.put(buffer)
                                response_chunks.append(buffer)
                                buffer = ""
                                last_send_time = current_time
                                await asyncio.sleep(0.01)
                        elif not first_tts_sent and (current_time - first_tts_timer > first_tts_timeout) and (len(full_response) - last_tts_length) > 0:
                            new_sentence = full_response[last_tts_length:].strip()
                            if new_sentence:
                                print(f"超时发送第一个TTS: {new_sentence}")
                                await task_tts_queue.put(new_sentence)
                                last_tts_length = len(full_response)
                                first_tts_sent = True

                                # 将buffer添加到LLM文本队列，而不是直接发送
                                print(f"添加chunk组到队列: {buffer[:20]}..." if len(buffer) > 20 else f"添加chunk组到队列: {buffer}")
                                await llm_text_queue.put(buffer)
                                response_chunks.append(buffer)
                                buffer = ""
                                last_send_time = current_time
                                await asyncio.sleep(0.01)
                else:
                    await llm_text_queue.put("AI_ERROR:NN detected")
                    
                # # if current_time - last_send_time >= time_interval:
                # if buffer:
                #     # 将buffer添加到LLM文本队列，而不是直接发送
                #     print(f"添加chunk组到队列: {buffer[:20]}..." if len(buffer) > 20 else f"添加chunk组到队列: {buffer}")
                #     await llm_text_queue.put(buffer)
                #     response_chunks.append(buffer)
                #     buffer = ""
                #     last_send_time = current_time
                #     await asyncio.sleep(0.01)
                # 点位5: 开始文本生成
                if voice_id in connection_session_timers:
                    text_generation_count += 1
                    current_text_count = text_generation_count
                    text_gen_start_time = time.time()
                    elapsed = text_gen_start_time - connection_session_timers[voice_id]
                    print(f"【对话{voice_id}计时：第{current_text_count}个文本 开始生成】{elapsed:.3f}s")
        except asyncio.CancelledError:
            print("文本生成任务被取消")
            raise
        except Exception as e:
            print(f"生成过程中出错: {e}")
            await llm_text_queue.put("AI_ERROR:生成过程中出错")
            return
            
        # 发送剩余的buffer
        if buffer and not has_nn:
            # 异常情况下智商不够高的大模型会生成夹杂了[NNNN]字符串的文本，需要在此移除[NNNN]字符串
            if "[NNNN]" in buffer:
                buffer = buffer.replace("[NNNN]", "")
            # 将剩余buffer添加到LLM文本队列，而不是直接发送
            print(f"添加最后chunk组到队列: {buffer[:20]}..." if len(buffer) > 20 else f"添加最后chunk组到队列: {buffer}")
            await llm_text_queue.put(buffer)
            response_chunks.append(buffer)
            
            print(f"发送剩余文本到TTS: {buffer}")
            await task_tts_queue.put(buffer)
        
        # 发送结束信号到TTS队列
        await task_tts_queue.put("__END__")
        
        # 等待TTS任务完成
        if tts_task:
            try:
                await tts_task
            except asyncio.CancelledError:
                print("TTS任务被取消")
            except Exception as e:
                print(f"TTS任务出错: {e}")
        
        await llm_text_queue.put("AI_END")

        print(f"大模型响应完成")
        
    except asyncio.CancelledError:
        print("文本生成任务被取消")
        raise
    except Exception as e:
        print(f"大模型处理过程中出错: {e}")
        try:
            await llm_text_queue.put("AI_ERROR:抱歉，我遇到了一些问题，无法回答您的问题。")
        except Exception:
            print("无法发送错误消息")
    finally:
        print("确保清理所有资源")
        # 确保清理所有资源 - 修改这里，使用tts_task而不是current_tts_task
        if 'tts_task' in locals() and not tts_task.done():
            tts_task.cancel()
            try:
                await tts_task
            except asyncio.CancelledError:
                pass

async def process_tts(websocket: WebSocket, text_queue: asyncio.Queue, voice_id=None, connection_tasks=None, connection_tasks_lock=None, llm_text_queue: asyncio.Queue = None, audio_bytes_queue: asyncio.Queue = None, connection_session_timers: dict = None, connection_cancel_tasks=None):
    """处理TTS的独立任务"""
    try:
        # 持续处理队列中的文本
        waiting_log_printed = False  # 添加标志，避免重复打印等待日志
        
        while True:
            # 检查是否需要取消当前任务
            if connection_cancel_tasks and connection_cancel_tasks.is_set():
                print("TTS音频发送被取消")
                # 清空音频队列
                while not audio_bytes_queue.empty():
                    try:
                        audio_bytes_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                # 向客户端发送取消消息
                try:
                    await llm_text_queue.put("AI_CANCELLED")
                except Exception as e:
                    print(f"发送取消消息时出错: {e}")
                break
                
            try:
                # 等待获取文本（使用阻塞方式，但添加超时）
                if not waiting_log_printed:
                    print("等待TTS文本输入...")
                    waiting_log_printed = True
                
                text = await asyncio.wait_for(text_queue.get(), timeout=5.0)
                waiting_log_printed = False  # 重置标志
                print(f"获取到TTS文本: {text[:20]}..." if len(text) > 20 else f"获取到TTS文本: {text}")
                
                # 检查是否是结束信号
                if text == "__END__":
                    print("收到TTS结束信号")
                    break
                
                # 加强文本验证：检查文本是否为空、只包含空白字符或只包含特殊字符
                if not text or not text.strip() or text.strip() in ['""', '""', '""', '\'\'', '``']:
                    print(f"跳过无效文本: '{text}'")
                    continue
                    
                # 过滤掉可能导致TTS错误的文本
                cleaned_text = text.strip().strip('"').strip("'").strip("`")
                if not cleaned_text:
                    print(f"清理后文本为空，跳过: '{text}'")
                    continue
                
                print(f"TTS处理文本: {cleaned_text[:20]}..." if len(cleaned_text) > 20 else f"TTS处理文本: {cleaned_text}")
                
                tts_start_time = time.time()
                first_chunk_received = False
                
                # 添加重试机制
                max_retries = 3
                retry_count = 0
                
                while retry_count < max_retries:
                    try:
                        # 点位7: 开始语音生成
                        if voice_id in connection_session_timers:
                            tts_generation_count = 1
                            current_tts_count = tts_generation_count
                            elapsed = time.time() - connection_session_timers[voice_id]
                            print(f"【对话{voice_id}计时：第{current_tts_count}个语音 开始生成】{elapsed:.3f}s")
                    
                        success = False
                        async for audio_chunk in tts_service.text_to_speech_stream(cleaned_text):
                            success = True
                            # 只输出第一个包的延迟
                            if current_tts_count == 1:
                                elapsed = time.time() - connection_session_timers[voice_id]
                                print(f"【对话{voice_id}计时：第{current_tts_count}个语音 结束生成】{elapsed:.3f}s")
                                current_tts_count += 1

                            # 再次检查是否被取消
                            if asyncio.current_task().cancelled():
                                print("TTS任务在生成音频时被取消")
                                break
                                
                            if not first_chunk_received:
                                first_chunk_received = True
                                tts_first_chunk_time = time.time()
                                tts_first_chunk_latency = tts_first_chunk_time - tts_start_time
                                print(f"TTS音频块延迟: {tts_first_chunk_latency:.3f}秒")
                                
                            try:
                                # 将音频数据添加到队列，而不是直接发送
                                await audio_bytes_queue.put((voice_id, audio_chunk))
                            
                            except Exception as e:
                                print(f"添加音频数据到队列时出错: {e}")
                                # 如果添加失败，我们应该退出整个处理循环
                                return
                        
                        # 如果成功生成了音频，跳出重试循环
                        if success:
                            # 发送音频包尾
                            try:
                                end_marker = bytes([0x00, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0xFF])
                                await audio_bytes_queue.put((voice_id, end_marker))
                                print("已发送音频包尾标记")
                            except Exception as e:
                                print(f"发送音频包尾时出错: {e}")
                            break
                        else:
                            # 如果没有生成任何音频，视为失败
                            raise Exception("TTS服务未返回任何音频数据")
                            
                    except Exception as e:
                        retry_count += 1
                        error_msg = str(e)
                        print(f"TTS第{retry_count}次尝试失败: {error_msg}")
                        
                        # 根据错误类型决定是否重试
                        if "503" in error_msg or "Invalid response status" in error_msg:
                            # 网络错误，等待后重试
                            if retry_count < max_retries:
                                wait_time = retry_count * 2  # 递增等待时间
                                print(f"网络错误，等待{wait_time}秒后重试...")
                                await asyncio.sleep(wait_time)
                                continue
                        elif "No audio was received" in error_msg:
                            # 参数错误，不重试
                            print(f"参数错误，跳过此文本: '{cleaned_text}'")
                            break
                        else:
                            # 其他错误，短暂等待后重试
                            if retry_count < max_retries:
                                await asyncio.sleep(1)
                                continue
                        
                        # 达到最大重试次数
                        if retry_count >= max_retries:
                            print(f"TTS重试{max_retries}次后仍然失败，跳过此文本: '{cleaned_text}'")
                            # 发送错误标记而不是让整个任务失败
                            try:
                                error_marker = bytes([0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00])
                                await audio_bytes_queue.put((voice_id, error_marker))
                                print("已发送TTS错误标记")
                            except Exception as queue_error:
                                print(f"发送错误标记失败: {queue_error}")
                            break
                
                # 记录结束时间
                tts_end_time = time.time()
                tts_total_time = tts_end_time - tts_start_time
                text_length = len(cleaned_text)
                if text_length > 0:
                    print(f"TTS总处理时间: {tts_total_time:.3f}秒, 文本长度: {text_length}字符, 平均速度: {text_length/tts_total_time:.2f}字符/秒")
                
            except asyncio.TimeoutError:
                # 超时但没有收到文本，继续等待
                # 添加较长的休眠时间，避免频繁循环
                await asyncio.sleep(1.0)  # 增加到1秒
                continue
            except asyncio.CancelledError:
                # 处理取消请求
                print("TTS任务被取消")
                return
            except Exception as e:
                print(f"TTS处理出错: {e}")
                # 短暂暂停后继续
                await asyncio.sleep(0.1)
                continue
            
        # 通知客户端音频结束
        try:
            await llm_text_queue.put("AUDIO_END")
        except Exception as e:
            print(f"发送AUDIO_END时出错: {e}")
    except asyncio.CancelledError:
        print("TTS主任务被取消")
    except Exception as e:
        print(f"TTS处理过程中出错: {e}")
        try:
            await llm_text_queue.put("AUDIO_ERROR:语音合成出错")
        except Exception:
            print("无法发送错误消息")
    finally:
        # 确保TTS任务从活跃任务列表中移除
        current_task = asyncio.current_task()
        if connection_tasks_lock and connection_tasks:
            async with connection_tasks_lock:
                if current_task in connection_tasks:
                    connection_tasks.remove(current_task)
                    print(f"TTS任务完成，从活跃任务列表中移除，当前活跃任务数: {len(connection_tasks)}")

# 添加一个新方法来监控LLM文本队列并发送数据
async def send_llm_text(websocket: WebSocket, llm_text_queue: asyncio.Queue):
    """持续监控LLM文本队列并发送数据"""
    try:
        while True:
            try:
                # 等待获取文本（使用阻塞方式，但添加超时）
                text = await asyncio.wait_for(llm_text_queue.get(), timeout=5.0)

                # 发送文本到客户端
                print(f"发送LLM文本: {text[:20]}..." if len(text) > 20 else f"发送LLM文本: {text}")
                await filtered_send_text(websocket, text)
                
            except asyncio.TimeoutError:
                # 超时但没有收到文本，继续等待
                await asyncio.sleep(0.1)
                continue
            except Exception as e:
                print(f"发送LLM文本时出错: {e}")
                # 短暂暂停后继续
                await asyncio.sleep(0.1)
                continue
    except asyncio.CancelledError:
        print("LLM文本发送任务被取消")
    except Exception as e:
        print(f"LLM文本发送过程中出错: {e}")
        
async def filtered_send_text(websocket: WebSocket, text: str) -> bool:
        """
        发送经过过滤的文本消息
        返回值: 是否成功发送（文本是否通过过滤）
        """
        if filter_text(text):
            await websocket.send_text(text)
            return True
        return False

# 添加一个新方法来监控音频队列并发送数据
async def send_audio_bytes(websocket: WebSocket, llm_text_queue: asyncio.Queue, audio_bytes_queue: asyncio.Queue, connection_cancel_tasks=None):
    """持续监控音频队列并发送数据"""
    try:
        # 记录上一次发送的音频数据所属的会话ID
        last_session_id = None  # 初始化为None，表示尚未发送过任何音频

        while True:
            # 检查是否需要取消当前任务
            if connection_cancel_tasks and connection_cancel_tasks.is_set():
                log_with_time("音频发送被取消")
                # 清空音频队列
                while not audio_bytes_queue.empty():
                    try:
                        audio_bytes_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                break
                
            try:
                # 等待获取音频数据（使用阻塞方式，但添加超时）
                queue_item = await asyncio.wait_for(audio_bytes_queue.get(), timeout=5.0)
                
                # 解析会话ID和音频数据
                session_id, audio_chunk = queue_item
                
                # 再次检查是否被取消
                if connection_cancel_tasks and connection_cancel_tasks.is_set():
                    log_with_time("音频发送过程中检测到取消标志")
                    break
                
                # 检查会话ID是否变化
                if last_session_id is not None and session_id != last_session_id:
                    log_with_time(f"检测到会话ID变化: {last_session_id} -> {session_id}，发送AI_NEWCHAT")
                    await websocket.send_text("AI_NEWCHAT")

                # 更新上一次的会话ID
                last_session_id = session_id

                # 发送音频数据到客户端
                log_with_time(f"发送音频数据: 会话ID {session_id}, {len(audio_chunk)} 字节")
                await websocket.send_bytes(audio_chunk)
                
            except asyncio.TimeoutError:
                # 超时但没有收到音频数据，继续等待
                await asyncio.sleep(0.1)
                continue
            except Exception as e:
                log_with_time(f"发送音频数据时出错: {e}")
                # 短暂暂停后继续
                await asyncio.sleep(0.1)
                continue
    except asyncio.CancelledError:
        log_with_time("音频发送任务被取消")
    except Exception as e:
        log_with_time(f"音频发送过程中出错: {e}")

if __name__ == "__main__":
    print("启动AI服务器...")
    log_with_time("AI服务器启动中...")
    
    # 优化Uvicorn配置
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=7860,
        # 优化性能的配置
        limit_concurrency=1000,          # 增加并发限制
        limit_max_requests=1000,         # 增加请求限制
        timeout_keep_alive=30,           # 保持连接时间
        timeout_graceful_shutdown=30,    # 优雅关闭时间
        # 文件上传优化
        access_log=False,                # 关闭访问日志减少I/O
        # 使用更快的事件循环
        loop="asyncio",
        # 增加工作进程数（可选，如果CPU核心多的话）
        # workers=1,  # 保持单进程，避免状态共享问题
    )
