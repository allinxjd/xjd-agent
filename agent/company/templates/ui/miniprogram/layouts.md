# 微信小程序屏幕布局库

**8 个 paste-ready 屏幕原型。** 粘贴到 seed.html 的 `<div class="content">` 中。

## 使用前

1. 读完 seed.html 的 `<style>` 块，了解所有可用 class
2. 选择**恰好一个**原型。一个屏幕只做一件事
3. 小程序导航栏（wx-navbar）已在 seed 中，不要重复添加
4. 如果原型带 tabbar，保留；否则删除 `<nav class="tabbar">` 块

## Class 清单

> `pad` `pad-sm` `stack` `stack-sm` `row` `row-between` `grid-2` `grid-3` `grid-4` `section-gap` `h1` `h2` `h3` `body` `meta` `caption` `num` `link` `card` `card-flat` `wx-panel` `cell` `cell-body` `cell-title` `cell-desc` `cell-arrow` `cell-value` `cell-icon` `avatar` `avatar-sm` `avatar-lg` `avatar-round` `btn` `btn-primary` `btn-default` `btn-warn` `btn-ghost` `btn-sm` `btn-mini` `tag` `pill` `pill active` `badge` `input-group` `input` `search-bar` `tabbar` `tab` `tab active` `tab-icon` `wx-navbar` `wx-navbar-title` `wx-navbar-back` `wx-capsule` `wx-actionsheet` `wx-actionsheet-item` `wx-actionsheet-cancel` `wx-share` `wx-share-item` `wx-share-icon` `ph-img` `ph-img square` `ph-img wide` `ph-img banner` `skeleton`

---

## 原型 A — 首页/商城（Banner + 宫格 + 商品列表）

```html
<div class="content">
  <div class="ph-img banner">[REPLACE] 轮播 Banner</div>
  <div class="pad">
    <div class="grid-4">
      <div style="text-align:center;">
        <div class="cell-icon" style="width:44px;height:44px;margin:0 auto;border-radius:8px;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></svg>
        </div>
        <p class="caption" style="margin-top:4px;">[REPLACE] 分类</p>
      </div>
      <div style="text-align:center;">
        <div class="cell-icon" style="width:44px;height:44px;margin:0 auto;border-radius:8px;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
        </div>
        <p class="caption" style="margin-top:4px;">[REPLACE] 限时</p>
      </div>
      <div style="text-align:center;">
        <div class="cell-icon" style="width:44px;height:44px;margin:0 auto;border-radius:8px;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><path d="M20 12V8H6a2 2 0 01-2-2c0-1.1.9-2 2-2h12v4"/><path d="M4 6v12c0 1.1.9 2 2 2h14v-4"/></svg>
        </div>
        <p class="caption" style="margin-top:4px;">[REPLACE] 优惠</p>
      </div>
      <div style="text-align:center;">
        <div class="cell-icon" style="width:44px;height:44px;margin:0 auto;border-radius:8px;">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        </div>
        <p class="caption" style="margin-top:4px;">[REPLACE] 签到</p>
      </div>
    </div>
  </div>
  <div class="section-gap"></div>
  <div class="pad">
    <p class="h3" style="margin-bottom:12px;">[REPLACE] 推荐商品</p>
    <div class="grid-2">
      <div class="card">
        <div class="ph-img square">[REPLACE] 商品图</div>
        <p class="body" style="margin-top:8px;">[REPLACE] 商品名称</p>
        <p class="num" style="color:var(--danger);margin-top:4px;">[REPLACE] ¥99</p>
      </div>
      <div class="card">
        <div class="ph-img square">[REPLACE] 商品图</div>
        <p class="body" style="margin-top:8px;">[REPLACE] 商品名称</p>
        <p class="num" style="color:var(--danger);margin-top:4px;">[REPLACE] ¥199</p>
      </div>
    </div>
  </div>
</div>
<nav class="tabbar">
  <a class="tab active"><div class="tab-icon"></div>首页</a>
  <a class="tab"><div class="tab-icon"></div>分类</a>
  <a class="tab"><div class="tab-icon"></div>购物车</a>
  <a class="tab"><div class="tab-icon"></div>我的</a>
</nav>
```

## 原型 B — 商品详情（大图 + 信息 + 底部操作栏）

