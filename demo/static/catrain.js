// Easter egg: ↑ ↑ ↓ ↓ ← → ← → B A makes it rain cats for a few seconds.
(() => {
  const CODE = ["ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown", "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowRight", "b", "a"];
  const CATS = ["🐈", "🐱", "😺", "😸", "😹", "😻", "😼", "🐈‍⬛", "🙀", "😽"];
  let pos = 0, raining = false;

  document.addEventListener("keydown", e => {
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;   // don't hijack typing
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    pos = k === CODE[pos] ? pos + 1 : (k === CODE[0] ? 1 : 0);
    if (pos === CODE.length) { pos = 0; rain(); }
  });

  function rain() {
    if (raining) return;
    raining = true;
    const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    const layer = document.createElement("div");
    layer.setAttribute("aria-hidden", "true");
    layer.style.cssText = "position:fixed;inset:0;pointer-events:none;overflow:hidden;z-index:99999";
    document.body.appendChild(layer);
    const n = reduce ? 12 : 70, H = window.innerHeight;
    let last = 0;
    for (let i = 0; i < n; i++) {
      const c = document.createElement("span");
      c.textContent = CATS[Math.floor(Math.random() * CATS.length)];
      const size = 22 + Math.random() * 34, delay = Math.random() * 2500, dur = 2200 + Math.random() * 2600;
      const spin = (Math.random() - 0.5) * 720, drift = (Math.random() - 0.5) * 160;
      c.style.cssText = `position:absolute;top:-70px;left:${Math.random() * 100}vw;font-size:${size}px;line-height:1;will-change:transform`;
      layer.appendChild(c);
      c.animate(
        [{ transform: "translate(0,0) rotate(0deg)" },
         { transform: `translate(${drift}px,${H + 140}px) rotate(${reduce ? 0 : spin}deg)` }],
        { duration: dur, delay, easing: "cubic-bezier(.3,.1,.7,1)", fill: "forwards" });
      last = Math.max(last, delay + dur);
    }
    setTimeout(() => { layer.remove(); raining = false; }, last + 200);
  }
})();
