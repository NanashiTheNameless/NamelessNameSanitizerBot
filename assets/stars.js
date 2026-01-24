// This software is licensed under NNCL v1.3 see LICENSE.md for more info
// https://nnsb.namelessnanashi.dev/license
// Semi-Lightweight starfield, shared across pages

(function(){
  const canvas = document.getElementById('stars');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let stars = [];

  const TWO_PI = Math.PI * 2;

  // Render all stars and halos to the canvas
  function draw(){
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const s of stars) {
      // Draw prebuilt halo gradient if present
      if (s.haloGradient) {
        ctx.fillStyle = s.haloGradient;
        ctx.fillRect(s.haloRect[0], s.haloRect[1], s.haloRect[2], s.haloRect[3]);
      }

      // Draw star core
      ctx.globalAlpha = s.alpha;
      ctx.fillStyle = s.fill;
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, TWO_PI);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  // Generate starfield on viewport resize with random star count
  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    const area = canvas.width * canvas.height;
    const count = Math.round(area / 3000);
    stars = Array.from({ length: count }, () => makeStar());
    draw();
  }

  // Clamp numeric value to [min, max] range
  function clamp(n, min, max) { return Math.max(min, Math.min(max, n)); }

  // Generate random star color: 50% white, 50% tinted (warm/cool/reddish)
  function randomTint() {
    if (Math.random() < 0.5) {
      const tintType = Math.random();
      if (tintType < 0.33) {
        // Warm (yellow/orange)
        const r = clamp((255 + (Math.random() * 15 - 7.5)) | 0, 0, 255);
        const g = clamp((210 + (Math.random() * 30 - 15)) | 0, 0, 255);
        const b = clamp((170 + (Math.random() * 25 - 12.5)) | 0, 0, 255);
        return [r, g, b];
      } else if (tintType < 0.66) {
        // Cool (cyan/blue)
        const r = clamp((160 + (Math.random() * 30 - 15)) | 0, 0, 255);
        const g = clamp((190 + (Math.random() * 30 - 15)) | 0, 0, 255);
        const b = clamp((255 + (Math.random() * 15 - 7.5)) | 0, 0, 255);
        return [r, g, b];
      } else {
        // Reddish
        const r = clamp((245 + (Math.random() * 10 - 5)) | 0, 0, 255);
        const g = clamp((200 + (Math.random() * 25 - 12.5)) | 0, 0, 255);
        const b = clamp((210 + (Math.random() * 25 - 12.5)) | 0, 0, 255);
        return [r, g, b];
      }
    }
    // Neutral white with variation
    const w = clamp((225 + (Math.random() * 30 - 15)) | 0, 0, 255);
    return [w, w, w];
  }

  // Generate a single star with random properties: size, brightness, and halo
  function makeStar() {
    const x = Math.random() * canvas.width;
    const y = Math.random() * canvas.height;
    const roll = Math.random();
    let r, alpha, haloR, haloA;
    if (roll < 0.7) {
      // Small stars (70%)
      r = 0.6 + Math.random() * 1.2;
      alpha = 0.2 + Math.random() * 0.75;
      const haloChance = Math.random();
      if (haloChance < 0.95) {
        haloR = r * (1.8 + Math.random() * 2.8);
        haloA = 0.04 + Math.random() * 0.1;
      } else {
        haloR = 0;
        haloA = 0;
      }
    } else if (roll < 0.95) {
      // Medium stars (25%)
      r = 0.9 + Math.random() * 1.5;
      alpha = 0.3 + Math.random() * 0.65;
      const haloChance = Math.random();
      if (haloChance < 0.65) { // 65% halo chance
        haloR = r * (1.5 + Math.random() * 2.2);
        haloA = 0.05 + Math.random() * 0.15;
      } else {
        haloR = 0;
        haloA = 0;
      }
    } else {
      // Large/giant stars (5%)
      r = 1.6 + Math.random() * 1.5;
      alpha = 0.55 + Math.random() * 0.45;
      const haloChance = Math.random();
      if (haloChance < 0.50) { // 50% halo chance
        haloR = r * (2.5 + Math.random() * 2.3);
        haloA = 0.08 + Math.random() * 0.2;
      } else {
        haloR = 0;
        haloA = 0;
      }
    }
    const c = randomTint();
    const fill = `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
    let haloGradient = null;
    let haloRect = null;
    if (haloR > 0 && haloA > 0) {
      const grad = ctx.createRadialGradient(x, y, 0, x, y, haloR);
      grad.addColorStop(0, `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${haloA})`);
      grad.addColorStop(1, `rgba(${c[0]}, ${c[1]}, ${c[2]}, 0)`);
      haloGradient = grad;
      haloRect = [x - haloR, y - haloR, haloR * 2, haloR * 2];
    }
    return { x, y, r, alpha, haloR, haloA, c, fill, haloGradient, haloRect };
  }

  // Debounce resize handler to avoid excessive redrawing during window drag
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(resize, 100);
  });

  // Initialize starfield on page load
  resize();
})();
