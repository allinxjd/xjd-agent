#!/usr/bin/env python3
"""
基础图片处理工具
提供电商图片处理的基本功能
"""

import os
import sys
import logging
from pathlib import Path
from typing import Tuple, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    logger.warning("Pillow库未安装，部分功能不可用。安装命令: pip install Pillow")

class EcommerceImageProcessor:
    """电商图片处理器"""
    
    # 平台规格定义
    PLATFORM_SPECS = {
        'taobao': {
            'main_image': (800, 800),
            'detail_image_width': 750,
            'format': 'JPEG',
            'background_color': (255, 255, 255),  # 白色
            'quality': 95
        },
        'jd': {
            'main_image': (800, 800),
            'detail_image_width': 790,
            'format': 'JPEG',
            'background_color': (255, 255, 255),
            'quality': 95
        },
        'douyin': {
            'main_image': (1080, 1920),  # 9:16
            'detail_image': (1080, 1440),  # 3:4
            'format': 'PNG',
            'background_color': None,  # 透明
            'quality': 100
        },
        'pinduoduo': {
            'main_image': (400, 400),
            'format': 'JPEG',
            'background_color': (255, 255, 255),
            'quality': 90
        },
        'xiaohongshu': {
            'main_image': (1080, 1440),  # 3:4
            'detail_image': (1080, 1080),  # 1:1
            'format': 'JPEG',
            'background_color': (255, 255, 255),
            'quality': 95
        }
    }
    
    def __init__(self):
        if not HAS_PIL:
            raise ImportError("请先安装Pillow库: pip install Pillow")
    
    def create_white_background_image(self, image_path: str, platform: str = 'taobao') -> dict:
        """创建白底图"""
        try:
            # 获取平台规格
            specs = self.PLATFORM_SPECS.get(platform, self.PLATFORM_SPECS['taobao'])
            target_size = specs['main_image']
            bg_color = specs['background_color']
            output_format = specs['format']
            
            # 打开原始图片
            with Image.open(image_path) as img:
                # 转换为RGB模式（如果是RGBA）
                if img.mode in ('RGBA', 'LA'):
                    # 创建白色背景
                    background = Image.new('RGB', img.size, bg_color)
                    # 合并图层
                    background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                    img = background
                elif img.mode != 'RGB':
                    img = img.convert('RGB')
                
                # 调整尺寸
                img = self._resize_image(img, target_size)
                
                # 保存图片
                output_path = self._get_output_path(image_path, f"white_bg_{platform}")
                img.save(output_path, format=output_format, quality=specs.get('quality', 95))
                
                return {
                    'status': 'success',
                    'input': image_path,
                    'output': output_path,
                    'platform': platform,
                    'size': img.size,
                    'format': output_format,
                    'background': 'white' if bg_color == (255, 255, 255) else 'transparent'
                }
                
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e),
                'input': image_path
            }
    
    def resize_for_platform(self, image_path: str, platform: str = 'taobao', image_type: str = 'main') -> dict:
        """调整图片尺寸以适应平台"""
        try:
            specs = self.PLATFORM_SPECS.get(platform, self.PLATFORM_SPECS['taobao'])
            
            if image_type == 'main':
                target_size = specs['main_image']
            elif image_type == 'detail':
                target_width = specs.get('detail_image_width', 750)
                # 保持宽高比
                with Image.open(image_path) as img:
                    width, height = img.size
                    new_height = int(height * (target_width / width))
                    target_size = (target_width, new_height)
            else:
                target_size = specs['main_image']
            
            with Image.open(image_path) as img:
                img = self._resize_image(img, target_size)
                
                output_path = self._get_output_path(image_path, f"resized_{platform}_{image_type}")
                img.save(output_path, format=specs['format'], quality=specs.get('quality', 95))
                
                return {
                    'status': 'success',
                    'input': image_path,
                    'output': output_path,
                    'platform': platform,
                    'type': image_type,
                    'original_size': Image.open(image_path).size,
                    'new_size': img.size,
                    'format': specs['format']
                }
                
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e),
                'input': image_path
            }
    
    def batch_process(self, image_list: List[dict], operation: str = 'resize', platform: str = 'taobao') -> dict:
        """批量处理图片"""
        results = []
        
        for item in image_list:
            image_path = item.get('path')
            image_type = item.get('type', 'main')
            
            if operation == 'white_background':
                result = self.create_white_background_image(image_path, platform)
            elif operation == 'resize':
                result = self.resize_for_platform(image_path, platform, image_type)
            else:
                result = {'status': 'error', 'message': f'不支持的操作: {operation}'}
            
            results.append({
                'file': os.path.basename(image_path),
                'operation': operation,
                'result': result
            })
        
        return {
            'batch_id': datetime.now().strftime('%Y%m%d_%H%M%S'),
            'operation': operation,
            'platform': platform,
            'total': len(image_list),
            'success': len([r for r in results if r['result'].get('status') == 'success']),
            'failed': len([r for r in results if r['result'].get('status') == 'error']),
            'results': results
        }
    
    def get_platform_info(self, platform: str = 'all') -> dict:
        """获取平台图片规格信息"""
        if platform == 'all':
            return {
                'platforms': list(self.PLATFORM_SPECS.keys()),
                'specifications': self.PLATFORM_SPECS
            }
        elif platform in self.PLATFORM_SPECS:
            return {
                'platform': platform,
                'specifications': self.PLATFORM_SPECS[platform]
            }
        else:
            return {
                'status': 'error',
                'message': f'不支持的平台: {platform}',
                'available_platforms': list(self.PLATFORM_SPECS.keys())
            }
    
    def _resize_image(self, img: Image.Image, target_size: Tuple[int, int]) -> Image.Image:
        """调整图片尺寸（保持宽高比）"""
        # 计算缩放比例
        width, height = img.size
        target_width, target_height = target_size
        
        # 计算缩放比例
        width_ratio = target_width / width
        height_ratio = target_height / height
        ratio = min(width_ratio, height_ratio)
        
        # 计算新尺寸
        new_width = int(width * ratio)
        new_height = int(height * ratio)
        
        # 调整尺寸
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # 如果尺寸不足，填充背景
        if new_width < target_width or new_height < target_height:
            new_img = Image.new('RGB', target_size, (255, 255, 255))
            # 计算居中位置
            x = (target_width - new_width) // 2
            y = (target_height - new_height) // 2
            new_img.paste(img, (x, y))
            img = new_img
        
        return img
    
    def _get_output_path(self, input_path: str, suffix: str) -> str:
        """生成输出路径"""
        path = Path(input_path)
        output_dir = path.parent / 'processed'
        output_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_name = f"{path.stem}_{suffix}_{timestamp}{path.suffix}"
        return str(output_dir / output_name)


