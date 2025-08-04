#!/bin/bash
# 增强版音频服务器依赖安装脚本

echo "🚀 安装增强版音频服务器依赖..."

# 检查Python版本
python_version=$(python3 --version 2>/dev/null | cut -d' ' -f2)
if [ -z "$python_version" ]; then
    echo "❌ 未找到Python3，请先安装Python3"
    exit 1
fi

echo "✅ Python版本: $python_version"

# 更新包管理器
echo "📦 更新包管理器..."
sudo apt update

# 安装系统级音频依赖
echo "📦 安装系统级音频依赖..."
sudo apt install -y \
    python3-pip \
    python3-dev \
    portaudio19-dev \
    alsa-utils \
    alsamixer \
    libasound2-dev \
    libportaudio2 \
    libportaudiocpp0 \
    ffmpeg \
    pulseaudio \
    pulseaudio-utils

# 检查pip
if ! command -v pip3 &> /dev/null; then
    echo "❌ pip3未安装，请安装pip3"
    exit 1
fi

# 升级pip
echo "📦 升级pip..."
python3 -m pip install --upgrade pip

# 安装Python依赖
echo "📦 安装Python依赖..."
python3 -m pip install \
    pyaudio \
    numpy \
    wave \
    threading \
    pathlib

# 验证安装
echo "🔍 验证安装..."

# 检查PyAudio
python3 -c "import pyaudio; print('✅ PyAudio安装成功')" || {
    echo "❌ PyAudio安装失败，尝试手动安装..."
    # 尝试从源码编译安装
    sudo apt install -y python3-pyaudio
    python3 -c "import pyaudio; print('✅ PyAudio安装成功')" || {
        echo "❌ PyAudio安装仍然失败，请手动处理"
        echo "尝试运行: sudo apt install python3-pyaudio"
    }
}

# 检查NumPy
python3 -c "import numpy; print('✅ NumPy安装成功')" || {
    echo "❌ NumPy安装失败"
    exit 1
}

# 检查音频设备
echo "🔊 检查音频设备..."
if command -v aplay &> /dev/null; then
    echo "🎵 可用的播放设备:"
    aplay -l
else
    echo "⚠️ aplay命令不可用"
fi

if command -v arecord &> /dev/null; then
    echo "🎤 可用的录音设备:"
    arecord -l
else
    echo "⚠️ arecord命令不可用"
fi

# 创建音频目录
echo "📁 创建音频目录..."
mkdir -p received_audio
chmod 755 received_audio

echo ""
echo "🎉 依赖安装完成！"
echo ""
echo "💡 使用方法:"
echo "   python3 enhanced_mock_server.py --host 0.0.0.0 --port 8082"
echo ""
echo "📁 音频文件将保存到: ./received_audio/"
echo ""
echo "🎮 服务器运行时可用命令:"
echo "   start      - 发送开始录音指令"
echo "   stop       - 发送结束录音指令"
echo "   list       - 显示保存的音频文件"
echo "   play <文件名>  - 播放指定音频文件"
echo "   send <文件名>  - 发送音频文件到客户端"
echo "   status     - 显示服务器状态"
echo "   quit       - 退出服务器"
echo "" 