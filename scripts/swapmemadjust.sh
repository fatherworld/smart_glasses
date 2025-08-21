# 1. 创建一个2GB的交换文件（路径可自定义，如/swapfile）
dd if=/dev/zero of=/swapfile bs=200k count=100  # bs=块大小，count=块数（2048×1M=2GB）

# 2. 设置文件权限（仅root可访问）
chmod 600 /swapfile

# 3. 格式化为交换空间
mkswap /swapfile

# 4. 启用交换空间
swapon /swapfile

# 5. 验证是否生效
free -h  # 查看Swap行是否有数值