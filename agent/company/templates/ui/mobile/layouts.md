# 移动端屏幕布局库

**12 个 paste-ready 屏幕原型。** 粘贴到 seed.html 的 `<div class="content">` 中。不要从零写屏幕——选最接近的原型，粘贴，替换文案。

## 使用前

1. 读完 seed.html 的 `<style>` 块，了解所有可用 class
2. 选择**恰好一个**原型。一个屏幕只做一件事
3. 如果原型带 tabbar，保留；否则删除 `<nav class="tabbar">` 块

## Class 清单

> `pad` `pad-sm` `stack` `stack-sm` `row` `row-between` `grid-2` `grid-3` `section-gap` `h1` `h2` `h3` `body` `meta` `caption` `num` `link` `card` `card-flat` `cell` `cell-body` `cell-title` `cell-desc` `cell-arrow` `cell-value` `avatar` `avatar-sm` `avatar-lg` `btn` `btn-primary` `btn-default` `btn-warn` `btn-ghost` `btn-sm` `tag` `pill` `pill active` `input-group` `input` `search-bar` `badge` `tabbar` `tab` `tab active` `tab-icon` `navbar` `back` `action` `title` `ph-img` `ph-img square` `ph-img wide` `skeleton` `sheet` `sheet-handle` `modal` `modal-content` `modal-actions` `toast` `actionsheet` `actionsheet-item` `actionsheet-cancel` `empty` `empty-icon`

---

## 原型 A — 列表/Feed（首页/消息/动态）

顶部: 标题。主体: 4-6 个列表项。底部: Tab Bar。

```html
<div class="navbar">
  <span class="title">消息</span>
  <button class="action">+</button>
</div>
<div class="content">
  <div class="pad-sm">
    <div class="row" style="overflow-x:auto;gap:8px;">
      <span class="pill active">全部</span>
      <span class="pill">未读</span>
      <span class="pill">关注</span>
    </div>
  </div>
  <div>
    <div class="cell">
      <div class="avatar"></div>
      <div class="cell-body">
        <div class="cell-title">[REPLACE] 发送者名称</div>
        <div class="cell-desc">[REPLACE] 消息摘要内容</div>
      </div>
      <span class="meta">2分钟前</span>
    </div>
    <div class="cell">
      <div class="avatar"></div>
      <div class="cell-body">
        <div class="cell-title">[REPLACE] 发送者名称</div>
        <div class="cell-desc">[REPLACE] 消息摘要内容</div>
      </div>
      <span class="meta">14分钟前</span>
    </div>
    <div class="cell">
      <div class="avatar"></div>
      <div class="cell-body">
        <div class="cell-title">[REPLACE] 发送者名称</div>
        <div class="cell-desc">[REPLACE] 消息摘要内容</div>
      </div>
      <span class="meta">1小时前</span>
    </div>
  </div>
</div>
<nav class="tabbar">
  <a class="tab active"><div class="tab-icon"></div>首页</a>
  <a class="tab"><div class="tab-icon"></div>发现</a>
  <a class="tab"><div class="tab-icon"></div>消息</a>
  <a class="tab"><div class="tab-icon"></div>我的</a>
</nav>
```

## 原型 B — 详情页（文章/商品/个人主页）

顶部: 返回 + 标题。主体: 大图 + 标题 + 描述 + 操作按钮。无 Tab Bar。

```html
<div class="navbar">
  <button class="back">←</button>
  <span class="title">[REPLACE] 详情</span>
  <button class="action">分享</button>
</div>
<div class="content">
  <div class="ph-img wide" style="border-radius:0;">[REPLACE] 封面图</div>
  <div class="pad stack">
    <h1 class="h2">[REPLACE] 标题文字</h1>
    <div class="row">
      <div class="avatar-sm"></div>
      <span class="meta">[REPLACE] 作者 · 2024-01-15</span>
    </div>
    <p class="body">[REPLACE] 详细描述内容，2-3 行文字说明。</p>
    <div class="section-gap"></div>
    <div class="card">
      <div class="row-between">
        <span class="h3">[REPLACE] 价格/关键信息</span>
        <span class="tag">[REPLACE] 标签</span>
      </div>
    </div>
    <button class="btn btn-primary">[REPLACE] 主操作按钮</button>
  </div>
</div>
```

