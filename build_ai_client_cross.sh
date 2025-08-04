#!/bin/bash

# AR眼镜 ai_client_socket 交叉编译和部署脚本
# 适用于 RV1103B 芯片

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 配置信息
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
AI_CLIENT_DIR="$PROJECT_ROOT/project/test/ai_client_socket"
MEDIA_DIR="$PROJECT_ROOT/media"
OUTPUT_DIR="$PROJECT_ROOT/output/out"

# 从BoardConfig读取配置
source "$PROJECT_ROOT/.BoardConfig.mk"

# 工具链配置
TOOLCHAIN_DIR="$PROJECT_ROOT/tools/linux/toolchain/$RK_TOOLCHAIN_CROSS"
CROSS_COMPILE="$TOOLCHAIN_DIR/bin/$RK_TOOLCHAIN_CROSS"
CC="${CROSS_COMPILE}-gcc"
AR="${CROSS_COMPILE}-ar"
STRIP="${CROSS_COMPILE}-strip"

# 媒体库路径
MEDIA_OUTPUT="$OUTPUT_DIR/media_out"
INCLUDE_PATH="$MEDIA_OUTPUT/include"
LIB_PATH="$MEDIA_OUTPUT/lib"

# 打印带颜色的消息
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查环境
check_environment() {
    print_info "检查编译环境..."
    
    # 检查工具链
    if [ ! -f "$CC" ]; then
        print_error "交叉编译器不存在: $CC"
        print_info "请运行以下命令安装工具链："
        echo "  cd $TOOLCHAIN_DIR"
        echo "  source env_install_toolchain.sh"
        exit 1
    fi
    
    # 测试工具链
    if ! "$CC" --version >/dev/null 2>&1; then
        print_error "交叉编译器不可用"
        exit 1
    fi
    
    # 检查源文件
    if [ ! -f "$AI_CLIENT_DIR/ai_client_socket.c" ]; then
        print_error "源文件不存在: $AI_CLIENT_DIR/ai_client_socket.c"
        exit 1
    fi
    
    print_success "环境检查通过"
    
    # 显示配置信息
    print_info "编译配置："
    echo "  芯片: $RK_CHIP"
    echo "  架构: $RK_ARCH"
    echo "  工具链: $RK_TOOLCHAIN_CROSS"
    echo "  交叉编译器: $CC"
    echo "  源文件: $AI_CLIENT_DIR/ai_client_socket.c"
    echo "  媒体库路径: $MEDIA_OUTPUT"
}

# 构建媒体库
build_media_libs() {
    print_info "检查并构建媒体库..."
    
    # 检查是否已经存在媒体库
    if [ -f "$LIB_PATH/librockit.so" ]; then
        print_success "媒体库已存在，跳过构建"
        return 0
    fi
    
    print_info "媒体库不存在，开始构建..."
    
    # 进入媒体目录并构建
    cd "$MEDIA_DIR"
    
    # 清理之前的构建
    make clean || true
    
    # 构建媒体库
    print_info "正在构建媒体库，这可能需要几分钟..."
    make -j$(nproc)
    
    if [ $? -eq 0 ]; then
        print_success "媒体库构建完成"
    else
        print_error "媒体库构建失败"
        exit 1
    fi
    
    cd "$PROJECT_ROOT"
}