```html
<div class="content">
  <div class="ph-img wide" style="border-radius:0;">[REPLACE] 商品主图</div>
  <div class="pad stack">
    <div class="row-between">
      <span class="num h2" style="color:var(--danger);">[REPLACE] ¥299</span>
      <span class="tag">[REPLACE] 新品</span>
    </div>
    <h1 class="h3">[REPLACE] 商品标题，一行或两行</h1>
    <p class="meta">[REPLACE] 已售 1.2万件 · 好评 98%</p>
  </div>
  <div class="section-gap"></div>
  <div class="wx-panel">
    <div class="cell">
      <div class="cell-body"><div class="cell-title">规格</div><div class="cell-desc">[REPLACE] 请选择</div></div>
      <span class="cell-arrow">›</span>
    </div>
    <div class="cell">
      <div class="cell-body"><div class="cell-title">配送</div><div class="cell-desc">[REPLACE] 包邮</div></div>
      <span class="cell-arrow">›</span>
    </div>
  </div>
  <div class="section-gap"></div>
  <div class="pad">
    <p class="h3" style="margin-bottom:12px;">商品详情</p>
    <div class="ph-img portrait">[REPLACE] 详情长图</div>
  </div>
</div>
<div class="pad row-between" style="background:var(--surface);border-top:0.5px solid var(--border);position:sticky;bottom:0;">
  <div class="row" style="gap:16px;">
    <div style="text-align:center;font-size:10px;color:var(--muted);">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78L12 21.23l8.84-8.84a5.5 5.5 0 000-7.78z"/></svg>
      <p>收藏</p>
    </div>
    <div style="text-align:center;font-size:10px;color:var(--muted);">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 002 1.61h9.72a2 2 0 002-1.61L23 6H6"/></svg>
      <p>购物车</p>
    </div>
  </div>
  <div class="row" style="gap:8px;">
    <button class="btn btn-default btn-sm">[REPLACE] 加入购物车</button>
    <button class="btn btn-primary btn-sm">[REPLACE] 立即购买</button>
  </div>
</div>
```

## 原型 C — 个人中心（头像卡片 + 功能宫格 + 设置列表）

```html
<div class="content">
  <div class="pad" style="background:var(--surface);padding-top:24px;">
    <div class="row" style="gap:16px;">
      <div class="avatar-lg" style="border-radius:50%;"></div>
      <div class="stack-sm">
        <span class="h3">[REPLACE] 用户昵称</span>
        <span class="meta">[REPLACE] 会员等级 · ID: 12345</span>
      </div>
    </div>
    <div class="grid-4" style="margin-top:20px;">
      <div style="text-align:center;">
        <p class="num h3">[REPLACE] 12</p>
        <p class="caption">[REPLACE] 订单</p>
      </div>
      <div style="text-align:center;">
        <p class="num h3">[REPLACE] 5</p>
        <p class="caption">[REPLACE] 收藏</p>
      </div>
      <div style="text-align:center;">
        <p class="num h3">[REPLACE] 3</p>
        <p class="caption">[REPLACE] 优惠券</p>
      </div>
      <div style="text-align:center;">
        <p class="num h3">[REPLACE] 128</p>
        <p class="caption">[REPLACE] 积分</p>
      </div>
    </div>
  </div>
  <div class="section-gap"></div>
  <div class="wx-panel">
    <div class="cell">
      <div class="cell-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></div>
      <div class="cell-body"><div class="cell-title">[REPLACE] 个人信息</div></div>
      <span class="cell-arrow">›</span>
    </div>
    <div class="cell">
      <div class="cell-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg></div>
      <div class="cell-body"><div class="cell-title">[REPLACE] 账号安全</div></div>
      <span class="cell-arrow">›</span>
    </div>
    <div class="cell">
      <div class="cell-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15 1.65 1.65 0 003 14.08V14a2 2 0 014 0v.09"/></svg></div>
      <div class="cell-body"><div class="cell-title">[REPLACE] 设置</div></div>
      <span class="cell-arrow">›</span>
    </div>
  </div>
</div>
<nav class="tabbar">
  <a class="tab"><div class="tab-icon"></div>首页</a>
  <a class="tab"><div class="tab-icon"></div>分类</a>
  <a class="tab"><div class="tab-icon"></div>购物车</a>
  <a class="tab active"><div class="tab-icon"></div>我的</a>
</nav>
```