## 原型 C — 引导/注册（欢迎/登录/注册）

居中布局。大标题 + 说明 + 输入框 + 按钮。无 Tab Bar。

```html
<div class="content pad" style="justify-content:center;">
  <div class="stack" style="gap:24px;">
    <div class="stack-sm" style="text-align:center;">
      <h1 class="h1">[REPLACE] 欢迎标题</h1>
      <p class="meta">[REPLACE] 一句话说明</p>
    </div>
    <div style="background:var(--surface);border-radius:var(--radius-lg);">
      <div class="input-group">
        <label>[REPLACE] 手机号</label>
        <input class="input" placeholder="请输入手机号" />
      </div>
      <div class="input-group">
        <label>[REPLACE] 验证码</label>
        <input class="input" placeholder="请输入验证码" />
      </div>
    </div>
    <div class="stack-sm">
      <button class="btn btn-primary">[REPLACE] 登录/注册</button>
      <button class="btn btn-ghost">[REPLACE] 其他方式</button>
    </div>
    <p class="caption" style="text-align:center;">[REPLACE] 协议说明文字</p>
  </div>
</div>
```

## 原型 D — 个人中心（我的/设置）

顶部: 用户信息卡片。主体: 设置列表。底部: Tab Bar。

```html
<div class="content">
  <div class="pad" style="background:var(--surface);">
    <div class="row" style="gap:16px;">
      <div class="avatar-lg"></div>
      <div class="stack-sm">
        <span class="h3">[REPLACE] 用户名</span>
        <span class="meta">[REPLACE] 个人签名或 ID</span>
      </div>
    </div>
  </div>
  <div class="section-gap"></div>
  <div>
    <div class="cell">
      <div class="cell-body"><div class="cell-title">[REPLACE] 我的订单</div></div>
      <span class="cell-arrow">›</span>
    </div>
    <div class="cell">
      <div class="cell-body"><div class="cell-title">[REPLACE] 我的收藏</div></div>
      <span class="cell-arrow">›</span>
    </div>
    <div class="cell">
      <div class="cell-body"><div class="cell-title">[REPLACE] 设置</div></div>
      <span class="cell-arrow">›</span>
    </div>
  </div>
  <div class="section-gap"></div>
  <div>
    <div class="cell">
      <div class="cell-body"><div class="cell-title">[REPLACE] 帮助与反馈</div></div>
      <span class="cell-arrow">›</span>
    </div>
    <div class="cell">
      <div class="cell-body"><div class="cell-title">[REPLACE] 关于</div></div>
      <span class="cell-arrow">›</span>
    </div>
  </div>
</div>
<nav class="tabbar">
  <a class="tab"><div class="tab-icon"></div>首页</a>
  <a class="tab"><div class="tab-icon"></div>发现</a>
  <a class="tab"><div class="tab-icon"></div>消息</a>
  <a class="tab active"><div class="tab-icon"></div>我的</a>
</nav>
```

## 原型 E — 表单/结算（下单/支付/多步骤表单）

顶部: 返回 + 标题。主体: 表单区域。底部: 固定操作栏。无 Tab Bar。

```html
<div class="navbar">
  <button class="back">←</button>
  <span class="title">[REPLACE] 确认订单</span>
</div>
<div class="content">
  <div class="section-gap"></div>
  <div class="card" style="margin:0 16px;border-radius:var(--radius-lg);">
    <div class="stack">
      <div class="row-between">
        <span class="body">[REPLACE] 商品名称</span>
        <span class="num h3">[REPLACE] ¥99.00</span>
      </div>
      <div class="row-between">
        <span class="meta">[REPLACE] 规格说明</span>
        <span class="meta">x1</span>
      </div>
    </div>
  </div>
  <div class="section-gap"></div>
  <div style="background:var(--surface);">
    <div class="input-group">
      <label>收货地址</label>
      <input class="input" placeholder="[REPLACE] 请选择地址" />
    </div>
    <div class="input-group">
      <label>备注</label>
      <input class="input" placeholder="[REPLACE] 选填" />
    </div>
  </div>
  <div style="flex:1;"></div>
  <div class="pad row-between" style="background:var(--surface);border-top:0.5px solid var(--border);">
    <div>
      <span class="meta">合计</span>
      <span class="num h2" style="color:var(--danger);">[REPLACE] ¥99.00</span>
    </div>
    <button class="btn btn-primary btn-sm" style="width:auto;">[REPLACE] 提交订单</button>
  </div>
</div>
```

