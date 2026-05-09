# Web 页面 UI 设计自检清单

## P0 — 必须通过

- [ ] **无 :root 外的 hex 色值。** 所有颜色使用 CSS 变量。grep `#[0-9a-fA-F]{3,8}` 在 `:root{}` 外应无结果。
- [ ] **标题使用 var(--font-display)。** h1/h2/h3 不用 sans-serif 作为 display face。
- [ ] **accent 每屏最多 2 次。** eyebrow + primary CTA = 2。第三次出现则降级。
- [ ] **无 emoji 图标。** 使用 SVG 或 feature-mark class。
- [ ] **无占位文字。** 所有 `[REPLACE]` 已替换。无 lorem ipsum、"功能一/功能二"。
- [ ] **data-section 属性。** 每个顶级 `<section>` 都有 `data-section` 标识。
- [ ] **响应式正常。** grid-2/3/4 在 ≤920px 折叠为单列（seed 已处理）。
- [ ] **使用 seed 中已定义的 class。** 不自创全局 class。
- [ ] **交互状态完整。** 按钮有 hover/active/disabled 三态；表单输入有 focus/error 态。

## P1 — 应该通过

- [ ] **section 节奏交替。** 不连续出现两个 stat 行、两个 feature 三列、两个 quote。
- [ ] **标题 ≤ 14 字。** 超过则文案在做设计的工作。
- [ ] **lead 文字 ≤ 2 句。** `.lead` 的 max-width:60ch 已限制宽度。
- [ ] **CTA 按钮说明动作。** "免费试用" 优于 "开始使用"，"查看案例" 优于 "了解更多"。
- [ ] **数字用 .num class。** 价格、统计、版本号。
- [ ] **hover 状态存在。** 所有 `<a>` 和 `.btn` 有 hover 效果（seed 已处理）。
- [ ] **空状态设计。** 列表/卡片区域有"暂无数据"的空状态展示。
- [ ] **加载状态设计。** 数据区域有 skeleton 占位或 loading 指示。
- [ ] **导航高亮。** 当前页面对应的导航项有 active 样式。

## P2 — 加分项

- [ ] **有一个设计亮点。** 一个精心设计的 quote、一个真实感的数据展示、一个产品截图。
- [ ] **topnav 有毛玻璃效果。** seed 已内置 backdrop-filter。
- [ ] **color-mix() 派生色调。** 不额外定义 --accent-50 等 token。
- [ ] **微交互动画。** 卡片 hover 上浮、按钮点击缩放、页面切换过渡。

## 交互设计规范

每个页面必须包含以下交互说明（用 HTML 注释 `<!-- interaction: ... -->` 标注）：

- **按钮交互**: hover 变色/阴影、active 按下效果、disabled 灰色不可点击
- **表单交互**: input focus 边框高亮、校验失败红色边框+错误提示、提交中 loading 态
- **列表交互**: 空状态展示、加载中 skeleton、分页/加载更多
- **导航交互**: 当前页高亮、移动端汉堡菜单展开/收起
- **反馈交互**: 操作成功/失败的 toast 提示、确认弹窗

## 反 AI 味检查

看两秒钟。如果感觉：
- "像每个 AI 创业公司的首页"
- "功能行有图标+标题+三行模糊好处描述"
- "所有数字都是编的"

→ 回去把一个 feature cell 换成具体的产品截图/示例/真实输出，删掉一个 accent 使用。
