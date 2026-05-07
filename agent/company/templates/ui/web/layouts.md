# Web 页面布局库

**8 个 paste-ready section 模板。** 粘贴到 seed.html 的 `<main id="content">` 中。不要从零写 section——选最接近的布局，粘贴，替换文案。

## 使用前

1. 读完 seed.html 的 `<style>` 块
2. 先规划 section 列表再写内容。默认节奏：
   - **Landing**: Hero → Features → Stats/Quote → Split → CTA
   - **产品页**: Hero-split → Features → Quote → CTA
   - **定价页**: Hero-center → Table → CTA
3. accent 每屏最多 2 次（eyebrow + primary button）

## Class 清单

> `section` `container` `hero` `hero-center` `hero-split` `hero-cta` `eyebrow` `lead` `display` `h1` `h2` `h3` `meta` `num` `btn` `btn-primary` `btn-secondary` `btn-ghost` `card` `card-flat` `feature` `feature-mark` `stat` `stat-num` `stat-label` `quote` `quote-text` `quote-author` `tag` `pill` `field` `input` `ph-img` `square` `wide` `portrait` `rule` `grid-2` `grid-3` `grid-4` `grid-2-1` `grid-1-2` `row` `row-between` `stack`

---

## Layout 1 — Hero 居中

用于大多数 landing page 开头。一个 eyebrow + 一个大标题(≤14字) + 一句 lead + 两个 CTA。

```html
<section class="section hero" data-section="hero">
  <div class="container hero-center">
    <p class="eyebrow">[REPLACE] 品类 · 定位</p>
    <h1 class="display">[REPLACE] 一句话说清楚产品是什么</h1>
    <p class="lead">[REPLACE] 一句话说清楚用户能得到什么</p>
    <div class="hero-cta">
      <a class="btn btn-primary" href="#">[REPLACE] 主操作</a>
      <a class="btn btn-secondary" href="#">[REPLACE] 次操作</a>
    </div>
  </div>
</section>
```

## Layout 2 — Hero 分栏（文字 + 视觉）

左侧文案，右侧产品截图/插图。适合有真实产品视觉的页面。

```html
<section class="section" data-section="hero-split">
  <div class="container hero-split">
    <div>
      <p class="eyebrow">[REPLACE] 品类</p>
      <h1 class="h1" style="margin-top:12px;">[REPLACE] 标题</h1>
      <p class="lead" style="margin-top:16px;">[REPLACE] 副标题，两句话以内。</p>
      <div class="hero-cta" style="margin-top:24px;">
        <a class="btn btn-primary" href="#">[REPLACE] 主操作</a>
        <a class="btn btn-ghost" href="#">[REPLACE] 了解更多 →</a>
      </div>
    </div>
    <div class="ph-img wide">[REPLACE] 产品截图 16:9</div>
  </div>
</section>
```

## Layout 3 — 功能三列

3 个功能点，每个有图标 + 标题 + 描述。

```html
<section class="section" data-section="features">
  <div class="container">
    <h2 class="h2" style="text-align:center;margin-bottom:48px;">[REPLACE] 核心功能</h2>
    <div class="grid-3">
      <div class="feature">
        <div class="feature-mark">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
        </div>
        <h3 class="h3">[REPLACE] 功能一标题</h3>
        <p class="meta">[REPLACE] 具体描述这个功能解决什么问题，2-3 句话。</p>
      </div>
      <div class="feature">
        <div class="feature-mark">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
        </div>
        <h3 class="h3">[REPLACE] 功能二标题</h3>
        <p class="meta">[REPLACE] 具体描述。</p>
      </div>
      <div class="feature">
        <div class="feature-mark">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        </div>
        <h3 class="h3">[REPLACE] 功能三标题</h3>
        <p class="meta">[REPLACE] 具体描述。</p>
      </div>
    </div>
  </div>
</section>
```

## Layout 4 — 数据统计行

3-4 个关键数字，横向排列。

```html
<section class="section" style="background:var(--surface);border-top:1px solid var(--border);border-bottom:1px solid var(--border);" data-section="stats">
  <div class="container grid-4">
    <div class="stat">
      <p class="stat-num num">[REPLACE] 10K+</p>
      <p class="stat-label">[REPLACE] 活跃用户</p>
    </div>
    <div class="stat">
      <p class="stat-num num">[REPLACE] 99.9%</p>
      <p class="stat-label">[REPLACE] 可用性</p>
    </div>
    <div class="stat">
      <p class="stat-num num">[REPLACE] 50ms</p>
      <p class="stat-label">[REPLACE] 响应时间</p>
    </div>
    <div class="stat">
      <p class="stat-num num">[REPLACE] 24/7</p>
      <p class="stat-label">[REPLACE] 技术支持</p>
    </div>
  </div>
</section>
```

