# 技能：卡拉贝斯AI自动生成

## 技能名称
`calabash_auto_generate`

## 学习来源
基于用户实际操作观察和学习

## 已验证的操作流程

### 完整步骤（已验证）
1. **访问平台**：打开 https://ai.calabashai.cn
2. **上传图片**：点击上传按钮 → 选择图片文件
3. **选择功能**：在功能菜单中选择"产品单图"
4. **选择平台**：在平台菜单中选择"抖音"
5. **设置参数**：选择"透明背景"选项
6. **开始生成**：点击生成按钮，等待AI处理
7. **下载结果**：点击下载按钮，保存到本地

### 关键参数
- **输入文件**：PNG格式，800×800像素
- **生成功能**：产品单图
- **目标平台**：抖音
- **背景要求**：透明背景
- **输出格式**：PNG（保持透明）

## 文件处理模式

### 输入文件
- **位置**：用户桌面或其他指定位置
- **命名**：建议有意义的名称，如 `xjdlogo.PNG`
- **规格**：PNG格式，适当尺寸

### 输出文件
- **命名规则**：平台使用哈希值命名，如 `166f9d195666d99e3079a25df71f7730.PNG`
- **保存位置**：用户选择的下载位置（默认可能是桌面）
- **文件属性**：保持原图尺寸和质量

## 自动化方案

### 方案A：完整指导脚本
```bash
#!/bin/bash
# calabash_auto_guide.sh

IMAGE_PATH="$1"
PLATFORM="${2:-douyin}"
FUNCTION="${3:-product_single}"

echo "=== 卡拉贝斯AI自动生成指导 ==="
echo "图片: $(basename "$IMAGE_PATH")"
echo "平台: $PLATFORM"
echo "功能: $FUNCTION"
echo ""

# 1. 打开平台
open "https://ai.calabashai.cn"
echo "✅ 步骤1: 平台已打开"

# 2. 等待用户操作提示
echo ""
echo "📋 请按以下顺序操作："
echo "   [1] 点击上传按钮"
echo "   [2] 选择文件: $IMAGE_PATH"
echo "   [3] 选择功能: $FUNCTION"
echo "   [4] 选择平台: $PLATFORM"
echo "   [5] 选择透明背景"
echo "   [6] 点击生成按钮"
echo "   [7] 等待处理完成"
echo "   [8] 点击下载保存"
echo ""
echo "💡 提示: 文件位置: $IMAGE_PATH"
```

### 方案B：文件预处理脚本
```bash
#!/bin/bash
# calabash_prepare.sh

# 检查并准备图片文件
check_image() {
    local img="$1"
    
    echo "检查图片: $img"
    
    # 检查文件存在
    if [ ! -f "$img" ]; then
        echo "错误: 文件不存在"
        return 1
    fi
    
    # 检查文件格式
    local ext="${img##*.}"
    if [[ ! "$ext" =~ ^(png|PNG|jpg|JPG|jpeg|JPEG)$ ]]; then
        echo "警告: 建议使用PNG或JPG格式"
    fi
    
    # 检查文件大小
    local size=$(stat -f%z "$img")
    if [ $size -gt 10485760 ]; then  # 10MB
        echo "警告: 文件较大，可能影响上传速度"
    fi
    
    echo "✅ 图片准备就绪"
    return 0
}

# 批量处理支持
batch_process() {
    local dir="$1"
    
    echo "批量处理目录: $dir"
    
    for img in "$dir"/*.{png,PNG,jpg,JPG,jpeg,JPEG} 2>/dev/null; do
        if [ -f "$img" ]; then
            echo "准备处理: $(basename "$img")"
            # 这里可以添加实际处理逻辑
        fi
    done
}
```

### 方案C：结果整理脚本
```bash
#!/bin/bash
# calabash_organize.sh

# 整理生成的文件
organize_results() {
    local source_dir="$1"
    local target_dir="${2:-~/Desktop/Calabash_Results}"
    local prefix="${3:-generated}"
    
    mkdir -p "$target_dir"
    
    echo "整理生成文件..."
    echo "源目录: $source_dir"
    echo "目标目录: $target_dir"
    
    # 查找最近生成的图片文件
    find "$source_dir" -name "*.png" -o -name "*.PNG" -o -name "*.jpg" -o -name "*.JPG" 2>/dev/null | \
    while read -r file; do
        # 获取文件修改时间（最近1小时内）
        if [ $(find "$file" -mmin -60 2>/dev/null) ]; then
            local filename=$(basename "$file")
            local newname="${prefix}_${filename}"
            cp "$file" "$target_dir/$newname"
            echo "已复制: $filename → $newname"
        fi
    done
    
    echo "✅ 整理完成: $target_dir"
}
```

## 错误处理逻辑

### 常见问题及解决
1. **上传失败**
   - 检查网络连接
   - 验证文件格式和大小
   - 重新上传尝试

2. **生成失败**
   - 检查平台服务状态
   - 尝试更换AI模型
   - 简化生成参数

3. **下载问题**
   - 检查浏览器下载设置
   - 确认存储空间
   - 重新下载尝试

### 自动化检测
```bash
# 检测平台状态
check_platform_status() {
    if curl -s -I "https://ai.calabashai.cn" | grep -q "200"; then
        echo "✅ 平台可访问"
        return 0
    else
        echo "❌ 平台不可访问"
        return 1
    fi
}
```

## 优化建议

### 命名优化
建议用户下载后重命名文件：
```bash
# 示例：重命名生成的文件
mv ~/Desktop/166f9d195666d99e3079a25df71f7730.PNG \
   ~/Desktop/xjd_douyin_transparent_$(date +%Y%m%d).PNG
```

### 批量处理优化
1. 建立文件列表
2. 使用相同配置批量处理
3. 自动整理结果文件

### 质量检查
1. 验证图片尺寸
2. 检查透明度
3. 确认文件完整性

## 使用示例

### 单个文件生成
```bash
# 指导用户生成抖音透明底图
./calabash_auto_guide.sh ~/Desktop/xjdlogo.PNG douyin product_single
```

### 批量处理
```bash
# 准备批量处理
./calabash_prepare.sh ~/Desktop/product_images/

# 整理生成结果
./calabash_organize.sh ~/Desktop ~/Documents/Generated_Images "product"
```

## 学习验证
- ✅ 已验证完整操作流程
- ✅ 确认文件生成模式
- ✅ 理解参数设置方式
- ✅ 掌握错误处理要点

## 后续改进方向
1. 如果平台开放API，实现全自动化
2. 开发浏览器扩展辅助工具
3. 建立模板配置系统
4. 集成到工作流自动化中