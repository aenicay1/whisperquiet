/*
 * WhisperQuiet hero visual engine.
 * Samples hero-source.jpg into a low-res color grid, then paints each cell
 * as either a halftone dot ("quiet image" state) or a monospace glyph
 * ("text" state). The blend between the two states is driven by how far
 * the hero has scrolled past, plus a local boost near the pointer.
 * Everything is transform/opacity-free canvas drawing paused off-screen
 * and fully static under prefers-reduced-motion.
 */
(function () {
  "use strict";

  var art = document.querySelector(".hero-art");
  var canvas = document.getElementById("hero-canvas");
  if (!art || !canvas || !canvas.getContext) return;

  var ctx = canvas.getContext("2d", { alpha: false });
  var reduceQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  var reduceMotion = reduceQuery.matches;

  var img = new Image();
  var imgReady = false;
  var failed = false;
  img.src = "hero-source.jpg";

  var PALETTE = [
    { r: 9, g: 12, b: 20 },     // ink
    { r: 74, g: 99, b: 232 },   // cobalt
    { r: 237, g: 162, b: 63 },  // amber
    { r: 244, g: 241, b: 234 } // paper
  ];

  function nearestPalette(r, g, b) {
    var best = PALETTE[0];
    var bestD = Infinity;
    for (var i = 0; i < PALETTE.length; i++) {
      var c = PALETTE[i];
      var d = (c.r - r) * (c.r - r) + (c.g - g) * (c.g - g) + (c.b - b) * (c.b - b);
      if (d < bestD) {
        bestD = d;
        best = c;
      }
    }
    return best;
  }

  var GLYPHS = " .:-=+*#%@".split("");

  var sampleCanvas = document.createElement("canvas");
  var sampleCtx = sampleCanvas.getContext("2d", { willReadFrequently: true });
  var sampleCols = 72;
  var sampleRows = 40;
  var cells = [];

  var dpr = Math.min(window.devicePixelRatio || 1, 2);
  var cw = 0, ch = 0;

  // Cell size is held roughly constant in CSS pixels (tuned to match the
  // look of the original ~1000px-wide hero, where 118 cols across ~1000px
  // gave ~8.5px cells) so grid CELL COUNT grows with the canvas instead of
  // individual cells growing chunky on large/full-screen monitors. Total
  // cell count is capped for frame-time safety; past the cap, cell size is
  // allowed to grow slightly rather than tanking the frame rate.
  var TARGET_CELL_PX = 8.5;
  var MAX_CELLS = 16000;
  var MIN_COLS = 44;

  function computeGrid(w, h) {
    var targetAR = w / h;
    var cols = Math.max(MIN_COLS, Math.round(w / TARGET_CELL_PX));
    var rows = Math.max(16, Math.round(cols / targetAR));
    if (cols * rows > MAX_CELLS) {
      var scale = Math.sqrt(MAX_CELLS / (cols * rows));
      cols = Math.max(MIN_COLS, Math.round(cols * scale));
      rows = Math.max(16, Math.round(cols / targetAR));
    }
    return { cols: cols, rows: rows };
  }

  function sampleImage() {
    if (!imgReady || cw === 0 || ch === 0) return;
    var targetAR = cw / ch;
    var grid = computeGrid(cw, ch);
    sampleCols = grid.cols;
    sampleRows = grid.rows;
    sampleCanvas.width = sampleCols;
    sampleCanvas.height = sampleRows;

    var iw = img.naturalWidth, ih = img.naturalHeight;
    var srcAR = iw / ih;
    var sx, sy, sw, sh;
    if (srcAR > targetAR) {
      sh = ih;
      sw = ih * targetAR;
      sx = (iw - sw) / 2;
      sy = 0;
    } else {
      sw = iw;
      sh = iw / targetAR;
      sx = 0;
      sy = (ih - sh) / 2;
    }
    sampleCtx.clearRect(0, 0, sampleCols, sampleRows);
    sampleCtx.drawImage(img, sx, sy, sw, sh, 0, 0, sampleCols, sampleRows);

    var data;
    try {
      data = sampleCtx.getImageData(0, 0, sampleCols, sampleRows).data;
    } catch (err) {
      // Canvas tainted (e.g. opened from file://). Show the plain image instead.
      failed = true;
      canvas.style.display = "none";
      var fb = art.querySelector(".hero-fallback");
      if (fb) fb.style.display = "block";
      return;
    }
    cells = new Array(sampleCols * sampleRows);
    for (var i = 0; i < cells.length; i++) {
      var o = i * 4;
      var r = data[o], g = data[o + 1], b = data[o + 2];
      var pal = nearestPalette(r, g, b);
      // Painterly blend: keep most of the photo's color, pull toward the
      // disciplined cobalt/ink/amber/paper palette, then lift it a touch so
      // dark foliage still reads as texture instead of vanishing.
      var mr = Math.min(255, Math.round((r * 0.55 + pal.r * 0.45) * 1.18 + 10));
      var mg = Math.min(255, Math.round((g * 0.55 + pal.g * 0.45) * 1.18 + 10));
      var mb = Math.min(255, Math.round((b * 0.55 + pal.b * 0.45) * 1.18 + 12));
      cells[i] = {
        rgb: mr + "," + mg + "," + mb,
        r: mr, g: mg, b: mb,
        lum: (0.299 * r + 0.587 * g + 0.114 * b) / 255
      };
    }
  }

  function resize() {
    var rect = art.getBoundingClientRect();
    cw = Math.max(Math.round(rect.width), 1);
    ch = Math.max(Math.round(rect.height), 1);
    canvas.width = cw * dpr;
    canvas.height = ch * dpr;
    canvas.style.width = cw + "px";
    canvas.style.height = ch + "px";
    sampleImage();
  }

  var pointer = { x: -99999, y: -99999, active: false, seq: 0 };

  // Debug hook for automated/manual verification: exposes live pointer
  // state so it's possible to confirm proximity tracking updates without
  // needing to hover the literal canvas element.
  window.__wqProximity = pointer;

  // The cursor "bump" doesn't snap straight to the raw pointer position or
  // pop instantly to full strength -- both its center and its overall
  // strength ease every frame, so it glides behind the cursor and fades
  // in/out on hero enter/leave instead of appearing/disappearing at a hard
  // moment. See draw()'s effect-update block for the lerp factors.
  var effect = { x: 0, y: 0, strength: 0 };
  var wasPointerActive = false;

  // The hero copy block (eyebrow, H1, subhead, CTA row) sits in a stacking
  // context above the canvas and would swallow mousemove/pointermove events
  // if we listened on the canvas or even the .hero-art layer directly, which
  // is why the old proximity effect had a dead zone over that copy. Buttons
  // and links must stay clickable (no pointer-events:none), so instead we
  // listen at the window/document level and translate the pointer's page
  // coordinates into canvas-local space ourselves. This tracks the cursor
  // across the whole hero -- including when it's over text or buttons.
  window.addEventListener("pointermove", function (e) {
    var rect = art.getBoundingClientRect();
    pointer.x = e.clientX - rect.left;
    pointer.y = e.clientY - rect.top;
    pointer.active = true;
    pointer.seq++;
  }, { passive: true });

  document.addEventListener("pointerleave", function () {
    pointer.active = false;
  }, { passive: true });

  window.addEventListener("blur", function () {
    pointer.active = false;
  }, { passive: true });

  function smoothstep(edge0, edge1, x) {
    var v = (x - edge0) / (edge1 - edge0);
    if (v < 0) v = 0;
    else if (v > 1) v = 1;
    return v * v * (3 - 2 * v);
  }

  function scrollProgress() {
    var rect = art.getBoundingClientRect();
    var total = rect.height || 1;
    var scrolled = -rect.top;
    if (scrolled < 0) scrolled = 0;
    if (scrolled > total) scrolled = total;
    return scrolled / total;
  }

  function draw() {
    if (failed) return;
    if (!imgReady || !cells.length) {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = "rgb(9,12,20)";
      ctx.fillRect(0, 0, cw, ch);
      return;
    }

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "rgb(9,12,20)";
    ctx.fillRect(0, 0, cw, ch);

    var cellW = cw / sampleCols;
    var cellH = ch / sampleRows;
    var baseT = reduceMotion ? 0.4 : scrollProgress();

    // Cursor "mountain": a soft radial bump layered on top of the
    // scroll-driven baseT. The bump's center and overall strength ease
    // toward the pointer/its active state every frame (see lerp factors
    // below) so it glides in behind the cursor and fades in/out on hero
    // enter/leave rather than popping. The falloff is a gaussian in
    // distance, sharpened through a smoothstep (steeper mid-slope, still
    // zero-derivative at the rim) -- a continuous function with no radius
    // at which it "turns off" -- so there is no ring where the effect
    // visibly starts or stops.
    var effectRadius = Math.max(cw, ch) * 0.2; // founder round 4: ~33% smaller footprint than the prior 0.3
    var sigma = effectRadius / 2.6;
    var cutoff = effectRadius; // beyond this the gaussian contribution is ~0.03 or less; skip for perf
    var cutoffSq = cutoff * cutoff;

    if (!reduceMotion) {
      // Snap (rather than glide across the whole hero) if the bump had
      // already faded out and the pointer re-enters somewhere new.
      if (pointer.active && !wasPointerActive && effect.strength < 0.05) {
        effect.x = pointer.x;
        effect.y = pointer.y;
      }
      wasPointerActive = pointer.active;
      effect.x += (pointer.x - effect.x) * 0.16;
      effect.y += (pointer.y - effect.y) * 0.16;
      var targetStrength = pointer.active ? 1 : 0;
      effect.strength += (targetStrength - effect.strength) * 0.12;
    }
    var applyEffect = !reduceMotion && effect.strength > 0.002;

    ctx.textBaseline = "middle";
    ctx.textAlign = "center";
    var fontSize = Math.max(cellH * 0.95, 7);
    var FONT_PRE = "600 ";
    var FONT_POST = "px 'SF Mono', Menlo, ui-monospace, Consolas, monospace";
    var curFontSize = fontSize;
    ctx.font = FONT_PRE + fontSize + FONT_POST;
    // Swollen glyphs near the bump's peak need per-cell font sizes; quantize
    // and only touch ctx.font (which is expensive) when the size changes.
    function setFontSize(size) {
      var q = Math.round(size * 4) / 4;
      if (q !== curFontSize) {
        curFontSize = q;
        ctx.font = FONT_PRE + q + FONT_POST;
      }
    }

    for (var row = 0; row < sampleRows; row++) {
      for (var col = 0; col < sampleCols; col++) {
        var idx = row * sampleCols + col;
        var cell = cells[idx];
        if (!cell) continue;

        var cx = col * cellW + cellW / 2;
        var cy = row * cellH + cellH / 2;
        var t = baseT;
        var influence = 0;

        if (applyEffect) {
          var dx = cx - effect.x, dy = cy - effect.y;
          var distSq = dx * dx + dy * dy;
          if (distSq < cutoffSq) {
            // Gaussian profile sharpened by a smoothstep: steeper mid-slope
            // (more "mountain", less "plateau") while keeping a zero-slope,
            // edge-free landing at the rim.
            var g = Math.exp(-distSq / (2 * sigma * sigma));
            influence = smoothstep(0, 1, g) * effect.strength;
            if (influence > 0.001) t = Math.min(1, baseT + influence * 0.9);
            else influence = 0;
          }
        }

        var alpha = 0.5 + cell.lum * 0.5;

        if (influence > 0) {
          // Cursor-influenced cell: crossfade continuously between the dot
          // and glyph renders instead of flipping at t===0.5, so the
          // "mountain" eases smoothly from full glyph at its peak to the
          // ambient halftone at its rim with no visible edge. To sell the
          // "raised" relief feeling, cells physically swell (up to ~1.45x
          // at the peak) and their color lifts toward paper-white, as if
          // the field were displaced up toward a light source.
          var glyphWeight = smoothstep(0.5 - 0.13, 0.5 + 0.13, t);
          var dotWeight = 1 - glyphWeight;
          var swell = 1 + influence * 0.45;
          var lift = influence * 0.3;
          var br = Math.round(cell.r + (255 - cell.r) * lift);
          var bg = Math.round(cell.g + (255 - cell.g) * lift);
          var bb = Math.round(cell.b + (255 - cell.b) * lift);
          var liftedRgb = br + "," + bg + "," + bb;

          if (dotWeight > 0.01) {
            var dotShrink2 = 1 - (t / 0.5) * 0.25;
            var radius2 = Math.min(cellW, cellH) * 0.52 * dotShrink2 * (0.42 + cell.lum * 0.75) * swell;
            if (radius2 > 0.3) {
              ctx.beginPath();
              ctx.fillStyle = "rgba(" + liftedRgb + "," + (alpha * dotWeight).toFixed(3) + ")";
              ctx.arc(cx, cy, radius2, 0, Math.PI * 2);
              ctx.fill();
            }
          }
          if (glyphWeight > 0.01) {
            var charT2 = Math.max(0, (t - 0.5) / 0.5);
            var gi2 = Math.round(cell.lum * (GLYPHS.length - 1));
            if (gi2 < 1) gi2 = 1;
            var glyph2 = GLYPHS[gi2];
            var glyphAlpha2 = Math.min(1, alpha + charT2 * 0.3) * glyphWeight;
            var swellFont = fontSize * swell;
            setFontSize(swellFont);
            ctx.fillStyle = "rgba(" + liftedRgb + "," + glyphAlpha2.toFixed(3) + ")";
            ctx.fillText(glyph2, cx, cy + swellFont * 0.04);
          }
        } else if (t < 0.5) {
          var dotShrink = 1 - (t / 0.5) * 0.25;
          var radius = Math.min(cellW, cellH) * 0.52 * dotShrink * (0.42 + cell.lum * 0.75);
          if (radius > 0.3) {
            ctx.beginPath();
            ctx.fillStyle = "rgba(" + cell.rgb + "," + alpha.toFixed(3) + ")";
            ctx.arc(cx, cy, radius, 0, Math.PI * 2);
            ctx.fill();
          }
        } else {
          var charT = (t - 0.5) / 0.5;
          var gi = Math.round(cell.lum * (GLYPHS.length - 1));
          if (gi < 1) gi = 1;
          var glyph = GLYPHS[gi];
          setFontSize(fontSize); // restore base size after any swollen cells
          ctx.fillStyle = "rgba(" + cell.rgb + "," + Math.min(1, alpha + charT * 0.3).toFixed(3) + ")";
          ctx.fillText(glyph, cx, cy + fontSize * 0.04);
        }
      }
    }
  }

  var running = false;
  var rafId = null;

  function loop() {
    if (!running || reduceMotion || failed) {
      rafId = null;
      return;
    }
    draw();
    rafId = requestAnimationFrame(loop);
  }

  function kick() {
    draw();
    if (running && !reduceMotion && !rafId) {
      rafId = requestAnimationFrame(loop);
    }
  }

  // Debug hook for automated/manual verification of grid density.
  window.__wqDebugGrid = function () {
    return {
      cols: sampleCols,
      rows: sampleRows,
      cellCount: sampleCols * sampleRows,
      cw: cw,
      ch: ch,
      cellPxW: cw / sampleCols,
      cellPxH: ch / sampleRows,
      dpr: dpr
    };
  };

  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        running = entry.isIntersecting;
        if (running) kick();
      });
    }, { threshold: 0 });
    io.observe(art);
  } else {
    running = true;
  }

  reduceQuery.addEventListener
    ? reduceQuery.addEventListener("change", function (e) {
        reduceMotion = e.matches;
        kick();
      })
    : reduceQuery.addListener(function (e) {
        reduceMotion = e.matches;
        kick();
      });

  img.onload = function () {
    imgReady = true;
    sampleImage();
    kick();
  };

  var resizeTimer = null;
  window.addEventListener("resize", function () {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(function () {
      resize();
      kick();
    }, 120);
  }, { passive: true });

  resize();
})();

