# 中性现代

> 类别: 通用
> 干净、产品导向的默认风格。适用于 B2B 工具、仪表盘、实用型页面。

## 视觉主题
冷静、功能性、低调自信。无装饰。内容优先，界面退后。

## 色彩
- **--bg:** `#FAFAFA` — 页面背景
- **--fg:** `#111111` — 主文字
- **--accent:** `#2F6FEB` — CTA 按钮、链接、每屏最多一个强调元素
- **--muted:** `#6B6B6B` — 次要文字、说明
- **--border:** `#E5E5E5` — 分割线、边框
- **--surface:** `#FFFFFF` — 卡片、弹窗

辅助色（仅用于状态）：
- Success: `#17A34A`, Warning: `#EAB308`, Danger: `#DC2626`

## 字体
- **Display/标题:** `'Inter', -apple-system, system-ui, sans-serif`, weight 600
- **Body:** `'Inter', -apple-system, system-ui, sans-serif`, weight 400
- **Mono:** `ui-monospace, 'JetBrains Mono', monospace`
- 字号阶梯: 12 · 14 · 16 · 20 · 24 · 32 · 48
- 行高: body 1.5, heading 1.2

## 组件样式
- **按钮:** 8px 圆角, 10px 上下内距, 16px 左右内距。Primary = accent 填充 + 白字。Secondary = 1px 边框 + 透明底。
- **卡片:** 白底, 1px 边框, 12px 圆角, 20px 内距, 无阴影。
- **输入框:** 1px 边框, 8px 圆角, 10px 上下内距, focus 时 accent 边框。
- **链接:** accent 色, 无下划线, hover 时下划线。

## 布局
- 12 列网格, 1200px 最大宽度, 24px 间距。
- Hero: 40-60vh。内容上偏，不垂直居中。
- Section 间距: 桌面 80px, 平板 48px, 手机 32px。
- 用留白分隔，分割线仅用于无关联的顶级区块之间。

## 规则
- 每屏 accent 最多出现 2 次
- 不使用渐变（除非 hero 区域极少量 accent→accent-80%）
- 输入框无阴影
- 同一屏不超过 3 种字号
- 标题用 sentence-case