## 原型 D — 订单/列表（筛选 + 卡片列表）

```html
<div class="content">
  <div class="pad-sm">
    <div class="row" style="overflow-x:auto;gap:8px;">
      <span class="pill active">[REPLACE] 全部</span>
      <span class="pill">[REPLACE] 待付款</span>
      <span class="pill">[REPLACE] 待发货</span>
      <span class="pill">[REPLACE] 待收货</span>
      <span class="pill">[REPLACE] 已完成</span>
    </div>
  </div>
  <div class="pad stack">
    <div class="card">
      <div class="row-between" style="margin-bottom:12px;">
        <span class="meta">[REPLACE] 店铺名称</span>
        <span class="tag">[REPLACE] 待发货</span>
      </div>
      <div class="row">
        <div class="ph-img square" style="width:80px;height:80px;flex-shrink:0;">[REPLACE] 商品图</div>
        <div class="stack-sm" style="flex:1;">
          <p class="body">[REPLACE] 商品名称描述</p>
          <p class="meta">[REPLACE] 规格：默认</p>
          <div class="row-between">
            <span class="num" style="color:var(--danger);">[REPLACE] ¥99.00</span>
            <span class="meta">x1</span>
          </div>
        </div>
      </div>
      <div class="row-between" style="margin-top:12px;padding-top:12px;border-top:0.5px solid var(--border);">
        <span class="meta">[REPLACE] 共1件商品 合计: ¥99.00</span>
        <button class="btn btn-default btn-mini">[REPLACE] 查看物流</button>
      </div>
    </div>
    <div class="card">
      <div class="row-between" style="margin-bottom:12px;">
        <span class="meta">[REPLACE] 店铺名称</span>
        <span class="tag">[REPLACE] 已完成</span>
      </div>
      <div class="row">
        <div class="ph-img square" style="width:80px;height:80px;flex-shrink:0;">[REPLACE] 商品图</div>
        <div class="stack-sm" style="flex:1;">
          <p class="body">[REPLACE] 商品名称</p>
          <p class="meta">[REPLACE] 规格</p>
          <div class="row-between">
            <span class="num" style="color:var(--danger);">[REPLACE] ¥199.00</span>
            <span class="meta">x1</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
```

## 原型 E — 搜索结果（搜索栏 + 结果列表）

```html
<div class="content">
  <div class="search-bar">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
    <input placeholder="[REPLACE] 搜索关键词" />
  </div>
  <div class="pad-sm">
    <div class="row-between">
      <div class="row" style="gap:16px;">
        <span class="body" style="color:var(--accent);">[REPLACE] 综合</span>
        <span class="body">[REPLACE] 销量</span>
        <span class="body">[REPLACE] 价格</span>
      </div>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" stroke-width="2"><path d="M4 4h16M4 12h16M4 20h16"/></svg>
    </div>
  </div>
  <div class="pad">
    <div class="grid-2">
      <div class="card" style="padding:0;overflow:hidden;">
        <div class="ph-img square" style="border-radius:var(--radius-lg) var(--radius-lg) 0 0;">[REPLACE] 商品图</div>
        <div style="padding:8px 12px;">
          <p class="body">[REPLACE] 商品名称两行文字</p>
          <div class="row-between" style="margin-top:8px;">
            <span class="num" style="color:var(--danger);font-weight:600;">[REPLACE] ¥59</span>
            <span class="caption">[REPLACE] 已售999</span>
          </div>
        </div>
      </div>
      <div class="card" style="padding:0;overflow:hidden;">
        <div class="ph-img square" style="border-radius:var(--radius-lg) var(--radius-lg) 0 0;">[REPLACE] 商品图</div>
        <div style="padding:8px 12px;">
          <p class="body">[REPLACE] 商品名称</p>
          <div class="row-between" style="margin-top:8px;">
            <span class="num" style="color:var(--danger);font-weight:600;">[REPLACE] ¥128</span>
            <span class="caption">[REPLACE] 已售500</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
```

## 原型 F — 表单提交（地址/信息填写）

