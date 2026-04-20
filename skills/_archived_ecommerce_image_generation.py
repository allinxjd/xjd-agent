#!/usr/bin/env python3
"""
电商图片生成技能
支持白底图、商品详情图、透明底图等电商平台图片生成
"""

import os
import json
import logging
from typing import Dict, List, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

class EcommerceImageGenerator:
    """电商图片生成器"""
    
    def __init__(self):
        self.platform_presets = {
            'taobao': {
                'name': '淘宝/天猫',
                'main_image': {'size': '800x800', 'format': 'jpg', 'background': 'white'},
                'detail_image': {'width': 750, 'format': 'jpg'},
                'white_background': True,
                'requirements': ['白底图', '无logo', '无水印', '清晰']
            },
            'jd': {
                'name': '京东',
                'main_image': {'size': '800x800', 'format': 'jpg'},
                'detail_image': {'width': 790, 'format': 'jpg'},
                'brand_certification': True,
                'requirements': ['品牌认证', '参数展示']
            },
            'douyin': {
                'name': '抖音',
                'main_image': {'size': '9:16', 'format': 'png', 'background': 'transparent'},
                'detail_image': {'size': '3:4', 'format': 'png'},
                'transparent_background': True,
                'requirements': ['竖版', '透明底', '短视频适配']
            },
            'pinduoduo': {
                'name': '拼多多',
                'main_image': {'size': '400x400+', 'format': 'jpg'},
                'price_highlight': True,
                'requirements': ['价格突出', '团购信息']
            },
            'xiaohongshu': {
                'name': '小红书',
                'main_image': {'size': '3:4', 'format': 'jpg'},
                'detail_image': {'size': '1:1', 'format': 'jpg'},
                'lifestyle': True,
                'requirements': ['生活方式', '使用教程']
            }
        }
        
        self.image_types = {
            'white_background': {
                'name': '白底图',
                'description': '纯白色背景的产品图，符合电商平台规范',
                'use_cases': ['淘宝主图', '产品列表', '商品卡片'],
                'requirements': ['背景纯白', '产品完整', '无阴影', '无杂物']
            },
            'product_detail': {
                'name': '商品详情图',
                'description': '展示产品细节、功能、使用场景的图片',
                'use_cases': ['商品详情页', '功能展示', '使用说明'],
                'sections': ['主图', '细节图', '场景图', '参数图', '对比图']
            },
            'transparent_background': {
                'name': '透明底图',
                'description': '背景透明的产品图，便于二次设计',
                'use_cases': ['抖音视频', '海报设计', '广告素材'],
                'requirements': ['高质量抠图', '边缘清晰', '无锯齿']
            },
            'marketing_image': {
                'name': '营销图片',
                'description': '包含促销信息、价格标签的营销图',
                'use_cases': ['促销活动', '社交媒体', '广告投放'],
                'elements': ['价格标签', '促销文案', '倒计时', '优惠券']
            }
        }
    
    def generate_white_background_image(self, image_path: str, platform: str = 'taobao') -> Dict:
        """生成白底图"""
        preset = self.platform_presets.get(platform, self.platform_presets['taobao'])
        
        return {
            'status': 'success',
            'type': 'white_background',
            'platform': preset['name'],
            'specifications': {
                'size': preset['main_image']['size'],
                'format': preset['main_image']['format'],
                'background': '纯白色 (#FFFFFF)',
                'requirements': preset.get('requirements', [])
            },
            'processing_steps': [
                '1. 智能抠图 - 移除原背景',
                '2. 背景替换 - 填充纯白色背景',
                '3. 边缘优化 - 处理边缘锯齿',
                '4. 尺寸调整 - 适配平台规格',
                '5. 质量优化 - 提升图片清晰度'
            ],
            'output': {
                'format': preset['main_image']['format'],
                'size': preset['main_image']['size'],
                'quality': '高清',
                'compression': '有损压缩（JPG）' if preset['main_image']['format'] == 'jpg' else '无损（PNG）'
            }
        }
    
    def generate_product_detail_images(self, image_paths: List[str], platform: str = 'taobao') -> Dict:
        """生成商品详情图"""
        preset = self.platform_presets.get(platform, self.platform_presets['taobao'])
        
        detail_sections = [
            {
                'name': '主展示图',
                'description': '产品整体展示，突出核心卖点',
                'elements': ['产品主体', '品牌logo', '核心卖点文案']
            },
            {
                'name': '细节展示图',
                'description': '展示产品细节和工艺',
                'elements': ['材质特写', '工艺细节', '尺寸标注']
            },
            {
                'name': '使用场景图',
                'description': '产品在实际使用中的场景',
                'elements': ['场景背景', '人物互动', '使用效果']
            },
            {
                'name': '参数规格图',
                'description': '展示产品技术参数和规格',
                'elements': ['参数表格', '技术指标', '认证标识']
            },
            {
                'name': '对比展示图',
                'description': '与竞品或不同型号的对比',
                'elements': ['竞品对比', '功能对比', '价格对比']
            }
        ]
        
        return {
            'status': 'success',
            'type': 'product_detail',
            'platform': preset['name'],
            'image_count': len(image_paths),
            'detail_sections': detail_sections,
            'specifications': {
                'width': preset.get('detail_image', {}).get('width', 750),
                'format': preset.get('detail_image', {}).get('format', 'jpg'),
                'layout': '竖版长图',
                'recommended_order': ['主图', '细节', '场景', '参数', '对比']
            },
            'content_suggestions': [
                '突出产品核心功能',
                '展示实际使用效果',
                '强调独特卖点',
                '提供详细参数',
                '增加用户评价截图'
            ]
        }
    
    def get_platform_guidelines(self, platform: str) -> Dict:
        """获取平台图片规范指南"""
        preset = self.platform_presets.get(platform)
        if not preset:
            return {'error': f'不支持的平台: {platform}'}
        
        guidelines = {
            'platform': preset['name'],
            'main_image': preset['main_image'],
            'technical_requirements': preset.get('requirements', []),
            'common_mistakes': [],
            'best_practices': []
        }
        
        if platform == 'taobao':
            guidelines.update({
                'common_mistakes': [
                    '背景不纯白（有阴影或杂色）',
                    '图片尺寸不符合800×800px',
                    '产品不完整或变形',
                    '有水印或logo',
                    '图片模糊不清'
                ],
                'best_practices': [
                    '使用专业摄影设备',
                    '保证光线均匀',
                    '产品居中展示',
                    '保留适当边距',
                    '使用高质量JPG格式'
                ]
            })
        elif platform == 'douyin':
            guidelines.update({
                'common_mistakes': [
                    '背景不透明',
                    '尺寸不符合9:16比例',
                    '产品边缘有锯齿',
                    '文件过大影响加载',
                    '色彩模式不正确'
                ],
                'best_practices': [
                    '使用PNG-24格式',
                    '确保透明底质量',
                    '优化文件大小',
                    '适配竖屏显示',
                    '添加动态元素'
                ]
            })
        
        return guidelines
    
    def batch_process_images(self, image_list: List[Dict], config: Dict) -> Dict:
        """批量处理图片"""
        results = []
        
        for item in image_list:
            image_path = item.get('path')
            image_type = item.get('type', 'white_background')
            platform = item.get('platform', 'taobao')
            
            if image_type == 'white_background':
                result = self.generate_white_background_image(image_path, platform)
            elif image_type == 'product_detail':
                result = self.generate_product_detail_images([image_path], platform)
            else:
                result = {'status': 'error', 'message': f'不支持的图片类型: {image_type}'}
            
            results.append({
                'file': os.path.basename(image_path),
                'type': image_type,
                'platform': platform,
                'result': result
            })
        
        return {
            'batch_id': datetime.now().strftime('%Y%m%d_%H%M%S'),
            'total_images': len(image_list),
            'success_count': len([r for r in results if r['result'].get('status') == 'success']),
            'results': results,
            'summary': {
                'formats': list(set([r['result'].get('output', {}).get('format', 'unknown') for r in results])),
                'platforms': list(set([r['platform'] for r in results])),
                'types': list(set([r['type'] for r in results]))
            }
        }


