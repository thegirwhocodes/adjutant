// Adjutant marketing page interactions.
// Vanilla. No dependencies. Coexists with app.js.
//
// Responsibilities:
//   1. Reveal-on-scroll (IntersectionObserver) — only for elements
//      below the fold; the hero is always visible at first paint.
//   2. Sticky-nav hide-on-scroll-down / show-on-scroll-up.
//   3. Marquee population.
//   4. Live latency meter — wraps WebSocket so any /ws/voice connection
//      is timed end-of-speech → first-audio-out.
//   5. "Simulate offline" toggle that flips the badge for 8 s without
//      breaking the demo connection.
//   6. Tasteful hero parallax that pauses once the title leaves the fold.
//
// Removed (per design audit): cursor dot, magnetic buttons, grain layer.

(() => {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // -----------------------------------------------------------
  // 1. Reveal on scroll
  // -----------------------------------------------------------
  const reveals = document.querySelectorAll(".reveal");
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
    window.addEventListener("scroll", () => {
      if (!ticking) { requestAnimationFrame(onScroll); ticking = true; }
    }, { passive: true });
  }

  // -----------------------------------------------------------
  // 3. Marquee — corpus regulations
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
    const html = items.map((t) => `<span>${t}</span>`).join("");
    track.innerHTML = html + html;
  }

  // -----------------------------------------------------------
  // 4. Live latency meter
  //
  // Wrap window.WebSocket so any /ws/voice connection auto-instruments
  // round-trip from USER_DONE → first audio chunk (binary frame).
  // -----------------------------------------------------------
  const latencyEl = document.getElementById("latency-ms");
  function setLatency(ms) {
    if (!latencyEl) return;
    latencyEl.textContent = String(Math.round(ms));
  }

  if (latencyEl && "WebSocket" in window) {
    const NativeWS = window.WebSocket;
    function WrappedWS(url, protocols) {
      const ws = protocols ? new NativeWS(url, protocols) : new NativeWS(url);
      // Only instrument the voice WebSocket
      try {
        const u = new URL(url, window.location.origin);
        if (!u.pathname.includes("/ws/voice")) return ws;
      } catch (_) {}

      let userDoneAt = null;
      let firstAudioCounted = false;

      ws.addEventListener("message", (ev) => {
        try {
          if (typeof ev.data === "string") {
            const obj = JSON.parse(ev.data);
            if (obj && obj.type === "USER_DONE") {
              userDoneAt = performance.now();
              firstAudioCounted = false;
            }
          } else if (userDoneAt && !firstAudioCounted) {
            // First binary frame after USER_DONE = first audio out
            const ms = performance.now() - userDoneAt;
            setLatency(ms);
            firstAudioCounted = true;
          }
        } catch (_) {}
      });
      return ws;
    }
    WrappedWS.prototype = NativeWS.prototype;
    WrappedWS.CONNECTING = NativeWS.CONNECTING;
    WrappedWS.OPEN       = NativeWS.OPEN;
    WrappedWS.CLOSING    = NativeWS.CLOSING;
    WrappedWS.CLOSED     = NativeWS.CLOSED;
    window.WebSocket = WrappedWS;
  }

  // -----------------------------------------------------------
  // 5. Simulate-offline toggle
  // -----------------------------------------------------------
  const simBtn = document.getElementById("sim-offline");
  const netBadge = document.getElementById("net-status");
  if (simBtn && netBadge) {
    simBtn.addEventListener("click", () => {
      if (simBtn.classList.contains("simulating")) return;
      simBtn.classList.add("simulating");
      simBtn.textContent = "● simulating offline";
      const prevText = netBadge.textContent;
      const prevClass = netBadge.className;
      netBadge.textContent = "OFFLINE — still working";
      netBadge.className = "badge offline";
      setTimeout(() => {
        netBadge.textContent = prevText;
        netBadge.className = prevClass;
        simBtn.classList.remove("simulating");
        simBtn.textContent = "⏻ Simulate offline";
      }, 8000);
    });
  }

  // -----------------------------------------------------------
  // 6. Hero parallax (subtle, paused after fold)
  // -----------------------------------------------------------
  const heroTitle = document.querySelector(".hero__title");
  if (heroTitle && !reduceMotion) {
    let ticking = false;
    window.addEventListener("scroll", () => {
      if (window.scrollY > window.innerHeight) return;
      if (!ticking) {
        requestAnimationFrame(() => {
          const y = Math.min(window.scrollY, 600);
          heroTitle.style.transform = `translate3d(0, ${y * 0.12}px, 0)`;
          ticking = false;
        });
        ticking = true;
      }
    }, { passive: true });
  }

  // -----------------------------------------------------------
  // 7. Smooth-scroll for in-page anchors (respects reduced-motion)
  // -----------------------------------------------------------
  document.querySelectorAll('a[href^="#"]').forEach((a) => {
    a.addEventListener("click", (ev) => {
      const id = a.getAttribute("href").slice(1);
      if (!id) return;
      const target = document.getElementById(id);
      if (!target) return;
      ev.preventDefault();
      target.scrollIntoView({
        behavior: reduceMotion ? "auto" : "smooth",
        block: "start",
      });
      target.focus({ preventScroll: true });
    });
  });
})();