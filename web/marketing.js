// Adjutant marketing-page interactions.
// Pure vanilla. Coexists with app.js — touches none of the live-demo IDs.

(() => {
  // -----------------------------------------------------------
  // 1. Reveal on scroll
  // -----------------------------------------------------------
  const reveals = document.querySelectorAll("[data-reveal]");
  if ("IntersectionObserver" in window && reveals.length) {
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add("in");
            io.unobserve(e.target);
          }
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.06 }
    );
    reveals.forEach((el) => io.observe(el));
  } else {
    reveals.forEach((el) => el.classList.add("in"));
  }

  // -----------------------------------------------------------
  // 2. Sticky nav: hide on scroll-down, show on scroll-up
  // -----------------------------------------------------------
  const nav = document.getElementById("nav");
  if (nav) {
    let last = window.scrollY;
    let ticking = false;
    const onScroll = () => {
      const y = window.scrollY;
      nav.classList.toggle("scrolled", y > 24);
      if (y > 120 && y > last + 6) nav.classList.add("hidden");
      else if (y < last - 6 || y < 120) nav.classList.remove("hidden");
      last = y;
      ticking = false;
    };
    window.addEventListener(
      "scroll",
      () => {
        if (!ticking) {
          requestAnimationFrame(onScroll);
          ticking = true;
        }
      },
      { passive: true }
    );
  }

  // -----------------------------------------------------------
  // 3. Marquee populate — corpus regulations indexed
  // -----------------------------------------------------------
  const track = document.getElementById("marqueeTrack");
  if (track) {
    const items = [
      "AR 600-8-10 · Leaves & Passes",
      "JTR 2025-06 · Joint Travel Regulations",
      "AR 27-10 · Military Justice",
      "AR 623-3 · Evaluation Reporting",
      "AR 735-5 · Property Accountability",
      "AR 670-1 · Wear & Appearance",
      "AR 600-8-22 · Military Awards",
      "AR 600-9 · Body Composition",
      "AR 600-85 · Substance Abuse",
      "AR 600-8-101 · Personnel Processing",
      "DA Pam 600-25 · NCO Guide",
      "FM 6-22 · Leader Development",
      "FM 3-0 · Operations",
      "DA Pam 623-3 · Evaluation Doctrine",
      "DD-1351-2 · Travel Voucher",
      "DA-31 · Leave Form",
      "DA-4856 · Counseling",
    ];
    // Duplicate twice for seamless infinite loop.
    const html = items.map((t) => `<span>${t}</span>`).join("");
    track.innerHTML = html + html;
  }

  // -----------------------------------------------------------
  // 4. Magnetic buttons (cursor-pull)
  // -----------------------------------------------------------
  const magnets = document.querySelectorAll("[data-magnetic]");
  const isFinePointer =
    window.matchMedia &&
    window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  if (isFinePointer) {
    magnets.forEach((el) => {
      const STRENGTH = 0.25;
      el.addEventListener("mousemove", (ev) => {
        const r = el.getBoundingClientRect();
        const x = ev.clientX - (r.left + r.width / 2);
        const y = ev.clientY - (r.top + r.height / 2);
        el.style.transform = `translate(${x * STRENGTH}px, ${y * STRENGTH}px)`;
      });
      el.addEventListener("mouseleave", () => {
        el.style.transform = "";
      });
    });
  }

  // -----------------------------------------------------------
  // 5. Cursor dot
  // -----------------------------------------------------------
  const dot = document.querySelector(".cursor-dot");
  if (dot && isFinePointer) {
    let x = 0, y = 0, tx = 0, ty = 0;
    let rafId = null;
    document.addEventListener("mouseenter", () => dot.classList.add("active"));
    document.addEventListener("mouseleave", () => dot.classList.remove("active"));
    document.addEventListener("mousemove", (ev) => {
      tx = ev.clientX;
      ty = ev.clientY;
      if (!dot.classList.contains("active")) dot.classList.add("active");
      if (!rafId) rafId = requestAnimationFrame(loop);
    });
    function loop() {
      x += (tx - x) * 0.22;
      y += (ty - y) * 0.22;
      dot.style.transform = `translate3d(${x}px, ${y}px, 0) translate(-50%, -50%)`;
      if (Math.abs(tx - x) > 0.1 || Math.abs(ty - y) > 0.1) {
        rafId = requestAnimationFrame(loop);
      } else {
        rafId = null;
      }
    }
    // Hover targets — anything interactive enlarges the dot.
    const hoverTargets = document.querySelectorAll(
      "a, button, [data-magnetic], summary, .form-card, .stage, .stat, .claim"
    );
    hoverTargets.forEach((t) => {
      t.addEventListener("mouseenter", () => dot.classList.add("hover"));
      t.addEventListener("mouseleave", () => dot.classList.remove("hover"));
    });
  }

  // -----------------------------------------------------------
  // 6. Hero parallax — title drifts up on scroll
  // -----------------------------------------------------------
  const heroTitle = document.querySelector(".hero__title");
  if (heroTitle && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    let ticking = false;
    window.addEventListener(
      "scroll",
      () => {
        if (!ticking) {
          requestAnimationFrame(() => {
            const y = Math.min(window.scrollY, 600);
            heroTitle.style.transform = `translate3d(0, ${y * 0.18}px, 0)`;
            heroTitle.style.opacity = String(Math.max(0, 1 - y / 700));
            ticking = false;
          });
          ticking = true;
        }
      },
      { passive: true }
    );
  }

  // -----------------------------------------------------------
  // 7. Smooth-scroll for internal anchors (respects prefers-reduced-motion)
  // -----------------------------------------------------------
  document.querySelectorAll('a[href^="#"]').forEach((a) => {
    a.addEventListener("click", (ev) => {
      const id = a.getAttribute("href").slice(1);
      const target = id ? document.getElementById(id) : null;
      if (!target) return;
      ev.preventDefault();
      target.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
        block: "start",
      });
    });
  });
})();
