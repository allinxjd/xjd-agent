# shadcn/ui 风格

> 类别: 现代极简
> 受 shadcn/ui 启发，单色调、工具感、开发者友好。

## 视觉主题
极简、精确、工具感。黑白为主，accent 极度克制。适合 SaaS、开发者工具、管理后台。

## 色彩
- **--bg:** `#FFFFFF` — 页面背景
- **--fg:** `#09090B` — 主文字
- **--accent:** `#18181B` — CTA 按钮（黑底白字）
- **--muted:** `#71717A` — 次要文字
- **--border:** `#E4E4E7` — 分割线
- **--surface:** `#FAFAFA` — 卡片背景

辅助色：
- Success: `#16A34A`, Warning: `#D97706`, Danger: `#DC2626`

## 字体
- **Display/标题:** `'Geist', -apple-system, system-ui, sans-serif`, weight 600
- **Body:** `'Geist', -apple-system, system-ui, sans-serif`, weight 400
- **Mono:** `'Geist Mono', ui-monospace, monospace`
- 字号阶梯: 12 · 14 · 16 · 20 · 24 · 32
- 行高: body 1.5, heading 1.2

## 组件样式
- **按钮:** 6px 圆角, 9px 上下内距, 16px 左右内距。Primary = 黑底白字。Secondary = 白底 + 1px 边框。Ghost = 透明底 + hover 灰底。
- **卡片:** surface 底色, 1px 边框, 8px 圆角, 24px 内距, 无阴影。
- **输入框:** 1px 边框, 6px 圆角, 8px 上下内距, focus 时 ring-2 黑色。
- **Badge:** 极小圆角, 12px 字号, 内距 2px 8px。

## 布局
- 最大宽度 1100px, 居中。
- 间距紧凑: section 间 64px, 组件间 16px。
- 偏好垂直堆叠，少用多列网格。

## 规则
- 不使用彩色 accent（黑白为主）
- 不使用阴影（仅 dropdown/modal 用极淡阴影）
- 圆角统一 6px 或 8px，不混用
- 图标用 Lucide，线宽 1.5px
- 动效极短: 150ms ease