# 命令行接口
def main():
    """命令行主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='电商图片处理工具')
    parser.add_argument('operation', choices=['white_bg', 'resize', 'batch', 'info'], 
                       help='操作类型')
    parser.add_argument('--input', '-i', help='输入图片路径')
    parser.add_argument('--platform', '-p', default='taobao', 
                       choices=['taobao', 'jd', 'douyin', 'pinduoduo', 'xiaohongshu'],
                       help='目标平台')
    parser.add_argument('--type', '-t', default='main', choices=['main', 'detail'],
                       help='图片类型（主图/详情图）')
    parser.add_argument('--list', '-l', help='批量处理列表文件（JSON格式）')
    
    args = parser.parse_args()
    
    processor = EcommerceImageProcessor()
    
    if args.operation == 'white_bg':
        if not args.input:
            logger.error("需要指定输入图片路径")
            return

        result = processor.create_white_background_image(args.input, args.platform)
        logger.info("白底图生成结果: %s", result)
    
    elif args.operation == 'resize':
        if not args.input:
            logger.error("需要指定输入图片路径")
            return

        result = processor.resize_for_platform(args.input, args.platform, args.type)
        logger.info("尺寸调整结果: %s", result)
    
    elif args.operation == 'info':
        result = processor.get_platform_info(args.platform if args.platform != 'all' else 'all')
        logger.info("平台规格信息: %s", result)
    
    elif args.operation == 'batch':
        if not args.list:
            logger.error("需要指定批量处理列表文件")
            return
        
        import json
        with open(args.list, 'r', encoding='utf-8') as f:
            image_list = json.load(f)
        
        result = processor.batch_process(image_list, 'resize', args.platform)
        logger.info("批量处理结果: %s", json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if not HAS_PIL:
        logger.error("请先安装Pillow库: pip install Pillow")
        sys.exit(1)
    
    main()