## 原型 F — 数据/仪表盘（统计/图表/单一大数字）

顶部: 标题。主体: 大数字 + 卡片网格。底部: Tab Bar。

```html
<div class="navbar">
  <span class="title">[REPLACE] 数据概览</span>
</div>
<div class="content pad">
  <div class="card" style="text-align:center;padding:24px;">
    <p class="meta">[REPLACE] 今日收入</p>
    <p class="num" style="font-size:36px;font-weight:600;margin:8px 0;">[REPLACE] ¥12,580</p>
    <p class="caption">[REPLACE] 较昨日 +12.3%</p>
  </div>
  <div class="grid-2" style="margin-top:12px;">
    <div class="card">
      <p class="meta">[REPLACE] 订单数</p>
      <p class="num h2">[REPLACE] 128</p>
    </div>
    <div class="card">
      <p class="meta">[REPLACE] 用户数</p>
      <p class="num h2">[REPLACE] 1,024</p>
    </div>
    <div class="card">
      <p class="meta">[REPLACE] 转化率</p>
      <p class="num h2">[REPLACE] 4.2%</p>
    </div>
    <div class="card">
      <p class="meta">[REPLACE] 客单价</p>
      <p class="num h2">[REPLACE] ¥98</p>
    </div>
  </div>
  <div class="card" style="margin-top:12px;">
    <p class="h3" style="margin-bottom:12px;">[REPLACE] 趋势图</p>
    <div class="ph-img wide">[REPLACE] 图表区域</div>
  </div>
</div>
<nav class="tabbar">
  <a class="tab"><div class="tab-icon"></div>首页</a>
  <a class="tab active"><div class="tab-icon"></div>数据</a>
  <a class="tab"><div class="tab-icon"></div>消息</a>
  <a class="tab"><div class="tab-icon"></div>我的</a>
</nav>
```

## 原型 G — 搜索（搜索栏 + 历史/热门）

顶部: 搜索栏。主体: 搜索历史 + 热门推荐。无 Tab Bar。

```html
<div class="content">
  <div class="search-bar">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
    <input placeholder="[REPLACE] 搜索..." />
  </div>
  <div class="pad">
    <div class="row-between" style="margin-bottom:12px;">
      <span class="h3">[REPLACE] 搜索历史</span>
      <span class="meta">[REPLACE] 清空</span>
    </div>
    <div class="row" style="flex-wrap:wrap;gap:8px;">
      <span class="pill">[REPLACE] 关键词一</span>
      <span class="pill">[REPLACE] 关键词二</span>
      <span class="pill">[REPLACE] 关键词三</span>
    </div>
  </div>
  <div class="section-gap"></div>
  <div class="pad">
    <p class="h3" style="margin-bottom:12px;">[REPLACE] 热门搜索</p>
    <div class="stack-sm">
      <div class="row"><span class="num meta">1</span><span class="body">[REPLACE] 热搜词</span></div>
      <div class="row"><span class="num meta">2</span><span class="body">[REPLACE] 热搜词</span></div>
      <div class="row"><span class="num meta">3</span><span class="body">[REPLACE] 热搜词</span></div>
    </div>
  </div>
</div>
```

## 原型 H — 聊天（消息气泡 + 输入框）

顶部: 返回 + 对方名称。主体: 消息气泡。底部: 输入框。

```html
<div class="navbar">
  <button class="back">←</button>
  <span class="title">[REPLACE] 对方名称</span>
</div>
<div class="content pad" style="background:var(--bg);">
  <div class="stack" style="gap:16px;">
    <div class="row" style="align-items:flex-start;">
      <div class="avatar-sm"></div>
      <div class="card" style="max-width:70%;padding:10px 12px;">
        <p class="body">[REPLACE] 对方消息</p>
      </div>
    </div>
    <div class="row" style="align-items:flex-start;justify-content:flex-end;">
      <div class="card" style="max-width:70%;padding:10px 12px;background:var(--accent);">
        <p class="body" style="color:#fff;">[REPLACE] 我的消息</p>
      </div>
      <div class="avatar-sm"></div>
    </div>
  </div>
</div>
<div class="row" style="padding:8px 12px;background:var(--surface);border-top:0.5px solid var(--border);gap:8px;">
  <input class="input" style="flex:1;padding:8px 12px;background:var(--bg);border-radius:var(--radius);" placeholder="[REPLACE] 输入消息..." />
  <button class="btn btn-primary btn-sm" style="width:auto;">[REPLACE] 发送</button>
</div>
```