# ai_client_start_stop
compile_ai_client() {
    print_info "编译 ai_client_start_stop..."
    
    cd "$AI_CLIENT_DIR"
    
    # 清理之前的编译
    rm -f ai_client_socket out/bin/ai_client_socket
    
    # 设置编译参数
    CFLAGS="-g -Wall -O2"
    CFLAGS="$CFLAGS -I$INCLUDE_PATH"
    CFLAGS="$CFLAGS -I$INCLUDE_PATH/rkaiq"
    CFLAGS="$CFLAGS -I$INCLUDE_PATH/rkaiq/uAPI2"
    CFLAGS="$CFLAGS -D_LARGEFILE_SOURCE -D_LARGEFILE64_SOURCE -D_FILE_OFFSET_BITS=64"
    CFLAGS="$CFLAGS -DRKPLATFORM=ON -DARCH64=OFF -DRV1103B"
    CFLAGS="$CFLAGS -DUAPI2"
    CFLAGS="$CFLAGS -mfpu=neon -mfloat-abi=hard"
    
    LDFLAGS="-Wl,-rpath-link,$LIB_PATH"
    LDFLAGS="$LDFLAGS -L$LIB_PATH"
    LDFLAGS="$LDFLAGS -lpthread -lm -lrockit_full"
    LDFLAGS="$LDFLAGS -lrockchip_mpp -lrga"
    LDFLAGS="$LDFLAGS -Wl,--gc-sections -Wl,--as-needed"
    
    print_info "编译参数："
    echo "  CC: $CC"
    echo "  CFLAGS: $CFLAGS"
    echo "  LDFLAGS: $LDFLAGS"
    
    # 编译
    print_info "正在编译..."
    "$CC" ai_client_start_stop2.c test_comm_argparse.c -o ai_client_start_stop $CFLAGS $LDFLAGS
    
    if [ $? -eq 0 ] && [ -f "ai_client_start_stop" ]; then
        print_success "编译成功"
        
        # 创建输出目录
        mkdir -p out/bin
        cp ai_client_start_stop out/bin/
        
        # 显示文件信息
        print_info "编译结果："
        ls -la ai_client_start_stop
        file ai_client_start_stop
        
        # 检查依赖库
        print_info "依赖库检查："
        "$CROSS_COMPILE-objdump" -p ai_client_start_stop | grep NEEDED || true
        
    else
        print_error "编译失败"
        exit 1
    fi
    
    cd "$PROJECT_ROOT"
}

# 检查ADB连接
check_adb_connection() {
    print_info "检查ADB设备连接..."
    
    if ! command -v adb &> /dev/null; then
        print_error "ADB未安装或不在PATH中"
        exit 1
    fi
    
    devices=$(adb devices | grep -v "List of devices" | grep "device$" | wc -l)
    if [ $devices -eq 0 ]; then
        print_error "没有检测到ADB设备连接"
        print_info "请确保："
        echo "  1. 设备已连接并开启USB调试"
        echo "  2. 设备已授权ADB连接"
        exit 1
    fi
    
    device_id=$(adb devices | grep "device$" | awk '{print $1}' | head -n1)
    print_success "检测到设备: $device_id"
}

# 部署到设备
deploy_to_device() {
    print_info "部署到AR眼镜设备..."
    
    check_adb_connection
    
    # 创建目标目录
    print_info "创建目标目录..."
    adb shell "mkdir -p /oem/usr/bin /oem/usr/lib"
    
    # 推送可执行文件
    print_info "推送 ai_client_start_stop 到设备..."
    adb push "$AI_CLIENT_DIR/ai_client_start_stop" /oem/usr/bin/
    
    # 设置可执行权限
    adb shell "chmod +x /oem/usr/bin/ai_client_start_stop"
    
    # 推送必要的库文件
    print_info "推送依赖库..."
    if [ -d "$LIB_PATH" ]; then
        # 只推送必要的库文件
        for lib in librockit_full.so librockit.so librockchip_mpp.so librga.so; do
            if [ -f "$LIB_PATH/$lib" ]; then
                print_info "推送 $lib..."
                adb push "$LIB_PATH/$lib" /oem/usr/lib/
            fi
        done
        
        # 推送其他.so文件
        adb shell "mkdir -p /oem/usr/lib"
        find "$LIB_PATH" -name "*.so*" -exec adb push {} /oem/usr/lib/ \; 2>/dev/null || true
    fi
    
    print_success "部署完成"
}

