#!/usr/bin/env python3
"""
飞书群管理技能
提供完整的群管理功能
"""

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class GroupMember:
    """群成员信息"""
    user_id: str
    name: str
    join_time: datetime
    last_active: datetime
    message_count: int = 0
    role: str = "member"  # member, admin, owner

@dataclass
class GroupInfo:
    """群信息"""
    chat_id: str
    name: str
    description: str
    owner_id: str
    member_count: int
    created_time: datetime
    members: Dict[str, GroupMember] = None
    
    def __post_init__(self):
        if self.members is None:
            self.members = {}

class FeishuGroupManager:
    """飞书群管理器"""
    
    def __init__(self, config_path: str = "config/feishu_config.yaml"):
        """
        初始化群管理器
        
        Args:
            config_path: 配置文件路径
        """
        self.config_path = config_path
        self.config = self._load_config()
        self.groups: Dict[str, GroupInfo] = {}
        self.keywords_to_monitor = self.config.get("group_management", {}).get("monitor_keywords", [])
        
        logger.info("飞书群管理器初始化完成")
        
    def _load_config(self) -> Dict:
        """加载配置文件"""
        try:
            import yaml
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            return {}
    
    def welcome_new_member(self, chat_id: str, user_id: str, user_name: str) -> str:
        """
        欢迎新成员
        
        Args:
            chat_id: 群聊ID
            user_id: 用户ID
            user_name: 用户名
            
        Returns:
            欢迎消息
        """
        welcome_template = """
🎉 欢迎 {user_name} 加入群聊！

我是小巨蛋智能助手，可以帮你：
📌 **常用功能**
• 问答助手 - 回答各种问题
• 任务管理 - 创建/跟踪任务
• 日程提醒 - 设置重要提醒
• 信息查询 - 快速查找信息

📋 **群管理**
• 成员统计 - 查看群活跃度
• 消息摘要 - 每日群聊摘要
• 内容监控 - 维护群聊环境

💡 **使用方式**
1. @我 + 问题 → 智能回答
2. 输入"帮助" → 查看完整功能
3. 输入"任务" → 管理任务

有任何问题随时问我哦！😊
        """
        
        message = welcome_template.format(
            user_name=user_name,
            group_name=self.groups.get(chat_id, GroupInfo("", "", "", "", 0, datetime.now())).name
        )
        
        # 记录新成员
        if chat_id in self.groups:
            self.groups[chat_id].members[user_id] = GroupMember(
                user_id=user_id,
                name=user_name,
                join_time=datetime.now(),
                last_active=datetime.now()
            )
            self.groups[chat_id].member_count = len(self.groups[chat_id].members)
        
        logger.info(f"欢迎新成员: {user_name} 加入群 {chat_id}")
        return message.strip()
    
    def monitor_message(self, chat_id: str, user_id: str, message: str) -> Optional[Dict]:
        """
        监控消息内容
        
        Args:
            chat_id: 群聊ID
            user_id: 用户ID
            message: 消息内容
            
        Returns:
            处理结果或None
        """
        # 检查敏感词
        for keyword in self.keywords_to_monitor:
            if keyword in message:
                warning = f"⚠️ 检测到敏感词 '{keyword}'，请遵守群规。"
                logger.warning(f"敏感词警告: {user_id} 在 {chat_id} 发送: {message}")
                
                return {
                    "action": "warn",
                    "message": warning,
                    "user_id": user_id,
                    "keyword": keyword
                }
        
        # 更新成员活跃度
        if chat_id in self.groups and user_id in self.groups[chat_id].members:
            member = self.groups[chat_id].members[user_id]
            member.last_active = datetime.now()
            member.message_count += 1
        
        return None
    
    def get_group_stats(self, chat_id: str) -> Dict:
        """
        获取群统计信息
        
        Args:
            chat_id: 群聊ID
            
        Returns:
            统计信息
        """
        if chat_id not in self.groups:
            return {"error": "群聊不存在"}
        
        group = self.groups[chat_id]
        now = datetime.now()
        
        # 计算活跃成员（24小时内发言）
        active_members = [
            member for member in group.members.values()
            if now - member.last_active < timedelta(hours=24)
        ]
        
        # 计算今日消息数
        today_start = datetime(now.year, now.month, now.day)
        today_messages = sum(
            1 for member in group.members.values()
            if member.last_active >= today_start
        )
        
        # 最活跃成员
        top_active = sorted(
            group.members.values(),
            key=lambda m: m.message_count,
            reverse=True
        )[:5]
        
        return {
            "group_name": group.name,
            "total_members": group.member_count,
            "active_members": len(active_members),
            "today_messages": today_messages,
            "created_time": group.created_time.strftime("%Y-%m-%d"),
            "top_active_members": [
                {"name": m.name, "messages": m.message_count}
                for m in top_active
            ],
            "activity_rate": f"{(len(active_members) / group.member_count * 100):.1f}%"
        }
    
    def generate_daily_summary(self, chat_id: str) -> str:
        """
        生成每日群聊摘要
        
        Args:
            chat_id: 群聊ID
            
        Returns:
            摘要消息
        """
        stats = self.get_group_stats(chat_id)
        
        if "error" in stats:
            return "无法生成摘要：群聊不存在"
        
        summary = f"""
📊 **{stats['group_name']} 每日摘要** ({datetime.now().strftime('%Y-%m-%d')})

👥 **成员统计**
• 总成员数：{stats['total_members']} 人
• 活跃成员：{stats['active_members']} 人
• 活跃度：{stats['activity_rate']}

💬 **消息统计**
• 今日消息：{stats['today_messages']} 条

🏆 **今日活跃榜**
{chr(10).join(f'• {m["name"]}: {m["messages"]} 条' for m in stats['top_active_members'])}

💡 **温馨提示**
• 保持友好交流，遵守群规
• 有问题随时 @我
• 输入"统计"查看详细数据

祝大家交流愉快！🎯
        """
        
        return summary.strip()
    
    def set_reminder(self, chat_id: str, time: str, message: str) -> Dict:
        """
        设置群提醒
        
        Args:
            chat_id: 群聊ID
            time: 提醒时间 (格式: "HH:MM" 或 "YYYY-MM-DD HH:MM")
            message: 提醒内容
            
        Returns:
            设置结果
        """
        try:
            # 解析时间
            if " " in time:
                reminder_time = datetime.strptime(time, "%Y-%m-%d %H:%M")
            else:
                # 假设是今天的时间
                today = datetime.now().date()
                reminder_time = datetime.strptime(f"{today} {time}", "%Y-%m-%d %H:%M")
            
            # 检查时间是否已过
            if reminder_time < datetime.now():
                return {"success": False, "error": "提醒时间不能是过去时间"}
            
            # 这里实际应该保存到数据库或任务队列
            # 简化版本：返回成功信息
            return {
                "success": True,
                "reminder_time": reminder_time.strftime("%Y-%m-%d %H:%M"),
                "message": message,
                "chat_id": chat_id
            }
            
        except ValueError as e:
            return {"success": False, "error": f"时间格式错误: {e}"}
    
    def handle_command(self, chat_id: str, user_id: str, command: str) -> str:
        """
        处理群命令
        
        Args:
            chat_id: 群聊ID
            user_id: 用户ID
            command: 命令
            
        Returns:
            响应消息
        """
        command = command.strip().lower()
        
        if command == "帮助" or command == "help":
            return self._get_help_message()
        
        elif command == "统计" or command == "stats":
            stats = self.get_group_stats(chat_id)
            if "error" in stats:
                return stats["error"]
            
            stats_text = f"""
📈 **群聊统计**
群名：{stats['group_name']}
成员：{stats['total_members']} 人（活跃 {stats['active_members']} 人）
今日消息：{stats['today_messages']} 条
活跃度：{stats['activity_rate']}

🏆 **活跃榜**
{chr(10).join(f'{i+1}. {m["name"]}: {m["messages"]} 条' for i, m in enumerate(stats['top_active_members']))}
            """
            return stats_text.strip()
        
        elif command == "摘要" or command == "summary":
            return self.generate_daily_summary(chat_id)
        
        elif command.startswith("提醒"):
            # 解析提醒命令格式：提醒 时间 内容
            parts = command.split(" ", 2)
            if len(parts) < 3:
                return "提醒命令格式：提醒 时间 内容\n例如：提醒 14:30 开会"
            
            _, time, content = parts
            result = self.set_reminder(chat_id, time, content)
            if result["success"]:
                return f"✅ 提醒设置成功：{result['reminder_time']} - {content}"
            else:
                return f"❌ 设置失败：{result['error']}"
        
        elif command == "成员" or command == "members":
            if chat_id not in self.groups:
                return "群聊信息未加载"
            
            group = self.groups[chat_id]
            members_list = "\n".join([
                f"• {member.name} ({'管理员' if member.role == 'admin' else '成员'})"
                for member in group.members.values()
            ])
            
            return f"👥 群成员列表（共 {group.member_count} 人）：\n{members_list}"
        
        else:
            return "未知命令，请输入'帮助'查看可用命令"
    
    def _get_help_message(self) -> str:
        """获取帮助消息"""
        return """
🤖 **小巨蛋群管理助手 - 帮助手册**

📌 **基础命令**
• 帮助 / help - 显示此帮助信息
• 统计 / stats - 查看群聊统计
• 摘要 / summary - 生成每日摘要
• 成员 / members - 查看成员列表

⏰ **提醒功能**
• 提醒 时间 内容 - 设置群提醒
  示例：提醒 14:30 开会
  示例：提醒 2024-01-15 10:00 项目评审

🔧 **管理功能**（管理员可用）
• 监控关键词 [添加/删除/列表] - 管理敏感词
• 欢迎消息 [设置/查看] - 管理欢迎消息
• 成员管理 [禁言/移除] - 管理成员

💬 **智能功能**
• @我 + 问题 - 智能问答
• 翻译 [文本] - 翻译文本
• 计算 [表达式] - 数学计算

📞 **支持与反馈**
遇到问题或建议，请联系管理员。
        """.strip()

# 使用示例
if __name__ == "__main__":
    # 创建群管理器实例
    manager = FeishuGroupManager()
    
    # 模拟群信息
    test_chat_id = "test_chat_123"
    manager.groups[test_chat_id] = GroupInfo(
        chat_id=test_chat_id,
        name="测试群",
        description="这是一个测试群",
        owner_id="owner_123",
        member_count=10,
        created_time=datetime.now()
    )
    
    # 测试欢迎消息
    welcome_msg = manager.welcome_new_member(test_chat_id, "user_456", "张三")
    logger.info("欢迎消息示例:")
    logger.info(welcome_msg)
    logger.info("\n" + "="*50 + "\n")

    # 测试命令处理
    commands = ["帮助", "统计", "提醒 15:00 开会"]
    for cmd in commands:
        response = manager.handle_command(test_chat_id, "user_456", cmd)
        logger.info("命令: %s", cmd)
        logger.info("响应: %s", response)
        logger.info("")