## Layout 5 — 分栏（图 + 文字）

左图右文或左文右图，适合功能详解。

```html
<section class="section" data-section="split">
  <div class="container grid-1-2">
    <div class="ph-img square">[REPLACE] 功能截图</div>
    <div class="stack" style="gap:16px;justify-content:center;">
      <p class="eyebrow">[REPLACE] 模块名</p>
      <h2 class="h2">[REPLACE] 这个功能如何帮助用户</h2>
      <p style="color:var(--muted);line-height:1.6;">[REPLACE] 详细说明，3-4 句话描述使用场景和价值。</p>
      <a class="btn btn-secondary" href="#" style="width:fit-content;">[REPLACE] 了解详情</a>
    </div>
  </div>
</section>
```

## Layout 6 — CTA 横幅

页面底部的行动号召区域。

```html
<section class="section" data-section="cta">
  <div class="container" style="text-align:center;">
    <h2 class="h2">[REPLACE] 准备好开始了吗？</h2>
    <p class="lead" style="margin:16px auto 0;">[REPLACE] 一句话价值主张</p>
    <div class="hero-cta" style="justify-content:center;margin-top:24px;">
      <a class="btn btn-primary" href="#">[REPLACE] 免费试用</a>
      <a class="btn btn-secondary" href="#">[REPLACE] 联系销售</a>
    </div>
  </div>
</section>
```

## Layout 7 — 引用/评价

客户评价或权威引用。

```html
<section class="section" data-section="quote">
  <div class="container" style="max-width:800px;">
    <div class="quote">
      <p class="quote-text">[REPLACE] "具体的客户评价内容，说明产品如何帮助他们解决了什么问题。"</p>
      <p class="quote-author">[REPLACE] — 张三，某公司 CEO</p>
    </div>
  </div>
</section>
```

## Layout 8 — 定价表格

2-3 个定价方案对比。

```html
<section class="section" data-section="pricing">
  <div class="container">
    <h2 class="h2" style="text-align:center;margin-bottom:48px;">[REPLACE] 选择适合的方案</h2>
    <div class="grid-3">
      <div class="card">
        <h3 class="h3">[REPLACE] 免费版</h3>
        <p class="meta" style="margin-top:8px;">[REPLACE] 适合个人用户</p>
        <p style="margin-top:16px;"><span class="num" style="font-size:32px;font-weight:600;">¥0</span><span class="meta">/月</span></p>
        <hr class="rule">
        <div class="stack" style="gap:8px;">
          <p class="meta">✓ [REPLACE] 功能一</p>
          <p class="meta">✓ [REPLACE] 功能二</p>
          <p class="meta">✓ [REPLACE] 功能三</p>
        </div>
        <a class="btn btn-secondary" href="#" style="width:100%;margin-top:24px;">[REPLACE] 开始使用</a>
      </div>
      <div class="card" style="border-color:var(--accent);">
        <div class="row-between">
          <h3 class="h3">[REPLACE] 专业版</h3>
          <span class="tag">推荐</span>
        </div>
        <p class="meta" style="margin-top:8px;">[REPLACE] 适合团队</p>
        <p style="margin-top:16px;"><span class="num" style="font-size:32px;font-weight:600;">[REPLACE] ¥99</span><span class="meta">/月</span></p>
        <hr class="rule">
        <div class="stack" style="gap:8px;">
          <p class="meta">✓ [REPLACE] 包含免费版全部</p>
          <p class="meta">✓ [REPLACE] 高级功能</p>
          <p class="meta">✓ [REPLACE] 优先支持</p>
        </div>
        <a class="btn btn-primary" href="#" style="width:100%;margin-top:24px;">[REPLACE] 升级专业版</a>
      </div>
      <div class="card">
        <h3 class="h3">[REPLACE] 企业版</h3>
        <p class="meta" style="margin-top:8px;">[REPLACE] 适合大型组织</p>
        <p style="margin-top:16px;"><span class="num" style="font-size:32px;font-weight:600;">定制</span></p>
        <hr class="rule">
        <div class="stack" style="gap:8px;">
          <p class="meta">✓ [REPLACE] 包含专业版全部</p>
          <p class="meta">✓ [REPLACE] 私有部署</p>
          <p class="meta">✓ [REPLACE] 专属客户经理</p>
        </div>
        <a class="btn btn-secondary" href="#" style="width:100%;margin-top:24px;">[REPLACE] 联系我们</a>
      </div>
    </div>
  </div>
</section>
```