## 原型 I — 设置（分组列表 + 开关）

顶部: 返回 + 标题。主体: 分组设置项。

```html
<div class="navbar">
  <button class="back">←</button>
  <span class="title">[REPLACE] 设置</span>
</div>
<div class="content">
  <div class="section-gap"></div>
  <div>
    <div class="cell">
      <div class="cell-body"><div class="cell-title">[REPLACE] 通知设置</div></div>
      <span class="cell-arrow">›</span>
    </div>
    <div class="cell">
      <div class="cell-body"><div class="cell-title">[REPLACE] 隐私</div></div>
      <span class="cell-arrow">›</span>
    </div>
  </div>
  <div class="section-gap"></div>
  <div>
    <div class="cell">
      <div class="cell-body"><div class="cell-title">[REPLACE] 深色模式</div></div>
      <div style="width:44px;height:26px;border-radius:13px;background:var(--accent);position:relative;">
        <div style="width:22px;height:22px;border-radius:50%;background:#fff;position:absolute;top:2px;right:2px;"></div>
      </div>
    </div>
    <div class="cell">
      <div class="cell-body"><div class="cell-title">[REPLACE] 清除缓存</div></div>
      <span class="cell-value">[REPLACE] 23.5MB</span>
    </div>
  </div>
  <div class="pad" style="margin-top:24px;">
    <button class="btn btn-warn">[REPLACE] 退出登录</button>
  </div>
</div>
```

## 原型 J — 图片画廊（网格展示）

顶部: 返回 + 标题。主体: 图片网格。

```html
<div class="navbar">
  <button class="back">←</button>
  <span class="title">[REPLACE] 相册</span>
  <button class="action">[REPLACE] 选择</button>
</div>
<div class="content pad">
  <div class="grid-3" style="gap:2px;">
    <div class="ph-img square">[REPLACE] 图片</div>
    <div class="ph-img square">[REPLACE] 图片</div>
    <div class="ph-img square">[REPLACE] 图片</div>
    <div class="ph-img square">[REPLACE] 图片</div>
    <div class="ph-img square">[REPLACE] 图片</div>
    <div class="ph-img square">[REPLACE] 图片</div>
    <div class="ph-img square">[REPLACE] 图片</div>
    <div class="ph-img square">[REPLACE] 图片</div>
    <div class="ph-img square">[REPLACE] 图片</div>
  </div>
</div>
```

## 原型 K — 空状态

居中图标 + 说明 + 操作按钮。

```html
<div class="navbar">
  <button class="back">←</button>
  <span class="title">[REPLACE] 页面标题</span>
</div>
<div class="content">
  <div class="empty">
    <svg class="empty-icon" width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
      <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
    </svg>
    <p class="h3">[REPLACE] 暂无内容</p>
    <p class="meta">[REPLACE] 这里还没有数据</p>
    <button class="btn btn-primary btn-sm" style="margin-top:12px;">[REPLACE] 去逛逛</button>
  </div>
</div>
```

## 原型 L — 弹窗/对话框

展示 Modal 对话框覆盖在页面上的状态。

```html
<div class="navbar">
  <span class="title">[REPLACE] 页面标题</span>
</div>
<div class="content pad">
  <p class="body">[REPLACE] 页面背景内容</p>
</div>
<div class="modal">
  <div class="modal-content">
    <p class="h3">[REPLACE] 确认操作</p>
    <p class="meta" style="margin-top:8px;">[REPLACE] 确定要执行此操作吗？</p>
    <div class="modal-actions">
      <button class="btn btn-default btn-sm">[REPLACE] 取消</button>
      <button class="btn btn-primary btn-sm">[REPLACE] 确定</button>
    </div>
  </div>
</div>
```