/*
 * Sticky product-flow: lightweight enhancement only. The pin/stack effect
 * itself is pure CSS (position: sticky). This just toggles an "is-active"
 * class so the current step's copy is at full strength while neighbours
 * dim slightly. Degrades to a no-op (all steps fully visible) if
 * IntersectionObserver is missing, and is skipped under reduced motion.
 */
(function () {
  "use strict";
  if (!("IntersectionObserver" in window)) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  var steps = document.querySelectorAll(".flow-step");
  if (!steps.length) return;

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      entry.target.classList.toggle("is-active", entry.isIntersecting && entry.intersectionRatio > 0.55);
    });
  }, { threshold: [0, 0.55, 1] });

  steps.forEach(function (step) {
    io.observe(step);
  });
})();

/*
 * Demo video: plays only while scrolled into view, pauses otherwise, so it
 * never runs silently in the background. Muted + playsinline keeps autoplay
 * allowed by the browser. Under prefers-reduced-motion, autoplay is skipped
 * entirely -- the poster (first frame) shows and native <video controls>
 * let the visitor start playback themselves.
 */
(function () {
  "use strict";
  var video = document.getElementById("demo-video");
  if (!video) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  if (!("IntersectionObserver" in window)) return;

  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        var p = video.play();
        if (p && p.catch) p.catch(function () {});
      } else {
        video.pause();
      }
    });
  }, { threshold: 0.5 });

  io.observe(video);
})();