```html
<div class="content">
  <div class="section-gap"></div>
  <div class="wx-panel">
    <div class="input-group">
      <label>[REPLACE] 收货人</label>
      <input class="input" placeholder="[REPLACE] 请输入姓名" />
    </div>
    <div class="input-group">
      <label>[REPLACE] 手机号</label>
      <input class="input" placeholder="[REPLACE] 请输入手机号" />
    </div>
    <div class="input-group">
      <label>[REPLACE] 地区</label>
      <input class="input" placeholder="[REPLACE] 请选择省市区" />
    </div>
    <div class="input-group">
      <label>[REPLACE] 详细地址</label>
      <input class="input" placeholder="[REPLACE] 街道、楼牌号等" />
    </div>
  </div>
  <div class="pad" style="margin-top:12px;">
    <div class="row" style="gap:8px;">
      <div style="width:18px;height:18px;border:1.5px solid var(--accent);border-radius:4px;display:flex;align-items:center;justify-content:center;">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="3"><polyline points="20 6 9 17 4 12"/></svg>
      </div>
      <span class="meta">[REPLACE] 设为默认地址</span>
    </div>
  </div>
  <div style="flex:1;"></div>
  <div class="pad">
    <button class="btn btn-primary">[REPLACE] 保存地址</button>
  </div>
</div>
```

## 原型 G — 聊天/客服（消息列表 + 输入框）

```html
<div class="content" style="background:var(--bg);">
  <div class="pad stack" style="gap:16px;">
    <div class="row" style="align-items:flex-start;">
      <div class="avatar-sm" style="border-radius:4px;"></div>
      <div class="card" style="max-width:70%;padding:10px 12px;">
        <p class="body">[REPLACE] 您好，请问有什么可以帮您？</p>
      </div>
    </div>
    <div class="row" style="align-items:flex-start;justify-content:flex-end;">
      <div class="card" style="max-width:70%;padding:10px 12px;background:var(--accent);color:#fff;">
        <p class="body" style="color:#fff;">[REPLACE] 我想问一下订单什么时候发货</p>
      </div>
      <div class="avatar-sm" style="border-radius:4px;"></div>
    </div>
    <div class="row" style="align-items:flex-start;">
      <div class="avatar-sm" style="border-radius:4px;"></div>
      <div class="card" style="max-width:70%;padding:10px 12px;">
        <p class="body">[REPLACE] 您的订单预计明天发出，请耐心等待。</p>
      </div>
    </div>
  </div>
</div>
<div class="row" style="padding:8px 12px;background:var(--surface);border-top:0.5px solid var(--border);gap:8px;">
  <input class="input" style="flex:1;padding:8px 12px;background:var(--bg);border-radius:var(--radius);" placeholder="[REPLACE] 输入消息..." />
  <button class="btn btn-primary btn-sm" style="width:auto;">[REPLACE] 发送</button>
</div>
```

## 原型 H — 授权/登录（微信一键登录）

```html
<div class="content pad" style="justify-content:center;">
  <div class="stack" style="gap:32px;align-items:center;">
    <div class="avatar-lg" style="width:80px;height:80px;border-radius:16px;"></div>
    <div class="stack-sm" style="text-align:center;">
      <h1 class="h2">[REPLACE] 小程序名称</h1>
      <p class="meta">[REPLACE] 申请获取以下权限</p>
    </div>
    <div class="wx-panel" style="width:100%;">
      <div class="cell">
        <div class="avatar-sm" style="border-radius:50%;"></div>
        <div class="cell-body">
          <div class="cell-title">[REPLACE] 获取你的头像、昵称</div>
        </div>
      </div>
      <div class="cell">
        <div class="cell-icon"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07 19.5 19.5 0 01-6-6 19.79 19.79 0 01-3.07-8.67A2 2 0 014.11 2h3a2 2 0 012 1.72"/></svg></div>
        <div class="cell-body">
          <div class="cell-title">[REPLACE] 获取你的手机号</div>
        </div>
      </div>
    </div>
    <div class="stack-sm" style="width:100%;">
      <button class="btn btn-primary">[REPLACE] 微信快捷登录</button>
      <button class="btn btn-ghost">[REPLACE] 暂不登录</button>
    </div>
  </div>
</div>
```
