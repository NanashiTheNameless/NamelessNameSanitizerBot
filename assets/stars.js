// This software is licensed under NNCL v1.2 see LICENSE.md for more info
// https://github.com/NanashiTheNameless/NamelessNameSanitizerBot/blob/gh-pages/LICENSE.md
// Lightweight starfield, shared across pages
(function(){
  const canvas = document.getElementById('stars');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let stars = [];
  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    const count = Math.min(250, Math.floor((canvas.width * canvas.height) / 8000));
    stars = Array.from({ length: count }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      r: Math.random() * 1.1 + 0.2,
      a: Math.random() * 1,
      v: (Math.random() * 0.015) + 0.005
    }));
  }
  window.addEventListener('resize', resize);
  resize();
  function frame(){
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const s of stars) {
      s.a += s.v;
      const alpha = 0.3 + 0.7 * (0.5 + 0.5 * Math.sin(s.a));
      ctx.globalAlpha = alpha;
      ctx.fillStyle = '#ffffff';
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