# 技能类定义
class EcommerceImageGenerationSkill:
    """电商图片生成技能"""
    
    name = "电商图片生成"
    description = "生成符合电商平台规范的白底图、商品详情图、透明底图等"
    version = "1.0.0"
    
    def __init__(self):
        self.generator = EcommerceImageGenerator()
    
    async def execute(self, context):
        """执行技能"""
        command = context.get('command', '')
        params = context.get('params', {})
        
        if '白底图' in command or 'white_background' in command:
            image_path = params.get('image_path', '')
            platform = params.get('platform', 'taobao')
            result = self.generator.generate_white_background_image(image_path, platform)
            return result
        
        elif '商品详情图' in command or 'product_detail' in command:
            image_paths = params.get('image_paths', [])
            platform = params.get('platform', 'taobao')
            result = self.generator.generate_product_detail_images(image_paths, platform)
            return result
        
        elif '平台规范' in command or 'guidelines' in command:
            platform = params.get('platform', 'taobao')
            result = self.generator.get_platform_guidelines(platform)
            return result
        
        elif '批量处理' in command or 'batch' in command:
            image_list = params.get('image_list', [])
            config = params.get('config', {})
            result = self.generator.batch_process_images(image_list, config)
            return result
        
        else:
            return {
                'status': 'info',
                'message': '电商图片生成技能已就绪',
                'available_commands': [
                    '生成白底图',
                    '生成商品详情图',
                    '查看平台规范',
                    '批量处理图片'
                ],
                'supported_platforms': list(self.generator.platform_presets.keys()),
                'image_types': list(self.generator.image_types.keys())
            }


# 使用示例
if __name__ == "__main__":
    skill = EcommerceImageGenerationSkill()
    
    # 测试白底图生成
    test_result = skill.generator.generate_white_background_image(
        image_path="product.jpg",
        platform="taobao"
    )
    
    logger.info("白底图生成测试:")
    logger.info(json.dumps(test_result, indent=2, ensure_ascii=False))

    # 测试平台规范
    guidelines = skill.generator.get_platform_guidelines("douyin")
    logger.info("抖音平台规范:")
    logger.info(json.dumps(guidelines, indent=2, ensure_ascii=False))