# 创建启动脚本
create_startup_script() {
    print_info "创建设备端启动脚本..."
    
    cat > /tmp/start_ai_client.sh << 'EOF'
#!/bin/sh

# AR眼镜 AI客户端启动脚本

echo "=== AR眼镜 AI音频客户端 ==="
echo "正在启动AI客户端..."

# 设置库路径
export LD_LIBRARY_PATH="/oem/usr/lib:/usr/lib:$LD_LIBRARY_PATH"

# 检查程序文件
if [ ! -f "/oem/usr/bin/ai_client_start_stop" ]; then
    echo "错误：AI客户端程序不存在"
    exit 1
fi

# 显示帮助信息
echo ""
echo "使用方法："
echo "  /oem/usr/bin/ai_client_start_stop [选项]"
echo ""
echo "常用选项："
echo "  -o <file>           输出PCM文件路径"
echo "  -t <seconds>        录音时长(秒)"
echo "  --enable-upload     启用服务器上传"
echo "  --server <host>     服务器地址"
echo "  --port <port>       服务器端口"
echo "  --enable-gpio       启用GPIO触发录音"
echo "  --help              显示详细帮助"
echo ""
echo "示例："
echo "  # 录音10秒并保存到文件"
echo "  /oem/usr/bin/ai_client_start_stop -o /tmp/test.pcm -t 10"
echo ""
echo "  # 启用Socket上传到AI服务器"
echo "  /oem/usr/bin/ai_client_start_stop --enable-upload --server 192.168.1.100 --port 7861"
echo ""
echo "  # 启用GPIO触发录音"
echo "  /oem/usr/bin/ai_client_start_stop --enable-gpio --enable-upload --server 192.168.1.100"
echo ""

# 如果没有参数，显示帮助后退出
if [ $# -eq 0 ]; then
    echo "请提供参数以启动AI客户端"
    exit 0
fi

# 运行AI客户端
cd /oem/usr/bin
exec ./ai_client_start_stop "$@"
EOF

    # 推送启动脚本到设备
    adb push /tmp/start_ai_client.sh /oem/usr/bin/
    adb shell "chmod +x /oem/usr/bin/start_ai_client.sh"
    
    print_success "启动脚本已创建: /oem/usr/bin/start_ai_client.sh"
}

# 创建测试脚本
create_test_script() {
    print_info "创建测试脚本..."
    
    cat > /tmp/test_ai_client.sh << 'EOF'
#!/bin/sh

# AR眼镜 AI客户端测试脚本

echo "=== AR眼镜 AI客户端测试 ==="

# 设置库路径
export LD_LIBRARY_PATH="/oem/usr/lib:/usr/lib:$LD_LIBRARY_PATH"

cd /oem/usr/bin

echo "1. 基础录音测试（5秒）..."
./ai_client_start_stop -o /tmp/test_basic.pcm -t 5

if [ -f "/tmp/test_basic.pcm" ]; then
    echo "✓ 基础录音测试成功，文件大小: $(wc -c < /tmp/test_basic.pcm) 字节"
else
    echo "✗ 基础录音测试失败"
    exit 1
fi

echo ""
echo "2. 音频播放测试..."
./ai_client_start_stop --test-play /tmp/test_basic.pcm --playback-rate 16000

echo ""
echo "3. 检查GPIO状态..."
if [ -f "/sys/kernel/debug/gpio" ]; then
    echo "✓ GPIO调试接口可用"
    head -5 /sys/kernel/debug/gpio
else
    echo "✗ GPIO调试接口不可用"
fi

echo ""
echo "4. 网络连接测试..."
if ping -c 1 8.8.8.8 >/dev/null 2>&1; then
    echo "✓ 网络连接正常"
else
    echo "✗ 网络连接异常"
fi

echo ""
echo "测试完成！如需完整功能测试，请配置AI服务器地址："
echo "  ./ai_client_start_stop --enable-upload --server <服务器IP> --port 7861"
EOF

    # 推送测试脚本到设备
    adb push /tmp/test_ai_client.sh /oem/usr/bin/
    adb shell "chmod +x /oem/usr/bin/test_ai_client.sh"
    
    print_success "测试脚本已创建: /oem/usr/bin/test_ai_client.sh"
}

# 获取设备信息
get_device_info() {
    print_info "获取设备信息..."
    
    echo "设备信息："
    adb shell "uname -a" || true
    
    echo ""
    echo "网络信息："
    DEVICE_IP=$(adb shell "hostname -I | awk '{print \$1}'" | tr -d '\r' 2>/dev/null || echo "未知")
    echo "  设备IP: $DEVICE_IP"
    
    echo ""
    echo "存储信息："
    adb shell "df -h | grep -E '(oem|userdata|rootfs)'" || true
    
    echo ""
    echo "音频设备："
    adb shell "ls -la /dev/snd/ 2>/dev/null || echo '音频设备信息不可用'"
}

# 显示使用说明
show_usage() {
    cat << EOF
AR眼镜 AI客户端交叉编译和部署工具

用法: $0 [选项]

选项:
  build          只编译，不部署
  deploy         只部署，不编译
  test           编译、部署并运行测试
  all            编译、部署和配置（默认）
  clean          清理编译输出
  help           显示此帮助信息

示例:
  $0              # 完整编译和部署
  $0 build        # 只编译
  $0 deploy       # 只部署（需要先编译）
  $0 test         # 编译、部署并测试
  $0 clean        # 清理编译文件

编译后的程序将部署到设备的 /oem/usr/bin/ai_client_start_stop
EOF
}

# 清理编译输出
clean_build() {
    print_info "清理编译输出..."
    
    rm -f "$AI_CLIENT_DIR/ai_client_start_stop"
    rm -rf "$AI_CLIENT_DIR/out"
    
    print_success "清理完成"
}

# 主函数
main() {
    print_info "=== AR眼镜 AI客户端交叉编译部署工具 ==="
    print_info "开始时间: $(date)"
    echo ""
    
    # 解析参数
    ACTION=${1:-all}
    
    case $ACTION in
        "build")
            #check_environment
            #build_media_libs
            compile_ai_client
            ;;
        "deploy")
            if [ ! -f "$AI_CLIENT_DIR/ai_client_start_stop" ]; then
                print_error "可执行文件不存在，请先运行编译"
                exit 1
            fi
            deploy_to_device
            create_startup_script
            create_test_script
            get_device_info
            ;;
        "test")
            check_environment
            build_media_libs
            compile_ai_client
            deploy_to_device
            create_startup_script
            create_test_script
            get_device_info
            print_info "开始运行设备端测试..."
            adb shell "/oem/usr/bin/test_ai_client.sh"
            ;;
        "clean")
            clean_build
            ;;
        "help")
            show_usage
            exit 0
            ;;
        "all"|*)
            check_environment
            build_media_libs
            compile_ai_client
            deploy_to_device
            create_startup_script
            create_test_script
            get_device_info
            ;;
    esac
    
    print_success "=== 操作完成 ==="
    
    if [ "$ACTION" = "all" ] || [ "$ACTION" = "deploy" ] || [ "$ACTION" = "test" ]; then
        echo ""
        print_info "设备端使用说明："
        echo "  启动脚本: adb shell '/oem/usr/bin/start_ai_client.sh'"
        echo "  测试脚本: adb shell '/oem/usr/bin/test_ai_client.sh'"
        echo "  直接运行: adb shell '/oem/usr/bin/ai_client_start_stop --help'"
        echo ""
        print_info "示例用法："
        echo "  # 基础录音测试"
        echo "  adb shell '/oem/usr/bin/ai_client_start_stop -o /tmp/test.pcm -t 5'"
        echo ""
        echo "  # AI服务器通信（需要先启动服务器）"
        echo "  adb shell '/oem/usr/bin/ai_client_start_stop --enable-upload --server 192.168.1.100 --port 7861'"
        echo ""
        echo "  # GPIO触发录音模式"
        echo "  adb shell '/oem/usr/bin/ai_client_start_stop --enable-gpio --enable-upload --server 192.168.1.100'"
    fi
}

# 错误处理
trap 'print_error "脚本执行出错，请检查上面的错误信息"' ERR

# 运行主函数
main "$@" 