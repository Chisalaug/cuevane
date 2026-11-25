// ==UserScript==
// @name         Test Helper Event Bridge
// @namespace    testhelper.local
// @version      0.2.0
// @description  本地测试辅助：把 sound / DOM / canvas 事件桥接到 127.0.0.1:8765；判定在本地服务端。
// @author       local
// @match        *://*/*
// @run-at       document-start
// @grant        GM_xmlhttpRequest
// @grant        unsafeWindow
// @connect      127.0.0.1
// @connect      localhost
// ==/UserScript==

(function () {
  'use strict';

  /* ------------------------------------------------------------------ *
   *  配置
   * ------------------------------------------------------------------ */
  const CFG = {
    endpoint: 'http://127.0.0.1:8765/evt',
    domFlushMs: 40,        // dom 队列的发送节流（毫秒）
    domPerFlush: 12,       // 每个节流窗口最多发几条
    queueMax: 600,         // 队列上限，超出丢最旧的
    dedupeMs: 120,         // 相同内容 120ms 内只上报一次
    textMax: 180,          // 单个节点文本截断长度
    canvasPollMs: 100,     // canvas 数量轮询间隔
    enabled: true,         // Alt+Shift+N 可切换
  };

  const W = (function () {
    try { return (typeof unsafeWindow !== 'undefined' && unsafeWindow) ? unsafeWindow : window; }
    catch (e) { return window; }
  })();

  const IS_IFRAME = (function () {
    try { return W.top !== W.self; } catch (e) { return true; }
  })();

  const START_RE = /all resources downloaded/i;

  /* ------------------------------------------------------------------ *
   *  上报
   * ------------------------------------------------------------------ */
  function gmPost(payload) {
    try {
      GM_xmlhttpRequest({
        method: 'POST',
        url: CFG.endpoint,
        headers: { 'Content-Type': 'application/json' },
        data: JSON.stringify(payload),
        timeout: 1500,
        onerror: function () { },
        ontimeout: function () { },
      });
    } catch (e) {
      /* 静默：绝不能因为上报失败把页面搞崩 */
    }
  }

  function evt(type, detail) {
    return { t: Date.now(), type: type, detail: detail };
  }

  // 声音 / start / canvas 这类“关键帧”立即发
  function urgent(type, detail) {
    if (!CFG.enabled) return;
    gmPost(evt(type, detail));
  }

  // dom 走队列 + 节流，避免一屏渲染把服务端刷爆
  const domQueue = [];
  let flushTimer = null;
  let droppedCount = 0;
  const recent = new Map();

  function scheduleFlush() {
    if (flushTimer !== null) return;
    flushTimer = setTimeout(flushDom, CFG.domFlushMs);
  }

  function flushDom() {
    flushTimer = null;
    let sent = 0;
    while (domQueue.length && sent < CFG.domPerFlush) {
      gmPost(domQueue.shift());
      sent += 1;
    }
    if (domQueue.length) scheduleFlush();
  }

  function queueDom(detail, dedupeKey) {
    if (!CFG.enabled) return;
    const t = Date.now();
    if (dedupeKey) {
      const last = recent.get(dedupeKey);
      if (last && (t - last) < CFG.dedupeMs) return;
      recent.set(dedupeKey, t);
      if (recent.size > 300) recent.clear();
    }
    domQueue.push(evt('dom', detail));
    if (domQueue.length > CFG.queueMax) {
      domQueue.shift();
      droppedCount += 1;
    }
    scheduleFlush();
  }

  /* ------------------------------------------------------------------ *
   *  DOM 描述
   * ------------------------------------------------------------------ */
  const SKIP_TAGS = {
    script: 1, style: 1, meta: 1, link: 1, noscript: 1, template: 1,
    head: 1, br: 1, hr: 1, path: 0,
  };

  function textOf(node, limit) {
    try {
      if (!node || node.nodeType !== 1) return '';
      const raw = node.textContent || '';
      if (!raw) return '';
      const s = raw.replace(/\s+/g, ' ').trim();
      return s.length > limit ? s.slice(0, limit) : s;
    } catch (e) { return ''; }
  }

  function shortUrl(u) {
    if (!u) return '';
    const s = String(u);
    return s.length > 160 ? s.slice(0, 160) : s;
  }

  function attr(el, name) {
    try {
      const v = el.getAttribute && el.getAttribute(name);
      return v ? String(v).slice(0, 120) : '';
    } catch (e) { return ''; }
  }

  let lastStartAt = 0;
  let startSent = false;

  function checkStart(text, tag) {
    if (!text || startSent) return;          // 每次页面加载只报一次 start，避免每帧重复
    const m = START_RE.exec(text);
    if (!m) return;
    const t = Date.now();
    if (t - lastStartAt < 3000) return;      // 防止一屏文本里有多个关键词时连发
    lastStartAt = t;
    startSent = true;
    const idx = Math.max(0, m.index - 40);
    urgent('start', {
      keyword: m[0],
      tag: tag || '',
      frame: IS_IFRAME ? 'iframe' : 'top',
      text: text.slice(idx, idx + 140),
    });
  }

  function describe(node, why) {
    try {
      if (!node) return null;
      if (node.nodeType === 3) node = node.parentElement;
      if (!node || node.nodeType !== 1) return null;

      const tag = (node.tagName || '').toLowerCase();
      if (!tag || SKIP_TAGS[tag]) return null;
      if (attr(node, 'data-nback') === '1') return null;

      const d = { why: why, kind: 'text', tag: tag };

      if (tag === 'img') d.kind = 'img';
      else if (tag === 'svg') d.kind = 'svg';
      else if (tag === 'canvas') d.kind = 'canvas';
      else if (tag === 'input' || tag === 'button' || tag === 'select') d.kind = 'control';

      const cls = attr(node, 'class');
      if (cls) d.cls = cls;
      const id = attr(node, 'id');
      if (id) d.id = id;

      if (tag === 'img') {
        d.src = shortUrl(node.currentSrc || node.src || attr(node, 'src'));
        d.alt = attr(node, 'alt');
        d.w = node.naturalWidth || node.width || 0;
        d.h = node.naturalHeight || node.height || 0;
      } else if (tag === 'canvas') {
        d.w = node.width;
        d.h = node.height;
      } else if (tag === 'svg') {
        d.src = shortUrl(attr(node, 'src'));
        d.vb = attr(node, 'viewBox');
        d.w = attr(node, 'width');
        d.h = attr(node, 'height');
      } else if (tag === 'input') {
        d.value = attr(node, 'value');
        d.type = attr(node, 'type');
      }

      const txt = textOf(node, CFG.textMax);
      if (txt) {
        d.text = txt;
        checkStart(txt, tag);
      }
      if (d.kind === 'canvas') {
        d.note = 'canvas 内容不可读，需要降级截图识别';
      }
      return d;
    } catch (e) {
      return null;
    }
  }

  function emitNode(node, why) {
    const d = describe(node, why);
    if (!d) return;
    if (d.kind === 'text' && !d.text) return;        // 纯容器、无文本，不占日志
    const key = [d.kind, d.tag, d.cls || '', d.src || '', (d.text || '').slice(0, 60)].join('|');
    queueDom(d, key);
  }

  /* ------------------------------------------------------------------ *
   *  MutationObserver：整个 document
   * ------------------------------------------------------------------ */
  const MO_OPTS = {
    childList: true,
    characterData: true,
    attributes: true,
    subtree: true,
    // 只盯真正影响“刺激外观”的属性，避免 style 抖动以外的噪声
    attributeFilter: ['class', 'src', 'alt', 'href', 'style', 'width', 'height'],
  };

  let budgetHitCount = 0;

  function onRecords(records) {
    if (!CFG.enabled) return;
    const BUDGET = 80;         // 一次回调最多处理 80 条
    let used = 0;

    for (let i = 0; i < records.length && used < BUDGET; i++) {
      const m = records[i];
      if (m.type === 'characterData') {
        const p = (m.target && m.target.parentElement) || m.target;
        emitNode(p, 'text');
        used += 1;
      } else if (m.type === 'attributes') {
        emitNode(m.target, 'attr:' + m.attributeName);
        used += 1;
      } else {
        const added = m.addedNodes || [];
        if (added.length) {
          for (let j = 0; j < added.length && used < BUDGET; j++) {
            emitNode(added[j], 'add');
            used += 1;
          }
        } else if (m.target) {
          emitNode(m.target, 'child-remove');
          used += 1;
        }
      }
    }

    if (records.length > BUDGET) {
      budgetHitCount += 1;
      if (budgetHitCount % 5 === 1) {
        queueDom({ why: 'overflow', dropped: records.length - BUDGET, total: budgetHitCount }, 'overflow|' + Math.floor(Date.now() / 1000));
      }
    }
  }

  let observerStarted = false;

  function startObserver(tries) {
    if (observerStarted) return;
    try {
      const mo = new MutationObserver(onRecords);
      mo.observe(W.document, MO_OPTS);
      observerStarted = true;
    } catch (e) {
      if ((tries || 0) < 20) setTimeout(function () { startObserver((tries || 0) + 1); }, 50);
    }
  }

  /* ------------------------------------------------------------------ *
   *  声音钩子：media.play / new Audio().play / buffer.start / osc.start
   * ------------------------------------------------------------------ */
  // Web Audio 的 start(when) 是按 AudioContext 时钟排程的：start 调用那一刻并没有响，
  // 真正发声在 when 秒之后。这里把延迟算出来，到点再上报，否则 stop-signal 的判定会早 250ms 以上。
  function soundDelayMs(node, when) {
    try {
      const ctx = node && node.context;
      if (typeof when === 'number' && ctx && typeof ctx.currentTime === 'number') {
        const d = (when - ctx.currentTime) * 1000;
        if (d > 4 && d < 20000) return d;
      }
    } catch (e) { }
    return 0;
  }

  function postSound(payload, delayMs) {
    if (!CFG.enabled) return;
    if (delayMs && delayMs > 4) {
      setTimeout(function () { urgent('sound', payload); }, delayMs);
    } else {
      urgent('sound', payload);
    }
  }
  function hookMediaPlay() {
    try {
      const proto = W.HTMLMediaElement && W.HTMLMediaElement.prototype;
      if (!proto || proto.__nbackPlayHooked) return;
      const orig = proto.play;
      if (typeof orig !== 'function') return;

      proto.play = function () {
        try {
          urgent('sound', {
            kind: this.__nbackAuto ? 'new-audio.play' : 'media.play',
            tag: (this.tagName || '').toLowerCase(),
            src: shortUrl(this.currentSrc || this.src || ''),
            muted: !!this.muted,
            volume: typeof this.volume === 'number' ? Math.round(this.volume * 100) / 100 : null,
            frame: IS_IFRAME ? 'iframe' : 'top',
          });
        } catch (e) { }
        return orig.apply(this, arguments);
      };
      Object.defineProperty(proto, '__nbackPlayHooked', { value: true, enumerable: false, configurable: true });
    } catch (e) { }
  }

  function hookAudioConstructor() {
    try {
      const OrigAudio = W.Audio;
      if (!OrigAudio || OrigAudio.__nbackWrapped) return;

      function NBackAudio() {
        const el = Reflect.construct(OrigAudio, Array.prototype.slice.call(arguments));
        try { el.__nbackAuto = true; } catch (e) { }
        return el;
      }
      NBackAudio.prototype = OrigAudio.prototype;
      try { Object.defineProperty(NBackAudio, '__nbackWrapped', { value: true, enumerable: false }); } catch (e) { }
      W.Audio = NBackAudio;
    } catch (e) { }
  }

  function hookAudioBufferSource() {
    try {
      const proto = W.AudioBufferSourceNode && W.AudioBufferSourceNode.prototype;
      if (!proto || proto.__nbackStartHooked) return;
      const orig = proto.start;
      if (typeof orig !== 'function') return;

      proto.start = function () {
        let payload = null;
        try {
          payload = {
            kind: 'buffer.start',
            duration: this.buffer ? Math.round(this.buffer.duration * 1000) / 1000 : null,
            channels: this.buffer ? this.buffer.numberOfChannels : null,
            scheduled_ms: Math.round(soundDelayMs(this, arguments[0])),
            frame: IS_IFRAME ? 'iframe' : 'top',
          };
        } catch (e) { }
        if (payload) postSound(payload, payload.scheduled_ms);
        return orig.apply(this, arguments);
      };
      Object.defineProperty(proto, '__nbackStartHooked', { value: true, enumerable: false, configurable: true });
    } catch (e) { }
  }

  function hookOscillator() {
    try {
      const proto = W.OscillatorNode && W.OscillatorNode.prototype;
      if (!proto || proto.__nbackStartHooked) return;
      const orig = proto.start;
      if (typeof orig !== 'function') return;

      proto.start = function () {
        let payload = null;
        try {
          payload = {
            kind: 'osc.start',
            freq: this.frequency ? this.frequency.value : null,
            scheduled_ms: Math.round(soundDelayMs(this, arguments[0])),
            frame: IS_IFRAME ? 'iframe' : 'top',
          };
        } catch (e) { }
        if (payload) postSound(payload, payload.scheduled_ms);
        return orig.apply(this, arguments);
      };
      Object.defineProperty(proto, '__nbackStartHooked', { value: true, enumerable: false, configurable: true });
    } catch (e) { }
  }

  // 诊断用：PsychoJS 的 TextStim 最终会走 canvas 的 fillText/strokeText。
  // 只记录短字形（箭头/单字母），用来验证刺激是什么；不在这里发 stim，避免和像素识别重复触发。
  function hookCanvasText() {
    try {
      const proto = W.CanvasRenderingContext2D && W.CanvasRenderingContext2D.prototype;
      if (!proto || proto.__nbackTextHooked) return;
      const names = ['fillText', 'strokeText'];
      for (let i = 0; i < names.length; i++) {
        const name = names[i];
        const orig = proto[name];
        if (typeof orig !== 'function') continue;
        proto[name] = function (text) {
          try {
            const s = text == null ? '' : String(text);
            if (s === '←' || s === '→' || s === '↑' || s === '↓' || /^[A-Za-z]$/.test(s)) {
              const t = Date.now();
              const last = recent.get('ct|' + s);
              if (!last || t - last > 250) {
                recent.set('ct|' + s, t);
                urgent('canvas_text', {
                  text: s,
                  font: this.font,
                  x: Math.round(arguments[1] || 0),
                  y: Math.round(arguments[2] || 0),
                  cw: this.canvas ? this.canvas.width : 0,
                  ch: this.canvas ? this.canvas.height : 0,
                });
              }
            }
          } catch (e) { }
          return orig.apply(this, arguments);
        };
      }
      Object.defineProperty(proto, '__nbackTextHooked', { value: true, enumerable: false, configurable: true });
    } catch (e) { }
  }

  function hookSound() {
    hookMediaPlay();
    hookAudioConstructor();
    hookAudioBufferSource();
    hookOscillator();
    hookCanvasText();
  }

  /* ------------------------------------------------------------------ *
   *  canvas 巡检：每 100ms 看数量
   * ------------------------------------------------------------------ */
  let lastCanvasCount = -1;
  let lastCanvasWarnAt = 0;

  function canvasTick() {
    if (!CFG.enabled) return;
    let list;
    try { list = W.document.querySelectorAll('canvas'); } catch (e) { return; }
    const count = list.length;

    if (count !== lastCanvasCount) {
      const sizes = [];
      for (let i = 0; i < list.length && i < 6; i++) {
        const c = list[i];
        let rect = null;
        try {
          const r = c.getBoundingClientRect();
          rect = [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)];
        } catch (e) { }
        sizes.push({
          w: c.width, h: c.height,
          cls: attr(c, 'class').slice(0, 60),
          rect: rect,
          dpr: W.devicePixelRatio || 1,
          screen: [W.screenX || 0, W.screenY || 0],
        });
      }
      lastCanvasCount = count;
      urgent('canvas', { count: count, sizes: sizes, frame: IS_IFRAME ? 'iframe' : 'top' });

      if (count > 0 && Date.now() - lastCanvasWarnAt > 3000) {
        lastCanvasWarnAt = Date.now();
        urgent('canvas_warning', {
          count: count,
          hint: '页面用 canvas 画东西，dom 日志拿不到内容；如果 stim 就在这里，走降级方案（mss + 模板匹配）',
          frame: IS_IFRAME ? 'iframe' : 'top',
        });
      }
    }
  }

  /* ------------------------------------------------------------------ *
   *  首屏扫描 + 就绪自检
   * ------------------------------------------------------------------ */
  let initialScanDone = false;

  function initialScan() {
    if (initialScanDone) return;
    try {
      const body = W.document.body;
      if (!body) return;
      initialScanDone = true;
      const whole = (body.textContent || '').replace(/\s+/g, ' ');
      if (whole) checkStart(whole.slice(0, 4000), 'body');
      urgent('note', {
        kind: 'initial-scan',
        frame: IS_IFRAME ? 'iframe' : 'top',
        href: shortUrl(W.location && W.location.href),
        textLen: whole.length,
        canvas: W.document.querySelectorAll('canvas').length,
        dropped: droppedCount,
      });
    } catch (e) { }
  }

  /* ------------------------------------------------------------------ *
   *  热键：Alt+Shift+N 暂停 / 恢复上报（默认开启）
   * ------------------------------------------------------------------ */
  function installToggle() {
    try {
      W.addEventListener('keydown', function (e) {
        if (!(e.altKey && e.shiftKey)) return;
        const k = e.key;
        if (k !== 'N' && k !== 'n') return;
        if (CFG.enabled) {
          urgent('note', { kind: 'disabled-by-hotkey' });
          CFG.enabled = false;
        } else {
          CFG.enabled = true;
          urgent('note', { kind: 'enabled-by-hotkey' });
        }
      }, true);
    } catch (e) { }
  }

  /* ------------------------------------------------------------------ *
   *  启动
   * ------------------------------------------------------------------ */
  hookSound();
  installToggle();
  startObserver(0);
  setInterval(canvasTick, CFG.canvasPollMs);
  setTimeout(canvasTick, 0);

  urgent('note', { kind: 'script-ready', frame: IS_IFRAME ? 'iframe' : 'top', href: shortUrl(W.location && W.location.href) });

  if (W.document.readyState === 'loading') {
    W.document.addEventListener('DOMContentLoaded', function () {
      hookSound();
      initialScan();
      canvasTick();
    }, { once: true });
  } else {
    setTimeout(function () { initialScan(); canvasTick(); }, 0);
  }

  W.addEventListener('load', function () {
    hookSound();
    setTimeout(function () { initialScan(); canvasTick(); }, 300);
  }, { once: true });
})();
