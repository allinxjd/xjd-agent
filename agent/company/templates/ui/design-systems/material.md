# Material Design 3

> 类别: 系统级
> Google Material Design 3 (Material You) 风格。适合 Android 应用、跨平台产品。

## 视觉主题
圆润、有层次、色彩丰富但有节制。强调 surface 层级和动态色彩。

## 色彩
- **--bg:** `#FFFBFE` — 页面背景
- **--fg:** `#1C1B1F` — 主文字
- **--accent:** `#6750A4` — Primary，CTA 按钮
- **--muted:** `#79747E` — 次要文字 (on-surface-variant)
- **--border:** `#CAC4D0` — outline
- **--surface:** `#FFFBFE` — surface container

辅助色：
- Secondary: `#625B71`, Tertiary: `#7D5260`
- Success: `#386A20`, Warning: `#7C5800`, Danger: `#BA1A1A`

## 字体
- **Display/标题:** `'Roboto', system-ui, sans-serif`, weight 400-500
- **Body:** `'Roboto', system-ui, sans-serif`, weight 400
- **Mono:** `'Roboto Mono', monospace`
- 字号阶梯 (M3 type scale): 11 · 12 · 14 · 16 · 22 · 24 · 28 · 32 · 36 · 45 · 57
- 行高: body 1.5, headline 1.2, display 1.1

## 组件样式
- **按钮:** 20px 全圆角(pill), 内距 12px 24px。Filled = accent 底 + 白字。Outlined = 1px border + accent 字。Text = 无底无框。
- **卡片:** surface 底, 12px 圆角, 16px 内距。Elevated = 1dp shadow。Filled = surface-variant 底色。Outlined = 1px border。
- **输入框:** Outlined = 4px 圆角 + 1px border + floating label。Filled = 底部 surface-variant + 底线。
- **FAB:** 16px 圆角, 56x56, accent 底色 + 图标。

## 布局
- 响应式断点: compact(<600) / medium(600-840) / expanded(>840)
- Navigation: compact 用 bottom bar, expanded 用 navigation rail
- 内容区最大宽度 840px (compact) / 1200px (expanded)
- 间距: 16px (compact) / 24px (medium) / 32px (expanded)

## 规则
- 圆角统一用 M3 shape scale: 4/8/12/16/28px
- 阴影用 elevation 系统: 0/1/2/3/4/5 dp
- 状态层: hover=8% opacity, pressed=12%, focus=12%
- 动效: emphasized 500ms, standard 300ms, emphasized-decelerate 400ms
- 图标: Material Symbols Outlined, 24px, weight 400
