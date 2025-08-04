#!/usr/bin/env python3
"""
HTTP文件上传接口测试脚本
用于测试嵌入式设备的音频文件上传和处理功能
优化版本，解决VPN等网络问题
"""

import requests
import sys
import time
import os

def test_audio_upload(audio_file_path, server_url="http://xiaoyunyun.com:7860", response_format="json"):
    """
    测试音频文件上传接口
    
    Args:
        audio_file_path: 音频文件路径
        server_url: 服务器地址 (使用127.0.0.1而不是localhost避免VPN问题)
        response_format: 响应格式 ("json" 或 "audio")
    """
    
    url = f"{server_url}/process_audio_file"
    
    try:
        print(f"开始上传音频文件: {audio_file_path}")
        print(f"服务器地址: {url}")
        print(f"响应格式: {response_format}")
        print("-" * 50)
        
        # 检查文件是否存在
        if not os.path.exists(audio_file_path):
            print(f"❌ 文件不存在: {audio_file_path}")
            return
        
        file_size = os.path.getsize(audio_file_path)
        print(f"📁 文件大小: {file_size} bytes ({file_size/1024:.2f} KB)")
        
        overall_start = time.time()
        
        # 准备文件和参数
        file_prep_start = time.time()
        with open(audio_file_path, 'rb') as audio_file:
            files = {
                'audio_file': (os.path.basename(audio_file_path), audio_file, 'audio/wav')
            }
            data = {
                'response_format': response_format
            }
            
            file_prep_end = time.time()
            print(f"📁 文件准备耗时: {file_prep_end - file_prep_start:.3f}s")
            
            # 发送HTTP请求
            request_start = time.time()
            print(f"🚀 开始发送HTTP请求 - {request_start:.3f}")
            
            # 创建会话并配置
            session = requests.Session()
            session.trust_env = False  # 禁用代理，避免VPN干扰
            
            # 准备请求
            request_prep_start = time.time()
            req = requests.Request(
                'POST',
                url,
                files=files,
                data=data
            )
            prepared = session.prepare_request(req)
            
            request_prep_end = time.time()
            print(f"📋 请求对象准备完成，耗时: {request_prep_end - request_prep_start:.3f}s")
            
            # 实际发送请求
            send_start = time.time()
            print(f"📡 开始发送请求到服务器 - {send_start:.3f}")
            
            # 发送请求，设置合理的超时
            response = session.send(
                prepared, 
                timeout=(5, 30),  # (连接超时5秒, 读取超时30秒)
                stream=False
            )
            
            send_end = time.time()
            send_duration = send_end - send_start
            print(f"📡 服务器响应接收完成，网络耗时: {send_duration:.3f}s")
            
            # 解析响应
            response_parse_start = time.time()
            print(f"📋 开始解析响应 - {response_parse_start:.3f}")
            
            overall_end = time.time()
            total_duration = overall_end - overall_start
            
            print(f"✅ 请求完成，总耗时: {total_duration:.2f}秒")
            print(f"📊 详细耗时分解:")
            print(f"   - 文件准备: {file_prep_end - file_prep_start:.3f}s")
            print(f"   - 请求准备: {request_prep_end - request_prep_start:.3f}s")
            print(f"   - 网络传输: {send_duration:.3f}s")
            print(f"   - 总耗时: {total_duration:.3f}s")
            print(f"HTTP状态码: {response.status_code}")
            
            if response.status_code == 200:
                response_parse_end = time.time()
                parse_duration = response_parse_end - response_parse_start
                print(f"📋 响应解析耗时: {parse_duration:.3f}s")
                
                if response_format == "json":
                    try:
                        result = response.json()
                        print("\n=== 处理结果 ===")
                        print(f"会话ID: {result.get('session_id', 'N/A')}")
                        print(f"用户语音识别: {result.get('user_text', 'N/A')}")
                        print(f"AI回复: {result.get('ai_text', 'N/A')}")
                        
                        if 'timing' in result:
                            timing = result['timing']
                            print("\n=== 性能统计 ===")
                            print(f"ASR耗时: {timing.get('asr_duration', 0):.2f}秒")
                            print(f"LLM耗时: {timing.get('llm_duration', 0):.2f}秒")
                            print(f"总耗时: {timing.get('total_duration', 0):.2f}秒")
                            
                            # 性能分析
                            server_total = timing.get('total_duration', 0)
                            network_overhead = total_duration - server_total
                            print(f"\n=== 性能分析 ===")
                            print(f"服务器处理时间: {server_total:.2f}秒")
                            print(f"网络传输开销: {network_overhead:.2f}秒")
                            print(f"网络效率: {(server_total/total_duration*100):.1f}%")
                            
                    except Exception as e:
                        print(f"解析JSON响应失败: {e}")
                        print(f"原始响应: {response.text[:500]}")
                        
                elif response_format == "audio":
                    # 保存音频文件
                    output_file = f"output_audio_{int(time.time())}.wav"
                    with open(output_file, 'wb') as f:
                        f.write(response.content)
                    print(f"音频文件已保存为: {output_file}")
                    print(f"音频文件大小: {len(response.content)} bytes")
                    
            else:
                print(f"❌ 请求失败: {response.status_code}")
                print(f"错误信息: {response.text}")
    
    except requests.exceptions.Timeout:
        error_time = time.time() - overall_start
        print(f"❌ 请求超时，耗时: {error_time:.3f}s")
        print("💡 建议检查服务器是否正常运行")
        
    except requests.exceptions.ConnectionError as e:
        error_time = time.time() - overall_start
        print(f"❌ 连接错误，耗时: {error_time:.3f}s")
        print(f"错误详情: {e}")
        print("💡 建议检查:")
        print("   1. 服务器是否启动 (python AIServer/AIServer.py)")
        print("   2. 端口7860是否被占用")
        print("   3. 防火墙设置")
        print("   4. VPN是否影响本地连接")
        
    except Exception as e:
        error_time = time.time() - overall_start
        print(f"❌ 请求失败，耗时: {error_time:.3f}s，错误: {e}")
    
    print("=" * 50)

def test_embedded_device_simulation():
    """模拟嵌入式设备的使用场景"""
    print("🤖 模拟嵌入式设备场景测试")
    print("=" * 50)
    
    # 模拟多个连续请求
    test_files = [
        "AIServer/test_query.mp3",
        "AIServer/test_submit.mp3", 
        "AIServer/volcano_test.mp3"
    ]
    
    for i, test_file in enumerate(test_files, 1):
        if os.path.exists(test_file):
            print(f"\n🔄 第{i}次请求:")
            test_audio_upload(test_file, response_format="json")
            time.sleep(1)  # 模拟嵌入式设备的间隔
        else:
            print(f"⚠️ 测试文件不存在: {test_file}")

def test_performance_benchmark():
    """性能基准测试"""
    print("📊 性能基准测试")
    print("=" * 50)
    
    test_file = "AIServer/test_query.mp3"
    if not os.path.exists(test_file):
        print(f"❌ 基准测试文件不存在: {test_file}")
        return
    
    num_tests = 3
    durations = []
    
    for i in range(num_tests):
        print(f"\n🔄 第{i+1}/{num_tests}次基准测试:")
        start_time = time.time()
        test_audio_upload(test_file, response_format="json")
        duration = time.time() - start_time
        durations.append(duration)
        
        if i < num_tests - 1:
            time.sleep(2)  # 间隔2秒
    
    # 统计结果
    avg_duration = sum(durations) / len(durations)
    min_duration = min(durations)
    max_duration = max(durations)
    
    print(f"\n📈 基准测试结果:")
    print(f"   平均耗时: {avg_duration:.2f}秒")
    print(f"   最快耗时: {min_duration:.2f}秒") 
    print(f"   最慢耗时: {max_duration:.2f}秒")
    print(f"   标准偏差: {(max_duration - min_duration):.2f}秒")

def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("使用方法:")
        print("  python test_http_upload.py <音频文件路径> [响应格式]")
        print("  python test_http_upload.py simulate     # 模拟嵌入式设备测试")
        print("  python test_http_upload.py benchmark    # 性能基准测试")
        print("")
        print("示例:")
        print("  python test_http_upload.py recording.wav json")
        print("  python test_http_upload.py recording.wav audio")
        print("  python test_http_upload.py AIServer/test_query.mp3")
        return
    
    if sys.argv[1] == "simulate":
        test_embedded_device_simulation()
    elif sys.argv[1] == "benchmark":
        test_performance_benchmark()
    else:
        audio_file = sys.argv[1]
        response_format = sys.argv[2] if len(sys.argv) > 2 else "json"
        
        print("🎵 HTTP音频上传测试")
        print("=" * 50)
        test_audio_upload(audio_file, response_format=response_format)

if __name__ == "__main__":
    main() 